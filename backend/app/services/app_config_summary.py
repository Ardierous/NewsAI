"""Сводка основных параметров конфигурации для UI (без секретов)."""

from __future__ import annotations

import os
from typing import Any

from app.config import Settings, get_settings
from app.digest_defaults import get_digest_defaults
from app.pipeline_settings import get_pipeline_config
from app.services.app_config_item_meta import config_item_meta
from app.services.step1_filter_settings import load_step1_filter_settings
from app.services.step1_filters import STEP1_FILTER_DEF_BY_ID

_SETTING_ENV: dict[str, str] = {
    "proxyapi_base_url": "PROXYAPI_BASE_URL",
    "proxyapi_model": "PROXYAPI_MODEL",
    "proxyapi_image_model": "PROXYAPI_IMAGE_MODEL",
    "database_url": "DATABASE_URL",
    "backend_host": "BACKEND_HOST",
    "backend_port": "BACKEND_PORT",
    "frontend_origin": "FRONTEND_ORIGIN",
    "enable_web_fetch": "ENABLE_WEB_FETCH",
    "proxyapi_web_search_enabled": "PROXYAPI_WEB_SEARCH_ENABLED",
    "proxyapi_web_search_model": "PROXYAPI_WEB_SEARCH_MODEL",
    "proxyapi_web_search_preview_model": "PROXYAPI_WEB_SEARCH_PREVIEW_MODEL",
    "proxyapi_web_search_context_size": "PROXYAPI_WEB_SEARCH_CONTEXT_SIZE",
    "proxyapi_web_search_context_size_supplement": "PROXYAPI_WEB_SEARCH_CONTEXT_SIZE_SUPPLEMENT",
    "step1_search_tier1_min_raw_urls": "STEP1_SEARCH_TIER1_MIN_RAW_URLS",
    "step1_max_cost_rub": "STEP1_MAX_COST_RUB",
    "step1_batch_size": "STEP1_BATCH_SIZE",
    "step1_search_fetch_limit": "STEP1_SEARCH_FETCH_LIMIT",
    "step1_urls_checked_per_collect": "STEP1_URLS_CHECKED_PER_COLLECT",
    "step1_soft_time_limit_sec": "STEP1_SOFT_TIME_LIMIT_SEC",
    "step1_hard_time_limit_sec": "STEP1_HARD_TIME_LIMIT_SEC",
    "step1_max_candidates_for_ui": "STEP1_MAX_CANDIDATES_FOR_UI",
    "step1_verify_workers": "STEP1_VERIFY_WORKERS",
    "step1_crew_fallback_only_if_empty": "STEP1_CREW_FALLBACK_ONLY_IF_EMPTY",
    "step1_tier_strict_search": "STEP1_TIER_STRICT_SEARCH",
    "step1_telegram_monitor_enabled": "STEP1_TELEGRAM_MONITOR_ENABLED",
    "step1_telegram_monitor_channels": "STEP1_TELEGRAM_MONITOR_CHANNELS",
    "step1_telegram_max_pages": "STEP1_TELEGRAM_MAX_PAGES",
    "step1_telegram_max_links": "STEP1_TELEGRAM_MAX_LINKS",
    "step1_seed_urls_max": "STEP1_SEED_URLS_MAX",
    "step2_max_cost_rub": "STEP2_MAX_COST_RUB",
    "auto_run_step3_after_order": "AUTO_RUN_STEP3_AFTER_ORDER",
    "enable_step4_image_generation": "ENABLE_STEP4_IMAGE_GENERATION",
    "log_level": "LOG_LEVEL",
    "log_enable_file": "LOG_ENABLE_FILE",
    "log_file_name": "LOG_FILE_NAME",
    "log_max_bytes": "LOG_MAX_BYTES",
    "log_backup_count": "LOG_BACKUP_COUNT",
}

_PIPELINE_FIELDS = frozenset(
    {
        "enable_web_fetch",
        "step1_search_tier1_min_raw_urls",
        "step1_max_cost_rub",
        "step1_batch_size",
        "step1_search_fetch_limit",
        "step1_urls_checked_per_collect",
        "step1_soft_time_limit_sec",
        "step1_hard_time_limit_sec",
        "step1_max_candidates_for_ui",
        "step1_verify_workers",
        "step1_crew_fallback_only_if_empty",
        "step1_tier_strict_search",
        "step1_telegram_monitor_enabled",
        "step1_telegram_monitor_channels",
        "step1_telegram_max_pages",
        "step1_telegram_max_links",
        "step1_seed_urls_max",
        "step2_max_cost_rub",
        "auto_run_step3_after_order",
        "enable_step4_image_generation",
        "log_level",
        "log_enable_file",
        "log_file_name",
        "log_max_bytes",
        "log_backup_count",
    }
)


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "да" if value else "нет"
    if value is None:
        return "—"
    return str(value)


def _source_for(field: str) -> str:
    env_key = _SETTING_ENV.get(field)
    if env_key and env_key in os.environ:
        return ".env"
    if field in _PIPELINE_FIELDS:
        return "pipeline_settings.json"
    return "config.py"


def _item(
    label: str,
    value: Any,
    field: str,
    *,
    hint: str | None = None,
    source: str | None = None,
    why_chosen: str | None = None,
    alternatives: str | None = None,
) -> dict[str, str]:
    resolved_source = source or _source_for(field)
    auto_why, auto_alt = config_item_meta(field, _fmt(value))
    if resolved_source == ".env":
        env_suffix = " Текущее значение переопределено через .env."
    elif resolved_source == "pipeline_settings.json":
        env_suffix = " Основной источник — pipeline_settings.json (не .env)."
    elif resolved_source == "step1_filter_settings.json":
        env_suffix = " Задаётся в step1_filter_settings.json или UI «Настройки фильтра»."
    elif resolved_source == "digest_defaults.json":
        env_suffix = " Дефолт шага 0; на конкретном выпуске можно изменить перед запуском."
    else:
        env_suffix = ""
    row: dict[str, str] = {
        "label": label,
        "value": _fmt(value),
        "source": resolved_source,
        "why_chosen": (why_chosen or auto_why) + env_suffix,
        "alternatives": alternatives or auto_alt,
    }
    if hint:
        row["hint"] = hint
    return row


def build_app_config_summary() -> dict[str, Any]:
    settings = get_settings()
    pipeline = get_pipeline_config()
    step1_cfg = load_step1_filter_settings()
    step0 = get_digest_defaults().step0
    web = pipeline["web"]

    enabled_filters = []
    for row in step1_cfg.get("filters") or []:
        fid = str(row.get("id") or "")
        if not row.get("enabled"):
            continue
        fdef = STEP1_FILTER_DEF_BY_ID.get(fid)
        enabled_filters.append(fdef.label_ru if fdef else fid)

    sections: list[dict[str, Any]] = [
        {
            "id": "deployment",
            "title": "Сеть и база",
            "file": ".env / config.py",
            "items": [
                _item("Backend host", settings.backend_host, "backend_host"),
                _item("Backend port", settings.backend_port, "backend_port"),
                _item("Frontend origin (CORS)", settings.frontend_origin, "frontend_origin"),
                _item("Database URL", settings.database_url, "database_url"),
            ],
        },
        {
            "id": "proxyapi",
            "title": "ProxyAPI",
            "file": ".env",
            "items": [
                _item("API key", "задан" if bool(settings.proxyapi_api_key) else "не задан", "proxyapi_api_key"),
                _item("Base URL", settings.proxyapi_base_url, "proxyapi_base_url"),
                _item("Модель текста", settings.proxyapi_model, "proxyapi_model"),
                _item("Модель изображений", settings.proxyapi_image_model, "proxyapi_image_model"),
                _item("Web search", settings.proxyapi_web_search_enabled, "proxyapi_web_search_enabled"),
                _item("Web search model", settings.proxyapi_web_search_model, "proxyapi_web_search_model"),
                _item(
                    "Web search preview model",
                    settings.proxyapi_web_search_preview_model,
                    "proxyapi_web_search_preview_model",
                ),
                _item("Context size", settings.proxyapi_web_search_context_size, "proxyapi_web_search_context_size"),
                _item(
                    "Context size (добор)",
                    settings.proxyapi_web_search_context_size_supplement,
                    "proxyapi_web_search_context_size_supplement",
                ),
                _item(
                    "SerpAPI key",
                    "задан" if settings.serpapi_api_key else "не задан",
                    "serpapi_api_key",
                ),
                _item(
                    "Tavily key",
                    "задан" if settings.tavily_api_key else "не задан",
                    "tavily_api_key",
                ),
            ],
        },
        {
            "id": "web",
            "title": "Веб-сбор новостей",
            "file": "backend/app/pipeline_settings.json",
            "items": [
                _item(
                    "Автосбор (HTTP + поиск)",
                    settings.enable_web_fetch,
                    "enable_web_fetch",
                    hint=f"JSON: web.enable_fetch = {_fmt(web.get('enable_fetch'))}",
                ),
            ],
        },
        {
            "id": "step1_pipeline",
            "title": "Шаг 1 — технические лимиты",
            "file": "backend/app/pipeline_settings.json → step1",
            "items": [
                _item("Размер батча", settings.step1_batch_size, "step1_batch_size"),
                _item("Soft timeout (с)", settings.step1_soft_time_limit_sec, "step1_soft_time_limit_sec"),
                _item("Hard timeout (с)", settings.step1_hard_time_limit_sec, "step1_hard_time_limit_sec"),
                _item("Workers HTTP-проверки", settings.step1_verify_workers, "step1_verify_workers"),
                _item("URL на collect (HTTP)", settings.step1_urls_checked_per_collect, "step1_urls_checked_per_collect"),
                _item("URL из поиска (fetch)", settings.step1_search_fetch_limit, "step1_search_fetch_limit"),
                _item("Tier-1 min raw URLs", settings.step1_search_tier1_min_raw_urls, "step1_search_tier1_min_raw_urls"),
                _item("Макс. кандидатов в UI", settings.step1_max_candidates_for_ui, "step1_max_candidates_for_ui"),
                _item("Лимит ₽ (шаг 1)", settings.step1_max_cost_rub, "step1_max_cost_rub"),
                _item(
                    "Crew только если пусто",
                    settings.step1_crew_fallback_only_if_empty,
                    "step1_crew_fallback_only_if_empty",
                ),
                _item(
                    "Tier-строгий поиск",
                    settings.step1_tier_strict_search,
                    "step1_tier_strict_search",
                    hint="Поиск только по tier-1…4 из source_tiers.txt (site: батчи по приоритету)",
                ),
                _item("Telegram monitor", settings.step1_telegram_monitor_enabled, "step1_telegram_monitor_enabled"),
                _item("Telegram каналы", settings.step1_telegram_monitor_channels, "step1_telegram_monitor_channels"),
                _item("Telegram max pages", settings.step1_telegram_max_pages, "step1_telegram_max_pages"),
                _item("Telegram max links", settings.step1_telegram_max_links, "step1_telegram_max_links"),
                _item("Seed URLs max", settings.step1_seed_urls_max, "step1_seed_urls_max"),
            ],
        },
        {
            "id": "step1_filters",
            "title": "Шаг 1 — фильтры и воронка",
            "file": "backend/app/step1_filter_settings.json",
            "items": [
                _item(
                    "Мин. страниц (воронка)",
                    step1_cfg.get("min_discovered_pages"),
                    "min_discovered_pages",
                    source="step1_filter_settings.json",
                ),
                _item(
                    "Мин. итераций web-поиска",
                    step1_cfg.get("min_collection_iterations"),
                    "min_collection_iterations",
                    source="step1_filter_settings.json",
                ),
                _item(
                    "Включено фильтров",
                    len(enabled_filters),
                    "step1_filters_enabled_count",
                    source="step1_filter_settings.json",
                ),
                _item(
                    "Активные фильтры",
                    ", ".join(enabled_filters[:12]) + ("…" if len(enabled_filters) > 12 else ""),
                    "step1_filters_enabled_list",
                    source="step1_filter_settings.json",
                ),
            ],
        },
        {
            "id": "step0_defaults",
            "title": "Шаг 0 — дефолты",
            "file": "backend/app/digest_defaults.json",
            "items": [
                _item("Тип по умолчанию", step0.digest_type_default, "digest_type_default", source="digest_defaults.json"),
                _item("Окно (дней)", step0.news_window_days_default, "news_window_days_default", source="digest_defaults.json"),
                _item(
                    "Тип дней",
                    step0.news_window_day_kind_default,
                    "news_window_day_kind_default",
                    source="digest_defaults.json",
                ),
            ],
        },
        {
            "id": "workflow",
            "title": "Шаги 2–4 и логи",
            "file": "backend/app/pipeline_settings.json",
            "items": [
                _item("Лимит ₽ (шаг 2)", settings.step2_max_cost_rub, "step2_max_cost_rub"),
                _item("Авто шаг 3 после порядка", settings.auto_run_step3_after_order, "auto_run_step3_after_order"),
                _item("Генерация обложек (шаг 4)", settings.enable_step4_image_generation, "enable_step4_image_generation"),
                _item("Log level", settings.log_level, "log_level"),
                _item("Log в файл", settings.log_enable_file, "log_enable_file"),
                _item("Log file", settings.log_file_name, "log_file_name"),
                _item("Log max bytes", settings.log_max_bytes, "log_max_bytes"),
                _item("Log backup count", settings.log_backup_count, "log_backup_count"),
            ],
        },
    ]

    env_overrides = sorted(
        env_key
        for field, env_key in _SETTING_ENV.items()
        if env_key in os.environ and field in Settings.model_fields
    )

    return {
        "sections": sections,
        "env_overrides": env_overrides,
        "note": (
            "Обычно в backend/.env только PROXYAPI_API_KEY. "
            "Поведение пайплайна — в backend/app/pipeline_settings.json и step1_filter_settings.json; "
            "после правок перезапустите backend."
        ),
    }
