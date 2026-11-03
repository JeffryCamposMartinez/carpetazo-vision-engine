"""image associations (printing <-> R2 object)

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.create_table(
        "image_associations",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("printing_id", sa.Uuid, sa.ForeignKey("card_printings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("bucket", sa.String(100), nullable=False),
        sa.Column("object_key", sa.Text, nullable=False),
        sa.Column("etag", sa.String(100)),
        sa.Column("size", sa.Integer),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("method", sa.String(100)),
        sa.Column("match_status", sa.String(20), nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("approved_by", sa.String(100), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reverted_at", sa.DateTime(timezone=True)),
        sa.Column("reverted_by", sa.String(100)),
        sa.UniqueConstraint("printing_id", "bucket", "object_key"),
    )
    op.create_index("ix_assoc_key", "image_associations", ["bucket", "object_key"])


def downgrade() -> None:
    op.drop_table("image_associations")
