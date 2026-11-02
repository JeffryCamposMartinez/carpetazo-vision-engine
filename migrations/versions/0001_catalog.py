"""catalog schema

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None


def _ts():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "games",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(50), nullable=False, unique=True),
    )
    op.create_table(
        "catalog_sources",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("source_url", sa.Text),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("license_status", sa.String(20), server_default="unknown", nullable=False),
        sa.Column("attribution", sa.Text),
        sa.UniqueConstraint("provider", "external_id"),
    )
    op.create_table(
        "sets",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("game_id", sa.Uuid, sa.ForeignKey("games.id"), nullable=False),
        sa.Column("source_id", sa.Uuid, sa.ForeignKey("catalog_sources.id")),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("code", sa.String(50)),
        sa.Column("release_date", sa.Date),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        *_ts(),
        sa.UniqueConstraint("game_id", "external_id", "language"),
    )
    op.create_table(
        "cards",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("game_id", sa.Uuid, sa.ForeignKey("games.id"), nullable=False),
        sa.Column("external_id", sa.String(100)),
        sa.Column("canonical_name", sa.String(300), nullable=False),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        *_ts(),
        sa.UniqueConstraint("game_id", "external_id"),
    )
    op.create_index("ix_cards_name", "cards", ["canonical_name"])
    op.create_table(
        "card_printings",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("card_id", sa.Uuid, sa.ForeignKey("cards.id"), nullable=False),
        sa.Column("set_id", sa.Uuid, sa.ForeignKey("sets.id"), nullable=False),
        sa.Column("source_id", sa.Uuid, sa.ForeignKey("catalog_sources.id")),
        sa.Column("external_id", sa.String(150), nullable=False),
        sa.Column("collector_number", sa.String(30)),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("rarity", sa.String(100)),
        sa.Column("variant", sa.String(50)),
        sa.Column("image_reference", sa.Text),
        sa.Column("image_sha256", sa.String(64)),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        *_ts(),
        sa.UniqueConstraint("source_id", "external_id"),
    )
    op.create_index("ix_printings_card", "card_printings", ["card_id"])
    op.create_index("ix_printings_set_number", "card_printings", ["set_id", "collector_number"])
    op.create_table(
        "card_embeddings",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("printing_id", sa.Uuid, sa.ForeignKey("card_printings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("model_version", sa.String(50), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("printing_id", "model_name", "model_version"),
    )


def downgrade() -> None:
    for table in ("card_embeddings", "card_printings", "cards", "sets", "catalog_sources", "games"):
        op.drop_table(table)
