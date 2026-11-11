# -*- coding: utf-8 -*-
"""三级分流：规则 → 小模型 → 大模型。

为什么不是按"句子长短"路由，而是按**不确定性**路由：
短句也可能杀人（"把验证码发我"），长句也可能完全正常（一段售后说明）。

路由规则：

  1. 规则命中且严重度=high            → 规则直接定（威胁、隐私、线下交易，这类错不起）
  2. 规则命中但文本含糊（引用条款/条件）→ 小模型复核
  3. 规则未命中但含风险词（弱信号）    → 小模型判
  4. 规则未命中且无风险信号            → 规则放行（不调模型）
  5. 小模型置信 < 0.75 或判成高风险类型 → 升级大模型

成本单位（相对值，便于算账）：规则 0、小模型 1、大模型 10。
"""
import re

HIGH_RISK = {"威胁或辱骂", "索要不必要的身份信息", "诱导线下交易", "以利益换好评"}

# 含糊表述：出现这些词，说明这句话可能在引用规则、加条件，规则容易误判
AMBIGUOUS = ["规定", "条款", "页面", "写", "同意", "原则上", "按理", "特殊情况",
             "一般情况下", "看情况", "不一定", "应该"]

# 风险词：即使没命中完整规则，出现这些也该让模型看一眼
RISK_WORDS = ["验证码", "身份证", "银行卡", "密码", "加微信", "私下", "转账",
              "差评", "好评", "五星", "评价", "评论", "删", "撤", "返现", "红包", "补差价",
              "证件", "身份证", "不管", "不接待", "拉黑", "税",
              "不退", "不能退", "不给", "处理不了", "走不了"]

RIGHT_WORDS = ["退", "换", "售后", "赔", "发票", "开票"]

COST_UNITS = {"rule": 0.0, "small": 1.0, "large": 10.0}
SMALL_CONF_FLOOR = 0.75


def decide(rule, text):
    """返回 (tier, reason)。tier ∈ rule / small / large。"""
    hit = bool(rule.get("hit"))
    sev = rule.get("severity", "")
    typ = rule.get("type", "")

    if hit and sev == "high":
        return "rule", "高严重度命中（%s），规则置信足够，不调模型" % typ

    if hit:
        if any(w in text for w in AMBIGUOUS):
            return "small", "命中「%s」但含含糊表述，交小模型复核" % typ
        if typ in HIGH_RISK:
            return "small", "命中「%s」属高风险类型，交小模型确认" % typ
        return "rule", "命中「%s」（中等严重度），规则直接定" % typ

    # 未命中：看有没有弱信号
    risks = [w for w in RISK_WORDS if w in text]
    rights = [w for w in RIGHT_WORDS if w in text]
    score = len(risks) + (1 if rights else 0)
    if score >= 2 or (risks and rights):
        return "small", "未命中规则，但含风险词 %s，交小模型判" % "、".join(risks[:3])
    if risks:
        return "small", "未命中规则，但有单个风险词「%s」" % risks[0]
    return "rule", "无风险信号，规则放行"


def escalate(small_result):
    """小模型判完之后，要不要升级大模型。"""
    if not small_result or small_result.get("hit") is None:
        return False, ""
    conf = float(small_result.get("confidence") or 0)
    if conf < SMALL_CONF_FLOOR:
        return True, "小模型置信 %.2f < %.2f" % (conf, SMALL_CONF_FLOOR)
    if small_result.get("hit") and small_result.get("type") in HIGH_RISK:
        return True, "小模型判成高风险类型「%s」，升级复核" % small_result.get("type")
    return False, ""


def simulate(rows):
    """对一批 (text, rule) 跑分流，给出分布与相对成本。"""
    dist = {"rule": 0, "small": 0, "large": 0}
    unreviewed = 0
    for text, rule in rows:
        tier, _ = decide(rule, text)
        if tier == "rule" and not rule.get("hit") and rule.get("severity") != "high":
            unreviewed += 1
        dist[tier] += 1
    n = len(rows) or 1
    avg_cost = sum(COST_UNITS[t] * c for t, c in dist.items()) / n
    return {
        "n": len(rows),
        "dist": dist,
        "share": {k: round(v / n, 3) for k, v in dist.items()},
        "avg_cost_units": round(avg_cost, 2),
        "vs_all_large": round(avg_cost / COST_UNITS["large"], 3),
    }
