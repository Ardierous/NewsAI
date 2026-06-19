from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from app.pipeline_settings import pipeline_settings_flat


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

    # --- Секреты и деплой (.env) ---
    proxyapi_api_key: str
    proxyapi_base_url: str = "https://openai.api.proxyapi.ru/v1"
    proxyapi_model: str = "openai/gpt-4.1-mini"
    proxyapi_image_model: str = "openai/gpt-image-1"
    database_url: str = "sqlite:///./digest.db"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_origin: str = "http://localhost:3000"
    enable_web_fetch: bool = False
    # Веб-поиск (HTTP + ProxyAPI web_search). Основной источник — pipeline_settings.json → web.enable_fetch.
    proxyapi_web_search_enabled: bool = True
    proxyapi_web_search_model: str = "gpt-4o-mini"
    proxyapi_web_search_preview_model: str = "gpt-4o-mini-search-preview"
    proxyapi_web_search_context_size: str = "low"
    proxyapi_web_search_context_size_supplement: str = "low"
    serpapi_api_key: str | None = None
    tavily_api_key: str | None = None

    # --- Пайплайн (дефолты; основной источник — app/pipeline_settings.json) ---
    step1_search_tier1_min_raw_urls: int = 15
    step1_max_cost_rub: float = 50.0
    step1_max_web_search_api_calls: int = 0
    step1_web_search_api_bonus_near_target: int = 10
    step1_batch_size: int = 10
    step1_search_fetch_limit: int = 36
    step1_urls_checked_per_collect: int = 24
    step1_soft_time_limit_sec: int = 90
    step1_hard_time_limit_sec: int = 150
    step1_max_candidates_for_ui: int = 15
    step1_verify_workers: int = 6
    step1_crew_fallback_only_if_empty: bool = True
    step1_tier_strict_search: bool = True
    step1_curious_use_serious_tiers: bool = False
    step1_telegram_monitor_enabled: bool = True
    step1_telegram_monitor_channels: str = "technokratos"
    step1_telegram_max_pages: int = 2
    step1_telegram_max_links: int = 30
    step1_telegram_max_digest_posts: int = 3
    step1_telegram_post_text_filter: str = "Дайджест"
    step1_telegram_timeout_sec: float = 10.0
    step1_telegram_via_proxyapi: bool = False
    step1_telegram_direct_fallback: bool = True
    step1_telegram_proxyapi_context_size: str = "low"
    step1_tier_max_web_search_batches: int = 6
    step1_min_urls_before_proxyapi: int = 5
    step1_web_search_prefer_alt_providers: bool = False
    step1_web_search_cache_enabled: bool = True
    step1_web_search_cache_ttl_days: int = 90
    step1_url_registry_ttl_days: int = 90
    step1_url_registry_reuse_enabled: bool = True
    step1_registry_max_raw_when_short_pool: int = 8
    step1_host_unreachable_autoblock_threshold: int = 20
    step1_seed_urls_max: int = 35
    step2_max_cost_rub: float = 50.0
    auto_run_step3_after_order: bool = True
    enable_step4_image_generation: bool = False
    log_level: str = "INFO"
    log_dir: Path = BASE_DIR / "logs"
    log_file_name: str = "app.log"
    log_max_bytes: int = 5_000_000
    log_backup_count: int = 5
    log_enable_file: bool = True
    step1_log_filter_stats_every_n: int = 50
    step1_log_reject_audit_top_reasons: int = 5
    step1_log_reject_samples_per_reason: int = 8
    step1_curious_tone_log_enabled: bool = True
    step1_curious_tone_log_level: str = "INFO"
    step1_curious_tone_log_separate_file: bool = True
    step1_curious_tone_log_file_name: str = "step1-curious-tone.log"
    step1_curious_tone_log_accept: bool = True
    step1_curious_tone_log_reject: bool = True
    step1_curious_tone_log_low_tone: bool = True
    step1_curious_tone_log_max_events: int = 200
    step1_curious_tone_title_preview_chars: int = 120
    step1_curious_tone_corpus_preview_chars: int = 160
    step1_curious_tone_include_signals: bool = True
    step1_curious_yield_enabled: bool = True
    step1_curious_yield_min_verified: int = 10
    step1_curious_yield_target_pool: int = 12
    step1_curious_yield_soft_time_sec: int = 480
    step1_curious_yield_hard_time_sec: int = 600
    step1_curious_yield_max_cost_rub: float = 50.0
    step1_curious_yield_min_iterations: int = 2
    step1_curious_yield_supplement_rounds: int = 2
    step1_curious_yield_max_search_batches: int = 5
    step1_curious_yield_skip_aggregator_search: bool = True
    step1_curious_yield_no_progress_limit: int = 2
    step1_curious_yield_seed_listing_scan_limit: int = 4
    step1_curious_yield_entertainment_rescue_queries: int = 2
    step1_curious_yield_rescue_collect_batch: int = 8

    storage_dir: Path = BASE_DIR / "storage"
    image_dir: Path = BASE_DIR / "storage" / "images"
    docx_dir: Path = BASE_DIR / "storage" / "docx"
    step1_manual_ratings_path: Path = BASE_DIR / "storage" / "step1_manual_ratings.json"
    prompts_path: Path = BASE_DIR / "app" / "prompts" / "digest_contract.txt"
    source_tiers_path: Path = BASE_DIR / "app" / "prompts" / "source_tiers.txt"
    curious_source_hosts_path: Path = BASE_DIR / "app" / "prompts" / "curious_source_hosts.txt"


def _apply_pipeline_settings(settings: Settings) -> None:
    """JSON-пайплайн поверх дефолтов класса; явные значения из .env/env не перезаписываем."""
    for key, value in pipeline_settings_flat().items():
        if key in Settings.model_fields and key not in settings.model_fields_set:
            setattr(settings, key, value)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    _apply_pipeline_settings(settings)
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    settings.image_dir.mkdir(parents=True, exist_ok=True)
    settings.docx_dir.mkdir(parents=True, exist_ok=True)
    settings.step1_manual_ratings_path.parent.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    return settings


def clear_settings_cache() -> None:
    get_settings.cache_clear()
    from app.pipeline_settings import get_pipeline_config

    get_pipeline_config.cache_clear()
