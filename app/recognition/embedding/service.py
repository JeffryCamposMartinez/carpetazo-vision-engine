# ruff: noqa: E501
"""Servicio de embeddings: extractor primario (DINOv2-small por worker) con respaldo `grid` y versionado explícito.

- Crear el servicio NO carga ningún modelo ni lanza procesos (la API puede arrancar sin tocar la GPU).
- Si el primario no está disponible, se usa `grid` y el resultado lo declara (`fallback_used`, `fallback_reason`, `spec` del respaldo).
  Los vectores de modelos distintos nunca se mezclan: cada resultado lleva su `spec` y quien consulta un índice debe comparar `spec.key`.
- Tras un fallo del primario se espera `cooldown_seconds` antes de reintentar (evita relanzar el worker en cada petición).
"""

import logging
import threading
import time
from collections.abc import Callable

import numpy as np

from app.core.config import Settings
from app.recognition.detection import RectifiedCard
from app.recognition.embedding.base import (
    EmbeddingResult,
    EmbeddingSpec,
    Extractor,
    ExtractorUnavailable,
    validate_card_image,
)
from app.recognition.embedding.dinov2_client import Dinov2WorkerExtractor
from app.recognition.embedding.grid import GridExtractor

log = logging.getLogger("vision.embedding")


class EmbeddingService:
    def __init__(self, primary: Extractor | None, fallback: Extractor | None = None, cooldown_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic):
        if primary is None and fallback is None:
            raise ValueError("se necesita al menos un extractor")
        self.primary, self.fallback = primary, fallback or GridExtractor()
        self.cooldown_seconds, self._clock = cooldown_seconds, clock
        self._retry_at = 0.0
        self.last_failure: str | None = None
        self._state_lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> "EmbeddingService":
        primary: Extractor | None = None
        if settings.embedding_primary == "dinov2-small":
            primary = Dinov2WorkerExtractor(
                python=settings.embedding_ml_python, hf_home=settings.embedding_ml_home, device=settings.embedding_device,
                min_free_vram_mb=settings.embedding_min_free_vram_mb, idle_seconds=settings.embedding_worker_idle_seconds,
                startup_timeout=settings.embedding_worker_startup_timeout, request_timeout=settings.embedding_request_timeout,
            )  # fmt: skip
        elif settings.embedding_primary != "grid":
            raise ValueError(f"embedding_primary desconocido: {settings.embedding_primary!r} (dinov2-small | grid)")
        return cls(primary, GridExtractor(), settings.embedding_retry_cooldown_seconds)

    # ---- estado (sin cargar nada)
    def status(self) -> dict:
        with self._state_lock:
            wait = max(0.0, self._retry_at - self._clock())
        return {
            "primary": self.primary.spec.key if self.primary else None,
            "primary_loaded": bool(getattr(self.primary, "loaded", False)),
            "primary_in_cooldown": wait > 0, "retry_in_seconds": round(wait, 1), "last_failure": self.last_failure,
            "fallback": self.fallback.spec.key,
        }  # fmt: skip

    def embed(self, card_bgr: np.ndarray) -> EmbeddingResult:
        validate_card_image(card_bgr)
        t0 = time.perf_counter()
        reason: str | None = None
        if self.primary is not None:
            with self._state_lock:
                in_cooldown = self._clock() < self._retry_at
            if in_cooldown:
                reason = f"primario en pausa tras un fallo: {self.last_failure}"
            else:
                try:
                    vec = self.primary.embed(card_bgr)
                    backend = f"dinov2-worker:{getattr(self.primary, 'last_device', None) or '?'}"
                    return EmbeddingResult(vec, self.primary.spec, backend, False, None, (time.perf_counter() - t0) * 1000)
                except ExtractorUnavailable as exc:
                    reason = str(exc)
                    with self._state_lock:
                        self.last_failure, self._retry_at = reason, self._clock() + self.cooldown_seconds
                    log.warning("extractor primario no disponible, se usa el respaldo: %s", reason)
        vec = self.fallback.embed(card_bgr)
        return EmbeddingResult(vec, self.fallback.spec, self.fallback.spec.model_name, self.primary is not None, reason, (time.perf_counter() - t0) * 1000)

    def embed_orientations(self, rectified: RectifiedCard) -> tuple[EmbeddingResult, EmbeddingResult]:
        """Embeddings de la carta en 0° y 180° (la orientación la decide la búsqueda: la de mejor coincidencia)."""
        return self.embed(rectified.upright), self.embed(rectified.flipped)

    def specs(self) -> list[EmbeddingSpec]:
        return [s for s in (self.primary.spec if self.primary else None, self.fallback.spec) if s is not None]

    def close(self) -> None:
        for e in (self.primary, self.fallback):
            if e is not None:
                e.close()
