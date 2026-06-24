#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Публикация Docker-образов проекта News в Docker Hub.

Интерактивный сценарий:
1. Авторизация — всегда запрос пароля / PAT (без кэша Docker Desktop).
2. Для каждого сервиса (backend, frontend):
   - подтверждение → сборка контейнера (docker build);
   - подтверждение → push в Docker Hub (тег версии и latest).

Переменные окружения:
- DOCKER_USERNAME (по умолчанию: avardous)
- DOCKER_BACKEND_REPO (по умолчанию: extellect-news-backend)
- DOCKER_FRONTEND_REPO (по умолчанию: extellect-news-frontend)
- DOCKER_TAG (если не задан, генерируется автоматически)
"""

from __future__ import annotations

import getpass
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)


def run_command_with_result(cmd: str) -> tuple[bool, subprocess.CompletedProcess[str]]:
    print(f"\n{'=' * 68}")
    print(f"Выполняется: {cmd}")
    print(f"{'=' * 68}\n")
    result = subprocess.run(
        cmd,
        shell=True,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=PROJECT_ROOT,
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n")
    return result.returncode == 0, result


def run_command(cmd: str, *, fail_on_error: bool = True) -> bool:
    ok, _ = run_command_with_result(cmd)
    if not ok and fail_on_error:
        print(f"\n❌ Ошибка: {cmd}")
        sys.exit(1)
    return ok


def run_command_streaming(cmd: str) -> bool:
    print(f"\n{'=' * 68}")
    print(f"Выполняется: {cmd}")
    print(f"{'=' * 68}\n", flush=True)
    result = subprocess.run(cmd, shell=True, check=False, cwd=PROJECT_ROOT)
    return result.returncode == 0


def confirm_yes(prompt: str) -> bool:
    answer = input(f"\n{prompt}\nПродолжить? (y/n): ").strip().lower()
    if answer != "y":
        print("⏭️ Шаг пропущен.")
        return False
    return True


def disk_free_gb(path: Path | None = None) -> float:
    root = path or PROJECT_ROOT
    anchor = root.drive + "\\" if os.name == "nt" and root.drive else root.anchor or str(root)
    try:
        return shutil.disk_usage(anchor).free / (1024**3)
    except Exception:
        return -1.0


def docker_disk_preflight() -> bool:
    free_gb = disk_free_gb()
    if free_gb >= 0:
        print(f"\n💾 Свободно на диске (для Docker): {free_gb:.1f} ГБ")
    else:
        print("\n💾 Не удалось проверить свободное место на диске")
    if 0 <= free_gb < 8:
        print("❌ Мало места на диске — Docker-сборка backend часто требует 8–15 ГБ.")
        print("   Освободите место или выполните: docker system prune -af")
        return False
    if 0 <= free_gb < 15:
        print("⚠️ Места мало: первая сборка backend может занять 15–25 мин.")
    else:
        print("✅ Места на диске достаточно для сборки")
    ok, result = run_command_with_result("docker system df")
    if not ok:
        print("⚠️ Не удалось получить docker system df — продолжаем.")
    combined = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    if "reclaimable" in combined and free_gb < 20:
        print("💡 Подсказка: docker system prune -af освободит старые слои образов.")
    return True


def is_disk_full_error(text: str) -> bool:
    s = (text or "").lower()
    return any(
        marker in s
        for marker in (
            "no space left on device",
            "enospc",
            "erofs: read-only file system",
            "read-only file system",
            "not enough space",
        )
    )


def print_disk_full_hint() -> None:
    print("\n💡 Похоже, закончилось место на диске или в образе Docker Desktop.")
    print("   1) Освободите 10+ ГБ на диске C:")
    print("   2) docker system prune -af")
    print("   3) Docker Desktop → Troubleshoot → Clean / Purge data (если нужно)")
    print("   4) Перезапустите Docker Desktop и повторите push.")


def normalize_docker_name(name: str) -> str:
    normalized = (name or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9._-]", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    return normalized


def get_git_short_sha() -> str | None:
    ok, result = run_command_with_result("git rev-parse --short HEAD")
    if ok and result.stdout.strip():
        return result.stdout.strip()
    return None


def generate_auto_tag() -> str:
    sha = get_git_short_sha()
    date_part = datetime.now().strftime("%Y%m%d")
    if sha:
        return f"{date_part}-{sha}"
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _can_resolve_host(hostname: str) -> bool:
    try:
        socket.getaddrinfo(hostname, 443)
        return True
    except Exception:
        return False


def dockerhub_preflight() -> bool:
    print("\n🌐 Проверка DNS для Docker Hub...")
    hosts = ["registry-1.docker.io", "auth.docker.io", "index.docker.io"]
    failed = [h for h in hosts if not _can_resolve_host(h)]
    if failed:
        print("❌ Не удалось разрешить DNS:")
        for host in failed:
            print(f"   - {host}")
        print("💡 Подсказка: перезапустите Docker Desktop и проверьте VPN/прокси.")
        return False
    print("✅ DNS Docker Hub доступен")
    return True


def should_retry_push(stderr_text: str) -> bool:
    s = (stderr_text or "").lower()
    retry_markers = [
        "unexpected status from put request",
        "failed commit on ref",
        "400 bad request",
        "500 internal server error",
        "502 bad gateway",
        "503 service unavailable",
        "504 gateway timeout",
        "tls handshake timeout",
        "i/o timeout",
        "connection reset by peer",
        "context deadline exceeded",
        "toomanyrequests",
        "429",
        "eof",
    ]
    return any(marker in s for marker in retry_markers)


def _is_unauthorized_error(text: str) -> bool:
    s = (text or "").lower()
    return "unauthorized" in s or "denied" in s or "authentication required" in s


def _login_with_token(username: str, token: str) -> bool:
    print(f"\n🔐 Вход в Docker Hub как {username}...")
    proc = subprocess.run(
        ["docker", "login", "-u", username, "--password-stdin"],
        input=token + "\n",
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=PROJECT_ROOT,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.stdout:
        print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
    if proc.stderr:
        print(proc.stderr, end="" if proc.stderr.endswith("\n") else "\n")
    if proc.returncode == 0 and "login succeeded" in out.lower():
        print("✅ Успешный вход в Docker Hub")
        return True
    print("❌ Не удалось войти в Docker Hub")
    if "unauthorized" in out.lower():
        print("💡 Проверьте логин и PAT (права Read & Write).")
    return False


def docker_login_interactive(default_username: str) -> str | None:
    """Явная авторизация: логин и пароль/PAT только от пользователя (без кэша)."""
    print("\n" + "=" * 68)
    print("🔐 Авторизация Docker Hub")
    print("=" * 68)
    print("Введите логин и пароль. Рекомендуется Personal Access Token (PAT) вместо пароля.")
    print("Создать PAT: https://app.docker.com/settings/security (права Read & Write)")

    entered_user = input(f"\nЛогин Docker Hub [{default_username}]: ").strip()
    username = normalize_docker_name(entered_user or default_username)
    if not username:
        print("❌ Логин не может быть пустым.")
        return None

    for attempt in range(1, 4):
        token = getpass.getpass(f"Пароль / PAT Docker Hub (попытка {attempt}/3): ").strip()
        if not token:
            print("❌ Пароль не может быть пустым.")
            continue
        if _login_with_token(username, token):
            return username
    return None


def reauth_on_push_failure(username: str) -> str | None:
    print("\n⚠️ Push отклонён — нужна повторная авторизация.")
    return docker_login_interactive(username)


def push_with_retries(
    image_name: str,
    username: str,
    *,
    retries: int = 4,
    base_delay_sec: int = 4,
) -> tuple[bool, str]:
    current_user = username
    for attempt in range(1, retries + 1):
        print(f"\n📤 Push {attempt}/{retries}: {image_name}")
        ok, result = run_command_with_result(f"docker push {image_name}")
        if ok:
            print(f"✅ Push успешен: {image_name}")
            return True, current_user
        combined = f"{result.stdout}\n{result.stderr}"
        if _is_unauthorized_error(combined):
            new_user = reauth_on_push_failure(current_user)
            if new_user:
                current_user = new_user
                continue
            return False, current_user
        if is_disk_full_error(combined):
            print_disk_full_hint()
            return False, current_user
        if attempt < retries and should_retry_push(combined):
            delay = base_delay_sec * (2 ** (attempt - 1))
            print(f"⚠️ Временная ошибка, повтор через {delay} сек...")
            time.sleep(delay)
            continue
        print(f"❌ Push не выполнен: {image_name}")
        return False, current_user
    return False, current_user


def build_service_image(
    *,
    service_name: str,
    dockerfile_path: str,
    context_path: str,
    image_tagged: str,
    image_latest: str,
    build_args: str = "",
) -> bool:
    print(f"\n🔨 Сборка контейнера: {service_name}")
    if service_name == "backend":
        print("   Первая сборка backend (CrewAI) обычно 15–25 мин — смотрите строки Step … ниже.")
    cmd = (
        f"docker build -f {dockerfile_path} {build_args} "
        f"-t {image_tagged} -t {image_latest} {context_path}"
    ).strip()
    if run_command_streaming(cmd):
        print(f"✅ Контейнер собран: {service_name}")
        return True
    print(f"❌ Сборка {service_name} не удалась")
    if 0 <= disk_free_gb() < 8:
        print_disk_full_hint()
    return False


def push_service_images(
    *,
    service_name: str,
    image_tagged: str,
    image_latest: str,
    docker_username: str,
) -> tuple[bool, str]:
    print(f"\n📤 Публикация в Docker Hub: {service_name}")
    ok, docker_username = push_with_retries(image_tagged, docker_username)
    if not ok:
        return False, docker_username
    ok, docker_username = push_with_retries(image_latest, docker_username, retries=3)
    if not ok:
        return False, docker_username
    print(f"✅ Образы {service_name} опубликованы на Docker Hub")
    return True, docker_username


def process_service(
    *,
    service_name: str,
    dockerfile_path: str,
    context_path: str,
    image_tagged: str,
    image_latest: str,
    docker_username: str,
    build_args: str = "",
) -> tuple[bool, str]:
    print("\n" + "=" * 68)
    print(f"📦 Сервис: {service_name}")
    print("=" * 68)
    print(f"   Тег версии: {image_tagged}")
    print(f"   Тег latest: {image_latest}")

    build_ok = False
    if confirm_yes(
        f"▶ Шаг 1 — Сформировать контейнер «{service_name}» локально (docker build)?"
    ):
        build_ok = build_service_image(
            service_name=service_name,
            dockerfile_path=dockerfile_path,
            context_path=context_path,
            image_tagged=image_tagged,
            image_latest=image_latest,
            build_args=build_args,
        )
        if not build_ok:
            return False, docker_username
    else:
        print("   Используем уже собранный образ (если он есть локально).")

    if not confirm_yes(
        f"▶ Шаг 2 — Отправить «{service_name}» в Docker Hub?\n"
        f"   • {image_tagged}\n"
        f"   • {image_latest}"
    ):
        print(f"⏭️ Push для {service_name} пропущен.")
        return True, docker_username

    push_ok, docker_username = push_service_images(
        service_name=service_name,
        image_tagged=image_tagged,
        image_latest=image_latest,
        docker_username=docker_username,
    )
    return push_ok, docker_username


def main() -> None:
    print("=" * 68)
    print("🚀 Публикация Docker-образов News в Docker Hub")
    print("   Каждый шаг — с вашим подтверждением (y/n)")
    print("=" * 68)

    if not run_command("docker --version", fail_on_error=False):
        print("❌ Docker не установлен или не доступен.")
        sys.exit(1)

    docker_username = normalize_docker_name(os.getenv("DOCKER_USERNAME", "avardous"))
    backend_repo = normalize_docker_name(os.getenv("DOCKER_BACKEND_REPO", "extellect-news-backend"))
    frontend_repo = normalize_docker_name(os.getenv("DOCKER_FRONTEND_REPO", "extellect-news-frontend"))
    docker_tag = normalize_docker_name(os.getenv("DOCKER_TAG", "")) or generate_auto_tag()

    backend_image = f"{docker_username}/{backend_repo}:{docker_tag}"
    backend_latest = f"{docker_username}/{backend_repo}:latest"
    frontend_image = f"{docker_username}/{frontend_repo}:{docker_tag}"
    frontend_latest = f"{docker_username}/{frontend_repo}:latest"

    print("\n📋 План публикации:")
    print(f"   backend:  {backend_image}")
    print(f"             {backend_latest}")
    print(f"   frontend: {frontend_image}")
    print(f"             {frontend_latest}")
    print("\nПорядок шагов:")
    print("   1) Авторизация Docker Hub (логин + пароль/PAT)")
    print("   2) backend — подтверждение сборки → сборка")
    print("   3) backend — подтверждение push → push")
    print("   4) frontend — подтверждение сборки → сборка")
    print("   5) frontend — подтверждение push → push")

    if not confirm_yes(
        "▶ Запустить автоматическую публикацию backend и frontend на Docker Hub?\n"
        "   (на каждом шаге сборки и push будет отдельный запрос y/n)"
    ):
        print("❌ Отменено.")
        sys.exit(0)

    if not dockerhub_preflight():
        sys.exit(1)

    if not docker_disk_preflight():
        sys.exit(1)

    logged_in_user = docker_login_interactive(docker_username)
    if not logged_in_user:
        print("❌ Авторизация не выполнена — выход.")
        sys.exit(1)
    docker_username = logged_in_user

    backend_ok, docker_username = process_service(
        service_name="backend",
        dockerfile_path="backend/Dockerfile",
        context_path="backend",
        image_tagged=backend_image,
        image_latest=backend_latest,
        docker_username=docker_username,
    )
    if not backend_ok:
        sys.exit(1)

    frontend_ok, docker_username = process_service(
        service_name="frontend",
        dockerfile_path="frontend/Dockerfile",
        context_path="frontend",
        image_tagged=frontend_image,
        image_latest=frontend_latest,
        docker_username=docker_username,
        build_args="--build-arg NEXT_PUBLIC_API_BASE=http://localhost:8000",
    )
    if not frontend_ok:
        sys.exit(1)

    print("\n" + "=" * 68)
    print("✅ Сценарий завершён")
    print("=" * 68)
    print("\nDocker Hub:")
    print(f"  https://hub.docker.com/r/{docker_username}/{backend_repo}")
    print(f"  https://hub.docker.com/r/{docker_username}/{frontend_repo}")
    print("\nДля прод-запуска: docker compose -f scripts/docker-compose.prod.yml up -d")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n❌ Прервано пользователем")
        sys.exit(1)
