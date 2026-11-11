# -*- coding: utf-8 -*-
"""商客对话监测 —— 检测器

输入：一段消费者与商家的对话（scenarios.jsonl）
输出：逐轮判定 + 首次介入点

判定分两层：
  1) 单句规则：命中「不公平话术」（拒绝法定权利、诱导线下、删差评、威胁、索要隐私…）
  2) 对话级规则：同一类问题反复出现（如连续三次拖延）才判定
负向规则优先级最高：同一句里出现「可以退 / 已经安排」这类正常承诺，不判为不公平。
"""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"

SEV = {"high": 3, "mid": 2, "low": 1, "none": 0}


def load_jsonl(name):
    with open(DATA / name, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


class Detector:
    def __init__(self):
        self.rules = load_jsonl("rules.jsonl")
        self.clauses = {c["id"]: c for c in load_jsonl("clauses.jsonl")}
        self.negative = [r for r in self.rules if r.get("negative")]
        self.positive = [r for r in self.rules if not r.get("negative")]

    # ---------------------------------------------------------- 分句级匹配
    def __init_negative(self):
        if not hasattr(self, "_neg_patterns"):
            self._neg_patterns = [p for r in self.negative for p in r.get("patterns", [])]

    def match(self, text):
        """返回 [(rule, matched_keyword)]，已按「严重度 → 优先级」排序。

        切块策略见 chunking.py：负向抑制与模式匹配都按分句，
        权利+拒绝组合规则看整条消息（跨句语义）。
        """
        self.__init_negative()
        import chunking

        hits, seen = [], set()

        # ① 权利 + 拒绝：整条消息范围（「我要退」和「不退」常在不同分句）
        for r in self.positive:
            if "rights" not in r:
                continue
            has_right = any(w in text for w in r["rights"])
            denied = next((d for d in r["denials"] if d in text), None)
            if has_right and denied:
                hits.append((r, denied))
                seen.add(r["id"])

        # ② 模式规则：逐分句；同一分句里出现正常承诺则该分句放行
        for clause in chunking.split_clauses(text):
            if any(p in clause for p in self._neg_patterns):
                continue
            for r in self.positive:
                if "rights" in r or r["id"] in seen:
                    continue
                for p in r.get("patterns", []):
                    if p in clause:
                        hits.append((r, p))
                        seen.add(r["id"])
                        break

        hits.sort(key=lambda x: (-SEV[x[0]["severity"]], x[0]["priority"]))
        return hits

    # ---------------------------------------------------------- 整段对话
    def analyze(self, scenario):
        counts = {}
        verdicts = []
        first = None
        for i, t in enumerate(scenario["turns"]):
            v = {"turn": i, "role": t["role"], "text": t["text"], "intervene": False}
            if t["role"] == "merchant":
                hits = self.match(t["text"])
                for r, _ in hits:
                    counts[r["id"]] = counts.get(r["id"], 0) + 1
                ready = []
                for r, kw in hits:
                    need = r.get("repeat_threshold", 1)
                    if counts[r["id"]] >= need:
                        ready.append((r, kw))
                if ready:
                    r, kw = ready[0]
                    clause = self.clauses.get(r.get("clause", ""), {})
                    v.update({
                        "intervene": True,
                        "rule_id": r["id"],
                        "type": r["type"],
                        "severity": r["severity"],
                        "matched": kw,
                        "clause": r.get("clause", ""),
                        "clause_title": clause.get("title", ""),
                        "clause_text": clause.get("text", ""),
                        "hint": r["hint"],
                        "action": r["action"],
                    })
                    if first is None:
                        first = dict(v)
            verdicts.append(v)
        return {
            "scenario_id": scenario["id"],
            "title": scenario["title"],
            "product": scenario.get("product", ""),
            "verdicts": verdicts,
            "first": first,
        }


if __name__ == "__main__":
    d = Detector()
    for s in load_jsonl("scenarios.jsonl"):
        r = d.analyze(s)
        f = r["first"]
        print("%s %-16s -> %s" % (s["id"], s["title"],
                                  ("第 %d 轮 %s" % (f["turn"], f["type"])) if f else "不介入"))
