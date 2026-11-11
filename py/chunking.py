# -*- coding: utf-8 -*-
"""切块：把一条消息切成判定单元。

现状（切块前）：整条消息当一个单元 → 负向抑制按整条判断。
  坏处：「可以给你退，你先把差评删了」里有「可以退」，整条会被当成正常沟通放过去。
  而这句恰恰是最典型的违规——承诺 + 要挟在同一条消息里。

切块后：按标点切分句，**每个分句独立判定**：
  分句1「可以给你退」        → 命中负向抑制，放行
  分句2「你先把差评删了」    → 命中 R04，介入
  结果：整条消息按最严重的分句处理 → 介入

切分符：，。；！？、换行。合并过短的片段（<2 字），避免「嗯」「啊」这种碎片。
"""
import re

SPLIT_RE = re.compile(r"[，。；！？,;!?、\n\r]+")
MIN_LEN = 2


def split_clauses(text, min_len=MIN_LEN):
    """返回分句列表；若切完只剩一片，返回原文。"""
    if not text:
        return []
    parts = [p.strip() for p in SPLIT_RE.split(text)]
    parts = [p for p in parts if len(p) >= min_len]
    if not parts:
        t = (text or "").strip()
        return [t] if t else []
    return parts


if __name__ == "__main__":
    cases = [
        "可以给你退，你先把差评删了",
        "您好，有什么可以帮您",
        "特价商品不退不换的，这是店铺规定",
        "可以退的，您申请一下就行",
    ]
    for c in cases:
        print(c, "→", split_clauses(c))
