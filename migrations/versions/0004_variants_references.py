"""variantes de impresión, referencias externas, equivalencias, errores de importación y propuestas de imagen

No destruye datos: solo agrega tablas y columnas. card_printings.variant (texto) queda como columna heredada
sin uso; las variantes viven en printing_variants.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    dup = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM (SELECT 1 FROM card_printings GROUP BY card_id, set_id HAVING count(*) > 1) t"
            )
        )
        .scalar()
    )
    if dup:
        raise RuntimeError(
            f"{dup} pares (carta, set) tienen más de una impresión: fusiónalos a mano antes de migrar "
            "(el modelo nuevo guarda las variantes en printing_variants)."
        )

    op.add_column(
        "cards", sa.Column("identity_basis", sa.String(30), server_default="provider_card_id", nullable=False)
    )
    op.add_column("card_printings", sa.Column("catalog_language", sa.String(10)))
    op.execute("UPDATE card_printings SET catalog_language = language")
    op.alter_column("card_printings", "catalog_language", nullable=False)
    op.create_unique_constraint("uq_printing_card_set", "card_printings", ["card_id", "set_id"])
    op.add_column("card_images", sa.Column("language", sa.String(10), server_default="unknown", nullable=False))

    op.create_table(
        "printing_variants",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("printing_id", sa.Uuid, sa.ForeignKey("card_printings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("variant_type", sa.String(30), nullable=False),
        sa.Column("variant_subtype", sa.String(60), server_default="", nullable=False),
        sa.Column("foil", sa.String(60), server_default="", nullable=False),
        sa.Column("stamp", sa.String(120), server_default="", nullable=False),
        sa.Column("size", sa.String(30), server_default="", nullable=False),
        sa.Column("language", sa.String(10)),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("external_id", sa.String(150), nullable=False),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("printing_id", "source", "external_id", name="uq_variant_source_id"),
    )
    op.create_index("ix_variants_printing", "printing_variants", ["printing_id"])

    op.add_column(
        "image_associations",
        sa.Column("variant_id", sa.Uuid, sa.ForeignKey("printing_variants.id", ondelete="SET NULL")),
    )
    op.add_column(
        "image_associations", sa.Column("image_language", sa.String(10), server_default="unknown", nullable=False)
    )

    op.create_table(
        "external_references",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_key", sa.String(36), nullable=False),
        sa.Column("game_id", sa.Uuid, sa.ForeignKey("games.id", ondelete="CASCADE")),
        sa.Column("set_id", sa.Uuid, sa.ForeignKey("sets.id", ondelete="CASCADE")),
        sa.Column("card_id", sa.Uuid, sa.ForeignKey("cards.id", ondelete="CASCADE")),
        sa.Column("printing_id", sa.Uuid, sa.ForeignKey("card_printings.id", ondelete="CASCADE")),
        sa.Column("variant_id", sa.Uuid, sa.ForeignKey("printing_variants.id", ondelete="CASCADE")),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("scope", sa.String(20), server_default="", nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "(CASE WHEN game_id IS NULL THEN 0 ELSE 1 END + CASE WHEN set_id IS NULL THEN 0 ELSE 1 END"
            " + CASE WHEN card_id IS NULL THEN 0 ELSE 1 END + CASE WHEN printing_id IS NULL THEN 0 ELSE 1 END"
            " + CASE WHEN variant_id IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_extref_one_entity",
        ),
        sa.UniqueConstraint("provider", "scope", "external_id", "entity_type", "entity_key", name="uq_extref_entity"),
    )
    op.create_index("ix_extref_lookup", "external_references", ["provider", "entity_type", "scope", "external_id"])

    op.create_table(
        "card_equivalences",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("card_a_id", sa.Uuid, sa.ForeignKey("cards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("card_b_id", sa.Uuid, sa.ForeignKey("cards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relation", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("evidence", JSONB, nullable=False, server_default="{}"),
        sa.Column("reviewed_by", sa.String(100)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("card_a_id < card_b_id", name="ck_equiv_ordered"),
        sa.UniqueConstraint("card_a_id", "card_b_id", "relation"),
    )

    op.create_table(
        "import_errors",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("run_id", sa.Uuid, sa.ForeignKey("import_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("language", sa.String(10)),
        sa.Column("stage", sa.String(30), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_import_errors_run", "import_errors", ["run_id"])

    op.create_table(
        "image_mapping_proposals",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("bucket", sa.String(100), nullable=False),
        sa.Column("object_key", sa.Text, nullable=False),
        sa.Column("r2_folder", sa.Text),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("candidate_card_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("resolved_card_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("qualifiers", sa.Text),
        sa.Column("image_language", sa.String(10), server_default="unknown", nullable=False),
        sa.Column("flags", JSONB, nullable=False, server_default="[]"),
        sa.Column("evidence", JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("bucket", "object_key", "source", name="uq_proposal_object_source"),
    )
    op.create_index("ix_proposals_outcome", "image_mapping_proposals", ["outcome"])


def downgrade() -> None:
    for table in ("image_mapping_proposals", "import_errors", "card_equivalences", "external_references"):
        op.drop_table(table)
    op.drop_column("image_associations", "image_language")
    op.drop_column("image_associations", "variant_id")
    op.drop_table("printing_variants")
    op.drop_column("card_images", "language")
    op.drop_constraint("uq_printing_card_set", "card_printings", type_="unique")
    op.drop_column("card_printings", "catalog_language")
    op.drop_column("cards", "identity_basis")
