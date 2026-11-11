# -*- coding: utf-8 -*-
"""熔断器三态演练（不依赖真实模型）。

验证：closed → (3次失败) → open → (等待) → half_open → (成功) → closed
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import resilience


def show(tag):
    s = resilience.BREAKERS["small"].snapshot()
    print(f"  {tag:<28} state={s['state']:<9} fails={s['fails_in_window']}  mode={resilience.mode()}")


def main():
    b = resilience.BREAKERS["small"]
    b.open_seconds = 1.0          # 缩短等待，便于演示
    print("初始：")
    show("start")

    print("\n连续 3 次失败：")
    for i in range(3):
        try:
            resilience.call_model("small", lambda: {"error": "401 Unauthorized"})
        except resilience.Degraded as e:
            print(f"  第{i + 1}次 → 降级：{e}")
        show(f"fail#{i + 1}")

    print("\n熔断后继续调用（应被直接拒绝，不打上游）：")
    t0 = time.time()
    try:
        resilience.call_model("small", lambda: {"hit": True})
    except resilience.Degraded as e:
        print(f"  拒绝：{e}（耗时 {time.time() - t0:.3f}s）")
    show("open-blocked")

    print(f"\n等待 {b.open_seconds}s 进入半开：")
    time.sleep(b.open_seconds + 0.1)
    try:
        out, ms = resilience.call_model("small", lambda: {"hit": False, "confidence": 0.9})
        print(f"  半开探测成功 → {out}（{ms}ms）")
    except resilience.Degraded as e:
        print(f"  半开探测失败：{e}")
    show("half_open-ok")

    print("\n恢复正常：")
    show("recovered")


if __name__ == "__main__":
    main()
