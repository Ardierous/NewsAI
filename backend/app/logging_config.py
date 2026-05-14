import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

from app.config import Settings


class DailyFileHandler(logging.Handler):
    """Пишет в app-YYYY-MM-DD.log и переключает файл при смене даты."""

    def __init__(self, log_dir: Path, base_name: str, keep_files: int) -> None:
        super().__init__()
        self.log_dir = log_dir
        self.base_name = base_name
        self.keep_files = keep_files
        self.current_date = ""
        self.file_handler: logging.FileHandler | None = None
        self._switch_if_needed()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._switch_if_needed()
            if self.file_handler:
                self.file_handler.emit(record)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        try:
            if self.file_handler:
                self.file_handler.close()
                self.file_handler = None
        finally:
            super().close()

    def setFormatter(self, fmt: logging.Formatter) -> None:  # noqa: N802
        super().setFormatter(fmt)
        if self.file_handler:
            self.file_handler.setFormatter(fmt)

    def _switch_if_needed(self) -> None:
        today = date.today().isoformat()
        if today == self.current_date and self.file_handler:
            return
        if self.file_handler:
            self.file_handler.close()
        log_file = self.log_dir / f"{self.base_name}-{today}.log"
        self.file_handler = logging.FileHandler(log_file, encoding="utf-8")
        if self.formatter:
            self.file_handler.setFormatter(self.formatter)
        self.current_date = today
        _cleanup_old_daily_logs(self.log_dir, self.base_name, keep=self.keep_files)


def setup_logging(settings: Settings) -> None:
    """Единая настройка логирования приложения (консоль + ежедневный файл)."""
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    log_base_name = Path(settings.log_file_name).stem

    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt=fmt, datefmt=datefmt)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    if settings.log_enable_file:
        file_handler = DailyFileHandler(
            log_dir=settings.log_dir,
            base_name=log_base_name,
            keep_files=settings.log_backup_count,
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Сторонние библиотеки — меньше шума
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Логирование инициализировано: level=%s pattern=%s-%s.log keep=%s",
        settings.log_level,
        log_base_name,
        date.today().isoformat(),
        settings.log_backup_count,
    )


def log_extra(**kwargs: Any) -> dict[str, Any]:
    """Удобная обёртка для structured extra (при необходимости расширить фильтрами)."""
    return kwargs


def _cleanup_old_daily_logs(log_dir: Path, base_name: str, keep: int) -> None:
    """Оставляет только `keep` последних ежедневных логов <base>-YYYY-MM-DD.log."""
    files = sorted(log_dir.glob(f"{base_name}-*.log"), key=lambda p: p.name, reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            logging.getLogger(__name__).warning("Не удалось удалить старый лог-файл: %s", old)
