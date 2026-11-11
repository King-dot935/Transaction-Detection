# -*- coding: utf-8 -*-
"""兜底层：熔断器 + 时间预算 + 降级模式判定。

设计原则（写进代码注释，避免后来人改坏）：
  1. 只降不升：任何依赖故障只能降低处理级别，不能提升风险（不能因为模型挂了就把可疑样本放行）
  2. 降级要有痕：每次降级都写 degrade_events，事后能复盘
  3. 熔断优先于重试：连续失败就断开，避免把上游打死
  4. 预算优先于完美：单条判定总耗时封顶，超了就降级，不让用户等
  5. 恢复要自动：半开探测，不用人工重启
"""
import json
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent


class Degraded(Exception):
    """依赖不可用，调用方应走降级分支。"""


class Breaker:
    """三态熔断器：closed → open → half_open → closed。"""

    def __init__(self, name, fail_threshold=3, window=60.0, open_seconds=30.0):
        self.name = name
        self.fail_threshold = fail_threshold
        self.window = window
        self.open_seconds = open_seconds
        self.state = "closed"
        self.fails = []          # 失败时间戳
        self.opened_at = 0.0
        self.half_open_probe = False
        self._lock = threading.Lock()

    def allow(self):
        with self._lock:
            now = time.time()
            if self.state == "open":
                if now - self.opened_at >= self.open_seconds:
                    self.state = "half_open"
                    self.half_open_probe = False
                else:
                    return False
            if self.state == "half_open":
                # 半开时只放一个探测请求，避免故障恢复瞬间形成重试风暴。
                if self.half_open_probe:
                    return False
                self.half_open_probe = True
                return True
            if self.state == "closed":
                return True
            return False

    def record_ok(self):
        with self._lock:
            self.fails.clear()
            self.state = "closed"
            self.half_open_probe = False

    def record_fail(self):
        with self._lock:
            now = time.time()
            self.fails = [t for t in self.fails if now - t <= self.window]
            self.fails.append(now)
            if self.state == "half_open" or len(self.fails) >= self.fail_threshold:
                self.state = "open"
                self.opened_at = now
            self.half_open_probe = False

    def snapshot(self):
        with self._lock:
            return {"name": self.name, "state": self.state,
                    "fails_in_window": len(self.fails)}


class Budget:
    """单条判定的时间预算：超了就必须降级，不能让用户干等。"""

    def __init__(self, ms=2500):
        self.deadline = time.time() + ms / 1000.0
        self.ms = ms

    def remaining(self):
        return max(0.0, self.deadline - time.time())

    def expired(self):
        return self.remaining() <= 0

    def slice(self, ms):
        """给某个阶段一段预算，不超过剩余。"""
        return min(ms / 1000.0, self.remaining())


# 全局熔断器（进程内）
BREAKERS = {
    "cache": Breaker("cache"),
    "small": Breaker("llm.small"),
    "large": Breaker("llm.large"),
}


def call_model(tier, fn, budget=None):
    """带熔断与预算的模型调用。失败抛 Degraded，由调用方回落规则。"""
    b = BREAKERS.get(tier) or Breaker(tier)
    if not b.allow():
        raise Degraded(f"{tier} 熔断中")

    if budget is not None and budget.expired():
        raise Degraded("时间预算用尽")

    t0 = time.time()
    try:
        out = fn()
    except Exception as e:
        b.record_fail()
        raise Degraded(f"{tier} 调用异常：{type(e).__name__}") from e

    if out is None:
        b.record_fail()
        raise Degraded(f"{tier} 无返回")

    # llm.judge 会把异常包成 {"error": ...} 返回，这种必须算失败
    if isinstance(out, dict):
        err = out.get("error")
        if err:
            b.record_fail()
            raise Degraded(f"{tier} 返回错误：{str(err)[:40]}")
        if out.get("type") == "解析失败":
            b.record_fail()
            raise Degraded(f"{tier} 输出无法解析")
        if out.get("hit") is None:
            b.record_fail()
            raise Degraded(f"{tier} 返回格式不完整")

    b.record_ok()
    return out, int((time.time() - t0) * 1000)


def mode():
    """整体降级模式：normal / degraded / safe。"""
    states = [b.snapshot()["state"] for b in BREAKERS.values()]
    model_states = [BREAKERS[t].snapshot()["state"] for t in ("small", "large")]
    if all(s == "open" for s in model_states):
        return "safe"          # 两层模型都不可用：只剩规则 + 人工
    if any(s != "closed" for s in states):
        return "degraded"      # 部分能力受损
    return "normal"


def status():
    return {"mode": mode(), "breakers": [b.snapshot() for b in BREAKERS.values()]}
