from app.services.reader_copy import build_platform_description, sanitize_reader_description


def test_sanitize_strips_tier_and_stamps():
    raw = "Ключевая суть: Tier-2 кандидат. Это важно для рынка ИИ."
    out = sanitize_reader_description(raw)
    assert "Tier" not in out
    assert "ключевая суть" not in out.lower()
    assert "рынка ии" not in out.lower()


def test_build_platform_description_prefers_comment():
    desc = build_platform_description(
        essence="Короткая суть.",
        comment=(
            "OpenAI сводит ChatGPT и Codex в одну линию агентов. "
            "Для разработчиков это сигнал, что инструменты будут ближе друг к другу. "
            "Следите за ценами API и условиями доступа."
        ),
        analysis="Длинный анализ для редактора " * 5,
    )
    assert desc.count(".") >= 2
    assert "редактора" not in desc
    assert "Короткая суть" not in desc


def test_build_platform_description_trims_to_three_sentences():
    long = "Первое. Второе. Третье. Четвёртое."
    desc = build_platform_description("", long, "")
    assert "Четвёртое" not in desc
    assert "Третье." in desc
