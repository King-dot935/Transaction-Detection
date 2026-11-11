# -*- coding: utf-8 -*-
"""把 SQLite 里的数据导成 CSV，方便导入 MySQL / PostgreSQL 或丢进 Excel。

用法：
    python export_data.py            # 导出到 exports/
    python export_data.py --table judge_logs
"""
import csv
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = BASE / "sentinel.db"
OUT = BASE / "exports"


def export(db_path=DB, only=None):
    if not Path(db_path).exists():
        raise SystemExit("找不到数据库：%s，先跑一次 demo.py" % db_path)
    OUT.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    if only:
        tables = [t for t in tables if t == only]

    for t in tables:
        rows = conn.execute("SELECT * FROM %s" % t).fetchall()
        p = OUT / ("%s.csv" % t)
        with p.open("w", encoding="utf-8-sig", newline="") as f:
            if not rows:
                f.write("")
                print("  %-16s 0 行（空表，跳过表头）" % t)
                continue
            w = csv.writer(f)
            w.writerow(rows[0].keys())
            for r in rows:
                w.writerow([r[k] for k in r.keys()])
        print("  %-16s %4d 行 → %s" % (t, len(rows), p.name))
    conn.close()
    print("\n输出目录：%s" % OUT)


if __name__ == "__main__":
    only = None
    if "--table" in sys.argv:
        only = sys.argv[sys.argv.index("--table") + 1]
    export(only=only)
