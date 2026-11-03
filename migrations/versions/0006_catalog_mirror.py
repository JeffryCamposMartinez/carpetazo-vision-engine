"""espejo local de solo lectura del catálogo de Carpetazo.cl (Pokémon: categorías 3 y 100)

Solo agrega tablas; no toca datos existentes. Se ejecuta SOLO en el laboratorio hasta nueva decisión.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"

JSON = sa.JSON().with_variant(JSONB(), "postgresql")
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "mirror_sync_runs",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("source_base_url", sa.Text, nullable=False),
        sa.Column("scope", JSON),
        sa.Column("dry_run", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("started_at", TS, server_default=sa.func.now()),
        sa.Column("finished_at", TS),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("requests_made", sa.Integer, nullable=False, server_default="0"),
        sa.Column("counts", JSON),
        sa.Column("errors", JSON),
        sa.Column("cursor", JSON),
        sa.Column("resumed_from", sa.Uuid, sa.ForeignKey("mirror_sync_runs.id")),
    )
    op.create_table(
        "mirror_games",
        sa.Column("category_id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("name", sa.Text),
        sa.Column("first_seen_at", TS, server_default=sa.func.now()),
        sa.Column("last_seen_at", TS, server_default=sa.func.now()),
        sa.Column("retired_at", TS),
    )
    op.create_table(
        "mirror_sets",
        sa.Column("category_id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("group_id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("abbreviation", sa.String(40)),
        sa.Column("published_on", sa.String(40)),
        sa.Column("modified_on", sa.String(40)),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("first_seen_at", TS, server_default=sa.func.now()),
        sa.Column("last_seen_at", TS, server_default=sa.func.now()),
        sa.Column("last_changed_at", TS, server_default=sa.func.now()),
        sa.Column("retired_at", TS),
        sa.Column("cards_synced_at", TS),
        sa.Column("cards_modified_on_at_sync", sa.String(40)),
        sa.Column("cards_count", sa.Integer),
        sa.Column("cards_listing_digest", sa.String(64)),
        sa.ForeignKeyConstraint(["category_id"], ["mirror_games.category_id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_mirror_sets_category", "mirror_sets", ["category_id"])
    op.create_table(
        "mirror_cards",
        sa.Column("category_id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("product_id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("group_id", sa.Integer, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("number", sa.String(40)),
        sa.Column("rarity", sa.String(80)),
        sa.Column("source_category_id", sa.Integer),
        sa.Column("language", sa.String(10)),
        sa.Column("game_name", sa.Text),
        sa.Column("extended_data", JSON),
        sa.Column("tcgplayer_product_id", sa.Integer),
        sa.Column("image_url", sa.Text),
        sa.Column("image_origin", sa.String(20), nullable=False, server_default="none"),
        sa.Column("image_key", sa.Text),
        sa.Column("has_image", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("relation", sa.String(40)),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("first_seen_at", TS, server_default=sa.func.now()),
        sa.Column("last_seen_at", TS, server_default=sa.func.now()),
        sa.Column("last_changed_at", TS, server_default=sa.func.now()),
        sa.Column("retired_at", TS),
        sa.Column("last_run_id", sa.Uuid, sa.ForeignKey("mirror_sync_runs.id")),
        sa.ForeignKeyConstraint(["category_id"], ["mirror_games.category_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["category_id", "group_id"], ["mirror_sets.category_id", "mirror_sets.group_id"], ondelete="RESTRICT"),  # noqa: E501
    )
    op.create_index("ix_mirror_cards_group", "mirror_cards", ["category_id", "group_id"])
    op.create_index("ix_mirror_cards_product", "mirror_cards", ["product_id"])
    op.create_table(
        "mirror_changes",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("run_id", sa.Uuid, sa.ForeignKey("mirror_sync_runs.id"), nullable=False),
        sa.Column("entity", sa.String(10), nullable=False),
        sa.Column("category_id", sa.Integer, nullable=False),
        sa.Column("group_id", sa.Integer),
        sa.Column("product_id", sa.Integer),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("before", JSON),
        sa.Column("after", JSON),
        sa.Column("changed_at", TS, server_default=sa.func.now()),
    )
    op.create_index("ix_mirror_changes_entity", "mirror_changes", ["entity", "category_id", "product_id"])


def downgrade() -> None:
    for table in ("mirror_changes", "mirror_cards", "mirror_sets", "mirror_games", "mirror_sync_runs"):
        op.drop_table(table)
