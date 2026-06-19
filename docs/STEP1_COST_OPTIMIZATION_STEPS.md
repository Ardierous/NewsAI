# Оптимизация стоимости шага 1

Текущая конфигурация экономии ProxyAPI и внешних API. Основной источник — [backend/app/pipeline_settings.json](../backend/app/pipeline_settings.json).

## Принципы

1. **ProxyAPI web_search** — основной источник URL; SerpAPI/Tavily выключены по умолчанию.
2. **Context size `low`** — для tier-батчей и supplement; `medium` включается только в short-pool режиме (verified < 10).
3. **Telegram direct** — парсинг `t.me/s/` без ProxyAPI.
4. **Кэш web_search** — повторные запросы не тарифицируются повторно (TTL 90 д).
5. **Strict citations** — для serious tier_strict URL только из citations; меньше HTTP на выдуманные ссылки.
6. **Лимит tier-батчей** — до 12 за проход; supplement ограничен при нехватке пула.

## Текущие параметры

| Параметр | Значение | Эффект |
|----------|----------|--------|
| `max_cost_rub` | 50 | Потолок ₽ на шаг 1 |
| `web_search_context_size` | low | Меньше токенов на батч |
| `web_search_context_size_supplement` | low | То же для добора |
| `tier_max_web_search_batches` | 12 | Лимит ProxyAPI-вызовов за tier-проход |
| `web_search_prefer_alt_providers` | false | Только ProxyAPI (без SerpAPI/Tavily) |
| `web_search_cache_enabled` | true | Кэш ответов 90 д |
| `telegram_via_proxyapi` | false | Telegram без ProxyAPI |
| `telegram_direct_fallback` | true | Прямой HTTP к t.me/s/ |
| `telegram_proxyapi_context_size` | low | На случай fallback через ProxyAPI |
| `crew_fallback_only_if_empty` | false | Crew только при verified < 10, не «если пусто» |

## Альтернативные провайдеры (опционально)

Код SerpAPI/Tavily остаётся в репозитории. Для включения:

1. Добавить ключи в `backend/.env`: `SERPAPI_API_KEY`, `TAVILY_API_KEY`.
2. Установить `"web_search_prefer_alt_providers": true` в `pipeline_settings.json`.

Без ключей экономия идёт за счёт параметров таблицы выше.

## Реестр URL

`step1_url_registry_reuse_enabled: true` (config.py) — переиспользование проверенных URL между прогонами снижает число web_search-вызовов. При нехватке пула (verified < 10) реестр **не** используется как основной seed — приоритет свежему поиску.

## Тесты

```bash
cd backend
python -m pytest tests/test_step1_cost_optimization.py tests/test_news_search.py tests/test_step1_web_search_cache.py tests/test_pipeline_settings.py -q
```

Ключевые тесты:

- `test_step1_config_economy_defaults` — параметры экономии в конфиге
- `test_tier_search_respects_max_batches` — лимит tier-батчей
- `test_log_proxyapi_usage_reads_cached_tokens` — учёт cached_tokens

## Живой прогон

1. Перезапустить backend.
2. Новый выпуск шаг 0→1.
3. Смотреть `backend/logs/app-*.log`:
   - `ProxyAPI usage | … cached_tokens=…`
   - `Tier-поиск: … batches=N/12`
   - `Web search: … citations=…`
4. `GET /digests/{id}/step1/statistics` — `proxyapi_cost_rub`, воронка.

## Связанные документы

- [STEP1_OPTIMIZATION_BASELINE.md](STEP1_OPTIMIZATION_BASELINE.md) — целевые метрики
- [STEP1_PIPELINE.md](STEP1_PIPELINE.md) — полная дорожка шага 1
