# -*- coding: utf-8 -*-
"""失败归因：一条漏判到底错在哪一层。

五层归因（按顺序判断，命中即停）：
  L1 词表：这句话里有没有任何一个触发词
  L2 组合：需要"两个词同时出现"的规则（当前只支持 权利+拒绝）
  L3 阈值：命中了但 repeat_threshold > 1，单句不成立
  L4 负向：命中了但被正常承诺词抑制
  L5 口径：单句判不出，但会话累计能判

用法：
    python diagnose.py                 # 诊断所有留出集失败
    python diagnose.py holdout3.jsonl
"""
import json
import sys
from pathlib import Path

import chunking
import detector

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"


def load(name):
    p = DATA / name
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def merchant_turns(sc):
    return [t["text"] for t in sc["turns"] if t["role"] == "merchant"]


def diagnose(sc, det):
    """对一段应介入的对话做分层归因。"""
    exp = sc.get("expect") or {}
    if not exp.get("intervene"):
        return None
    want_type = exp.get("type", "")
    turns = merchant_turns(sc)

    # 已经在评测里失败才会进来；这里看每一层
    any_hit = False
    hit_but_threshold = []
    for t in turns:
        hits = det.match(t)
        if hits:
            any_hit = True
        for r, kw in hits:
            if r.get("repeat_threshold", 1) > 1:
                hit_but_threshold.append((t, r, kw))

    # L1：整段里有没有该类型的触发词
    target_rules = [r for r in det.positive if r["type"] == want_type]
    has_word = any(p in t for t in turns for r in target_rules for p in r.get("patterns", []))

    # L3：阈值导致单句不成立
    threshold_blocked = bool(hit_but_threshold) and not any(
        r.get("repeat_threshold", 1) <= 1 for t in turns for r, _ in det.match(t))

    # L5：会话累计能不能救
    counts = {}
    for t in turns:
        for r, _ in det.match(t):
            counts[r["type"]] = counts.get(r["type"], 0) + 1
    need = {}
    for r in det.positive:
        need[r["type"]] = r.get("repeat_threshold", 1)
    session_can = any(counts.get(tp, 0) >= need.get(tp, 1) and tp == want_type
                      for tp in counts)

    if threshold_blocked:
        layer = "L3 阈值过严"
        why = (f"命中了「{hit_but_threshold[0][2]}」，但该规则要求出现 "
               f"{hit_but_threshold[0][1].get('repeat_threshold')} 次，"
               f"这段会话里只出现 {counts.get(want_type, 0)} 次")
    elif has_word:
        layer = "L3 阈值"
        why = (f"命中了但 repeat_threshold={hit_but_threshold[0][1].get('repeat_threshold')}，"
               f"单句不成立；会话里只出现 {counts.get(want_type, 0)} 次")
    elif has_word:
        layer = "L2 词在表里但没命中"
        why = f"触发词存在于句中，但匹配/切块环节没命中（期望类型 {want_type}）"
    elif any_hit:
        layer = "L4 负向抑制"
        why = "命中了但被正常承诺词抑制"
    elif session_can:
        layer = "L5 评测口径"
        why = "单句判不出，但会话累计能判——评测按单句跑，所以记成漏判"
    else:
        layer = "L1 词表"
        why = f"触发词表完全不覆盖这种说法（期望类型 {want_type}）"

    return {"id": sc["id"], "title": sc["title"], "want_type": want_type,
            "layer": layer, "why": why, "turns": turns,
            "counts": counts, "thresholds": {k: v for k, v in need.items() if v > 1}}


def main():
    import run_eval
    sets = [sys.argv[1]] if len(sys.argv) > 1 else ["holdout3.jsonl"]
    det = detector.Detector()
    rows = []
    for name in sets:
        res = run_eval.evaluate_set(name, det)
        failed = {r["id"] for r in res["rows"]
                  if r["want"] and (not r["got"] or r["got_type"] != r["expect_type"])}
        for sc in load(name):
            if sc["id"] not in failed:
                continue                      # 只诊断真的失败的
            d = diagnose(sc, det)
            if d:
                rows.append(d)

    if not rows:
        print("没有失败的应介入样本")
        return

    print(f"诊断 {len(rows)} 段应介入样本\n")
    for d in rows:
        print(f"[{d['id']}] {d['title']}  期望 {d['want_type']}")
        print(f"  归因层：{d['layer']}")
        print(f"  原因：{d['why']}")
        print(f"  商家话术：{d['turns']}")
        print()

    from collections import Counter
    c = Counter(d["layer"] for d in rows)
    print("归因分布：", dict(c))


if __name__ == "__main__":
    main()
