# AGENTS.md — подсказки для агентов Cursor

## Нерабочие ссылки / серые карточки на шаге 1

**Обязательный документ:** [docs/STEP1_LINKS_RUNBOOK.md](docs/STEP1_LINKS_RUNBOOK.md)

Используйте при запросах про битые URL, серые плашки, 502 на `POST /digests/{id}/step1/run`, расхождение заголовка и верификации.

**Не делайте без runbook:**

- не ослабляйте проверки «чтобы прошло больше кандидатов», без кода причины отказа;
- не возвращайте доверие к `url`/`title` от CrewAI без `_verify_llm_candidate_dict`;
- не удаляйте `_filter_score_url_mutations` и не принимайте URL из ScoringAgent.

**Быстрые команды после правок шага 1:**

```bash
cd backend
python -m pytest tests/test_step1_link_validation_smoke.py tests/test_step1_regression_pipeline.py -q
```

**Главный код шага 1:** `backend/app/services/digest_service.py` — `run_step_1`, `_fetch_article_page_bundle`, `_choose_coherent_headline`, `_verify_llm_candidate_dict`, `_redirect_should_reject`, `_page_is_article_like`, `_filter_score_url_mutations`.

**Смежные модули:**

| Модуль | Назначение |
|--------|------------|
| `news_search.py` | tier-поиск, prefilter, strict citations |
| `proxyapi_client.py` | ProxyAPI web_search, chat |
| `source_tiers_editor.py` | seed-URL, tier-домены, skip redundant homepage |
| `step1_url_registry.py` | реестр URL между прогонами |
| `step1_web_search_cache.py` | кэш web_search (TTL 90 д) |
| `step1_filters.py`, `step1_filter_settings.json` | каталог и конфиг фильтров |
| `step1_statistics.py`, `step1_statistics_insights.py` | воронка и insights для UI |
| `step1_curious_yield.py`, `curious_tone.py` | курьёзный сбор и тон |
| `step1_cancellation.py`, `step1_live_progress.py` | отмена и live-прогресс |
| `platform_assembly.py` | финальные тексты Telegram/MAX/VK/Дzen |
| `article_reader_fallback.py` | reader proxy (r.jina.ai) при антиботе |

**Поведение, которое важно не ломать:**

- Ручные URL (`MANUAL_REQUIRED`) — обязательные, не режутся host cap в пуле; при пустом поле пересборки подтягиваются из БД.
- Пул шага 1: до **3** URL с домена; топ‑5 шага 2: до **2** с домена (`STEP2_MAX_PER_SOURCE`).
- Заголовки в финальных текстах — полные: `_choose_coherent_headline` предпочитает длинный h1; на шаге 4 `_refresh_truncated_candidate_title` дочитывает со страницы.

## Инспекция / регрессия

Полное описание — [README.md](README.md) § «Тестирование».

```bash
# Backend: все тесты (461)
cd backend && python -m pytest tests -q

# Сквозной офлайн 0→4 (моки, без live ProxyAPI)
cd backend && python -m pytest tests/test_digest_pipeline_chain.py -q

# Frontend
cd frontend && npm run build

# Smoke backend
curl http://127.0.0.1:8000/health
```

**Browser E2E:** `python main.py` → мастер на `/digests/{id}`. Шаг 1 — длительный запрос. Шаг 2: «Подтвердить 5» → порядок → «Применить порядок» (не «Оптимально по мнению ИИ» для запуска шага 3). Шаг 4: копирование MAX/Дzen кнопками в строке над полями; обложка отдельно; «На главную» в шапке и в блоке копирования.

## Модели Cursor (IDE)

[docs/CURSOR_MODELS.md](docs/CURSOR_MODELS.md), правила `.cursor/rules/cursor-models*.mdc`.

## Прочее

- Запуск, Docker, `.env`: [README.md](README.md)
- Пайплайн: `backend/app/pipeline_settings.json`, `backend/app/digest_defaults.json`
- Дорожка шага 1: [docs/STEP1_PIPELINE.md](docs/STEP1_PIPELINE.md)
- Контракт дайджеста: `backend/app/prompts/digest_contract.txt`
- Вёрстка финала: `backend/app/services/platform_assembly.py` (MAX канал: `https://max.ru/channel_extellect`)
