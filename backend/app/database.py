from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import get_settings

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


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


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
