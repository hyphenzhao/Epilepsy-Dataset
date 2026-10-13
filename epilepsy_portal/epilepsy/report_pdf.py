"""Paginated, embedded-font PDF rendering for Markdown clinical reports."""

from functools import lru_cache
import io
from pathlib import Path
from urllib.parse import urlsplit
from xml.sax.saxutils import escape, quoteattr

from lxml import html
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import CondPageBreak, HRFlowable, LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle


FONT_DIR = Path(__file__).with_name("report_fonts")
INK = colors.HexColor("#27364B")
BLUE = colors.HexColor("#22517C")
MUTED = colors.HexColor("#66758A")
LINE = colors.HexColor("#D8E1EB")
PALE = colors.HexColor("#F3F6FA")


@lru_cache(maxsize=1)
def _register_fonts():
    regular_paths = [
        FONT_DIR / "ReportSans-Regular.ttf",
        Path("/usr/share/fonts/truetype/noto/NotoSansSC-Regular.ttf"),
        Path("/usr/share/fonts/truetype/arphic/uming.ttc"),
        Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
        Path("C:/Windows/Fonts/msyh.ttc"),
    ]
    regular = None
    for path in regular_paths:
        if path.is_file():
            candidate = TTFont("ReportSans", str(path))
            if all(ord(character) in candidate.face.charToGlyph for character in "A1中文•"):
                regular = path
                break
    if regular is None:
        raise RuntimeError("PDF 导出需要中文字体，请安装或配置 Noto Sans SC 字体。")
    paths = {
        "ReportSans": regular,
        "ReportSans-Bold": FONT_DIR / "ReportSans-Bold.ttf",
        "ReportSans-Oblique": FONT_DIR / "ReportSans-Oblique.ttf",
        "ReportSans-BoldOblique": FONT_DIR / "ReportSans-BoldOblique.ttf",
    }
    for name, path in paths.items():
        if not path.is_file():
            path = paths["ReportSans-Bold"] if "Bold" in name and paths["ReportSans-Bold"].is_file() else regular
        pdfmetrics.registerFont(TTFont(name, str(path)))
    pdfmetrics.registerFontFamily(
        "ReportSans", normal="ReportSans", bold="ReportSans-Bold",
        italic="ReportSans-Oblique", boldItalic="ReportSans-BoldOblique",
    )


def _styles():
    body = ParagraphStyle(
        "ReportBody", fontName="ReportSans", fontSize=10.5, leading=17,
        textColor=INK, alignment=TA_LEFT, wordWrap="CJK", spaceAfter=7,
        splitLongWords=True, allowWidows=0, allowOrphans=0,
    )
    styles = {"body": body}
    styles["title"] = ParagraphStyle(
        "ReportTitle", parent=body, fontName="ReportSans-Bold", fontSize=20,
        leading=29, textColor=BLUE, spaceAfter=10, keepWithNext=True,
    )
    styles["subtitle"] = ParagraphStyle(
        "ReportSubtitle", parent=body, fontSize=9, leading=14, textColor=MUTED,
        spaceAfter=13, keepWithNext=True,
    )
    for level, size in enumerate((16, 14, 12, 11, 10.5, 10.5), start=1):
        styles[f"h{level}"] = ParagraphStyle(
            f"ReportHeading{level}", parent=body, fontName="ReportSans-Bold",
            fontSize=size, leading=size * 1.5, textColor=BLUE,
            spaceBefore=12 if level <= 2 else 8, spaceAfter=5, keepWithNext=True,
        )
    styles["quote"] = ParagraphStyle(
        "ReportQuote", parent=body, leftIndent=12, rightIndent=8,
        textColor=MUTED, backColor=PALE, borderPadding=7, spaceBefore=4, spaceAfter=9,
    )
    styles["code"] = ParagraphStyle(
        "ReportCode", parent=body, fontSize=9, leading=14, backColor=PALE,
        borderPadding=8, spaceBefore=4, spaceAfter=11,
    )
    styles["cell"] = ParagraphStyle(
        "ReportCell", parent=body, fontSize=9.2, leading=14,
        spaceAfter=0, allowWidows=1, allowOrphans=1,
    )
    styles["header_cell"] = ParagraphStyle(
        "ReportHeaderCell", parent=styles["cell"], fontName="ReportSans-Bold", textColor=BLUE,
    )
    return styles


def _tag(node):
    return node.tag.lower() if isinstance(node.tag, str) else ""


def _link_markup(node, inner):
    target = node.get("href", "")
    if target and urlsplit(target).scheme.lower() in {"http", "https", "mailto"}:
        return f'<link href={quoteattr(target)} color="#22517C"><u>{inner}</u></link>'
    return inner


def _inline(node):
    parts = [escape(node.text or "")]
    for child in node:
        tag = _tag(child)
        if tag in {"script", "style", "iframe", "object", "img", "svg", "ul", "ol"}:
            inner = ""
        else:
            inner = _inline(child)
        if tag in {"strong", "b"}:
            parts.append(f"<b>{inner}</b>")
        elif tag in {"em", "i"}:
            parts.append(f"<i>{inner}</i>")
        elif tag == "br":
            parts.append("<br/>")
        elif tag == "code":
            parts.append(f'<font face="ReportSans" color="#22517C" backColor="#EDF2F7">{inner}</font>')
        elif tag == "a":
            parts.append(_link_markup(child, inner))
        elif tag in {"s", "del"}:
            parts.append(f"<strike>{inner}</strike>")
        elif tag in {"sup", "sub", "u"}:
            parts.append(f"<{tag}>{inner}</{tag}>")
        else:
            parts.append(inner)
        parts.append(escape(child.tail or ""))
    return "".join(parts)


def _table(node, width, styles):
    rows = node.xpath("./thead/tr|./tbody/tr|./tfoot/tr|./tr")
    cell_rows = [row.xpath("./th|./td") for row in rows]
    cell_rows = [cells for cells in cell_rows if cells]
    if not cell_rows:
        return []
    columns = max(len(cells) for cells in cell_rows)
    header_rows = 1 if any(_tag(cell) == "th" for cell in cell_rows[0]) else 0
    weights = []
    for index in range(columns):
        measured = max(
            (pdfmetrics.stringWidth(cells[index].text_content(), "ReportSans", 9.2)
             for cells in cell_rows[:40] if index < len(cells)), default=40,
        )
        weights.append(max(45, min(220, measured + 15)))
    col_widths = [width * weight / sum(weights) for weight in weights]
    data = []
    for row_index, cells in enumerate(cell_rows):
        row = []
        for column in range(columns):
            cell = cells[column] if column < len(cells) else None
            style = styles["header_cell"] if row_index < header_rows else styles["cell"]
            if cell is not None:
                alignment = cell.get("style", "")
                if "text-align: right" in alignment or "text-align:right" in alignment:
                    style = ParagraphStyle(f"{style.name}Right", parent=style, alignment=2)
                elif "text-align: center" in alignment or "text-align:center" in alignment:
                    style = ParagraphStyle(f"{style.name}Center", parent=style, alignment=1)
            row.append(Paragraph(_inline(cell) if cell is not None else "", style))
        data.append(row)
    table = LongTable(
        data, colWidths=col_widths, repeatRows=header_rows,
        splitByRow=1, splitInRow=1, hAlign="LEFT", spaceBefore=5, spaceAfter=11,
    )
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.45, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]
    if header_rows:
        commands.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF0F7")))
    for index in range(header_rows, len(data)):
        if (index - header_rows) % 2 == 1:
            commands.append(("BACKGROUND", (0, index), (-1, index), colors.HexColor("#FAFBFD")))
    table.setStyle(TableStyle(commands))
    return [table]


def _list(node, width, styles, depth=0, quote=False):
    flowables = []
    try:
        start = max(1, int(node.get("start", "1")))
    except ValueError:
        start = 1
    for index, item in enumerate(node.xpath("./li"), start=start):
        markup = escape(item.text or "")
        nested = []
        for child in item:
            if _tag(child) in {"ul", "ol"}:
                nested.append(child)
            else:
                if _tag(child) in {"p", "div"} and markup.strip():
                    markup += "<br/>"
                tag = _tag(child)
                inner = _inline(child)
                if tag in {"strong", "b"}:
                    inner = f"<b>{inner}</b>"
                elif tag in {"em", "i"}:
                    inner = f"<i>{inner}</i>"
                elif tag == "code":
                    inner = f'<font face="ReportSans" color="#22517C">{inner}</font>'
                elif tag == "br":
                    inner = "<br/>"
                elif tag == "a":
                    inner = _link_markup(child, inner)
                elif tag in {"s", "del"}:
                    inner = f"<strike>{inner}</strike>"
                markup += inner
                markup += escape(child.tail or "")
        prefix = f"{index}." if _tag(node) == "ol" else "•"
        style = ParagraphStyle(
            f"ReportList{depth}", parent=styles["body"],
            leftIndent=depth * 16 + 16 + (12 if quote else 0), firstLineIndent=-14,
            spaceAfter=4, textColor=MUTED if quote else INK,
        )
        flowables.append(Paragraph(f"{prefix} {markup}", style))
        for child in nested:
            flowables.extend(_list(child, width, styles, depth=depth + 1, quote=quote))
    flowables.append(Spacer(1, 3))
    return flowables


def _blocks(root, width, styles, quote=False):
    flowables = []
    if (root.text or "").strip():
        flowables.append(Paragraph(escape(root.text), styles["quote"] if quote else styles["body"]))
    nodes = list(root)
    for index, node in enumerate(nodes):
        tag = _tag(node)
        if tag in {"script", "style", "iframe", "object", "img", "svg"}:
            continue
        if tag in {"div", "section", "article", "blockquote"}:
            flowables.extend(_blocks(node, width, styles, quote=quote or tag == "blockquote"))
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            style = styles[tag]
            if index + 1 < len(nodes) and _tag(nodes[index + 1]) == "table":
                # Keep the heading with a table header and first row, while
                # allowing the rest of a long table to fill the current page.
                flowables.append(CondPageBreak(80))
                style = ParagraphStyle(f"{style.name}BeforeTable", parent=style, keepWithNext=False)
            flowables.append(Paragraph(_inline(node), style))
        elif tag in {"ul", "ol"}:
            flowables.extend(_list(node, width, styles, quote=quote))
        elif tag == "table":
            flowables.extend(_table(node, width, styles))
        elif tag == "pre":
            code = escape(node.text_content().rstrip("\n")).expandtabs(4)
            code = code.replace(" ", "&nbsp;").replace("\n", "<br/>")
            flowables.append(Paragraph(code, styles["code"]))
        elif tag == "hr":
            flowables.append(HRFlowable(width="100%", thickness=0.65, color=LINE, spaceBefore=7, spaceAfter=10))
        elif tag:
            flowables.append(Paragraph(_inline(node), styles["quote"] if quote else styles["body"]))
        if (node.tail or "").strip():
            flowables.append(Paragraph(escape(node.tail), styles["body"]))
    return flowables


def build_report_pdf(markdown_text, title="患者报告"):
    from .report_markdown import render_report_markdown

    _register_fonts()
    root = html.fragment_fromstring(render_report_markdown(markdown_text), create_parent="div")
    styles = _styles()
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=19 * mm, bottomMargin=19 * mm, title=title or "患者报告",
        allowSplitting=True,
    )
    first = root[0] if len(root) else None
    if first is not None and _tag(first) == "h1":
        report_title = first.text_content().strip() or title or "患者报告"
        title_markup = _inline(first)
        root.remove(first)
    else:
        report_title = title or "患者报告"
        title_markup = escape(report_title)
    story = [Paragraph(title_markup, styles["title"])]
    if title and title != "患者报告" and title.strip() != report_title:
        story.append(Paragraph(escape(title), styles["subtitle"]))
    story.append(HRFlowable(width="100%", thickness=1, color=LINE, spaceAfter=12))
    story.extend(_blocks(root, document.width, styles))

    def decorate_page(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(doc.leftMargin, 14 * mm, A4[0] - doc.rightMargin, 14 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont("ReportSans", 8)
        canvas.drawRightString(A4[0] - doc.rightMargin, 9 * mm, f"第 {doc.page} 页")
        if doc.page > 1:
            header = report_title
            while len(header) > 1 and pdfmetrics.stringWidth(header, "ReportSans", 8) > doc.width:
                header = header[:-1]
            canvas.drawString(doc.leftMargin, A4[1] - 12 * mm, header)
        canvas.restoreState()

    document.build(story, onFirstPage=decorate_page, onLaterPages=decorate_page)
    buffer.seek(0)
    return buffer
