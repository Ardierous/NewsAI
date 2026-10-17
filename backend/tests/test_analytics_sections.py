from app.services.analytics_sections import (
    build_extended_news_sections,
    extended_sections_sentence_count,
    parse_analysis_blocks,
)


def test_parse_analysis_blocks_five_labels():
    analysis = (
        "Аудитория: Любители моды. "
        "Почему важно: Задаёт тренд сезона. "
        "Польза: Помогает обновить гардероб. "
        "Вред: Риск импульсивных покупок. "
        "Возможные последствия: Рост спроса на аксессуары."
    )
    blocks = parse_analysis_blocks(analysis)
    assert "моды" in blocks["audience"]
    assert "тренд" in blocks["why"]
    assert "гардероб" in blocks["benefit"]
    assert "покупок" in blocks["harm"]
    assert "спроса" in blocks["consequences"]


def test_build_extended_news_sections_sentence_budget():
    analysis = (
        "Аудитория: Стилисты и покупатели онлайн. "
        "Почему важно: Неделя моды задаёт силуэты. "
        "Польза: Можно спланировать покупки заранее. "
        "Вред: Давление трендов на бюджет. "
        "Возможные последствия: Бренды ускорят коллекции."
    )
    reader = (
        "На показе доминировал красный total look. "
        "Дизайнеры смешали прозрачные ткани с классическими юбками. "
        "Образы выглядели смелыми, но носибельными. "
        "Публика отметила акцент на комфорте. "
        "Критики назвали сезон поворотом к практичности."
    )
    sections = build_extended_news_sections(
        title="Тест",
        essence="Красный цвет и юбки — главные тренды.",
        comment="Проверить цены в российских магазинах.",
        analysis=analysis,
        reader_text=reader,
    )
    headings = [h for h, _ in sections]
    assert "Краткий пересказ новости" in headings
    assert "Актуальность для российских читателей" in headings
    count = extended_sections_sentence_count(sections)
    assert 18 <= count <= 28
