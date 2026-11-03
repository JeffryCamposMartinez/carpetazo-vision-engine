# ruff: noqa: E501
"""índice de referencias con los identificadores de Carpetazo: tres niveles (verificada, candidata, ambigua) y vínculo (category_id, product_id) al espejo

Reemplaza los ids UUID del catálogo propio por (category_id, product_id) de Carpetazo. Las tablas de 0005 deben estar VACÍAS (nunca hubo datos reales);
si tuvieran filas la migración se detiene sin tocar nada.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0007"
down_revision = "0006"

SLOTS = (("dinov2_small_ed25f3a_p1", 768), ("grid_g1", 456))
MIRROR = ["mirror_cards.category_id", "mirror_cards.product_id"]


def upgrade() -> None:
    bind = op.get_bind()
    for table in ("verified_references", "candidate_references"):
        if bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar():  # noqa: S608
            raise RuntimeError(f"{table} tiene filas: esta migración solo se aplica sobre el índice vacío del laboratorio")
    # --- verificadas: producto de Carpetazo en lugar de ids UUID propios
    for col in ("card_id", "printing_id", "variant_id"):
        op.drop_column("verified_references", col)
    op.add_column("verified_references", sa.Column("category_id", sa.Integer, nullable=False))
    op.add_column("verified_references", sa.Column("product_id", sa.Integer, nullable=False))
    op.add_column("verified_references", sa.Column("variant", sa.String(20), nullable=False, server_default="unknown"))
    op.add_column("verified_references", sa.Column("variant_key", sa.Text))
    op.create_foreign_key("fk_vref_mirror_card", "verified_references", "mirror_cards", ["category_id", "product_id"], ["category_id", "product_id"], ondelete="RESTRICT")
    op.create_check_constraint("ck_vref_product_link", "verified_references", "category_id IS NOT NULL AND product_id IS NOT NULL")
    op.create_index("ix_vref_product", "verified_references", ["category_id", "product_id"])
    # --- candidatas: vínculo afirmado por Carpetazo, sin revisar
    op.drop_constraint("ck_cref_status", "candidate_references", type_="check")
    for col in ("claimed_label", "proposal_status", "proposal_source"):
        op.drop_column("candidate_references", col)
    op.add_column("candidate_references", sa.Column("claimed_category_id", sa.Integer, nullable=False))
    op.add_column("candidate_references", sa.Column("claimed_product_id", sa.Integer, nullable=False))
    op.add_column("candidate_references", sa.Column("link_source", sa.String(40), nullable=False, server_default="carpetazo_catalog"))
    op.add_column("candidate_references", sa.Column("status", sa.String(20), nullable=False, server_default="unreviewed"))
    op.add_column("candidate_references", sa.Column("note", sa.Text))
    op.create_check_constraint("ck_cref_status", "candidate_references", "status in ('unreviewed')")
    op.create_foreign_key("fk_cref_mirror_card", "candidate_references", "mirror_cards", ["claimed_category_id", "claimed_product_id"], ["category_id", "product_id"], ondelete="RESTRICT")
    # --- ambiguas: tercer nivel, con su propio juego de tablas
    op.create_table(
        "ambiguous_references",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("storage_key", sa.Text, nullable=False),
        sa.Column("image_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("claimed_category_id", sa.Integer),
        sa.Column("claimed_product_id", sa.Integer),
        sa.Column("ambiguity_reason", sa.Text, nullable=False),
        sa.Column("license_status", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("imported_by", sa.String(100), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("import_run_id", sa.Uuid, sa.ForeignKey("reference_import_runs.id")),
        sa.CheckConstraint("length(trim(ambiguity_reason)) > 0", name="ck_aref_reason"),
        sa.CheckConstraint("(claimed_category_id IS NULL) = (claimed_product_id IS NULL)", name="ck_aref_claim_pair"),
        sa.ForeignKeyConstraint(["claimed_category_id", "claimed_product_id"], MIRROR, ondelete="RESTRICT"),
    )
    for slot_name, dim in SLOTS:
        name = f"ambiguous_embeddings_{slot_name}"
        op.create_table(
            name,
            sa.Column("id", sa.Uuid, primary_key=True),
            sa.Column("reference_id", sa.Uuid, sa.ForeignKey("ambiguous_references.id", ondelete="CASCADE"), nullable=False),
            sa.Column("normalization", sa.String(30), nullable=False),
            sa.Column("embedding", Vector(dim), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("reference_id", "normalization", name=f"uq_{name}"),
        )


def downgrade() -> None:
    for slot_name, _dim in SLOTS:
        op.drop_table(f"ambiguous_embeddings_{slot_name}")
    op.drop_table("ambiguous_references")
    op.drop_constraint("fk_cref_mirror_card", "candidate_references", type_="foreignkey")
    op.drop_constraint("ck_cref_status", "candidate_references", type_="check")
    for col in ("claimed_category_id", "claimed_product_id", "link_source", "status", "note"):
        op.drop_column("candidate_references", col)
    op.add_column("candidate_references", sa.Column("claimed_label", sa.Text))
    op.add_column("candidate_references", sa.Column("proposal_status", sa.String(20), nullable=False, server_default="probable"))
    op.add_column("candidate_references", sa.Column("proposal_source", sa.Text, nullable=False, server_default=""))
    op.create_check_constraint("ck_cref_status", "candidate_references", "proposal_status in ('probable','ambiguous')")
    op.drop_index("ix_vref_product", "verified_references")
    op.drop_constraint("ck_vref_product_link", "verified_references", type_="check")
    op.drop_constraint("fk_vref_mirror_card", "verified_references", type_="foreignkey")
    for col in ("category_id", "product_id", "variant", "variant_key"):
        op.drop_column("verified_references", col)
    op.add_column("verified_references", sa.Column("card_id", sa.Uuid, sa.ForeignKey("cards.id")))
    op.add_column("verified_references", sa.Column("printing_id", sa.Uuid, sa.ForeignKey("card_printings.id")))
    op.add_column("verified_references", sa.Column("variant_id", sa.Uuid, sa.ForeignKey("printing_variants.id")))
