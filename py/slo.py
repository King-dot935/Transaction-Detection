# -*- coding: utf-8 -*-
"""SLO：把"服务好不好"变成可判定的数字。

四个指标，每个都有目标和当前值：
  · 判定延迟 P95       目标 ≤ 800ms
  · 待复核率           目标 ≤ 15%
  · 满意度（反馈正面率） 目标 ≥ 80%
  · 数据新鲜度         目标 ≤ 5 分钟内有新记录
"""
from datetime import datetime
from pathlib import Path

import backup
import db

TARGETS = {
    "latency_p95": 800,        # ms
    "pending_rate": 0.15,
    "satisfaction": 0.80,
    "freshness_min": 5,
    "satisfaction_min_n": 30,
}


def _pct(values, p):
    if not values:
        return None
    values = sorted(values)
    k = max(0, min(len(values) - 1, int(round(p / 100 * (len(values) - 1)))))
    return values[k]


def compute(conn=None):
    own = conn is None
    conn = conn or db.connect()

    lat = [r["lat_ms"] for r in conn.execute(
        "SELECT lat_ms FROM judge_logs WHERE lat_ms IS NOT NULL")]
    total = conn.execute("SELECT COUNT(*) n FROM judge_logs").fetchone()["n"] or 0
    pending = conn.execute(
        "SELECT COUNT(*) n FROM review_queue WHERE status='pending'").fetchone()["n"] or 0
    fb_good = conn.execute("SELECT COUNT(*) n FROM feedback WHERE useful=1").fetchone()["n"] or 0
    fb_all = conn.execute("SELECT COUNT(*) n FROM feedback").fetchone()["n"] or 0
    last_ts = conn.execute("SELECT MAX(ts) FROM judge_logs").fetchone()[0]
    if own:
        conn.close()

    freshness = None
    if last_ts:
        try:
            freshness = (datetime.now() - datetime.strptime(last_ts, "%Y-%m-%d %H:%M:%S")
                         ).total_seconds() / 60
        except Exception:
            freshness = None

    sat = (fb_good / fb_all) if fb_all else None
    pr = (pending / total) if total else 0.0
    p95 = _pct(lat, 95)

    rows = [
        {"key": "latency_p95", "name": "判定延迟 P95", "value": p95,
         "display": f"{p95} ms" if p95 is not None else "无数据",
         "target": f"≤ {TARGETS['latency_p95']} ms",
         "ok": (p95 is not None and p95 <= TARGETS["latency_p95"])},
        {"key": "pending_rate", "name": "待复核率", "value": round(pr, 3),
         "display": f"{pr:.0%}", "target": f"≤ {TARGETS['pending_rate']:.0%}",
         "ok": pr <= TARGETS["pending_rate"]},
        {"key": "satisfaction", "name": "满意度（反馈正面率）", "value": sat,
         "display": (f"{sat:.0%}（n={fb_all}）"
                     if fb_all >= TARGETS["satisfaction_min_n"]
                     else f"样本不足（{fb_all}/{TARGETS['satisfaction_min_n']}）"),
         "target": f"≥ {TARGETS['satisfaction']:.0%}（样本 ≥ {TARGETS['satisfaction_min_n']}）",
         "ok": (fb_all >= TARGETS["satisfaction_min_n"]
                and sat is not None and sat >= TARGETS["satisfaction"])},
        {"key": "freshness", "name": "数据新鲜度",
         "value": freshness,
         "display": f"{freshness:.1f} 分钟前" if freshness is not None else "无数据",
         "target": f"≤ {TARGETS['freshness_min']} 分钟",
         "ok": (freshness is not None and freshness <= TARGETS["freshness_min"])},
    ]

    # 数据连续性：一致性 + 备份年龄
    h = backup.health()
    rows.append({
        "key": "integrity", "name": "数据库完整性",
        "value": h["integrity"], "display": "正常" if h["integrity"] else "异常",
        "target": "integrity_check = ok", "ok": h["integrity"]})
    rows.append({
        "key": "backup_age", "name": "最近备份",
        "value": h["backup_age_hours"],
        "display": (f"{h['backup_age_hours']} 小时前" if h["backup_age_hours"] is not None
                    else "从未备份"),
        "target": "≤ 24 小时", "ok": (h["backup_age_hours"] is not None
                                     and h["backup_age_hours"] <= 24)})

    passed = sum(1 for r in rows if r["ok"])
    return {"rows": rows, "passed": passed, "total": len(rows),
            "all_ok": passed == len(rows), "counts": h["counts"],
            "last_judge": h["last_judge"]}
