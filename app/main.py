import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import catalog, health, scans, sessions
from app.core.config import get_settings
from app.services.scan_service import ScanService


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.secret_key is None or not settings.service_keys:
        raise RuntimeError("Faltan VISION_SECRET_KEY y/o VISION_SERVICE_API_KEYS")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    docs = settings.environment == "development"
    app = FastAPI(
        title="Carpetazo Vision Engine",
        version=settings.engine_version,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.scan_service = ScanService(settings)
    for module in (health, sessions, scans, catalog):
        app.include_router(module.router)
    if settings.demo_ui_enabled:
        app.mount("/demo", StaticFiles(directory=Path(__file__).parent / "demo_ui", html=True), name="demo")
    return app


app = create_app()
