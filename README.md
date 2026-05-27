# ExTellect Daily AI Digest MVP

MVP веб-приложение для ежедневной генерации AI-дайджеста: **FastAPI + Next.js + CrewAI**, LLM и изображения только через **ProxyAPI**, БД SQLite (через `DATABASE_URL`).

## Структура проекта

```text
main.py                      # запуск backend + frontend одной командой
push_to_github.bat
backend/
  app/
    api/routes_digests.py    # REST API выпусков
    config.py                # Settings: .env (секреты/деплой) + pipeline_settings.json
    crew/agents.py, workflow.py
    services/
      digest_service.py      # пайплайн шагов 0→4
      news_search.py         # ProxyAPI / SerpAPI / Tavily
      telegram_channel_monitor.py  # внешние ссылки из t.me/s/
      step1_filters.py       # каталог фильтров шага 1
      step1_filter_settings.py
      digest_defaults.py     # дефолты шага 0
      export_service.py, reader_copy.py, …
    pipeline_settings.json       # батчи, таймауты, telegram, лимиты ₽, логи
    step1_filter_settings.json   # порог воронки, порядок фильтров
    digest_defaults.json
    prompts/digest_contract.txt, source_tiers.txt
    proxyapi_client.py
    models.py, schemas.py, database.py
  tests/                     # pytest (132+ тестов)
  .env.example
frontend/
  app/page.tsx               # панель выпусков
  app/digests/[id]/page.tsx  # мастер
  components/Dashboard.tsx, DigestWizard.tsx
  lib/api.ts
docs/
  STEP1_PIPELINE.md          # дорожка шага 1
  STEP1_LINKS_RUNBOOK.md     # битые ссылки, серые плашки
AGENTS.md                    # подсказки для агентов Cursor
```

## Пайплайн шагов 0 → 1 → 2 → 3 → 4

Мастер — одна страница `/digests/{id}`; блоки идут сверху вниз. Статус в шапке совпадает с полем `status` в API.

| Шаг | Статус после успеха | Что делает пользователь | Что делает сервер |
|-----|---------------------|-------------------------|-------------------|
| 0 | `step_0` | Окно дат + «Серьёзный» / «Курьёзный» / «По умолчанию» | Сохраняет тип и окно новостей |
| 1 | `step_1_candidates` | «Запустить сбор кандидатов» (опц. manual URL, пересборка пула) | Поиск URL, HTTP-верификация, пул кандидатов |
| 2 | `selected` | **Выбор:** отметить 5 → «Подтвердить 5» / «Оставь топ‑5». **Порядок:** drag-and-drop → «Применить порядок» или «Оптимально по мнению ИИ»; **«Изменить порядок»** — если аналитика уже была | Сохраняет пятёрку, порядок, обоснования ИИ; при смене пятёрки/порядка сбрасывает шаги 3–4 |
| 3 | `analytics_ready` | Обычно автоматически после сохранения порядка; иначе «Запустить аналитику вручную» / «Повторить аналитику» | AnalyticsAgent: редакторские карточки по каждой новости + хэштеги |
| 4 | `final_ready` | Обложки (если включены) → тексты площадок → `.docx` | ReaderCopyAgent: простые тексты для читателей (до 450 символов без заголовка); сборка Telegram / MAX / VK / Дзен |

**Важно:**

- Окно дат передаётся при каждом `POST …/step1/run` и может быть изменено через `PATCH …/news-window` без сброса шага 0.
- **Выбор пятёрки** (`POST …/step2/select`) не запускает аналитику — только сохраняет состав. **Порядок** (`…/step2/order` или `…/order/ai-optimal`) сохраняет расстановку и обоснования; при `AUTO_RUN_STEP3_AFTER_ORDER=true` (по умолчанию) сразу запускает шаг 3.
- Пятёрку можно **перевыбрать на любом этапе** после шага 1 — аналитика и финал сбросятся. **Порядок** после шага 3 меняют кнопкой «Изменить порядок» в блоке drag-and-drop.
- «Оптимально по мнению ИИ» возвращает общую аргументацию порядка и пояснение к каждой позиции.
- Тексты под заголовком новости на площадках — **2–4 простых предложения**, разговорный русский, **не более 450 символов** (без учёта заголовка); формируются на шаге 4 (`reader_copy.py`, `ReaderCopyAgent`).
- Шаг 4 в UI: `generate-images` → `select-image` → `generate-texts`. Эндпоинт `confirm-final` — legacy-монолит.
- «Пересобрать пул кандидатов» → `POST …/step1/run` с `"rebuild": true` (частичная пересборка с сохранением отмеченных — через `keep_candidate_ids`).

Подробности шага 1: [docs/STEP1_PIPELINE.md](docs/STEP1_PIPELINE.md).  
Битые ссылки: [docs/STEP1_LINKS_RUNBOOK.md](docs/STEP1_LINKS_RUNBOOK.md).

## API endpoints

Базовый префикс: `/digests` (см. `backend/app/main.py`).

**Выпуски**

- `POST /digests/create` — создать или вернуть выпуск на сегодня
- `GET /digests` — список выпусков
- `GET /digests/{id}` — полное состояние мастера

**Шаг 0**

- `POST /digests/{id}/step0` — тип + окно дат
- `PATCH /digests/{id}/news-window` — только окно дат

**Шаг 1**

- `POST /digests/{id}/step1/run` — сбор пула (`manual_urls`, `rebuild`, `news_window_*`)
- `GET|PUT /digests/{id}/step1/filters` — настройки фильтров (UI «Настройки фильтра новостей»)
- `POST /digests/{id}/step1/discovered/{news_id}/feedback` — ручная оценка URL
- `GET /digests/step1/manual-ratings/export` — экспорт оценок (JSON)

**Шаг 2**

- `POST /digests/{id}/step2/select` — сохранить пятёрку (`selected_ids` или `top5: true`); доступен на этапах `step_1_candidates`, `selected`, `analytics_ready`, `final_ready`
- `POST /digests/{id}/step2/order` — сохранить порядок после drag-and-drop + опционально авто-шаг 3
- `POST /digests/{id}/step2/order/ai-optimal` — оптимальный порядок через ProxyAPI (gpt-4.1-mini), с общей аргументацией и `ordering_reason` по позициям

**Шаг 3**

- `POST /digests/{id}/step3/confirm-ready` — аналитика (`command`: пусто или «готово»)

**Шаг 4**

- `POST /digests/{id}/step4/generate-images` — 4 варианта обложки (если `ENABLE_STEP4_IMAGE_GENERATION=true`)
- `POST /digests/{id}/step4/select-image` — выбор варианта 1–4
- `POST /digests/{id}/step4/generate-texts` — тексты площадок + `.docx`
- `POST /digests/{id}/step4/confirm-final` — legacy: тексты + обложка v1 одним вызовом

**Артефакты**

- `GET /digests/{id}/final` — финальные тексты
- `GET /digests/{id}/docx` — скачать документ
- `GET /digests/{id}/image?variant=N` — обложка

**Служебный**

- `GET /health` — `{"status":"ok"}`

## Конфигурация

### `backend/.env` — только секрет

Шаблон: [backend/.env.example](backend/.env.example).

**Достаточно одной строки:**

```env
PROXYAPI_API_KEY=ваш_ключ
```

Порты, модели, web_search, лимиты шага 1, `web.enable_fetch` и логи берутся из `config.py` и [backend/app/pipeline_settings.json](backend/app/pipeline_settings.json). Переменные в `.env` **не обязательны** — они лишь перекрывают JSON при необходимости (например `STEP1_HARD_TIME_LIMIT_SEC=900`).

### JSON в репозитории — поведение пайплайна

| Файл | Содержимое |
|------|------------|
| [backend/app/pipeline_settings.json](backend/app/pipeline_settings.json) | `web.enable_fetch`, батчи и timebox шага 1, workers, Telegram, лимиты ₽, автозапуск шага 3, обложки шага 4, логи |
| [backend/app/step1_filter_settings.json](backend/app/step1_filter_settings.json) | фильтры, `min_discovered_pages`, `min_collection_iterations` (UI «Настройки фильтра») |
| [backend/app/digest_defaults.json](backend/app/digest_defaults.json) | дефолты шага 0 (тип выпуска, окно дат) |

## Запуск

Из корня репозитория:

```bash
python main.py
```

Опции: `--no-install`, `--backend-only`, `--frontend-only`, `--reload`, `--backend-port N`, `--frontend-port N`.

1. Скопируйте `backend/.env.example` → `backend/.env`, задайте `PROXYAPI_API_KEY`.
2. `frontend/.env.local`: `NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000`
3. Откройте [http://localhost:3000](http://localhost:3000) → «Создать или открыть сегодняшний дайджест».

Проверка backend: `GET http://127.0.0.1:8000/health`. На ответах смотрите заголовок `X-Request-ID`.

Если порты заняты — см. раздел «Если порт занят» ниже (без изменений по смыслу).

## Тестирование

**Полный набор backend:**

```bash
cd backend
python -m pytest tests -q
```

**Регрессия шага 1 (ссылки, фильтры, окно дат):**

```bash
cd backend
python -m pytest tests/test_step1_link_validation_smoke.py tests/test_step1_regression_pipeline.py tests/test_step1_filters_config.py tests/test_news_window_sync.py tests/test_telegram_channel_monitor.py tests/test_digest_pipeline_chain.py -q
```

**Сквозной офлайн-тест 0→4** (моки ProxyAPI/Crew, без live API): `tests/test_digest_pipeline_chain.py`.

**Frontend:**

```bash
cd frontend
npm run build
```

**Browser E2E (ручной чеклист):**

1. Поднять `python main.py`, открыть мастер.
2. Шаг 0 → шаг 1 (дождаться пула) → выбор 5 («Подтвердить 5») → порядок («Применить порядок» или «Оптимально по мнению ИИ») → дождаться аналитики → тексты шага 4.
3. Проверить «Скопировать текст» по площадкам, «Скачать .docx» и блок «Шаг 4 — результат».

См. также [AGENTS.md](AGENTS.md) § «Инспекция / регрессия».

## AI-агенты

CrewAI-агенты: `NewsResearchAgent`, `SourceVerificationAgent`, `ScoringAgent`, `OrderingAgent`, `AnalyticsAgent`, `PlatformWriterAgent`, `ImagePromptAgent`, `QualityControlAgent`.

Рекомендуемые модели и тарифы — в UI мастера и через ProxyAPI pricing; учёт стоимости в `llm_cost_records`.

## Логирование

Консоль и `backend/logs/app-YYYY-MM-DD.log`. HTTP: middleware с `X-Request-ID`. Модули: `app.digest`, `app.proxyapi`, `app.http`.

## Push в GitHub

`push_to_github.bat "сообщение коммита"` — см. существующий скрипт и `docs/push_protocol.log`.

## Если порт занят или UI ходит не в тот backend

- `python main.py` поднимает uvicorn без `--reload` (один процесс). Для разработки: `python main.py --reload`.
- Согласуйте `BACKEND_PORT`, `FRONTEND_PORT`, `FRONTEND_ORIGIN` и `NEXT_PUBLIC_API_BASE`.
- PowerShell: `Get-NetTCPConnection -LocalPort <порт> | Select-Object OwningProcess`, затем `Stop-Process -Id <PID> -Force`.

## Примечания

- При `web.enable_fetch: false` в `pipeline_settings.json` для шага 1 нужны ручные URL (5–10) в теле `step1/run`.
- При `web.enable_fetch: true` — ProxyAPI web_search, опционально SerpAPI/Tavily, ссылки из Telegram (`t.me/s/`), затем Crew fallback.
- Обложки шага 4: `ENABLE_STEP4_IMAGE_GENERATION=false` по умолчанию — только тексты и `.docx`.
- В топ‑5 попадают только кандидаты с «Читаемый заголовок», «Ссылка рабочая», «Можно в топ‑5».
- Для выбора пятёрки достаточно **5 подходящих** кандидатов в пуле (не обязательно 10 строк в списке).
- Разделитель между новостями во ВКонтакте в финальных текстах — `· · ·` (не длинная черта).
