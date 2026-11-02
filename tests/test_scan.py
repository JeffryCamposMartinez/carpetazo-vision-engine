import numpy as np

from tests.conftest import encode


def _post(client, headers, data, name="f.png", extra=None):
    return client.post("/v1/scan", files={"file": (name, data, "image/png")}, headers={**headers, **(extra or {})})


def test_scan_requires_token(client, sharp_png):
    assert client.post("/v1/scan", files={"file": ("f.png", sharp_png, "image/png")}).status_code == 401


def test_valid_image_is_unknown_and_not_faked(client, session_headers, sharp_png):
    r = _post(client, session_headers, sharp_png)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unknown"
    assert body["card"] is None and body["printing"] is None
    assert body["recognition_available"] is False
    assert body["quality"]["perspective"] is None


def test_blurry_image_is_poor_quality(client, session_headers, blurry_png):
    assert _post(client, session_headers, blurry_png).json()["status"] == "poor_quality"


def test_corrupt_image(client, session_headers, sharp_png):
    assert _post(client, session_headers, sharp_png[:200]).status_code == 422


def test_wrong_mime(client, session_headers):
    assert _post(client, session_headers, b"%PDF-1.4 not an image", name="x.png").status_code == 415


def test_too_large(client, session_headers):
    big = b"\xff\xd8\xff" + b"0" * 4_100_000
    assert _post(client, session_headers, big).status_code == 413


def test_too_small_dimensions(client, session_headers):
    tiny = encode(np.zeros((50, 50, 3), np.uint8))
    assert _post(client, session_headers, tiny).status_code == 422


def test_session_id_mismatch(client, session_headers, sharp_png):
    r = client.post(
        "/v1/scan",
        files={"file": ("f.png", sharp_png, "image/png")},
        data={"session_id": "00000000-0000-0000-0000-000000000000"},
        headers=session_headers,
    )
    assert r.status_code == 403


def test_idempotency_replays_same_result(client, session_headers, sharp_png):
    first = _post(client, session_headers, sharp_png, extra={"Idempotency-Key": "k1"})
    second = _post(client, session_headers, sharp_png, extra={"Idempotency-Key": "k1"})
    assert first.json()["scan_id"] == second.json()["scan_id"]
    assert second.headers["Idempotent-Replayed"] == "true"


def test_get_scan_only_for_owner(client, session_headers, sharp_png):
    scan_id = _post(client, session_headers, sharp_png).json()["scan_id"]
    assert client.get(f"/v1/scan/{scan_id}", headers=session_headers).status_code == 200
    other = client.post("/v1/sessions", headers={"X-Service-Key": "service-key-a"}).json()["token"]
    assert client.get(f"/v1/scan/{scan_id}", headers={"Authorization": f"Bearer {other}"}).status_code == 404


def test_rate_limit(client, session_headers, sharp_png, monkeypatch):
    monkeypatch.setattr(client.app.state.scan_service._settings, "scan_rate_limit_per_minute", 2)
    codes = [_post(client, session_headers, sharp_png).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_jpeg_accepted(client, session_headers):
    rng = np.random.default_rng(1)
    data = encode(rng.integers(0, 256, (800, 600, 3), dtype=np.uint8), ".jpg")
    assert _post(client, session_headers, data, name="f.jpg").status_code == 200
