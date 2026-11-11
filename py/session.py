# -*- coding: utf-8 -*-
"""会话级判定：把"按句判"升级成"按会话判"。

真实电商介入不是一句一句看的，至少有三个能力是单句做不到的：
  1. 累计：同一类违规在会话里出现 N 次才成立（反复拖延）
  2. 指代：这一句说"这个不行"，要知道"这个"指上一轮的什么
  3. 组合：前面承诺退货、后面加条件，单看每一句都不算违规
"""
import json
import re
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent

ANAPHORA = re.compile(r"(这个|那个|它|这|那|该商品|该订单)")
# 会话内累计到几次算成立（只对带 repeat_threshold 的规则生效）
DEFAULT_REPEAT = 3


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Session:
    def __init__(self, sid, order_no="", merchant="", user_id=""):
        self.sid = sid
        self.order_no = order_no
        self.merchant = merchant
        self.user_id = user_id
        self.turns = []          # [{role, text, hits:[type], ts}]
        self.counts = {}         # type -> 次数
        self.verdict = "allow"   # allow / warn / block
        self.reason = ""
        self.created = _now()
        self.updated = self.created

    def to_dict(self):
        return {"session_id": self.sid, "order_no": self.order_no,
                "merchant": self.merchant, "turns": len(self.turns),
                "counts": self.counts, "verdict": self.verdict,
                "reason": self.reason, "created": self.created,
                "updated": self.updated}


_STORE = {}


def get(sid):
    if sid not in _STORE:
        _STORE[sid] = Session(sid)
    return _STORE[sid]


def _context_text(sess, role):
    """取上一轮同一角色的话，用于指代补全（只用于匹配，不用于展示）。"""
    for t in reversed(sess.turns):
        if t["role"] == role:
            return t["text"]
    return ""


def expand(text, sess, role):
    """指代补全：把"这个/那个"替换成上一轮的主语实体。

    只做最简单的规则——不是真正的指代消解，但能覆盖"这个不能退"这类高频说法。
    """
    if not ANAPHORA.search(text):
        return text, False
    prev = _context_text(sess, role)
    if not prev:
        return text, False
    noun = None
    for m in re.finditer(r"([\u4e00-\u9fa5A-Za-z0-9]{2,8})(?:不能|不支持|不给|退不了)", prev):
        noun = m.group(1)
        break
    if not noun:
        subject = re.search(r"(电饭煲|耳机|鞋子|手机|充电宝|沙发|杯子|衣服|冰箱|空调)", prev)
        noun = subject.group(1) if subject else None
    if not noun:
        return text, False
    return ANAPHORA.sub(noun, text, count=1), True


def evaluate(sess, role, text, matcher, rules_by_id):
    """判定一轮，并更新会话状态。

    matcher(text) 返回 [(rule, kw)]（不过滤 repeat_threshold）。
    返回本轮结果 + 会话结论。
    """
    expanded, used_ctx = expand(text, sess, role)
    hits = matcher(expanded)

    # 逐条累计
    for rule, kw in hits:
        t = rule["type"]
        sess.counts[t] = sess.counts.get(t, 0) + 1

    # 会话级结论：任一类型累计到阈值就 block
    verdict, reason = "allow", ""
    for rule, kw in hits:
        need = rule.get("repeat_threshold", 1)
        got = sess.counts.get(rule["type"], 0)
        if got >= need:
            sev = rule.get("severity")
            if sev in ("high",):
                verdict, reason = "block", (
                    f"会话内「{rule['type']}」出现 {got} 次（阈值 {need}），"
                    f"本条命中「{kw}」")
                break
            if sev in ("mid", "low") and verdict != "block":
                verdict = "warn"
                reason = (f"会话内「{rule['type']}」累计 {got} 次（阈值 {need}）")

    sess.verdict = verdict if verdict != "allow" else sess.verdict
    if reason:
        sess.reason = reason
    sess.turns.append({"role": role, "text": text, "hits": [r[0]["type"] for r in hits],
                       "ts": _now()})
    sess.updated = _now()

    return {
        "session_id": sess.sid,
        "turn": len(sess.turns),
        "text": text,
        "expanded": expanded if used_ctx else text,
        "used_context": used_ctx,
        "turn_hits": [{"type": r[0]["type"], "severity": r[0]["severity"], "kw": r[1]}
                      for r in hits],
        "counts": dict(sess.counts),
        "session_verdict": sess.verdict,
        "reason": sess.reason,
        "action": {"block": "拦截并转人工", "warn": "提示商家并记录",
                   "allow": "放行"}[sess.verdict],
    }


def save(sess):
    import db
    conn = db.connect()
    with conn:
        conn.execute(
            "INSERT INTO sessions(session_id,order_no,merchant,user_id,turns,counts,"
            "verdict,reason,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(session_id) DO UPDATE SET turns=excluded.turns,"
            " counts=excluded.counts, verdict=excluded.verdict,"
            " reason=excluded.reason, updated=excluded.updated",
            (sess.sid, sess.order_no, sess.merchant, sess.user_id, len(sess.turns),
             json.dumps(sess.counts, ensure_ascii=False), sess.verdict,
             sess.reason, sess.created, sess.updated))
    conn.close()
