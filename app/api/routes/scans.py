import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, get_settings
from app.core.security import SessionClaims, require_session
from app.detection.quality import assess_quality
from app.schemas.scan import Quality, ScanResponse, ScanStatus
from app.services.image_input import ImageRejected, decode_image
from app.services.scan_service import ScanService

router = APIRouter(prefix="/v1")
log = logging.getLogger("vision.scan")


def _error(status_code: int, code: str, detail: str, headers: dict | None = None) -> HTTPException:
    return HTTPException(status_code, detail={"error": code, "detail": detail}, headers=headers)


def _process(data: bytes, session_id: str, settings: Settings) -> ScanResponse:
    image = decode_image(data, settings)
    quality = assess_quality(image.bgr)
    poor = (
        quality.sharpness < settings.min_sharpness
        or not settings.min_brightness <= quality.brightness <= settings.max_brightness
    )
    return ScanResponse(
        scan_id=str(uuid.uuid4()),
        session_id=session_id,
        status=ScanStatus.poor_quality if poor else ScanStatus.unknown,
        quality=Quality(sharpness=round(quality.sharpness, 2), lighting=round(quality.brightness, 2)),
        recognition_available=False,
        message=(
            "Imagen de baja calidad" if poor else "Imagen válida; detección y reconocimiento aún no están implementados"
        ),
    )


@router.post("/scan", response_model=ScanResponse)
async def create_scan(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    session_id: str | None = Form(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=100),
    claims: SessionClaims = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> ScanResponse:
    service: ScanService = request.app.state.scan_service
    if session_id is not None and session_id != claims.session_id:
        raise _error(403, "session_mismatch", "El session_id no corresponde al token")

    if idempotency_key:
        previous = service.find_idempotent(claims.session_id, idempotency_key)
        if previous:
            response.headers["Idempotent-Replayed"] = "true"
            return previous

    wait = service.allow_request(claims.session_id)
    if wait is not None:
        raise _error(429, "rate_limited", "Demasiadas solicitudes", {"Retry-After": str(wait)})

    data = bytearray()
    while chunk := await file.read(65536):
        data += chunk
        if len(data) > settings.max_image_bytes:
            raise _error(413, "image_too_large", "La imagen supera el tamaño máximo")

    if not service.try_begin(claims.session_id):
        raise _error(409, "scan_in_progress", "Ya hay un escaneo en curso para esta sesión")
    try:
        try:
            await asyncio.wait_for(service.semaphore.acquire(), settings.scan_queue_timeout_seconds)
        except TimeoutError:
            raise _error(503, "busy", "Servidor ocupado", {"Retry-After": "1"}) from None
        try:
            result = await run_in_threadpool(_process, bytes(data), claims.session_id, settings)
        except ImageRejected as exc:
            raise _error(exc.http_status, exc.code, exc.detail) from None
        finally:
            service.semaphore.release()
    finally:
        service.end(claims.session_id)

    service.save(result, idempotency_key)
    log.info("scan status=%s session=%s", result.status, claims.session_id)
    return result


@router.get("/scan/{scan_id}", response_model=ScanResponse)
def get_scan(scan_id: str, request: Request, claims: SessionClaims = Depends(require_session)) -> ScanResponse:
    result = request.app.state.scan_service.get(scan_id)
    if result is None or result.session_id != claims.session_id:
        raise _error(404, "not_found", "Escaneo no encontrado o expirado")
    return result
