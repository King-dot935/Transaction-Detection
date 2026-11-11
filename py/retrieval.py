# -*- coding: utf-8 -*-
"""检索层：倒排索引 + BM25 + 多路融合（RRF）。

零依赖（只用 sqlite3）。解决现在的两个瓶颈：
  · 相似召回靠"最近 N 条兜底扫描"，O(N)
  · 打分只用 Dice，长句被短句压、常见 bigram 权重过大

结构：
  rt_docs(id, text, dl)          dl = 文档 bigram 数（长度归一用）
  rt_post(tok, doc_id, tf)       token → 文档 的倒排表
  rt_meta(k, v)                  存 N / avgdl / 版本

BM25:
  score = Σ idf(t) · (tf·(k1+1)) / (tf + k1·(1-b+b·dl/avgdl))
  idf(t) = ln(1 + (N-df+0.5)/(df+0.5))
"""
import math
import re
import sqlite3
import unicodedata
from pathlib import Path

K1, B = 1.5, 0.75
BASE = Path(__file__).resolve().parent


# ---------------------------------------------------------------- 分词
def tokens(text):
    """中文用字 bigram；英文数字按词。"""
    s = unicodedata.normalize("NFKC", str(text or ""))
    s = re.sub(r"[\s\W_]+", "", s, flags=re.U).lower()
    if len(s) < 2:
        return [s] if s else []
    out = [s[i:i + 2] for i in range(len(s) - 1)]
    if len(out) < 3:                      # 短句补单字，避免召回太少
        out += list(s)
    return out


def tf_of(text):
    d = {}
    for t in tokens(text):
        d[t] = d.get(t, 0) + 1
    return d


# ---------------------------------------------------------------- 建索引
SCHEMA = """
CREATE TABLE IF NOT EXISTS rt_docs(
  doc_id INTEGER PRIMARY KEY, text TEXT, dl INTEGER);
CREATE TABLE IF NOT EXISTS rt_post(
  tok TEXT, doc_id INTEGER, tf INTEGER);
CREATE INDEX IF NOT EXISTS idx_rt_tok ON rt_post(tok);
CREATE INDEX IF NOT EXISTS idx_rt_doc ON rt_post(doc_id);
CREATE TABLE IF NOT EXISTS rt_meta(k TEXT PRIMARY KEY, v TEXT);
"""


class Index:
    def __init__(self, db_path=None):
        self.conn = sqlite3.connect(db_path or (BASE / "sentinel.db"))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._n = None
        self._avg = None

    # ---- 写 ----
    def add(self, doc_id, text, commit=True):
        tf = tf_of(text)
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO rt_docs(doc_id,text,dl) VALUES(?,?,?)",
                              (doc_id, text, sum(tf.values())))
            self.conn.execute("DELETE FROM rt_post WHERE doc_id=?", (doc_id,))
            self.conn.executemany("INSERT INTO rt_post(tok,doc_id,tf) VALUES(?,?,?)",
                                  [(t, doc_id, c) for t, c in tf.items()])
        self._n = None
        return self

    def rebuild(self, rows):
        """rows: [(doc_id, text)] —— 批量写入，一次事务。"""
        docs, posts = [], []
        for doc_id, text in rows:
            tf = tf_of(text)
            docs.append((doc_id, text, sum(tf.values())))
            posts += [(t, doc_id, c) for t, c in tf.items()]
        with self.conn:
            self.conn.execute("DELETE FROM rt_docs")
            self.conn.execute("DELETE FROM rt_post")
            self.conn.executemany(
                "INSERT OR REPLACE INTO rt_docs(doc_id,text,dl) VALUES(?,?,?)", docs)
            self.conn.executemany(
                "INSERT INTO rt_post(tok,doc_id,tf) VALUES(?,?,?)", posts)
        self._n = None
        return self

    def _stats(self):
        if self._n is None:
            self._n = self.conn.execute("SELECT COUNT(*) n FROM rt_docs").fetchone()["n"] or 0
            avg = self.conn.execute("SELECT AVG(dl) a FROM rt_docs").fetchone()["a"]
            self._avg = avg or 1.0
        return self._n, self._avg

    # ---- 读 ----
    def df(self, tok):
        return self.conn.execute("SELECT COUNT(*) n FROM rt_post WHERE tok=?",
                                 (tok,)).fetchone()["n"]

    def search(self, query, k=10, max_postings=4000):
        """返回 [(doc_id, score)]，按 BM25 降序。"""
        n, avgdl = self._stats()
        if not n:
            return []
        tf = tf_of(query)
        if not tf:
            return []

        # 只取 df 较小的若干 token，避免常见词把候选撑爆（停用词效应）
        scored_toks = sorted(tf, key=lambda t: self.df(t))
        use = scored_toks[:min(6, len(scored_toks))]

        cand = {}
        for tok in use:
            df = self.df(tok)
            if df == 0:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            # JOIN 一次取回 tf 和 dl，避免逐行再查（N+1）
            rows = self.conn.execute(
                "SELECT p.doc_id AS doc_id, p.tf AS tf, d.dl AS dl FROM rt_post p "
                "JOIN rt_docs d ON d.doc_id=p.doc_id WHERE p.tok=? LIMIT ?",
                (tok, max_postings)).fetchall()
            for r in rows:
                dl = r["dl"] or 1
                denom = r["tf"] + K1 * (1 - B + B * dl / avgdl)
                cand[r["doc_id"]] = cand.get(r["doc_id"], 0.0) + \
                    idf * (r["tf"] * (K1 + 1)) / denom
        return sorted(cand.items(), key=lambda x: -x[1])[:k]

    def close(self):
        self.conn.close()


# ---------------------------------------------------------------- 多路融合
def rrf(rankings, k=60):
    """Reciprocal Rank Fusion：把多路召回的结果合并。

    rankings: [[doc_id, doc_id, ...], ...]  每路按相关度降序
    score = Σ 1/(k + rank)
    """
    score = {}
    for lst in rankings:
        for rank, doc in enumerate(lst, 1):
            score[doc] = score.get(doc, 0.0) + 1.0 / (k + rank)
    return sorted(score.items(), key=lambda x: -x[1])
