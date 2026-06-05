# Оптимизация стоимости шага 1 — шаги 1–3

Проверка после каждого шага: один прогон шага 1 → «Зафиксировать» → сравнить с [STEP1_OPTIMIZATION_BASELINE.md](STEP1_OPTIMIZATION_BASELINE.md) (~80 ₽).

## Шаг 1 — конфиг (сделано)

| Параметр | Было | Стало |
|----------|------|-------|
| `max_cost_rub` | 50 | **40** |
| `web_search_context_size` | medium (.env) | **low** (pipeline) |
| `telegram_via_proxyapi` | true | **false** |
| `telegram_direct_fallback` | false | **true** |
| `telegram_proxyapi_context_size` | medium | **low** |

**Тест:** `pytest tests/test_step1_cost_optimization.py::test_step1_config_economy_defaults -q`

**Живой прогон:** перезапуск backend, новый выпуск шаг 0→1, кнопка «Зафиксировать». Ожидание: **−10…25 ₽** (без ProxyAPI-Telegram).

## Шаг 2 — лимит tier-батчей + лог кэша (сделано)

| Параметр | Значение |
|----------|----------|
| `tier_max_web_search_batches` | **6** за один tier-проход |
| Логи | `ProxyAPI usage \| … cached_tokens=…` ([кэш промптов](https://proxyapi.ru/docs/openai-prompt-caching)) |

**Тест:** `pytest tests/test_step1_cost_optimization.py::test_tier_search_respects_max_batches tests/test_step1_cost_optimization.py::test_log_proxyapi_usage_reads_cached_tokens -q`

**Живой прогон:** смотреть `backend/logs/app-*.log` — `batches=6/6`, меньше строк `ProxyAPI web_search`. Ожидание: **−15…30 ₽** к шагу 1.

## Шаг 3 — альт. провайдеры (SerpAPI/Tavily) — **выключено**

| Параметр | Значение |
|----------|----------|
| `web_search_prefer_alt_providers` | **false** (режим «только ProxyAPI») |
| `SERPAPI_API_KEY` / `TAVILY_API_KEY` | не нужны |

Код шага 3 остаётся в репозитории: при появлении ключей можно включить `web_search_prefer_alt_providers: true`.  
Без ключей экономия идёт за счёт шагов 1–2 (low, Telegram direct, лимит 6 батчей, ранний стоп).

**Тест:** `pytest tests/test_step1_cost_optimization.py -q`

## Шаг 4 — не делаем

Кэш сырых URL в приложении (SQLite) — отложен.

## Полный pytest

```bash
cd backend
python -m pytest tests/test_step1_cost_optimization.py tests/test_news_search.py tests/test_pipeline_settings.py -q
```
