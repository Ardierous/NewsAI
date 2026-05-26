from app.services.reader_copy import (
    build_platform_description,
    build_reader_text_fallback,
    sanitize_reader_description,
)


def test_sanitize_strips_tier_and_stamps():
    raw = "Ключевая суть: Tier-2 кандидат. Это важно для рынка ИИ."
    out = sanitize_reader_description(raw)
    assert "Tier" not in out
    assert "ключевая суть" not in out.lower()
    assert "рынка ии" not in out.lower()


def test_build_platform_description_prefers_reader_text():
    desc = build_platform_description(
        (
            "OpenAI сводит ChatGPT и Codex в одну линию агентов. "
            "Для разработчиков это сигнал, что инструменты будут ближе друг к другу. "
            "Следите за ценами API и условиями доступа."
        ),
        essence="Короткая суть.",
        analysis="Длинный анализ для редактора " * 5,
    )
    assert desc.count(".") >= 2
    assert "редактора" not in desc
    assert "Короткая суть" not in desc


def test_build_platform_description_trims_to_four_sentences():
    long = "Первое. Второе. Третье. Четвёртое. Пятое."
    desc = build_platform_description(long)
    assert "Пятое" not in desc
    assert "Четвёртое." in desc


def test_build_platform_description_fallback_from_analysis():
    desc = build_platform_description(
        "",
        essence="OpenAI обновила Codex.",
        analysis="Это важно командам, которые пишут код с LLM. Стоит проверить лимиты API.",
    )
    assert "OpenAI" in desc
    assert len(desc) > 20


def test_build_reader_text_fallback():
    text = build_reader_text_fallback(
        "Суть новости.",
        "Анализ для редактора. Второе предложение анализа. Третье.",
    )
    assert "Суть новости" in text
    assert text.count(".") >= 1
