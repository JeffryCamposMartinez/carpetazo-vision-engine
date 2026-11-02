import os

os.environ.setdefault("VISION_SECRET_KEY", "test-secret-key")
os.environ.setdefault("VISION_SERVICE_API_KEYS", "service-key-a,service-key-b")
os.environ.setdefault("VISION_DATABASE_URL", "sqlite://")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.database.models import Base  # noqa: E402
from app.database.session import get_db  # noqa: E402
from app.main import create_app  # noqa: E402

SERVICE_HEADERS = {"X-Service-Key": "service-key-a"}


@pytest.fixture
def db_factory():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def client(db_factory):
    app = create_app()

    def _db():
        with db_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


@pytest.fixture
def session_headers(client):
    token = client.post("/v1/sessions", headers=SERVICE_HEADERS).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def encode(img: np.ndarray, ext: str = ".png") -> bytes:
    ok, buf = cv2.imencode(ext, img)
    assert ok
    return buf.tobytes()


@pytest.fixture
def sharp_png() -> bytes:
    rng = np.random.default_rng(0)
    return encode(rng.integers(0, 256, (900, 640, 3), dtype=np.uint8))


@pytest.fixture
def blurry_png() -> bytes:
    return encode(np.full((900, 640, 3), 128, dtype=np.uint8))
