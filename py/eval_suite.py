# -*- coding: utf-8 -*-
"""评测体系：离线切片 + 在线信号 + 缺陷清单 + 回归对比。

比 run_eval.py 多四件事：
  1. 按维度切片（类型 / 句长 / 是否引用条款），找出"哪一类判不好"
  2. 把每次评测落 eval_runs，与上一次比，标出回归与新修复
  3. 生成 badcases.md：每条失败的期望值、实际值、首轮位置、原因初判
  4. 汇总在线信号（缓存命中、待复核率、人工改判率、用户误报）

用法：
    python eval_suite.py              # 跑评测并生成报告
    python eval_suite.py --no-save    # 不写 eval_runs（临时试跑）
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import db
import detector
import run_eval

BASE = Path(__file__).resolve().parent
SETS = ["scenarios.jsonl", "holdout.jsonl", "holdout2.jsonl", "holdout3.jsonl"]

# 句长分桶
BUCKETS = [(0, 10, "短句 ≤10"), (10, 20, "中句 11-20"), (20, 10 ** 6, "长句 >20")]
# 判断"是不是在引用条款/条件"——这类句子规则最容易漏
CLAUSE_HINTS = ["规定", "条款", "原则上", "按照", "根据", "除非", "除了", "之外", "情况"]


def bucket_of(text):
    n = len(text)
    for lo, hi, name in BUCKETS:
        if lo < n <= hi:
            return name
    return BUCKETS[-1][2]


def run_sets(det):
    out = {}
    for name in SETS:
        out[name.replace(".jsonl", "")] = run_eval.evaluate_set(name, det)
    return out


def slice_metrics(results):
    """按类型 / 句长 / 是否引用条款切片，看召回与误报。"""
    slices = defaultdict(lambda: {"n": 0, "tp": 0, "fn": 0, "fp": 0, "tn": 0})

    for setname, res in results.items():
        for r in res["rows"]:
            key_type = "类型:" + (r["expect_type"] or "—")
            key_bucket = "句长:" + bucket_of(r.get("text") or "")
            key_clause = "含条款词:" + (
                "是" if any(h in (r.get("text") or "") for h in CLAUSE_HINTS) else "否")
            for k in (key_type, key_bucket, key_clause):
                s = slices[k]
                s["n"] += 1
                if r["want"] and r["got"]:
                    s["tp"] += 1
                elif r["want"] and not r["got"]:
                    s["fn"] += 1
                elif not r["want"] and r["got"]:
                    s["fp"] += 1
                else:
                    s["tn"] += 1
    for k, s in slices.items():
        pos = s["tp"] + s["fn"]
        neg = s["fp"] + s["tn"]
        s["recall"] = round(s["tp"] / pos, 3) if pos else None
        s["fpr"] = round(s["fp"] / neg, 3) if neg else None
    return dict(slices)


def badcases(results):
    """失败样本：漏判、误报、类型错。"""
    out = []
    for setname, res in results.items():
        for r in res["rows"]:
            if r["want"] and not r["got"]:
                out.append({"set": setname, "kind": "漏判", "id": r["id"], "title": r["title"],
                            "expect": f'{r["expect_type"]} @第{r["expect_turn"]}轮',
                            "got": "未介入",
                            "hint": "规则没覆盖这种说法"})
            elif not r["want"] and r["got"]:
                out.append({"set": setname, "kind": "误报", "id": r["id"], "title": r["title"],
                            "expect": "不介入", "got": f'{r["got_type"]}（{r["got_rule"]}）',
                            "hint": "规则太激进或负向抑制不足"})
            elif r["want"] and r["got"] and r["got_type"] != r["expect_type"]:
                out.append({"set": setname, "kind": "类型错", "id": r["id"], "title": r["title"],
                            "expect": r["expect_type"], "got": r["got_type"],
                            "hint": "多条规则命中，优先级选错"})
    return out


def online_metrics():
    """在线信号：缓存、复核、改判、用户反馈。"""
    conn = db.connect()
    total = conn.execute("SELECT COUNT(*) n FROM judge_logs").fetchone()["n"] or 0
    cache = conn.execute(
        "SELECT COUNT(*) n FROM judge_logs WHERE route_reason LIKE '缓存命中%'"
    ).fetchone()["n"] or 0
    pending = conn.execute("SELECT COUNT(*) n FROM review_queue").fetchone()["n"] or 0
    decided = conn.execute(
        "SELECT COUNT(*) n FROM review_queue WHERE status<>'pending'").fetchone()["n"] or 0
    flip = conn.execute(
        "SELECT COUNT(*) n FROM review_queue WHERE status<>'pending'"
        " AND ((rule_hit=1) <> (status='approved'))").fetchone()["n"] or 0
    fb = conn.execute("SELECT COUNT(*) n FROM feedback").fetchone()["n"] or 0
    fb_bad = conn.execute("SELECT COUNT(*) n FROM feedback WHERE useful=0").fetchone()["n"] or 0
    tiers = {r["tier"]: r["n"] for r in conn.execute(
        "SELECT COALESCE(tier,'?') tier, COUNT(*) n FROM judge_logs GROUP BY tier")}
    conn.close()

    return {
        "judge_total": total,
        "cache_hit": cache,
        "cache_rate": round(cache / total, 3) if total else 0,
        "pending": pending,
        "pending_rate": round(pending / total, 3) if total else 0,
        "decided": decided,
        "human_flip": flip,
        "flip_rate": round(flip / decided, 3) if decided else 0,
        "feedback": fb,
        "feedback_bad": fb_bad,
        "tiers": tiers,
    }


def save_run(results):
    conn = db.connect()
    dev = results["scenarios"]["detect_rate"]
    h1 = results["holdout"]["detect_rate"]
    h2 = results["holdout2"]["detect_rate"]
    h3 = results["holdout3"]["detect_rate"]
    fp = results["holdout3"]["false_positive_rate"]
    ta = results["holdout3"]["type_accuracy"]
    gate_result = run_eval.gate({
        "dev": results["scenarios"], "holdout": results["holdout"],
        "holdout2": results["holdout2"], "holdout3": results["holdout3"],
        "audit": run_eval.audit_splits(),
    })
    passed = 1 if gate_result["passed"] else 0
    prev = conn.execute(
        "SELECT * FROM eval_runs ORDER BY id DESC LIMIT 1").fetchone()
    with conn:
        conn.execute(
            "INSERT INTO eval_runs(ts,dev_recall,holdout_recall,holdout2_recall,"
            "holdout3_recall,false_positive,type_accuracy,passed)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (db.now(), dev, h1, h2, h3, fp, ta, passed))
    out = dict(prev) if prev else None
    conn.close()
    return out, {"dev": dev, "holdout": h1, "holdout2": h2, "holdout3": h3,
                 "fp": fp, "type_acc": ta, "passed": bool(passed)}


def main(save=True):
    det = detector.Detector()
    results = run_sets(det)
    audit = run_eval.audit_splits()
    slices = slice_metrics(results)
    cases = badcases(results)
    online = online_metrics()

    print("=" * 58)
    print("离线评测")
    print("=" * 58)
    for name, r in results.items():
        print(f"  {name:<10} 召回 {r['detect_rate']:.0%}  误报 {r['false_positive_rate']:.0%}"
              f"  类型 {r['type_accuracy']:.0%}  首轮 {r['avg_first_turn']}")

    # 最差的切片
    worst = sorted(
        [(k, v) for k, v in slices.items() if v["recall"] is not None and v["tp"] + v["fn"] >= 2],
        key=lambda kv: kv[1]["recall"])[:3]
    print("\n最弱切片：")
    for k, v in worst:
        print(f"  {k:<26} 召回 {v['recall']:.0%}（样本 {v['tp'] + v['fn']}）")

    print(f"\n缺陷样本：{len(cases)} 条")
    for c in cases[:6]:
        print(f"  [{c['kind']}] {c['id']} {c['title']}  期望={c['expect']} 实际={c['got']}")

    print("\n在线信号：")
    print(f"  判定 {online['judge_total']} 条 · 缓存命中率 {online['cache_rate']:.0%}"
          f" · 待复核率 {online['pending_rate']:.0%} · 人工与规则不一致 {online['flip_rate']:.0%}")
    print(f"  用户反馈 {online['feedback']} 条，其中「误报」{online['feedback_bad']} 条")

    prev = None
    if save:
        prev, cur = save_run(results)
        print("\n回归对比：")
        if not prev:
            print("  首次记录，没有历史可比")
        else:
            for key, label in (("dev_recall", "开发集"), ("holdout_recall", "留出集一"),
                               ("holdout2_recall", "留出集二"),
                               ("holdout3_recall", "留出集三")):
                a, b = prev[key] or 0, cur[{"dev_recall": "dev", "holdout_recall": "holdout",
                                            "holdout2_recall": "holdout2",
                                            "holdout3_recall": "holdout3"}[key]]
                arrow = "→" if b == a else ("↑" if b > a else "↓")
                print(f"  {label:<6} {a:.0%} {arrow} {b:.0%}")

    write_report(results, slices, cases, online, worst, audit)
    return 0


def write_report(results, slices, cases, online, worst, audit):
    L = ["# 评测报告", "", "## 一、离线指标", "",
         "| 集合 | 段数 | 召回 | 误报 | 类型准确率 | 平均首轮 |",
         "|---|---:|---:|---:|---:|---:|"]
    NOTE = {'scenarios': '开发集，已调参', 'holdout': '历史污染，不作泛化',
            'holdout2': '历史污染，不作泛化', 'holdout3': '未调参（当前唯一可信）'}
    for name, r in results.items():
        L.append(f"| {name}（{NOTE.get(name, '—')}） | {r['total']} | {r['detect_rate']:.0%} | "
                 f"{r['false_positive_rate']:.0%} | {r['type_accuracy']:.0%} | "
                 f"{r['avg_first_turn']} |")

    L += ["", "## 一（续）、数据切分审计", ""]
    L += ["| 集合 | 段数 | 正样本 | 负样本 |", "|---|---:|---:|---:|"]
    for name, row in audit["manifest"].items():
        L.append(f"| {name} | {row['total']} | {row['positive']} | {row['negative']} |")
    L.append("")
    if audit["issues"]:
        L.append("发现问题：")
        L.extend("- " + issue for issue in audit["issues"])
    else:
        L.append("未发现 ID 或商家原话跨集合重复。该检查不能证明历史人工调参污染不存在。")

    L += ["", "## 二、最弱切片", "", "| 切片 | 样本 | 召回 | 误报 |", "|---|---:|---:|---:|"]
    for k, v in worst:
        rec = f"{v['recall']:.0%}" if v["recall"] is not None else "—"
        fpr = f"{v['fpr']:.0%}" if v["fpr"] is not None else "—"
        L.append(f"| {k} | {v['tp'] + v['fn']} | {rec} | {fpr} |")

    L += ["", "## 三、缺陷清单", ""]
    if not cases:
        L.append("本轮没有失败样本。")
    else:
        L += ["| 类型 | 集合 | ID | 话术 | 期望 | 实际 | 初判原因 |",
              "|---|---|---|---|---|---|---|"]
        for c in cases:
            L.append(f"| {c['kind']} | {c['set']} | {c['id']} | {c['title']} | "
                     f"{c['expect']} | {c['got']} | {c['hint']} |")

    L += ["", "## 四、在线信号", "",
          f"- 判定总量：{online['judge_total']}",
          f"- 缓存命中率：{online['cache_rate']:.0%}（{online['cache_hit']} 次）",
          f"- 待复核率：{online['pending_rate']:.0%}（{online['pending']} 条）",
          f"- 人工改判率：{online['flip_rate']:.0%}（{online['human_flip']}/{online['decided']}）",
          f"- 用户反馈：{online['feedback']} 条，误报 {online['feedback_bad']} 条",
          f"- 分流分布：{json.dumps(online['tiers'], ensure_ascii=False)}"]

    gate_result = run_eval.gate({
        "dev": results["scenarios"], "holdout": results["holdout"],
        "holdout2": results["holdout2"], "holdout3": results["holdout3"],
        "audit": audit,
    })
    L += ["", "## 五、回归门禁", ""]
    gate_md = run_eval.render_gate(gate_result)
    gate_md = gate_md.replace("## 回归门禁", "", 1).strip()
    L += [gate_md]

    (BASE / "评测报告.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n报告 → 评测报告.md")


if __name__ == "__main__":
    sys.exit(main(save="--no-save" not in sys.argv))
