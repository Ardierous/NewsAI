from app.services.curious_tone import (
    curious_tone_score,
    curious_total_score_from_tone,
    has_curious_positive_signal,
    is_dry_serious_curious_news,
    passes_curious_pool_gate,
    passes_curious_tone_gate,
)


def test_passes_curious_funny_headline() -> None:
    assert passes_curious_tone_gate(
        "Нейросеть перепутала кота с борщом — смешной фейл",
        "чат-бот ошибся в генерации картинки",
    )


def test_rejects_serious_regulation_without_humor() -> None:
    assert not passes_curious_tone_gate(
        "ЕС принял новый закон о регулировании ИИ",
        "законодательство и инвестиции в отрасль",
    )


def test_rejects_dry_product_launch() -> None:
    assert not passes_curious_tone_gate(
        "Google представила новую версию Gemini",
        "компания анонсировала модель для разработчиков",
    )


def test_curious_tone_score_ranks_funny_higher() -> None:
    funny = curious_tone_score("Абсурдный глюк чат-бота в поддержке", "")
    serious = curious_tone_score("Инвестиции в стартап ИИ", "раунд финансирования")
    assert funny > serious


def test_curious_tone_recognizes_user_backlash_and_broken_code() -> None:
    assert passes_curious_tone_gate(
        "Gemini сломала сайт, удалив 30 тысяч строк кода",
        "пользователи жалуются на странный баг ИИ-агента",
    )
    assert passes_curious_tone_gate(
        "Пользователи недовольны новыми лимитами Google Gemini",
        "жалобы пользователей и странный кейс вокруг чат-бота",
    )


def test_passes_human_interest_without_explicit_funny_word() -> None:
    assert passes_curious_tone_gate(
        "Пользователь подал в суд на Character AI из-за чат-бота",
        "искусственный интеллект и нейросети",
    )


def test_still_rejects_press_launch() -> None:
    assert not passes_curious_tone_gate(
        "Google представила новую версию Gemini",
        "компания анонсировала модель для разработчиков",
    )
    assert is_dry_serious_curious_news(
        "Google представила новую версию Gemini",
        "компания анонсировала модель для разработчиков",
    )


def test_rejects_product_launch_with_sidebar_noise() -> None:
    title = "Xiaomi представила ИИ для программистов"
    sidebar = (
        "Другие новости: курьёз с нейросетью, смешной фейл чат-бота, "
        "абсурдный глюк в поддержке, вирусный мем про GPT. " * 20
    )
    assert not passes_curious_tone_gate(title, sidebar)
    assert is_dry_serious_curious_news(title, sidebar)


def test_rejects_industrial_ai_rollout() -> None:
    assert not passes_curious_tone_gate(
        "ОДК начали контролировать лопатки с помощью ИИ",
        "промышленное внедрение технологий контроля качества деталей",
    )


def test_pool_gate_accepts_curious_lead_without_title_marker() -> None:
    title = "ИИ-радиостанция начала вещать бред ночью"
    corpus = (
        "Слушатели смеются над абсурдными ответами нейросети — вирусный кринж "
        "разошёлся по соцсетям, это смешной фейл вещания."
    )
    assert passes_curious_pool_gate(title, corpus)


def test_pool_gate_rejects_neutral_overview() -> None:
    title = "Как нейросети меняют образование: новые возможности"
    corpus = "обзор и анализ влияния искусственного интеллекта"
    assert not passes_curious_pool_gate(title, corpus)
    assert not passes_curious_tone_gate(title, corpus)


def test_rejects_neutral_tech_overview_without_humor() -> None:
    assert not passes_curious_tone_gate(
        "Как нейросети меняют образование: новые возможности и перспективы",
        "обзор и анализ влияния искусственного интеллекта",
    )


def test_curious_total_score_prefers_entertaining_over_low_tone() -> None:
    assert curious_total_score_from_tone(5, low=False) > curious_total_score_from_tone(1, low=True)


def test_expanded_ru_curious_markers() -> None:
    for title in (
        "Казус с нейросетью в метро",
        "Конфуз на презентации ИИ-бота",
        "Ляп в промпте ChatGPT",
        "Прокол при запуске голосового агента",
        "Хохма про машинное обучение",
        "Уморительная байка про GPT",
        "Комичный парадокс в ответе Claude",
    ):
        assert has_curious_positive_signal(title, ""), title


def test_expanded_en_curious_markers() -> None:
    for title in (
        "Hilarious AI mishap at airport",
        "Ridiculous blooper from voice assistant",
        "Wacky spoof of ChatGPT launch",
        "Side-splitting gaffe by coding bot",
    ):
        assert has_curious_positive_signal(title, ""), title


def test_pool_gate_rejects_midjourney_product_changelog() -> None:
    title = "Новости Midjourney от 10.06.2026"
    corpus = (
        "V8.1 становится моделью по умолчанию. Панель промптов и масштабный редизайн сайта. "
        "Улучшенная генерация текста. Style Creator и локализация через Crowdin."
    )
    assert not passes_curious_pool_gate(title, corpus)
    assert not passes_curious_tone_gate(title, corpus)
    assert is_dry_serious_curious_news(title, corpus)


def test_pool_gate_accepts_funny_url_slug_with_weak_title() -> None:
    assert passes_curious_pool_gate(
        "Страница: theregister.com",
        "искусственный интеллект и технологии",
        url="https://www.theregister.com/2024/1/15/ai_image_generator_creates_hilarious_pictures/",
    )


def test_pool_gate_accepts_robot_phone_demo() -> None:
    assert passes_curious_pool_gate(
        "Honor Robot Phone показали в мультимедиа-арт-музее",
        "Необычный прототип робота-телефона с ИИ на выставке в Москве.",
    )


def test_pool_gate_accepts_marker_without_explicit_fail() -> None:
    assert passes_curious_pool_gate(
        "Уморительная история: нейросеть перепутала заказы",
        "искусственный интеллект",
    )


def test_raw_url_keyword_score_uses_search_literals() -> None:
    from app.services.curious_tone import curious_raw_url_keyword_score, curious_url_positive_hints

    funny_url = "https://www.theregister.com/2024/1/15/ai_image_generator_creates_hilarious_pictures/"
    dry_url = "https://kod.ru/nvidia-dgx-station"
    assert curious_raw_url_keyword_score(funny_url) >= 3
    assert curious_raw_url_keyword_score(dry_url) < curious_raw_url_keyword_score(funny_url)
    assert "hilarious" in curious_url_positive_hints(funny_url)


def test_raw_url_rank_prefers_in_window_over_old_fail_slug() -> None:
    from app.services.curious_tone import curious_raw_url_rank_key

    stale_fail = curious_raw_url_rank_key(
        "https://futurism.com/the-byte/epic-ai-fail-2023",
        fresh_rank=2,
        source_rank=1,
        day_ordinal=738_000,
    )
    fresh_neutral = curious_raw_url_rank_key(
        "https://3dnews.ru/2026/06/08/neutral-ai-story",
        fresh_rank=0,
        source_rank=1,
        day_ordinal=739_000,
    )
    assert stale_fail > fresh_neutral


def test_tone_accepts_ai_tool_failure_and_bot_traffic_paradox() -> None:
    assert passes_curious_pool_gate(
        "Starbucks отказалась от AI-инструмента для инвентаризации: нейросеть не помогла решить проблему",
        "компания отказалась от инструмента искусственного интеллекта",
    )
    assert passes_curious_pool_gate(
        "Боты впервые обогнали людей по интернет-трафику — виноваты ИИ-агенты",
        "боты обогнали людей по трафику",
    )
