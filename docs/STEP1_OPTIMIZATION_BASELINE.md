# Метрики и ориентиры шага 1

Справочник по ключевым показателям прогона шага 1. Данные берутся из `assets.type = step1_collection_meta` в SQLite и из `GET /digests/{id}/step1/statistics`.

## Целевые показатели (текущая конфигурация)

| Метрика | Ориентир | Источник |
|---------|----------|----------|
| `verified_total` | **10** (минимум) или честный **502** | `STEP1_MIN_VERIFIED` |
| Размер пула в UI | **10–12** | `max_candidates_for_ui: 12` |
| `elapsed_sec` | **8–12 мин** при нормальной конверсии | `soft: 480`, `hard: 600` с |
| `urls_sent_to_http` | **30–48** за прогон | `urls_checked_per_collect: 48` |
| `urls_raw_merged` | **40–64** | `search_fetch_limit: 64` |
| ProxyAPI шаг 1 | **35–55 ₽** | `max_cost_rub: 50` |
| `stop_reason` | `target_min_met`, `soft_timeout_target_met` | не `hard_timeout` на типичном выпуске |
| `conversion_e2e_pct` | **15–30%** (verified / raw) | зависит от окна дат и tier |

## Поля `step1_collection_meta`

| Поле | Смысл |
|------|--------|
| `elapsed_sec` | Длительность прогона |
| `iterations` | Число итераций web-цикла |
| `verified_total` | Подтверждённых материалов в пуле |
| `urls_raw_merged` | Сырых URL после tier-поиска |
| `urls_prefilter_rejected` | Отсеяно на prefilter |
| `urls_sent_to_http` | Отправлено на HTTP-верификацию |
| `stop_reason` | Причина остановки (`hard_timeout`, `target_min_met`, …) |
| `conversion_e2e_pct` | verified / raw × 100 |
| `proxyapi_cost_rub` | Стоимость ProxyAPI за прогон |

## Типичные `stop_reason`

| Значение | Значение для оператора |
|----------|------------------------|
| `target_min_met` | Набрано ≥10 verified, цель достигнута |
| `soft_timeout_target_met` | Soft-таймаут, но пул ≥10 |
| `hard_timeout` | Жёсткий лимит 600 с; проверить конверсию и окно дат |
| `hard_timeout_after_collect` | Таймаут после collect-фазы |
| `no_progress` | Две итерации без прироста verified |
| `user_cancelled` | Пользователь нажал «Остановить» |
| `budget_exceeded` | Исчерпан `max_cost_rub` |

## Типичные коды отбраковки

| Код | Когда доминирует |
|-----|------------------|
| `published_before_window` | Устаревшие URL в выдаче или реестре |
| `news_listing_page` | Много лент вместо статей |
| `http_unreachable` | Битые или заблокированные URL |
| `off_topic_not_ai` | Нерелевантная тематика |
| `non_policy_source` | URL вне tier-политики |

Подробнее: [STEP1_LINKS_RUNBOOK.md](STEP1_LINKS_RUNBOOK.md).

## Как снять метрики

1. Прогон шага 1 в UI → «Зафиксировать» не обязателен для meta.
2. `GET /digests/{id}/step1/statistics` — воронка, стоимость, insights.
3. SQL: `SELECT content FROM assets WHERE digest_id = ? AND type = 'step1_collection_meta' ORDER BY id DESC LIMIT 1`.
4. Лог: `backend/logs/app-YYYY-MM-DD.log` — строки `Шаг 1:` и `Tier-поиск:`.

## Связанные документы

- [STEP1_PIPELINE.md](STEP1_PIPELINE.md) — параметры пайплайна
- [STEP1_COST_OPTIMIZATION_STEPS.md](STEP1_COST_OPTIMIZATION_STEPS.md) — настройки экономии
- [STEP1_SEARCH_FLOWCHART.md](STEP1_SEARCH_FLOWCHART.md) — где тратится время
