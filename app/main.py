from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .admin.router import router as admin_router
from .core.auth_middleware import AuthMiddleware
from .core.database import dispose_engines, get_firebird_engine, get_metastore_engine
from .core.dynamic_loader import dynamic_loader
from .core.execution_service import execution_service
from .core.middleware import RequestSecurityMiddleware
from .core.migrations import current_revision, head_revision, upgrade_metastore
from .core.settings import get_settings
from .ui import install_ui

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    upgrade_metastore()
    execution_service.startup()
    dynamic_loader.load_routes()
    dynamic_loader.apply_to_app(app)
    try:
        yield
    finally:
        await execution_service.shutdown()
        dispose_engines()


app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description="Transforme consultas Firebird em APIs HTTP seguras e dinamicas.",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    swagger_ui_parameters={"persistAuthorization": True},
)
app.include_router(admin_router, prefix="/api/base/admin", include_in_schema=False)
app.mount("/assets", StaticFiles(directory="app/assets"), name="assets")

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
    )
app.add_middleware(AuthMiddleware)
app.add_middleware(RequestSecurityMiddleware)


@app.exception_handler(Exception)
async def unhandled_exception(_request: Request, exc: Exception):
    logging.getLogger(__name__).error("Erro nao tratado error_type=%s", type(exc).__name__)
    return JSONResponse(
        {
            "success": False,
            "message": "Erro interno",
            "data": [],
            "errors": [{"detail": "INTERNAL_ERROR"}],
            "meta": {},
        },
        status_code=500,
    )


@app.get("/health", tags=["System"])
def health():
    return {"status": "healthy", "version": settings.api_version}


@app.get("/ready", tags=["System"])
def ready():
    checks = {"metastore": False, "migration": False, "firebird": False}
    errors = []
    try:
        with get_metastore_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["metastore"] = True
        checks["migration"] = current_revision() == head_revision()
    except Exception:
        errors.append("metastore")
    try:
        with get_firebird_engine().connect() as connection:
            connection.execute(text("SELECT 1 FROM RDB$DATABASE"))
        checks["firebird"] = True
    except Exception:
        errors.append("firebird")
    status_code = 200 if all(checks.values()) else 503
    return JSONResponse(
        {
            "status": "ready" if status_code == 200 else "not_ready",
            "checks": checks,
            "errors": errors,
        },
        status_code=status_code,
    )


@app.get("/admin", include_in_schema=False)
@app.get("/admin/{path:path}", include_in_schema=False)
def admin_compat_redirect(path: str = ""):
    return RedirectResponse("/", status_code=307)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title, version=app.version, description=app.description, routes=app.routes
    )
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "Access Key",
    }
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi  # type: ignore[method-assign]
install_ui(app)
