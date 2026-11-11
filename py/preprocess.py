# -*- coding: utf-8 -*-
"""文本预处理：所有进入判定的文本都先过这里。

为什么需要它：商家打的是全角数字（「３２号登机口」）、带多余空格、偶尔贴一大段，
不统一处理的话，规则里的「32 号」永远匹配不上。

处理顺序：
  1. NFKC 归一化（全角 → 半角，英文/数字/标点）
  2. 合并连续空白、去掉首尾空白
  3. 超长截断（默认 500 字），并在结果里标记 truncated
  4. 空文本直接判为无效

返回 (text, meta)，meta 记录做过哪些动作，方便排查"为什么没命中"。
"""
import re
import unicodedata

MAX_LEN = 500


def normalize(text, max_len=MAX_LEN):
    raw = text or ""
    if not raw.strip():
        return "", {"ok": False, "reason": "empty"}

    t = unicodedata.normalize("NFKC", raw)
    changed_nfkc = (t != raw)

    t = re.sub(r"[ \t\u3000]+", " ", t)
    t = re.sub(r"\n{2,}", "\n", t).strip()

    truncated = False
    if len(t) > max_len:
        t = t[:max_len]
        truncated = True

    return t, {
        "ok": True,
        "nfkc": changed_nfkc,
        "truncated": truncated,
        "length": len(t),
    }


if __name__ == "__main__":
    for s in ["３２号登机口有旅客晕倒", "  加我微信  转我 ", "长" * 900, "   "]:
        t, m = normalize(s)
        print(repr(s[:20]), "->", repr(t[:20]), m)
