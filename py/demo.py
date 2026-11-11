# -*- coding: utf-8 -*-
"""商客对话监测 —— 本地演示服务（零第三方依赖）

路由：
  GET  /                     演示页面
  GET  /api/scenarios        场景列表
  GET  /api/scenario/<id>    单个场景 + 逐轮判定
  GET  /api/eval             跑一遍评测并返回指标（?record=1 时写入历史）
  POST /api/feedback         用户点「准 / 误报」，沉淀 bad case
  GET  /api/feedback         反馈列表与误报率

降级约定：判定失败不阻塞页面，返回 degraded 标记，前端按"未介入"展示并提示。
"""
import json
import hmac
import ipaddress
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import db
import recall
import resilience
import security
import backpressure
import slo
import session
import textnorm
import detector
import generator
import llm
import preprocess
import router
import run_eval

BASE = Path(__file__).resolve().parent
# 缓存开关：CACHE=0 可关掉召回，方便对比命中率
ENABLE_CACHE = os.environ.get("CACHE", "1") != "0"
# 单条判定的时间预算：超了就降级，不让用户干等
BUDGET_MS = int(os.environ.get("BUDGET_MS", "2500"))
PORT = int(os.environ.get("PORT", "8765"))
HOST = os.environ.get("HOST", "127.0.0.1")
READONLY = os.environ.get("READONLY", "0").strip() in ("1", "true", "yes")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
MAX_TURNS = 200          # 超过这个轮次不判，避免长会话拖垮服务
CLIENT_EVENTS = {
    "card_exposure", "card_evidence_open", "card_click_intervene",
    "card_feedback", "review_open",
}

DET = detector.Detector()
SCENARIOS = detector.load_jsonl("scenarios.jsonl")


def safe_analyze(scenario):
    if len(scenario.get("turns", [])) > MAX_TURNS:
        return {"scenario_id": scenario["id"], "title": scenario.get("title", ""),
                "verdicts": [], "first": None,
                "degraded": "会话过长，已跳过自动判定"}
    try:
        return DET.analyze(scenario)
    except Exception as e:  # 规则异常、数据异常都不该让页面挂
        return {"scenario_id": scenario["id"], "title": scenario.get("title", ""),
                "verdicts": [], "first": None, "degraded": "判定服务异常：%s" % e}


CACHE = {s["id"]: safe_analyze(s) for s in SCENARIOS}



RULES_BY_ID = {r["id"]: r for r in DET.rules}


def _record_event(event, trace_id, properties=None, session_id=""):
    """事件失败不能影响主判定。"""
    try:
        db.add_event(event, trace_id, properties or {}, session_id=session_id)
    except Exception as e:
        db.log_degrade("event", "drop", type(e).__name__)


def _cache_lookup(norm_text):
    breaker = resilience.BREAKERS["cache"]
    if not breaker.allow():
        return None, "", 0.0
    try:
        row = db.cache_exact(norm_text)
        if row:
            breaker.record_ok()
            return row, "exact", 1.0
        got = db.cache_similar(norm_text)
        breaker.record_ok()
        if got:
            row, score = got
            return row, "fuzzy", score
    except Exception as e:
        breaker.record_fail()
        db.log_degrade("cache", "fallback", type(e).__name__)
    return None, "", 0.0


def _cache_save(text, norm_text, result, tier, conf):
    breaker = resilience.BREAKERS["cache"]
    if not breaker.allow():
        return
    try:
        db.cache_save(text, norm_text, result, tier, conf)
        breaker.record_ok()
    except Exception as e:
        breaker.record_fail()
        db.log_degrade("cache", "write_fallback", type(e).__name__)


def judge_one(text, source="manual", truth=None, covered=None):
    """一条话术：预处理 → 规则判定 → 分流 → （按需）模型判定，落库后返回。"""
    t_start = time.time()
    trace_id = "judge-" + uuid.uuid4().hex[:12]
    raw_text = str(text or "")
    # 安全检查必须看原文，不能先截断；否则超长输入会绕过限制。
    sec = security.inspect(raw_text)
    text, meta = preprocess.normalize(raw_text)
    if not meta.get("ok"):
        return {"trace_id": trace_id, "text": "",
                "rule": {"hit": False, "type": "空文本", "score": 0},
                "llm": None, "tier": "rule", "skipped": "empty",
                "pending_review": False, "review_reason": "", "review_id": None}
    # ---- 背压：人工队列堵了就减载，别让雪球越滚越大 ----
    pending_now = db.pending_count()
    bp_level, bp_msg = backpressure.check(pending_now)
    if bp_level == "shed":
        return {"trace_id": trace_id, "text": text, "skipped": "shed",
                "backpressure": bp_level,
                "message": bp_msg, "pending_review": False, "review_id": None,
                "rule": {"hit": False, "type": "系统繁忙", "score": 0},
                "llm": None, "tier": "rule", "route_reason": "背压减载",
                "cost_units": 0.0}

    # ---- 安全：指令性输入一律不自动放行，也不送进模型 ----
    blocked = sec["risk"] == "high"
    if blocked:
        db.log_security("injection", path="/judge", detail=sec["reason"])

    # ---- 缓存召回：先看有没有判过的同一句 / 近似句 ----
    norm_text = recall.normalize(text)
    cached, cache_kind, cache_sim = None, "", 0.0
    if ENABLE_CACHE and not blocked:
        cached, cache_kind, cache_sim = _cache_lookup(norm_text)

    if cached and not blocked:
        try:
            v = json.loads(cached["verdict"])
        except Exception:
            cached = None
        if cached:
            v = dict(v)
            v.update({"trace_id": trace_id, "text": text, "cached": cache_kind,
                      "cache_similarity": round(cache_sim, 2),
                      "cache_source": cached["source"], "cost_units": 0.0,
                      "pending_review": False, "review_id": None,
                      "backpressure": bp_level, "message": bp_msg,
                      "route_reason": "缓存命中（%s，相似度 %.2f），跳过规则与模型" % (
                          cache_kind, cache_sim)})
            db.add_judge_log(source, text, truth, v.get("rule") or {},
                             v.get("llm"), covered, tier=v.get("tier") or "rule",
                             cost_units=0.0,
                             route_reason="缓存命中（%s %.2f）" % (cache_kind, cache_sim),
                             lat_ms=int((time.time() - t_start) * 1000),
                             trace_id=trace_id)
            _record_event("judge_result", trace_id, {
                "cached": cache_kind, "tier": v.get("tier"), "hit": bool((v.get("rule") or {}).get("hit")),
                "lat_ms": int((time.time() - t_start) * 1000), "cost_units": 0.0,
            })
            return v

    # 需要累计次数才成立的规则（如反复拖延），单句不能判定
    hits = [h for h in DET.match(text) if h[0].get("repeat_threshold", 1) <= 1]
    if hits:
        r, kw = hits[0]
        rule = {"hit": True, "type": r["type"], "rule_id": r["id"],
                "severity": r["severity"], "matched": kw,
                "score": round(min(0.99, 0.6 + 0.1 * min(len(hits), 3)), 2),
                "hint": r.get("hint", "")}
    else:
        rule = {"hit": False, "type": "正常沟通", "rule_id": "", "severity": "",
                "matched": "", "score": 0.0, "hint": ""}
    # ---- 三级分流 ----
    if blocked:
        tier, reason = "rule", "安全策略命中，禁止调用模型，强制人工复核"
    else:
        tier, reason = router.decide(rule, text)
    model = None
    escalated = False
    if tier in ("small", "large") and not blocked:
        budget = resilience.Budget(BUDGET_MS)
        try:
            model, _ms = resilience.call_model(
                tier, lambda: llm.judge(
                    text, tier, timeout=max(0.1, budget.slice(BUDGET_MS))), budget)
        except resilience.Degraded as e:
            db.log_degrade("llm." + tier, "fallback", str(e), resilience.mode())
            model = None
            reason += "（%s，回落规则）" % str(e)
        if model is not None and tier == "small":
            should, why = router.escalate(model)
            if should:
                escalated = True
                try:
                    remaining_ms = max(100, int(budget.remaining() * 1000))
                    model, _ms = resilience.call_model(
                        "large", lambda: llm.judge(
                            text, "large", timeout=max(0.1, remaining_ms / 1000)), budget)
                except resilience.Degraded as e2:
                    db.log_degrade("llm.large", "fallback", str(e2), resilience.mode())
                    reason += "（升级失败：%s）" % str(e2)
                tier, reason = "large", reason + "；升级：" + why
        if model is None:
            # 该层模型没配：回落规则，但把"本应升级"记下来
            reason += "（该层模型未配置，回落规则）"
    # 成本只在真的调了模型时计——没配模型却记成本，指标会虚高
    cost = router.COST_UNITS.get(tier, 0.0) if model is not None else 0.0

    # ---- 能不能自动下结论 ----
    pending, why = False, ""
    if tier == "rule":
        pass                                   # 规则直接定
    elif model is None:
        if not rule["hit"]:
            pending, why = True, "规则未命中且该层模型未配置，无法自动判定"
    else:
        conf = float(model.get("confidence") or 0)
        if model.get("hit") is None:
            pending, why = True, "模型调用异常，交人工"
        elif conf < 0.6:
            pending, why = True, "模型置信 %.2f < 0.6" % conf
        elif bool(model["hit"]) != bool(rule["hit"]):
            pending, why = True, "规则（%s）与模型（%s）结论冲突" % (
                rule["type"] if rule["hit"] else "不介入", model.get("type"))

    if blocked:
        pending = True
        why = (why + "；" if why else "") + "安全策略：" + sec["reason"]

    review_id = None
    if pending:
        review_id = db.add_review(text, truth, rule, tier, model, why, trace_id=trace_id)

    db.add_judge_log(source, text, truth, rule, model, covered,
                     tier=tier, cost_units=cost,
                     route_reason=reason + ("；待人工：" + why if pending else ""),
                     lat_ms=int((time.time() - t_start) * 1000),
                     trace_id=trace_id)

    result = {"trace_id": trace_id, "text": text, "normalized": meta,
              "truth": truth, "covered": covered,
              "rule": rule, "llm": model,
              "tier": tier, "route_reason": reason, "cost_units": cost,
              "pending_review": pending, "review_reason": why, "review_id": review_id,
              "escalated": escalated, "security": sec,
              "backpressure": bp_level, "message": bp_msg,
              "agree": (None if not model or model.get("hit") is None
                        else bool(rule["hit"]) == bool(model["hit"]))}

    # ---- 写缓存：只存"敢复用"的结论 ----
    if ENABLE_CACHE and not pending and not blocked:
        conf = float((model or {}).get("confidence") or 0)
        if tier == "rule":
            _cache_save(text, norm_text, result, "rule", 0.95)
        elif model is not None and conf >= 0.8:
            _cache_save(text, norm_text, result, tier, conf)
    _record_event("judge_result", trace_id, {
        "cached": "", "tier": tier, "hit": bool(rule["hit"]),
        "pending_review": pending, "lat_ms": int((time.time() - t_start) * 1000),
        "cost_units": cost, "security_risk": sec["risk"],
    })
    return result


DEMO_LINES = [
    ("你把那条评论撤掉吧", "approve"),
    ("麻烦拍个证件给我核一下", "approve"),
    ("这件事我们不管了", "reject"),
    ("我们这边原则上不支持", None),
    ("你再这样我们直接不接待", None),
    ("可以给你退，你先把差评删了", None),
]



def load_dotenv(path=None):
    """读同目录 .env（KEY=VALUE 一行一个），只补未设置的环境变量。"""
    p = Path(path) if path else BASE / ".env"
    if not p.exists():
        return 0
    n = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
            n += 1
    return n


def seed_demo():
    """库里没有判定记录时，灌一批演示数据（判定 + 两条裁决）。"""
    conn = db.connect()
    n = conn.execute("SELECT COUNT(*) n FROM judge_logs").fetchone()["n"]
    conn.close()
    if n:
        return 0
    made = 0
    for text, decide in DEMO_LINES:
        res = judge_one(text, "seed")
        made += 1
        if decide and res.get("review_id"):
            db.decide_review(res["review_id"], decide, "演示审核员")
    return made


class Handler(BaseHTTPRequestHandler):
    server_version = "FairnessSentinel/0.2"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, payload, ctype="application/json; charset=utf-8"):
        body = payload if isinstance(payload, bytes) else json.dumps(
            payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 页面每次改完都要立刻生效，别让浏览器拿旧缓存
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
            "form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _admin_allowed(self):
        """管理写接口鉴权。

        配置 ADMIN_TOKEN 时使用 Bearer Token；未配置时只允许本机回环地址，
        避免局域网/公网裸奔。公网部署必须配置 token。
        """
        if ADMIN_TOKEN:
            auth = self.headers.get("Authorization", "")
            expected = "Bearer " + ADMIN_TOKEN
            return hmac.compare_digest(auth, expected)
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False

    def _same_origin(self):
        origin = self.headers.get("Origin", "")
        return security.same_origin(origin, self.headers.get("Host", ""))

    def _text(self, payload, key="text"):
        value = payload.get(key)
        if not isinstance(value, str):
            return None, "字段 %s 必须是字符串" % key
        value = value.strip()
        if not value:
            return None, "字段 %s 不能为空" % key
        if len(value) > security.MAX_INPUT:
            return None, "文本超过 %d 字上限" % security.MAX_INPUT
        return value, ""

    def do_GET(self):
        u = urlparse(self.path)
        ip = self.client_address[0]
        ok, wait = security.READ_LIMITER.allow(ip)
        if not ok:
            db.log_security("rate_limit", ip, u.path, "GET 超限")
            return self._send(429, {"error": "请求过于频繁，请稍后重试",
                                    "retry_after": wait})
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            return self._send(200, (BASE / "static" / "index.html").read_bytes(),
                              "text/html; charset=utf-8")
        if u.path == "/api/scenarios":
            items = [{"id": s["id"], "title": s["title"], "product": s.get("product", ""),
                      "turns": len(s["turns"]), "expect": s.get("expect", {})}
                     for s in SCENARIOS]
            return self._send(200, {"items": items})
        if u.path.startswith("/api/scenario/"):
            sid = u.path.rsplit("/", 1)[-1]
            for s in SCENARIOS:
                if s["id"] == sid:
                    return self._send(200, {"scenario": s, "analysis": CACHE[sid]})
            return self._send(404, {"error": "scenario not found"})
        if u.path == "/api/eval":
            m = run_eval.evaluate()
            if q.get("record"):
                if not self._admin_allowed():
                    return self._send(401, {"error": "记录评测历史需要管理授权"})
                g = run_eval.gate(m)
                db.add_eval_run(m["dev"], m["holdout"], m["holdout2"],
                                m["holdout3"], g["passed"])
            return self._send(200, {k: {kk: vv for kk, vv in m[k].items() if kk != "rows"}
                                    for k in m})
        if u.path == "/api/feedback":
            return self._send(200, db.list_feedback())
        if u.path == "/lab":
            return self._send(200, (BASE / "static" / "lab.html").read_bytes(),
                              "text/html; charset=utf-8")
        if u.path == "/api/sample":
            text, truth, covered = generator.random_line()
            return self._send(200, {"text": text, "truth": truth, "covered": covered})
        if u.path == "/api/review":
            status = q.get("status", ["pending"])[0]
            return self._send(200, db.list_review(status))
        if u.path == "/review":
            return self._send(200, (BASE / "static" / "review.html").read_bytes(),
                              "text/html; charset=utf-8")
        if u.path.startswith("/api/session/"):
            sid = u.path.rsplit("/", 1)[-1]
            return self._send(200, session.get(sid).to_dict())

        if u.path == "/api/slo":
            return self._send(200, slo.compute())

        if u.path == "/api/system/status":
            return self._send(200, {
                "system": resilience.status(),
                "cache": db.cache_stats(),
                "budget_ms": BUDGET_MS,
                "degrade_recent": db.recent_degrade(10),
                "security_recent": db.recent_security(10),
                "pending_review": db.pending_count(),
                "backpressure": backpressure.level(db.pending_count()),
            })

        if u.path == "/api/cache/stats":
            return self._send(200, db.cache_stats())

        if u.path == "/api/llm/status":
            return self._send(200, {"tiers": llm.status(),
                                    "provider_env": os.environ.get("LLM_PROVIDER", "")})
        if u.path == "/healthz":
            try:
                conn = db.connect()
                n = conn.execute("SELECT COUNT(*) n FROM judge_logs").fetchone()["n"]
                pending = conn.execute(
                    "SELECT COUNT(*) n FROM review_queue WHERE status='pending'").fetchone()["n"]
                conn.close()
                return self._send(200, {"ok": True, "readonly": READONLY,
                                        "judge_logs": n, "pending": pending})
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)})
        if u.path == "/api/catalog":
            rules = detector.load_jsonl("rules.jsonl")
            clauses = {c["id"]: c for c in detector.load_jsonl("clauses.jsonl")}
            out = []
            for r in rules:
                cid = r.get("clause") or ""
                c = clauses.get(cid)
                out.append({
                    "id": r["id"], "type": r.get("type", ""),
                    "severity": r.get("severity", ""), "priority": r.get("priority"),
                    "negative": bool(r.get("negative")),
                    "repeat_threshold": r.get("repeat_threshold", 1),
                    "patterns": r.get("patterns", []),
                    "rights": r.get("rights", []), "denials": r.get("denials", []),
                    "action": r.get("action", ""), "hint": r.get("hint", ""),
                    "clause_id": cid,
                    "clause_title": c.get("title", "") if c else "",
                    "clause_text": c.get("text", "") if c else "",
                    "clause_ok": bool(c),
                })
            used = {r.get("clause") for r in rules if r.get("clause")}
            return self._send(200, {
                "rules": out,
                "clause_count": len(clauses),
                "clauses_used": len(used),
                "unused_clauses": sorted(set(clauses) - used),
                "integrity": {
                    "rules_without_clause": [r["id"] for r in rules
                                             if not r.get("negative") and not r.get("clause")],
                    "rules_with_broken_clause": [r["id"] for r in rules
                                                 if r.get("clause") and r["clause"] not in clauses],
                },
            })
        if u.path == "/api/cases":
            return self._send(200, db.list_cases())
        if u.path == "/api/metrics":
            return self._send(200, db.metrics_detail())
        if u.path == "/api/labstats":
            res = db.judge_stats()
            res["llm_ready"] = llm.available()
            return self._send(200, res)
        if u.path == "/api/events/stats":
            return self._send(200, db.event_stats())
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if not self._same_origin():
            db.log_security("origin", self.client_address[0], u.path, "跨站 Origin")
            return self._send(403, {"error": "不允许跨站写入"})
        if READONLY:
            return self._send(403, {"error": "演示环境为只读，写操作已关闭",
                                    "hint": "要体验裁决请在本地运行 demo.py"})
        admin_paths = {"/api/review", "/api/cache/clear", "/api/llm/test"}
        if u.path in admin_paths and not self._admin_allowed():
            db.log_security("unauthorized", self.client_address[0], u.path, "管理接口未授权")
            return self._send(401, {"error": "管理接口未授权",
                                    "hint": "配置 ADMIN_TOKEN 并使用 Bearer Token"})
        ip = self.client_address[0]
        ok, wait = security.WRITE_LIMITER.allow(ip)
        if not ok:
            db.log_security("rate_limit", ip, u.path, "POST 超限")
            return self._send(429, {"error": "请求过于频繁，请稍后重试",
                                    "retry_after": wait})
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, {"error": "Content-Length 非法"})
        if n < 0:
            return self._send(400, {"error": "Content-Length 非法"})
        if n > security.BODY_LIMIT:
            db.log_security("oversize", ip, u.path, f"body {n} 字节")
            return self._send(413, {"error": "请求体过大"})
        raw = self.rfile.read(n) if n else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return self._send(400, {"error": "bad json"})
        if not isinstance(payload, dict):
            return self._send(400, {"error": "JSON 顶层必须是对象"})

        if u.path == "/api/events":
            event = str(payload.get("event") or "")
            if event not in CLIENT_EVENTS:
                return self._send(403, {"error": "该事件只能由服务端写入"})
            try:
                db.add_event(
                    event,
                    str(payload.get("trace_id") or ""),
                    payload.get("properties") or {},
                    session_id=str(payload.get("session_id") or ""),
                    actor_id=str(payload.get("actor_id") or ""),
                    schema_ver=payload.get("schema_version", 1))
            except (ValueError, TypeError) as e:
                return self._send(400, {"error": str(e)})
            return self._send(202, {"ok": True})

        if u.path == "/api/session/judge":
            sid = str(payload.get("session_id") or "s-1")
            role = payload.get("role") or "merchant"
            text, err = self._text(payload)
            if err:
                return self._send(400, {"error": err})
            if role not in ("merchant", "user"):
                return self._send(400, {"error": "role 只能是 merchant/user"})
            sec = security.inspect(text)
            if sec["risk"] == "high":
                db.log_security("injection", ip, u.path, sec["reason"])
                return self._send(422, {"error": "疑似指令注入，已拒绝并记录"})

            sess = session.get(sid)
            sess.order_no = payload.get("order_no", sess.order_no)
            sess.merchant = payload.get("merchant", sess.merchant)

            def matcher(t):
                # 这里故意不过滤 repeat_threshold：单句不成立的要靠会话累计
                return DET.match(t)

            rules_by_id = {r["id"]: r for r in DET.rules} if hasattr(DET, "rules") else {}
            out = session.evaluate(sess, role, text, matcher, rules_by_id)
            session.save(sess)
            trace_id = "session-" + uuid.uuid4().hex[:12]
            db.add_judge_log("session", text, None,
                             {"hit": bool(out["turn_hits"]),
                              "type": out["turn_hits"][0]["type"] if out["turn_hits"] else "正常沟通",
                              "score": 0.8 if out["turn_hits"] else 0},
                             None, tier="session",
                             route_reason="会话累计：" + out["reason"][:60],
                             trace_id=trace_id)
            out["trace_id"] = trace_id
            _record_event("judge_result", trace_id, {
                "tier": "session", "hit": bool(out["turn_hits"]),
                "session_verdict": out["session_verdict"],
            }, session_id=sid)
            return self._send(200, out)

        if u.path == "/api/check":
            # 发送前快检：只走规则 + 会话上下文，不调模型
            import time as _t
            t0 = _t.time()
            text, err = self._text(payload)
            if err:
                return self._send(400, {"error": err})
            sec = security.inspect(text)
            if sec["risk"] == "high":
                db.log_security("injection", ip, u.path, sec["reason"])
                return self._send(422, {"error": "疑似指令注入，已拒绝并记录"})
            sid = payload.get("session_id")
            sess = session.get(sid) if sid else None
            probe = session.expand(text, sess, "merchant")[0] if sess else text
            probe = textnorm.canonical(probe)      # 语序/同义变化也能命中
            hits = [h for h in DET.match(probe) if h[0].get("repeat_threshold", 1) <= 1]
            block = any(h[0]["severity"] == "high" for h in hits)
            return self._send(200, {
                "decision": "block" if block else "allow",
                "hits": [{"type": h[0]["type"], "severity": h[0]["severity"],
                          "kw": h[1]} for h in hits],
                "ms": int((_t.time() - t0) * 1000),
            })

        if u.path == "/api/cache/clear":
            db.cache_clear()
            return self._send(200, {"ok": True, "message": "缓存已清空"})
        if u.path == "/api/llm/test":
            import time as _t
            text, err = self._text(payload)
            if err:
                text = "可以给你退，你先把差评删了"
            sec = security.inspect(text)
            if sec["risk"] == "high":
                db.log_security("injection", ip, u.path, sec["reason"])
                return self._send(422, {"error": "测试文本含指令性内容，已拒绝"})
            out = {"text": text, "tiers": {}}
            for tier in ("small", "large"):
                if not llm.available(tier):
                    out["tiers"][tier] = {"ok": False, "error": "该层未配置"}
                    continue
                t0 = _t.time()
                res = llm.judge(text, tier, timeout=max(0.1, BUDGET_MS / 1000))
                ms = int((_t.time() - t0) * 1000)
                if res is None:
                    out["tiers"][tier] = {"ok": False, "error": "无返回"}
                elif res.get("error"):
                    out["tiers"][tier] = {"ok": False, "error": res["error"], "ms": ms}
                else:
                    out["tiers"][tier] = {"ok": True, "ms": ms, "hit": res.get("hit"),
                                          "type": res.get("type"),
                                          "confidence": res.get("confidence"),
                                          "reason": res.get("reason", ""),
                                          "model": res.get("model", "")}
            return self._send(200, out)

        if u.path == "/api/judge":
            text, err = self._text(payload)
            if err:
                return self._send(400, {"error": err})
            result = judge_one(text, "manual")
            if result.get("skipped") == "shed":
                return self._send(503, result)
            return self._send(200, result)

        if u.path == "/api/batch":
            try:
                n = max(1, min(50, int(payload.get("n", 10))))
            except (TypeError, ValueError):
                return self._send(400, {"error": "n 必须是整数"})
            samples = generator.batch(n)
            out = [judge_one(t, "batch", truth, cov) for t, truth, cov in samples]
            res = db.judge_stats()
            res["llm_ready"] = llm.available()
            res["small_ready"] = llm.available("small")
            res["large_ready"] = llm.available("large")
            res["samples"] = out[:12]
            return self._send(200, res)

        if u.path == "/api/review":
            action = payload.get("action", "")
            preset = payload.get("preset", "refund")
            reviewer = str(payload.get("reviewer") or "").strip()
            if action not in ("approve", "reject", "escalate"):
                return self._send(400, {"error": "action 非法"})
            if preset not in ("refund", "warn", "fp", "escalate"):
                return self._send(400, {"error": "preset 非法"})
            if not reviewer or len(reviewer) > 64:
                return self._send(400, {"error": "审核人不能为空且不超过 64 字"})
            try:
                rid = int(payload.get("id", 0))
            except (TypeError, ValueError):
                return self._send(400, {"error": "id 非法"})
            ok, msg = db.decide_review(rid, action, reviewer,
                                       str(payload.get("note") or ""), preset)
            return self._send(200 if ok else 409, {"ok": ok, "message": msg})

        if u.path == "/api/feedback":
            try:
                turn = int(payload.get("turn", 0))
            except (TypeError, ValueError):
                return self._send(400, {"error": "turn 非法"})
            db.add_feedback(str(payload.get("scenario_id", ""))[:64], turn,
                            str(payload.get("rule_id", ""))[:32],
                            str(payload.get("type", ""))[:64],
                            bool(payload.get("useful")),
                            str(payload.get("note", ""))[:500])
            trace_id = str(payload.get("trace_id") or "")
            if trace_id:
                try:
                    db.add_event("card_feedback", trace_id, {
                        "useful": bool(payload.get("useful")),
                        "rule_id": str(payload.get("rule_id", ""))[:32],
                    }, session_id=str(payload.get("scenario_id", ""))[:64])
                except Exception:
                    pass
            stat = db.list_feedback()["stats"]
            return self._send(200, {"ok": True, "stats": stat})
        return self._send(404, {"error": "not found"})


def main():
    loaded = load_dotenv()
    if loaded:
        print("已从 .env 读取 %d 个配置项" % loaded)
    db.init()
    if os.environ.get("SEED", "1") != "0":
        n = seed_demo()
        if n:
            print("已灌入 %d 条演示判定数据" % n)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print("商客对话监测已启动： http://127.0.0.1:%d" % PORT)
    print("数据库：%s（反馈与评测历史）" % db.DB_PATH)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
