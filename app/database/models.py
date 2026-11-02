import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid, func
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
