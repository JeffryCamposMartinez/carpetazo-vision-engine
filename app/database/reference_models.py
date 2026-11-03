# ruff: noqa: E501
"""Tablas del índice de referencias (E4), con los identificadores de Carpetazo.

Tres niveles en tablas SEPARADAS (decisión del proyecto):
- `verified_references`: imagen revisada a mano y asociada a un producto de Carpetazo (quién, cuándo y qué comprobó son obligatorios).
  Es lo único que puede producir una identidad confirmada.
- `candidate_references`: imagen que Carpetazo vincula a un producto pero que nadie revisó (`claimed_*` = afirmación, no verdad). Sirve para experimentos de búsqueda.
- `ambiguous_references`: imagen o asociación con dudas (motivo obligatorio). Puede o no traer un vínculo afirmado; nunca confirma nada.
No hay ningún camino de código que mueva una fila de un nivel a otro: pasar a verificada exige registrarla de nuevo como referencia verificada, con su verificador.

`*_embeddings_<modelo>`: una tabla por modelo/versión y por nivel, con dimensión FIJA (permite índices HNSW y evita mezclar espacios vectoriales).
Los embeddings cuelgan de la referencia (no del espejo del catálogo): una sincronización no los toca ni los pierde (las claves hacia el espejo son RESTRICT y el espejo no borra).
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models import Base, Json
from app.recognition.index.registry import MODELS, TIERS

MIRROR_CARD = ["mirror_cards.category_id", "mirror_cards.product_id"]


class ReferenceImportRun(Base):
    __tablename__ = "reference_import_runs"
    __table_args__ = (CheckConstraint("kind in ('verified','candidate')", name="ck_import_run_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(20))  # verified | candidate (también registra las ambiguas)
    requested_by: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(Text)  # p. ej. ruta/huella del dataset de origen
    counts: Mapped[dict] = mapped_column(Json, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VerifiedReference(Base):
    """Identidad VERIFICADA por una persona y vinculada a un producto de Carpetazo (`category_id`, `product_id`)."""

    __tablename__ = "verified_references"
    __table_args__ = (
        CheckConstraint("length(trim(verified_by)) > 0", name="ck_vref_verified_by"),
        CheckConstraint("length(trim(verification_note)) > 0", name="ck_vref_note"),
        CheckConstraint("source_kind in ('pilot_dataset','assisted_review')", name="ck_vref_source_kind"),
        CheckConstraint("category_id IS NOT NULL AND product_id IS NOT NULL", name="ck_vref_product_link"),
        ForeignKeyConstraint(["category_id", "product_id"], MIRROR_CARD, ondelete="RESTRICT", name="fk_vref_mirror_card"),
        Index("ix_vref_product", "category_id", "product_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_kind: Mapped[str] = mapped_column(String(30))
    source_ref: Mapped[str] = mapped_column(Text)  # p. ej. «pc-001» del dataset del piloto
    storage_key: Mapped[str | None] = mapped_column(Text)  # clave en R2 (solo trazabilidad; aquí no se lee ni se escribe)
    image_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    license_status: Mapped[str] = mapped_column(String(20), default="unknown", server_default="unknown")  # sin resolver: no apto para uso productivo
    category_id: Mapped[int] = mapped_column(Integer)  # identidad = producto de Carpetazo (impresión del catálogo)
    product_id: Mapped[int] = mapped_column(Integer)
    # Descriptores leídos en la carta física al verificar (no sustituyen a la identidad ni son una entidad de variante)
    name: Mapped[str] = mapped_column(Text)
    set_name: Mapped[str] = mapped_column(Text)
    collector_number: Mapped[str] = mapped_column(String(30))
    language: Mapped[str] = mapped_column(String(10), default="unknown")
    finish: Mapped[str] = mapped_column(String(20), default="desconocido")
    pattern: Mapped[str] = mapped_column(String(20), default="desconocido")
    # Variante: no existe como entidad en Carpetazo; siempre «unknown» hasta que haya evidencia y una entidad real (variant_key).
    variant: Mapped[str] = mapped_column(String(20), default="unknown", server_default="unknown")
    variant_key: Mapped[str | None] = mapped_column(Text)
    verified_by: Mapped[str] = mapped_column(String(100))
    verified_at: Mapped[str] = mapped_column(String(40))
    verification_note: Mapped[str] = mapped_column(Text)
    imported_by: Mapped[str] = mapped_column(String(100))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    import_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reference_import_runs.id"))


class CandidateReference(Base):
    """Imagen vinculada por Carpetazo a un producto (`claimed_*`) pero NO revisada. Sirve para experimentos; nunca da una identidad confirmada."""

    __tablename__ = "candidate_references"
    __table_args__ = (
        CheckConstraint("status in ('unreviewed')", name="ck_cref_status"),
        ForeignKeyConstraint(["claimed_category_id", "claimed_product_id"], MIRROR_CARD, ondelete="RESTRICT", name="fk_cref_mirror_card"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    storage_key: Mapped[str] = mapped_column(Text)
    image_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    claimed_category_id: Mapped[int] = mapped_column(Integer)  # lo que AFIRMA Carpetazo (productId → imageKey); no está verificado
    claimed_product_id: Mapped[int] = mapped_column(Integer)
    link_source: Mapped[str] = mapped_column(String(40), default="carpetazo_catalog", server_default="carpetazo_catalog")
    status: Mapped[str] = mapped_column(String(20), default="unreviewed", server_default="unreviewed")
    note: Mapped[str | None] = mapped_column(Text)
    license_status: Mapped[str] = mapped_column(String(20), default="unknown", server_default="unknown")
    imported_by: Mapped[str] = mapped_column(String(100))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    import_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reference_import_runs.id"))


class AmbiguousReference(Base):
    """Imagen o asociación con dudas (varias candidatas, conflicto, evidencia insuficiente). Nunca confirma nada."""

    __tablename__ = "ambiguous_references"
    __table_args__ = (
        CheckConstraint("length(trim(ambiguity_reason)) > 0", name="ck_aref_reason"),
        CheckConstraint("(claimed_category_id IS NULL) = (claimed_product_id IS NULL)", name="ck_aref_claim_pair"),
        ForeignKeyConstraint(["claimed_category_id", "claimed_product_id"], MIRROR_CARD, ondelete="RESTRICT", name="fk_aref_mirror_card"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    storage_key: Mapped[str] = mapped_column(Text)
    image_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    claimed_category_id: Mapped[int | None] = mapped_column(Integer)
    claimed_product_id: Mapped[int | None] = mapped_column(Integer)
    ambiguity_reason: Mapped[str] = mapped_column(Text)
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


EMBEDDING_TABLES = {(tier, key): _embedding_table(tier, slot, f"{tier}_references") for key, slot in MODELS.items() for tier in TIERS}
