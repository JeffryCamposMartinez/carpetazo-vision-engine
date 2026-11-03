# ruff: noqa: E501
"""E3: servicio de embeddings. Los tests de protocolo usan un worker FALSO (sin torch); el test real de DINOv2-small usa los pesos locales ya descargados, sin red."""

import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.core.config import Settings
from app.recognition.detection import CardDetector
from app.recognition.embedding import EmbeddingService, ExtractorUnavailable
from app.recognition.embedding.base import CANON_H, CANON_W, EmbeddingSpec
from app.recognition.embedding.dinov2_client import (
    DINOV2_SPEC,
    Dinov2WorkerExtractor,
    default_ml_home,
    default_ml_python,
)
from app.recognition.embedding.grid import GRID_SPEC, GridExtractor
from tests import synthetic_scenes as S
from tests.test_recognition_lab import fake_card

FAKE = Path(__file__).with_name("fake_dinov2_worker.py")


def canon(seed: int) -> np.ndarray:
    return cv2.resize(fake_card(seed), (CANON_W, CANON_H), interpolation=cv2.INTER_AREA)


def fake_worker(mode: str = "ok", **kw) -> Dinov2WorkerExtractor:
    kw.setdefault("startup_timeout", 20)
    kw.setdefault("request_timeout", 20)
    return Dinov2WorkerExtractor(command=[sys.executable, str(FAKE), "--mode", mode], **kw)


class StubPrimary:
    spec = DINOV2_SPEC

    def __init__(self, fail=True):
        self.fail, self.calls, self.loaded = fail, 0, False

    def embed(self, _):
        self.calls += 1
        if self.fail:
            raise ExtractorUnavailable("caído a propósito")
        return np.ones(768, np.float32) / np.sqrt(768)

    def close(self):
        return None


# ---- carga diferida y arranque de la API
def test_creating_the_service_and_the_app_never_loads_the_model():
    svc = EmbeddingService.from_settings(Settings(secret_key="x", service_api_keys="y"))
    assert svc.primary.starts == 0 and not svc.primary.loaded and svc.status()["primary_loaded"] is False
    from app.main import app

    assert not hasattr(app.state, "embedding_service")  # la API no crea ni carga nada de E3 al arrancar


def test_from_settings_validates_the_primary_choice():
    with pytest.raises(ValueError, match="desconocido"):
        EmbeddingService.from_settings(Settings(secret_key="x", service_api_keys="y", embedding_primary="otro"))
    svc = EmbeddingService.from_settings(Settings(secret_key="x", service_api_keys="y", embedding_primary="grid"))
    r = svc.embed(canon(1))
    assert svc.primary is None and r.spec == GRID_SPEC and r.fallback_used is False


# ---- protocolo con el worker falso
def test_worker_is_started_lazily_once_and_reused():
    w = fake_worker()
    assert w.starts == 0
    a, b = w.embed(canon(1)), w.embed(canon(2))
    assert w.starts == 1 and w.loaded and w.last_device == "cpu"
    assert a.shape == (768,) and abs(float(np.linalg.norm(a)) - 1) < 1e-5 and not np.allclose(a, b)
    w.close()
    assert not w.loaded


def test_concurrent_requests_are_serialized_through_one_worker():
    w = fake_worker()
    results, errors = [], []

    def go(i):
        try:
            results.append((i, w.embed(canon(i))))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and len(results) == 6 and w.starts == 1
    again = {i: w.embed(canon(i)) for i in range(6)}
    assert all(np.allclose(v, again[i]) for i, v in results)  # mismo resultado sin importar el orden de llegada
    w.close()


def test_idle_worker_unloads_itself_and_relaunches_on_demand():
    w = fake_worker(idle_seconds=0.6)
    w.embed(canon(1))
    assert w.loaded
    deadline = time.time() + 6
    while w.loaded and time.time() < deadline:
        time.sleep(0.1)
    assert not w.loaded  # VRAM liberada
    w.embed(canon(1))
    assert w.starts == 2
    w.close()


@pytest.mark.parametrize("mode", ["bad_revision", "wrong_dim", "crash_on_request", "error_on_request"])
def test_worker_failures_become_extractor_unavailable(mode):
    w = fake_worker(mode)
    with pytest.raises(ExtractorUnavailable):
        w.embed(canon(1))
    w.close()


def test_a_hung_worker_times_out_and_is_killed():
    w = fake_worker("hang_on_request", request_timeout=1.0)
    t0 = time.time()
    with pytest.raises(ExtractorUnavailable, match="no respondió"):
        w.embed(canon(1))
    assert time.time() - t0 < 15 and not w.loaded


def test_missing_ml_environment_is_reported_not_raised_as_a_crash(tmp_path):
    w = Dinov2WorkerExtractor(python=tmp_path / "no-existe" / "python.exe")
    with pytest.raises(ExtractorUnavailable, match="entorno ML"):
        w.embed(canon(1))
    w2 = Dinov2WorkerExtractor(python=sys.executable, hf_home=tmp_path / "sin-pesos")
    with pytest.raises(ExtractorUnavailable, match="pesos"):
        w2.embed(canon(1))


def test_input_must_be_a_rectified_card():
    w = fake_worker()
    for bad in (np.zeros((100, 100, 3), np.uint8), np.zeros((CANON_H, CANON_W, 3), np.float32), None):
        with pytest.raises(ValueError, match="rectificada"):
            w.embed(bad)
    assert w.starts == 0  # la validación ocurre antes de lanzar nada


# ---- servicio: respaldo y versionado
def test_service_falls_back_to_grid_and_declares_it():
    svc = EmbeddingService(Dinov2WorkerExtractor(python="no-existe.exe"), GridExtractor())
    r = svc.embed(canon(1))
    assert r.fallback_used and r.spec == GRID_SPEC and "entorno ML" in r.fallback_reason and r.vector.shape == (GRID_SPEC.dim,)
    assert r.spec.key != DINOV2_SPEC.key  # nunca se etiqueta como DINOv2


def test_primary_failure_enters_cooldown_then_retries():
    now = [100.0]
    primary = StubPrimary(fail=True)
    svc = EmbeddingService(primary, GridExtractor(), cooldown_seconds=30, clock=lambda: now[0])
    assert svc.embed(canon(1)).fallback_used and primary.calls == 1
    r = svc.embed(canon(1))
    assert r.fallback_used and primary.calls == 1 and "en pausa" in r.fallback_reason  # no reintenta durante la pausa
    assert svc.status()["primary_in_cooldown"] is True
    now[0] += 31
    primary.fail = False
    r = svc.embed(canon(1))
    assert not r.fallback_used and r.spec == DINOV2_SPEC and primary.calls == 2


def test_service_uses_the_primary_when_available():
    svc = EmbeddingService(fake_worker(), GridExtractor())
    r = svc.embed(canon(3))
    assert not r.fallback_used and r.fallback_reason is None and r.spec == DINOV2_SPEC and r.backend == "dinov2-worker:cpu" and r.latency_ms > 0
    svc.close()


def test_embed_orientations_returns_both_and_they_differ():
    img, _ = S.card_scene(4, S.LEVELS["easy"])
    det = CardDetector()
    rect = det.rectify(img, det.detect(img))
    svc = EmbeddingService(None, GridExtractor())
    up, flip = svc.embed_orientations(rect)
    assert up.spec == flip.spec and not np.allclose(up.vector, flip.vector)


def test_versions_fit_the_database_column_and_identify_the_exact_weights():
    assert len(DINOV2_SPEC.model_version) <= 50 and "ed25f3a" in DINOV2_SPEC.model_version and DINOV2_SPEC.dim == 768
    assert GRID_SPEC.dim == 456 and GRID_SPEC.key != DINOV2_SPEC.key
    with pytest.raises(ValueError, match="50"):
        EmbeddingSpec("x", "v" * 51, 3)


def test_grid_embedding_is_deterministic_normalized_and_discriminative():
    g = GridExtractor()
    a, a2, b = g.embed(canon(1)), g.embed(canon(1)), g.embed(canon(2))
    assert np.array_equal(a, a2) and abs(float(np.linalg.norm(a)) - 1) < 1e-5 and a.dtype == np.float32
    assert float(a @ a2) > float(a @ b)
    assert float(a @ g.embed(cv2.rotate(canon(1), cv2.ROTATE_180))) < 0.999  # el 180° cambia el vector (por eso se prueban ambas orientaciones)


# ---- DINOv2-small real (pesos locales ya descargados; sin red; GPU o CPU)
ML_READY = default_ml_python().exists() and (default_ml_home() / "hub").exists()


@pytest.mark.skipif(not ML_READY, reason="no está el entorno ML independiente (vision-ml-lab)")
def test_real_dinov2_small_through_the_decoupled_worker():
    w = Dinov2WorkerExtractor(idle_seconds=0)
    try:
        v = w.embed(canon(1))
        assert v.shape == (768,) and abs(float(np.linalg.norm(v)) - 1) < 1e-4 and w.hello["revision"].startswith(DINOV2_SPEC.model_version.split("-")[0])
        assert w.hello["model_id"] == "facebook/dinov2-small" and w.last_device in ("cuda", "cpu")
        assert w.embed(canon(1)).tolist() == pytest.approx(v.tolist(), abs=1e-5)  # determinista
        assert float(v @ w.embed(canon(2))) < 0.99  # cartas distintas -> vectores distintos
    finally:
        w.close()


@pytest.mark.skipif(not ML_READY, reason="no está el entorno ML independiente (vision-ml-lab)")
def test_real_dinov2_cpu_fallback_matches_the_requested_device():
    w = Dinov2WorkerExtractor(device="cpu", idle_seconds=0)
    try:
        v = w.embed(canon(1))
        assert w.last_device == "cpu" and w.hello["device"] == "cpu" and v.shape == (768,)
    finally:
        w.close()
