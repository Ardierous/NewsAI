import logging
import sys
from logging.handlers import RotatingFileHandler
from typing import Any

from app.config import Settings


def setup_logging(settings: Settings) -> None:
    """Единая настройка логирования приложения (консоль + ротация в файл)."""
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    log_file = settings.log_dir / settings.log_file_name

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
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Сторонние библиотеки — меньше шума
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)

    logging.getLogger(__name__).info("Логирование инициализировано: level=%s file=%s", settings.log_level, log_file)


def log_extra(**kwargs: Any) -> dict[str, Any]:
    """Удобная обёртка для structured extra (при необходимости расширить фильтрами)."""
    return kwargs
