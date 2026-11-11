# -*- coding: utf-8 -*-
"""生成单文件版演示页：把场景与判定结果内嵌进 HTML，双击即可打开，不需要起服务。

用法：python build_standalone.py
输出：demo_standalone.html
"""
import json
from pathlib import Path

import detector

BASE = Path(__file__).resolve().parent
det = detector.Detector()
scenarios = detector.load_jsonl("scenarios.jsonl")

embedded = {"list": [], "byId": {}}
for s in scenarios:
    analysis = det.analyze(s)
    embedded["list"].append({
        "id": s["id"], "title": s["title"], "product": s.get("product", ""),
        "turns": len(s["turns"]), "expect": s.get("expect", {}),
    })
    embedded["byId"][s["id"]] = {"scenario": s, "analysis": analysis}

html = (BASE / "static" / "index.html").read_text(encoding="utf-8")
payload = json.dumps(embedded, ensure_ascii=False)
inject = "<script>window.__EMBEDDED__ = %s;</script>\n" % payload

# 注入到主脚本之前
marker = "<script>\nlet scenarios"
if marker in html:
    html = html.replace(marker, inject + marker, 1)
else:
    # 兜底：插到 </body> 前
    html = html.replace("</body>", inject + "</body>", 1)

out = BASE / "demo_standalone.html"
out.write_text(html, encoding="utf-8")
print("已生成 %s（%.1f KB），双击即可打开" % (out.name, out.stat().st_size / 1024))
