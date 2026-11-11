# -*- coding: utf-8 -*-
"""答据 EvidenceQA —— 核心引擎（零第三方依赖）

链路：retrieve -> answer -> verify -> decide
每一步都可以单独替换：
  · 检索：现在用 IDF 加权的字 bigram 匹配，可替换为向量检索
  · 回答：默认 mock（只念主条款、故意忽略例外，用来复现"模型答得太顺"），
          设置环境变量后走真实 LLM
  · 对账：规则优先，检查例外冲突与空引用
  · 裁决：5 类信号 + 优先级，输出唯一原因码
"""
import json
import math
import os
import re
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"

# ------------------------------------------------------------------ 可调阈值
THRESHOLD_RETRIEVAL = 0.45   # 低于此分视为检索不足
AMOUNT_LIMIT = 500           # 金额超过此值视为高危
TOP_K = 5

HIGH_RISK_TERMS = [
    "投诉", "起诉", "报警", "诈骗", "赔偿", "三倍", "人身", "受伤", "过敏",
    "盗刷", "资金", "法院", "律师", "曝光", "媒体",
]
IDENTITY_TERMS = ["我的订单", "我买的", "帮我查", "订单号", "查一下订单", "物流到哪"]
NEGATIVE_TERMS = ["垃圾", "骗人", "骗子", "没人管", "态度差", "退钱"]

# 例外条件的口语说法：例外写得规范，用户说得随意，中间要有一张映射表
EXCEPTION_SYNONYMS = {
    "已剪吊牌": ["吊牌", "剪标", "剪了牌"],
    "已洗涤": ["洗涤", "洗过", "洗了", "下过水", "水洗"],
    "缺少配件": ["缺配件", "配件少", "配件丢"],
    "大促": ["大促", "活动价", "618", "双11"],
    "秒杀": ["秒杀", "抢购"],
    "优惠券": ["优惠券", "用券", "领券"],
    "满减": ["满减", "凑单"],
    "超重": ["超重", "太重"],
    "超区": ["超区", "偏远"],
    "缺货": ["缺货", "没货", "卖完"],
    "部分退货": ["部分退货", "只退一部分", "退其中一件"],
    "赠品缺失": ["赠品丢", "赠品没", "赠品少"],
    "已拆封": ["拆封", "拆开", "打开过"],
    "预售": ["预售", "预付"],
    "账户注销": ["注销"],
}

REASON_TEXT = {
    "HIGH_RISK": ("涉及赔付、责任认定或资金安全，不能由模型自行答复",
                  "核对订单金额与责任方，必要时升级主管"),
    "CITATION_MISMATCH": ("答案与条款的例外条件冲突，模型忽略了限定条件",
                          "核对商品状态（吊牌 / 洗水 / 配件 / 包装）后按例外条款处理"),
    "NO_EVIDENCE": ("没有检索到可以支撑结论的条款",
                    "记录问题，进入知识缺口清单"),
    "IDENTITY_GAP": ("需要核验身份与订单归属，模型无权查询",
                     "先做身份与订单核验"),
    "REPEAT": ("同一问题重复提问，上一轮答复没有解决",
               "附上此前答复，换一种解释方式"),
}


# ------------------------------------------------------------------ 工具函数
def load_jsonl(path):
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def load_clauses():
    return load_jsonl(DATA / "clauses.jsonl")


def grams(text):
    t = re.sub(r"\s+", "", text.lower())
    g = {t[i:i + 2] for i in range(len(t) - 1)}
    g |= set(re.findall(r"[a-z0-9]+", text.lower()))
    return g


def exception_hit(exception, query):
    keys = EXCEPTION_SYNONYMS.get(exception, [exception.replace("已", "")])
    return any(k in query for k in keys)


def parse_amount(query):
    m = re.findall(r"(\d+(?:\.\d+)?)\s*(?:元|块)", query)
    return max([float(x) for x in m], default=0.0)


# ------------------------------------------------------------------ 检索
class Retriever:
    def __init__(self, clauses):
        self.clauses = clauses
        self.docs = {}
        for c in clauses:
            text = " ".join([
                c.get("title", ""), c.get("text", ""), c.get("scope", ""),
                " ".join(c.get("exceptions", [])),
            ])
            self.docs[c["id"]] = grams(text)
        df = {}
        for d in self.docs.values():
            for g in d:
                df[g] = df.get(g, 0) + 1
        self.N = len(clauses)
        self.idf = {g: math.log(1 + self.N / (1 + v)) for g, v in df.items()}
        self.default_idf = math.log(1 + self.N)
        # 预先算好每条条款的向量长度（TF=1，权重取 idf）
        self.docnorm = {
            cid: math.sqrt(sum(self.idf.get(g, self.default_idf) ** 2 for g in d)) or 1.0
            for cid, d in self.docs.items()
        }

    def search(self, query, k=TOP_K):
        """余弦相似度：TF=1、权重 IDF。分数落在 0~1，越大越相关。"""
        qg = grams(query)
        if not qg:
            return []
        qnorm = math.sqrt(sum(self.idf.get(g, self.default_idf) ** 2 for g in qg)) or 1.0
        scored = []
        for c in self.clauses:
            hit = qg & self.docs[c["id"]]
            if not hit:
                continue
            dot = sum(self.idf.get(g, self.default_idf) ** 2 for g in hit)
            s = dot / (qnorm * self.docnorm[c["id"]])
            scored.append((round(s, 3), c))
        scored.sort(key=lambda x: (-x[0], x[1]["id"]))
        return [(s, c) for s, c in scored[:k]]


# ------------------------------------------------------------------ 回答
def mock_answer(query, clause):
    """模拟一个"答得太顺"的模型：只念主条款，不看例外，也不说自己不确定。"""
    answer = "根据《%s》：%s" % (clause["title"], clause["text"])
    if re.search(r"能退|可以退|退货|退吗|退掉|退全款", query):
        answer += " 结论：可以申请退货。"
    elif re.search(r"价保|补差|降价", query):
        answer += " 结论：可以申请价保补差。"
    elif re.search(r"换货|能换|换吗", query):
        answer += " 结论：可以申请换货。"
    return answer, [clause["id"]]


def llm_answer(query, clauses):
    """真实模型通道（可选）。没有配置环境变量时返回 None，自动回落 mock。"""
    base = os.environ.get("EVIDENCEQA_LLM_BASE")
    key = os.environ.get("EVIDENCEQA_LLM_KEY")
    model = os.environ.get("EVIDENCEQA_LLM_MODEL", "gpt-4o-mini")
    if not (base and key):
        return None
    ctx = "\n".join("[%s] %s：%s" % (c["id"], c["title"], c["text"]) for c in clauses)
    prompt = (
        "只允许使用下列条款回答，每个结论必须标注条款 ID。"
        "如果条款不足以回答，直接输出 NO_ANSWER。\n\n条款：\n%s\n\n问题：%s\n"
        "输出 JSON：{\"answer\": \"...\", \"citations\": [\"条款ID\"]}" % (ctx, query)
    )
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        payload = json.loads(data["choices"][0]["message"]["content"])
        ans = payload.get("answer", "")
        if not ans or "NO_ANSWER" in ans:
            return None
        return ans, payload.get("citations", []) or []
    except Exception:
        return None


def answer(query, clause):
    got = llm_answer(query, [clause]) if clause else None
    if got:
        return got
    if not clause:
        return "", []
    return mock_answer(query, clause)


# ------------------------------------------------------------------ 引用对账
def verify(query, ans, citations, hits, by_id):
    problems = []
    if not citations:
        problems.append("NO_CITATION")
    for cid in citations:
        if cid not in by_id:
            problems.append("MISSING_CLAUSE:" + cid)
    asserts_ok = bool(re.search(r"可以|支持|允许", ans))
    for _, c in hits[:3]:
        for ex in c.get("exceptions", []):
            if asserts_ok and exception_hit(ex, query):
                problems.append("EXCEPTION_CONFLICT:%s:%s" % (c["id"], ex))
    return problems


# ------------------------------------------------------------------ 转人工裁决
def decide(query, hits, problems, repeat, has_order):
    top = hits[0][0] if hits else 0.0
    signals = {
        "HIGH_RISK": any(t in query for t in HIGH_RISK_TERMS)
                     or parse_amount(query) > AMOUNT_LIMIT,
        "CITATION_MISMATCH": any(
            p.startswith("EXCEPTION_CONFLICT") or p in ("NO_CITATION",)
            or p.startswith("MISSING_CLAUSE") for p in problems),
        "IDENTITY_GAP": (any(t in query for t in IDENTITY_TERMS) and not has_order),
        "NO_EVIDENCE": (not hits) or top < THRESHOLD_RETRIEVAL,
        "REPEAT": bool(repeat),
    }
    for code in ("HIGH_RISK", "CITATION_MISMATCH", "IDENTITY_GAP", "NO_EVIDENCE", "REPEAT"):
        if signals.get(code):
            why, todo = REASON_TEXT[code]
            return {
                "decision": "escalate",
                "reason_code": code,
                "explain": why,
                "suggestion": todo,
                "signals": signals,
            }
    return {"decision": "answer", "reason_code": "", "explain": "", "suggestion": "",
            "signals": signals}


# ------------------------------------------------------------------ 对外入口
class Engine:
    def __init__(self):
        self.clauses = load_clauses()
        self.by_id = {c["id"]: c for c in self.clauses}
        self.retriever = Retriever(self.clauses)

    def ask(self, query, has_order=False, repeat=False, session_id=""):
        hits = self.retriever.search(query)
        top_clause = hits[0][1] if hits else None
        ans, citations = answer(query, top_clause)
        problems = verify(query, ans, citations, hits, self.by_id)
        verdict = decide(query, hits, problems, repeat, has_order)
        return {
            "session_id": session_id,
            "query": query,
            "hits": [{"score": s, "id": c["id"], "title": c["title"]} for s, c in hits],
            "answer": ans,
            "citations": citations,
            "problems": problems,
            "decision": verdict["decision"],
            "reason_code": verdict["reason_code"],
            "explain": verdict["explain"],
            "suggestion": verdict["suggestion"],
            "signals": verdict["signals"],
            "clauses": [
                {"id": c["id"], "title": c["title"], "text": c["text"],
                 "scope": c.get("scope", ""), "exceptions": c.get("exceptions", [])}
                for _, c in hits[:3]
            ],
        }


if __name__ == "__main__":
    eng = Engine()
    for q in ["七天无理由从哪天开始算", "吊牌剪了还能退吗", "支持以旧换新吗", "我要投诉你们"]:
        r = eng.ask(q)
        print(q, "->", r["decision"], r["reason_code"], "top=", r["hits"][0]["score"] if r["hits"] else 0)
