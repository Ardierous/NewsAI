# Публикация контейнеров News в Docker Hub

Инструкция для публикации двух образов:
- backend (`extellect-news-backend`)
- frontend (`extellect-news-frontend`)

## 1) Требования

- Docker Desktop (или Docker Engine + Compose)
- Python 3.11+
- Docker Hub аккаунт

## 2) Быстрый старт (Windows)

Двойной клик или из корня проекта:

```bat
push_dockerhub.bat
```

Скрипт:
- проверит Docker и место на диске;
- **запросит логин и пароль / PAT** (без использования старого кэша Docker Desktop);
- для **backend** и **frontend** отдельно:
  - **шаг 1** — подтверждение → сборка контейнера (`docker build`);
  - **шаг 2** — подтверждение → push в Docker Hub (тег версии и `latest`).

PAT создаётся здесь: https://app.docker.com/settings/security (права **Read & Write**).
В поле пароля вводите **токен**, не пароль от сайта (если включена 2FA).

## 3) Переменные окружения (опционально)

По умолчанию:
- `DOCKER_USERNAME=avardous`
- `DOCKER_BACKEND_REPO=extellect-news-backend`
- `DOCKER_FRONTEND_REPO=extellect-news-frontend`
- `DOCKER_TAG=<автогенерация>`

Можно переопределить:

```powershell
$env:DOCKER_USERNAME="ваш_логин"
$env:DOCKER_BACKEND_REPO="news-backend"
$env:DOCKER_FRONTEND_REPO="news-frontend"
$env:DOCKER_TAG="v1.0.0"
python scripts/push_to_dockerhub.py
```

## 4) Запуск из Docker Hub (без локальной сборки)

Используйте `scripts/docker-compose.prod.yml`:

```bash
docker compose -f scripts/docker-compose.prod.yml up -d
```

По умолчанию подтягиваются `:latest`.
Чтобы запустить конкретный тег:

```powershell
$env:DOCKER_TAG="v1.0.0"
docker compose -f scripts/docker-compose.prod.yml up -d
```

## 5) Проверка

- UI: <http://localhost:3000>
- API: <http://localhost:8000/health>

## 6) Частые проблемы

- Скрипт «завис» на сборке backend  
  Первая сборка **15–25 минут** — это нормально. Прогресс виден в окне (`Step 3/6`, `pip install`…).  
  В Docker Desktop → **Builds** тоже виден статус.

- `no space left on device` / `EROFS` / сборка обрывается на frontend  
  На диске **C:** мало места (нужно **8–15 ГБ** свободно). Освободите место и выполните:
  ```powershell
  docker system prune -af
  ```
  Затем снова `push_dockerhub.bat`.

- `denied: requested access to the resource is denied`  
  Проверьте `DOCKER_USERNAME` и имя репозитория.

- `unauthorized: authentication required`  
  Повторите `docker login` (лучше использовать access token).

- DNS ошибки Docker Hub  
  Перезапустите Docker Desktop, отключите VPN/прокси или проверьте DNS.

## 7) Автопубликация через GitHub Actions

В репозитории добавлен workflow: `.github/workflows/docker-publish.yml`.

Триггеры:
- push тега `v*` (например `v1.0.0`);
- публикация GitHub Release;
- ручной запуск (`workflow_dispatch`).

### Что настроить в GitHub (один раз)

В `Settings → Secrets and variables → Actions` добавьте:
- `DOCKERHUB_USERNAME` — логин Docker Hub;
- `DOCKERHUB_TOKEN` — Docker Hub access token.

### Как выпускать новую версию

```bash
git tag v1.0.0
git push origin v1.0.0
```

После этого GitHub Actions автоматически соберет и запушит:
- `DOCKERHUB_USERNAME/extellect-news-backend:v1.0.0`
- `DOCKERHUB_USERNAME/extellect-news-backend:latest`
- `DOCKERHUB_USERNAME/extellect-news-frontend:v1.0.0`
- `DOCKERHUB_USERNAME/extellect-news-frontend:latest`

## 8) Проверка Docker-сборки в PR (без push)

Добавлен workflow: `.github/workflows/docker-ci.yml`.

Триггеры:
- любой Pull Request;
- ручной запуск (`workflow_dispatch`).

Что делает:
- собирает backend-образ;
- собирает frontend-образ;
- ничего не публикует в Docker Hub (только проверка, что Dockerfile и контексты валидны).
