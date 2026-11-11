# -*- coding: utf-8 -*-
"""用一条 DSN 连数据库，打印每张表的行数——用来确认"连上了、数据在"。

支持三种目标（按驱动是否安装自动判断）：
    sqlite   直接可用
    mysql    需要 pip install pymysql
    postgres 需要 pip install psycopg[binary]

用法：
    python db_connect.py                                  # 默认连本地 sentinel.db
    python db_connect.py sqlite:///sentinel.db
    python db_connect.py mysql://root:pass@127.0.0.1:3306/sentinel
    python db_connect.py postgresql://user:pass@127.0.0.1:5432/sentinel
"""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
TABLES = ["judge_logs", "review_queue", "cases", "notifications",
          "merchants", "rule_candidates", "feedback", "eval_runs"]


def connect(dsn):
    """返回 (conn, kind)。只认 dsn，不读别的配置。"""
    if dsn.startswith("mysql://") or dsn.startswith("mysql+"):
        try:
            import pymysql
        except ImportError:
            raise SystemExit("缺驱动：pip install pymysql")
        from urllib.parse import urlparse
        u = urlparse(dsn.replace("mysql+", "mysql://", 1))
        return pymysql.connect(host=u.hostname, port=u.port or 3306,
                               user=u.username, password=u.password or "",
                               database=u.path.lstrip("/"), charset="utf8mb4"), "mysql"

    if dsn.startswith("postgres://") or dsn.startswith("postgresql://"):
        try:
            import psycopg
        except ImportError:
            raise SystemExit("缺驱动：pip install 'psycopg[binary]'")
        return psycopg.connect(dsn), "postgres"

    # 其余都当 sqlite 路径
    import sqlite3
    path = dsn.replace("sqlite:///", "").replace("sqlite://", "")
    p = Path(path)
    if not p.is_absolute():
        p = BASE / path
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    return conn, "sqlite"


def count(conn, kind, table):
    sql = "SELECT COUNT(*) AS n FROM " + table
    if kind == "sqlite":
        return conn.execute(sql).fetchone()["n"]
    cur = conn.cursor()
    cur.execute(sql)
    return cur.fetchone()[0]


def main():
    dsn = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
        "DATABASE_URL", "sqlite:///sentinel.db")
    conn, kind = connect(dsn)
    print("已连接：%s（%s）" % (dsn, kind))
    total = 0
    for t in TABLES:
        try:
            n = count(conn, kind, t)
            total += n
            print("  %-16s %6d 行" % (t, n))
        except Exception as e:
            print("  %-16s 读不到：%s" % (t, e))
    print("合计 %d 行" % total)
    conn.close()


if __name__ == "__main__":
    main()
