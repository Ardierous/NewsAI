@echo off
REM Публикация контейнеров News в Docker Hub (обёртка из корня проекта).
cd /d "%~dp0"
call "%~dp0scripts\push_dockerhub.bat" %*
exit /b %errorlevel%
