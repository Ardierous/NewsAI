@echo off
chcp 65001 >nul
cd /d "%~dp0"

where docker >nul 2>&1
if errorlevel 1 (
  echo Docker не найден.
  pause
  exit /b 1
)

echo Останавливаю контейнеры ExTellect Digest ...
docker compose down

if errorlevel 1 (
  echo Не удалось остановить. Проверьте, что Docker запущен.
) else (
  echo Готово. Контейнеры остановлены.
)

pause
