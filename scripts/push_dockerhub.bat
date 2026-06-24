@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

cd /d "%~dp0"

REM Двойной клик: открыть окно, которое не закроется сразу.
if /I "%~1"=="--stay-open" (
    shift
) else (
    echo %cmdcmdline% | findstr /I /C:" /c " >nul
    if not errorlevel 1 (
        start "News Docker Hub" cmd /k ""%~f0" --stay-open %*"
        exit /b 0
    )
)

echo ============================================================
echo  Публикация Docker-образов News на Docker Hub
echo  Запускает push_to_dockerhub.py
echo  На каждом шаге — подтверждение y/n
echo ============================================================
echo.

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

echo 💾 Проверка места на диске C:...
for /f "tokens=3" %%a in ('powershell -NoProfile -Command "(Get-PSDrive C).Free / 1GB"') do set FREE_GB=%%a
if defined FREE_GB (
    echo    Свободно: примерно !FREE_GB! ГБ
    powershell -NoProfile -Command "if ((Get-PSDrive C).Free -lt 8GB) { exit 1 }" >nul 2>&1
    if errorlevel 1 (
        echo ❌ Мало места ^(нужно ~8 ГБ+^). Освободите диск или: docker system prune -af
        pause
        exit /b 1
    )
)

if not defined DOCKER_USERNAME set DOCKER_USERNAME=avardous

echo.
echo Дальше Python-скрипт:
echo   - запросит логин и пароль/PAT Docker Hub
echo   - для backend и frontend: сборка и push ^(каждый шаг — y^)
echo.
echo PAT: https://app.docker.com/settings/security
echo.

py -3.11 --version >nul 2>&1
if !errorlevel!==0 (
    py -3.11 -u "%~dp0push_to_dockerhub.py"
) else (
    python --version >nul 2>&1
    if errorlevel 1 (
        echo ❌ Python 3.11+ не найден.
        pause
        exit /b 1
    )
    python -u "%~dp0push_to_dockerhub.py"
)

set EXIT_CODE=!errorlevel!
echo.
if !EXIT_CODE!==0 (
    echo ✅ Публикация завершена.
) else (
    echo ❌ Ошибка ^(код !EXIT_CODE!^).
)
pause
exit /b !EXIT_CODE!
