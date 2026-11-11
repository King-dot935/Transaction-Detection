# -*- coding: utf-8 -*-
"""规则库与知识库的一致性自检。

检查四件事：
  1. 每条规则的 clause 是否在知识库里找得到（找不到 = 提示卡只能给泛化说法）
  2. 每条条款是否至少被一条规则引用（没被引用 = 冗余或漏挂）
  3. 同一个触发词是否出现在多条正向规则里（会打架，靠优先级裁决，但要看得见）
  4. 高危规则是否都有依据（risk_level=high 的条款覆盖率）

用法：python check_catalog.py   （不通过时退出码 1，可挂 CI）
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"


def load(name):
    return [json.loads(l) for l in (DATA / name).read_text(encoding="utf-8").splitlines() if l.strip()]


def main():
    rules = load("rules.jsonl")
    clauses = {c["id"]: c for c in load("clauses.jsonl")}
    problems = []

    # 1) 规则 → 条款
    missing = []
    for r in rules:
        cid = r.get("clause") or ""
        if r.get("negative"):
            continue
        if not cid:
            missing.append(r["id"])
        elif cid not in clauses:
            problems.append("规则 %s 引用了不存在的条款 %s" % (r["id"], cid))
    if missing:
        problems.append("以下规则没有挂依据条款：%s" % "、".join(missing))

    # 2) 条款 → 规则
    used = {r.get("clause") for r in rules if r.get("clause")}
    unused = sorted(set(clauses) - used)

    # 3) 触发词冲突
    owners = {}
    for r in rules:
        if r.get("negative"):
            continue
        for p in r.get("patterns", []):
            owners.setdefault(p, []).append(r["id"])
    conflicts = {p: v for p, v in owners.items() if len(set(v)) > 1}

    # 4) 高危规则覆盖率
    high = [r for r in rules if r.get("severity") == "high"]
    high_ok = [r for r in high if r.get("clause") in clauses]

    print("规则 %d 条 · 条款 %d 条" % (len(rules), len(clauses)))
    print("高危规则 %d 条，其中挂上依据的 %d 条" % (len(high), len(high_ok)))
    print("未被任何规则引用的条款：%s" % ("无" if not unused else "、".join(unused)))
    if conflicts:
        print("触发词重复（按优先级裁决，不一定是错）：")
        for p, ids in conflicts.items():
            print("  「%s」→ %s" % (p, "、".join(sorted(set(ids)))))
    else:
        print("触发词无重复")

    if problems:
        print("\n问题：")
        for p in problems:
            print("  - " + p)
        sys.exit(1)
    print("\n一致性检查通过")


if __name__ == "__main__":
    main()
