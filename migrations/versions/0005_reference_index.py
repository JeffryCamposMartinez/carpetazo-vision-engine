"""índice de referencias: verificadas y candidatas separadas, con embeddings de dimensión fija por modelo

No destruye datos: solo agrega tablas. Se ejecuta SOLO en el laboratorio hasta que se decida otra cosa.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

from app.recognition.index.registry import MODELS, TIERS

revision = "0005"
down_revision = "0004"


def upgrade() -> None:
    uuid = sa.Uuid
    op.create_table(
        "reference_import_runs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("requested_by", sa.String(100), nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("counts", sa.JSON().with_variant(JSONB(), "postgresql")),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("kind in ('verified','candidate')", name="ck_import_run_kind"),
    )
    op.create_table(
        "verified_references",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("source_kind", sa.String(30), nullable=False),
        sa.Column("source_ref", sa.Text, nullable=False),
        sa.Column("storage_key", sa.Text),
        sa.Column("image_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("license_status", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("set_name", sa.Text, nullable=False),
        sa.Column("collector_number", sa.String(30), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("finish", sa.String(20), nullable=False),
        sa.Column("pattern", sa.String(20), nullable=False),
        sa.Column("card_id", uuid, sa.ForeignKey("cards.id")),
        sa.Column("printing_id", uuid, sa.ForeignKey("card_printings.id")),
        sa.Column("variant_id", uuid, sa.ForeignKey("printing_variants.id")),
        sa.Column("verified_by", sa.String(100), nullable=False),
        sa.Column("verified_at", sa.String(40), nullable=False),
        sa.Column("verification_note", sa.Text, nullable=False),
        sa.Column("imported_by", sa.String(100), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("import_run_id", uuid, sa.ForeignKey("reference_import_runs.id")),
        sa.CheckConstraint("length(trim(verified_by)) > 0", name="ck_vref_verified_by"),
        sa.CheckConstraint("length(trim(verification_note)) > 0", name="ck_vref_note"),
        sa.CheckConstraint("source_kind in ('pilot_dataset','assisted_review')", name="ck_vref_source_kind"),
    )
    op.create_table(
        "candidate_references",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("storage_key", sa.Text, nullable=False),
        sa.Column("image_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("claimed_label", sa.Text),
        sa.Column("proposal_status", sa.String(20), nullable=False),
        sa.Column("proposal_source", sa.Text, nullable=False),
        sa.Column("license_status", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("imported_by", sa.String(100), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("import_run_id", uuid, sa.ForeignKey("reference_import_runs.id")),
        sa.CheckConstraint("proposal_status in ('probable','ambiguous')", name="ck_cref_status"),
    )
    for slot in MODELS.values():
        for tier in TIERS:
            name = slot.table(tier)
            parent = "verified_references" if tier == "verified" else "candidate_references"
            op.create_table(
                name,
                sa.Column("id", uuid, primary_key=True),
                sa.Column("reference_id", uuid, sa.ForeignKey(f"{parent}.id", ondelete="CASCADE"), nullable=False),
                sa.Column("normalization", sa.String(30), nullable=False),
                sa.Column("embedding", Vector(slot.dim), nullable=False),
                sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
                sa.UniqueConstraint("reference_id", "normalization", name=f"uq_{name}"),
            )


def downgrade() -> None:
    for slot in MODELS.values():
        for tier in TIERS:
            op.drop_table(slot.table(tier))
    for table in ("candidate_references", "verified_references", "reference_import_runs"):
        op.drop_table(table)
