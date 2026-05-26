from app.services.curious_tone import curious_tone_score, passes_curious_tone_gate


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
