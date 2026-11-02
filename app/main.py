import logging

from fastapi import FastAPI

from app.api.routes import catalog, health, scans, sessions
from app.core.config import get_settings
from app.services.scan_service import ScanService


def create_app() -> FastAPI:
    settings = get_settings()
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
    return app


app = create_app()
