"""Происхождение кандидата в пуле (откуда попала ссылка)."""

from __future__ import annotations

ORIGIN_MANUAL = "manual"
ORIGIN_TELEGRAM = "telegram_seed"
ORIGIN_SEARCH = "search"
ORIGIN_LLM = "llm_crew"

_ORIGIN_LABELS_RU: dict[str, str] = {
    ORIGIN_MANUAL: "Ручная ссылка (поле URL)",
    ORIGIN_TELEGRAM: "Из Telegram",
    ORIGIN_SEARCH: "Web-поиск",
    ORIGIN_LLM: "LLM-добор",
}


def resolve_candidate_origin(
    category: str | None,
    verification_comment: str | None,
    description: str | None = None,
) -> str:
    """Единая логика origin: manual / telegram_seed / search / llm_crew."""
    comment = str(verification_comment or "")
    desc = str(description or "")
    cat = str(category or "").strip().lower()

    if "TELEGRAM_SEED:" in comment or "Telegram-монитор" in desc or cat == ORIGIN_TELEGRAM:
        return ORIGIN_TELEGRAM
    if "Источник из веб-поиска" in comment or cat == ORIGIN_SEARCH:
        return ORIGIN_SEARCH
    if cat in {ORIGIN_LLM, "technology", "analytics"}:
        return ORIGIN_LLM
    if "поле URL на шаге 1" in desc:
        return ORIGIN_MANUAL
    if "MANUAL_REQUIRED:" in comment:
        # До разделения origin telegram-seed сохранялись как category=manual без маркера TELEGRAM_SEED.
        return ORIGIN_TELEGRAM
    if cat == ORIGIN_MANUAL:
        return ORIGIN_TELEGRAM
    if cat:
        return cat
    return ORIGIN_SEARCH


def origin_label_ru(origin: str | None) -> str:
    key = str(origin or "").strip()
    return _ORIGIN_LABELS_RU.get(key, key or ORIGIN_SEARCH)


def apply_resolved_origin(item: dict) -> None:
    item["category"] = resolve_candidate_origin(
        item.get("category"),
        item.get("verification_comment"),
        item.get("description"),
    )
