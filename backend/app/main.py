import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes_digests import router as digests_router
from app.config import get_settings
from app.database import Base, engine, ensure_digest_schema_migrations
from app.logging_config import setup_logging
from app.middleware.request_logging import RequestLoggingMiddleware
from app.services.digest_service import DigestService

settings = get_settings()
setup_logging(settings)
logger = logging.getLogger("app.main")
scheduler = BackgroundScheduler(timezone="Europe/Moscow")


def scheduled_digest_generation() -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        service = DigestService(db)
        service.create_digest_for_today()
    except Exception:
        logger.exception("Ошибка планировщика: ежедневное создание выпуска")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Старт приложения: создание таблиц БД и планировщика")
    Base.metadata.create_all(bind=engine)
    ensure_digest_schema_migrations()
    try:
        from app.database import SessionLocal
        from app.services.cost_tracker import ProxyApiCostTracker, touch_proxyapi_spend_day

        db = SessionLocal()
        try:
            touch_proxyapi_spend_day(db, ProxyApiCostTracker().get_balance_snapshot())
        finally:
            db.close()
    except Exception:
        logger.warning("Не удалось инициализировать снимок баланса ProxyAPI за день", exc_info=True)
    try:
        from app.database import SessionLocal
        from app.services.step1_manual_ratings_export import sync_step1_manual_ratings_export

        db = SessionLocal()
        try:
            sync_step1_manual_ratings_export(db, settings.step1_manual_ratings_path)
        finally:
            db.close()
    except Exception:
        logger.warning("Не удалось собрать файл ручных оценок шага 1 при старте", exc_info=True)
    scheduler.add_job(scheduled_digest_generation, "cron", hour=8, minute=0, id="daily_digest_job", replace_existing=True)
    scheduler.start()
    yield
    logger.info("Остановка планировщика")
    scheduler.shutdown(wait=False)


app = FastAPI(title="ExTellect Digest MVP", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(digests_router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    logger.warning("HTTP %s | %s %s | detail=%s", exc.status_code, request.method, request.url.path, exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.warning("Validation error | %s %s | %s", request.method, request.url.path, exc.errors())
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Необработанная ошибка | %s %s", request.method, request.url.path)
    detail = str(exc).strip() or "Внутренняя ошибка сервера"
    if len(detail) > 500:
        detail = detail[:497] + "..."
    return JSONResponse(status_code=500, content={"detail": detail})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
