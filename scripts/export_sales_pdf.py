from __future__ import annotations

import html
import os
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag
from markdown import markdown
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, StyleSheet1, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image as RLImage,
)
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
    XPreformatted,
)

ROOT = Path(__file__).resolve().parents[1]
SALES_DIR = ROOT / "docs" / "sales"
ASSET_DIR = SALES_DIR / "assets"
EXPORT_DIR = SALES_DIR / "export"
SOURCE_TO_TARGET = {
    SALES_DIR / "customer-sales-guide-ko.md": EXPORT_DIR / "customer-sales-guide-ko.pdf",
    SALES_DIR / "internal-sales-enablement-ko.md": EXPORT_DIR / "internal-sales-enablement-ko.pdf",
}

PRIMARY = colors.HexColor("#0F172A")
ACCENT = colors.HexColor("#A66E1D")
SOFT = colors.HexColor("#F6E9CF")
TEAL = colors.HexColor("#0F766E")
RISK = colors.HexColor("#B42318")
INK = "#0F172A"
PAPER = "#FFF9F0"


def _font_candidates() -> tuple[list[Path], list[Path]]:
    env_regular = os.getenv("SALES_PDF_FONT_REGULAR")
    env_bold = os.getenv("SALES_PDF_FONT_BOLD")
    regular = [Path(env_regular)] if env_regular else []
    bold = [Path(env_bold)] if env_bold else []

    regular.extend(
        [
            Path("C:/Windows/Fonts/malgun.ttf"),
            Path("C:/Windows/Fonts/NanumGothic.ttf"),
            Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]
    )
    bold.extend(
        [
            Path("C:/Windows/Fonts/malgunbd.ttf"),
            Path("C:/Windows/Fonts/NanumGothicBold.ttf"),
            Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ]
    )
    return regular, bold


def register_fonts() -> tuple[str, str]:
    regular_candidates, bold_candidates = _font_candidates()
    regular = next((path for path in regular_candidates if path.exists()), None)
    bold = next((path for path in bold_candidates if path.exists()), None)
    if regular is None or bold is None:
        raise FileNotFoundError(
            "Korean PDF font not found. Set SALES_PDF_FONT_REGULAR and SALES_PDF_FONT_BOLD."
        )

    regular_name = "SalesKoRegular"
    bold_name = "SalesKoBold"
    if regular_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(regular_name, str(regular)))
    if bold_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(bold_name, str(bold)))
    return regular_name, bold_name


def build_styles(font_regular: str, font_bold: str) -> StyleSheet1:
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="SalesTitle",
            parent=styles["Title"],
            fontName=font_bold,
            fontSize=24,
            leading=30,
            alignment=TA_CENTER,
            textColor=PRIMARY,
            spaceAfter=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SalesHeading1",
            parent=styles["Heading1"],
            fontName=font_bold,
            fontSize=18,
            leading=24,
            textColor=PRIMARY,
            spaceBefore=14,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SalesHeading2",
            parent=styles["Heading2"],
            fontName=font_bold,
            fontSize=14,
            leading=20,
            textColor=ACCENT,
            spaceBefore=12,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SalesHeading3",
            parent=styles["Heading3"],
            fontName=font_bold,
            fontSize=11.5,
            leading=16,
            textColor=PRIMARY,
            spaceBefore=10,
            spaceAfter=5,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SalesBody",
            parent=styles["BodyText"],
            fontName=font_regular,
            fontSize=10.5,
            leading=15.5,
            textColor=PRIMARY,
            alignment=TA_LEFT,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SalesMeta",
            parent=styles["BodyText"],
            fontName=font_regular,
            fontSize=9.5,
            leading=13,
            textColor=colors.HexColor("#475467"),
            alignment=TA_CENTER,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SalesQuote",
            parent=styles["BodyText"],
            fontName=font_regular,
            fontSize=10,
            leading=15,
            textColor=PRIMARY,
            leftIndent=10,
            borderPadding=10,
            backColor=SOFT,
            borderColor=colors.HexColor("#E4C790"),
            borderWidth=1,
            borderLeftWidth=3,
            spaceBefore=6,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SalesCode",
            parent=styles["Code"],
            fontName="Courier",
            fontSize=8.8,
            leading=12,
            backColor=colors.HexColor("#F4F4F5"),
            borderColor=colors.HexColor("#D0D5DD"),
            borderWidth=0.5,
            borderPadding=8,
            leftIndent=2,
            rightIndent=2,
            spaceBefore=6,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SalesList",
            parent=styles["BodyText"],
            fontName=font_regular,
            fontSize=10.3,
            leading=15,
            leftIndent=6,
            textColor=PRIMARY,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SalesTable",
            parent=styles["BodyText"],
            fontName=font_regular,
            fontSize=9.3,
            leading=13,
            textColor=PRIMARY,
        )
    )
    return styles


def _load_pillow_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    regular_candidates, bold_candidates = _font_candidates()
    target = bold_candidates if bold else regular_candidates
    path = next((item for item in target if item.exists()), None)
    if path is None:
        return ImageFont.load_default()
    return ImageFont.truetype(str(path), size=size)


def _draw_card(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    body: str,
    title_font: ImageFont.FreeTypeFont,
    body_font: ImageFont.FreeTypeFont,
    fill: str,
) -> None:
    draw.rounded_rectangle(box, radius=24, fill=fill, outline="#D9B77A", width=3)
    x1, y1, x2, _ = box
    draw.text((x1 + 24, y1 + 22), title, fill=INK, font=title_font)
    draw.multiline_text((x1 + 24, y1 + 72), body, fill="#334155", font=body_font, spacing=8)
    draw.line((x1 + 24, y1 + 58, x2 - 24, y1 + 58), fill="#D9B77A", width=2)


def generate_assets() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    title_font = _load_pillow_font(40, bold=True)
    card_title = _load_pillow_font(26, bold=True)
    body_font = _load_pillow_font(20)
    small_font = _load_pillow_font(18)

    images = {
        "sales-pillars-ko.png": [
            (
                "세 가지 판매 포인트",
                [
                    ("AI 분석", "다중 에이전트가 신호를 분석하고 요약합니다."),
                    ("리스크 통제", "결정론적 정책이 실제 실행 권한을 가집니다."),
                    ("운영 가시성", "대시보드와 감사 로그로 상태를 추적합니다."),
                ],
            )
        ],
        "product-control-flow-ko.png": [
            (
                "제품 통제 흐름",
                [
                    ("1. Market Snapshot", "모의 시장 데이터를 정규화합니다."),
                    ("2. AI Recommendation", "Trading Decision AI가 구조화된 결정을 냅니다."),
                    ("3. Risk Engine", "리스크 규칙이 최종 허용/차단을 결정합니다."),
                    ("4. Paper Execution", "허용된 경우에만 종이매매 체결을 기록합니다."),
                ],
            )
        ],
        "demo-journey-ko.png": [
            (
                "데모 진행 순서",
                [
                    ("seed", "샘플 데이터를 채워 초기 화면을 준비합니다."),
                    ("dashboard", "Overview에서 모드와 상태를 설명합니다."),
                    ("replay", "리플레이로 의사결정과 체결을 재현합니다."),
                    ("audit", "왜 실행/차단됐는지 타임라인으로 보여줍니다."),
                ],
            )
        ],
        "dashboard-page-map-ko.png": [
            (
                "운영 화면 구성",
                [
                    ("Overview", "모드, 손익, 차단 사유 요약"),
                    ("Decisions / Risk", "추천과 승인 여부 분리"),
                    ("Orders / Executions", "paper fill과 주문 상태"),
                    ("Audit / Settings", "이력 추적과 일시중지 제어"),
                ],
            )
        ],
    }

    for filename, sections in images.items():
        path = ASSET_DIR / filename
        canvas = Image.new("RGB", (1600, 900), PAPER)
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle((36, 36, 1564, 864), radius=34, outline="#D9B77A", width=4, fill="#FFFDF8")
        draw.text((72, 68), sections[0][0], fill=INK, font=title_font)
        draw.text(
            (72, 130),
            "현재 MVP 기준으로 설명되는 판매/데모 자산",
            fill="#475467",
            font=small_font,
        )

        card_y = 210
        for index, (heading, body) in enumerate(sections[0][1]):
            row = index // 2
            col = index % 2
            x = 72 + (col * 736)
            y = card_y + (row * 254)
            _draw_card(
                draw,
                (x, y, x + 660, y + 210),
                heading,
                body,
                card_title,
                body_font,
                "#FFF5E3" if index % 2 == 0 else "#EFF8F6",
            )

        draw.text((72, 830), "문서 버전 v0.1 MVP", fill="#667085", font=small_font)
        canvas.save(path)


def _escape_text(text: str) -> str:
    return html.escape(text).replace("\n", "<br/>")


def _inline_html(node: Tag | NavigableString, font_regular: str, font_bold: str) -> str:
    if isinstance(node, NavigableString):
        return _escape_text(str(node))

    children = "".join(_inline_html(child, font_regular, font_bold) for child in node.children)
    if node.name in {"strong", "b"}:
        return f"<b>{children}</b>"
    if node.name in {"em", "i"}:
        return f"<i>{children}</i>"
    if node.name == "code":
        return f'<font face="Courier">{children}</font>'
    if node.name == "a":
        href = html.escape(node.get("href", ""))
        return f'<link href="{href}">{children}</link>'
    if node.name == "br":
        return "<br/>"
    return children


def _image_flowable(path: Path, max_width: float) -> RLImage:
    image = RLImage(str(path))
    width, height = image.drawWidth, image.drawHeight
    ratio = min(max_width / width, 1.0)
    image.drawWidth = width * ratio
    image.drawHeight = height * ratio
    image.hAlign = "CENTER"
    return image


def _list_flowable(tag: Tag, styles: StyleSheet1, font_regular: str, font_bold: str) -> ListFlowable:
    items: list[ListItem] = []
    for li in tag.find_all("li", recursive=False):
        text = "".join(_inline_html(child, font_regular, font_bold) for child in li.children if not isinstance(child, Tag) or child.name not in {"ul", "ol"})
        paragraph = Paragraph(text or _escape_text(li.get_text(" ", strip=True)), styles["SalesList"])
        items.append(ListItem(paragraph))
    bullet_type = "1" if tag.name == "ol" else "bullet"
    return ListFlowable(items, bulletType=bullet_type, start="1", leftIndent=16)


def _table_flowable(tag: Tag, styles: StyleSheet1, font_regular: str, font_bold: str, max_width: float) -> Table:
    rows: list[list[Paragraph]] = []
    for row in tag.find_all("tr"):
        cells = row.find_all(["th", "td"])
        rendered = [
            Paragraph("".join(_inline_html(child, font_regular, font_bold) for child in cell.children), styles["SalesTable"])
            for cell in cells
        ]
        rows.append(rendered)
    col_count = max(len(row) for row in rows)
    col_width = max_width / max(col_count, 1)
    table = Table(rows, repeatRows=1, colWidths=[col_width] * col_count, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), SOFT),
                ("TEXTCOLOR", (0, 0), (-1, -1), PRIMARY),
                ("FONTNAME", (0, 0), (-1, 0), font_bold),
                ("FONTNAME", (0, 1), (-1, -1), font_regular),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D0D5DD")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _blockquote_flowable(tag: Tag, styles: StyleSheet1, font_regular: str, font_bold: str) -> Paragraph:
    text = "".join(_inline_html(child, font_regular, font_bold) for child in tag.children)
    return Paragraph(text, styles["SalesQuote"])


def _render_html_elements(
    html_fragment: str,
    styles: StyleSheet1,
    font_regular: str,
    font_bold: str,
    source_dir: Path,
    max_width: float,
) -> tuple[str, list]:
    soup = BeautifulSoup(html_fragment, "html.parser")
    title = "Sales PDF"
    story: list = []

    for node in soup.children:
        if isinstance(node, NavigableString):
            continue
        if node.name == "h1":
            title = node.get_text(" ", strip=True)
            story.append(Paragraph(_escape_text(title), styles["SalesTitle"]))
        elif node.name == "h2":
            story.append(Paragraph(_escape_text(node.get_text(" ", strip=True)), styles["SalesHeading1"]))
        elif node.name == "h3":
            story.append(Paragraph(_escape_text(node.get_text(" ", strip=True)), styles["SalesHeading2"]))
        elif node.name == "h4":
            story.append(Paragraph(_escape_text(node.get_text(" ", strip=True)), styles["SalesHeading3"]))
        elif node.name == "p":
            if node.get_text(" ", strip=True) == ":::pagebreak":
                story.append(PageBreak())
                continue

            images = node.find_all("img", recursive=False)
            if images:
                for image_tag in images:
                    source = image_tag.get("src", "")
                    image_path = (source_dir / source).resolve()
                    if image_path.exists():
                        story.append(_image_flowable(image_path, max_width))
                        alt = image_tag.get("alt", "")
                        if alt:
                            story.append(Spacer(1, 4))
                            story.append(Paragraph(_escape_text(alt), styles["SalesMeta"]))
                        story.append(Spacer(1, 10))
                continue

            text = "".join(_inline_html(child, font_regular, font_bold) for child in node.children)
            story.append(Paragraph(text, styles["SalesBody"]))
        elif node.name in {"ul", "ol"}:
            story.append(_list_flowable(node, styles, font_regular, font_bold))
            story.append(Spacer(1, 6))
        elif node.name == "blockquote":
            story.append(_blockquote_flowable(node, styles, font_regular, font_bold))
        elif node.name == "pre":
            story.append(XPreformatted(node.get_text(), styles["SalesCode"]))
        elif node.name == "table":
            story.append(_table_flowable(node, styles, font_regular, font_bold, max_width))
            story.append(Spacer(1, 8))
        elif node.name == "hr":
            story.append(Spacer(1, 6))

    return title, story


def _page_decorator(font_regular: str, title: str):
    def _decorate(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont(font_regular, 8.5)
        canvas.setFillColor(colors.HexColor("#667085"))
        canvas.drawString(doc.leftMargin, 11 * mm, title)
        canvas.drawRightString(doc.pagesize[0] - doc.rightMargin, 11 * mm, f"{canvas.getPageNumber()}")
        canvas.restoreState()

    return _decorate


def export_pdf(source: Path, target: Path, styles: StyleSheet1, font_regular: str, font_bold: str) -> None:
    raw_markdown = source.read_text(encoding="utf-8")
    html_body = markdown(
        raw_markdown,
        extensions=["extra", "fenced_code", "tables", "sane_lists"],
        output_format="html5",
    )

    doc = SimpleDocTemplate(
        str(target),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=source.stem,
        author="Codex",
    )
    title, story = _render_html_elements(
        html_body,
        styles,
        font_regular,
        font_bold,
        source.parent,
        doc.width,
    )
    decorator = _page_decorator(font_regular, title)
    doc.build(story, onFirstPage=decorator, onLaterPages=decorator)


def main() -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    generate_assets()
    font_regular, font_bold = register_fonts()
    styles = build_styles(font_regular, font_bold)

    for source, target in SOURCE_TO_TARGET.items():
        export_pdf(source, target, styles, font_regular, font_bold)

    print(f"Generated {len(SOURCE_TO_TARGET)} PDF files on {date.today().isoformat()}")
    for path in SOURCE_TO_TARGET.values():
        print(path)


if __name__ == "__main__":
    main()
