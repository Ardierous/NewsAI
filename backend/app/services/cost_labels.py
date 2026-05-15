"""Русские подписи агентов и операций для блока «Стоимость запросов»."""

from __future__ import annotations

# Ключ агента (как в CrewAI / учёте) → краткая роль на русском
AGENT_TITLE_RU: dict[str, str] = {
    "NewsResearchAgent": "Поиск кандидатов в новостях",
    "SourceVerificationAgent": "Проверка источников и ссылок",
    "ScoringAgent": "Оценка и ранжирование новостей",
    "OrderingAgent": "Порядок материалов в выпуске",
    "AnalyticsAgent": "Аналитика по выбранной пятёрке",
    "PlatformWriterAgent": "Тексты для соцсетей и каналов",
    "ImagePromptAgent": "Обложка выпуска",
    "QualityControlAgent": "Контроль качества текстов",
    "WebSearch": "Веб-поиск статей",
}

# Метка операции (request_label) → понятное описание
OPERATION_TITLE_RU: dict[str, str] = {
    "step_1_collect_pool": "Сбор и проверка пула новостей (шаг 1)",
    "step_2_ordering": "Расстановка порядка в выпуске",
    "step_2_ai_optimal_order": "AI-расстановка оптимального порядка",
    "step_3_analytics": "Аналитика по выбранным новостям",
    "step_4_images": "Обложки: промпт и 4 варианта",
    "step_4_texts": "Тексты площадок и проверка качества",
    "run_candidates_research": "Поиск кандидатов (CrewAI)",
    "run_candidates_verify": "Проверка ссылок кандидатов",
    "run_candidates_score": "Скоринг кандидатов",
    "run_candidates_refill": "Добор кандидатов",
    "run_ordering": "Порядок выпуска (CrewAI)",
    "suggest_news_order_ai_optimal": "Порядок через ProxyAPI",
    "run_analytics": "Аналитика блоков",
    "run_image_prompt": "Промпт для обложки",
    "generate_image": "Генерация изображения",
    "run_platform_writer": "Написание текстов площадок",
    "run_platform_writer_repair": "Исправление текстов после QC",
    "run_qc": "Проверка качества текстов",
    "proxyapi_web_search_urls": "Веб-поиск URL статей",
    "proxyapi_web_search_supplement": "Дополнительный веб-поиск",
    "manual_headline_translate_ru": "Перевод заголовка на русский",
}

STEP_TITLE_RU: dict[str, str] = {
    "step_1": "Шаг 1",
    "step_2": "Шаг 2",
    "step_3": "Шаг 3",
    "step_4": "Шаг 4",
}


def agent_title_ru(agent_name: str) -> str:
    return AGENT_TITLE_RU.get(agent_name, agent_name.replace("Agent", "").replace("_", " "))


def operation_title_ru(request_label: str) -> str:
    return OPERATION_TITLE_RU.get(request_label, request_label.replace("_", " "))


def enrich_llm_cost_row(row: dict) -> dict:
    row = dict(row)
    row["agent_title_ru"] = agent_title_ru(str(row.get("agent_name", "")))
    row["operation_title_ru"] = operation_title_ru(str(row.get("request_label", "")))
    row["step_title_ru"] = STEP_TITLE_RU.get(str(row.get("step", "")), str(row.get("step", "")))
    return row
