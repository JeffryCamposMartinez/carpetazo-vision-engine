# ruff: noqa: E501
"""Tablas del índice de referencias (E4).

- `verified_references`: identidad VERIFICADA por una persona (quién, cuándo y qué comprobó son obligatorios). Es lo único que puede devolverse como identidad.
- `candidate_references`: imágenes probables o ambiguas. NO tiene columnas de identidad ni de verificación y no hay ningún camino de código que mueva una fila a la otra tabla:
  una candidata solo puede servir de distractor. Pasar a verificada exige registrarla de nuevo como referencia verificada, con su verificador.
- `*_embeddings_<modelo>`: una tabla por modelo/versión y por nivel, con dimensión FIJA (permite índices HNSW y evita mezclar espacios vectoriales).
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models import Base, Json
from app.recognition.index.registry import MODELS, TIERS


class ReferenceImportRun(Base):
    __tablename__ = "reference_import_runs"
    __table_args__ = (CheckConstraint("kind in ('verified','candidate')", name="ck_import_run_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(20))  # verified | candidate
    requested_by: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(Text)  # p. ej. ruta/huella del dataset de origen
    counts: Mapped[dict] = mapped_column(Json, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VerifiedReference(Base):
    __tablename__ = "verified_references"
    __table_args__ = (
        CheckConstraint("length(trim(verified_by)) > 0", name="ck_vref_verified_by"),
        CheckConstraint("length(trim(verification_note)) > 0", name="ck_vref_note"),
        CheckConstraint("source_kind in ('pilot_dataset','assisted_review')", name="ck_vref_source_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_kind: Mapped[str] = mapped_column(String(30))
    source_ref: Mapped[str] = mapped_column(Text)  # p. ej. «pc-001» del dataset del piloto
    storage_key: Mapped[str | None] = mapped_column(Text)  # clave en R2 (solo trazabilidad; aquí no se lee ni se escribe)
    image_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    license_status: Mapped[str] = mapped_column(String(20), default="unknown", server_default="unknown")  # sin resolver: no apto para uso productivo
    # identidad verificada (texto leído en la carta física); los ids del catálogo son opcionales y solo se rellenan con una asociación revisada
    name: Mapped[str] = mapped_column(Text)
    set_name: Mapped[str] = mapped_column(Text)
    collector_number: Mapped[str] = mapped_column(String(30))
    language: Mapped[str] = mapped_column(String(10), default="unknown")
    finish: Mapped[str] = mapped_column(String(20), default="desconocido")
    pattern: Mapped[str] = mapped_column(String(20), default="desconocido")
    card_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cards.id"))
    printing_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("card_printings.id"))
    variant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("printing_variants.id"))
    verified_by: Mapped[str] = mapped_column(String(100))
    verified_at: Mapped[str] = mapped_column(String(40))
    verification_note: Mapped[str] = mapped_column(Text)
    imported_by: Mapped[str] = mapped_column(String(100))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    import_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reference_import_runs.id"))


class CandidateReference(Base):
    """Probable o ambigua. SIN identidad: `claimed_label` es solo lo que dice el origen (p. ej. una carpeta) y no es verdad."""

    __tablename__ = "candidate_references"
    __table_args__ = (CheckConstraint("proposal_status in ('probable','ambiguous')", name="ck_cref_status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    storage_key: Mapped[str] = mapped_column(Text)
    image_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    claimed_label: Mapped[str | None] = mapped_column(Text)
    proposal_status: Mapped[str] = mapped_column(String(20))
    proposal_source: Mapped[str] = mapped_column(Text)
    license_status: Mapped[str] = mapped_column(String(20), default="unknown", server_default="unknown")
    imported_by: Mapped[str] = mapped_column(String(100))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    import_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reference_import_runs.id"))


def _embedding_table(tier: str, slot, parent: str):
    name = slot.table(tier)
    return type(
        "".join(p.capitalize() for p in name.split("_")),
        (Base,),
        {
            "__tablename__": name,
            "__table_args__": (UniqueConstraint("reference_id", "normalization", name=f"uq_{name}"),),
            "id": mapped_column(Uuid, primary_key=True, default=uuid.uuid4),
            "reference_id": mapped_column(ForeignKey(f"{parent}.id", ondelete="CASCADE")),
            "normalization": mapped_column(String(30)),  # «raw» o la etiqueta de normalización usada (p. ej. n1-1a2b3c4d)
            "embedding": mapped_column(Vector(slot.dim).with_variant(JSON(), "sqlite")),
            "created_at": mapped_column(DateTime(timezone=True), server_default=func.now()),
        },
    )


EMBEDDING_TABLES = {
    (tier, key): _embedding_table(tier, slot, "verified_references" if tier == "verified" else "candidate_references")
    for key, slot in MODELS.items()
    for tier in TIERS
}
