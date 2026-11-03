# ruff: noqa: E501
"""Interfaz desacoplada de extractor de embeddings y versionado.

Contrato de entrada: una carta RECTIFICADA (BGR uint8, 614x440, retrato; ver `detection.RectifiedCard`).
Contrato de salida: vector float32 L2-normalizado de `spec.dim` componentes. Los vectores de especificaciones distintas NO se comparan entre sí.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

CANON_H, CANON_W = 614, 440
MAX_VERSION_LEN = 50  # columna card_embeddings.model_version


class ExtractorUnavailable(Exception):
    """El extractor no puede producir embeddings ahora (entorno ML ausente, pesos ausentes, proceso caído, tiempo agotado...)."""


@dataclass(frozen=True)
class EmbeddingSpec:
    """Identidad versionada de un embedding: modelo + versión (pesos, pooling, tamaño, preprocesado). Cualquier cambio exige otra versión."""

    model_name: str
    model_version: str
    dim: int

    def __post_init__(self) -> None:
        if len(self.model_version) > MAX_VERSION_LEN:
            raise ValueError(f"model_version supera {MAX_VERSION_LEN} caracteres (columna de la base de datos)")

    @property
    def key(self) -> str:
        return f"{self.model_name}:{self.model_version}"


@runtime_checkable
class Extractor(Protocol):
    spec: EmbeddingSpec

    def embed(self, card_bgr: np.ndarray) -> np.ndarray: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class EmbeddingResult:
    vector: np.ndarray
    spec: EmbeddingSpec
    backend: str  # "dinov2-worker:cuda", "dinov2-worker:cpu", "grid"
    fallback_used: bool
    fallback_reason: str | None
    latency_ms: float


def validate_card_image(card_bgr: np.ndarray) -> None:
    if not isinstance(card_bgr, np.ndarray) or card_bgr.dtype != np.uint8 or card_bgr.shape != (CANON_H, CANON_W, 3):
        raise ValueError(f"se espera una carta rectificada BGR uint8 de {CANON_H}x{CANON_W}x3")


def unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return (v / n).astype(np.float32) if n > 0 else v.astype(np.float32)
