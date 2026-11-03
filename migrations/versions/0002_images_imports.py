"""card images, import runs, localized printing names, card provenance

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.add_column("cards", sa.Column("source_id", sa.Uuid, sa.ForeignKey("catalog_sources.id")))
    op.add_column("card_printings", sa.Column("name", sa.String(300)))
    op.create_index("ix_printings_name", "card_printings", ["name"])

    op.create_table(
        "card_images",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("printing_id", sa.Uuid, sa.ForeignKey("card_printings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", sa.Uuid, sa.ForeignKey("catalog_sources.id")),
        sa.Column("source_url", sa.Text, nullable=False),
        sa.Column("license_status", sa.String(20), server_default="unknown", nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("sha256", sa.String(64)),
        sa.Column("storage_key", sa.Text),
        sa.Column("content_type", sa.String(30)),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("byte_size", sa.Integer),
        sa.Column("error", sa.Text),
        sa.Column("fetched_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("printing_id", "source_url"),
    )
    op.create_index("ix_card_images_sha", "card_images", ["sha256"])

    op.create_table(
        "import_runs",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("scope", JSONB, nullable=False),
        sa.Column("scope_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("dry_run", sa.Boolean, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("summary", JSONB),
    )
    op.create_table(
        "import_run_items",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("run_id", sa.Uuid, sa.ForeignKey("import_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("set_external_id", sa.String(100), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("finished_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("run_id", "set_external_id", "language"),
    )


def downgrade() -> None:
    for table in ("import_run_items", "import_runs", "card_images"):
        op.drop_table(table)
    op.drop_index("ix_printings_name", "card_printings")
    op.drop_column("card_printings", "name")
    op.drop_column("cards", "source_id")
