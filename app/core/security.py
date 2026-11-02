import base64
import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status

from app.core.config import Settings, get_settings


@dataclass(frozen=True)
class SessionClaims:
    session_id: str
    expires_at: int


def _sign(secret: str, payload: str) -> str:
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def create_session_token(settings: Settings, ttl: int | None = None) -> tuple[str, SessionClaims]:
    session_id = str(uuid.uuid4())
    expires_at = int(time.time()) + (ttl if ttl is not None else settings.session_ttl_seconds)
    payload = f"{session_id}.{expires_at}"
    token = f"{payload}.{_sign(settings.secret_key.get_secret_value(), payload)}"
    return token, SessionClaims(session_id, expires_at)


def verify_session_token(settings: Settings, token: str) -> SessionClaims | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    session_id, exp, signature = parts
    expected = _sign(settings.secret_key.get_secret_value(), f"{session_id}.{exp}")
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        expires_at = int(exp)
        uuid.UUID(session_id)
    except ValueError:
        return None
    if expires_at < time.time():
        return None
    return SessionClaims(session_id, expires_at)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "unauthorized", "detail": detail})


def _is_service_key(settings: Settings, key: str | None) -> bool:
    return bool(key) and any(hmac.compare_digest(key, k) for k in settings.service_keys)


def require_service_key(
    x_service_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if not _is_service_key(settings, x_service_key):
        raise _unauthorized("Clave de servicio inválida o ausente")


def require_session(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> SessionClaims:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized("Token de sesión ausente")
    claims = verify_session_token(settings, authorization[7:].strip())
    if claims is None:
        raise _unauthorized("Token de sesión inválido o expirado")
    return claims


def require_access(
    x_service_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Catálogo: acepta clave de servicio o un token de sesión válido."""
    if _is_service_key(settings, x_service_key):
        return
    if authorization and authorization.lower().startswith("bearer "):
        if verify_session_token(settings, authorization[7:].strip()):
            return
    raise _unauthorized("Credenciales inválidas o ausentes")
