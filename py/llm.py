# -*- coding: utf-8 -*-
"""模型判定层（按层配置）。

两级模型：
    小模型   LLM_SMALL_BASE / LLM_SMALL_KEY / LLM_SMALL_MODEL   便宜、快，判"看起来像"的句子
    大模型   LLM_LARGE_BASE / LLM_LARGE_KEY / LLM_LARGE_MODEL   贵，复核小模型不确定的

兼容旧写法：只配 LLM_BASE / LLM_KEY / LLM_MODEL 时，等同只配了大模型。

约定：**某一层没配，就返回 None**，上层回落规则。
绝不编造一个"模型返回"出来——这比不接模型更糟。
"""
import json
import os
import urllib.request

TYPES = [
    "拒绝法定权利", "扩大退货例外", "诱导放弃权利", "以利益换好评",
    "诱导线下交易", "威胁或辱骂", "索要不必要的身份信息", "责任推诿",
    "拒绝开票", "反复拖延", "虚假承诺", "正常沟通",
]

SYSTEM = (
    "你是电商平台的会话合规判定器。判断商家这句话有没有侵犯消费者权益。"
    "只输出 JSON，不要解释。格式："
    '{"hit": true/false, "type": "下面列表之一", "confidence": 0~1, "reason": "一句话"}'
    "类型列表：" + "、".join(TYPES) +
    "。正常沟通填 hit=false、type=正常沟通。"
)


def _cfg(tier):
    pre = "LLM_" + tier.upper() + "_"
    base = os.environ.get(pre + "BASE")
    key = os.environ.get(pre + "KEY", "")
    model = os.environ.get(pre + "MODEL", "")
    if tier == "large":                      # 兼容旧的单模型配置
        base = base or os.environ.get("LLM_BASE")
        key = key or os.environ.get("LLM_KEY", "")
        model = model or os.environ.get("LLM_MODEL", "")
    return base, key, model


class _HttpLLM:
    name = "http"

    def __init__(self, tier):
        base, key, model = _cfg(tier)
        if not base:
            raise RuntimeError("未配置 %s 模型" % tier)
        self.tier = tier
        self.base = base.rstrip("/")
        self.key = key
        self.model = model or "deepseek-chat"

    def judge(self, text, timeout=10.0):
        body = json.dumps({
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": text}],
        }).encode("utf-8")
        req = urllib.request.Request(
            self.base + "/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.key})
        with urllib.request.urlopen(req, timeout=max(0.1, float(timeout))) as r:
            data = json.loads(r.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"].strip()
        content = content.replace("```json", "").replace("```", "").strip()
        try:
            obj = json.loads(content)
        except Exception:
            return {"provider": self.tier, "tier": self.tier, "hit": False,
                    "type": "解析失败", "confidence": 0.0, "reason": content[:80]}
        hit = obj.get("hit")
        typ = str(obj.get("type", "")).strip()
        try:
            confidence = float(obj.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = min(1.0, max(0.0, confidence))
        if not isinstance(hit, bool):
            hit = None
        if typ not in TYPES:
            return {"provider": self.tier, "tier": self.tier, "model": self.model,
                    "hit": hit, "type": "解析失败", "confidence": 0.0,
                    "reason": "模型返回了未登记的类型：%s" % typ[:40]}
        return {
            "provider": self.tier, "tier": self.tier, "model": self.model,
            "hit": hit,
            "type": typ,
            "confidence": confidence,
            "reason": obj.get("reason", ""),
        }


_cache = {}


def get_provider(tier="large"):
    if tier in _cache:
        return _cache[tier]
    base, key, _ = _cfg(tier)
    if not base or not key:
        _cache[tier] = None
        return None
    _cache[tier] = _HttpLLM(tier)
    return _cache[tier]


def available(tier="large"):
    try:
        return get_provider(tier) is not None
    except Exception:
        return False


def judge(text, tier="large", timeout=10.0):
    """返回该层模型判定；未配置返回 None，出错返回带 error 的字典。"""
    try:
        p = get_provider(tier)
        if p is None:
            return None
        return p.judge(text, timeout=timeout)
    except Exception as e:
        return {"provider": tier, "tier": tier, "error": str(e)[:120],
                "hit": None, "type": "", "confidence": 0, "reason": ""}


def status():
    """报告每一层配了什么，不发起请求。"""
    out = {}
    for tier in ("small", "large"):
        base, key, model = _cfg(tier)
        host = ""
        if base:
            host = base.split("//")[-1].split("/")[0]
        out[tier] = {
            "configured": bool(base and key),
            "model": model or "",
            "host": host,
            "has_key": bool(key),
        }
    return out
