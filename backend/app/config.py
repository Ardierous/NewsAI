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
    # Веб-поиск новостей через ProxyAPI (OpenAI Responses + tool web_search). Требует ENABLE_WEB_FETCH=true.
    proxyapi_web_search_enabled: bool = True
    proxyapi_web_search_model: str = "gpt-4o-mini"
    proxyapi_web_search_preview_model: str = "gpt-4o-mini-search-preview"
    proxyapi_web_search_context_size: str = "medium"
    # Меньший контекст для добора / второго запроса (дешевле по тарифу ProxyAPI web_search).
    proxyapi_web_search_context_size_supplement: str = "low"
    # Второй запрос (tier-1 site:) только если после основного поиска сырых URL меньше порога.
    step1_search_tier1_min_raw_urls: int = 15
    serpapi_api_key: str | None = None
    tavily_api_key: str | None = None
    # Сумма по llm_cost_records step=step_1: после лимита добор и LLM-refill прекращаются.
    step1_max_cost_rub: float = 50.0
    # Шаг 1: итеративный добор кандидатов.
    step1_batch_size: int = 20
    # Сколько URL запрашивать у провайдеров поиска за один проход (до HTTP).
    step1_search_fetch_limit: int = 100
    # Сколько уникальных URL прогонять через HTTP за один вызов collect.
    step1_urls_checked_per_collect: int = 80
    step1_soft_time_limit_sec: int = 180
    step1_hard_time_limit_sec: int = 300
    step1_max_candidates_for_ui: int = 15
    # Параллельные HTTP-проверки страниц на шаге 1 (без LLM-перевода заголовков).
    step1_verify_workers: int = 6
    # CrewAI-добор только если веб-поиск не дал ни одной проверенной статьи (экономия 10–20 мин).
    step1_crew_fallback_only_if_empty: bool = True
    # Мониторинг публичных TG-каналов (t.me/s/): в шаг 1 подмешиваются внешние URL из постов, не сами посты.
    step1_telegram_monitor_enabled: bool = True
    step1_telegram_monitor_channels: str = "technokratos"
    step1_telegram_max_pages: int = 2
    step1_telegram_max_links: int = 30
    step1_seed_urls_max: int = 35
    # step_2 ordering: при суммарном расходе шага >= лимита OrderingAgent не вызывается.
    step2_max_cost_rub: float = 50.0
    # После «Применить порядок» / AI-порядка автоматически запускать шаг 3 (аналитика).
    auto_run_step3_after_order: bool = True
    # Генерация обложек на шаге 4 (gpt-image через ProxyAPI).
    enable_step4_image_generation: bool = False

    log_level: str = "INFO"
    log_dir: Path = BASE_DIR / "logs"
    log_file_name: str = "app.log"
    log_max_bytes: int = 5_000_000
    log_backup_count: int = 5
    log_enable_file: bool = True

    storage_dir: Path = BASE_DIR / "storage"
    image_dir: Path = BASE_DIR / "storage" / "images"
    docx_dir: Path = BASE_DIR / "storage" / "docx"
    step1_manual_ratings_path: Path = BASE_DIR / "storage" / "step1_manual_ratings.json"
    prompts_path: Path = BASE_DIR / "app" / "prompts" / "digest_contract.txt"
    source_tiers_path: Path = BASE_DIR / "app" / "prompts" / "source_tiers.txt"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    settings.image_dir.mkdir(parents=True, exist_ok=True)
    settings.docx_dir.mkdir(parents=True, exist_ok=True)
    settings.step1_manual_ratings_path.parent.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    return settings
