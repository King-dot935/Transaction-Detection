# -*- coding: utf-8 -*-
"""对比三种检索：暴力 Dice 扫描 / 倒排+BM25 / 融合。

用合成语料（真实话术 + 变体）跑，看耗时与 Top-1 一致性。
"""
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import recall
import retrieval
import textnorm

BASE = Path(__file__).resolve().parent

SEEDS = [
    "可以给你退，你先把差评删了", "特价商品不退不换，这是店铺规定", "加我微信，转账给我我给你处理",
    "你把身份证拍给我核一下", "我们这边一律不开票", "再等等吧，过几天再说",
    "这个我们不管", "好评返现5元", "拆封了就不能退", "保价不关我们事",
    "你投诉也没用", "签收之后出问题我们不管", "没有发票这一说", "私下转更快，平台要走流程",
    "质量问题我们负责", "七天后退不了", "这是公司规定", "你先删差评我就退",
    "拍个证件过来我核一下", "给你补偿5元删评",
]
SUFFIX = ["", "啊", "呢", "谢谢理解", "行吗", "可以吗"]


def build_corpus(n=2000):
    """把种子句加变体，凑到 n 条。"""
    docs, seen = [], set()
    i = 0
    limit = len(SEEDS) * len(SUFFIX)
    while len(docs) < n:
        s = SEEDS[i % len(SEEDS)]
        # 前 limit 条靠后缀区分，之后靠编号——判重要看 i，不能看 len(docs)
        tag = "" if i < limit else "#%d" % i
        v = s + SUFFIX[(i // len(SEEDS)) % len(SUFFIX)] + tag
        i += 1
        key = textnorm.squash(v)
        if key in seen:
            continue
        seen.add(key)
        docs.append((len(docs) + 1, v))
    return docs


def brute_force(docs, q, k=5):
    qs = recall.bigrams(textnorm.squash(q))
    scored = []
    for did, text in docs:
        d = recall.dice(qs, recall.bigrams(textnorm.squash(text)))
        if d > 0:
            scored.append((did, d))
    scored.sort(key=lambda x: -x[1])
    return scored[:k]


def main():
    N = 2000
    docs = build_corpus(N)
    print(f"语料 {len(docs)} 条")

    idx = retrieval.Index(BASE / "_bench.db")
    idx.rebuild(docs)
    print("倒排索引已建")

    queries = ["能退吗", "可以退货吗", "删差评就退款", "加微信转账", "拍身份证",
               "开不了发票", "过几天再说", "好评返现", "拆封不退", "保价不关事"]

    # 暴力扫描
    t0 = time.perf_counter()
    bf = {q: brute_force(docs, q) for q in queries}
    t_bf = (time.perf_counter() - t0) / len(queries) * 1000

    # 倒排
    t0 = time.perf_counter()
    iv = {q: idx.search(q, k=5) for q in queries}
    t_iv = (time.perf_counter() - t0) / len(queries) * 1000

    # Top-1 一致性
    agree = 0
    for q in queries:
        a = bf[q][0][0] if bf[q] else None
        b = iv[q][0][0] if iv[q] else None
        if a is not None and a == b:
            agree += 1

    print(f"\n单次查询耗时（{len(queries)} 条平均）")
    print(f"  暴力 Dice 扫描   {t_bf:7.2f} ms")
    print(f"  倒排 + BM25      {t_iv:7.2f} ms")
    print(f"  加速比           {t_bf / t_iv:.1f}×")
    print(f"\nTop-1 与暴力一致：{agree}/{len(queries)}")

    print("\n样例：'能退吗'")
    for did, sc in iv["能退吗"][:3]:
        text = dict(docs)[did]
        print(f"   {sc:6.2f}  {text}")

    idx.close()


if __name__ == "__main__":
    main()
