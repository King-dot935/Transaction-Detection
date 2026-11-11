# -*- coding: utf-8 -*-
"""四类数据的结构与文本卫生校验。

  rules.jsonl      规则库   —— 字段齐全、id 唯一、严重度合法、优先级是整数
  clauses.jsonl    知识库   —— id 命名规范、必填字段、是否被引用
  scenarios/*.jsonl 标注集  —— 对话结构、role 取值、expect 字段
  运行数据          SQLite   —— 表结构、状态取值

用法：python validate_data.py   （有错退出码 1，可挂 CI）
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

import preprocess

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
SEVERITIES = {"high", "mid", "low", "none"}
ROLES = {"user", "merchant"}
CLAUSE_ID = re.compile(r"^[A-Z]+_[0-9]+(\.[0-9]+)?$")

errors, warns = [], []


def load(name):
    return [json.loads(l) for l in (DATA / name).read_text(encoding="utf-8").splitlines() if l.strip()]


def check_rules():
    rules = load("rules.jsonl")
    ids = set()
    for r in rules:
        rid = r.get("id")
        if not rid:
            errors.append("规则缺 id：%s" % json.dumps(r, ensure_ascii=False)[:60])
            continue
        if rid in ids:
            errors.append("规则 id 重复：%s" % rid)
        ids.add(rid)
        for k in ("type", "severity", "priority"):
            if k not in r:
                errors.append("规则 %s 缺字段 %s" % (rid, k))
        if r.get("severity") not in SEVERITIES:
            errors.append("规则 %s 严重度非法：%s" % (rid, r.get("severity")))
        if not isinstance(r.get("priority"), int):
            errors.append("规则 %s priority 不是整数" % rid)
        if r.get("negative"):
            if not r.get("patterns"):
                errors.append("负向规则 %s 没有 patterns" % rid)
        elif not r.get("patterns") and not (r.get("rights") and r.get("denials")):
            errors.append("规则 %s 既没有 patterns 也没有 rights/denials" % rid)
        for p in r.get("patterns", []):
            t, m = preprocess.normalize(p)
            if t != p:
                warns.append("规则 %s 触发词含全角/多余空白：%r → %r" % (rid, p, t))
    print("规则库：%d 条，id 唯一 %d 个" % (len(rules), len(ids)))
    return rules


def check_clauses():
    clauses = load("clauses.jsonl")
    ids = set()
    for c in clauses:
        cid = c.get("id", "")
        if not cid:
            errors.append("条款缺 id")
            continue
        if not CLAUSE_ID.match(cid):
            errors.append("条款 id 不符合 域_数字 规范：%s" % cid)
        if cid in ids:
            errors.append("条款 id 重复：%s" % cid)
        ids.add(cid)
        for k in ("title", "text", "scope"):
            if not c.get(k):
                errors.append("条款 %s 缺字段 %s" % (cid, k))
    print("知识库：%d 条" % len(clauses))
    return clauses


def check_scenarios():
    total = 0
    all_ids = {}
    merchant_texts = {}
    for name in ("scenarios.jsonl", "holdout.jsonl", "holdout2.jsonl", "holdout3.jsonl"):
        items = load(name)
        total += len(items)
        for s in items:
            sid = s.get("id", "")
            if not sid:
                errors.append("%s 有样本缺 id" % name)
                continue
            if sid in all_ids:
                errors.append("id 跨集合重复：%s（%s / %s）" % (sid, all_ids[sid], name))
            all_ids[sid] = name
            turns = s.get("turns") or []
            if not turns:
                errors.append("%s %s 没有 turns" % (name, sid))
            for t in turns:
                if t.get("role") not in ROLES:
                    errors.append("%s %s role 非法：%s" % (name, sid, t.get("role")))
                if not (t.get("text") or "").strip():
                    errors.append("%s %s 有空消息" % (name, sid))
                elif t.get("role") == "merchant":
                    key = preprocess.normalize(t["text"])[0]
                    if key in merchant_texts and merchant_texts[key] != name:
                        errors.append("商家原话跨集合重复：%s / %s" % (
                            merchant_texts[key], name))
                    merchant_texts.setdefault(key, name)
            exp = s.get("expect") or {}
            if "intervene" not in exp:
                errors.append("%s %s 缺 expect.intervene" % (name, sid))
            elif exp["intervene"]:
                turn_no = exp.get("turn")
                if not isinstance(turn_no, int) or not 0 <= turn_no < len(turns):
                    errors.append("%s %s 应介入但 turn 缺失或越界" % (name, sid))
                if exp.get("type") not in set(r for r in (
                        "拒绝法定权利", "扩大退货例外", "诱导放弃权利", "以利益换好评",
                        "诱导线下交易", "威胁或辱骂", "索要不必要的身份信息",
                        "责任推诿", "拒绝开票", "反复拖延", "虚假承诺")):
                    errors.append("%s %s 的期望类型非法：%s" % (
                        name, sid, exp.get("type")))
    print("标注集：%d 段对话" % total)


def check_db():
    p = BASE / "sentinel.db"
    if not p.exists():
        warns.append("sentinel.db 还不存在（跑一次 demo.py 会创建）")
        return
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("judge_logs", "review_queue", "feedback", "eval_runs"):
        if t not in tables:
            errors.append("运行数据缺表：%s" % t)
    if "review_queue" in tables:
        bad = conn.execute("SELECT COUNT(*) n FROM review_queue "
                           "WHERE status NOT IN ('pending','approved','rejected')").fetchone()["n"]
        if bad:
            errors.append("review_queue 有 %d 条状态非法" % bad)
        no_reviewer = conn.execute(
            "SELECT COUNT(*) n FROM review_queue WHERE status<>'pending' AND "
            "(reviewer IS NULL OR reviewer='')").fetchone()["n"]
        if no_reviewer:
            errors.append("有 %d 条已裁决记录没有审核人" % no_reviewer)
    if "judge_logs" in tables:
        empty = conn.execute("SELECT COUNT(*) n FROM judge_logs WHERE text IS NULL OR TRIM(text)=''").fetchone()["n"]
        if empty:
            errors.append("judge_logs 有 %d 条空文本" % empty)
    conn.close()
    print("运行数据：表齐全" if not errors else "运行数据：有问题")


def main():
    check_rules()
    check_clauses()
    check_scenarios()
    check_db()
    print()
    if warns:
        print("提醒：")
        for w in warns:
            print("  - " + w)
    if errors:
        print("错误：")
        for e in errors:
            print("  - " + e)
        sys.exit(1)
    print("数据校验通过")


if __name__ == "__main__":
    main()
