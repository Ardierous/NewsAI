from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Для локального запуска приоритет у backend/.env, чтобы внешние ENV не ломали поведение.
        return init_settings, dotenv_settings, env_settings, file_secret_settings

    proxyapi_api_key: str
    proxyapi_base_url: str = "https://openai.api.proxyapi.ru/v1"
    proxyapi_model: str = "openai/gpt-4.1"
    proxyapi_image_model: str = "openai/gpt-image-1"
    database_url: str = "sqlite:///./digest.db"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_origin: str = "http://localhost:3000"
    enable_web_fetch: bool = False
    serpapi_api_key: str | None = None
    tavily_api_key: str | None = None
    # Сумма по llm_cost_records step=step_1: после лимита добор и LLM-refill прекращаются.
    step1_max_cost_rub: float = 50.0
    # step_2 ordering: при суммарном расходе шага >= лимита OrderingAgent не вызывается.
    step2_max_cost_rub: float = 50.0

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
    source_tiers_path: Path = BASE_DIR / "app" / "prompts" / "source_tiers.txt"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    settings.image_dir.mkdir(parents=True, exist_ok=True)
    settings.docx_dir.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    return settings
