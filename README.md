# ExTellect Daily AI Digest

Веб-приложение для ежедневной генерации AI-дайджеста: **FastAPI + Next.js + CrewAI**, LLM и изображения через **ProxyAPI**, БД **SQLite** (путь задаётся `DATABASE_URL`).

## Структура проекта

```text
main.py                      # запуск backend + frontend одной командой
run-docker.bat, stop-docker.bat
push_to_github.bat, push_dockerhub.bat
docker-compose.yml
backend/
  app/
    api/
      routes_digests.py      # REST API выпусков (шаги 0–4)
      routes_config.py       # GET /config — сводка настроек для UI
      routes_source_tiers.py # редактор доменов источников (tier-1/2/3)
    config.py                # Settings: .env + pipeline_settings.json
    crew/agents.py, workflow.py, model_policy.py
    services/
      digest_service.py      # пайплайн шагов 0→4, HTTP-верификация ссылок
      news_search.py         # tier-поиск, ProxyAPI / SerpAPI / Tavily
      source_tiers_editor.py # seed-URL и tier-домены для шага 1
      step1_url_registry.py  # реестр raw/verified/reject URL между прогонами
      step1_web_search_cache.py
      step1_statistics.py, step1_statistics_insights.py
      step1_filters.py, step1_filter_settings.py, step1_filter_audit.py
      step1_curious_yield.py, curious_tone.py
      step1_search_routing.py, digest_type_policy.py, digest_defaults.py
      step1_phase_timers.py, step1_usage_breakdown.py, step1_web_search_stats.py
      step1_tiers_autoblock.py, step1_cancellation.py, step1_live_progress.py
      article_reader_fallback.py, telegram_channel_monitor.py
      platform_assembly.py, reader_copy.py, export_service.py
      usage_cost.py, cost_tracker.py, digest_pool_stats.py, digest_list.py
    pipeline_settings.json       # батчи, таймауты, telegram, лимиты ₽, логи
    step1_filter_settings.json   # порог воронки, порядок фильтров (serious/curious)
    digest_defaults.json         # дефолты шага 0
    prompts/digest_contract.txt, source_tiers.txt, curious_source_hosts.txt
    proxyapi_client.py, models.py, schemas.py, database.py
  tests/                     # pytest (461 тест)
  storage/                   # SQLite, docx, JSON (Docker: volume на хост)
  logs/
  .env.example
frontend/
  app/page.tsx               # панель выпусков
  app/digests/[id]/page.tsx  # мастер 0→4
  components/Dashboard.tsx, DigestWizard.tsx
  lib/api.ts
docs/                        # runbook и дорожки шага 1 (см. ниже)
AGENTS.md                    # подсказки для агентов Cursor
```

## Пайплайн шагов 0 → 1 → 2 → 3 → 4

Мастер — одна страница `/digests/{id}`; блоки идут сверху вниз. Статус в шапке совпадает с полем `status` в API.

| Шаг | Статус после успеха | Пользователь | Сервер |
|-----|---------------------|--------------|--------|
| 0 | `step_0` | Окно дат + тип выпуска («Серьёзный» / «Курьёзный» / «По умолчанию») | Сохраняет тип и окно; дефолт типа — **serious** (`digest_defaults.json`) |
| 1 | `step_1_candidates` | «Запустить сбор», опционально URL в поле, пересборка пула | Tier-поиск, HTTP-верификация, фильтры, пул кандидатов (мин. **10** verified) |
| 2 | `selected` | Выбор 5 → «Подтвердить 5»; порядок drag-and-drop или «Оптимально по мнению ИИ» → «Применить порядок» | Пятёрка и порядок; при смене состава/порядка сбрасываются шаги 3–4 |
| 3 | `analytics_ready` | Автозапуск после порядка или «Запустить аналитику вручную» | AnalyticsAgent: карточки по новостям + хэштеги |
| 4 | `final_ready` | Тексты площадок, копирование, «Зафиксировать выпуск» | ReaderCopyAgent + `platform_assembly.py`; тексты Telegram / MAX / VK / Дзен |

### Шаг 0

- Дефолты: **3 рабочих дня**, тип **«Серьёзный»** (`backend/app/digest_defaults.json`).
- Новый выпуск на сегодня (`POST /digests/create`) сразу проходит шаг 0 с этими дефолтами.
- Окно дат можно менять через `PATCH …/news-window` без сброса шага 0.

### Шаг 1

- При `web.enable_fetch: true` — ProxyAPI web_search по tier-политике, seed-ленты, Telegram-монитор, реестр URL, Crew fallback при нехватке verified.
- **Ручные URL** в поле шага 1 — обязательные материалы: не режутся лимитом «3 с домена» в пуле; при пересборке подтягиваются из сохранённого выпуска, если поле пустое.
- **Лимиты домена:** в пуле шага 1 — до **3** статей с одного сайта; в топ‑5 на шаге 2 — не более **2** с одного сайта.
- Остановка сбора: `POST …/step1/cancel`; прогресс: `GET …/step1/progress`.
- Статистика воронки и расходов: кнопка «Статистика шага 1» → `GET …/step1/statistics`.
- Пересборка: `POST …/step1/run` с `"rebuild": true`; частичная — с `keep_candidate_ids`.

Документация: [docs/STEP1_PIPELINE.md](docs/STEP1_PIPELINE.md), [docs/STEP1_SEARCH_FLOWCHART.md](docs/STEP1_SEARCH_FLOWCHART.md), [docs/STEP1_LINKS_RUNBOOK.md](docs/STEP1_LINKS_RUNBOOK.md).

### Шаг 2

- «Подтвердить 5» / «Оставь топ‑5» — только сохранение состава (`POST …/step2/select`).
- «Оптимально по мнению ИИ» — порядок через ProxyAPI, **не** запускает шаг 3.
- Шаг 3 стартует после **«Применить порядок»** при `auto_run_step3_after_order: true` в `pipeline_settings.json`.
- Ручной URL на шаге 2: `POST …/step2/manual-url`.

### Шаг 3

- `POST …/step3/confirm-ready` — аналитика по выбранной пятёрке.

### Шаг 4

- Последовательность в UI: `generate-images` (если включено) → `select-image` → `generate-texts`.
- Тексты под заголовком — **2–4 предложения**, до **450 символов** (без заголовка); `reader_copy.py`, `ReaderCopyAgent`.
- **Заголовки новостей** в финальных текстах — полные: при сборке шага 4 подтягиваются со страницы, если в базе сохранён укороченный вариант.
- Кнопки «Скопировать для …» — одной строкой над полями; «На главную» — в шапке мастера и в блоке копирования.
- «Зафиксировать» — `POST …/finalize` (учёт стоимости выпуска).
- Legacy: `POST …/step4/confirm-final` — тексты + обложка одним вызовом.

### Публикация на площадках

Сборка — `platform_assembly.py` (не копируйте сырой markdown из textarea для MAX/Дzen, если нужно форматирование):

| Площадка | Формат | Публикация |
|----------|--------|------------|
| Telegram | Markdown | «Скопировать для Telegram» → Ctrl+V |
| MAX | HTML (`<b>`, `<a>`, `<br>`) | Только кнопка копирования → веб-редактор MAX |
| Дзен | HTML (разделитель «—») | Только кнопка копирования |
| ВКонтакте | Plain text, CAPS, URL в «Подробности:» | «Скопировать для VK»; между новостями `· · ·` |

Подпись ExTellect: канал MAX — [max.ru/channel_extellect](https://max.ru/channel_extellect).

**Обложка** не входит в буфер с текстом: JPG в `images/` или скачивание варианта на шаге 4 при `enable_image_generation: true`. Экспорт `.docx` — архив и сверка.

При открытии выпуска тексты MAX/Дzen в устаревшем markdown пересобираются в HTML автоматически.

## API endpoints

Базовый префикс выпусков: `/digests`. Конфигурация: `/config`, `/config/source-tiers`.

**Выпуски**

- `POST /digests/create` — создать или открыть выпуск на сегодня
- `GET /digests` — список
- `GET /digests/{id}` — состояние мастера
- `POST /digests/{id}/finalize` — зафиксировать выпуск

**Шаг 0:** `POST …/step0`, `PATCH …/news-window`

**Шаг 1:** `POST …/step1/run`, `POST …/step1/cancel`, `GET …/step1/progress`, `GET|PUT …/step1/filters`, `GET …/step1/statistics`, `POST …/step1/discovered/{news_id}/feedback`, `GET /digests/step1/manual-ratings/export`

**Шаг 2:** `POST …/step2/select`, `POST …/step2/manual-url`, `POST …/step2/order`, `POST …/step2/order/ai-optimal`

**Шаг 3:** `POST …/step3/confirm-ready`

**Шаг 4:** `POST …/step4/generate-images`, `…/select-image`, `…/generate-texts`, `…/confirm-final`

**Артефакты:** `GET …/final`, `GET …/docx`, `GET …/image?variant=N`

**Служебный:** `GET /health` → `{"status":"ok"}`

## Конфигурация

### `backend/.env`

Шаблон: [backend/.env.example](backend/.env.example). Минимум:

```env
PROXYAPI_API_KEY=ваш_ключ
```

Порты, `DATABASE_URL`, лимиты шага 1 и флаги можно переопределить переменными окружения; иначе читаются из [backend/app/pipeline_settings.json](backend/app/pipeline_settings.json).

Опционально: `SERPAPI_API_KEY`, `TAVILY_API_KEY` — альтернативный поиск (`web_search_prefer_alt_providers: false` по умолчанию).

### JSON в репозитории

| Файл | Назначение |
|------|------------|
| [pipeline_settings.json](backend/app/pipeline_settings.json) | web_fetch, батчи и timebox шага 1, Telegram, лимиты ₽, crew enrich, curious_yield, автозапуск шага 3, обложки, логи |
| [step1_filter_settings.json](backend/app/step1_filter_settings.json) | фильтры, `min_discovered_pages`, `min_collection_iterations` (serious: **3**) |
| [digest_defaults.json](backend/app/digest_defaults.json) | дефолты шага 0: serious, 3 рабочих дня |

### Параметры шага 1 (актуальные значения в pipeline_settings.json)

| Параметр | Значение | Назначение |
|----------|----------|------------|
| `batch_size` | 14 | URL в tier-батче |
| `search_fetch_limit` | 64 | сырые URL за collect |
| `urls_checked_per_collect` | 48 | HTTP-проверок за проход |
| `soft_time_limit_sec` | 480 | мягкий таймаут цикла |
| `hard_time_limit_sec` | 600 | жёсткий таймаут |
| `tier_max_web_search_batches` | 8 | батчей ProxyAPI за tier-проход |
| `max_cost_rub` | 50 | бюджет ProxyAPI на шаг 1 |
| `max_candidates_for_ui` | 20 | целевой размер пула в UI |
| `verify_workers` | 8 | параллельные HTTP-проверки |
| `crew_fallback_only_if_empty` | true | Crew только если verified = 0 |
| `crew_enrich_verified_scores` | true | доп. скоринг ScoringAgent по verified |
| `tier_strict_search` | true | поиск по доменам политики |
| `telegram_monitor_enabled` | true | монитор каналов в seed |
| `telegram_via_proxyapi` | false | t.me через прямой HTTP (при timeout включите true) |

Минимум успешного пула: **10** verified (`STEP1_MIN_VERIFIED` в коде).

## Запуск

### Локально

```bash
python main.py
```

Флаги: `--no-install`, `--backend-only`, `--frontend-only`, `--reload`, `--clean-frontend`, `--force`, `--backend-port`, `--frontend-port`.

1. `backend/.env` с `PROXYAPI_API_KEY`
2. `frontend/.env.local`: `NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000`
3. [http://localhost:3000](http://localhost:3000) → «Создать или открыть сегодняшний дайджест»

Проверка: `GET http://127.0.0.1:8000/health` (заголовок `X-Request-ID` на ответах API).

### Docker

`docker compose up --build` или `run-docker.bat`. UI :3000, API :8000.

Данные на хосте:

- `backend/storage/` — SQLite `storage/digest.db`, docx, JSON, реестр URL
- `backend/logs/` — `app-YYYY-MM-DD.log`, `step1-curious-tone-*.log`

Локальный запуск без Docker по умолчанию использует `sqlite:///./digest.db` в каталоге backend (зависит от cwd).

### Docker Hub

`push_dockerhub.bat` / `scripts/push_to_dockerhub.py`, CI: `.github/workflows/docker-publish.yml`. Подробнее: [docs/DOCKERHUB_SETUP.md](docs/DOCKERHUB_SETUP.md).

## Тестирование

```bash
cd backend && python -m pytest tests -q
```

Регрессия шага 1:

```bash
cd backend && python -m pytest tests/test_step1_link_validation_smoke.py tests/test_step1_regression_pipeline.py tests/test_step1_filters_config.py tests/test_step1_statistics.py tests/test_digest_pipeline_chain.py -q
```

Сквозной офлайн 0→4 (моки): `tests/test_digest_pipeline_chain.py`.

Frontend: `cd frontend && npm run build`.

Browser E2E: `python main.py` → мастер 0→4 на `/digests/{id}`; шаг 1 — длительный запрос; шаг 4 — копирование HTML для MAX/Дzen кнопками, обложка отдельно.

См. [AGENTS.md](AGENTS.md).

## AI-агенты (CrewAI)

NewsResearchAgent, SourceVerificationAgent, ScoringAgent, OrderingAgent, AnalyticsAgent, ReaderCopyAgent, PlatformWriterAgent, ImagePromptAgent, QualityControlAgent.

Модели и тарифы — через ProxyAPI; учёт в `llm_cost_records` и `usage_cost.py`.

## Логирование

Консоль и `backend/logs/app-YYYY-MM-DD.log`. HTTP middleware с `X-Request-ID`. Модули: `app.digest`, `app.proxyapi`, `app.http`, `app.step1.curious_tone`.

## Документация

| Документ | Тема |
|----------|------|
| [AGENTS.md](AGENTS.md) | подсказки для Cursor-агентов |
| [docs/CURSOR_MODELS.md](docs/CURSOR_MODELS.md) | выбор моделей в IDE |
| [docs/STEP1_PIPELINE.md](docs/STEP1_PIPELINE.md) | дорожка шага 1 |
| [docs/STEP1_SEARCH_FLOWCHART.md](docs/STEP1_SEARCH_FLOWCHART.md) | блок-схема поиска |
| [docs/STEP1_LINKS_RUNBOOK.md](docs/STEP1_LINKS_RUNBOOK.md) | битые ссылки, серые плашки |
| [docs/STEP1_OPTIMIZATION_BASELINE.md](docs/STEP1_OPTIMIZATION_BASELINE.md) | метрики шага 1 |
| [docs/STEP1_COST_OPTIMIZATION_STEPS.md](docs/STEP1_COST_OPTIMIZATION_STEPS.md) | оптимизация стоимости |

## GitHub и порты

`push_to_github.bat "сообщение"` — см. `docs/push_protocol.log`.

Если порт занят: `python main.py --reload` для разработки; согласуйте `BACKEND_PORT`, `FRONTEND_PORT`, `NEXT_PUBLIC_API_BASE`.

## Примечания

- При `web.enable_fetch: false` для шага 1 нужны ручные URL (5–10) в `step1/run`.
- Обложки: `enable_image_generation: false` — готовые JPG в `images/`.
- В топ‑5 — только кандидаты с «Читаемый заголовок», «Ссылка рабочая», «Можно в топ‑5».
- Редактор источников (tier-домены) — в UI мастера / API `GET|PUT /config/source-tiers`.
