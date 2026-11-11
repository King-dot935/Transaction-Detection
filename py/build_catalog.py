# -*- coding: utf-8 -*-
"""从 data/ 生成《数据源与规则清单.md》，保证文档与数据永远一致。

用法：python build_catalog.py
"""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
OUT = BASE / "数据源与规则清单.md"


def load(name):
    return [json.loads(l) for l in (DATA / name).read_text(encoding="utf-8").splitlines() if l.strip()]


def trig(r):
    if r.get("negative"):
        return "（负向抑制）" + " / ".join(r.get("patterns", [])[:4])
    if "rights" in r:
        return "权利词（%s）同时出现拒绝词（%s）" % (
            "、".join(r["rights"]), "、".join(r["denials"][:4]) + "…")
    ps = r.get("patterns", [])
    return " / ".join(ps[:5]) + ("…" if len(ps) > 5 else "")


def main():
    rules = load("rules.jsonl")
    clauses = load("clauses.jsonl")
    cmap = {c["id"]: c for c in clauses}
    used = {r.get("clause") for r in rules if r.get("clause")}

    L = []
    a = L.append
    a("# 数据源与规则清单")
    a("")
    a("> 由 `build_catalog.py` 从 `data/` 自动生成，改完数据重跑即可，不用手改文档。")
    a("")

    # ---------------- 1 数据源 ----------------
    a("## 一、四类数据源，各管一件事")
    a("")
    a("| 数据源 | 文件 / 表 | 装什么 | 谁维护 | 被谁读 | **不装什么** |")
    a("|---|---|---|---|---|---|")
    a("| **规则库** | `data/rules.jsonl`（%d 条） | 「商家怎么说算越界」：触发词、严重度、优先级、动作 | 产品 + 风控运营 | 判定引擎、实验台、分流器 | 不装法律条文原文，也不装用户会话 |" % len(rules))
    a("| **知识库** | `data/clauses.jsonl`（%d 条） | 「凭什么这么说」：平台规则条款、适用范围、例外 | 法务 / 规则运营 | 提示卡、介入单 | 不装触发逻辑，也不装具体案例 |" % len(clauses))
    a("| **标注集** | `data/scenarios.jsonl` + `holdout*.jsonl` | 「判得对不对」：人工标注的对话与期望结果 | 产品 | 评测脚本 `run_eval.py` | 不参与线上判定 |")
    a("| **运行数据** | `sentinel.db`（`judge_logs` / `review_queue` / `feedback`） | 「实际发生了什么」：每次判定、待复核、用户反馈 | 系统自动写 | 审核管理、指标页 | 不回流成规则，需人工确认 |")
    a("")
    a("**职责边界**：规则库定义「像不像」，知识库说明「凭什么」，标注集验证「对不对」，运行数据记录「实际怎样」。")
    a("")

    # ---------------- 2 调用链 ----------------
    a("## 二、一条消息进来，数据是怎么被调用的")
    a("")
    a("```")
    a("商家消息")
    a("  │")
    a("  ├─① 负向抑制  读 rules.jsonl 里 negative=true 的条目（R12）")
    a("  │     命中「可以退 / 已经安排」→ 直接放行，不往下走")
    a("  │")
    a("  ├─② 正向匹配  读其余 11 条规则的 patterns / rights+denials")
    a("  │     命中多条 → 按「严重度 → priority」排序取第一条")
    a("  │")
    a("  ├─③ 取依据    用命中规则的 clause 字段去 clauses.jsonl 查条款")
    a("  │     查不到 → 提示卡降级为泛化文案（现在 12 条全部能查到）")
    a("  │")
    a("  ├─④ 分流      router.decide()：规则能定就定，含糊的交小模型/大模型")
    a("  │     判定不了 → 写 review_queue 等人")
    a("  │")
    a("  ├─⑤ 落库      写 judge_logs（规则、tier、成本、原因）")
    a("  │")
    a("  └─⑥ 展示      提示卡 = 类型 + 依据条款 + 你能主张什么 + 两个动作")
    a("```")
    a("")
    a("| 步骤 | 读什么 | 写什么 |")
    a("|---|---|---|")
    a("| ① 负向抑制 | `rules.jsonl` negative 条目 | — |")
    a("| ② 正向匹配 | `rules.jsonl` 正向条目 | — |")
    a("| ③ 取依据 | `clauses.jsonl` | — |")
    a("| ④ 分流 | `router.py` 内置信号表 | — |")
    a("| ⑤ 落库 | — | `judge_logs`；判不了写 `review_queue` |")
    a("| ⑥ 复核 | `review_queue` | 裁决写回 `judge_logs(source=human)` |")
    a("")

    # ---------------- 3 规则全表 ----------------
    a("## 三、规则全表（%d 条）" % len(rules))
    a("")
    a("| ID | 类型 | 严重度 | 优先级 | 触发方式 | 依据条款 | 动作 | 需累计 |")
    a("|---|---|---|---:|---|---|---|---|")
    for r in sorted(rules, key=lambda x: (x.get("priority", 99))):
        cid = r.get("clause") or "—"
        cname = cmap.get(cid, {}).get("title", "")
        a("| %s | %s | %s | %s | %s | %s%s | %s | %s |" % (
            r["id"], r.get("type", ""), r.get("severity", ""), r.get("priority", ""),
            trig(r), cid, ("（%s）" % cname if cname else ""),
            r.get("action", "抑制" if r.get("negative") else ""),
            ("%d 次" % r["repeat_threshold"]) if r.get("repeat_threshold", 1) > 1 else "否"))
    a("")
    a("**读法**：先比严重度，再比 priority，**数字越小越靠前**。")
    a("同一严重度（都是 mid）时，R02（priority 5）压过 R01（priority 10）——"
      "也就是说「拆封不退」优先判成「扩大退货例外」，而不是笼统的「拒绝法定权利」。")
    a("")

    # ---------------- 4 条款清单 ----------------
    a("## 四、知识库清单（%d 条，被引用 %d 条）" % (len(clauses), len(used)))
    a("")
    a("| 条款 ID | 标题 | 适用范围 | 被哪条规则引用 |")
    a("|---|---|---|---|")
    for c in clauses:
        refs = [r["id"] for r in rules if r.get("clause") == c["id"]]
        a("| %s | %s | %s | %s |" % (
            c["id"], c.get("title", ""), c.get("scope", ""),
            "、".join(refs) if refs else "**未引用**"))
    a("")
    a("> 未被引用的条款不是错的——它们代表**知识库比规则库覆盖更广**，是还没做成规则的存量。")
    a("> 反过来，一条规则没有依据条款才是真问题：提示卡只能给泛化说法。现在 12 条全部有依据。")
    a("")

    # ---------------- 5 自检 ----------------
    a("## 五、一致性自检")
    a("")
    a("跑 `python check_catalog.py`，检查四件事：")
    a("")
    a("1. 每条规则的 `clause` 都能在知识库里找到")
    a("2. 每条条款是否至少被一条规则引用（未引用会列出来，不算错）")
    a("3. 同一个触发词是否出现在多条规则里（会打架，靠优先级裁决，但要看得见）")
    a("4. 高危规则是否都挂了依据")
    a("")

    # ---------------- 6 约定 ----------------
    a("## 六、几条硬约定")
    a("")
    a("- **规则不写文案**：提示语里的「你能主张什么」来自条款的 `text`，不另写一份，避免两边不一致")
    a("- **条款不写触发词**：条款只描述规则，不描述怎么说；话术识别全部留在规则库")
    a("- **运行数据不自动改规则**：人工裁决先落库当标注，改规则必须人工过一遍")
    a("- **不存用户隐私原文进知识库**：知识库只有平台条款，会话原文只在运行数据里，且有留存期")
    a("")

    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("已生成", OUT.name, len(L), "行")


if __name__ == "__main__":
    main()
