# -*- coding: utf-8 -*-
"""知识库切块流水线：把一篇规则文档切成条款 JSONL。

流程（对应 切块策略.md 第 2.3 节）：
  ① 清洗：去空行、去页眉页脚式噪声
  ② 按条款号切：第X条 / X.Y
  ③ 抽字段：标题、正文、适用范围、例外
  ④ 生成 id：域_条款号（域由关键词推断）
  ⑤ 超长（>300 字）按 ①②③ 子项拆开
  ⑥ 去重：与已有 clauses.jsonl 文本相同的跳过
  ⑦ 校验：缺 scope / 缺 text 的标记出来，等人补

用法：
  python chunk_doc.py data/sample_rules.md            # 预览，不写盘
  python chunk_doc.py data/sample_rules.md --write    # 写入 data/clauses_imported.jsonl
"""
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
MAX_LEN = 300

# 章节标题（以「第 X 章」或 markdown 标题开头）——用于给条款挂上下文
CHAPTER_RE = re.compile(r"^\s*(?:#+\s*)?第[一二三四五六七八九十百]+章")
# 条款号：第X条 或 数字.数字
ARTICLE_RE = re.compile(r"^\s*(?:#+\s*)?(第[一二三四五六七八九十百]+条|[0-9]+(?:\.[0-9]+)+)")

CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
          "八": 8, "九": 9, "十": 10}

# 域推断：正文命中关键词 → 前缀
DOMAINS = [
    (["退货", "无理由", "签收"], "RETURN"),
    (["换货", "换新"], "EXCHANGE"),
    (["运费", "邮费"], "FREIGHT"),
    (["评价", "好评", "差评"], "EVAL"),
    (["发票", "开票"], "INVOICE"),
    (["隐私", "个人信息", "验证码", "身份证"], "PRIVACY"),
    (["物流", "快递", "配送", "发货"], "DELIVERY"),
    (["价格", "价保"], "PRICE"),
    (["退款"], "REFUND"),
    (["客服", "响应", "服务时效"], "SERVICE"),
    (["交易", "付款", "站外", "微信"], "TRANS"),
    (["优惠券", "券"], "COUPON"),
    (["赠品"], "GIFT"),
]

SUBITEM_RE = re.compile(r"[①②③④⑤⑥⑦⑧⑨]")


def cn2int(s):
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    total = 0
    if "十" in s:
        left, _, right = s.partition("十")
        total = (CN_NUM.get(left, 1) if left else 1) * 10 + CN_NUM.get(right, 0)
    else:
        total = CN_NUM.get(s, 0)
    return total


def infer_domain(text, title=""):
    """标题命中权重 3，正文权重 1；取分数最高的域，平手时按 DOMAINS 顺序。

    先用关键词顺序直接 return 会踩坑：
    「评价的独立性」正文里有「退换货」，会被判成 EXCHANGE，其实是 EVAL。
    """
    best, best_score = "RULE", 0.0
    for kws, prefix in DOMAINS:
        score = 0.0
        last_pos = -1
        for k in kws:
            if k in title:
                score += 3
                last_pos = max(last_pos, title.rfind(k))
            if k in text:
                score += 1
        # 平手时：标题里越靠后命中，越可能是中心词
        score += last_pos / 1000.0
        if score > best_score:
            best, best_score = prefix, score
    return best


def clean_lines(raw):
    out = []
    for ln in raw.splitlines():
        s = ln.strip()
        if not s:
            continue
        if re.fullmatch(r"[-—_=·\s]+", s):        # 分隔线
            continue
        if re.fullmatch(r"\d{1,3}", s):            # 孤立页码
            continue
        out.append(s)
    return out


def split_articles(lines):
    """按条款号切块，返回 [(article_label, chapter, [lines])]。"""
    blocks, cur = [], None
    chapter = ""
    for ln in lines:
        if CHAPTER_RE.match(ln):
            chapter = ln.lstrip("# ").strip()
            continue
        if ARTICLE_RE.match(ln):
            if cur:
                blocks.append(cur)
            cur = {"label": ln, "chapter": chapter, "lines": [ln]}
        elif cur:
            cur["lines"].append(ln)
        # 条款之前的内容丢弃（标题、目录等）
    if cur:
        blocks.append(cur)
    return blocks


def parse_block(b):
    lines = b["lines"]
    head = lines[0]
    m = ARTICLE_RE.match(head)
    label = m.group(1) if m else ""
    title = ARTICLE_RE.sub("", head).strip(" ：:、") or label

    scope, exceptions, body = "", [], []
    for ln in lines[1:]:
        if ln.startswith("适用范围"):
            scope = re.sub(r"^适用范围[：:]\s*", "", ln).strip("。 ")
        elif ln.startswith("例外"):
            exceptions = [x for x in re.split(r"[、，,]", re.sub(r"^例外[：:]\s*", "", ln)) if x]
        else:
            body.append(ln)

    text = "".join(body).strip()
    return {"label": label, "title": title, "text": text,
            "scope": scope, "exceptions": exceptions, "chapter": b["chapter"]}


def split_long(text, limit=MAX_LEN):
    """超长按 ①②③ 子项拆；拆不开就硬切并在末尾补省略号。"""
    if len(text) <= limit:
        return [text]
    if SUBITEM_RE.search(text):
        parts = [p.strip() for p in SUBITEM_RE.split(text) if p.strip()]
        if len(parts) > 1:
            return parts
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def build(doc_path, write=False):
    raw = Path(doc_path).read_text(encoding="utf-8")
    blocks = split_articles(clean_lines(raw))

    existing = []
    ep = DATA / "clauses.jsonl"
    if ep.exists():
        existing = [json.loads(l) for l in ep.read_text(encoding="utf-8").splitlines() if l.strip()]
    used_ids = {c["id"] for c in existing}
    exist_texts = {c["text"].strip() for c in existing}
    counters = {}

    out, issues = [], []
    for b in blocks:
        p = parse_block(b)
        if not p["text"]:
            issues.append("「%s」没有正文，跳过" % p["title"])
            continue
        if p["text"].strip() in exist_texts:
            issues.append("「%s」与已有条款重复，跳过" % p["title"])
            continue

        prefix = infer_domain(p["text"], p["title"])
        # 条款号：第X条 → 数字；X.Y 直接用
        num = ""
        if p["label"].startswith("第") and p["label"].endswith("条"):
            num = str(cn2int(p["label"][1:-1]))
        elif p["label"]:
            num = p["label"]
        key = (prefix, num)

        for i, piece in enumerate(split_long(p["text"])):
            if num:
                cid = "%s_%s%s" % (prefix, num, (".%d" % (i + 1)) if i else "")
            else:
                counters[prefix] = counters.get(prefix, 0) + 1
                cid = "%s_N%d" % (prefix, counters[prefix])
            while cid in used_ids:
                cid += "_2"
            used_ids.add(cid)
            out.append({
                "id": cid,
                "title": p["title"] if i == 0 else "%s（%d）" % (p["title"], i + 1),
                "text": piece,
                "scope": p["scope"] or "",
                "exceptions": p["exceptions"] if i == 0 else [],
                "risk_level": "high" if prefix in ("PRIVACY", "TRANS", "EVAL") else "low",
                "_chapter": p["chapter"],
            })
            if not p["scope"]:
                issues.append("「%s」缺适用范围，需人工补" % p["title"])

    return out, issues


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    doc = sys.argv[1]
    write = "--write" in sys.argv
    items, issues = build(doc, write)

    print("切出 %d 条条款" % len(items))
    for it in items:
        print("  %-14s %s ｜ %d 字 ｜ %s" % (
            it["id"], it["title"][:16], len(it["text"]), it["scope"] or "**缺 scope**"))
    if issues:
        print("\n需要人工处理：")
        for x in issues:
            print("  - " + x)

    if write:
        p = DATA / "clauses_imported.jsonl"
        with p.open("w", encoding="utf-8") as f:
            for it in items:
                it.pop("_chapter", None)
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
        print("\n已写入", p.name)


if __name__ == "__main__":
    main()
