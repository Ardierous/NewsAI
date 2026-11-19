from pathlib import Path

import pytest

from app.services.style_extended_pdf import build_style_extended_pdf, style_extended_pdf_filename


def test_style_extended_pdf_filename_has_style_date_and_id():
    name = style_extended_pdf_filename("2026-10-20", 12)
    assert name == "digest_style_2026-10-20_id12.pdf"
    assert "style" in name
    assert "2026-10-20" in name
    assert "id12" in name


def test_build_style_extended_pdf_smoke(tmp_path: Path):
    news = {
        "output_position": 1,
        "title": "Тренды сезона",
        "url": "https://example.com/style",
        "essence": "Красный цвет возвращается в моду.",
        "comment": "Проверить российские бренды.",
        "analysis": (
            "Аудитория: Любители моды. "
            "Почему важно: Задаёт тренд. "
            "Польза: Помогает обновить гардероб. "
            "Вред: Импульсивные траты. "
            "Возможные последствия: Рост спроса на аксессуары."
        ),
        "reader_text": "На показе доминировал красный total look. Дизайнеры смешали прозрачные ткани с юбками.",
    }
    out = tmp_path / "test.pdf"
    try:
        build_style_extended_pdf(
            digest_date="2026-10-16",
            digest_id=99,
            news_items=[news] * 5,
            output_path=out,
        )
    except FileNotFoundError:
        pytest.skip("Нет TTF-шрифта с кириллицей для PDF на этой машине")
    assert out.is_file()
    assert out.stat().st_size > 2000
