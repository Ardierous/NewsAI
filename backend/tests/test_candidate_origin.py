from app.services.candidate_origin import (
    ORIGIN_LLM,
    ORIGIN_MANUAL,
    ORIGIN_SEARCH,
    ORIGIN_TELEGRAM,
    apply_resolved_origin,
    origin_label_ru,
    resolve_candidate_origin,
)


def test_resolve_telegram_seed_by_comment():
    assert (
        resolve_candidate_origin(
            "manual",
            "TELEGRAM_SEED: ссылка из мониторинга канала",
            "",
        )
        == ORIGIN_TELEGRAM
    )


def test_resolve_legacy_manual_category_as_telegram():
    assert (
        resolve_candidate_origin(
            "manual",
            "MANUAL_REQUIRED: добавлено пользователем",
            "Старый текст без маркера поля URL",
        )
        == ORIGIN_TELEGRAM
    )


def test_resolve_real_manual_by_description():
    assert (
        resolve_candidate_origin(
            "manual",
            "MANUAL_REQUIRED: добавлено пользователем",
            "Вставлено в поле URL на шаге 1; материал обязателен",
        )
        == ORIGIN_MANUAL
    )


def test_resolve_search_origin():
    assert resolve_candidate_origin("search", "Источник из веб-поиска", "") == ORIGIN_SEARCH


def test_resolve_llm_categories():
    assert resolve_candidate_origin("technology", "", "") == ORIGIN_LLM
    assert resolve_candidate_origin("analytics", "", "") == ORIGIN_LLM


def test_apply_resolved_origin_mutates_dict():
    item = {
        "category": "manual",
        "verification_comment": "TELEGRAM_SEED: x",
        "description": "",
    }
    apply_resolved_origin(item)
    assert item["category"] == ORIGIN_TELEGRAM


def test_origin_label_ru():
    assert origin_label_ru(ORIGIN_TELEGRAM) == "Из Telegram"
    assert origin_label_ru("unknown") == "unknown"
