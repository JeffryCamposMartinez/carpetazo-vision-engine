import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

Json = JSON().with_variant(JSONB(), "postgresql")
# Sin dimensión fija: depende del modelo que gane el benchmark. El índice HNSW se crea
# cuando se fije la dimensión (ver README, sección Embeddings).
EmbeddingVector = Vector().with_variant(JSON(), "sqlite")


class Base(DeclarativeBase):
    pass


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Game(Base):
    __tablename__ = "games"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(50), unique=True)


class CatalogSource(Base):
    __tablename__ = "catalog_sources"
    __table_args__ = (UniqueConstraint("provider", "external_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(Text)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # unknown | permitted | restricted: no se descargan imágenes salvo 'permitted'.
    license_status: Mapped[str] = mapped_column(String(20), default="unknown", server_default="unknown")
    attribution: Mapped[str | None] = mapped_column(Text)


class CardSet(Base, Timestamps):
    __tablename__ = "sets"
    __table_args__ = (UniqueConstraint("game_id", "external_id", "language"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"))
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("catalog_sources.id"))
    external_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str | None] = mapped_column(String(50))
    release_date: Mapped[date | None] = mapped_column(Date)
    language: Mapped[str] = mapped_column(String(10))
    meta: Mapped[dict] = mapped_column("metadata", Json, default=dict)

    printings: Mapped[list["CardPrinting"]] = relationship(back_populates="set")


class Card(Base, Timestamps):
    __tablename__ = "cards"
    __table_args__ = (UniqueConstraint("game_id", "external_id"), Index("ix_cards_name", "canonical_name"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"))
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("catalog_sources.id"))
    external_id: Mapped[str | None] = mapped_column(String(100))
    canonical_name: Mapped[str] = mapped_column(String(300))
    meta: Mapped[dict] = mapped_column("metadata", Json, default=dict)

    printings: Mapped[list["CardPrinting"]] = relationship(back_populates="card")


class CardPrinting(Base, Timestamps):
    __tablename__ = "card_printings"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id"),
        Index("ix_printings_card", "card_id"),
        Index("ix_printings_set_number", "set_id", "collector_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cards.id"))
    set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sets.id"))
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("catalog_sources.id"))
    external_id: Mapped[str] = mapped_column(String(150))
    name: Mapped[str | None] = mapped_column(String(300))  # nombre en el idioma de la impresión
    collector_number: Mapped[str | None] = mapped_column(String(30))
    language: Mapped[str] = mapped_column(String(10))
    rarity: Mapped[str | None] = mapped_column(String(100))
    variant: Mapped[str | None] = mapped_column(String(50))
    image_reference: Mapped[str | None] = mapped_column(Text)  # clave en el almacenamiento, no URL pública
    image_sha256: Mapped[str | None] = mapped_column(String(64))
    meta: Mapped[dict] = mapped_column("metadata", Json, default=dict)

    card: Mapped[Card] = relationship(back_populates="printings")
    set: Mapped[CardSet] = relationship(back_populates="printings")


class CardEmbedding(Base):
    __tablename__ = "card_embeddings"
    __table_args__ = (UniqueConstraint("printing_id", "model_name", "model_version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    printing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("card_printings.id", ondelete="CASCADE"))
    model_name: Mapped[str] = mapped_column(String(100))
    model_version: Mapped[str] = mapped_column(String(50))
    embedding = mapped_column(EmbeddingVector)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CardImage(Base):
    """Imagen de referencia de una impresión. Solo se descarga si license_status es 'permitted'."""

    __tablename__ = "card_images"
    __table_args__ = (UniqueConstraint("printing_id", "source_url"), Index("ix_card_images_sha", "sha256"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    printing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("card_printings.id", ondelete="CASCADE"))
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("catalog_sources.id"))
    source_url: Mapped[str] = mapped_column(Text)
    license_status: Mapped[str] = mapped_column(String(20), default="unknown", server_default="unknown")
    # pending | stored | skipped_unlicensed | failed
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    sha256: Mapped[str | None] = mapped_column(String(64))
    storage_key: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(30))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    byte_size: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImportRun(Base):
    __tablename__ = "import_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(50))
    scope: Mapped[dict] = mapped_column(Json, default=dict)
    scope_hash: Mapped[str] = mapped_column(String(64))
    # running | completed | completed_with_errors | interrupted | failed
    status: Mapped[str] = mapped_column(String(30), default="running")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict | None] = mapped_column(Json)


class ImportRunItem(Base):
    __tablename__ = "import_run_items"
    __table_args__ = (UniqueConstraint("run_id", "set_external_id", "language"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_runs.id", ondelete="CASCADE"))
    set_external_id: Mapped[str] = mapped_column(String(100))
    language: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10))  # done | failed
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImageAssociation(Base):
    """Relación auditable entre una impresión y un objeto de R2. Revertir no toca el objeto."""

    __tablename__ = "image_associations"
    __table_args__ = (
        UniqueConstraint("printing_id", "bucket", "object_key"),
        Index("ix_assoc_key", "bucket", "object_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    printing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("card_printings.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(20), default="r2")
    bucket: Mapped[str] = mapped_column(String(100))
    object_key: Mapped[str] = mapped_column(Text)
    etag: Mapped[str | None] = mapped_column(String(100))
    size: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="approved")  # approved | reverted
    method: Mapped[str | None] = mapped_column(String(100))
    match_status: Mapped[str] = mapped_column(String(20))  # matched | probable (según el manifiesto)
    evidence: Mapped[dict] = mapped_column(Json, default=dict)
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str] = mapped_column(String(100))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reverted_by: Mapped[str | None] = mapped_column(String(100))
