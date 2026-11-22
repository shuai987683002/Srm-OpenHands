#!/usr/bin/env python3
"""Build the Word report for the offline SRM LLM deployment plan.

The report is generated from `docs/llm_agent/离线部署实施手册.md` so the .docx
and the Markdown cannot drift: one source, two renderings.  Structure and styles
follow the delivery template (封面 / 目录 / 正文章节 / 表格 / 页眉页脚).

    python3 scripts/build_docx_report.py
    python3 scripts/build_docx_report.py --out output/SRM离线大模型部署报告.docx

Requires python-docx (a build-time tool, not a runtime dependency of srm_open).
Exit codes: 0 written, 66 missing source or dependency.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

try:
    from docx import Document
    from docx.enum.section import WD_SECTION
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor
except ImportError:
    print("FATAL: python-docx is required (pip install python-docx)", file=sys.stderr)
    raise SystemExit(66) from None

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "docs" / "llm_agent" / "离线部署实施手册.md"
DEFAULT_OUT = REPO_ROOT / "docs" / "llm_agent" / "reports" / "SRM领域离线大模型部署方案报告.docx"

TITLE = "离线固体火箭发动机领域 AI 智能体"
SUBTITLE = "本地大模型部署与配置实施报告"
VERSION = "v1.0"

# 中文正文用宋体，标题用黑体；西文用 Times New Roman。这是国内技术报告的常见排版。
FONT_CN_BODY = "宋体"
FONT_CN_HEAD = "黑体"
FONT_EN = "Times New Roman"
ACCENT = RGBColor(0x1F, 0x3B, 0x63)


# --- low-level style helpers -------------------------------------------------


def set_run_font(run, cn: str, en: str, size: float | None = None, bold: bool | None = None):
    run.font.name = en
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    # eastAsia is what actually selects the CJK face; setting font.name alone
    # leaves Word rendering CJK in the default theme font.
    rfonts.set(qn("w:ascii"), en)
    rfonts.set(qn("w:hAnsi"), en)
    rfonts.set(qn("w:eastAsia"), cn)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold


def style_document(doc: Document) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = FONT_EN
    normal.font.size = Pt(10.5)
    normal.paragraph_format.line_spacing = 1.4
    normal.paragraph_format.space_after = Pt(4)
    rpr = normal.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), FONT_EN)
    rfonts.set(qn("w:hAnsi"), FONT_EN)
    rfonts.set(qn("w:eastAsia"), FONT_CN_BODY)

    for name, size in (("Heading 1", 18), ("Heading 2", 14), ("Heading 3", 12)):
        st = doc.styles[name]
        st.font.name = FONT_EN
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = ACCENT
        st.paragraph_format.space_before = Pt(12)
        st.paragraph_format.space_after = Pt(6)
        st.paragraph_format.keep_with_next = True
        rpr = st.element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.append(rfonts)
        rfonts.set(qn("w:ascii"), FONT_EN)
        rfonts.set(qn("w:hAnsi"), FONT_EN)
        rfonts.set(qn("w:eastAsia"), FONT_CN_HEAD)


def add_page_number_footer(section) -> None:
    para = section.footer.paragraphs[0]
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = para.add_run()
    for instr in ("PAGE",):
        fld = OxmlElement("w:fldSimple")
        fld.set(qn("w:instr"), instr)
        run._element.addnext(fld)
    set_run_font(para.runs[0], FONT_CN_BODY, FONT_EN, 9)


def add_header_text(section, text: str) -> None:
    para = section.header.paragraphs[0]
    para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = para.add_run(text)
    set_run_font(run, FONT_CN_BODY, FONT_EN, 9)
    run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)


def shade_cell(cell, hex_fill: str) -> None:
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), hex_fill)
    tcpr.append(shd)


def repeat_table_header(row) -> None:
    trpr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    trpr.append(el)


# --- markdown parsing -------------------------------------------------------

INLINE_RE = re.compile(r"(\*\*.+?\*\*|`.+?`|\*[^*]+?\*)")


def add_inline(para, text: str, size: float = 10.5) -> None:
    """Render **bold**, `code` and *italic* spans inside a paragraph."""
    for chunk in INLINE_RE.split(text):
        if not chunk:
            continue
        if chunk.startswith("**") and chunk.endswith("**"):
            run = para.add_run(chunk[2:-2])
            set_run_font(run, FONT_CN_BODY, FONT_EN, size, bold=True)
        elif chunk.startswith("`") and chunk.endswith("`"):
            run = para.add_run(chunk[1:-1])
            set_run_font(run, "Consolas", "Consolas", size - 0.5)
            run.font.color.rgb = RGBColor(0xA3, 0x1D, 0x1D)
        elif chunk.startswith("*") and chunk.endswith("*") and len(chunk) > 2:
            run = para.add_run(chunk[1:-1])
            set_run_font(run, FONT_CN_BODY, FONT_EN, size)
            run.font.italic = True
        else:
            run = para.add_run(chunk)
            set_run_font(run, FONT_CN_BODY, FONT_EN, size)


def split_table_row(line: str) -> list[str]:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return cells


def is_separator(line: str) -> bool:
    return bool(re.fullmatch(r"\|?[\s:\-|]+\|?", line.strip())) and "-" in line


def parse_blocks(md: str) -> list[tuple[str, object]]:
    """Turn the Markdown into (kind, payload) blocks the writer understands."""
    lines = md.splitlines()
    blocks: list[tuple[str, object]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            i += 1
            code: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            blocks.append(("code", "\n".join(code)))
            continue

        if not stripped:
            i += 1
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            blocks.append(("heading", (len(m.group(1)), m.group(2).strip())))
            i += 1
            continue

        if stripped.startswith("|") and i + 1 < len(lines) and is_separator(lines[i + 1]):
            header = split_table_row(stripped)
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(split_table_row(lines[i]))
                i += 1
            blocks.append(("table", (header, rows)))
            continue

        if stripped.startswith(">"):
            quoted: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quoted.append(lines[i].strip().lstrip(">").strip())
                i += 1
            blocks.append(("quote", " ".join(q for q in quoted if q)))
            continue

        if re.match(r"^[-*]\s+", stripped):
            items: list[str] = []
            while i < len(lines) and re.match(r"^[-*]\s+", lines[i].strip()):
                items.append(re.sub(r"^[-*]\s+", "", lines[i].strip()))
                i += 1
            blocks.append(("bullets", items))
            continue

        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i].strip()):
                items.append(re.sub(r"^\d+\.\s+", "", lines[i].strip()))
                i += 1
            blocks.append(("numbers", items))
            continue

        if stripped == "---":
            blocks.append(("rule", None))
            i += 1
            continue

        # paragraph: gather until a blank line or a new block marker
        para_lines = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if (not nxt or nxt.startswith(("#", "|", ">", "```", "---"))
                    or re.match(r"^[-*]\s+", nxt) or re.match(r"^\d+\.\s+", nxt)):
                break
            para_lines.append(nxt)
            i += 1
        blocks.append(("para", " ".join(para_lines)))
    return blocks


# --- document assembly ------------------------------------------------------


def add_cover(doc: Document) -> None:
    for _ in range(4):
        doc.add_paragraph()

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(TITLE)
    set_run_font(run, FONT_CN_HEAD, FONT_EN, 26, bold=True)
    run.font.color.rgb = ACCENT

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(SUBTITLE)
    set_run_font(run, FONT_CN_HEAD, FONT_EN, 18, bold=True)

    doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("科研教学与非商业研发用途 ｜ 全离线部署 ｜ 数值以 srm_open 引擎为准")
    set_run_font(run, FONT_CN_BODY, FONT_EN, 11)
    run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    for _ in range(8):
        doc.add_paragraph()

    info = doc.add_table(rows=4, cols=2)
    info.style = "Table Grid"
    info.alignment = WD_TABLE_ALIGNMENT.CENTER
    rows = [
        ("文档版本", VERSION),
        ("适用范围", "5–10 名固体火箭发动机设计师与火箭总体设计师"),
        ("编制日期", dt.date.today().isoformat()),
        ("配套资产", "deploy/（脚本与配置）· docs/llm_agent/（主方案与本手册）"),
    ]
    for r, (k, v) in zip(info.rows, rows):
        shade_cell(r.cells[0], "EEF2F7")
        for cell, text, bold in ((r.cells[0], k, True), (r.cells[1], v, False)):
            cell.text = ""
            run = cell.paragraphs[0].add_run(text)
            set_run_font(run, FONT_CN_BODY, FONT_EN, 10.5, bold=bold)

    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def add_toc(doc: Document) -> None:
    p = doc.add_paragraph()
    run = p.add_run("目　录")
    set_run_font(run, FONT_CN_HEAD, FONT_EN, 18, bold=True)
    run.font.color.rgb = ACCENT
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # A real TOC field: Word fills it on open (F9 / print preview).  Writing
    # static page numbers here would be wrong the moment anyone edits the file.
    para = doc.add_paragraph()
    run = para.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = r'TOC \o "1-2" \h \z \u'
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    placeholder = OxmlElement("w:t")
    placeholder.text = "（在 Word 中按 Ctrl+A 然后 F9 更新目录）"
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    for el in (fld_begin, instr, fld_sep, placeholder, fld_end):
        run._element.append(el)

    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def add_code_block(doc: Document, code: str) -> None:
    table = doc.add_table(rows=1, cols=1)
    table.style = "Table Grid"
    cell = table.rows[0].cells[0]
    shade_cell(cell, "F5F5F5")
    cell.text = ""
    para = cell.paragraphs[0]
    para.paragraph_format.line_spacing = 1.0
    para.paragraph_format.space_after = Pt(0)
    for idx, line in enumerate(code.splitlines()):
        if idx:
            para.add_run().add_break()
        run = para.add_run(line)
        set_run_font(run, "Consolas", "Consolas", 8.5)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def add_table(doc: Document, header: list[str], rows: list[list[str]]) -> None:
    table = doc.add_table(rows=1, cols=len(header))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = table.rows[0]
    repeat_table_header(hdr)
    for cell, text in zip(hdr.cells, header):
        shade_cell(cell, "DCE4EF")
        cell.text = ""
        run = cell.paragraphs[0].add_run(text)
        set_run_font(run, FONT_CN_HEAD, FONT_EN, 9.5, bold=True)
    for row in rows:
        cells = table.add_row().cells
        for cell, text in zip(cells, row):
            cell.text = ""
            para = cell.paragraphs[0]
            para.paragraph_format.line_spacing = 1.15
            add_inline(para, text, size=9)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def add_quote(doc: Document, text: str) -> None:
    table = doc.add_table(rows=1, cols=1)
    table.style = "Table Grid"
    cell = table.rows[0].cells[0]
    shade_cell(cell, "FFF8E1")
    cell.text = ""
    add_inline(cell.paragraphs[0], text, size=9.5)


def build(md_path: Path, out_path: Path) -> Path:
    blocks = parse_blocks(md_path.read_text(encoding="utf-8"))

    doc = Document()
    style_document(doc)

    section = doc.sections[0]
    section.page_height, section.page_width = Cm(29.7), Cm(21.0)
    section.top_margin = section.bottom_margin = Cm(2.4)
    section.left_margin = section.right_margin = Cm(2.6)

    add_cover(doc)
    add_toc(doc)

    body_section = doc.add_section(WD_SECTION.NEW_PAGE)
    body_section.page_height, body_section.page_width = Cm(29.7), Cm(21.0)
    body_section.top_margin = body_section.bottom_margin = Cm(2.4)
    body_section.left_margin = body_section.right_margin = Cm(2.6)
    add_header_text(body_section, "离线 SRM 领域 AI 智能体 · 本地大模型部署与配置实施报告")
    add_page_number_footer(body_section)

    heading_count = 0
    for kind, payload in blocks:
        if kind == "heading":
            level, text = payload
            # The document title is on the cover; do not repeat it as H1.
            if level == 1:
                continue
            heading_count += 1
            doc.add_heading(text, level=min(level - 1, 3))
        elif kind == "para":
            para = doc.add_paragraph()
            para.paragraph_format.first_line_indent = Pt(21)
            add_inline(para, payload)
        elif kind == "bullets":
            for item in payload:
                para = doc.add_paragraph(style="List Bullet")
                add_inline(para, item)
        elif kind == "numbers":
            for item in payload:
                para = doc.add_paragraph(style="List Number")
                add_inline(para, item)
        elif kind == "code":
            add_code_block(doc, payload)
        elif kind == "table":
            header, rows = payload
            add_table(doc, header, rows)
        elif kind == "quote":
            add_quote(doc, payload)
        elif kind == "rule":
            doc.add_paragraph()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    print(f"wrote {out_path}")
    print(f"  blocks: {len(blocks)}  headings: {heading_count}")
    return out_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, default=SOURCE)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    if not args.source.is_file():
        print(f"FATAL: source not found: {args.source}", file=sys.stderr)
        return 66
    build(args.source, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
