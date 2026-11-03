# ruff: noqa: E501
"""Integración con PostgreSQL nativo. Se omite sin VISION_TEST_POSTGRES_URL.

Usa una base EXCLUSIVA de pruebas (el nombre debe terminar en `_test`): cada prueba borra y recrea el esquema public.
Si el servidor no tiene pgvector, se omite todo salvo que VISION_TEST_ALLOW_VECTOR_STUB=1: en ese modo se reemplaza
`CREATE EXTENSION vector` por un dominio de relleno. Eso valida lo relacional pero NO pgvector (ver docs/postgres-windows.md).
"""

import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database.models import CardEquivalence, CardPrinting, ExternalReference, Game
from tests.test_tcgdex_importer import counts, make_api, make_importer

URL = os.environ.get("VISION_TEST_POSTGRES_URL")
ROOT = Path(__file__).resolve().parents[1]

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not URL, reason="define VISION_TEST_POSTGRES_URL (ver docs/postgres-windows.md)"),
]

NEW_TABLES = {
    "printing_variants",
    "external_references",
    "card_equivalences",
    "import_errors",
    "image_mapping_proposals",
}
OLD_TABLES = {
    "games", "catalog_sources", "sets", "cards", "card_printings", "card_embeddings", "card_images", "import_runs",
    "import_run_items", "image_associations",
}  # fmt: skip


@pytest.fixture
def engine():
    eng = create_engine(URL)
    assert (eng.url.database or "").endswith("_test"), "la base de pruebas debe llamarse *_test (se borra el esquema)"
    with eng.connect() as c:
        has_vector = bool(c.scalar(text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")))
    stub = not has_vector
    if stub and os.environ.get("VISION_TEST_ALLOW_VECTOR_STUB") != "1":
        pytest.skip(
            "pgvector no está instalado en este servidor (VISION_TEST_ALLOW_VECTOR_STUB=1 valida solo lo relacional)"
        )
    eng.pgvector_available = has_vector

    if stub:

        @event.listens_for(eng, "before_cursor_execute", retval=True)
        def _stub(conn, cursor, statement, params, context, executemany):
            if statement.strip().upper().startswith("CREATE EXTENSION IF NOT EXISTS VECTOR"):
                return "SELECT 1", params
            return statement, params

    def reset():
        with eng.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
            if stub:
                c.execute(text("CREATE DOMAIN vector AS real[]"))

    reset()
    eng.reset_schema = reset
    yield eng
    eng.dispose()


def migrate(engine, action, revision):
    with engine.begin() as conn:
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "migrations"))
        cfg.attributes["connection"] = conn
        getattr(command, action)(cfg, revision)


def current(engine):
    with engine.connect() as c:
        return (
            c.scalar(text("SELECT version_num FROM alembic_version"))
            if inspect(c).has_table("alembic_version")
            else None
        )


def test_upgrade_from_empty_creates_schema_constraints_and_indexes(engine):
    migrate(engine, "upgrade", "head")
    assert current(engine) == "0004"
    insp = inspect(engine)
    assert (OLD_TABLES | NEW_TABLES) <= set(insp.get_table_names())

    constraints = {
        r[0]
        for r in engine.connect().execute(
            text("SELECT conname FROM pg_constraint WHERE connamespace = 'public'::regnamespace")
        )
    }
    assert {"uq_printing_card_set", "uq_variant_source_id", "uq_extref_entity", "uq_proposal_object_source",
            "ck_extref_one_entity", "ck_equiv_ordered"} <= constraints  # fmt: skip
    indexes = {i["name"] for t in (OLD_TABLES | NEW_TABLES) for i in insp.get_indexes(t)}
    assert {"ix_extref_lookup", "ix_variants_printing", "ix_proposals_outcome", "ix_import_errors_run",
            "ix_printings_name", "ix_cards_name", "ix_assoc_key"} <= indexes  # fmt: skip
    fks = [fk["referred_table"] for fk in insp.get_foreign_keys("printing_variants")]
    assert fks == ["card_printings"]
    with engine.connect() as c:
        has_ext = c.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
    assert bool(has_ext) == engine.pgvector_available  # con relleno no hay extensión real


def test_constraints_and_referential_integrity_on_real_postgres(engine):
    migrate(engine, "upgrade", "head")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    make_importer(factory, make_api()).run()
    with factory() as db:
        p = db.scalar(select(CardPrinting).where(CardPrinting.external_id == "base1-1"))
        game = db.scalar(select(Game))
        bad = [
            CardPrinting(card_id=p.card_id, set_id=p.set_id, external_id="dup", language="en", catalog_language="en"),
            CardPrinting(card_id=uuid.uuid4(), set_id=p.set_id, external_id="huerfana", language="en", catalog_language="en"),
            ExternalReference(entity_type="game", entity_key="k", provider="x", external_id="1"),
            ExternalReference(entity_type="game", entity_key="k", game_id=game.id, card_id=p.card_id, provider="x", external_id="1"),
            CardEquivalence(card_a_id=uuid.UUID(int=2), card_b_id=uuid.UUID(int=1)),
        ]  # fmt: skip
        for row in bad:
            db.add(row)
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()


def test_importer_is_idempotent_on_real_postgres(engine):
    migrate(engine, "upgrade", "head")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    importer = make_importer(factory, make_api())
    first = importer.run(languages=("en", "fr"))
    before = counts(factory)
    second = importer.run(languages=("en", "fr"))
    assert first.status == second.status == "completed"
    assert counts(factory) == before and before["cards"] == 3 and before["card_printings"] == 4
    assert second.external_references == {}


def test_card_savepoint_rollback_on_real_postgres(engine, monkeypatch):
    migrate(engine, "upgrade", "head")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    importer = make_importer(factory, make_api())
    original = importer._upsert_variants

    def boom(db, printing, lang, raw, delta, cid):
        if cid == "base1-4":
            raise RuntimeError("fallo simulado")
        return original(db, printing, lang, raw, delta, cid)

    monkeypatch.setattr(importer, "_upsert_variants", boom)
    report = importer.run()
    assert [e["external_id"] for e in report.errors] == ["base1-4"]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(CardPrinting)) == 2


def test_downgrade_roundtrip_to_base_and_back(engine):
    migrate(engine, "upgrade", "head")
    migrate(engine, "downgrade", "0003")
    tables = set(inspect(engine).get_table_names())
    assert not (NEW_TABLES & tables) and "image_associations" in tables
    cols = {c["name"] for c in inspect(engine).get_columns("card_printings")}
    assert "catalog_language" not in cols and "variant" in cols
    migrate(engine, "downgrade", "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    migrate(engine, "upgrade", "head")
    assert current(engine) == "0004"


def test_0004_refuses_ambiguous_data_then_backfills_when_clean(engine):
    migrate(engine, "upgrade", "0003")
    ids = {k: uuid.uuid4() for k in ("game", "set", "card", "p1", "p2")}
    with engine.begin() as c:
        c.execute(text("INSERT INTO games (id, name, slug) VALUES (:g, 'P', 'p')"), {"g": ids["game"]})
        c.execute(text("INSERT INTO sets (id, game_id, external_id, name, language, metadata) VALUES (:s, :g, 'e', 'S', 'es', '{}')"),
                  {"s": ids["set"], "g": ids["game"]})  # fmt: skip
        c.execute(text("INSERT INTO cards (id, game_id, external_id, canonical_name, metadata) VALUES (:c, :g, 'c1', 'C', '{}')"),
                  {"c": ids["card"], "g": ids["game"]})  # fmt: skip
        for key, variant in (("p1", "normal"), ("p2", "holo")):
            c.execute(
                text("INSERT INTO card_printings (id, card_id, set_id, external_id, language, variant, metadata) "
                     "VALUES (:p, :c, :s, :e, 'es', :v, '{}')"),
                {"p": ids[key], "c": ids["card"], "s": ids["set"], "e": f"x-{key}", "v": variant},
            )  # fmt: skip
    with pytest.raises(RuntimeError, match="más de una impresión"):
        migrate(engine, "upgrade", "head")
    assert current(engine) == "0003"  # la migración falló completa: nada a medias
    with engine.begin() as c:
        assert c.scalar(text("SELECT count(*) FROM card_printings")) == 2
        c.execute(text("DELETE FROM card_printings WHERE id = :p"), {"p": ids["p2"]})
    migrate(engine, "upgrade", "head")
    with engine.connect() as c:
        assert c.scalar(text("SELECT catalog_language FROM card_printings")) == "es"  # copiado desde language
        assert c.scalar(text("SELECT identity_basis FROM cards")) == "provider_card_id"
