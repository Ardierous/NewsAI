@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

cd /d "%~dp0"

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
echo  News - publish Docker images to Docker Hub
echo  Runs: scripts\push_to_dockerhub.py
echo  Confirm each step with y/n in the script window
echo ============================================================
echo.

where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker not found. Install Docker Desktop.
    pause
    exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker Desktop is not running. Wait for Running status.
    pause
    exit /b 1
)

if not defined DOCKER_USERNAME set DOCKER_USERNAME=avardous

echo Docker Hub user default: %DOCKER_USERNAME%
echo PAT: https://app.docker.com/settings/security
echo Disk space check runs inside Python script.
echo.

set PY_CMD=
py -3.11 --version >nul 2>&1
if not errorlevel 1 set PY_CMD=1
if defined PY_CMD (
    py -3.11 -u "%~dp0scripts\push_to_dockerhub.py"
) else (
    python --version >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python 3.11+ not found.
        pause
        exit /b 1
    )
    python -u "%~dp0scripts\push_to_dockerhub.py"
)
set EXIT_CODE=%errorlevel%

echo.
if %EXIT_CODE%==0 (
    echo [OK] Done.
) else (
    echo [ERROR] Exit code %EXIT_CODE%.
)
pause
exit /b %EXIT_CODE%
