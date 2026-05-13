from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    proxyapi_api_key: str
    proxyapi_base_url: str = "https://openai.api.proxyapi.ru/v1"
    proxyapi_model: str = "openai/gpt-4.1"
    proxyapi_image_model: str = "openai/gpt-image-1"
    database_url: str = "sqlite:///./digest.db"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_origin: str = "http://localhost:3000"
    enable_web_fetch: bool = False

    log_level: str = "INFO"
    log_dir: Path = BASE_DIR / "logs"
    log_file_name: str = "app.log"
    log_max_bytes: int = 5_000_000
    log_backup_count: int = 5
    log_enable_file: bool = True

    storage_dir: Path = BASE_DIR / "storage"
    image_dir: Path = BASE_DIR / "storage" / "images"
    docx_dir: Path = BASE_DIR / "storage" / "docx"
    prompts_path: Path = BASE_DIR / "app" / "prompts" / "digest_contract.txt"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    settings.image_dir.mkdir(parents=True, exist_ok=True)
    settings.docx_dir.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    return settings
