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


def test_passes_curious_pool_gate_allows_human_interest_without_strict_score() -> None:
    from app.services.curious_tone import passes_curious_pool_gate, passes_curious_tone_gate

    title = "Пользователь поделился экспериментом с промптом"
    corpus = "обычный текст про prompt engineering"
    assert passes_curious_pool_gate(title, corpus)
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


def test_pool_gate_accepts_marker_without_explicit_fail() -> None:
    assert passes_curious_pool_gate(
        "Уморительная история: нейросеть перепутала заказы",
        "искусственный интеллект",
    )
