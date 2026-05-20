"""Человекочитаемые подписи статусов выпуска для UI."""

from __future__ import annotations

STATUS_LABEL_RU: dict[str, str] = {
    "draft": "Черновик",
    "step_0": "Тип выпуска задан",
    "step_1_candidates": "Пул кандидатов собран",
    "selected": "Пятёрка новостей выбрана",
    "analytics_ready": "Аналитика готова",
    "final_ready": "Готов к публикации",
}


def digest_status_label_ru(status: str | None) -> str:
    key = str(status or "").strip()
    if not key:
        return "Неизвестно"
    return STATUS_LABEL_RU.get(key, key.replace("_", " "))
