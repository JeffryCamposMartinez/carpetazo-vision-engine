from app.core.config import get_settings
from app.core.security import create_session_token
from tests.conftest import SERVICE_HEADERS


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_version_declares_no_recognition(client):
    body = client.get("/v1/version").json()
    assert body["api_version"] == "v1"
    assert body["recognition_available"] is False


def test_session_requires_service_key(client):
    assert client.post("/v1/sessions").status_code == 401
    assert client.post("/v1/sessions", headers={"X-Service-Key": "wrong"}).status_code == 401


def test_session_created(client):
    r = client.post("/v1/sessions", headers=SERVICE_HEADERS, json={"user_ref": "u1"})
    assert r.status_code == 201
    assert {"session_id", "token", "expires_at"} <= r.json().keys()


def test_expired_or_tampered_token_rejected(client):
    files = {"file": ("a.png", b"x", "image/png")}
    expired, _ = create_session_token(get_settings(), ttl=-5)
    assert client.post("/v1/scan", files=files, headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    good, _ = create_session_token(get_settings())
    tampered = good[:-3] + ("aaa" if not good.endswith("aaa") else "bbb")
    assert client.post("/v1/scan", files=files, headers={"Authorization": f"Bearer {tampered}"}).status_code == 401
