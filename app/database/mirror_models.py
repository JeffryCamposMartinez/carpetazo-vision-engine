# ruff: noqa: E501
"""Espejo LOCAL y de solo lectura del catálogo de Carpetazo.cl (fuente de verdad). Solo Pokémon al inicio: categoría 3 (inglés) y 100 (español).

Reglas de diseño:
- Los identificadores maestros son los de Carpetazo, sin cambios: `category_id` (juego), `group_id` (edición) y `product_id` (impresión del catálogo).
  Las cartas se identifican por (category_id, product_id): la misma `product_id` puede aparecer en la categoría 3 y en la 100 (vista en español de un producto inglés).
- Nada se borra: lo que Carpetazo deja de entregar se marca `retired_at`; si reaparece, se limpia y se registra el cambio.
- El espejo no se escribe desde ningún otro sitio que el sincronizador; las referencias verificadas lo apuntan con claves foráneas RESTRICT (el espejo no cascada a nada).
- `digest` = SHA-256 del contenido semántico del registro: permite detectar altas y modificaciones y no reescribir lo que no cambió.
- No se guardan imágenes: solo la URL/clave que entrega la API como trazabilidad. Nada se descarga de R2 en la sincronización.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models import Base, Json


class MirrorSyncRun(Base):
    __tablename__ = "mirror_sync_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_base_url: Mapped[str] = mapped_column(Text)
    scope: Mapped[dict] = mapped_column(Json, default=dict)  # categorías y parámetros
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | ok | partial | failed | interrupted
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    requests_made: Mapped[int] = mapped_column(Integer, default=0)
    counts: Mapped[dict] = mapped_column(Json, default=dict)
    errors: Mapped[list] = mapped_column(Json, default=list)
    cursor: Mapped[dict] = mapped_column(Json, default=dict)  # conjuntos ya terminados: permite reanudar
    resumed_from: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mirror_sync_runs.id"))


class MirrorGame(Base):
    __tablename__ = "mirror_games"

    category_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MirrorSet(Base):
    __tablename__ = "mirror_sets"
    __table_args__ = (ForeignKeyConstraint(["category_id"], ["mirror_games.category_id"], ondelete="RESTRICT"), Index("ix_mirror_sets_category", "category_id"))

    category_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    group_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(Text)
    abbreviation: Mapped[str | None] = mapped_column(String(40))
    published_on: Mapped[str | None] = mapped_column(String(40))  # tal como lo entrega la API
    modified_on: Mapped[str | None] = mapped_column(String(40))
    digest: Mapped[str] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cards_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # última descarga COMPLETA de las cartas de la edición
    cards_modified_on_at_sync: Mapped[str | None] = mapped_column(String(40))  # `modified_on` de la edición cuando se descargaron sus cartas
    cards_count: Mapped[int | None] = mapped_column(Integer)
    cards_listing_digest: Mapped[str | None] = mapped_column(String(64))


class MirrorCard(Base):
    __tablename__ = "mirror_cards"
    __table_args__ = (
        ForeignKeyConstraint(["category_id"], ["mirror_games.category_id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["category_id", "group_id"], ["mirror_sets.category_id", "mirror_sets.group_id"], ondelete="RESTRICT"),
        Index("ix_mirror_cards_group", "category_id", "group_id"),
        Index("ix_mirror_cards_product", "product_id"),
    )

    category_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    product_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    group_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(Text)
    number: Mapped[str | None] = mapped_column(String(40))
    rarity: Mapped[str | None] = mapped_column(String(80))
    source_category_id: Mapped[int | None] = mapped_column(Integer)  # categoryId de la fila tal como lo entrega la API (en la categoría 100: 3 = producto inglés visto en español, 100 = solo español)
    language: Mapped[str | None] = mapped_column(String(10))  # «Language» de extendedData si viene; si no, null (no se adivina)
    game_name: Mapped[str | None] = mapped_column(Text)
    extended_data: Mapped[list] = mapped_column(Json, default=list)  # tal como lo entrega la API (incluye TcgplayerProductId si algún día se expone)
    tcgplayer_product_id: Mapped[int | None] = mapped_column(Integer)  # de extendedData «TcgplayerProductId», solo si la API lo entrega
    image_url: Mapped[str | None] = mapped_column(Text)  # trazabilidad: NUNCA se descarga durante la sincronización
    image_origin: Mapped[str] = mapped_column(String(20), default="none")  # carpetazo_r2 | tcgplayer_cdn | tcgdex | placeholder | none
    image_key: Mapped[str | None] = mapped_column(Text)  # ruta de la imagen cuando el origen es R2 de Carpetazo
    has_image: Mapped[bool] = mapped_column(Boolean, default=False)
    relation: Mapped[str | None] = mapped_column(String(40))  # DERIVADO de source_category_id (no lo entrega la API como tal): catalog_product | spanish_view_of_english_product | spanish_only
    digest: Mapped[str] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mirror_sync_runs.id"))


class MirrorChange(Base):
    """Bitácora de altas, modificaciones, bajas y reapariciones (con antes/después). No se borra."""

    __tablename__ = "mirror_changes"
    __table_args__ = (Index("ix_mirror_changes_entity", "entity", "category_id", "product_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mirror_sync_runs.id"))
    entity: Mapped[str] = mapped_column(String(10))  # game | set | card
    category_id: Mapped[int] = mapped_column(Integer)
    group_id: Mapped[int | None] = mapped_column(Integer)
    product_id: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(12))  # inserted | updated | retired | reappeared
    before: Mapped[dict | None] = mapped_column(Json)
    after: Mapped[dict | None] = mapped_column(Json)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
