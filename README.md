# ExTellect Daily AI Digest MVP

MVP веб-приложение для ежедневной генерации AI-дайджеста на базе CrewAI + FastAPI + Next.js, с LLM/изображениями только через ProxyAPI.

## Структура проекта

```text
main.py
push_to_github.bat
backend/
  app/
    api/routes_digests.py
    crew/agents.py
    crew/workflow.py
    services/digest_service.py
    services/export_service.py
    proxyapi_client.py
    prompts/digest_contract.txt
    models.py
    schemas.py
    database.py
    logging_config.py
    middleware/request_logging.py
    main.py
  requirements.txt
  .env.example
frontend/
  app/page.tsx
  app/digests/[id]/page.tsx
  components/Dashboard.tsx
  components/DigestWizard.tsx
  lib/api.ts
docs/
  STEP1_PIPELINE.md        # дорожка шага 1: поиск, верификация и формирование пула 10
  STEP1_LINKS_RUNBOOK.md   # runbook: битые ссылки, серые плашки, 502 на шаге 1
  push_protocol.log
AGENTS.md                    # указатель для агентов Cursor (см. runbook выше)
```

## Что реализовано

- Pipeline шагов `0 -> 1 -> 2 -> 3 -> 4`
- Жесткие переходы:
  - Step3 только после команды `готово`
  - Step4 только после команды `Ок`
- CrewAI-агенты:
  - `NewsResearchAgent`
  - `SourceVerificationAgent`
  - `ScoringAgent`
  - `OrderingAgent`
  - `AnalyticsAgent`
  - `PlatformWriterAgent`
  - `ImagePromptAgent`
  - `QualityControlAgent`
- ProxyAPI-only интеграция:
  - текст: OpenAI-compatible `chat.completions` / `responses`
  - изображения: `images.generate` (`1200x630`)
- БД SQLite (через `DATABASE_URL`, легко заменить на PostgreSQL)
- Планировщик APScheduler (ежедневное создание черновика)
- Экспорт `.docx` через `python-docx`
- Frontend-мастер со строгой последовательностью шагов

## API endpoints

- `POST /digests/create`
- `GET /digests`
- `GET /digests/{id}`
- `POST /digests/{id}/step0`
- `POST /digests/{id}/step1/run`
- `POST /digests/{id}/step2/select`
- `POST /digests/{id}/step2/order`
- `POST /digests/{id}/step3/confirm-ready`
- `POST /digests/{id}/step4/confirm-final`
- `GET /digests/{id}/final`
- `GET /digests/{id}/docx`
- `GET /digests/{id}/image`

## AI-агенты проекта

1. `NewsResearchAgent`
   - **Роль:** поиск свежих новостей об ИИ.
   - **Функции:**
     - собирает 10 кандидатов за целевое окно времени;
     - формирует поля: заголовок, URL, источник, дата, категория, описание;
     - учитывает требования контракта дайджеста к структуре входных данных.

1. `SourceVerificationAgent`
   - **Роль:** верификация источников и пригодности ссылок.
   - **Функции:**
     - проверяет корректность ссылок и качество источников;
     - помечает агрегаторы/дубли/сомнительные публикации;
     - обновляет поля надежности и комментарии проверки.

1. `ScoringAgent`
   - **Роль:** скоринг и баланс подборки.
   - **Функции:**
     - выставляет оценки значимости, новизны и влияния;
     - рассчитывает общий балл;
     - помогает удерживать баланс категорий итогового пула.

1. `OrderingAgent`
   - **Роль:** оптимальная расстановка выбранных 5 новостей.
   - **Функции:**
     - переставляет только порядок (без смены состава);
     - формирует позицию вывода для каждой новости;
     - добавляет краткую причину выбранного порядка.

1. `AnalyticsAgent`
   - **Роль:** подготовка аналитики выпуска.
   - **Функции:**
     - генерирует карточку аналитики по каждой выбранной новости;
     - формирует общий аналитический вывод по выпуску;
     - формирует набор релевантных хэштегов и self-check блок.

1. `PlatformWriterAgent`
   - **Роль:** подготовка финальных текстов для платформ.
   - **Функции:**
     - создаёт отдельные блоки для Telegram, MAX, ВКонтакте и Дзен;
     - соблюдает форматные ограничения каждой платформы;
     - при необходимости перегенерирует проблемные блоки (repair режим).

1. `ImagePromptAgent`
   - **Роль:** подготовка визуальной части выпуска.
   - **Функции:**
     - генерирует prompt для обложечного изображения;
     - учитывает стиль/ограничения контракта;
     - используется перед генерацией изображения через ProxyAPI.

1. `QualityControlAgent`
   - **Роль:** финальная самопроверка качества.
   - **Функции:**
     - проверяет обязательные критерии качества финала;
     - возвращает чек-лист статусов и комментарии;
     - инициирует повторную сборку проблемного блока при fail.

## Шаги пайплайна 0 → 1 → 2 → 3 → 4

### Шаг 0 — выбор типа дайджеста

- **Назначение:** определить тональность выпуска (`serious` / `curious`) и зафиксировать стартовое состояние.
- **Функции:**
  - выбор типа вручную или по умолчанию (будни/выходные);
  - сохранение типа и статуса выпуска в БД.
- **Исполнители (ИИ-агенты):** не используется (системная логика).
- **Условия входа:** выпуск создан (`draft`).
- **Условия выхода:** тип выбран, выпуск готов к переходу на Шаг 1.

### Шаг 1 — сбор и верификация 10 кандидатов

- **Назначение:** собрать пул новостей-кандидатов и подготовить его к редакторскому выбору.
- **Функции:**
  - поиск 10 кандидатов по теме ИИ;
  - проверка источников/ссылок/дубликатов;
  - скоринг (значимость/новизна/влияние) и итоговый балл.
  - поддержка ручного ввода ссылок с приоритетом (ручные ссылки включаются в пул кандидатов как обязательные).
- **Исполнители (ИИ-агенты):**
  - `NewsResearchAgent`
  - `SourceVerificationAgent`
  - `ScoringAgent`
- **Условия входа:** завершён Шаг 0.
- **Условия выхода:** сохранены 10–15 кандидатов (минимум 10), выпуск готов к переходу на Шаг 2.
  - если веб-поиск недоступен, система просит вставить вручную 5–10 ссылок;
  - если ручные ссылки переданы, они используются обязательно, а остальные кандидаты добираются стандартным алгоритмом (когда доступен).
- **Пересборка пула** (после шагов 2–4): кнопка «Пересобрать пул кандидатов» → `POST /digests/{id}/step1/run` с `"rebuild": true` (сбрасывает выбор, аналитику, финал).

**Полная дорожка** (итерации по 20 URL → `verified_pool` → rebalance → 10–15 в БД): **[docs/STEP1_PIPELINE.md](docs/STEP1_PIPELINE.md)**.

#### Настройки шага 0

| Файл | Что задаёт |
|------|------------|
| `backend/app/digest_defaults.json` | Дефолты окна дат на шаге 0 (`digest_type_default`, `news_window_*`) |
| `backend/.env` | `ENABLE_WEB_FETCH`, лимиты ₽, ключи поиска |

Шаг 1 работает итерациями: батчи URL из поиска, верификация каждой страницы, накопление валидных материалов. При достижении минимума формируется пул 10–15 кандидатов с квотами rebalance. Подробности — в [STEP1_PIPELINE.md](docs/STEP1_PIPELINE.md).

#### Если ссылки «не работают» или почти все карточки серые

См. **[docs/STEP1_LINKS_RUNBOOK.md](docs/STEP1_LINKS_RUNBOOK.md)** — симптомы, коды `REJECT_REASON:*`, инварианты HTTP-проверки, тесты и типовые регрессии.  
Для агентов Cursor: **[AGENTS.md](AGENTS.md)**.

Кратко: **рабочая ссылка** = страница открылась на сервере (`_fetch_article_page_bundle`) и прошла `_verify_llm_candidate_dict`; URL из CrewAI без HTTP-проверки не считается рабочим.

### Шаг 2 — выбор 5 новостей и упорядочивание

- **Назначение:** зафиксировать итоговую пятёрку и оптимальный порядок публикации.
- **Функции:**
  - выбор ровно 5 новостей пользователем (или `top-5`);
  - перестановка порядка drag-and-drop;
  - объяснение порядка от агента.
- **Исполнители (ИИ-агенты):**
  - `OrderingAgent`
- **Условия входа:** завершён Шаг 1 (есть 10–15 кандидатов).
- **Условия выхода:** сохранены 5 новостей и порядок, выпуск готов к переходу на Шаг 3.

### Шаг 3 — аналитика выпуска

- **Назначение:** сформировать аналитическую часть по каждой новости и общий вывод выпуска.
- **Функции:**
  - аналитика по каждой новости (суть, комментарий, последствия);
  - общий аналитический вывод;
  - генерация хэштегов и self-check таблицы.
- **Исполнители (ИИ-агенты):**
  - `AnalyticsAgent`
- **Условия входа:** завершён Шаг 2 и получена команда пользователя `готово`.
- **Условия выхода:** аналитика сохранена, выпуск готов к переходу на Шаг 4.

### Шаг 4 — финальная сборка

- **Назначение:** собрать финальные тексты для платформ и финальные артефакты выпуска.
- **Функции:**
  - A/B/V выбор крючка;
  - генерация prompt и изображения;
  - генерация блоков Telegram/MAX/ВКонтакте/Дзен;
  - финальная проверка качества и repair при необходимости;
  - сохранение результатов, экспорт `.docx`.
- **Исполнители (ИИ-агенты):**
  - `ImagePromptAgent`
  - `PlatformWriterAgent`
  - `QualityControlAgent`
- **Условия входа:** завершён Шаг 3 и получена команда пользователя `Ок`.
- **Условия выхода:** выпуск в `final_ready`, доступны финальные блоки, изображение и `.docx`.

## Запуск (локально)

### Быстрый запуск

Из корня репозитория:

```bash
python main.py
```

Опции: `--no-install` (без `pip install` / `npm install`), `--backend-only`, `--frontend-only`, `--reload` (uvicorn с автоперезапуском для разработки), `--backend-port N`, `--frontend-port N`. Порты также читаются из переменных окружения `BACKEND_PORT` / `FRONTEND_PORT` и из `backend/.env` (см. ниже).

1. Создайте `.env` в папке `backend` по шаблону `backend/.env.example`:

```env
PROXYAPI_API_KEY=your_key_here
PROXYAPI_BASE_URL=https://openai.api.proxyapi.ru/v1
PROXYAPI_MODEL=openai/gpt-4.1
PROXYAPI_IMAGE_MODEL=openai/gpt-image-1
DATABASE_URL=sqlite:///./digest.db
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000
FRONTEND_PORT=3000
FRONTEND_ORIGIN=http://localhost:3000
ENABLE_WEB_FETCH=false
# Параметры итеративного шага 1 (опционально)
# STEP1_BATCH_SIZE=20
# STEP1_SOFT_TIME_LIMIT_SEC=180
# STEP1_HARD_TIME_LIMIT_SEC=300
# STEP1_MAX_CANDIDATES_FOR_UI=15

LOG_LEVEL=INFO
LOG_ENABLE_FILE=true
LOG_FILE_NAME=app.log
LOG_MAX_BYTES=5000000
LOG_BACKUP_COUNT=5
```

1. Установите зависимости backend:

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

1. Запустите backend:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Для разработки с автоперезапуском при изменении кода добавьте флаг `--reload` (на Windows это создаёт дополнительный процесс на том же порту).

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

1. Установите зависимости frontend:

```bash
cd ../frontend
npm install
```

1. Создайте `frontend/.env.local`:

```env
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000
```

1. Запустите frontend:

```bash
npm run dev
```

Порт по умолчанию — 3000. Другой порт: `npm run dev -- -p 3001` (и обновите `FRONTEND_ORIGIN` в `backend/.env`).

1. Откройте интерфейс:

- [http://localhost:3000](http://localhost:3000) (или порт из `FRONTEND_PORT` / `--frontend-port`)
- Нажмите **Создать сегодняшний дайджест**
- Пройдите шаги мастера по порядку.

## Если порт занят или UI ходит не в тот backend

- По умолчанию `python main.py` поднимает **uvicorn без `--reload`** (один процесс на порт; на Windows так проще освободить порт и не путать PID). Для автоперезапуска при правках кода: `python main.py --reload`.
- **Порты:** в `backend/.env` задайте `BACKEND_PORT` и при необходимости `FRONTEND_PORT`, либо экспортируйте те же переменные в окружение; можно вызвать `python main.py --backend-port 8001 --frontend-port 3001`. Если backend не на `8000`, во `frontend/.env.local` укажите тот же хост и порт в `NEXT_PUBLIC_API_BASE` (шаблон: `frontend/.env.local.example`). Если фронт не на `3000`, обновите `FRONTEND_ORIGIN` в `backend/.env`, иначе браузер заблокирует запросы из‑за CORS.
- **Проверка инстанса:** откройте или выполните `GET http://127.0.0.1:<BACKEND_PORT>/health` — ожидается JSON `{"status":"ok"}`. На ответах приложения смотрите заголовок **`X-Request-ID`** (request logging): если его нет, запрос мог попасть на другой сервис на том же порту.
- Если `main.py` не смог освободить порт, в PowerShell (при необходимости от администратора): `Get-NetTCPConnection -LocalPort <порт> | Select-Object OwningProcess`, затем `Stop-Process -Id <PID> -Force`.

## Логирование (backend)

- Консоль и ежедневные файлы: `backend/logs/app-YYYY-MM-DD.log` (каталог создаётся автоматически).
- Каждый день создаётся отдельный лог-файл; переключение на новый день происходит автоматически даже без перезапуска сервиса.
- Хранится не более 5 последних лог-файлов (старые удаляются автоматически).
- Уровень логов настраивается через `LOG_LEVEL`; файловое логирование включается через `LOG_ENABLE_FILE`.
- HTTP: middleware `RequestLoggingMiddleware` пишет старт/конец запроса, длительность и заголовок `X-Request-ID` (можно передать свой).
- Бизнес-логика: модуль `app.digest` — шаги пайплайна дайджеста; `app.proxyapi` — ошибки вызовов ProxyAPI; `app.main` — старт/стоп и ошибки планировщика.

## Модели и стоимость

- Источник тарифов: ProxyAPI (`pricing/list`), цены в рублях за 1M токенов.
- Для текущего MVP выбраны минимально достаточные модели:
  - `NewsResearchAgent` → `gpt-4.1-mini`
  - `SourceVerificationAgent` → `gpt-4.1-mini`
  - `ScoringAgent` → `gpt-4.1-nano`
  - `OrderingAgent` → `gpt-4.1-nano`
  - `AnalyticsAgent` → `gpt-4.1-mini`
  - `PlatformWriterAgent` → `gpt-4.1-mini`
  - `ImagePromptAgent` → `gpt-4.1-nano`
  - `QualityControlAgent` → `gpt-4.1-nano`
- В UI выводится стоимость каждого AI-запроса (по шагам и агентам), а также суммарная стоимость выпуска.
- Для получения стоимости требуется разрешение ProxyAPI API-ключа на запрос баланса.

## Push в GitHub

- Используйте `push_to_github.bat` (аналогично проекту Invest).
- Примеры:
  - `push_to_github.bat "your commit message"`
  - `push_to_github.bat --dry-run`
  - `push_to_github.bat --max-len 120`
- Если сообщение не передано, скрипт генерирует его автоматически из изменённых файлов.
- Скрипт добавляет строку протокола в `docs/push_protocol.log`, затем делает `git add -A`, `git commit` и `git push`.

## Примечания

- Если `ENABLE_WEB_FETCH=false`, для Шага 1 нужно вручную передать 5-10 ссылок.
- При `ENABLE_WEB_FETCH=true` шаг 1 сначала ищет реальные URL через **ProxyAPI web_search** (см. [документацию](https://proxyapi.ru/docs/openai-web-search)), затем при нехватке — CrewAI; опционально запасной поиск: `SERPAPI_API_KEY` / `TAVILY_API_KEY`.
- Шаг 1 формирует пул итерациями (батчи поиска и верификации). По умолчанию: батч `20`, soft-limit `3` мин, hard-limit `5` мин, итоговый пул `10–15` с соблюдением квот качества.
- Новости с нерабочей ссылкой и со статусом `❗ без подтверждения` не допускаются к выбору.
- Код хранит модель в переменной `PROXYAPI_MODEL`, можно менять без изменения исходников.
