@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
echo ============================================================
echo 🚀 Публикация Docker-образов News на Docker Hub
echo ============================================================
echo.

cd /d "%~dp0"

where docker >nul 2>&1
if errorlevel 1 (
    echo ❌ Docker не найден. Установите Docker Desktop.
    pause
    exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
    echo ❌ Docker Desktop не запущен. Дождитесь статуса Running.
    pause
    exit /b 1
)

if not defined DOCKER_USERNAME set DOCKER_USERNAME=avardous

echo Docker Hub username [!DOCKER_USERNAME!]:
set "DH_USER="
set /p DH_USER=
if not "!DH_USER!"=="" set DOCKER_USERNAME=!DH_USER!

echo.
echo Если раньше был unauthorized — введите PAT сейчас.
echo Создать токен: https://app.docker.com/settings/security
echo.
echo Введите PAT ^(Enter = попробовать сохранённый вход^):
set "DOCKERHUB_TOKEN="
set /p DOCKERHUB_TOKEN=

py -3.11 --version >nul 2>&1
if !errorlevel!==0 (
    py -3.11 -u push_to_dockerhub.py
) else (
    python --version >nul 2>&1
    if errorlevel 1 (
        echo ❌ Python/py launcher не найден. Установите Python 3.11+
        pause
        exit /b 1
    )
    python -u push_to_dockerhub.py
)

exit /b !errorlevel!
