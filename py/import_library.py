# -*- coding: utf-8 -*-
"""规则 / 知识库批量导入。

支持格式：
    结构化（规则）：.csv  .json  .jsonl  .xlsx
    非结构化（条款文本）：.md  .txt  .docx  .xlsx

设计原则：
    · 一律先进暂存区（rules_staging.jsonl / clauses_imported.jsonl），不直接改生产库
    · 逐条校验：必填字段、严重度取值、依据条款是否存在
    · 按归一化文本去重（同一条重复导入不会重复入库）
    · 输出导入报告，告诉你哪条被拒、为什么

用法：
    python import_library.py 文件或目录            # 只解析+校验，写暂存
    python import_library.py 文件 --commit         # 校验通过后并入 rules.jsonl
"""
import csv
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"

STRUCTURED_EXT = {".csv", ".json", ".jsonl", ".xlsx"}
TEXT_EXT = {".md", ".txt", ".docx", ".xlsx"}
VALID_SEVERITY = {"high", "mid", "low", "none"}


# ---------------------------------------------------------------- 工具
def norm(s):
    """归一化用于去重：全半角统一、去标点空白、转小写。"""
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = re.sub(r"[\s\W_]+", "", s, flags=re.U)
    return s.lower()


def fingerprint(text):
    return hashlib.sha1(norm(text).encode("utf-8")).hexdigest()[:16]


ENCODINGS = ("utf-8-sig", "gbk", "utf-16", "latin-1")


def read_text_smart(path):
    """按常见中文编码依次尝试，返回 (文本, 实际编码)。"""
    raw = Path(path).read_bytes()
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def load_jsonl(path, bad=None):
    """逐行解析，坏行跳过并计入 bad（list）。"""
    if not path.exists():
        return []
    out = []
    for i, line in enumerate(read_text_smart(path)[0].splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            if bad is not None:
                bad.append((i, str(e)[:60]))
    return out


# ---------------------------------------------------------------- 抽取
def read_docx(path):
    from docx import Document
    doc = Document(path)
    lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    for t in doc.tables:                      # 表格里的内容也要
        for row in t.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
    return lines


def read_xlsx(path):
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = []
    for r in ws.iter_rows(values_only=True):
        cells = [str(c).strip() if c is not None else "" for c in r]
        if any(cells):
            rows.append(cells)
    wb.close()
    return rows


def read_csv(path):
    text, _enc = read_text_smart(path)
    return [dict(r) for r in csv.DictReader(text.splitlines())]


def extract_units(path):
    """返回 [(kind, payload)]，kind ∈ {'rule','text'}。"""
    ext = path.suffix.lower()

    if ext == ".jsonl":
        items = load_jsonl(path)
        if items and isinstance(items[0], dict) and "type" in items[0]:
            return [("rule", x) for x in items]
        return [("text", str(x)) for x in items]

    if ext == ".json":
        obj = json.loads(path.read_text(encoding="utf-8"))
        items = obj if isinstance(obj, list) else obj.get("rules") or obj.get("items") or [obj]
        if items and isinstance(items[0], dict) and "type" in items[0]:
            return [("rule", x) for x in items]
        return [("text", str(x)) for x in items]

    if ext == ".csv":
        rows = read_csv(path)
        if rows and ("type" in rows[0] or "类型" in rows[0]):
            return [("rule", x) for x in rows]
        return [("text", " | ".join(str(v) for v in r.values() if v)) for r in rows]

    if ext == ".xlsx":
        rows = read_xlsx(path)
        if not rows:
            return []
        head = [h.lower() for h in rows[0]]
        if "type" in head or "类型" in head:
            keys = [h.strip() for h in rows[0]]
            return [("rule", dict(zip(keys, r))) for r in rows[1:]]
        return [("text", " | ".join(c for c in r if c)) for r in rows]

    if ext == ".docx":
        return [("text", line) for line in read_docx(path)]

    if ext in (".md", ".txt"):
        raw = read_text_smart(path)[0]
        blocks, buf = [], []
        in_fence = False
        for line in raw.splitlines():
            # 代码块内部不切分
            if line.strip().startswith("```"):
                in_fence = not in_fence
                buf.append(line)
                continue
            if in_fence:
                buf.append(line)
                continue
            if re.match(r"^#{1,4}\s+", line) or not line.strip():
                if buf:
                    blocks.append("\n".join(buf).strip())
                    buf = []
                if line.strip().startswith("#"):
                    buf.append(line.lstrip("# ").strip())
            else:
                buf.append(line)
        if buf:
            blocks.append("\n".join(buf).strip())
        return [("text", b) for b in blocks if b.strip()]

    return []


# ---------------------------------------------------------------- 校验
def validate_rule(r, clause_ids, seen_ids):
    errs = []
    rid = str(r.get("id") or "").strip()
    rtype = str(r.get("type") or r.get("类型") or "").strip()
    sev = str(r.get("severity") or r.get("严重度") or "").strip().lower()

    pats = r.get("patterns") or r.get("pattern") or r.get("触发词") or ""
    if isinstance(pats, str):
        pats = [p.strip() for p in re.split(r"[|,，;；\n]+", pats) if p.strip()]

    if not rid:
        errs.append("缺 id")
    elif rid in seen_ids:
        errs.append("id 重复：%s" % rid)
    if not rtype:
        errs.append("缺 type")
    if sev not in VALID_SEVERITY:
        errs.append("severity 非法：%r（应为 high/mid/low/none）" % sev)
    if not pats:
        errs.append("缺触发词（patterns）")

    clause = str(r.get("clause") or r.get("依据") or "").strip()
    if clause and clause not in clause_ids:
        errs.append("依据条款不存在：%s" % clause)

    rec = {
        "id": rid, "type": rtype, "severity": sev,
        "patterns": pats,
        "clause": clause or None,
        "priority": int(r.get("priority") or 50),
        "hint": str(r.get("hint") or r.get("建议") or "").strip() or None,
    }
    return rec, errs


# ---------------------------------------------------------------- 主流程
def run(target, commit=False):
    target = Path(target)
    files = [target] if target.is_file() else sorted(
        p for p in target.rglob("*") if p.is_file() and p.suffix.lower() in STRUCTURED_EXT | TEXT_EXT)

    clauses = load_jsonl(DATA / "clauses.jsonl")
    clause_ids = {c["id"] for c in clauses}
    existing_rules = load_jsonl(DATA / "rules.jsonl")
    seen_rule_ids = {r["id"] for r in existing_rules}
    # 暂存区里的也算「已存在」，否则重复导入会反复通过
    staged = load_jsonl(DATA / "rules_staging.jsonl")
    seen_rule_fp = {fingerprint("|".join(r.get("patterns", [])))
                    for r in existing_rules + staged}
    seen_rule_ids |= {r["id"] for r in staged}

    clauses_now = load_jsonl(DATA / "clauses_imported.jsonl")
    seen_clause_fp = {fingerprint(c.get("text") or c.get("title") or "") for c in clauses_now}

    report = {"files": [], "rules_ok": [], "rules_bad": [],
              "clauses_ok": [], "clauses_skip": 0}

    for f in files:
        if not f.exists():
            print("跳过（不存在）：%s" % f)
            continue
        try:
            units = extract_units(f)
        except Exception as e:
            report["files"].append((f.name, "解析失败", str(e)))
            continue

        n_rule = n_text = 0
        for kind, payload in units:
            if kind == "rule":
                rec, errs = validate_rule(payload, clause_ids, seen_rule_ids)
                if errs:
                    report["rules_bad"].append((f.name, rec.get("id") or "?", errs))
                    continue
                if fingerprint("|".join(rec["patterns"])) in seen_rule_fp:
                    report["rules_bad"].append((f.name, rec["id"], ["与已有规则触发词完全重复"]))
                    continue
                seen_rule_ids.add(rec["id"])
                seen_rule_fp.add(fingerprint("|".join(rec["patterns"])))
                report["rules_ok"].append(rec)
                n_rule += 1
            else:
                text = str(payload).strip()
                if len(text) < 8:
                    report["clauses_skip"] += 1
                    continue
                fp = fingerprint(text)
                if fp in seen_clause_fp:
                    report["clauses_skip"] += 1
                    continue
                seen_clause_fp.add(fp)
                title = text.splitlines()[0][:40]
                report["clauses_ok"].append({
                    "id": "IMP_%s" % fp[:8],
                    "title": title,
                    "text": text,
                    "source": f.name,
                    "status": "open",
                })
                n_text += 1
        report["files"].append((f.name, "%d 条规则 · %d 条文本" % (n_rule, n_text), ""))

    # ---- 落暂存 ----
    if report["rules_ok"]:
        out = DATA / "rules_staging.jsonl"
        with open(out, "w", encoding="utf-8") as fh:
            for r in report["rules_ok"]:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print("规则暂存 → %s（%d 条）" % (out.name, len(report["rules_ok"])))

    if report["clauses_ok"]:
        out = DATA / "clauses_imported.jsonl"
        with open(out, "a", encoding="utf-8") as fh:
            for c in report["clauses_ok"]:
                fh.write(json.dumps(c, ensure_ascii=False) + "\n")
        print("条款暂存 → %s（新增 %d 条）" % (out.name, len(report["clauses_ok"])))

    # ---- 并入生产 ----
    if commit and report["rules_ok"]:
        merged = existing_rules + report["rules_ok"]
        (DATA / "rules.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in merged) + "\n", encoding="utf-8")
        print("已并入 rules.jsonl，共 %d 条" % len(merged))
        print("记得跑：python check_catalog.py")

    # ---- 报告 ----
    print_report(report)
    write_report(report)


def print_report(r):
    print("\n" + "=" * 56)
    print("导入结果")
    print("=" * 56)
    for name, info, err in r["files"]:
        print("  %-28s %s %s" % (name, info, ("× " + err) if err else ""))

    print("\n  规则：通过 %d 条 · 拒绝 %d 条" % (len(r["rules_ok"]), len(r["rules_bad"])))
    for fname, rid, errs in r["rules_bad"][:10]:
        print("    ✗ %s [%s] %s" % (fname, rid, "；".join(errs)))

    print("  条款：新增 %d 条 · 重复跳过 %d 条" % (len(r["clauses_ok"]), r["clauses_skip"]))
    if r["clauses_ok"]:
        print("    全部为 open 状态，需在审核管理页人工确认后才会生效")


def write_report(r):
    lines = ["# 导入报告", ""]
    for name, info, err in r["files"]:
        lines.append("- **%s**：%s %s" % (name, info, ("× " + err) if err else ""))
    lines += ["", "## 规则", "",
              "通过 %d 条，拒绝 %d 条。" % (len(r["rules_ok"]), len(r["rules_bad"]))]
    if r["rules_bad"]:
        lines += ["", "| 文件 | ID | 原因 |", "|---|---|---|"]
        for fname, rid, errs in r["rules_bad"]:
            lines.append("| %s | %s | %s |" % (fname, rid, "；".join(errs)))
    lines += ["", "## 条款文本", "",
              "新增 %d 条，重复跳过 %d 条。全部为 open 状态。" % (
                  len(r["clauses_ok"]), r["clauses_skip"])]
    (BASE / "导入报告.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n报告 → 导入报告.md")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit("用法：python import_library.py <文件或目录> [--commit]")
    run(args[0], commit="--commit" in sys.argv)
