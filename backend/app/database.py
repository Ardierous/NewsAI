from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import get_settings

settings = get_settings()

_is_sqlite = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False, "timeout": 30} if _is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=connect_args)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    if not _is_sqlite:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def _normalize_step1_url_registry_unified_keys(conn) -> None:
    """Снять префиксы serious:/curious: — единый ключ URL в реестре."""
    try:
        rows = conn.execute(
            text(
                """
                SELECT id, url_fingerprint FROM step1_url_registry
                WHERE url_fingerprint LIKE 'serious:%' OR url_fingerprint LIKE 'curious:%'
                """
            )
        ).fetchall()
        for row_id, fp in rows:
            bare = str(fp).split(":", 1)[1] if ":" in str(fp) else str(fp)
            dup = conn.execute(
                text(
                    "SELECT id FROM step1_url_registry WHERE url_fingerprint = :bare AND id != :row_id LIMIT 1"
                ),
                {"bare": bare, "row_id": row_id},
            ).fetchone()
            if dup:
                conn.execute(text("DELETE FROM step1_url_registry WHERE id = :row_id"), {"row_id": row_id})
            else:
                conn.execute(
                    text("UPDATE step1_url_registry SET url_fingerprint = :bare WHERE id = :row_id"),
                    {"bare": bare, "row_id": row_id},
                )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def ensure_digest_schema_migrations() -> None:
    """Лёгкие миграции без Alembic: новые колонки в существующей БД."""
    url = str(engine.url)
    if url.startswith("sqlite"):
        with engine.connect() as conn:
            rows = conn.execute(text("PRAGMA table_info(digests)")).fetchall()
            names = {row[1] for row in rows}
            if "digest_type_via_default" not in names:
                conn.execute(text("ALTER TABLE digests ADD COLUMN digest_type_via_default INTEGER NOT NULL DEFAULT 0"))
                conn.commit()
            rows_nc = conn.execute(text("PRAGMA table_info(news_candidates)")).fetchall()
            nc_names = {row[1] for row in rows_nc}
            if nc_names and "page_verified" not in nc_names:
                conn.execute(text("ALTER TABLE news_candidates ADD COLUMN page_verified INTEGER NOT NULL DEFAULT 0"))
                conn.commit()
            rows_nc = conn.execute(text("PRAGMA table_info(news_candidates)")).fetchall()
            nc_names = {row[1] for row in rows_nc}
            if nc_names and "headline_editorial_ok" not in nc_names:
                conn.execute(text("ALTER TABLE news_candidates ADD COLUMN headline_editorial_ok INTEGER NOT NULL DEFAULT 0"))
                conn.commit()
            rows_nc = conn.execute(text("PRAGMA table_info(news_candidates)")).fetchall()
            nc_names = {row[1] for row in rows_nc}
            if nc_names and "article_excerpt" not in nc_names:
                conn.execute(text("ALTER TABLE news_candidates ADD COLUMN article_excerpt TEXT NOT NULL DEFAULT ''"))
                conn.commit()
                conn.execute(
                    text(
                        "UPDATE news_candidates SET headline_editorial_ok = 1 "
                        "WHERE COALESCE(page_verified, 0) != 0 AND COALESCE(link_status, 0) != 0"
                    )
                )
                conn.commit()
            if "step1_budget_capped" not in names:
                conn.execute(text("ALTER TABLE digests ADD COLUMN step1_budget_capped INTEGER NOT NULL DEFAULT 0"))
                conn.commit()
            if "step2_budget_capped" not in names:
                conn.execute(text("ALTER TABLE digests ADD COLUMN step2_budget_capped INTEGER NOT NULL DEFAULT 0"))
                conn.commit()
            if "news_window_days" not in names:
                conn.execute(text("ALTER TABLE digests ADD COLUMN news_window_days INTEGER NOT NULL DEFAULT 3"))
                conn.commit()
            if "news_window_day_kind" not in names:
                conn.execute(
                    text("ALTER TABLE digests ADD COLUMN news_window_day_kind VARCHAR(16) NOT NULL DEFAULT 'working'")
                )
                conn.commit()
            if "step4_selected_image_variant" not in names:
                conn.execute(text("ALTER TABLE digests ADD COLUMN step4_selected_image_variant INTEGER"))
                conn.commit()
            if "analytics" in {
                r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
            }:
                an_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(analytics)")).fetchall()}
                if an_cols and "reader_text" not in an_cols:
                    conn.execute(text("ALTER TABLE analytics ADD COLUMN reader_text TEXT NOT NULL DEFAULT ''"))
                    conn.commit()
            rows = conn.execute(text("PRAGMA table_info(digests)")).fetchall()
            names = {row[1] for row in rows}
            if "proxyapi_balance_session_start" not in names:
                conn.execute(text("ALTER TABLE digests ADD COLUMN proxyapi_balance_session_start REAL"))
                conn.commit()
            if "proxyapi_budget_used_session_start" not in names:
                conn.execute(text("ALTER TABLE digests ADD COLUMN proxyapi_budget_used_session_start REAL"))
                conn.commit()
            if "proxyapi_balance_before" not in names:
                conn.execute(text("ALTER TABLE digests ADD COLUMN proxyapi_balance_before REAL"))
                conn.commit()
            if "proxyapi_balance_after" not in names:
                conn.execute(text("ALTER TABLE digests ADD COLUMN proxyapi_balance_after REAL"))
                conn.commit()
            if "proxyapi_budget_used_before" not in names:
                conn.execute(text("ALTER TABLE digests ADD COLUMN proxyapi_budget_used_before REAL"))
                conn.commit()
            if "proxyapi_budget_used_after" not in names:
                conn.execute(text("ALTER TABLE digests ADD COLUMN proxyapi_budget_used_after REAL"))
                conn.commit()
            for col in (
                "proxyapi_release_open_balance",
                "proxyapi_release_open_budget_used",
                "proxyapi_finalized_cost_rub",
            ):
                if col not in names:
                    conn.execute(text(f"ALTER TABLE digests ADD COLUMN {col} REAL"))
                    conn.commit()
                    names.add(col)
            if "proxyapi_finalized_at" not in names:
                conn.execute(text("ALTER TABLE digests ADD COLUMN proxyapi_finalized_at DATETIME"))
                conn.commit()
            if "proxyapi_spend_days" not in {
                r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
            }:
                conn.execute(
                    text(
                        """
                        CREATE TABLE proxyapi_spend_days (
                            day DATE PRIMARY KEY,
                            opening_balance REAL,
                            last_balance REAL,
                            opening_budget_used REAL,
                            last_budget_used REAL
                        )
                        """
                    )
                )
                conn.commit()
            if "step1_discovered_news" not in {
                r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
            }:
                conn.execute(
                    text(
                        """
                        CREATE TABLE step1_discovered_news (
                            id INTEGER PRIMARY KEY,
                            digest_id INTEGER NOT NULL,
                            source_stage VARCHAR(40) NOT NULL DEFAULT 'unknown',
                            title VARCHAR(500) NOT NULL,
                            url VARCHAR(1000) NOT NULL,
                            source VARCHAR(255) NOT NULL DEFAULT '',
                            published_at VARCHAR(100) NOT NULL DEFAULT '',
                            headline_editorial_ok INTEGER NOT NULL DEFAULT 0,
                            link_status INTEGER NOT NULL DEFAULT 0,
                            page_verified INTEGER NOT NULL DEFAULT 0,
                            reject_codes TEXT NOT NULL DEFAULT '',
                            verification_comment TEXT NOT NULL DEFAULT '',
                            manual_score INTEGER,
                            manual_reason VARCHAR(64),
                            manual_reason_other TEXT,
                            rated_at DATETIME,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY(digest_id) REFERENCES digests(id) ON DELETE CASCADE
                        )
                        """
                    )
                )
                conn.commit()
            if "step1_discovery_runs" not in {
                r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
            }:
                conn.execute(
                    text(
                        """
                        CREATE TABLE step1_discovery_runs (
                            id INTEGER PRIMARY KEY,
                            digest_id INTEGER NOT NULL,
                            run_number INTEGER NOT NULL,
                            started_at DATETIME NOT NULL,
                            pool_formed_at DATETIME,
                            news_count INTEGER NOT NULL DEFAULT 0,
                            FOREIGN KEY(digest_id) REFERENCES digests(id) ON DELETE CASCADE
                        )
                        """
                    )
                )
                conn.commit()
            rows_s1r = conn.execute(text("PRAGMA table_info(step1_discovery_runs)")).fetchall()
            s1r_names = {row[1] for row in rows_s1r}
            if s1r_names and "duration_sec" not in s1r_names:
                conn.execute(text("ALTER TABLE step1_discovery_runs ADD COLUMN duration_sec INTEGER"))
                conn.commit()
            rows_s1r = conn.execute(text("PRAGMA table_info(step1_discovery_runs)")).fetchall()
            s1r_names = {row[1] for row in rows_s1r}
            if s1r_names and "cost_rub" not in s1r_names:
                conn.execute(text("ALTER TABLE step1_discovery_runs ADD COLUMN cost_rub REAL"))
                conn.commit()
            rows_sd = conn.execute(text("PRAGMA table_info(step1_discovered_news)")).fetchall()
            sd_names = {row[1] for row in rows_sd}
            if sd_names and "discovery_run_id" not in sd_names:
                conn.execute(text("ALTER TABLE step1_discovered_news ADD COLUMN discovery_run_id INTEGER"))
                conn.commit()
            if "step1_manual_rating_log" not in {
                r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
            }:
                conn.execute(
                    text(
                        """
                        CREATE TABLE step1_manual_rating_log (
                            id INTEGER PRIMARY KEY,
                            discovery_run_id INTEGER NOT NULL,
                            digest_id INTEGER NOT NULL,
                            pool_date DATE NOT NULL,
                            run_number INTEGER NOT NULL,
                            discovered_news_id INTEGER,
                            title VARCHAR(500) NOT NULL,
                            url VARCHAR(1000) NOT NULL,
                            published_at VARCHAR(100) NOT NULL DEFAULT '',
                            manual_score INTEGER NOT NULL,
                            manual_reason VARCHAR(64),
                            manual_reason_other TEXT,
                            rated_at DATETIME NOT NULL,
                            FOREIGN KEY(discovery_run_id) REFERENCES step1_discovery_runs(id) ON DELETE CASCADE,
                            FOREIGN KEY(digest_id) REFERENCES digests(id) ON DELETE CASCADE
                        )
                        """
                    )
                )
                conn.commit()
            if "step1_web_search_cache" not in {
                r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
            }:
                conn.execute(
                    text(
                        """
                        CREATE TABLE step1_web_search_cache (
                            cache_key VARCHAR(64) PRIMARY KEY,
                            urls_json TEXT NOT NULL,
                            query_preview VARCHAR(500) NOT NULL DEFAULT '',
                            url_count INTEGER NOT NULL DEFAULT 0,
                            hit_count INTEGER NOT NULL DEFAULT 0,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                            last_hit_at DATETIME
                        )
                        """
                    )
                )
                conn.execute(
                    text("CREATE INDEX IF NOT EXISTS ix_step1_web_search_cache_created_at ON step1_web_search_cache (created_at)")
                )
                conn.commit()
            if "step1_url_registry" not in {
                r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
            }:
                conn.execute(
                    text(
                        """
                        CREATE TABLE step1_url_registry (
                            id INTEGER PRIMARY KEY,
                            url_fingerprint VARCHAR(512) NOT NULL UNIQUE,
                            url VARCHAR(1000) NOT NULL,
                            host VARCHAR(255) NOT NULL DEFAULT '',
                            digest_type VARCHAR(20) NOT NULL DEFAULT 'serious',
                            bucket VARCHAR(80) NOT NULL DEFAULT 'raw',
                            reject_codes TEXT NOT NULL DEFAULT '',
                            title VARCHAR(500) NOT NULL DEFAULT '',
                            source_stage VARCHAR(40) NOT NULL DEFAULT 'search',
                            verification_comment TEXT NOT NULL DEFAULT '',
                            last_digest_id INTEGER,
                            first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            expires_at DATETIME NOT NULL
                        )
                        """
                    )
                )
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_step1_url_registry_bucket ON step1_url_registry (bucket)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_step1_url_registry_host ON step1_url_registry (host)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_step1_url_registry_expires ON step1_url_registry (expires_at)"))
                conn.commit()
            _normalize_step1_url_registry_unified_keys(conn)
            if "step1_host_unreachable_stats" not in {
                r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
            }:
                conn.execute(
                    text(
                        """
                        CREATE TABLE step1_host_unreachable_stats (
                            host VARCHAR(255) PRIMARY KEY,
                            failure_count INTEGER NOT NULL DEFAULT 0,
                            first_failure_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            last_failure_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            autoblocked_at DATETIME
                        )
                        """
                    )
                )
                conn.commit()
            if "step1_filter_enabled_snapshots" not in {
                r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
            }:
                conn.execute(
                    text(
                        """
                        CREATE TABLE step1_filter_enabled_snapshots (
                            digest_type VARCHAR(20) PRIMARY KEY,
                            enabled_json TEXT NOT NULL DEFAULT '{}',
                            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    )
                )
                conn.commit()
        return
    from sqlalchemy import inspect

    insp = inspect(engine)
    if not insp.has_table("digests"):
        return
    cols = {c["name"] for c in insp.get_columns("digests")}
    if "digest_type_via_default" not in cols:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE digests ADD COLUMN digest_type_via_default BOOLEAN NOT NULL DEFAULT false"
                )
            )
    if insp.has_table("news_candidates"):
        nc_cols = {c["name"] for c in insp.get_columns("news_candidates")}
        if "page_verified" not in nc_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE news_candidates ADD COLUMN page_verified BOOLEAN NOT NULL DEFAULT false"))
        nc_cols = {c["name"] for c in insp.get_columns("news_candidates")}
        if "headline_editorial_ok" not in nc_cols:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE news_candidates ADD COLUMN headline_editorial_ok BOOLEAN NOT NULL DEFAULT false"
                    )
                )
                conn.execute(
                    text(
                        "UPDATE news_candidates SET headline_editorial_ok = (page_verified AND link_status) "
                        "WHERE headline_editorial_ok IS NOT DISTINCT FROM false"
                    )
                )
        nc_cols = {c["name"] for c in insp.get_columns("news_candidates")}
        if "article_excerpt" not in nc_cols:
            with engine.begin() as conn:
                conn.execute(
                    text("ALTER TABLE news_candidates ADD COLUMN article_excerpt TEXT NOT NULL DEFAULT ''")
                )
    if "step1_budget_capped" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE digests ADD COLUMN step1_budget_capped BOOLEAN NOT NULL DEFAULT false"))
    if "step2_budget_capped" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE digests ADD COLUMN step2_budget_capped BOOLEAN NOT NULL DEFAULT false"))
    cols = {c["name"] for c in insp.get_columns("digests")}
    if "news_window_days" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE digests ADD COLUMN news_window_days INTEGER NOT NULL DEFAULT 3"))
    cols = {c["name"] for c in insp.get_columns("digests")}
    if "news_window_day_kind" not in cols:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE digests ADD COLUMN news_window_day_kind VARCHAR(16) NOT NULL DEFAULT 'working'")
            )
    cols = {c["name"] for c in insp.get_columns("digests")}
    if "step4_selected_image_variant" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE digests ADD COLUMN step4_selected_image_variant INTEGER"))
    if insp.has_table("analytics"):
        an_cols = {c["name"] for c in insp.get_columns("analytics")}
        if "reader_text" not in an_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE analytics ADD COLUMN reader_text TEXT NOT NULL DEFAULT ''"))
    cols = {c["name"] for c in insp.get_columns("digests")}
    if "proxyapi_balance_session_start" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE digests ADD COLUMN proxyapi_balance_session_start DOUBLE PRECISION"))
    cols = {c["name"] for c in insp.get_columns("digests")}
    if "proxyapi_budget_used_session_start" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE digests ADD COLUMN proxyapi_budget_used_session_start DOUBLE PRECISION"))
    cols = {c["name"] for c in insp.get_columns("digests")}
    if "proxyapi_balance_before" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE digests ADD COLUMN proxyapi_balance_before DOUBLE PRECISION"))
    cols = {c["name"] for c in insp.get_columns("digests")}
    if "proxyapi_balance_after" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE digests ADD COLUMN proxyapi_balance_after DOUBLE PRECISION"))
    cols = {c["name"] for c in insp.get_columns("digests")}
    if "proxyapi_budget_used_before" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE digests ADD COLUMN proxyapi_budget_used_before DOUBLE PRECISION"))
    cols = {c["name"] for c in insp.get_columns("digests")}
    if "proxyapi_budget_used_after" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE digests ADD COLUMN proxyapi_budget_used_after DOUBLE PRECISION"))
    cols = {c["name"] for c in insp.get_columns("digests")}
    for col in (
        "proxyapi_release_open_balance",
        "proxyapi_release_open_budget_used",
        "proxyapi_finalized_cost_rub",
    ):
        if col not in cols:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE digests ADD COLUMN {col} DOUBLE PRECISION"))
    cols = {c["name"] for c in insp.get_columns("digests")}
    if "proxyapi_finalized_at" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE digests ADD COLUMN proxyapi_finalized_at TIMESTAMP"))
    if not insp.has_table("proxyapi_spend_days"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE proxyapi_spend_days (
                        day DATE PRIMARY KEY,
                        opening_balance DOUBLE PRECISION,
                        last_balance DOUBLE PRECISION,
                        opening_budget_used DOUBLE PRECISION,
                        last_budget_used DOUBLE PRECISION
                    )
                    """
                )
            )
    if not insp.has_table("step1_discovered_news"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE step1_discovered_news (
                        id SERIAL PRIMARY KEY,
                        digest_id INTEGER NOT NULL REFERENCES digests(id) ON DELETE CASCADE,
                        source_stage VARCHAR(40) NOT NULL DEFAULT 'unknown',
                        title VARCHAR(500) NOT NULL,
                        url VARCHAR(1000) NOT NULL,
                        source VARCHAR(255) NOT NULL DEFAULT '',
                        published_at VARCHAR(100) NOT NULL DEFAULT '',
                        headline_editorial_ok BOOLEAN NOT NULL DEFAULT false,
                        link_status BOOLEAN NOT NULL DEFAULT false,
                        page_verified BOOLEAN NOT NULL DEFAULT false,
                        reject_codes TEXT NOT NULL DEFAULT '',
                        verification_comment TEXT NOT NULL DEFAULT '',
                        manual_score INTEGER,
                        manual_reason VARCHAR(64),
                        manual_reason_other TEXT,
                        rated_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                    """
                )
            )
    if not insp.has_table("step1_discovery_runs"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE step1_discovery_runs (
                        id SERIAL PRIMARY KEY,
                        digest_id INTEGER NOT NULL REFERENCES digests(id) ON DELETE CASCADE,
                        run_number INTEGER NOT NULL,
                        started_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        pool_formed_at TIMESTAMP,
                        news_count INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
            )
    if insp.has_table("step1_discovered_news"):
        sd_cols = {c["name"] for c in insp.get_columns("step1_discovered_news")}
        if "discovery_run_id" not in sd_cols:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE step1_discovered_news "
                        "ADD COLUMN discovery_run_id INTEGER REFERENCES step1_discovery_runs(id) ON DELETE SET NULL"
                    )
                )
    if not insp.has_table("step1_manual_rating_log"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE step1_manual_rating_log (
                        id SERIAL PRIMARY KEY,
                        discovery_run_id INTEGER NOT NULL REFERENCES step1_discovery_runs(id) ON DELETE CASCADE,
                        digest_id INTEGER NOT NULL REFERENCES digests(id) ON DELETE CASCADE,
                        pool_date DATE NOT NULL,
                        run_number INTEGER NOT NULL,
                        discovered_news_id INTEGER,
                        title VARCHAR(500) NOT NULL,
                        url VARCHAR(1000) NOT NULL,
                        published_at VARCHAR(100) NOT NULL DEFAULT '',
                        manual_score INTEGER NOT NULL,
                        manual_reason VARCHAR(64),
                        manual_reason_other TEXT,
                        rated_at TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
    if not insp.has_table("step1_web_search_cache"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE step1_web_search_cache (
                        cache_key VARCHAR(64) PRIMARY KEY,
                        urls_json TEXT NOT NULL,
                        query_preview VARCHAR(500) NOT NULL DEFAULT '',
                        url_count INTEGER NOT NULL DEFAULT 0,
                        hit_count INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMP DEFAULT NOW(),
                        last_hit_at TIMESTAMP
                    )
                    """
                )
            )
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_step1_web_search_cache_created_at ON step1_web_search_cache (created_at)")
            )
    if not insp.has_table("step1_url_registry"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE step1_url_registry (
                        id SERIAL PRIMARY KEY,
                        url_fingerprint VARCHAR(512) NOT NULL UNIQUE,
                        url VARCHAR(1000) NOT NULL,
                        host VARCHAR(255) NOT NULL DEFAULT '',
                        digest_type VARCHAR(20) NOT NULL DEFAULT 'serious',
                        bucket VARCHAR(80) NOT NULL DEFAULT 'raw',
                        reject_codes TEXT NOT NULL DEFAULT '',
                        title VARCHAR(500) NOT NULL DEFAULT '',
                        source_stage VARCHAR(40) NOT NULL DEFAULT 'search',
                        verification_comment TEXT NOT NULL DEFAULT '',
                        last_digest_id INTEGER,
                        first_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        last_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        expires_at TIMESTAMP NOT NULL
                    )
                    """
                )
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_step1_url_registry_bucket ON step1_url_registry (bucket)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_step1_url_registry_host ON step1_url_registry (host)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_step1_url_registry_expires ON step1_url_registry (expires_at)"))
    if insp.has_table("step1_url_registry"):
        with engine.begin() as conn:
            _normalize_step1_url_registry_unified_keys(conn)
    if not insp.has_table("step1_host_unreachable_stats"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE step1_host_unreachable_stats (
                        host VARCHAR(255) PRIMARY KEY,
                        failure_count INTEGER NOT NULL DEFAULT 0,
                        first_failure_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        last_failure_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        autoblocked_at TIMESTAMP
                    )
                    """
                )
            )
    if not insp.has_table("step1_filter_enabled_snapshots"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE step1_filter_enabled_snapshots (
                        digest_type VARCHAR(20) PRIMARY KEY,
                        enabled_json TEXT NOT NULL DEFAULT '{}',
                        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
