"""
Ручной запуск MVP из корня репозитория:
  python main.py

Поднимает backend (uvicorn) и frontend (next dev) в одном терминале.
Ctrl+C — останавливает оба процесса.
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"


def _venv_python() -> Path:
    if os.name == "nt":
        return BACKEND / ".venv" / "Scripts" / "python.exe"
    return BACKEND / ".venv" / "bin" / "python"


def _ensure_venv() -> Path:
    py = _venv_python()
    if py.exists():
        return py
    print("Создаю виртуальное окружение backend/.venv ...", flush=True)
    subprocess.run([sys.executable, "-m", "venv", str(BACKEND / ".venv")], cwd=str(BACKEND), check=True)
    if not py.exists():
        raise RuntimeError(f"Не удалось создать venv: {py}")
    return py


def _run_pip_install(venv_python: Path) -> None:
    pip = venv_python.parent / ("pip.exe" if os.name == "nt" else "pip")
    if not pip.exists():
        subprocess.run([str(venv_python), "-m", "ensurepip", "--upgrade"], cwd=str(BACKEND), check=True)
    subprocess.run([str(venv_python), "-m", "pip", "install", "-r", str(BACKEND / "requirements.txt")], cwd=str(BACKEND), check=True)


def _run_npm_install() -> None:
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("В PATH не найден npm. Установите Node.js LTS.")
    subprocess.run([npm, "install"], cwd=str(FRONTEND), check=True)


def _terminate(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        proc.terminate()
    else:
        proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="Запуск ExTellect Digest (backend + frontend).")
    parser.add_argument("--no-install", action="store_true", help="Не вызывать pip/npm install перед стартом")
    parser.add_argument("--backend-only", action="store_true", help="Только FastAPI (uvicorn)")
    parser.add_argument("--frontend-only", action="store_true", help="Только Next.js (npm run dev)")
    args = parser.parse_args()

    if not BACKEND.is_dir():
        print(f"Не найдена папка backend: {BACKEND}", file=sys.stderr)
        return 1
    if not FRONTEND.is_dir():
        print(f"Не найдена папка frontend: {FRONTEND}", file=sys.stderr)
        return 1

    npm_path: str | None = shutil.which("npm") if not args.backend_only else None
    if not args.backend_only and not npm_path:
        print("В PATH не найден npm. Установите Node.js LTS.", file=sys.stderr)
        return 1

    backend_proc: subprocess.Popen | None = None
    frontend_proc: subprocess.Popen | None = None

    def shutdown(_signum: int | None = None, _frame: object | None = None) -> None:
        print("\nОстановка процессов ...", flush=True)
        _terminate(frontend_proc)
        _terminate(backend_proc)

    exit_code = 0
    try:
        if not args.frontend_only:
            venv_python = _ensure_venv()
            if not args.no_install:
                print("Установка зависимостей backend (pip) ...", flush=True)
                _run_pip_install(venv_python)
            print("Запуск backend: http://127.0.0.1:8000", flush=True)
            backend_proc = subprocess.Popen(
                [
                    str(venv_python),
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8000",
                    "--reload",
                ],
                cwd=str(BACKEND),
            )

        if not args.backend_only:
            if not args.no_install:
                print("Установка зависимостей frontend (npm) ...", flush=True)
                _run_npm_install()
            print("Запуск frontend: http://localhost:3000", flush=True)
            frontend_proc = subprocess.Popen([npm_path, "run", "dev"], cwd=str(FRONTEND), shell=False)

        print("Работает. Нажмите Ctrl+C для остановки.", flush=True)
        while True:
            if backend_proc is not None and backend_proc.poll() is not None:
                print(f"Backend завершился с кодом {backend_proc.returncode}.", flush=True)
                exit_code = int(backend_proc.returncode or 1)
                break
            if frontend_proc is not None and frontend_proc.poll() is not None:
                print(f"Frontend завершился с кодом {frontend_proc.returncode}.", flush=True)
                exit_code = int(frontend_proc.returncode or 1)
                break
            time.sleep(0.4)
    except KeyboardInterrupt:
        exit_code = 0
    except subprocess.CalledProcessError as e:
        print(f"Ошибка установки или команды: {e}", file=sys.stderr)
        exit_code = 1
    finally:
        shutdown()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
