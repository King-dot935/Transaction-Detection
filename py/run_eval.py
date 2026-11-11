# -*- coding: utf-8 -*-
"""跑标注集，输出介入召回 / 误报 / 类型准确率 / 首轮发现位置。

四组数据：
  scenarios  12 段，开发集
  holdout     6 段，历史上已被调参污染，不再作为泛化证据
  holdout2    6 段，历史上已被调参污染，不再作为泛化证据
  holdout3    6 段，当前唯一未调参集合，也是门禁的真实泛化依据

用法：python run_eval.py
"""
import json
from pathlib import Path

import detector
import textnorm

BASE = Path(__file__).resolve().parent


def evaluate_set(name, det):
    scenarios = detector.load_jsonl(name)
    rows = []
    tp = fp = fn = tn = 0
    type_ok = turn_ok = 0
    first_turns = []

    for s in scenarios:
        exp = s.get("expect", {})
        want = bool(exp.get("intervene"))
        res = det.analyze(s)
        first = res["first"]
        got = first is not None
        if want and got:
            tp += 1
            if first["type"] == exp.get("type"):
                type_ok += 1
            if first["turn"] == exp.get("turn"):
                turn_ok += 1
            first_turns.append(first["turn"])
        elif want and not got:
            fn += 1
        elif not want and got:
            fp += 1
        else:
            tn += 1
        rows.append({
            "id": s["id"], "title": s["title"], "want": want,
            "text": max((t.get("text", "") for t in s.get("turns", [])
                         if t.get("role") == "merchant"), key=len, default=""),
            "expect_turn": exp.get("turn", -1), "expect_type": exp.get("type", ""),
            "got": got,
            "got_turn": first["turn"] if first else -1,
            "got_type": first["type"] if first else "",
            "got_rule": first["rule_id"] if first else "",
        })

    total = len(scenarios)
    positives = sum(1 for r in rows if r["want"])
    negatives = total - positives
    return {
        "set": name.replace(".jsonl", ""),
        "total": total, "positives": positives, "negatives": negatives,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "detect_rate": round(tp / positives, 3) if positives else 0,
        "false_positive_rate": round(fp / negatives, 3) if negatives else 0,
        "type_accuracy": round(type_ok / tp, 3) if tp else 0,
        "turn_accuracy": round(turn_ok / tp, 3) if tp else 0,
        "avg_first_turn": round(sum(first_turns) / len(first_turns), 2) if first_turns else 0,
        "precision": round(tp / (tp + fp), 3) if tp + fp else 0,
        "accuracy": round((tp + tn) / total, 3) if total else 0,
        "rows": rows,
    }


def audit_splits():
    """检查数据集是否重复、标签是否完整，并返回可写进报告的清单。"""
    names = ("scenarios.jsonl", "holdout.jsonl", "holdout2.jsonl", "holdout3.jsonl")
    ids = {}
    messages = {}
    manifest = {}
    issues = []
    for name in names:
        items = detector.load_jsonl(name)
        pos = sum(1 for s in items if (s.get("expect") or {}).get("intervene"))
        neg = len(items) - pos
        manifest[name] = {"total": len(items), "positive": pos, "negative": neg}
        for s in items:
            sid = s.get("id")
            if sid in ids:
                issues.append("ID 跨集合重复：%s（%s / %s）" % (sid, ids[sid], name))
            else:
                ids[sid] = name
            for turn in s.get("turns", []):
                if turn.get("role") != "merchant":
                    continue
                key = textnorm.squash(turn.get("text", ""))
                if not key:
                    continue
                prev = messages.get(key)
                if prev and prev != name:
                    issues.append("商家原话跨集合重复：%s / %s" % (prev, name))
                messages.setdefault(key, name)
    return {"manifest": manifest, "issues": sorted(set(issues))}


def evaluate():
    det = detector.Detector()
    out = {
        "dev": evaluate_set("scenarios.jsonl", det),
        "holdout": evaluate_set("holdout.jsonl", det),
        "holdout2": evaluate_set("holdout2.jsonl", det),
        "holdout3": evaluate_set("holdout3.jsonl", det),
    }
    out["audit"] = audit_splits()
    return out


def render_markdown(m):
    d, h, h2, h3 = m["dev"], m["holdout"], m["holdout2"], m["holdout3"]
    out = [
        "# 商客对话监测 · 评测报告",
        "",
        "| 指标 | 开发集（已调参） | 留出集一（已污染） | 留出集二（已污染） | 留出集三（未调参） |",
        "|---|---:|---:|---:|---:|",
        "| 介入召回率 | %.0f%% | %.0f%% | %.0f%% | **%.0f%%** |" % (
            d["detect_rate"] * 100, h["detect_rate"] * 100, h2["detect_rate"] * 100,
            h3["detect_rate"] * 100),
        "| 误报率 | %.0f%% | %.0f%% | %.0f%% | %.0f%% |" % (
            d["false_positive_rate"] * 100, h["false_positive_rate"] * 100,
            h2["false_positive_rate"] * 100, h3["false_positive_rate"] * 100),
        "| 类型准确率 | %.0f%% | %.0f%% | %.0f%% | %.0f%% |" % (
            d["type_accuracy"] * 100, h["type_accuracy"] * 100,
            h2["type_accuracy"] * 100, h3["type_accuracy"] * 100),
        "| 首轮位置准确率 | %.0f%% | %.0f%% | %.0f%% | %.0f%% |" % (
            d["turn_accuracy"] * 100, h["turn_accuracy"] * 100,
            h2["turn_accuracy"] * 100, h3["turn_accuracy"] * 100),
        "| 平均发现轮次 | %.2f | %.2f | %.2f | %.2f |" % (
            d["avg_first_turn"], h["avg_first_turn"], h2["avg_first_turn"],
            h3["avg_first_turn"]),
        "",
        "> 开发集和留出集一/二都已参与调参，不能作为泛化证据。",
        "> 当前对外只报 **holdout3 的召回 %.0f%%**；误报率受负例数量限制，不能据此宣称低于 5%%。"
        % (h3["detect_rate"] * 100),
        "",
        "## 留出集三逐条",
        "",
        "| 场景 | 应介入 | 标注轮次 | 实际轮次 | 标注类型 | 实际类型 |",
        "|---|---|---|---|---|---|",
    ]
    for r in h3["rows"]:
        out.append("| %s %s | %s | %s | %s | %s | %s |" % (
            r["id"], r["title"], "是" if r["want"] else "否",
            r["expect_turn"] if r["expect_turn"] >= 0 else "-",
            r["got_turn"] if r["got_turn"] >= 0 else "-",
            r["expect_type"] or "-", r["got_type"] or "-"))
    return "\n".join(out)


# 回归门禁：召回不能掉、误报不能涨，且要有足够负例。不达标退出码 1。
GATE = {
    "holdout3_recall": 0.70,
    "false_positive": 0.05,
    "min_negatives": 60,
}


def gate(m):
    h, h2 = m["holdout"], m["holdout2"]
    h3 = m.get("holdout3") or {"detect_rate": 0.0, "false_positive_rate": 0.0}
    audit = m.get("audit") or {}
    checks = [
        ("留出集三召回(未调参)", h3["detect_rate"], ">=", GATE["holdout3_recall"]),
        ("未调参集合误报率", h3["false_positive_rate"], "<=", GATE["false_positive"]),
        ("未调参集合负样本数", h3["negatives"], ">=", GATE["min_negatives"]),
        ("数据集跨集合泄漏", len(audit.get("issues") or []), "<=", 0),
    ]
    rows, passed = [], True
    for name, val, op, limit in checks:
        ok = val >= limit if op == ">=" else val <= limit
        passed = passed and ok
        rows.append({"name": name, "value": val, "op": op, "limit": limit, "ok": ok})
    return {"passed": passed, "rows": rows}


def render_gate(g):
    lines = ["", "## 回归门禁", "", "| 检查 | 当前 | 阈值 | 结果 |", "|---|---:|---:|---|"]
    for r in g["rows"]:
        is_count = r["name"] in ("未调参集合负样本数", "数据集跨集合泄漏")
        value = "%.0f" % r["value"] if is_count else "%.0f%%" % (r["value"] * 100)
        limit = "%.0f" % r["limit"] if is_count else "%.0f%%" % (r["limit"] * 100)
        lines.append("| %s | %s | %s %s | %s |" % (
            r["name"], value, r["op"], limit, "通过" if r["ok"] else "未通过"))
    lines.append("")
    lines.append("**门禁结论：%s**" % ("通过，可以合入" if g["passed"] else "未通过，先修规则再合入"))
    return chr(10).join(lines)


if __name__ == "__main__":
    import sys
    m = evaluate()
    for k in ("dev", "holdout", "holdout2", "holdout3"):
        print(json.dumps({kk: vv for kk, vv in m[k].items() if kk != "rows"}, ensure_ascii=False))
    audit = m["audit"]
    print("DATA %s" % json.dumps(audit, ensure_ascii=False))
    g = gate(m)
    print("GATE %s" % ("PASS" if g["passed"] else "FAIL"))
    (BASE / "eval_report.md").write_text(
        render_markdown(m) + chr(10) + render_gate(g), encoding="utf-8")
    print("已生成 eval_report.md")
    sys.exit(0 if g["passed"] else 1)
