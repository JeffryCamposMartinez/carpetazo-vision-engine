# ruff: noqa: E501
"""Registro de modelos de embedding con DIMENSIÓN FIJA por modelo y versión. Cada entrada tiene sus propias tablas (verificadas y candidatas).

Añadir un modelo o una versión nueva exige una entrada aquí y una migración: los vectores de especificaciones distintas nunca comparten tabla.
"""

from dataclasses import dataclass

from app.recognition.embedding.base import EmbeddingSpec
from app.recognition.embedding.dinov2_client import DINOV2_SPEC
from app.recognition.embedding.grid import GRID_SPEC

TIERS = ("verified", "candidate", "ambiguous")  # verificada | candidata (vínculo de Carpetazo sin revisar) | ambigua


@dataclass(frozen=True)
class ModelSlot:
    slot: str  # sufijo de las tablas
    spec: EmbeddingSpec

    @property
    def dim(self) -> int:
        return self.spec.dim

    def table(self, tier: str) -> str:
        if tier not in TIERS:
            raise ValueError(f"nivel desconocido {tier!r} (verified | candidate)")
        return f"{tier}_embeddings_{self.slot}"


MODELS: dict[str, ModelSlot] = {s.spec.key: s for s in (ModelSlot("dinov2_small_ed25f3a_p1", DINOV2_SPEC), ModelSlot("grid_g1", GRID_SPEC))}


def slot_for(spec: EmbeddingSpec) -> ModelSlot:
    try:
        return MODELS[spec.key]
    except KeyError:
        raise ValueError(f"el modelo {spec.key} no tiene tablas de índice registradas (añade una entrada y una migración)") from None
