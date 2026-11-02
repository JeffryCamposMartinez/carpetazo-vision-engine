from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.security import create_session_token, require_service_key
from app.schemas.session import SessionCreate, SessionResponse

router = APIRouter(prefix="/v1")


@router.post("/sessions", response_model=SessionResponse, status_code=201, dependencies=[Depends(require_service_key)])
def create_session(_: SessionCreate | None = None, settings: Settings = Depends(get_settings)) -> SessionResponse:
    token, claims = create_session_token(settings)
    return SessionResponse(
        session_id=claims.session_id,
        token=token,
        expires_at=datetime.fromtimestamp(claims.expires_at, UTC),
    )
