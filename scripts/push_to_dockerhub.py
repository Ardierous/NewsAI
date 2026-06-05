#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Публикация Docker-образов проекта News в Docker Hub.

Публикуются два образа:
- backend
- frontend

Переменные окружения:
- DOCKER_USERNAME (по умолчанию: avardous)
- DOCKER_BACKEND_REPO (по умолчанию: extellect-news-backend)
- DOCKER_FRONTEND_REPO (по умолчанию: extellect-news-frontend)
- DOCKER_TAG (если не задан, генерируется автоматически)
- DOCKERHUB_TOKEN (PAT; если не задан — запрос при необходимости)
"""

from __future__ import annotations

import getpass
import os
import re
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


def prompt_for_pat(username: str, *, reason: str) -> bool:
    print(f"\n⚠️ {reason}")
    print("   Создать PAT: https://app.docker.com/settings/security")
    for attempt in range(1, 4):
        token = getpass.getpass(f"Введите Docker Hub PAT (попытка {attempt}/3): ").strip()
        if not token:
            print("❌ PAT пустой, повторите ввод.")
            continue
        if _login_with_token(username, token):
            return True
    return False


def docker_login(username: str) -> bool:
    """Вход в Docker Hub: PAT из env, иначе кэш, при ошибке — интерактивный запрос."""
    token = os.getenv("DOCKERHUB_TOKEN", "").strip()
    if token:
        return _login_with_token(username, token)

    print("\n🔐 Вход в Docker Hub (сохранённые учётные данные)...")
    print("   Если пароль не спросят — используется кэш Docker Desktop/Windows.")
    ok, result = run_command_with_result(f"docker login -u {username}")
    combined = ((result.stdout or "") + (result.stderr or "")).lower()
    if ok and "login succeeded" in combined:
        print("✅ Успешный вход в Docker Hub")
        return True

    return prompt_for_pat(username, reason="Сохранённый вход не подошёл — нужен Personal Access Token (PAT).")


def ensure_docker_auth(username: str, error_text: str) -> bool:
    if not _is_unauthorized_error(error_text):
        return False
    return prompt_for_pat(username, reason="Push отклонён (unauthorized) — нужен актуальный PAT.")


def push_with_retries(
    image_name: str,
    username: str,
    retries: int = 4,
    base_delay_sec: int = 4,
) -> bool:
    for attempt in range(1, retries + 1):
        print(f"\n📤 Push {attempt}/{retries}: {image_name}")
        ok, result = run_command_with_result(f"docker push {image_name}")
        if ok:
            print(f"✅ Push успешен: {image_name}")
            return True
        combined = f"{result.stdout}\n{result.stderr}"
        if _is_unauthorized_error(combined) and ensure_docker_auth(username, combined):
            continue
        if attempt < retries and should_retry_push(combined):
            delay = base_delay_sec * (2 ** (attempt - 1))
            print(f"⚠️ Временная ошибка, повтор через {delay} сек...")
            time.sleep(delay)
            continue
        print(f"❌ Push не выполнен: {image_name}")
        return False
    return False


def build_and_push_service(
    *,
    service_name: str,
    dockerfile_path: str,
    context_path: str,
    image_tagged: str,
    image_latest: str,
    docker_username: str,
    build_args: str = "",
) -> bool:
    print(f"\n🔨 Сборка сервиса: {service_name}")
    cmd = (
        f"docker build -f {dockerfile_path} {build_args} "
        f"-t {image_tagged} -t {image_latest} {context_path}"
    ).strip()
    if not run_command(cmd, fail_on_error=False):
        print(f"❌ Сборка {service_name} не удалась")
        return False

    print(f"\n📤 Публикация {service_name} ({image_tagged})")
    if not push_with_retries(image_tagged, docker_username):
        return False

    print(f"\n📤 Публикация {service_name} ({image_latest})")
    if not push_with_retries(image_latest, docker_username, retries=3):
        return False
    return True


def main() -> None:
    print("=" * 68)
    print("🚀 Публикация Docker-образов News в Docker Hub")
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

    print("\n📦 Будут опубликованы образы:")
    print(f"   backend:  {backend_image}")
    print(f"   backend:  {backend_latest}")
    print(f"   frontend: {frontend_image}")
    print(f"   frontend: {frontend_latest}")

    confirm = input("\nПродолжить? (y/n): ").strip().lower()
    if confirm != "y":
        print("❌ Отменено пользователем")
        sys.exit(0)

    if not docker_login(docker_username):
        print("❌ Не удалось войти в Docker Hub")
        sys.exit(1)

    if not dockerhub_preflight():
        sys.exit(1)

    backend_ok = build_and_push_service(
        service_name="backend",
        dockerfile_path="backend/Dockerfile",
        context_path="backend",
        image_tagged=backend_image,
        image_latest=backend_latest,
        docker_username=docker_username,
    )
    if not backend_ok:
        sys.exit(1)

    frontend_ok = build_and_push_service(
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
    print("✅ Публикация завершена успешно")
    print("=" * 68)
    print("\nDocker Hub:")
    print(f"  https://hub.docker.com/r/{docker_username}/{backend_repo}")
    print(f"  https://hub.docker.com/r/{docker_username}/{frontend_repo}")
    print("\nДля прод-запуска используйте scripts/docker-compose.prod.yml")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n❌ Прервано пользователем")
        sys.exit(1)
