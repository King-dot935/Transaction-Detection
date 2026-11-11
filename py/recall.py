# -*- coding: utf-8 -*-
"""相似问召回：归一化、精确键、SimHash 分桶、bigram Dice 打分。

零依赖，全部标准库。思路：
    1. 归一化（全半角/标点/空白）→ 精确键，O(1) 命中
    2. 字 bigram 集合 + 64 位 SimHash → 分成 4 段 16 位做 LSH 桶
    3. 同桶候选再用 Dice 相似度精排，避免 SimHash 的假阳性
"""
import hashlib
import re
import unicodedata

# 相似度阈值：≥0.86 认为几乎同一句；0.72~0.86 需要"命中同一个触发词"才认
TH_EXACT = 0.62    # ≥ 这个分：近似重复，直接认
TH_FUZZY = 0.28    # ≥ 这个分：需要"共享触发词"或人工确认过才认

STOP = set("的了呢吧啊呀哦嗯就是很也都还再又和与及")   # 语气词，对相似度贡献低


def normalize(text):
    """归一化：清洗 → 同义/模板归一 → 去标点空白。

    注意：这里用的是 textnorm.squash，所以「能退吗」和「可以退货吗」
    会归一到同一个键（能否退货）。精确命中因此表示归一化后相同，
    而不是原文相同。
    """
    try:
        import textnorm
        return textnorm.squash(text)
    except Exception:
        s = unicodedata.normalize("NFKC", str(text or ""))
        s = re.sub(r"[\s\W_]+", "", s, flags=re.U)
        return s.lower()


def key_of(norm_text):
    return hashlib.sha1(norm_text.encode("utf-8")).hexdigest()[:16]


def bigrams(norm_text):
    if len(norm_text) < 2:
        return {norm_text} if norm_text else set()
    g = {norm_text[i:i + 2] for i in range(len(norm_text) - 1)}
    # 单字兜底：短句时 bigram 太少，补上单字
    if len(g) < 4:
        g |= set(norm_text)
    return g


def _h64(s):
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big")


def simhash(norm_text):
    """64 位 SimHash：对每个 bigram 取哈希，按位投票。"""
    v = [0] * 64
    for g in bigrams(norm_text):
        h = _h64(g)
        for i in range(64):
            v[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(64):
        if v[i] > 0:
            out |= (1 << i)
    return out


def bands(h64, n=4):
    """切成 n 段，每段 16 位，做 LSH 桶键。"""
    width = 64 // n
    mask = (1 << width) - 1
    return ["%d:%d" % (i, (h64 >> (i * width)) & mask) for i in range(n)]


def dice(a, b):
    """集合 Dice 系数：2|A∩B| / (|A|+|B|)。"""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return 2.0 * inter / (len(a) + len(b))


def similarity(t1, t2):
    """两段原文的相似度（0~1）。"""
    return dice(bigrams(normalize(t1)), bigrams(normalize(t2)))
