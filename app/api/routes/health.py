from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/v1/version")
def version(settings: Settings = Depends(get_settings)) -> dict:
    return {
        "engine_version": settings.engine_version,
        "api_version": "v1",
        "model_version": None,
        "recognition_available": False,
    }
