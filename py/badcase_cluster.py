# -*- coding: utf-8 -*-
"""bad case 聚类：把"一条条失败"聚成"一类类问题"。

三类来源合并成一个池子：
  1. 离线评测失败（漏判 / 误报 / 类型错）
  2. 线上人工推翻规则（review_queue 里 rule_hit=0 但人判 approve）
  3. 用户点了「误报」（feedback.useful=0）

做法（零依赖）：
  · 字 bigram + TF-IDF 稀疏向量
  · 余弦相似度建图，Union-Find 按阈值合并
  · 每簇给代表样本、共有特征词、期望类型、建议动作

用法：
    python badcase_cluster.py                 # 默认阈值 0.42
    python badcase_cluster.py --th 0.35       # 放松，簇更少更大
    python badcase_cluster.py --source eval   # 只用离线失败
"""
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import db
import detector
import recall
import run_eval
import textnorm

BASE = Path(__file__).resolve().parent
SETS = ["scenarios.jsonl", "holdout.jsonl", "holdout2.jsonl", "holdout3.jsonl"]


# 领域术语：用于描述"这一簇在说什么"，比 bigram 可读
TERMS = ["评价", "差评", "好评", "五星", "删评", "改评", "撤销",
         "发票", "开票", "含税", "税率", "专票",
         "微信", "转账", "私下", "线下", "换个地方", "加好友",
         "身份证", "证件", "拍照", "验证码",
         "退款", "退钱", "退货", "退换", "运费", "差价",
         "拆封", "拆了", "不能退", "不退",
         "投诉", "举报", "威胁", "封号", "拉黑", "不接待",
         "签收", "责任", "不管", "拖延", "下周", "再等等",
         "返现", "红包", "补偿", "五星好评"]


# ---------------------------------------------------------------- 取失败样本
def _utterance(set_name, sid, turn):
    """取某段对话里指定的那一轮，商家说了什么。"""
    path = Path(__file__).resolve().parent / "data" / (set_name + ".jsonl")
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d["id"] != sid:
            continue
        turns = d.get("turns") or []
        t = int(turn)
        if 0 <= t < len(turns):
            return turns[t]["text"]
    return ""


def from_eval():
    det = detector.Detector()
    out = []
    for name in SETS:
        res = run_eval.evaluate_set(name, det)
        short = name.replace(".jsonl", "")
        for r in res["rows"]:
            beat = _utterance(short, r["id"], r.get("expect_turn", 1))
            if r["want"] and not r["got"]:
                out.append({"src": "eval:" + short, "kind": "漏判",
                            "text": beat or r["title"], "label": r["title"],
                            "expect": r["expect_type"], "got": "未介入"})
            elif r["want"] and r["got"] and r["got_type"] != r["expect_type"]:
                out.append({"src": "eval:" + short, "kind": "类型错",
                            "text": beat or r["title"], "label": r["title"],
                            "expect": r["expect_type"], "got": r["got_type"]})
            elif not r["want"] and r["got"]:
                out.append({"src": "eval:" + short, "kind": "误报",
                            "text": beat or r["title"], "label": r["title"],
                            "expect": "不介入", "got": r["got_type"]})
    return out


def from_online():
    """线上：规则没命中但人判成立（说明规则漏了），以及用户点误报。"""
    conn = db.connect()
    rows = conn.execute(
        "SELECT text, truth_type, rule_hit, rule_type, note FROM review_queue "
        "WHERE status='approved' AND (rule_hit=0 OR rule_hit IS NULL) LIMIT 60").fetchall()
    fb = conn.execute("SELECT note, useful FROM feedback WHERE useful=0 LIMIT 20").fetchall()
    conn.close()

    out = []
    for r in rows:
        out.append({"src": "online:human", "kind": "漏判（人工补）",
                    "text": r["text"], "expect": r["truth_type"] or "（人工认定成立）",
                    "got": "规则未命中"})
    for r in fb:
        if r["note"]:
            out.append({"src": "online:feedback", "kind": "用户误报", "text": r["note"],
                        "expect": "不介入", "got": "误判为风险"})
    return out


# ---------------------------------------------------------------- 向量与聚类
def build_vectors(items):
    """字 bigram + TF-IDF。"""
    docs = []
    for it in items:
        toks = recall.bigrams(textnorm.squash(it["text"]))
        docs.append(Counter(toks))
    N = len(docs)
    df = Counter()
    for c in docs:
        df.update(c.keys())
    idf = {t: math.log(1 + N / (1 + n)) for t, n in df.items()}
    vecs = []
    for c in docs:
        v = {t: tf * idf.get(t, 1.0) for t, tf in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs.append({t: x / norm for t, x in v.items()})
    return vecs


def cosine(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def cluster(items, th=0.42):
    """Union-Find 单链聚类。"""
    n = len(items)
    vecs = build_vectors(items)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    sims = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            s = cosine(vecs[i], vecs[j])
            sims[i][j] = sims[j][i] = s
            if s >= th:
                union(i, j)

    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    # 组装簇信息
    out = []
    for idxs in groups.values():
        # 代表样本：与簇内其他成员平均相似度最高
        best, best_s = idxs[0], -1
        for i in idxs:
            if len(idxs) == 1:
                best = i
                break
            avg = sum(sims[i][j] for j in idxs if j != i) / (len(idxs) - 1)
            if avg > best_s:
                best, best_s = i, avg

        # 共有特征词：出现于 ≥60% 成员的 bigram，按出现次数排序
        cnt = Counter()
        for i in idxs:
            cnt.update(recall.bigrams(textnorm.squash(items[i]["text"])))
        common = [t for t, c in cnt.most_common()
                  if c >= max(2, int(len(idxs) * 0.6))][:6]

        # 术语版本：更可读，用于给建议
        term_cnt = Counter()
        for i in idxs:
            raw = textnorm.canonical(items[i]["text"])
            for t in TERMS:
                if t in raw:
                    term_cnt[t] += 1
        common_terms = [t for t, c in term_cnt.most_common()
                        if c >= max(2, int(len(idxs) * 0.6))][:5]
        if len(idxs) == 1 and term_cnt:
            common_terms = [term_cnt.most_common(1)[0][0]]

        out.append({
            "size": len(idxs),
            "members": [items[i] for i in idxs],
            "rep": items[best],
            "common": common,
            "terms": common_terms,
            "kinds": Counter(items[i]["kind"] for i in idxs),
            "expects": Counter(items[i]["expect"] for i in idxs),
            "srcs": Counter(items[i]["src"] for i in idxs),
        })
    out.sort(key=lambda c: -c["size"])
    return out


# ---------------------------------------------------------------- 建议
def suggest(c):
    """按簇的构成给一条可执行的建议。"""
    kinds = c["kinds"]
    main_kind = kinds.most_common(1)[0][0]
    expect = [e for e in c["expects"] if e and e != "不介入" and e != "（人工认定成立）"]
    top_exp = expect[0] if expect else ""

    if main_kind.startswith("漏判") or main_kind == "漏判（人工补）":
        slot = ""
        if c.get("terms"):
            head = "、".join(c["terms"][:2])
            slot = f"（共有术语：`{head}`，可据此设计语义槽）"
        if c["size"] >= 3:
            return (f"**系统性漏判**：{top_exp or '该类'} 换了说法就漏，"
                    f"加「语义槽」比继续堆触发词更划算{slot}")
        return f"**单点漏判**：补 1~2 个触发词即可，注意别引入误报"
    if main_kind == "类型错":
        return f"**优先级冲突**：期望 {top_exp}，实际被判成别的类型 → 调 priority"
    if main_kind in ("误报", "用户误报"):
        return "**误报聚集**：检查负向抑制（R12）是否覆盖这类正常话术"
    return "待人工归类"


def main():
    th = 0.42
    if "--th" in sys.argv:
        th = float(sys.argv[sys.argv.index("--th") + 1])
    only = None
    if "--source" in sys.argv:
        only = sys.argv[sys.argv.index("--source") + 1]

    items = []
    if only != "online":
        items += from_eval()
    if only != "eval":
        items += from_online()

    # 去掉完全重复的文本
    seen, pool = set(), []
    for it in items:
        k = textnorm.squash(it["text"])
        if k in seen:
            continue
        seen.add(k)
        pool.append(it)

    print(f"失败样本 {len(pool)} 条（去重后），阈值 {th}")
    if not pool:
        print("没有失败样本")
        return

    clusters = cluster(pool, th)
    singles = [c for c in clusters if c["size"] == 1]
    print(f"聚成 {len(clusters)} 簇 · 单例 {len(singles)} 个 · "
          f"最大簇 {clusters[0]['size']} 条（占 {clusters[0]['size'] / len(pool):.0%}）")
    print(f"覆盖率：前 3 簇覆盖 {sum(c['size'] for c in clusters[:3]) / len(pool):.0%} 的失败")
    print()
    for i, c in enumerate(clusters[:6], 1):
        print(f"[簇 {i}] {c['size']} 条 · {dict(c['kinds'])}")
        print(f"   代表：{c['rep']['text']}  期望={c['rep']['expect']} 实际={c['rep']['got']}")
        if c.get("terms"):
            print(f"   共有术语：{' / '.join(c['terms'])}")
        print(f"   建议：{suggest(c)}")
        print()

    if singles:
        print("未归簇的单例（通常是真正的新情况，值得单独看）：")
        for c in singles[:6]:
            print(f"   · {c['rep']['text']}  期望={c['rep']['expect']}")
    write_report(clusters, pool, th)


def write_report(clusters, pool, th):
    L = ["# bad case 聚类", "",
         f"失败样本 {len(pool)} 条，阈值 {th}，聚成 {len(clusters)} 簇。", ""]
    L += ["## 总览", "", "| 簇 | 条数 | 失败类型 | 期望类型 | 共有术语 | 代表话术 |",
          "|---:|---:|---|---|---|---|"]
    for i, c in enumerate(clusters, 1):
        L.append(f"| {i} | {c['size']} | {('、'.join(f'{k}×{v}' for k, v in c['kinds'].items()))[:18]} | "
                 f"{('、'.join(c['expects']))[:20]} | {'、'.join(c.get('terms', []))[:22]} | "
                 f"{c['rep']['text'][:18]} |")

    L += ["", "## 逐簇详情", ""]
    for i, c in enumerate(clusters, 1):
        L.append(f"### 簇 {i}（{c['size']} 条）")
        L.append("")
        L.append(f"- 来源：{dict(c['srcs'])}")
        L.append(f"- 期望类型：{dict(c['expects'])}")
        if c.get("terms"):
            L.append(f"- 共有术语：`{'` `'.join(c['terms'])}`")
        if c["common"]:
            L.append(f"- 共有片段：`{'` `'.join(c['common'][:4])}`")
        L.append(f"- 建议：{suggest(c)}")
        L.append("")
        L.append("| 话术 | 期望 | 实际 |")
        L.append("|---|---|---|")
        for m in c["members"][:8]:
            L.append(f"| {m['text']} | {m['expect']} | {m['got']} |")
        if c["size"] > 8:
            L.append(f"| … 另有 {c['size'] - 8} 条 | | |")
        L.append("")

    (BASE / "badcase_clusters.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("报告 → badcase_clusters.md")


if __name__ == "__main__":
    main()
