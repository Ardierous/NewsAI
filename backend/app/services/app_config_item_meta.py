"""Пояснения к параметрам конфигурации для модалки «Настройки» в UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConfigItemMeta:
    why: str
    alternatives: str


def _bool_why(value: Any, when_true: str, when_false: str) -> str:
    if isinstance(value, bool):
        return when_true if value else when_false
    text = str(value or "").strip().lower()
    if text in {"да", "true", "1", "yes", "on"}:
        return when_true
    if text in {"нет", "false", "0", "no", "off"}:
        return when_false
    return when_true


_FIELD_META: dict[str, ConfigItemMeta] = {
    "backend_host": ConfigItemMeta(
        why="127.0.0.1 — backend слушает только локально; безопасно для разработки на одной машине с frontend.",
        alternatives="0.0.0.0 (доступ из LAN), IP или hostname сервера при деплое.",
    ),
    "backend_port": ConfigItemMeta(
        why="8000 — стандартный порт FastAPI/Uvicorn в README проекта; совпадает с URL frontend.",
        alternatives="любой свободный порт 1024–65535; после смены обновите frontend и CORS.",
    ),
    "frontend_origin": ConfigItemMeta(
        why="http://localhost:3000 — origin Next.js dev-сервера; нужен для CORS браузера.",
        alternatives="URL прод-frontend (https://…); должен точно совпадать со схемой и портом UI.",
    ),
    "database_url": ConfigItemMeta(
        why="sqlite:///./digest.db — файловая БД рядом с backend, без отдельного сервера БД.",
        alternatives="PostgreSQL/MySQL URL для продакшена; путь к другому .db файлу для SQLite.",
    ),
    "proxyapi_api_key": ConfigItemMeta(
        why="Ключ обязателен для LLM, web_search и перевода заголовков; в UI показываем только факт наличия.",
        alternatives="единственная обязательная строка в backend/.env; остальное — JSON/config.py.",
    ),
    "proxyapi_base_url": ConfigItemMeta(
        why="Официальный endpoint ProxyAPI для OpenAI-совместимых запросов из РФ.",
        alternatives="другой base URL только если провайдер явно указал иной адрес.",
    ),
    "proxyapi_model": ConfigItemMeta(
        why="gpt-4.1 — основная модель Crew/аналитики: баланс качества и стоимости для русскоязычного дайджеста.",
        alternatives="openai/gpt-4.1-mini (дешевле), openai/gpt-4o (дороже); префикс openai/ обязателен для LiteLLM.",
    ),
    "proxyapi_image_model": ConfigItemMeta(
        why="gpt-image-1 — модель обложек шага 4; шаг 4 по умолчанию выключен.",
        alternatives="другая image-модель из каталога ProxyAPI, если включите генерацию обложек.",
    ),
    "proxyapi_web_search_enabled": ConfigItemMeta(
        why="Включено: шаг 1 использует ProxyAPI web_search как один из провайдеров URL.",
        alternatives="false — только SerpAPI/Tavily (если ключи заданы) или ручные URL.",
    ),
    "proxyapi_web_search_model": ConfigItemMeta(
        why="gpt-4o-mini — Responses API + tool web_search: дешевле gpt-4.1 при приемлемом качестве выдачи.",
        alternatives="gpt-4o, gpt-4.1-mini; влияет на стоимость и полноту URL в ответе.",
    ),
    "proxyapi_web_search_preview_model": ConfigItemMeta(
        why="gpt-4o-mini-search-preview — запасной канал только при ошибке Responses API (не при пустом парсинге).",
        alternatives="другая *-search-preview модель из документации ProxyAPI.",
    ),
    "proxyapi_web_search_context_size": ConfigItemMeta(
        why="medium — основной tier-поиск: достаточный контекст без лишней оплаты context=high.",
        alternatives="low (дешевле, меньше URL), high (дороже, шире выдача).",
    ),
    "proxyapi_web_search_context_size_supplement": ConfigItemMeta(
        why="low — для дополнительных батчей и supplement: экономия, т.к. запросов несколько за прогон.",
        alternatives="medium или high, если мало сырых URL и нужен более агрессивный добор.",
    ),
    "serpapi_api_key": ConfigItemMeta(
        why="Не задан — необязательно: основной поиск идёт через ProxyAPI; SerpAPI дополняет merge URL.",
        alternatives="API-ключ SerpAPI в .env — добавит Google News URL параллельно другим провайдерам.",
    ),
    "tavily_api_key": ConfigItemMeta(
        why="Не задан — необязательно; Tavily даёт ещё один источник сырых URL при merge.",
        alternatives="API-ключ Tavily в .env; можно ограничивать include_domains при tier-поиске.",
    ),
    "enable_web_fetch": ConfigItemMeta(
        why="Включено: шаг 1 автоматически ищет и проверяет статьи по HTTP (основной режим работы).",
        alternatives="false — только ручные/telegram seed URL (минимум 5–10 ссылок в поле шага 1).",
    ),
    "step1_batch_size": ConfigItemMeta(
        why="20 URL за итерацию — баланс между прогрессом воронки и временем одного цикла verify.",
        alternatives="1–100 (clamp в JSON); меньше — больше итераций, больше — длиннее один проход HTTP.",
    ),
    "step1_soft_time_limit_sec": ConfigItemMeta(
        why="180 с — мягкий стоп после min_collection_iterations, если пул уже близок к цели.",
        alternatives="30–7200 с; меньше — быстрее stop, больше — дольше добор кандидатов.",
    ),
    "step1_hard_time_limit_sec": ConfigItemMeta(
        why="300 с — жёсткий потолок прогона шага 1; защита от бесконечного цикла и перерасхода API.",
        alternatives="60–14400 с; должен быть ≥ soft; увеличивайте, если tier-поиск не успевает за 5 мин.",
    ),
    "step1_verify_workers": ConfigItemMeta(
        why="6 параллельных HTTP-проверок — ускорение verify без перегрузки сайтов-источников.",
        alternatives="1–24; на слабом канале или при блокировках лучше 3–4, на мощном сервере — до 12.",
    ),
    "step1_urls_checked_per_collect": ConfigItemMeta(
        why="80 URL на collect — верхняя граница HTTP-проверок за один вызов поиска.",
        alternatives="10–500; больше — шире воронка, но дольше и дороже один collect.",
    ),
    "step1_search_fetch_limit": ConfigItemMeta(
        why="100 сырых URL из всех провайдеров — запас до prefilter и dedup.",
        alternatives="10–500; при tier-strict раскладывается на батчи site: по доменам политики.",
    ),
    "step1_search_tier1_min_raw_urls": ConfigItemMeta(
        why="15 — порог добора tier-1 только при tier_strict_search=false (legacy режим).",
        alternatives="1–100; при tier_strict=true не используется — поиск идёт сразу по tier-1…4.",
    ),
    "step1_max_candidates_for_ui": ConfigItemMeta(
        why="15 — целевой размер пула в UI (10–15 по контракту дайджеста).",
        alternatives="10–30; rebalance стремится к min(15, verified_count), но не ниже 10 для шага 2.",
    ),
    "step1_max_cost_rub": ConfigItemMeta(
        why="50 ₽ — потолок расхода ProxyAPI на шаг 1 за один прогон выпуска.",
        alternatives="1–10000 ₽; при достижении лимита web_search и Crew добор останавливаются.",
    ),
    "step1_crew_fallback_only_if_empty": ConfigItemMeta(
        why="Да — CrewAI не вызывается, если веб-поиск уже дал кандидатов (экономия 10–20 мин).",
        alternatives="false — Crew может добирать даже при частично заполненном пуле (дороже).",
    ),
    "step1_tier_strict_search": ConfigItemMeta(
        why="Да — поиск только по tier-1…4 из source_tiers.txt (site: батчи, allowed_hosts).",
        alternatives="false — общий web_search + legacy добор tier-1; шире выдача, но вне политики источников.",
    ),
    "step1_telegram_monitor_enabled": ConfigItemMeta(
        why="Да — канал technokratos даёт seed URL в окне выпуска без ручного копирования ссылок.",
        alternatives="false — только ручное поле URL и tier-поиск; telegram_seed не подмешивается.",
    ),
    "step1_telegram_monitor_channels": ConfigItemMeta(
        why="technokratos — канал по умолчанию из pipeline_settings.json.",
        alternatives="список через запятую: @channel или slug; каждый канал парсится до telegram_max_pages.",
    ),
    "step1_telegram_max_pages": ConfigItemMeta(
        why="2 страницы ленты — достаточно свежих постов за окно без долгого парсинга t.me/s.",
        alternatives="1–10; больше страниц — больше seed URL и дольше старт шага 1.",
    ),
    "step1_telegram_max_links": ConfigItemMeta(
        why="30 ссылок — верхняя граница внешних URL из Telegram за прогон.",
        alternatives="1–200; после нормализации merge с ручными URL и dedup.",
    ),
    "step1_seed_urls_max": ConfigItemMeta(
        why="35 — лимит объединённых seed (ручные + telegram) перед verify.",
        alternatives="1–100; защита от слишком длинного pre-verify этапа.",
    ),
    "min_discovered_pages": ConfigItemMeta(
        why="10 — минимум проверенных страниц в воронке до финального rebalance (контракт шага 2).",
        alternatives="целое ≥10 через UI «Настройки фильтра»; выше — дольше сбор, ниже — шаг 2 не откроется.",
    ),
    "min_collection_iterations": ConfigItemMeta(
        why="5 — soft/no_progress stop не срабатывает раньше пяти итераций web-поиска.",
        alternatives="1–20; меньше — быстрее stop при пустой выдаче, больше — упорнее добор.",
    ),
    "step1_filters_enabled_count": ConfigItemMeta(
        why="Считаются только фильтры с enabled=true в step1_filter_settings.json.",
        alternatives="0…N по числу фильтров в каталоге; отключение фильтра ослабляет отбор (осторожно).",
    ),
    "step1_filters_enabled_list": ConfigItemMeta(
        why="Порядок в списке = порядок применения на этапах pre_http / verify.",
        alternatives="перетаскивание в модалке «Настройки фильтра новостей» → PUT step1/filters.",
    ),
    "digest_type_default": ConfigItemMeta(
        why="curious — тип выпуска по умолчанию на шаге 0 (можно сменить перед запуском).",
        alternatives=(
            "serious — деловой тон; EN-ключи поиска, пресс-релизы в rebalance. "
            "curious — курьёзы/фейлы/мемы; RU+EN-ключи, без пресс-релизов и без добора press-query."
        ),
    ),
    "news_window_days_default": ConfigItemMeta(
        why="3 дня — окно «свежих» новостей от даты выпуска; типичный оперативный дайджест.",
        alternatives="1–14+ через шаг 0; влияет на prefilter published_before_window.",
    ),
    "news_window_day_kind_default": ConfigItemMeta(
        why="working — рабочие дни (пн–пт); для корпоративного ритма выпусков.",
        alternatives="calendar — календарные дни подряд, включая выходные.",
    ),
    "step2_max_cost_rub": ConfigItemMeta(
        why="50 ₽ — лимит ProxyAPI на шаг 2 (OrderingAgent / AI-порядок).",
        alternatives="1–10000 ₽; при превышении порядок сохраняется без LLM-оптимизации.",
    ),
    "auto_run_step3_after_order": ConfigItemMeta(
        why="Да — после «Применить порядок» или AI-порядка автоматически стартует аналитика.",
        alternatives="false — шаг 3 только вручную; удобно для проверки порядка перед расходом API.",
    ),
    "enable_step4_image_generation": ConfigItemMeta(
        why="Нет — генерация обложек отключена (экономия и скорость; текст шага 4 без картинок).",
        alternatives="true — вызов image-модели ProxyAPI при финальной сборке DOCX/платформ.",
    ),
    "log_level": ConfigItemMeta(
        why="INFO — достаточно для диагностики шага 1 (Tier-поиск, воронка) без шума DEBUG.",
        alternatives="DEBUG, WARNING, ERROR; DEBUG — для разбора отказов URL и фильтров.",
    ),
    "log_enable_file": ConfigItemMeta(
        why="Да — ротация в backend/logs/app-YYYY-MM-DD.log для post-mortem после прогона.",
        alternatives="false — только stdout (docker/journal); файл не создаётся.",
    ),
    "log_file_name": ConfigItemMeta(
        why="app.log — базовое имя; фактический файл с датой задаётся logging_config.",
        alternatives="любое имя .log; путь относительно log_dir.",
    ),
    "log_max_bytes": ConfigItemMeta(
        why="5 000 000 байт (~5 МБ) на файл — баланс объёма истории и места на диске.",
        alternatives="100000–100000000; больше — длиннее история без ротации по размеру.",
    ),
    "log_backup_count": ConfigItemMeta(
        why="5 архивных файлов — ~25 МБ истории логов на инстанс.",
        alternatives="0–50; 0 — без backup-файлов, только текущий log.",
    ),
}


def config_item_meta(field: str, value: Any | None = None) -> tuple[str, str]:
    """(why_chosen, alternatives) для поля конфигурации."""
    meta = _FIELD_META.get(field)
    if meta is None:
        return ("", "")
    why = meta.why
    if field == "proxyapi_api_key" and value == "не задан":
        why = "Ключ не задан — LLM и web_search не заработают; добавьте PROXYAPI_API_KEY в backend/.env."
    elif field == "serpapi_api_key" and value == "не задан":
        why = "SerpAPI не подключён — merge URL идёт без Google News (достаточно ProxyAPI/Tavily)."
    elif field == "tavily_api_key" and value == "не задан":
        why = "Tavily не подключён — merge URL без третьего провайдера."
    elif field == "enable_web_fetch":
        why = _bool_why(
            value,
            "Включено: шаг 1 автоматически ищет и проверяет статьи по HTTP.",
            "Выключено: только seed URL из поля и Telegram; нужно ≥5 прямых ссылок.",
        )
    elif field == "step1_tier_strict_search":
        why = _bool_why(
            value,
            "Включено: поиск строго по tier-1…4 из source_tiers.txt.",
            "Выключено: общий web_search по интернету + legacy добор tier-1.",
        )
    elif field == "step1_telegram_monitor_enabled":
        why = _bool_why(
            value,
            "Включено: ссылки из telegram_monitor_channels подмешиваются как seed.",
            "Выключено: Telegram не парсится; только ручные URL и tier-поиск.",
        )
    elif field == "step1_crew_fallback_only_if_empty":
        why = _bool_why(
            value,
            "CrewAI вызывается только если веб-поиск не дал ни одного кандидата.",
            "CrewAI может добирать пул даже при частично заполненной воронке.",
        )
    elif field == "auto_run_step3_after_order":
        why = _bool_why(
            value,
            "После сохранения порядка шаг 3 запускается автоматически.",
            "Шаг 3 нужно запускать вручную после выбора порядка.",
        )
    elif field == "enable_step4_image_generation":
        why = _bool_why(
            value,
            "Генерация обложек включена — расход image-модели на шаге 4.",
            "Обложки не генерируются — быстрее финализация и меньше расход API.",
        )
    elif field == "log_enable_file":
        why = _bool_why(value, "Логи пишутся в файл с ротацией.", "Логи только в консоль процесса backend.")
    elif field == "proxyapi_web_search_enabled":
        why = _bool_why(
            value,
            "ProxyAPI web_search участвует в merge URL на шаге 1.",
            "ProxyAPI web_search отключён — остаются SerpAPI/Tavily или только seed URL.",
        )
    source_note = ""
    return (why + source_note, meta.alternatives)
