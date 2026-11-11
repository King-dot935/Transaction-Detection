# -*- coding: utf-8 -*-
"""一条命令跑完整个评测：离线指标 → 缺陷聚类 → 门禁判定。

    python eval_all.py            # 全跑，门禁不过则退出码 1
    python eval_all.py --no-save  # 不写 eval_runs

CI 里直接接：退出码非 0 就拦合入。
"""
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
PY = sys.executable


def run(script, *args):
    print("\n" + "=" * 60)
    print("▶", script, " ".join(args))
    print("=" * 60)
    r = subprocess.run([PY, str(BASE / script), *args], cwd=str(BASE),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr.strip() else "")
    print(out.strip())
    return r.returncode, out


def _gate_rows(stdout_text):
    """从 eval_report.md 里摘门禁表格；没有就退回 stdout 的 GATE 行。"""
    p = BASE / "eval_report.md"
    if p.exists():
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines()
                 if ln.startswith("| 留出集") or ln.startswith("| 误报率(上限)")
                 or "门禁结论" in ln]
        if lines:
            return "\n".join(lines)
    return "\n".join(ln for ln in stdout_text.splitlines() if "GATE" in ln) or "无输出"


def main():
    save = "--no-save" not in sys.argv
    codes = {}

    code, out_eval = run("eval_suite.py", *([] if save else ["--no-save"]))
    codes["eval_suite"] = code

    code, out_cluster = run("badcase_cluster.py")
    codes["badcase_cluster"] = code

    code, out_gate = run("run_eval.py")
    codes["run_eval"] = code

    # 汇总
    gate_ok = "GATE PASS" in out_gate
    print("\n" + "=" * 60)
    print("汇总")
    print("=" * 60)
    for k, v in codes.items():
        print(f"  {k:<18} 退出码 {v}")
    print(f"  门禁              {'通过' if gate_ok else '未通过'}")

    overview = [
        "# 评测总览", "",
        "| 步骤 | 产物 | 状态 |", "|---|---|---|",
        f"| 离线评测 | 评测报告.md | {'OK' if codes['eval_suite'] == 0 else '失败'} |",
        f"| 缺陷聚类 | badcase_clusters.md | {'OK' if codes['badcase_cluster'] == 0 else '失败'} |",
        f"| 回归门禁 | eval_report.md | {'通过' if gate_ok else '未通过'} |",
        "", "## 门禁明细", "", "```",
        _gate_rows(out_gate),
        "```",
    ]
    (BASE / "评测总览.md").write_text("\n".join(overview) + "\n", encoding="utf-8")
    print("\n总览 → 评测总览.md")

    return 0 if (gate_ok and all(v == 0 for v in codes.values())) else 1


if __name__ == "__main__":
    sys.exit(main())
