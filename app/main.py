"""FastAPI application entry point."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.api.v1 import api_router
from app.core import configure_logging, logger, settings
from app.db import Database
from app.middleware import RequestLoggingMiddleware, register_exception_handlers
from app.scheduler import start_scheduler



@asynccontextmanager
async def lifespan(_: FastAPI):
    """Startup / shutdown hooks."""
    configure_logging()
    logger.info("=" * 60)
    logger.info(f"  {settings.app_name} v1.0.0")
    logger.info(f"  Env: {settings.app_env} · Debug: {settings.debug}")
    logger.info("=" * 60)

    try:
        Database.init_pool()
        start_scheduler()
    except Exception as e:  # noqa: BLE001
        logger.error(f"DB pool init failed or scheduler failed: {e}")
        logger.warning(
            "Continuing without DB — endpoints that touch the DB will return errors. "
            "Apply database/schema.sql and database/seed.sql before retrying."
        )

    yield

    Database.close_pool()
    logger.info("Application shut down cleanly")


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Enterprise AI-driven incident management. Ingests ITSM tickets, "
        "monitoring alerts, and user queries; classifies; auto-remediates; "
        "escalates with diagnostic context; and learns continuously."
    ),
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# ---- Middleware ------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)

# ---- Exception handlers ----------------------------------------------------
register_exception_handlers(app)

# ---- Routes ----------------------------------------------------------------
app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Redirect bare host to API docs for convenience."""
    return RedirectResponse(url="/api/docs")


@app.get("/health", include_in_schema=False)
async def health_root() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.debug,
        log_level="info",
    )
