@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

REM Запуск ExTellect Digest в Docker (backend + frontend).
REM Двойной клик по этому файлу из корня проекта News.

where docker >nul 2>&1
if errorlevel 1 (
  echo [ОШИБКА] Docker не найден. Установите Docker Desktop и перезапустите компьютер.
  echo https://www.docker.com/products/docker-desktop/
  pause
  exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
  echo [ОШИБКА] Docker установлен, но не запущен. Откройте Docker Desktop и подождите «Running».
  pause
  exit /b 1
)

if not exist "backend\.env" (
  echo Файл backend\.env не найден — копирую из backend\.env.example ...
  copy /Y "backend\.env.example" "backend\.env" >nul
  echo.
  echo Откройте backend\.env в блокноте и замените your_key_here на ваш ключ ProxyAPI.
  echo Сохраните файл и снова запустите run-docker.bat
  echo.
  notepad "backend\.env"
  pause
  exit /b 1
)

findstr /C:"PROXYAPI_API_KEY=your_key_here" "backend\.env" >nul 2>&1
if not errorlevel 1 (
  echo [ВНИМАНИЕ] В backend\.env стоит заглушка your_key_here.
  echo Вставьте настоящий ключ: PROXYAPI_API_KEY=sk-...
  notepad "backend\.env"
  pause
  exit /b 1
)

findstr /B /C:"PROXYAPI_API_KEY=" "backend\.env" >nul 2>&1
if errorlevel 1 (
  echo [ОШИБКА] В backend\.env нет строки PROXYAPI_API_KEY=...
  notepad "backend\.env"
  pause
  exit /b 1
)

echo ========================================
echo  ExTellect Digest — Docker
echo  UI:  http://localhost:3000
echo  API: http://localhost:8000/health
echo  Остановка: stop-docker.bat или Ctrl+C
echo ========================================
echo.

start "" "http://localhost:3000"
docker compose up --build

pause
