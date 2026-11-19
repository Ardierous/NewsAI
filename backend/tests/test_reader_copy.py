from app.services.reader_copy import (
    brief_page_teaser,
    build_platform_description,
    build_reader_text_fallback,
    is_technical_candidate_description,
    public_candidate_description,
    sanitize_reader_description,
    text_primarily_russian,
)


def test_search_stub_is_technical_description():
    assert is_technical_candidate_description(
        "Кандидат из веб-поиска; заголовок подтянут со страницы после проверки."
    )
    assert not is_technical_candidate_description(
        "На показе в Париже снова вышел красный total look и прозрачные ткани."
    )


def test_brief_page_teaser_skips_title_and_takes_sentences():
    title = "Новая коллекция на неделе моды"
    excerpt = (
        "Новая коллекция на неделе моды. "
        "Дизайнеры сделали акцент на красном цвете и удобной повседневной носке. "
        "Показы прошли в Париже. "
        "Гости отметили прозрачные ткани и высокий каблук. "
        "Продажи линейки стартуют в ноябре."
    )
    teaser = brief_page_teaser(excerpt, title=title)
    assert "Кандидат из веб-поиска" not in teaser
    assert "красном" in teaser.lower() or "Париже" in teaser
    assert "ноябре" in teaser


def test_brief_page_teaser_prefers_russian_and_skips_nav():
    excerpt = (
        "Subscribe now to continue reading. Cookie settings. "
        "The brand showed a sheer dress on the runway. "
        "Дом показал прозрачное платье на подиуме и вернул акцент на красный цвет. "
        "Коллекция выйдет в магазинах в декабре."
    )
    teaser = brief_page_teaser(excerpt, title="Показ")
    assert "Subscribe" not in teaser
    assert "прозрачное платье" in teaser
    assert "декабре" in teaser


def test_reader_excerpt_from_html_uses_og_and_body_after_h1():
    from app.services.digest_service import _reader_excerpt_from_article_chunk

    html = """
    <html><head>
    <meta property="og:description" content="Компания представила голосового помощника на устройстве без облака.">
    </head><body>
    <nav>Подписаться Войти Реклама</nav>
    <h1>Новый помощник</h1>
    <p>Устройство понимает речь офлайн и отвечает без отправки записей. Батарея держит сутки. Продажи стартуют в ноябре.</p>
    </body></html>
    """
    out = _reader_excerpt_from_article_chunk(html)
    assert "голосового помощника" in out
    assert "Продажи стартуют" in out


def test_public_candidate_description_keeps_russian_and_drops_english():
    assert text_primarily_russian("Дом показал новую коллекцию в Париже и вернул красный цвет.")
    assert not text_primarily_russian("The brand showed a sheer dress on the runway last night.")
    english = public_candidate_description(
        {
            "title": "Fashion show",
            "description": "The brand showed a sheer dress on the runway last night in Paris.",
            "article_excerpt": "The brand showed a sheer dress on the runway last night in Paris. Sales start in December.",
        },
        require_russian=True,
    )
    assert english == ""
    kept = public_candidate_description(
        {
            "title": "Показ",
            "description": "Дом показал прозрачное платье на подиуме и вернул акцент на красный цвет.",
            "article_excerpt": "The brand showed a sheer dress on the runway last night in Paris.",
        },
        require_russian=True,
    )
    assert "прозрачное платье" in kept
    assert "sheer dress" not in kept


def test_public_candidate_description_prefers_excerpt_over_stub():
    out = public_candidate_description(
        {
            "title": "Интервью про кутюр",
            "description": "Кандидат из веб-поиска; заголовок подтянут со страницы после проверки.",
            "article_excerpt": (
                "Интервью про кутюр. "
                "Актриса рассказала, почему высокая мода снова стала личным ритуалом."
            ),
        }
    )
    assert "веб-поиска" not in out
    assert "высокой моды" in out or "ритуал" in out


def test_sanitize_keeps_analytics_labels_when_asked():
    raw = (
        "Аудитория: банки. Почему важно: риск атак. "
        "Польза: можно усилить защиту. Вред: паника. Возможные последствия: аудит."
    )
    stripped = sanitize_reader_description(raw)
    kept = sanitize_reader_description(raw, keep_analytics_labels=True)
    assert "Почему важно:" not in stripped
    assert "Почему важно:" in kept
    assert "Польза:" in kept


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
