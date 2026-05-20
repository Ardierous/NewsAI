import re

from app.services.platform_assembly import (
    DZEN_POST_MAX_CHARS,
    MAX_NEWS_SEP,
    assemble_dzen,
    assemble_max,
    assemble_platform_outputs,
    assemble_telegram,
    assemble_vk,
    compress_paragraphs,
    digest_docx_filename,
    format_digest_date_ru,
    subscription_md_inline,
)


def _sample_payload() -> dict:
    news = {
        "title": "OpenAI перестраивается вокруг ИИ-агентов",
        "url": "https://3dnews.ru/1141822/openai-agents",
        "source": "3DNews",
        "essence": "Компания объединяет ChatGPT и Codex.",
        "comment": "Это меняет стратегию перед IPO.",
        "summary_short": "Компания объединяет ChatGPT и Codex. Это меняет стратегию перед IPO.",
        "summary": "Компания объединяет ChatGPT и Codex. " + ("Длинный анализ. " * 80),
    }
    return {
        "date": "2026-05-16",
        "overall_analysis": "Выпуск показывает ускорение прикладного ИИ и усиление конкуренции за вычисления.",
        "hashtags": ["#ИИ", "#AI", "#ExTellect"],
        "selected_news": [news, news, news, news, news],
    }


def test_format_digest_date_ru():
    assert format_digest_date_ru("2026-05-16") == "16 мая 2026"


def test_compress_paragraphs_no_triple_blank():
    raw = "a\n\n\n\nb"
    assert "\n\n\n" not in compress_paragraphs(raw)


def test_telegram_no_sep_inline_subscription_short_lead():
    payload = _sample_payload()
    text = assemble_telegram(payload)
    assert "———" not in text
    assert "16 мая 2026" in text
    assert "• [Telegram]" in text or "• [ВКонтакте]" in text
    assert subscription_md_inline() in text
    assert payload["overall_analysis"] not in text
    assert "Коротко: главные сдвиги" in text
    assert re.search(r"➤ \[OpenAI[^\]]+\]\(https://3dnews\.ru/[^\)]+\)\nКомпания объединяет", text)


def test_telegram_hashtags_separated_from_subscription():
    text = assemble_telegram(_sample_payload())
    sub_end = text.index("boosty.to/extellect")
    tags_start = text.index("#ИИ", sub_end)
    between = text[sub_end:tags_start]
    assert "\n\n" in between


def test_max_title_link_only_no_duplicate_url():
    text = assemble_max(_sample_payload())
    assert MAX_NEWS_SEP in text
    assert "———" not in text
    # по одной ссылке на новость (в markdown-заголовке), без дублирующей строки URL
    assert text.count("https://3dnews.ru/1141822/openai-agents") == 5
    assert subscription_md_inline() in text
    assert len(text) <= 4000
    assert re.search(r"➤ \[OpenAI[^\]]+\]\(https://3dnews\.ru/[^\)]+\)\nКомпания", text)


def test_vk_hashtags_separated_from_subscription():
    text = assemble_vk(_sample_payload())
    sub_end = text.index("Boosty: https://boosty.to/extellect")
    tags_start = text.index("#ИИ", sub_end)
    between = text[sub_end:tags_start]
    assert "\n\n" in between
    assert "OPENAI ПЕРЕСТРАИВАЕТСЯ" in text


def test_dzen_subscription_before_hashtags():
    text = assemble_dzen(_sample_payload())
    sub = subscription_md_inline()
    assert sub in text
    assert text.index(sub) < text.rfind("#ИИ")
    assert len(text) <= DZEN_POST_MAX_CHARS


def test_digest_docx_filename():
    assert digest_docx_filename("2026-05-16", 5) == "digest_2026-05-16_id5.docx"


def test_assemble_platform_outputs_all_keys():
    out = assemble_platform_outputs(_sample_payload())
    assert set(out.keys()) == {"telegram", "max", "vk", "dzen"}
    assert all(len(v) > 200 for v in out.values())
