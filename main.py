"""
Ручной запуск MVP из корня репозитория:
  python main.py

Поднимает backend (uvicorn) и frontend (next dev) в одном терминале.
По умолчанию uvicorn без --reload (один процесс на порт, удобнее на Windows).
Для автоперезапуска при правках кода: python main.py --reload
Если Next.js падает с Cannot find module './vendor-chunks/next.js' или 404 на /_next/static:
  python main.py --clean-frontend
Если launcher не может завершить старый PID и пишет про .app.pid:
  python main.py --force
Порты: переменные BACKEND_PORT / FRONTEND_PORT, ключи в backend/.env или флаги --backend-port / --frontend-port.
Ctrl+C — останавливает оба процесса.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
PID_FILE = ROOT / ".app.pid"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def _enable_windows_ansi_colors() -> None:
    """Включает ANSI-цвета в Windows Terminal/Console, если возможно."""
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        # Если не удалось включить цвета, просто печатаем обычный текст.
        pass


def _print_green(message: str) -> None:
    print(f"{GREEN}{message}{RESET}", flush=True)


def _print_red(message: str) -> None:
    print(f"{RED}{message}{RESET}", flush=True)


def _print_yellow(message: str) -> None:
    print(f"{YELLOW}{message}{RESET}", flush=True)


def _is_port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


def _get_listening_pids_on_port(port: int) -> set[int]:
    pids: set[int] = set()
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["cmd", "/c", "netstat -ano -p tcp"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=False,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line or "LISTENING" not in line:
                    continue
                parts = line.split()
                if len(parts) < 5:
                    continue
                local_addr = parts[1]
                pid_raw = parts[-1]
                if local_addr.endswith(f":{port}") and pid_raw.isdigit():
                    pids.add(int(pid_raw))
        else:
            result = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}"],
                capture_output=True,
                text=True,
                check=False,
            )
            for raw in result.stdout.splitlines():
                raw = raw.strip()
                if raw.isdigit():
                    pids.add(int(raw))
    except Exception:
        return set()
    return pids


def _kill_pid_force(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            # /T — завершить дерево процессов (важно для launcher → uvicorn / node).
            for args in (
                ["taskkill", "/PID", str(pid), "/F", "/T"],
                ["taskkill", "/PID", str(pid), "/F"],
            ):
                subprocess.run(
                    args,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    check=False,
                )
                if not _is_process_running(pid):
                    return True

            ps = shutil.which("powershell")
            if ps:
                subprocess.run(
                    [
                        ps,
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue",
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    check=False,
                )
            return not _is_process_running(pid)
        os.kill(pid, signal.SIGKILL)
        return True
    except Exception:
        return not _is_process_running(pid)


def _parse_backend_dotenv_ports() -> tuple[int | None, int | None]:
    """Читает BACKEND_PORT и FRONTEND_PORT из backend/.env без внешних зависимостей."""
    path = BACKEND / ".env"
    backend_port: int | None = None
    frontend_port: int | None = None
    if not path.is_file():
        return None, None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key == "BACKEND_PORT" and val.isdigit():
            backend_port = int(val)
        elif key == "FRONTEND_PORT" and val.isdigit():
            frontend_port = int(val)
    return backend_port, frontend_port


def _resolve_launch_ports(
    backend_port_arg: int | None,
    frontend_port_arg: int | None,
) -> tuple[int, int]:
    """CLI > переменные окружения > backend/.env > значения по умолчанию."""
    file_be, file_fe = _parse_backend_dotenv_ports()
    env_be = os.environ.get("BACKEND_PORT")
    env_fe = os.environ.get("FRONTEND_PORT")
    default_be = int(env_be) if env_be and env_be.isdigit() else (file_be if file_be is not None else 8000)
    default_fe = int(env_fe) if env_fe and env_fe.isdigit() else (file_fe if file_fe is not None else 3000)
    backend_port = backend_port_arg if backend_port_arg is not None else default_be
    frontend_port = frontend_port_arg if frontend_port_arg is not None else default_fe
    return backend_port, frontend_port


def _force_free_port(port: int, service_name: str) -> bool:
    for attempt in range(1, 4):
        pids = _get_listening_pids_on_port(port)
        if not pids:
            return True

        _print_red(
            f"Порт {port} занят. Принудительно останавливаю процесс(ы): "
            f"{', '.join(map(str, sorted(pids)))} (попытка {attempt}/3)"
        )
        for pid in sorted(pids):
            if _kill_pid_force(pid):
                print(f"Остановлен PID {pid} на порту {port} ({service_name}).", flush=True)
            else:
                _print_red(f"Не удалось остановить PID {pid} на порту {port}.")

        # Даём ОС время освободить сокет и обновить таблицу.
        time.sleep(0.8)
        if not _is_port_in_use("127.0.0.1", port):
            return True

    return False


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


def _clean_frontend_next_cache() -> None:
    """Удаляет frontend/.next — лечит битый инкрементальный кэш Next.js на Windows (vendor-chunks / 404 static)."""
    cache = FRONTEND / ".next"
    if not cache.exists():
        return
    print("Удаление frontend/.next (кэш Next.js) ...", flush=True)
    shutil.rmtree(cache, ignore_errors=True)


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


def _is_process_running(pid: int) -> bool:
    """Проверяет, существует ли процесс с указанным PID."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.SetLastError(0)
            # На Windows os.kill(pid, 0) иногда падает с WinError 87/SystemError
            # для "битых" PID, поэтому проверяем через OpenProcess.
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                exit_code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    alive = int(exit_code.value) == STILL_ACTIVE
                else:
                    alive = True
                kernel32.CloseHandle(handle)
                return alive
            # ERROR_ACCESS_DENIED (5): процесс существует, но нет прав.
            return ctypes.GetLastError() == 5
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Процесс существует, но нет прав сигналить его.
        return True
    except Exception:
        return False
    return True


def _acquire_single_instance_lock(backend_port: int, frontend_port: int, force: bool = False) -> bool:
    """Один активный launcher: при повторном запуске предыдущий экземпляр завершается."""
    if PID_FILE.exists():
        try:
            existing_pid_raw = PID_FILE.read_text(encoding="utf-8").strip()
            existing_pid = int(existing_pid_raw)
        except (ValueError, OSError):
            existing_pid = -1

        if existing_pid == os.getpid():
            return True

        if _is_process_running(existing_pid):
            _print_yellow(
                f"Обнаружен предыдущий экземпляр launcher (PID {existing_pid}). "
                "Принудительно завершаю и продолжаю запуск."
            )
            killed = _kill_pid_force(existing_pid)
            if not killed:
                _print_yellow(
                    f"Повтор через освобождение портов {backend_port} и {frontend_port} (uvicorn / Next.js) …"
                )
                _force_free_port(backend_port, "backend")
                _force_free_port(frontend_port, "frontend")
                time.sleep(0.4)
                killed = _kill_pid_force(existing_pid)
            if not killed:
                ports_busy = _is_port_in_use("127.0.0.1", backend_port) or _is_port_in_use(
                    "127.0.0.1", frontend_port
                )
                if not ports_busy:
                    _print_yellow(
                        f"PID {existing_pid} не завершился, но порты {backend_port}/{frontend_port} свободны — "
                        f"удаляю устаревший {PID_FILE.name}."
                    )
                    try:
                        PID_FILE.unlink()
                    except OSError as exc:
                        print(f"Не удалось удалить lock-файл: {exc}", file=sys.stderr)
                        return False
                elif force:
                    _print_yellow(
                        f"--force: не удалось завершить PID {existing_pid}; удаляю {PID_FILE.name} и продолжаю запуск. "
                        "Если порты 8000/3000 заняты старым процессом — остановите его вручную (Диспетчер задач или "
                        "Stop-Process от администратора)."
                    )
                    try:
                        PID_FILE.unlink()
                    except OSError as exc:
                        print(f"Не удалось удалить lock-файл: {exc}", file=sys.stderr)
                        return False
                else:
                    _print_red(
                        f"Не удалось завершить PID {existing_pid} (нужны права администратора или процесс защищён). "
                        f"Закройте процесс вручную, удалите файл {PID_FILE.name} в корне проекта, либо запустите: "
                        f"python main.py --force"
                    )
                    return False
            else:
                time.sleep(0.5)

        # Очистка lock после завершения или если PID уже не существует.
        try:
            PID_FILE.unlink(missing_ok=True)
        except OSError:
            print(f"Не удалось очистить старый lock-файл: {PID_FILE}", file=sys.stderr)
            return False

    try:
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except OSError as exc:
        print(f"Не удалось создать lock-файл запуска: {exc}", file=sys.stderr)
        return False
    return True


def _release_single_instance_lock() -> None:
    if not PID_FILE.exists():
        return
    try:
        lock_pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        lock_pid = None

    # Удаляем только собственный lock.
    if lock_pid == os.getpid():
        try:
            PID_FILE.unlink()
        except OSError:
            pass


def main() -> int:
    _enable_windows_ansi_colors()

    parser = argparse.ArgumentParser(description="Запуск ExTellect Digest (backend + frontend).")
    parser.add_argument("--no-install", action="store_true", help="Не вызывать pip/npm install перед стартом")
    parser.add_argument("--backend-only", action="store_true", help="Только FastAPI (uvicorn)")
    parser.add_argument("--frontend-only", action="store_true", help="Только Next.js (npm run dev)")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Режим разработки: uvicorn с автоперезапуском при изменении кода (на Windows создаёт доп. PID на порту)",
    )
    parser.add_argument(
        "--backend-port",
        type=int,
        default=None,
        metavar="N",
        help="Порт backend (иначе BACKEND_PORT из окружения или backend/.env, по умолчанию 8000)",
    )
    parser.add_argument(
        "--frontend-port",
        type=int,
        default=None,
        metavar="N",
        help="Порт Next.js dev (иначе FRONTEND_PORT из окружения или backend/.env, по умолчанию 3000)",
    )
    parser.add_argument(
        "--clean-frontend",
        action="store_true",
        help="Перед next dev удалить frontend/.next (при ошибках vendor-chunks, 404 на /_next/static)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Если старый launcher (PID из .app.pid) не завершается — удалить lock и продолжить (осторожно: возможен конфликт портов)",
    )
    args = parser.parse_args()

    if not BACKEND.is_dir():
        print(f"Не найдена папка backend: {BACKEND}", file=sys.stderr)
        return 1
    if not FRONTEND.is_dir():
        print(f"Не найдена папка frontend: {FRONTEND}", file=sys.stderr)
        return 1

    backend_port, frontend_port = _resolve_launch_ports(args.backend_port, args.frontend_port)

    npm_path: str | None = shutil.which("npm") if not args.backend_only else None
    if not args.backend_only and not npm_path:
        print("В PATH не найден npm. Установите Node.js LTS.", file=sys.stderr)
        return 1
    if not _acquire_single_instance_lock(backend_port, frontend_port, args.force):
        return 1

    if not args.frontend_only and _is_port_in_use("127.0.0.1", backend_port):
        if not _force_free_port(backend_port, "backend"):
            _print_red(f"Не удалось освободить порт {backend_port}. Запуск прерван.")
            _print_yellow(
                "Подсказка: в PowerShell (при необходимости от администратора): "
                f"Get-NetTCPConnection -LocalPort {backend_port} | Select-Object OwningProcess; "
                "Stop-Process -Id <PID> -Force. Либо задайте другой порт: "
                "переменная BACKEND_PORT или флаг --backend-port, и NEXT_PUBLIC_API_BASE во frontend/.env.local — см. README."
            )
            _release_single_instance_lock()
            return 1

    if not args.backend_only and _is_port_in_use("127.0.0.1", frontend_port):
        if not _force_free_port(frontend_port, "frontend"):
            _print_red(f"Не удалось освободить порт {frontend_port}. Запуск прерван.")
            _print_yellow(
                "Подсказка: см. выше (Get-NetTCPConnection / Stop-Process). "
                "Или другой порт: FRONTEND_PORT / --frontend-port и FRONTEND_ORIGIN в backend/.env — см. README."
            )
            _release_single_instance_lock()
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
            print(f"Запуск backend: http://127.0.0.1:{backend_port}", flush=True)
            uvicorn_cmd = [
                str(venv_python),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(backend_port),
            ]
            if args.reload:
                uvicorn_cmd.append("--reload")
            backend_proc = subprocess.Popen(uvicorn_cmd, cwd=str(BACKEND))

        if not args.backend_only:
            if not args.no_install:
                print("Установка зависимостей frontend (npm) ...", flush=True)
                _run_npm_install()
            if args.clean_frontend:
                _clean_frontend_next_cache()
            print(f"Запуск frontend: http://localhost:{frontend_port}", flush=True)
            frontend_proc = subprocess.Popen(
                [npm_path, "run", "dev", "--", "-p", str(frontend_port)],
                cwd=str(FRONTEND),
                shell=False,
            )

        _print_green("Процесс запущен. Работает. Нажмите Ctrl+C для остановки.")
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
        _release_single_instance_lock()
        _print_red("Процесс остановлен.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
