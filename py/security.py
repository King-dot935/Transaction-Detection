# -*- coding: utf-8 -*-
"""安全防护：输入检查、限流、脱敏。

这个 demo 没有登录体系，所以防护重点不是"谁能进来"，而是：
  1. 用户能输入的内容，能不能操纵判定结果（提示注入）
  2. 能不能把服务打挂（大包 / 高频）
  3. 敏感信息会不会被写进日志或报表
"""
import re
import time
import unicodedata
from collections import deque
from urllib.parse import urlparse

MAX_INPUT = 2000          # 硬上限，超过直接拒绝
BODY_LIMIT = 64 * 1024

# 提示注入：让模型"改变身份 / 忽略规则 / 直接给定论"的说法
INJECTION_PATTERNS = [
    r"忽略(以上|前面|之前|上面|所有)",
    r"(无视|跳过|绕过)[^。]{0,6}(规则|限制|指令|判断)",
    r"你(现在)?是[^。]{0,8}(助手|模型|AI|系统)",
    r"(system|assistant|user)\s*[:：]",
    r"(直接|必须)(返回|输出|回答|判定)",
    r"(输出|返回)[^。]{0,6}(json|格式|true|false)",
    r"(不要|别)(再)?(判断|分析|检查)",
    r"(override|ignore (all|previous)|jailbreak|DAN)",
    r"<script|javascript:|onerror=",
]

# 结构异常：控制字符、超长重复
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def inspect(text):
    """返回 {risk, hits, reason}。risk ∈ none / low / high。"""
    s = str(text or "")
    hits = []

    if len(s) > MAX_INPUT:
        return {"risk": "high", "hits": ["超长输入"], "reason": f"长度 {len(s)} 超过 {MAX_INPUT}"}

    if CTRL_RE.search(s):
        hits.append("控制字符")

    low = unicodedata.normalize("NFKC", s).lower()
    for pat in INJECTION_PATTERNS:
        if re.search(pat, low, flags=re.I):
            hits.append("指令性内容")

    if hits:
        return {"risk": "high", "hits": sorted(set(hits)),
                "reason": "输入含" + "、".join(sorted(set(hits))) + "，可能是提示注入"}
    return {"risk": "none", "hits": [], "reason": ""}


# ---------------------------------------------------------------- 限流
class RateLimiter:
    """滑动窗口限流：每个 key 在 window 秒内最多 limit 次。"""

    def __init__(self, limit=30, window=60.0):
        self.limit = limit
        self.window = window
        self.hits = {}
        self.last_prune = time.time()

    def allow(self, key):
        now = time.time()
        # 限制字典规模，避免伪造大量 key 造成内存持续增长。
        if now - self.last_prune > self.window:
            for k, q in list(self.hits.items()):
                while q and now - q[0] > self.window:
                    q.popleft()
                if not q:
                    self.hits.pop(k, None)
            self.last_prune = now
        q = self.hits.setdefault(key, deque())
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False, int(self.window - (now - q[0]))
        q.append(now)
        return True, 0


WRITE_LIMITER = RateLimiter(limit=20, window=60.0)    # POST（要花模型钱）
READ_LIMITER = RateLimiter(limit=120, window=60.0)    # GET


def same_origin(origin, host):
    """POST 防跨站：有 Origin 时必须与请求 Host 同源。

    本项目不使用 Cookie 鉴权，因此这里主要拦截浏览器跨站写入；
    管理端仍应配置 ADMIN_TOKEN。
    """
    if not origin:
        return True
    try:
        got = urlparse(origin)
        hostname = (got.hostname or "").lower()
        req_host = str(host or "").split(":")[0].lower()
        return got.scheme in ("http", "https") and hostname == req_host
    except Exception:
        return False


# ---------------------------------------------------------------- 脱敏
MASK = [
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "1**********"),
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "*****************"),
    (re.compile(r"(?<!\d)\d{16,19}(?!\d)"), "****"),
    (re.compile(r"sk-[A-Za-z0-9]{8,}"), "sk-***"),
]


def redact(text):
    """写日志/报表前过一遍，避免把手机号、key 存下来。"""
    s = str(text or "")
    for pat, repl in MASK:
        s = pat.sub(repl, s)
    return s
