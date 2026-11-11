# -*- coding: utf-8 -*-
"""把演示库重置成"干净可展示"的状态（进程内直接写库，不依赖服务、不删文件）。

不用删文件的原因：Windows 下 db 文件常被其他进程占用，unlink 会失败；
清表效果一样，还能保留 orders/merchants 这些基础数据。
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import db  # noqa: E402

LINES = [
    "特价商品不退不换，这是店铺规定",
    "可以给你退，你先把差评删了",
    "加我微信，转账给我我给你便宜 20",
    "你把身份证拍给我核一下",
    "我们这边一律不开票，要票加八个点",
    "再等等吧，过几天再说",
    "签收之后出问题我们不管啊",
    "好评返现 5 元，截图发我",
    "拆封了就不能退，公司规定",
    "保价不关我们事，降价不退差价",
    "你投诉也没用，爱要不要",
    "你先删差评，我马上给你退款",
    "这个我们处理不了，你找平台",
    "麻烦把订单号发我查一下",
    "48 小时内发出，走顺丰",
    "收到后不满意可以七天无理由",
    "质量有问题我们承担来回运费",
    "麻烦拍个证件给我核实一下",
    "你把那条评论撤掉吧",
    "按规定我们这边一律没有票",
    "实话跟您说，我们这边不含税",
    "这个我帮您问问，您先别急",
    "您先删了评价，我马上安排",
    "签收之后就不好说了",
    "我们只按页面写的规定来",
    "麻烦把身份证拍给我核实",
    "你再加点钱我给您换个新的",
    "退可以，运费您自己出",
    "这个我们处理不了，您自己看着办",
    "你再这样我们直接不接待了",
    "页面上的条款都写清楚了",
    "你先删了差评我这边就处理",
    "这个价我们是不含票的",
    "按公司规定就是不行",
    "您先签收，有问题再说",
    "这个不算质量问题",
    "退货运费您自己承担",
    "我们这边原则上不支持的",
    "税点这边我们承担不了",
    "好评这块您方便的话帮个忙",
    "评价那边您看着办吧",
    "这个删不删您自己决定",
    "红包我这边申请不下来",
    "证件我们不需要了",
]

WIPE = ["judge_logs", "review_queue", "cases", "notifications",
        "decision_cache", "cache_band", "sessions", "feedback",
        "degrade_events", "security_events", "rule_candidates", "eval_runs"]


def main():
    conn = db.connect()
    with conn:
        for t in WIPE:
            try:
                conn.execute("DELETE FROM " + t)
            except Exception:
                pass
        conn.execute("UPDATE merchants SET violations=12 WHERE id='m-2001'")
        conn.execute("UPDATE orders SET status='shipped', refund_status='none', frozen=0")
    conn.close()
    print("已清表")

    import demo
    made = 0
    for t in LINES:
        res = demo.judge_one(t, "seed")
        made += 1
    print(f"灌入 {made} 条判定")

    conn = db.connect()
    pend = [dict(r) for r in conn.execute(
        "SELECT id FROM review_queue WHERE status='pending' ORDER BY id LIMIT 40")]
    decided = 0
    for r in pend[:2]:                      # 造两条已裁决，让「裁决之后」有内容
        db.decide_review(r["id"], "approve", "袁逍堃", "确认属拒绝退货")
        decided += 1
    for r in pend[2:4]:
        db.decide_review(r["id"], "reject", "袁逍堃", "属正常沟通，误报")
        decided += 1

    conn = db.connect()
    for t in ("judge_logs", "review_queue", "cases", "decision_cache", "sessions"):
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {n}")
    bad = conn.execute(
        "SELECT COUNT(*) FROM review_queue WHERE text LIKE '%script%'"
        " OR text LIKE '%忽略以上%' OR text LIKE '%测试%'").fetchone()[0]
    print(f"  测试垃圾残留: {bad}")
    conn.close()


if __name__ == "__main__":
    main()
