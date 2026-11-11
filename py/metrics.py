# -*- coding: utf-8 -*-
"""判定质量的指标口径与置信区间。

验收约束：**误报率 ≤ 5% 时的召回率**。
当前规则输出是离散决策，没有可扫描的连续分数，所以这里只报告未调参集合的点估计，
不能声称已经完成 recall @ FPR ≤ 5% 的阈值搜索。采用该约束是因为这类任务误报和漏报的代价不对称：
误报打断正常交易，漏报只伤一个人。

辅指标：类型准确率、首响轮次。
不用 accuracy：正常对话占多数，全判"正常"也能拿到很高的准确率。
不用 F1：它默认误报和漏报同价。

本脚本还做一件事：**给每个数字配 95% 置信区间**。
样本少的时候，"0% 误报"这种事必须说清它有多不可靠。
"""
import math
from pathlib import Path

import detector

BASE = Path(__file__).resolve().parent


def wilson(k, n, z=1.96):
    """二项比例的 Wilson 置信区间，比正态近似稳，小样本也能用。"""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


def collect():
    det = detector.Detector()
    rows = []
    for name in ("scenarios.jsonl", "holdout.jsonl", "holdout2.jsonl", "holdout3.jsonl"):
        for s in detector.load_jsonl(name):
            exp = s.get("expect", {})
            res = det.analyze(s)
            first = res["first"]
            rows.append({
                "set": name.replace(".jsonl", ""),
                "id": s["id"],
                "want": bool(exp.get("intervene")),
                "expect_type": exp.get("type", ""),
                "got": first is not None,
                "type": first["type"] if first else "",
                "turn": first["turn"] if first else -1,
            })
    return rows


def summarize(rows, label):
    pos = [r for r in rows if r["want"]]
    neg = [r for r in rows if not r["want"]]
    tp = sum(1 for r in pos if r["got"])
    fp = sum(1 for r in neg if r["got"])
    tn = len(neg) - fp
    fn = len(pos) - tp

    recall = tp / len(pos) if pos else 0
    fpr = fp / len(neg) if neg else 0
    prec = tp / (tp + fp) if (tp + fp) else 0
    acc = (tp + tn) / len(rows) if rows else 0
    type_ok = sum(1 for r in pos if r["got"] and r["type"] == r["expect_type"])
    type_acc = type_ok / tp if tp else 0

    return {
        "label": label, "n": len(rows), "pos": len(pos), "neg": len(neg),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "recall": recall, "recall_ci": wilson(tp, len(pos)),
        "fpr": fpr, "fpr_ci": wilson(fp, len(neg)),
        "precision": prec, "accuracy": acc, "type_acc": type_acc,
    }


def rule_of_three(n):
    """0 次误报时，真实误报率的 95% 上界约 3/n。"""
    return min(1.0, 3.0 / n) if n else 1.0


def main():
    rows = collect()
    allset = summarize(rows, "全部标注集")
    dev = summarize([r for r in rows if r["set"] == "scenarios"], "开发集")
    contaminated = summarize(
        [r for r in rows if r["set"] in ("holdout", "holdout2")],
        "历史留出集（已污染）")
    hold = summarize([r for r in rows if r["set"] == "holdout3"],
                     "当前未调参留出集")

    lines = []
    add = lines.append
    add("# 判定质量指标")
    add("")
    add("## 一、主指标的定义")
    add("")
    add("> **业务验收约束：误报率（FPR）≤ 5%，同时报告介入召回率。**")
    add("")
    add("当前规则输出是离散决策，没有可用于阈值扫描的连续分数。因此下表是点估计，"
        "不是 recall @ FPR ≤ 5% 的完整阈值曲线。")
    add("")
    add("为什么不用别的：")
    add("")
    add("| 指标 | 问题 |")
    add("|---|---|")
    add("| accuracy | 正常对话占多数，全判「正常」也有很高的准确率 |")
    add("| F1 | 默认误报与漏报同价，但这里代价不对称 |")
    add("| AUC | 业务上要的是某一条阈值上的决策，不是排序能力 |")
    add("")
    add("## 二、当前实测")
    add("")
    add("| 数据 | 正 / 负样本 | 召回率 | 误报率 | 精确率 | 准确率 | 类型一致率 |")
    add("|---|---|---:|---:|---:|---:|---:|")
    for s in (dev, contaminated, hold, allset):
        add("| %s | %d / %d | %.0f%% | %.0f%% | %.0f%% | %.0f%% | %.0f%% |" % (
            s["label"], s["pos"], s["neg"], s["recall"] * 100, s["fpr"] * 100,
            s["precision"] * 100, s["accuracy"] * 100, s["type_acc"] * 100))
    add("")
    add("## 三、置信区间（这是最该被看见的一列）")
    add("")
    add("| 数据 | 召回率 95% CI | 误报率 95% CI |")
    add("|---|---|---|")
    for s in (dev, contaminated, hold, allset):
        add("| %s | %.0f%% – %.0f%% | %.0f%% – %.0f%% |" % (
            s["label"], s["recall_ci"][0] * 100, s["recall_ci"][1] * 100,
            s["fpr_ci"][0] * 100, s["fpr_ci"][1] * 100))
    add("")
    add("未调参留出集负样本只有 %d 条，误报率的分辨率是 1/%d ≈ %.0f%%——" % (
        hold["neg"], hold["neg"], 100 / hold["neg"] if hold["neg"] else 0))
    add("所以「误报率 0%」并不等于「误报率低于 5%」。")
    add("按 rule of three，%d 条负样本零误报时，真实误报率的 95%% 上界约 **%.0f%%**。" % (
        hold["neg"], rule_of_three(hold["neg"]) * 100))
    add("")
    add("**结论：要主张「误报率 ≤ 5%」，负样本至少要 60 条以上。**")
    add("")
    add("## 四、指标报告规范")
    add("")
    add("| 场合 | 报什么 | 不报什么 |")
    add("|---|---|---|")
    add("| 项目说明 | 未调参留出集召回 %.0f%%（n=%d） | 不报开发集或被调参污染的 100%% |"
        % (hold["recall"] * 100, hold["pos"]))
    add("| 指标复核 | 附置信区间，说明负样本不足 | 不说「误报率 0%」当结论 |")
    add("| PRD / 验收 | 召回 ≥ 70%，FPR ≤ 5%（负样本 ≥ 60 条） | 不用 accuracy 当验收线 |")

    report = "\n".join(lines)
    (BASE / "metrics_report.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
