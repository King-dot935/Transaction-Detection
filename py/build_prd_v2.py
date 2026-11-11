# -*- coding: utf-8 -*-
"""把 商客对话监测_PRD_v2.md 转成 .docx（正式文档形态）。

支持：# / ## / ### / ####、表格、无序/有序列表、代码块、引用、分割线、**加粗**、`行内代码`。
"""
import re
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor, Cm

BASE = Path(__file__).resolve().parent
SRC = BASE / "商客对话监测_PRD_v2.md"
OUT = BASE / "商客对话监测_PRD_v2.docx"

INK = RGBColor(0x11, 0x12, 0x14)
MUTED = RGBColor(0x5F, 0x63, 0x67)


def set_cell_bg(cell, hex_color):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def remove_title_border(doc):
    """Remove Office's built-in blue Title bottom border."""
    title = doc.styles["Title"]
    pPr = title.element.get_or_add_pPr()
    for border in pPr.findall(qn("w:pBdr")):
        pPr.remove(border)


def set_repeat_table_header(row):
    trPr = row._tr.get_or_add_trPr()
    tblHeader = OxmlElement("w:tblHeader")
    tblHeader.set(qn("w:val"), "true")
    trPr.append(tblHeader)


def prevent_row_split(row):
    trPr = row._tr.get_or_add_trPr()
    cantSplit = OxmlElement("w:cantSplit")
    trPr.append(cantSplit)


def set_table_borders(table, color="D9D9D9"):
    tblPr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement("w:" + edge)
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:color"), color)
        borders.append(el)
    tblPr.append(borders)


def add_runs(par, text, base_bold=False, size=None, color=None):
    """处理 **加粗** 与 `行内代码`。"""
    parts = re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text)
    for p in parts:
        if not p:
            continue
        if p.startswith("**") and p.endswith("**"):
            r = par.add_run(p[2:-2])
            r.bold = True
        elif p.startswith("`") and p.endswith("`"):
            r = par.add_run(p[1:-1])
            r.font.name = "Consolas"
            r._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
            r.font.color.rgb = RGBColor(0xB0, 0x30, 0x00)
        else:
            r = par.add_run(p)
            r.bold = base_bold
        if size:
            r.font.size = Pt(size)
        if color:
            r.font.color.rgb = color


def style_doc(doc):
    st = doc.styles["Normal"]
    st.font.name = "微软雅黑"
    st.font.size = Pt(10.5)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    title = doc.styles["Title"]
    title.font.name = "微软雅黑"
    title.font.size = Pt(20)
    title.font.bold = True
    title.font.color.rgb = RGBColor(0, 0, 0)
    title.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    remove_title_border(doc)
    for i, (sz, color) in enumerate([(16, INK), (13, INK), (11.5, INK), (10.5, INK)], start=1):
        h = doc.styles["Heading %d" % i]
        h.font.name = "微软雅黑"
        h.font.size = Pt(sz)
        h.font.bold = True
        h.font.color.rgb = color
        h.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")


def parse_table(lines, i):
    """从第 i 行开始读一张表，返回 (rows, next_i)。"""
    rows = []
    while i < len(lines) and lines[i].strip().startswith("|"):
        raw = lines[i].strip().strip("|")
        cells = [c.strip() for c in raw.split("|")]
        if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            rows.append(cells)
        i += 1
    return rows, i


def main():
    md = SRC.read_text(encoding="utf-8").splitlines()
    doc = Document()
    style_doc(doc)
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Cm(2.2)
    sec.top_margin = sec.bottom_margin = Cm(2.0)

    i, n = 0, len(md)
    title_written = False
    while i < n:
        line = md[i]
        s = line.strip()

        if not s:
            i += 1
            continue

        # 分割线
        if re.fullmatch(r"-{3,}", s):
            p = doc.add_paragraph()
            pPr = p._p.get_or_add_pPr()
            pbdr = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            bottom.set(qn("w:val"), "single")
            bottom.set(qn("w:sz"), "6")
            bottom.set(qn("w:color"), "DDD9D0")
            pbdr.append(bottom)
            pPr.append(pbdr)
            i += 1
            continue

        # 代码块
        if s.startswith("```"):
            i += 1
            buf = []
            while i < n and not md[i].strip().startswith("```"):
                buf.append(md[i])
                i += 1
            i += 1
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.4)
            p.paragraph_format.space_before = Pt(4)
            p.paragraph_format.space_after = Pt(6)
            r = p.add_run("\n".join(buf))
            r.font.name = "Consolas"
            r._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
            r.font.size = Pt(8.5)
            r.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
            continue

        # 表格
        if s.startswith("|"):
            rows, i = parse_table(md, i)
            if not rows:
                continue
            cols = max(len(r) for r in rows)
            t = doc.add_table(rows=0, cols=cols)
            t.style = "Table Grid"
            t.alignment = WD_TABLE_ALIGNMENT.CENTER
            set_table_borders(t)
            first_row = None
            for ri, row in enumerate(rows):
                table_row = t.add_row()
                cells = table_row.cells
                prevent_row_split(table_row)
                if ri == 0:
                    first_row = table_row
                    set_repeat_table_header(table_row)
                for ci in range(cols):
                    txt = row[ci] if ci < len(row) else ""
                    cells[ci].text = ""
                    par = cells[ci].paragraphs[0]
                    add_runs(par, txt, base_bold=(ri == 0), size=9)
                    cells[ci].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                    if ri <= 1:
                        par.paragraph_format.keep_with_next = True
                    if ri == 0:
                        set_cell_bg(cells[ci], "F1EFE9")
                    elif ri % 2 == 0:
                        set_cell_bg(cells[ci], "F8F8F7")
            doc.add_paragraph()
            continue

        # 标题
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            level = len(m.group(1))
            if level == 1 and not title_written:
                p = doc.add_paragraph(style="Title")
                title_written = True
            else:
                p = doc.add_heading(level=level)
            add_runs(p, m.group(2))
            p.paragraph_format.keep_with_next = True
            i += 1
            continue

        # 引用
        if s.startswith(">"):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.6)
            r = p.add_run(s.lstrip("> ").strip())
            r.italic = True
            r.font.size = Pt(9)
            r.font.color.rgb = MUTED
            i += 1
            continue

        # 列表
        m = re.match(r"^([*-]|\d+\.)\s+(.*)$", s)
        if m:
            style = "List Bullet" if m.group(1) in ("-", "*") else "List Number"
            p = doc.add_paragraph(style=style)
            add_runs(p, m.group(2))
            p.paragraph_format.space_after = Pt(2)
            i += 1
            continue

        # 普通段落
        p = doc.add_paragraph()
        add_runs(p, s)
        p.paragraph_format.space_after = Pt(4)
        i += 1

    doc.save(OUT)
    print(f"已生成 {OUT.name}（{OUT.stat().st_size / 1024:.0f} KB）")

    # 读回校验
    d2 = Document(OUT)
    heads = [p for p in d2.paragraphs if p.style.name.startswith("Heading")]
    print(f"  段落 {len(d2.paragraphs)} · 表格 {len(d2.tables)} · 标题 {len(heads)}")
    for h in heads[:6]:
        print(f"    {h.style.name}: {h.text[:28]}")


if __name__ == "__main__":
    main()
