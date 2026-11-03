import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
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
    # Cómo se estableció la identidad. 'provider_card_id' = la que da el proveedor (TCGdex: id por set+número);
    # no es una identidad global entre reimpresiones: para eso existe CardEquivalence (opcional y revisable).
    identity_basis: Mapped[str] = mapped_column(
        String(30), default="provider_card_id", server_default="provider_card_id"
    )
    canonical_name: Mapped[str] = mapped_column(String(300))
    meta: Mapped[dict] = mapped_column("metadata", Json, default=dict)

    printings: Mapped[list["CardPrinting"]] = relationship(back_populates="card")


class CardPrinting(Base, Timestamps):
    __tablename__ = "card_printings"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id"),
        UniqueConstraint("card_id", "set_id", name="uq_printing_card_set"),
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
    language: Mapped[str] = mapped_column(String(10))  # idioma de la impresión (declarado por el proveedor)
    catalog_language: Mapped[str] = mapped_column(
        String(10), default=lambda ctx: ctx.get_current_parameters()["language"]
    )  # idioma del catálogo del que viene el registro
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
    language: Mapped[str] = mapped_column(String(10), default="unknown", server_default="unknown")
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
    # Si la imagen corresponde a una variante concreta de la impresión (Shadowless, Poke Ball Pattern...).
    variant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("printing_variants.id", ondelete="SET NULL"))
    # Idioma verificado de la imagen; 'unknown' mientras no se compruebe.
    image_language: Mapped[str] = mapped_column(String(10), default="unknown", server_default="unknown")
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


class PrintingVariant(Base):
    """Variante física o visual de una impresión. Una impresión puede tener varias."""

    __tablename__ = "printing_variants"
    __table_args__ = (
        UniqueConstraint("printing_id", "source", "external_id", name="uq_variant_source_id"),
        Index("ix_variants_printing", "printing_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    printing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("card_printings.id", ondelete="CASCADE"))
    # Atributos vacíos = '' (no NULL) para no depender de cómo cada motor trata NULL en índices únicos.
    variant_type: Mapped[str] = mapped_column(String(30))  # normal | holo | reverse | ...
    variant_subtype: Mapped[str] = mapped_column(String(60), default="", server_default="")  # shadowless, unlimited...
    foil: Mapped[str] = mapped_column(String(60), default="", server_default="")  # patrón de foil: pokeball, masterball
    stamp: Mapped[str] = mapped_column(String(120), default="", server_default="")  # sellos ordenados unidos con '+'
    size: Mapped[str] = mapped_column(String(30), default="", server_default="")
    language: Mapped[str | None] = mapped_column(String(10))  # solo si está verificado; NULL = sin verificar
    source: Mapped[str] = mapped_column(String(30))  # tcgdex | tcgdex_flags
    # Id de la variante en el proveedor; si no existe, una clave derivada de los atributos.
    external_id: Mapped[str] = mapped_column(String(150))
    meta: Mapped[dict] = mapped_column("metadata", Json, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExternalReference(Base):
    """Identificador externo de una entidad. Exactamente una columna de entidad está informada.

    Un mismo id externo puede apuntar a varias entidades (p. ej. un productId de TCGplayer compartido por dos
    variantes): la unicidad es por entidad, no global.
    """

    __tablename__ = "external_references"
    __table_args__ = (
        CheckConstraint(
            "(CASE WHEN game_id IS NULL THEN 0 ELSE 1 END + CASE WHEN set_id IS NULL THEN 0 ELSE 1 END"
            " + CASE WHEN card_id IS NULL THEN 0 ELSE 1 END + CASE WHEN printing_id IS NULL THEN 0 ELSE 1 END"
            " + CASE WHEN variant_id IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_extref_one_entity",
        ),
        UniqueConstraint("provider", "scope", "external_id", "entity_type", "entity_key", name="uq_extref_entity"),
        Index("ix_extref_lookup", "provider", "entity_type", "scope", "external_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(20))  # game | set | card | printing | variant
    entity_key: Mapped[str] = mapped_column(String(36))  # uuid de la entidad, repetido para la restricción única
    game_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    set_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sets.id", ondelete="CASCADE"))
    card_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"))
    printing_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("card_printings.id", ondelete="CASCADE"))
    variant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("printing_variants.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(30))  # tcgdex | tcgplayer | cardmarket
    scope: Mapped[str] = mapped_column(String(20), default="", server_default="")  # p. ej. idioma del catálogo
    external_id: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CardEquivalence(Base):
    """Relación opcional y revisable entre dos Card (reimpresión / misma carta). Nunca se crea sola."""

    __tablename__ = "card_equivalences"
    __table_args__ = (
        CheckConstraint("card_a_id < card_b_id", name="ck_equiv_ordered"),
        UniqueConstraint("card_a_id", "card_b_id", "relation"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    card_a_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"))
    card_b_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"))
    relation: Mapped[str] = mapped_column(String(20), default="reprint_of")  # reprint_of | same_card
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed | confirmed | rejected
    evidence: Mapped[dict] = mapped_column(Json, default=dict)
    reviewed_by: Mapped[str | None] = mapped_column(String(100))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImportIssue(Base):
    """Error registrado por entidad durante una importación (la importación continúa)."""

    __tablename__ = "import_errors"
    __table_args__ = (Index("ix_import_errors_run", "run_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_runs.id", ondelete="CASCADE"))
    entity_type: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(200))
    language: Mapped[str | None] = mapped_column(String(10))
    stage: Mapped[str] = mapped_column(String(30))  # fetch | validate | persist
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImageMappingProposal(Base):
    """Propuesta de asociación objeto de R2 -> carta. Separada de ImageAssociation (las aprobadas)."""

    __tablename__ = "image_mapping_proposals"
    __table_args__ = (
        UniqueConstraint("bucket", "object_key", "source", name="uq_proposal_object_source"),
        Index("ix_proposals_outcome", "outcome"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    bucket: Mapped[str] = mapped_column(String(100))
    object_key: Mapped[str] = mapped_column(Text)
    r2_folder: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(30))  # manifest | audit_per_object
    # unique_candidate | multiple_candidates | no_candidate | excluded
    outcome: Mapped[str] = mapped_column(String(30))
    candidate_card_ids: Mapped[list] = mapped_column(Json, default=list)  # ids de TCGdex tal como llegan
    resolved_card_ids: Mapped[list] = mapped_column(Json, default=list)  # uuids de Card si existen en la base
    qualifiers: Mapped[str | None] = mapped_column(Text)
    image_language: Mapped[str] = mapped_column(String(10), default="unknown", server_default="unknown")
    flags: Mapped[list] = mapped_column(Json, default=list)  # variant_needed, duplicate_target, grouped_folder...
    evidence: Mapped[dict] = mapped_column(Json, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed | rejected | superseded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
