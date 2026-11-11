# -*- coding: utf-8 -*-
"""背压（backpressure）：兜底兜不住时的最后一道闸。

场景：模型挂了 → 全部转人工 → 人工队列积压 → 人工也堵了。
这时继续接新单只会让队列更长、用户等更久。正确做法是**主动减载**。

三级：
  normal   队列 < SOFT       正常受理
  throttle 队列 ≥ SOFT       不确定的一律挂起，并告诉用户预计延迟
  shed     队列 ≥ HARD       直接 503，让用户稍后再试（保护系统不雪崩）
"""
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent

SOFT = int(os.environ.get("BACKLOG_SOFT", "60"))
HARD = int(os.environ.get("BACKLOG_HARD", "150"))


def level(pending):
    if pending >= HARD:
        return "shed"
    if pending >= SOFT:
        return "throttle"
    return "normal"


def check(pending):
    """返回 (level, 用户可读提示)。"""
    lv = level(pending)
    if lv == "shed":
        return lv, ("当前咨询量过大，系统已暂停新判定。"
                    "请 5 分钟后再试，或联系人工客服。")
    if lv == "throttle":
        return lv, ("当前复核量较大，拿不准的样本会转人工，"
                    f"预计处理延迟 {pending // 10 + 1} 小时。")
    return lv, ""
