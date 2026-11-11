# -*- coding: utf-8 -*-
"""商客对话监测的持久化层。

核心约定：
  * truth_type IS NULL      未标注，不参与任何质量指标
  * truth_type = '正常沟通'  已标注负样本
  * 其他 truth_type         已标注正样本
  * 所有自由文本落库前统一脱敏，原始输入只在当前请求内用于判定
"""
import json
import sqlite3
import recall
import security
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "sentinel.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  scenario_id TEXT NOT NULL,
  turn        INTEGER NOT NULL,
  rule_id     TEXT,
  type        TEXT,
  useful      INTEGER NOT NULL,     -- 1 准 / 0 误报
  note        TEXT DEFAULT '',
  created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS judge_logs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          TEXT NOT NULL,
  source      TEXT,              -- manual / random / batch
  text        TEXT NOT NULL,
  truth_type  TEXT,              -- 随机生成时知道的真实类型；手输为空
  rule_hit    INTEGER,
  rule_type   TEXT,
  rule_score  REAL,
  llm_hit     INTEGER,           -- NULL 表示未配置模型
  llm_type    TEXT,
  llm_conf    REAL,
  agree       INTEGER,           -- 规则与模型是否一致；NULL 表示没跑模型
  covered     INTEGER,           -- 1=照规则说法写 0=换说法 NULL=手输
  tier        TEXT,              -- rule / small / large
  cost_units  REAL,              -- 相对成本单位
  route_reason TEXT,
  lat_ms      INTEGER,
  trace_id    TEXT
);
CREATE TABLE IF NOT EXISTS review_queue (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          TEXT NOT NULL,
  text        TEXT NOT NULL,
  truth_type  TEXT DEFAULT '',
  rule_hit    INTEGER,
  rule_type   TEXT,
  tier        TEXT,
  model_type  TEXT,
  model_conf  REAL,
  reason      TEXT,
  status      TEXT DEFAULT 'pending',   -- pending / approved / rejected
  reviewer    TEXT DEFAULT '',
  decided_at  TEXT DEFAULT '',
  note        TEXT DEFAULT '',
  trace_id    TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  order_no   TEXT,
  merchant   TEXT,
  user_id    TEXT,
  turns      INTEGER DEFAULT 0,
  counts     TEXT,
  verdict    TEXT DEFAULT 'allow',
  reason     TEXT,
  created    TEXT,
  updated    TEXT
);
CREATE TABLE IF NOT EXISTS security_events (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  ts     TEXT NOT NULL,
  kind   TEXT NOT NULL,       -- injection / rate_limit / oversize / blocked
  ip     TEXT,
  path   TEXT,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS degrade_events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        TEXT NOT NULL,
  component TEXT NOT NULL,      -- llm.small / llm.large / cache / db
  event     TEXT NOT NULL,      -- open / half_open / fallback / recover
  reason    TEXT,
  mode      TEXT
);
CREATE TABLE IF NOT EXISTS decision_cache (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  key       TEXT NOT NULL UNIQUE,     -- 归一化后的 sha1，精确命中用
  text      TEXT NOT NULL,
  norm      TEXT NOT NULL,
  simhash   INTEGER NOT NULL,
  verdict   TEXT NOT NULL,            -- 存整个判定结果的 JSON
  source    TEXT NOT NULL,            -- rule / human / small / large
  conf      REAL NOT NULL DEFAULT 0.9,
  rule_ver  TEXT NOT NULL,            -- 规则库版本，规则一改就失效
  hits      INTEGER NOT NULL DEFAULT 0,
  ts        TEXT NOT NULL,
  last_hit  TEXT
);
CREATE TABLE IF NOT EXISTS cache_band (
  band     TEXT NOT NULL,
  cache_id INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_band ON cache_band(band);
CREATE INDEX IF NOT EXISTS idx_cache_key ON decision_cache(key);

CREATE TABLE IF NOT EXISTS orders (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  order_no      TEXT NOT NULL UNIQUE,
  user_id       TEXT DEFAULT 'u-1001',
  merchant      TEXT DEFAULT '小家电旗舰店',
  amount        REAL DEFAULT 0,
  status        TEXT NOT NULL DEFAULT 'shipped',   -- shipped / dispute / resolved
  refund_status TEXT NOT NULL DEFAULT 'none',      -- none / processing / refunded
  frozen        INTEGER NOT NULL DEFAULT 0         -- 1 = 冻结结算
);
CREATE TABLE IF NOT EXISTS cases (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  case_no     TEXT NOT NULL,
  ts          TEXT NOT NULL,
  review_id   INTEGER,
  text        TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'accepted',
  evidence    TEXT DEFAULT '',
  merchant    TEXT DEFAULT '小家电旗舰店',
  user_id     TEXT DEFAULT 'u-1001'
);
CREATE TABLE IF NOT EXISTS notifications (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          TEXT NOT NULL,
  user_id     TEXT NOT NULL,
  title       TEXT NOT NULL,
  body        TEXT DEFAULT '',
  case_no     TEXT DEFAULT '',
  read        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS merchants (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  violations  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rule_candidates (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          TEXT NOT NULL,
  text        TEXT NOT NULL,
  kind        TEXT NOT NULL,       -- positive=确认要介入，negative=放行
  hits        INTEGER NOT NULL DEFAULT 1,
  status      TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS eval_runs (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  ts             TEXT NOT NULL,
  dev_recall     REAL,
  holdout_recall REAL,
  holdout2_recall REAL,
  holdout3_recall REAL,
  false_positive REAL,
  type_accuracy  REAL,
  passed         INTEGER
);
CREATE TABLE IF NOT EXISTS events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          TEXT NOT NULL,
  event       TEXT NOT NULL,
  schema_ver  INTEGER NOT NULL DEFAULT 1,
  trace_id    TEXT NOT NULL,
  session_id  TEXT DEFAULT '',
  actor_id    TEXT DEFAULT '',
  properties  TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id);
CREATE INDEX IF NOT EXISTS idx_events_name_ts ON events(event, ts);
"""


def connect():
    """统一连接参数：WAL 让读写不打架，NORMAL 在崩溃时不会丢已提交数据。"""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")      # 崩溃可恢复 + 读写并发
    conn.execute("PRAGMA synchronous=NORMAL")    # 已提交事务不丢
    conn.execute("PRAGMA busy_timeout=5000")     # 抢锁最多等 5 秒
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init():
    conn = connect()
    with conn:
        conn.executescript(SCHEMA)
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(judge_logs)")]
        if "covered" not in cols:
            conn.execute("ALTER TABLE judge_logs ADD COLUMN covered INTEGER")
        if "lat_ms" not in cols:
            conn.execute("ALTER TABLE judge_logs ADD COLUMN lat_ms INTEGER")
        for col, decl in (("tier", "TEXT"), ("cost_units", "REAL"), ("route_reason", "TEXT")):
            if col not in cols:
                conn.execute("ALTER TABLE judge_logs ADD COLUMN %s %s" % (col, decl))
        if "trace_id" not in cols:
            conn.execute("ALTER TABLE judge_logs ADD COLUMN trace_id TEXT")
        rcols = [r["name"] for r in conn.execute("PRAGMA table_info(review_queue)")]
        for col, decl in (("session_id", "TEXT"), ("user_id", "TEXT"),
                          ("case_no", "TEXT"), ("preset", "TEXT"), ("escalated", "INTEGER")):
            if col not in rcols:
                conn.execute("ALTER TABLE review_queue ADD COLUMN %s %s" % (col, decl))
        if "trace_id" not in rcols:
            conn.execute("ALTER TABLE review_queue ADD COLUMN trace_id TEXT DEFAULT ''")
        ecols = [r["name"] for r in conn.execute("PRAGMA table_info(eval_runs)")]
        if "holdout3_recall" not in ecols:
            conn.execute("ALTER TABLE eval_runs ADD COLUMN holdout3_recall REAL")

        # 兼容旧数据：空字符串曾经同时表示“未标注”和“正常”，这是错误口径。
        conn.execute(
            "UPDATE judge_logs SET truth_type=NULL, covered=NULL "
            "WHERE truth_type='' AND source IN ('manual','session','seed','human')")
        conn.execute(
            "UPDATE judge_logs SET truth_type='正常沟通' "
            "WHERE truth_type='' AND source='batch'")
        if not conn.execute("SELECT 1 FROM merchants LIMIT 1").fetchone():
            conn.execute("INSERT INTO merchants(id,name,violations) VALUES('m-2001','小家电旗舰店',12)")
        if not conn.execute("SELECT 1 FROM orders LIMIT 1").fetchone():
            conn.execute("INSERT INTO orders(order_no,user_id,merchant,amount,status,refund_status)"
                         " VALUES('2026 1109 8871','u-1001','小家电旗舰店',319.0,'shipped','none')")
    conn.close()



# ============================================================ 判定缓存

def _signed(h64):
    """64 位无符号 → SQLite 能存的 64 位有符号。"""
    return h64 - (1 << 64) if h64 >= (1 << 63) else h64




def log_security(kind, ip="", path="", detail=""):
    conn = connect()
    with conn:
        conn.execute("INSERT INTO security_events(ts,kind,ip,path,detail) VALUES(?,?,?,?,?)",
                     (now(), kind, ip, path, security.redact(detail)[:300]))
    conn.close()


def recent_security(limit=20):
    conn = connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM security_events ORDER BY id DESC LIMIT ?", (limit,))]
    conn.close()
    return rows


def log_degrade(component, event, reason="", mode=""):
    conn = connect()
    with conn:
        conn.execute("INSERT INTO degrade_events(ts,component,event,reason,mode)"
                     " VALUES(?,?,?,?,?)",
                     (now(), component, event, security.redact(reason)[:300], mode))
    conn.close()


def recent_degrade(limit=20):
    conn = connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM degrade_events ORDER BY id DESC LIMIT ?", (limit,))]
    conn.close()
    return rows



def pending_count():
    conn = connect()
    n = conn.execute(
        "SELECT COUNT(*) n FROM review_queue WHERE status='pending'").fetchone()["n"] or 0
    conn.close()
    return n


def rule_version():
    """规则库版本：文件内容变了就变，缓存自动失效。"""
    import hashlib
    p = Path(__file__).resolve().parent / "data" / "rules.jsonl"
    if not p.exists():
        return "none"
    return hashlib.sha1(p.read_bytes()).hexdigest()[:10]


def cache_exact(norm_text):
    """精确命中：同一句归一化后完全一致。"""
    conn = connect()
    rv = rule_version()
    row = conn.execute(
        "SELECT * FROM decision_cache WHERE key=? AND rule_ver=?",
        (recall.key_of(norm_text), rv)).fetchone()
    if not row:
        conn.close()
        return None
    with conn:
        conn.execute("UPDATE decision_cache SET hits=hits+1, last_hit=? WHERE id=?",
                     (now(), row["id"]))
    out = dict(row)
    conn.close()
    return out


def cache_similar(norm_text, limit=200):
    """相似命中：SimHash 分桶取候选，再用 Dice 精排。

    返回 (row, score, dice) 或 None。两道闸：
      · Dice ≥ 0.86 直接认
      · 0.72 ≤ Dice < 0.86 时，要求缓存那条的命中词出现在新句子里
    """
    conn = connect()
    rv = rule_version()
    h = recall.simhash(norm_text)
    bs = recall.bands(h)
    q = ",".join("?" * len(bs))
    cand = conn.execute(
        "SELECT DISTINCT c.* FROM cache_band b JOIN decision_cache c ON c.id=b.cache_id "
        "WHERE b.band IN (%s) AND c.rule_ver=? LIMIT ?" % q,
        (*bs, rv, limit)).fetchall()

    # 兜底：分桶没撞上时，直接看最近 N 条（缓存不大时这点开销可忽略）
    if len(cand) < 5:
        recent = conn.execute(
            "SELECT * FROM decision_cache WHERE rule_ver=? ORDER BY id DESC LIMIT ?",
            (rv, limit)).fetchall()
        seen = {r["id"] for r in cand}
        cand = list(cand) + [r for r in recent if r["id"] not in seen]

    if not cand:
        conn.close()
        return None

    g_new = recall.bigrams(norm_text)
    best, best_score, best_dice = None, 0.0, 0.0
    for row in cand:
        d = recall.dice(g_new, recall.bigrams(row["norm"]))
        if d < recall.TH_FUZZY:
            continue
        if d < recall.TH_EXACT:
            # 中等相似：必须共享同一个触发词，否则容易误召回
            try:
                v = json.loads(row["verdict"])
            except Exception:
                continue
            kw = (v.get("rule") or {}).get("matched") or ""
            if not kw or kw not in norm_text:
                continue
        if d > best_dice:
            best, best_dice = row, d
    if not best:
        conn.close()
        return None
    with conn:
        conn.execute("UPDATE decision_cache SET hits=hits+1, last_hit=? WHERE id=?",
                     (now(), best["id"]))
    out = dict(best)
    conn.close()
    return out, best_dice


def cache_save(text, norm_text, verdict, source="rule", conf=0.9):
    """写缓存（同 key 覆盖）。"""
    conn = connect()
    h = recall.simhash(norm_text)
    with conn:
        cur = conn.execute(
            "INSERT INTO decision_cache(key,text,norm,simhash,verdict,source,conf,rule_ver,ts)"
            " VALUES(?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET verdict=excluded.verdict,"
            " source=excluded.source, conf=excluded.conf, rule_ver=excluded.rule_ver",
            (recall.key_of(norm_text), security.redact(text), norm_text, _signed(h),
             security.redact(json.dumps(verdict, ensure_ascii=False)), source, conf,
             rule_version(), now()))
        cid = cur.lastrowid
        if not cid:
            row = conn.execute("SELECT id FROM decision_cache WHERE key=?",
                               (recall.key_of(norm_text),)).fetchone()
            cid = row["id"] if row else 0
        if cid:
            conn.execute("DELETE FROM cache_band WHERE cache_id=?", (cid,))
            for b in recall.bands(h):
                conn.execute("INSERT INTO cache_band(band,cache_id) VALUES(?,?)", (b, cid))
    conn.close()



def cache_drop(text):
    """删掉某句话的缓存（精确 + 近似）。人工裁决翻转时调用。"""
    n = recall.normalize(text)
    conn = connect()
    with conn:
        rows = conn.execute("SELECT id FROM decision_cache WHERE key=?",
                            (recall.key_of(n),)).fetchall()
        ids = [r["id"] for r in rows]
        for cid in ids:
            conn.execute("DELETE FROM cache_band WHERE cache_id=?", (cid,))
            conn.execute("DELETE FROM decision_cache WHERE id=?", (cid,))
        # 近似条目也一并清掉
        for r in conn.execute("SELECT id,norm FROM decision_cache").fetchall():
            if recall.dice(recall.bigrams(n), recall.bigrams(r["norm"])) >= recall.TH_FUZZY:
                conn.execute("DELETE FROM cache_band WHERE cache_id=?", (r["id"],))
                conn.execute("DELETE FROM decision_cache WHERE id=?", (r["id"],))
                ids.append(r["id"])
    conn.close()
    return len(set(ids))


def cache_stats():
    conn = connect()
    rv = rule_version()
    total = conn.execute("SELECT COUNT(*) n FROM decision_cache").fetchone()["n"]
    live = conn.execute("SELECT COUNT(*) n FROM decision_cache WHERE rule_ver=?",
                        (rv,)).fetchone()["n"]
    hits = conn.execute("SELECT SUM(hits) s FROM decision_cache").fetchone()["s"] or 0
    rows = [dict(r) for r in conn.execute(
        "SELECT source, COUNT(*) n, SUM(hits) h FROM decision_cache GROUP BY source")]
    conn.close()
    return {"total": total, "live": live, "stale": total - live,
            "saved_calls": hits, "by_source": rows, "rule_ver": rv}


def cache_clear():
    conn = connect()
    with conn:
        conn.execute("DELETE FROM cache_band")
        conn.execute("DELETE FROM decision_cache")
    conn.close()


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def add_feedback(scenario_id, turn, rule_id, vtype, useful, note=""):
    conn = connect()
    with conn:
        conn.execute(
            "INSERT INTO feedback(scenario_id,turn,rule_id,type,useful,note,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (security.redact(scenario_id), int(turn), security.redact(rule_id or ""),
             security.redact(vtype or ""), 1 if useful else 0,
             security.redact(note or "")[:500], now()))
    conn.close()


def add_judge_log(source, text, truth_type, rule, llm_res, covered=None,
                  tier=None, cost_units=0.0, route_reason="", lat_ms=None,
                  trace_id=""):
    agree = None
    if llm_res and llm_res.get("hit") is not None:
        agree = 1 if bool(rule["hit"]) == bool(llm_res["hit"]) else 0
    conn = connect()
    with conn:
        conn.execute(
            "INSERT INTO judge_logs(ts,source,text,truth_type,rule_hit,rule_type,rule_score,"
            "llm_hit,llm_type,llm_conf,agree,covered,tier,cost_units,route_reason,lat_ms,"
            "trace_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now(), source, security.redact(text),
             security.redact(truth_type) if truth_type is not None else None,
             1 if rule["hit"] else 0, rule.get("type", ""), rule.get("score", 0),
             None if not llm_res or llm_res.get("hit") is None else (1 if llm_res["hit"] else 0),
             (llm_res or {}).get("type", ""), (llm_res or {}).get("confidence", 0),
             agree, covered, tier, cost_units, security.redact(route_reason)[:500],
             lat_ms, trace_id or ""))
    conn.close()


def judge_stats(limit=200):
    conn = connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM judge_logs ORDER BY id DESC LIMIT ?", (limit,))]
    agg = dict(conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(rule_hit),0) rh,"
        " SUM(CASE WHEN llm_hit IS NOT NULL THEN 1 ELSE 0 END) lm,"
        " COALESCE(SUM(llm_hit),0) lh,"
        " SUM(CASE WHEN agree IS NOT NULL THEN 1 ELSE 0 END) ag,"
        " COALESCE(SUM(agree),0) ag_ok,"
        " SUM(CASE WHEN truth_type IS NOT NULL AND truth_type<>'正常沟通'"
        " THEN 1 ELSE 0 END) tl,"
        " COALESCE(SUM(CASE WHEN truth_type IS NOT NULL AND truth_type<>'正常沟通'"
        " AND rule_type=truth_type THEN 1 ELSE 0 END),0) tl_ok"
        " FROM judge_logs").fetchone())
    split = dict(conn.execute(
        "SELECT"
        " SUM(CASE WHEN covered=1 AND truth_type IS NOT NULL"
        " AND truth_type<>'正常沟通' THEN 1 ELSE 0 END) cov,"
        " COALESCE(SUM(CASE WHEN covered=1 AND truth_type IS NOT NULL"
        " AND truth_type<>'正常沟通' AND rule_type=truth_type"
        " THEN 1 ELSE 0 END),0) cov_ok,"
        " SUM(CASE WHEN covered=0 AND truth_type IS NOT NULL"
        " AND truth_type<>'正常沟通' THEN 1 ELSE 0 END) par,"
        " COALESCE(SUM(CASE WHEN covered=0 AND truth_type IS NOT NULL"
        " AND truth_type<>'正常沟通' AND rule_type=truth_type"
        " THEN 1 ELSE 0 END),0) par_ok"
        " FROM judge_logs WHERE source<>'human'").fetchone())
    split = {k: (v or 0) for k, v in split.items()}
    dec = dict(conn.execute(
        "SELECT COUNT(*) n,"
        " COALESCE(SUM(CASE WHEN (rule_hit=1) = (truth_type<>'正常沟通')"
        " THEN 1 ELSE 0 END),0) ok"
        " FROM judge_logs WHERE truth_type IS NOT NULL AND source<>'human'").fetchone())
    split["dec"] = dec["n"] or 0
    split["dec_ok"] = dec["ok"] or 0
    types = {r["type"]: r["n"] for r in conn.execute(
        "SELECT rule_type type, COUNT(*) n FROM judge_logs WHERE rule_hit=1 "
        "GROUP BY rule_type ORDER BY n DESC")}
    tiers = {(r["tier"] or "rule"): r["n"] for r in conn.execute(
        "SELECT tier, COUNT(*) n FROM judge_logs GROUP BY tier")}
    avg_cost = conn.execute("SELECT AVG(cost_units) c FROM judge_logs").fetchone()["c"] or 0
    conn.close()
    n = agg["n"] or 0
    return {
        "rows": rows,
        "stats": {
            "total": n,
            "rule_hit_rate": round(agg["rh"] / n, 3) if n else 0,
            "llm_calls": agg["lm"],
            "llm_hit_rate": round(agg["lh"] / agg["lm"], 3) if agg["lm"] else 0,
            "agree_rate": round(agg["ag_ok"] / agg["ag"], 3) if agg["ag"] else 0,
            "truth_checked": agg["tl"],
            "rule_type_accuracy": round(agg["tl_ok"] / agg["tl"], 3) if agg["tl"] else 0,
            "decision_accuracy": split["dec_ok"] / split["dec"] if split["dec"] else 0,
            "acc_covered": split["cov_ok"] and round(split["cov_ok"] / split["cov"], 3) or 0,
            "acc_covered_n": split["cov"],
            "acc_paraphrase": split["par_ok"] and round(split["par_ok"] / split["par"], 3) or 0,
            "acc_paraphrase_n": split["par"],
            "tiers": tiers,
            "avg_cost_units": round(avg_cost, 2),
            "by_type": types,
        },
    }


def add_review(text, truth_type, rule, tier, model, reason, trace_id=""):
    conn = connect()
    with conn:
        conn.execute(
            "INSERT INTO review_queue(ts,text,truth_type,rule_hit,rule_type,tier,"
            "model_type,model_conf,reason,trace_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (now(), security.redact(text),
             security.redact(truth_type) if truth_type is not None else None,
             1 if rule.get("hit") else 0,
             rule.get("type", ""), tier,
             (model or {}).get("type", ""), (model or {}).get("confidence", 0),
             security.redact(reason)[:500], security.redact(trace_id)[:64]))
        rid = conn.execute("SELECT last_insert_rowid() r").fetchone()["r"]
    conn.close()
    return rid


def list_review(status="pending", limit=50):
    conn = connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM review_queue WHERE status=? ORDER BY id DESC LIMIT ?",
        (status, limit))]
    # 给每条挂上上下文：同类型历史命中次数、商家累计违规、候选池相近条数
    for r in rows:
        rt = (r.get("rule_type") or "") if r.get("rule_hit") else ""   # 未命中就没有「同类」
        r["same_type"] = conn.execute(
            "SELECT COUNT(*) n FROM judge_logs WHERE rule_type=? AND source<>'human'",
            (rt,)).fetchone()["n"] if rt else 0
        r["candidates"] = conn.execute(
            "SELECT COUNT(*) n FROM rule_candidates WHERE status='open' AND text LIKE ?",
            ("%" + (r.get("text") or "")[:8] + "%",)).fetchone()["n"]
        r["decided_same"] = conn.execute(
            "SELECT COUNT(*) n FROM review_queue WHERE status<>'pending' AND rule_type=?",
            (rt,)).fetchone()["n"] if rt else 0
        if rt:
            h = conn.execute(
                "SELECT SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END) a,"
                " SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) b"
                " FROM review_queue WHERE status<>'pending' AND rule_type=?", (rt,)).fetchone()
            r["hist_approve"] = h["a"] or 0
            r["hist_reject"] = h["b"] or 0
        else:
            r["hist_approve"] = r["hist_reject"] = 0
    r_stats = dict(conn.execute(
        "SELECT COUNT(*) n FROM review_queue").fetchone())
    m = conn.execute("SELECT violations FROM merchants WHERE id='m-2001'").fetchone()
    violations = m["violations"] if m else 0
    counts = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) n FROM review_queue GROUP BY status")}
    conn.close()
    return {"rows": rows, "counts": counts, "pending": counts.get("pending", 0),
            "merchant_violations": violations}


def decide_review(rid, action, reviewer="审核员", note="", preset="refund"):
    """action: approve / reject / escalate；preset: refund / warn / fp / escalate。"""
    status = "approved" if action == "approve" else "rejected"
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM review_queue WHERE id=?", (rid,)).fetchone()
        if not row:
            return False, "记录不存在"
        if row["status"] != "pending":
            return False, "该条已处理（%s）" % row["status"]

        # ---- 转专家：不结案 ----
        if action == "escalate":
            with conn:
                conn.execute(
                    "UPDATE review_queue SET escalated=1, reviewer=?, note=? WHERE id=?",
                    (security.redact(reviewer)[:64],
                     security.redact(note or "")[:500], rid))
            return True, "已转专家复核，该条仍留在待复核队列"

        case_no = ""
        penalty = "警告"
        detail = ""

        with conn:
            safe_text = security.redact(row["text"])
            conn.execute(
                "UPDATE review_queue SET status=?, reviewer=?, decided_at=?, note=?, preset=?"
                " WHERE id=?", (status, security.redact(reviewer)[:64], now(),
                                security.redact(note or "")[:500], preset, rid))

            if action == "approve":
                n = conn.execute("SELECT COUNT(*) n FROM cases").fetchone()["n"]
                case_no = "C-%04d" % (n + 1)

                order = conn.execute(
                    "SELECT * FROM orders ORDER BY id DESC LIMIT 1").fetchone()
                order_no = order["order_no"] if order else ""
                if order:
                    if preset == "warn":
                        conn.execute("UPDATE orders SET status='dispute' WHERE order_no=?",
                                     (order_no,))
                    else:
                        conn.execute(
                            "UPDATE orders SET status='dispute', refund_status='processing',"
                            " frozen=1 WHERE order_no=?", (order_no,))

                conn.execute("UPDATE merchants SET violations=violations+1 WHERE id='m-2001'")
                v = conn.execute(
                    "SELECT violations FROM merchants WHERE id='m-2001'"
                ).fetchone()["violations"]
                if preset == "warn":
                    penalty = "警告"
                elif v >= 20:
                    penalty = "下架整改"
                elif v >= 15:
                    penalty = "限流 7 天"
                else:
                    penalty = "警告"

                if preset == "warn":
                    detail = ("订单 %s 已转入纠纷处理 · 未自动退款 · 商家处罚：%s"
                              "（累计违规 %d 次）" % (order_no or "—", penalty, v))
                else:
                    detail = ("订单 %s 已转入纠纷处理 · 退款处理中 · 结算已冻结 · 商家处罚：%s"
                              "（累计违规 %d 次）" % (order_no or "—", penalty, v))

                conn.execute(
                    "INSERT INTO cases(case_no,ts,review_id,text,status,evidence,merchant,user_id)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (case_no, now(), rid, safe_text, "accepted",
                     detail + "；原话已固定；规则=%s；分流=%s"
                     % (row["rule_type"] or "未命中", row["tier"] or "rule"),
                     "小家电旗舰店", row["user_id"] or "u-1001"))

                conn.execute(
                    "INSERT INTO notifications(ts,user_id,title,body,case_no) VALUES(?,?,?,?,?)",
                    (now(), row["user_id"] or "u-1001", "平台已受理你的举报",
                     "你反映的「%s」经人工核实成立。订单已转入纠纷处理，退款正在原路退回，"
                     "商家已被处罚。介入单 %s。" % (safe_text[:24], case_no), case_no))
                conn.execute("UPDATE review_queue SET case_no=? WHERE id=?", (case_no, rid))

            # 两种裁决都进候选池：够数就说明规则该更新
            kind = "positive" if action == "approve" else "negative"
            key = (row["text"] or "").strip()
            hit = conn.execute(
                "SELECT * FROM rule_candidates WHERE text=? AND kind=? AND status='open'",
                (key, kind)).fetchone()
            if hit:
                conn.execute("UPDATE rule_candidates SET hits=hits+1 WHERE id=?", (hit["id"],))
            else:
                conn.execute("INSERT INTO rule_candidates(ts,text,kind,hits) VALUES(?,?,?,1)",
                             (now(), key, kind))

            conn.execute(
                "INSERT INTO judge_logs(ts,source,text,truth_type,rule_hit,rule_type,"
                "rule_score,llm_hit,llm_type,llm_conf,agree,covered,tier,cost_units,"
                "route_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now(), "human", safe_text,
                 security.redact(row["truth_type"]) if row["truth_type"] is not None else None,
                 1 if action == "approve" else 0, row["rule_type"], 0,
                 None, row["model_type"], row["model_conf"], None, None,
                 "human", 0, "人工裁决：" + security.redact(reviewer)[:64]))

        # 人工结论与缓存可能不同，清掉这句的缓存（事务外，失败不影响裁决）
        try:
            cache_drop(row["text"])
        except Exception:
            pass
        try:
            add_event("case_decide", row["trace_id"] or ("review-%s" % rid), {
                "action": action, "preset": preset, "rule_id": row["rule_type"],
                "rule_hit": bool(row["rule_hit"]),
            }, session_id=row["session_id"] or "")
        except Exception:
            pass

        if action == "approve":
            if preset == "warn":
                return True, ("已确认介入（仅警告）：订单转入纠纷处理，未自动退款；"
                              "商家处罚 警告；介入单 %s" % case_no)
            return True, ("已确认介入：订单转入纠纷处理、退款启动、结算冻结；"
                          "商家处罚 %s；介入单 %s" % (penalty, case_no))
        return True, "已放行，样本进入规则候选池（negative）"
    finally:
        conn.close()

def metrics_detail():
    """返回每个指标的公式、分子、分母、当前值。口径公开，谁都能重算。"""
    conn = connect()
    j = dict(conn.execute(
        "SELECT COUNT(*) n,"
        " COALESCE(SUM(CASE WHEN tier='rule' THEN 1 ELSE 0 END),0) auto_rule,"
        " COALESCE(SUM(CASE WHEN source='human' THEN 1 ELSE 0 END),0) human,"
        " COALESCE(SUM(CASE WHEN truth_type IS NOT NULL THEN 1 ELSE 0 END),0) labeled,"
        " COALESCE(SUM(CASE WHEN truth_type IS NOT NULL"
        " AND truth_type<>'正常沟通' AND rule_hit=1 THEN 1 ELSE 0 END),0) tp,"
        " COALESCE(SUM(CASE WHEN truth_type IS NOT NULL"
        " AND truth_type<>'正常沟通' AND rule_hit=0 THEN 1 ELSE 0 END),0) fn,"
        " COALESCE(SUM(CASE WHEN truth_type='正常沟通' AND rule_hit=1 THEN 1 ELSE 0 END),0) fp,"
        " COALESCE(SUM(CASE WHEN truth_type='正常沟通' AND rule_hit=0 THEN 1 ELSE 0 END),0) tn,"
        " COALESCE(SUM(cost_units),0) cost"
        " FROM judge_logs WHERE source<>'human'").fetchone())
    q = dict(conn.execute(
        "SELECT COUNT(*) n,"
        " COALESCE(SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),0) pending,"
        " COALESCE(SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END),0) approved,"
        " COALESCE(SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END),0) rejected"
        " FROM review_queue").fetchone())
    conn.close()

    total = j["n"] or 0
    decided = (q["approved"] or 0) + (q["rejected"] or 0)
    tp, fn, fp, tn = j["tp"], j["fn"], j["fp"], j["tn"]
    pos, neg = tp + fn, fp + tn

    def ratio(a, b):
        return round(a / b, 4) if b else 0.0

    metrics = [
        {"key": "auto_rate", "name": "自动判定率",
         "formula": "(总判定 − 待复核) / 总判定",
         "num": total - q["pending"], "den": total,
         "value": ratio(total - q["pending"], total),
         "note": "规则或模型直接给出结论的比例"},
        {"key": "pending_rate", "name": "待复核率",
         "formula": "待复核 / 总判定",
         "num": q["pending"], "den": total,
         "value": ratio(q["pending"], total),
         "note": "越高说明规则覆盖不足"},
        {"key": "human_flip", "name": "人工改判率",
         "formula": "人工结论与规则不一致 / 已裁决",
         "num": q["approved"] if False else None, "den": decided,
         "value": None, "note": "人工推翻规则结论的比例，衡量规则有多不准"},
        {"key": "recall", "name": "召回率",
         "formula": "TP / (TP + FN)",
         "num": tp, "den": pos,
         "value": ratio(tp, pos),
         "note": "只统计有标注的样本"},
        {"key": "fpr", "name": "误报率",
         "formula": "FP / (FP + TN)",
         "num": fp, "den": neg,
         "value": ratio(fp, neg),
         "note": "只统计有标注的正常样本"},
        {"key": "cost", "name": "平均成本（单位）",
         "formula": "Σ 各层成本 / 总判定",
         "num": round(j["cost"], 2), "den": total,
         "value": ratio(j["cost"], total),
         "note": "规则 0 / 小模型 1 / 大模型 10"},
    ]

    # 人工改判率：拿复核队列里「规则当时的结论」和「人的最终结论」比
    conn = connect()
    flip_row = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN (rule_hit=1) <> (status='approved')"
        " THEN 1 ELSE 0 END),0) flip FROM review_queue WHERE status<>'pending'").fetchone()
    conn.close()
    for m in metrics:
        if m["key"] == "human_flip":
            m["num"] = flip_row["flip"]
            m["den"] = flip_row["n"] or 0
            m["value"] = round(flip_row["flip"] / flip_row["n"], 4) if flip_row["n"] else 0.0

    return {
        "metrics": metrics,
        "scope": {
            "judge_logs": total,
            "review_total": q["n"],
            "review_pending": q["pending"],
            "review_decided": decided,
            "labeled": j["labeled"],
            "note": "所有指标都可由 judge_logs 与 review_queue 两张表重算，"
                    "不依赖任何隐藏中间值。",
        },
    }


def list_cases(limit=20):
    conn = connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM cases ORDER BY id DESC LIMIT ?", (limit,))]
    notif = [dict(r) for r in conn.execute(
        "SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,))]
    cand = [dict(r) for r in conn.execute(
        "SELECT * FROM rule_candidates WHERE status='open' ORDER BY hits DESC, id DESC LIMIT ?",
        (limit,))]
    merch = dict(conn.execute("SELECT * FROM merchants WHERE id='m-2001'").fetchone())
    orders = [dict(r) for r in conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 5")]
    conn.close()
    return {"cases": rows, "notifications": notif, "candidates": cand,
            "merchant": merch, "orders": orders}


def list_feedback(limit=30):
    conn = connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM feedback ORDER BY id DESC LIMIT ?", (limit,))]
    stats = dict(conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(useful),0) good FROM feedback").fetchone())
    conn.close()
    stats["false_positive_rate"] = round(
        (stats["n"] - stats["good"]) / stats["n"], 3) if stats["n"] else 0.0
    return {"rows": rows, "stats": stats}


def add_eval_run(dev, holdout, holdout2, holdout3, passed):
    conn = connect()
    with conn:
        conn.execute(
            "INSERT INTO eval_runs(ts,dev_recall,holdout_recall,holdout2_recall,"
            "holdout3_recall,false_positive,type_accuracy,passed)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (now(), dev["detect_rate"], holdout["detect_rate"], holdout2["detect_rate"],
             holdout3["detect_rate"],
             max(dev["false_positive_rate"], holdout["false_positive_rate"],
                 holdout2["false_positive_rate"], holdout3["false_positive_rate"]),
             holdout3["type_accuracy"], 1 if passed else 0))
    conn.close()


def list_eval_runs(limit=10):
    conn = connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM eval_runs ORDER BY id DESC LIMIT ?", (limit,))]
    conn.close()
    return rows


ALLOWED_EVENTS = {
    "judge_result", "card_exposure", "card_evidence_open",
    "card_click_intervene", "card_feedback", "review_open", "case_decide",
}


def add_event(event, trace_id, properties=None, session_id="", actor_id="", schema_ver=1):
    """写入结构化产品事件；只接受白名单事件，属性统一脱敏并限制大小。"""
    if event not in ALLOWED_EVENTS:
        raise ValueError("未登记的事件：%s" % event)
    if not trace_id:
        raise ValueError("trace_id 不能为空")
    props = properties if isinstance(properties, dict) else {}
    props_text = security.redact(json.dumps(props, ensure_ascii=False))[:4000]
    conn = connect()
    with conn:
        conn.execute(
            "INSERT INTO events(ts,event,schema_ver,trace_id,session_id,actor_id,properties)"
            " VALUES(?,?,?,?,?,?,?)",
            (now(), event, int(schema_ver), security.redact(trace_id)[:64],
             security.redact(session_id)[:64], security.redact(actor_id)[:64], props_text))
    conn.close()


def event_stats(limit=5000):
    conn = connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT event, COUNT(*) n FROM events ORDER BY id DESC LIMIT ?", (limit,))]
    totals = {}
    traces = set()
    for r in conn.execute("SELECT event, trace_id FROM events ORDER BY id DESC LIMIT ?",
                          (limit,)):
        totals[r["event"]] = totals.get(r["event"], 0) + 1
        traces.add(r["trace_id"])
    exposure = totals.get("card_exposure", 0)
    intervene = totals.get("card_click_intervene", 0)
    evidence = totals.get("card_evidence_open", 0)
    feedback = totals.get("card_feedback", 0)
    conn.close()
    return {
        "total": sum(totals.values()),
        "by_event": totals,
        "unique_traces": len(traces),
        "rates": {
            "intervene_click_rate": round(intervene / exposure, 4) if exposure else None,
            "evidence_open_rate": round(evidence / exposure, 4) if exposure else None,
            "feedback_rate": round(feedback / exposure, 4) if exposure else None,
        },
        "sample_warning": None if exposure >= 30 else "曝光样本不足 30，不据此判断满意度",
    }


if __name__ == "__main__":
    init()
    print(json.dumps({"db": str(DB_PATH), "eval_runs": list_eval_runs()},
                     ensure_ascii=False, indent=2))
