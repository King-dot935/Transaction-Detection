# -*- coding: utf-8 -*-
"""统一重新截取所有页面截图，替换 assets 里的旧图。

截图口径：
  桌面 1280 宽；移动 390 宽（用 iframe 包裹，因为 headless Chrome 最小视口 500）
  虚拟时间预算：让自动播放/接口请求都跑完再截
"""
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
CHROME = os.environ.get("CHROME") or (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe")
ASSETS = BASE.parent / "github_repo" / "assets"
TMP = BASE / "_shots"
TMP.mkdir(exist_ok=True)
BASE_URL = "http://127.0.0.1:8765"


def shot(url, out, w=1280, h=900, budget=6000):
    subprocess.run([
        CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        f"--user-data-dir={TMP / ('prof' + str(time.time_ns()))}",
        f"--virtual-time-budget={budget}",
        f"--window-size={w},{h}",
        f"--screenshot={out}", url,
    ], capture_output=True)
    p = Path(out)
    return p.exists() and p.stat().st_size > 1000, p.stat().st_size if p.exists() else 0


def mobile(url, out, h=820, budget=6000):
    """用 iframe 包一层，绕过 Chrome 最小视口 500px 的限制。"""
    wrap = TMP / ("m_%d.html" % time.time_ns())
    wrap.write_text(
        "<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
        "body{margin:0;background:#EDEBE6;display:flex;justify-content:center;padding:14px}"
        "iframe{width:390px;height:%dpx;border:2px solid #111;border-radius:16px;background:#fff}"
        "</style></head><body><iframe src='%s'></iframe></body></html>" % (h, url),
        encoding="utf-8")
    ok, size = shot(wrap.as_uri(), out, 440, h + 60, budget)
    wrap.unlink(missing_ok=True)
    return ok, size


def main():
    jobs = [
        ("home.png", lambda: shot(BASE_URL + "/", TMP / "home.png", 1280, 900, 7000)),
        ("live.png", lambda: shot(BASE_URL + "/?mode=live",
                                  TMP / "live.png", 1280, 900, 5000)),
        ("lab.png", lambda: shot(BASE_URL + "/lab", TMP / "lab.png", 1280, 1000, 6000)),
        ("review.png", lambda: shot(BASE_URL + "/review", TMP / "review.png",
                                    1280, 1000, 6000)),
        ("home-mobile.png", lambda: mobile(BASE_URL + "/", TMP / "home-mobile.png")),
        ("mobile.png", lambda: mobile(BASE_URL + "/lab", TMP / "mobile.png")),
    ]

    ASSETS.mkdir(exist_ok=True)
    results = []
    for name, fn in jobs:
        ok, size = fn()
        if ok:
            dst = ASSETS / name
            dst.write_bytes((TMP / name).read_bytes())
            results.append((name, True, size))
            print(f"  ✓ {name:18} {size / 1024:6.1f} KB")
        else:
            results.append((name, False, size))
            print(f"  ✗ {name:18} 截图失败")

    bad = [r for r in results if not r[1]]
    print(f"\n完成 {len(results) - len(bad)}/{len(results)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
