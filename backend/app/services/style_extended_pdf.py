"""PDF с расширенными описаниями топ-5 для темы «Стиль» (дополнение к короткому MAX)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.services.analytics_sections import build_extended_news_sections, extended_sections_sentence_count
from app.services.platform_assembly import format_digest_date_ru


def style_extended_pdf_filename(digest_date: date | datetime | str, digest_id: int) -> str:
    if isinstance(digest_date, datetime):
        iso = digest_date.date().isoformat()
    elif isinstance(digest_date, date):
        iso = digest_date.isoformat()
    else:
        iso = str(digest_date).strip().split("T")[0]
    return f"style_max_extended_{iso}_id{digest_id}.pdf"


def _resolve_cyrillic_font_path() -> Path:
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "fonts" / "DejaVuSans.ttf",
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path(r"C:\Windows\Fonts\calibri.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(
        "Не найден TTF-шрифт с кириллицей для PDF (ожидался Arial/DejaVu). "
        "Положите DejaVuSans.ttf в backend/app/fonts/."
    )


def _register_pdf_font() -> str:
    font_path = _resolve_cyrillic_font_path()
    name = "ExTellectPdfBody"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(font_path)))
    return name


def _escape_pdf_html(text: str) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def build_style_extended_pdf(
    *,
    digest_date: date | datetime | str,
    digest_id: int,
    news_items: list[dict[str, Any]],
    output_path: Path,
) -> Path:
    """
    news_items: ordered list with keys title, url, essence, comment, analysis, reader_text, output_position.
    """
    font_name = _register_pdf_font()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ExtTitle",
        parent=styles["Heading1"],
        fontName=font_name,
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=8,
    )
    h2_style = ParagraphStyle(
        "ExtH2",
        parent=styles["Heading2"],
        fontName=font_name,
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#1e293b"),
        spaceBefore=10,
        spaceAfter=6,
    )
    h3_style = ParagraphStyle(
        "ExtH3",
        parent=styles["Heading3"],
        fontName=font_name,
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#334155"),
        spaceBefore=6,
        spaceAfter=4,
    )
    body_style = ParagraphStyle(
        "ExtBody",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=10,
        leading=14,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=6,
    )
    meta_style = ParagraphStyle(
        "ExtMeta",
        parent=body_style,
        fontSize=9,
        textColor=colors.HexColor("#475569"),
    )

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="Расширенный дайджест «Стиль»",
    )
    story: list[Any] = []
    date_ru = format_digest_date_ru(digest_date)
    story.append(Paragraph(_escape_pdf_html(f"Стиль: расширенная версия для MAX · {date_ru}"), title_style))
    story.append(
        Paragraph(
            _escape_pdf_html(
                "Короткий текст для публикации в MAX остаётся в интерфейсе копирования. "
                "Этот PDF — подробная редакционная расшифровка топ‑5 (ориентир 20–25 предложений на новость)."
            ),
            meta_style,
        )
    )
    story.append(Spacer(1, 6))

    for item in news_items:
        pos = int(item.get("output_position") or 0)
        title = str(item.get("title") or "Без заголовка").strip()
        url = str(item.get("url") or "").strip()
        sections = build_extended_news_sections(
            title=title,
            essence=str(item.get("essence") or ""),
            comment=str(item.get("comment") or ""),
            analysis=str(item.get("analysis") or ""),
            reader_text=str(item.get("reader_text") or ""),
        )
        story.append(Paragraph(_escape_pdf_html(f"{pos}. {title}"), h2_style))
        if url:
            story.append(Paragraph(_escape_pdf_html(url), meta_style))
        sent_count = extended_sections_sentence_count(sections)
        story.append(
            Paragraph(
                _escape_pdf_html(f"Объём блока: {sent_count} предложений."),
                meta_style,
            )
        )
        for heading, body in sections:
            story.append(Paragraph(_escape_pdf_html(heading), h3_style))
            story.append(Paragraph(_escape_pdf_html(body), body_style))
        story.append(Spacer(1, 8))

    doc.build(story)
    return output_path
