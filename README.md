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
  push_protocol.log
```

## Что реализовано

- Pipeline шагов `run_step_0 -> run_step_1 -> run_step_1_5 -> run_step_2 -> run_step_3`
- Жесткие переходы:
  - Step2 только после команды `готово`
  - Step3 только после команды `Ок`
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
- `POST /digests/{id}/select`
- `POST /digests/{id}/order`
- `POST /digests/{id}/confirm-ready`
- `POST /digests/{id}/confirm-final`
- `GET /digests/{id}/final`
- `GET /digests/{id}/docx`
- `GET /digests/{id}/image`

## Запуск (локально)

### Быстрый запуск

Из корня репозитория:

```bash
python main.py
```

Опции: `--no-install` (без `pip install` / `npm install`), `--backend-only`, `--frontend-only`.

1. Создайте `.env` в папке `backend` по шаблону `backend/.env.example`:

```env
PROXYAPI_API_KEY=your_key_here
PROXYAPI_BASE_URL=https://openai.api.proxyapi.ru/v1
PROXYAPI_MODEL=openai/gpt-4.1
PROXYAPI_IMAGE_MODEL=openai/gpt-image-1
DATABASE_URL=sqlite:///./digest.db
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000
FRONTEND_ORIGIN=http://localhost:3000
ENABLE_WEB_FETCH=false

LOG_LEVEL=INFO
LOG_ENABLE_FILE=true
LOG_FILE_NAME=app.log
LOG_MAX_BYTES=5000000
LOG_BACKUP_COUNT=5
```

2. Установите зависимости backend:

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

3. Запустите backend:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

4. Установите зависимости frontend:

```bash
cd ../frontend
npm install
```

5. Создайте `frontend/.env.local`:

```env
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000
```

6. Запустите frontend:

```bash
npm run dev
```

7. Откройте интерфейс:

- [http://localhost:3000](http://localhost:3000)
- Нажмите **Создать сегодняшний дайджест**
- Пройдите шаги мастера по порядку.

## Логирование (backend)

- Консоль и ротируемый файл: `backend/logs/app.log` (каталог создаётся автоматически).
- Уровень и параметры файла задаются переменными `LOG_LEVEL`, `LOG_ENABLE_FILE`, `LOG_FILE_NAME`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT` в `.env`.
- HTTP: middleware `RequestLoggingMiddleware` пишет старт/конец запроса, длительность и заголовок `X-Request-ID` (можно передать свой).
- Бизнес-логика: модуль `app.digest` — шаги пайплайна дайджеста; `app.proxyapi` — ошибки вызовов ProxyAPI; `app.main` — старт/стоп и ошибки планировщика.

## Push в GitHub

- Используйте `push_to_github.bat` (аналогично проекту Invest).
- Примеры:
  - `push_to_github.bat "your commit message"`
  - `push_to_github.bat --dry-run`
  - `push_to_github.bat --max-len 120`
- Если сообщение не передано, скрипт генерирует его автоматически из изменённых файлов.
- Скрипт добавляет строку протокола в `docs/push_protocol.log`, затем делает `git add -A`, `git commit` и `git push`.

## Примечания

- Если `ENABLE_WEB_FETCH=false`, для Step1 нужно вручную передать 5-10 ссылок.
- Новости с нерабочей ссылкой и со статусом `❗ без подтверждения` не допускаются к выбору.
- Код хранит модель в переменной `PROXYAPI_MODEL`, можно менять без изменения исходников.
