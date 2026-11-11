# -*- coding: utf-8 -*-
"""备份与数据健康检查。

    python backup.py            # 备份 + 校验 + 清理旧备份
    python backup.py --check    # 只做完整性检查
    python backup.py --list     # 列出备份
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = BASE / "sentinel.db"
OUT = BASE / "backups"
KEEP = 7


def backup():
    """用 SQLite 官方 backup API，热备份且一致（不锁库）。"""
    if not DB.exists():
        raise SystemExit("找不到数据库，先跑一次 demo.py")
    OUT.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst_path = OUT / ("sentinel-%s.db" % stamp)

    src = sqlite3.connect(DB, timeout=10)
    dst = sqlite3.connect(dst_path)
    with dst:
        src.backup(dst)          # 一致性快照
    src.close()
    dst.close()

    ok, msg = integrity(dst_path)
    size = dst_path.stat().st_size / 1024
    print(f"备份完成：{dst_path.name}（{size:.0f} KB）· 完整性 {msg}")
    prune()
    return dst_path


def integrity(path=DB):
    conn = sqlite3.connect(path)
    try:
        r = conn.execute("PRAGMA integrity_check").fetchone()[0]
        n = conn.execute("SELECT COUNT(*) FROM judge_logs").fetchone()[0]
    finally:
        conn.close()
    return (r == "ok"), f"{r}（judge_logs {n} 行）"


def prune(keep=KEEP):
    files = sorted(OUT.glob("sentinel-*.db"))
    for f in files[:-keep]:
        f.unlink()
        print(f"清理旧备份：{f.name}")
    return len(files)


def listing():
    if not OUT.exists():
        print("还没有备份")
        return
    for f in sorted(OUT.glob("sentinel-*.db"), reverse=True):
        print(f"  {f.name}  {f.stat().st_size / 1024:.0f} KB")


def health():
    """数据健康：一致性 + 新鲜度 + 备份年龄 + 关键表行数。"""
    ok, msg = integrity()
    conn = sqlite3.connect(DB)
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("judge_logs", "review_queue", "cases", "decision_cache")}
    last = conn.execute("SELECT MAX(ts) FROM judge_logs").fetchone()[0]
    conn.close()

    files = sorted(OUT.glob("sentinel-*.db")) if OUT.exists() else []
    age_h = None
    if files:
        age_h = (datetime.now().timestamp() - files[-1].stat().st_mtime) / 3600

    return {"integrity": ok, "integrity_msg": msg, "counts": counts,
            "last_judge": last, "backups": len(files),
            "backup_age_hours": round(age_h, 1) if age_h is not None else None}


if __name__ == "__main__":
    if "--check" in sys.argv:
        ok, msg = integrity()
        print(("通过：" if ok else "异常：") + msg)
        sys.exit(0 if ok else 1)
    if "--list" in sys.argv:
        listing()
        sys.exit(0)
    backup()
