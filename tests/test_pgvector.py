# ruff: noqa: E501
"""pgvector REAL: sin relleno ni mocks. Se omite sin VISION_TEST_POSTGRES_URL o si el servidor no ofrece la extensión.

La base debe llamarse *_test (se borra el esquema public). Cada prueba parte de las migraciones aplicadas desde cero:
la migración 0001 es la que ejecuta `CREATE EXTENSION vector`.
"""

import re
from math import sqrt

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.database.models import CardEmbedding, CardPrinting
from tests.test_postgres import URL, migrate
from tests.test_tcgdex_importer import make_api, make_importer

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not URL, reason="define VISION_TEST_POSTGRES_URL (ver docs/postgres-windows.md)"),
]


@pytest.fixture
def real(request):
    eng = create_engine(URL)
    assert (eng.url.database or "").endswith("_test")
    with eng.connect() as c:
        if not c.scalar(text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")):
            pytest.skip("este servidor no tiene pgvector (sin relleno no hay prueba real)")
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    migrate(eng, "upgrade", "head")  # ejecuta CREATE EXTENSION vector de verdad
    yield eng
    eng.dispose()


def scalar(eng, sql, **params):
    with eng.connect() as c:
        return c.scalar(text(sql), params)


def test_extension_is_real_and_created_by_the_migrations(real):
    version = scalar(real, "SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    assert re.fullmatch(r"0\.\d+\.\d+", version), version
    assert scalar(real, "SELECT count(*) FROM pg_am WHERE amname IN ('hnsw', 'ivfflat')") == 2
    assert (
        scalar(
            real,
            "SELECT count(*) FROM pg_opclass WHERE opcname IN ('vector_l2_ops', 'vector_cosine_ops', 'vector_ip_ops')",
        )
        >= 6
    )


def test_distance_operators_return_the_expected_values(real):
    row = (
        real.connect()
        .execute(
            text(
                "SELECT '[1,2,3]'::vector <-> '[4,5,6]'::vector, '[1,2,3]'::vector <=> '[1,2,3]'::vector, "
                "'[1,0,0]'::vector <=> '[0,1,0]'::vector, '[1,2,3]'::vector <#> '[4,5,6]'::vector"
            )
        )
        .one()
    )
    l2, cos_same, cos_orthogonal, neg_inner = row
    assert l2 == pytest.approx(sqrt(27))
    assert cos_same == pytest.approx(0, abs=1e-6) and cos_orthogonal == pytest.approx(1)
    assert neg_inner == pytest.approx(-32)  # <#> devuelve el producto interno NEGATIVO


def seed_embeddings(real):
    factory = sessionmaker(bind=real, expire_on_commit=False)
    make_importer(factory, make_api()).run()
    vectors = {"base1-1": [1, 0, 0], "base1-4": [0, 1, 0], "sv-test-9": [0.9, 0.1, 0]}
    with factory() as db:
        printings = {p.external_id: p for p in db.scalars(select(CardPrinting).where(CardPrinting.language == "en"))}
        for ext, vec in vectors.items():
            db.add(CardEmbedding(printing_id=printings[ext].id, model_name="lab", model_version="1", embedding=vec))
        db.commit()
    return factory


def test_nearest_neighbours_with_each_operator_through_the_real_table(real):
    factory = seed_embeddings(real)
    query = [1, 0.05, 0]
    with factory() as db:

        def order(expr):
            rows = db.execute(
                select(CardPrinting.external_id)
                .join(CardEmbedding, CardEmbedding.printing_id == CardPrinting.id)
                .order_by(expr(query))
            )
            return [r[0] for r in rows]

        # Distancias a [1, 0.05, 0]: base1-1 = 0.05, sv-test-9 ≈ 0.112, base1-4 ≈ 1.0 (calculadas a mano)
        expected = ["base1-1", "sv-test-9", "base1-4"]
        assert order(CardEmbedding.embedding.l2_distance) == expected
        assert order(CardEmbedding.embedding.cosine_distance) == expected
        assert order(CardEmbedding.embedding.max_inner_product) == expected  # mayor producto interno primero


def test_two_model_versions_can_coexist_but_not_duplicate(real):
    factory = seed_embeddings(real)
    with factory() as db:
        p = db.scalar(select(CardPrinting).where(CardPrinting.external_id == "base1-1", CardPrinting.language == "en"))
        db.add(CardEmbedding(printing_id=p.id, model_name="lab", model_version="2", embedding=[0, 0, 1]))
        db.commit()
        db.add(CardEmbedding(printing_id=p.id, model_name="lab", model_version="2", embedding=[0, 0, 1]))
        with pytest.raises(DBAPIError):
            db.commit()


def test_hnsw_and_ivfflat_indexes_can_be_created_used_and_dropped(real):
    seed_embeddings(real)
    # La columna del esquema no tiene dimensión fija: un índice directo es imposible (hallazgo importante).
    with pytest.raises(DBAPIError, match="dimensions"):
        with real.begin() as c:
            c.execute(text("CREATE INDEX bad_hnsw ON card_embeddings USING hnsw (embedding vector_cosine_ops)"))
    # Solución: índice sobre una expresión con la dimensión del modelo elegido.
    for name, method, opts in (("ix_lab_hnsw", "hnsw", ""), ("ix_lab_ivf", "ivfflat", "WITH (lists = 1)")):
        with real.begin() as c:
            c.execute(
                text(
                    f"CREATE INDEX {name} ON card_embeddings USING {method} ((embedding::vector(3)) vector_cosine_ops) {opts}"
                )
            )
        assert scalar(real, "SELECT count(*) FROM pg_indexes WHERE indexname = :n", n=name) == 1
        with real.begin() as c:
            c.execute(text("SET LOCAL enable_seqscan = off"))
            plan = "\n".join(
                r[0]
                for r in c.execute(
                    text("EXPLAIN SELECT 1 FROM card_embeddings ORDER BY embedding::vector(3) <=> '[1,0,0]' LIMIT 2")
                )
            )
        assert name in plan, plan
        with real.begin() as c:
            c.execute(text(f"DROP INDEX {name}"))
        assert scalar(real, "SELECT count(*) FROM pg_indexes WHERE indexname = :n", n=name) == 0


def test_incompatible_dimensions_are_rejected(real):
    with pytest.raises(DBAPIError, match="different vector dimensions"):
        scalar(real, "SELECT '[1,2]'::vector <-> '[1,2,3]'::vector")
    with real.begin() as c:
        c.execute(text("CREATE TABLE lab_typed (v vector(4))"))
    with pytest.raises(DBAPIError, match="expected 4 dimensions, not 3"):
        with real.begin() as c:
            c.execute(text("INSERT INTO lab_typed VALUES ('[1,2,3]')"))
    with pytest.raises(DBAPIError, match="cannot have more than 2000 dimensions"):
        with real.begin() as c:
            c.execute(text("CREATE TABLE lab_big (v vector(2001))"))
            c.execute(text("CREATE INDEX ON lab_big USING hnsw (v vector_l2_ops)"))
    with real.begin() as c:  # exactamente 2000 sí se puede indexar
        c.execute(text("CREATE TABLE lab_ok (v vector(2000))"))
        c.execute(text("CREATE INDEX ON lab_ok USING hnsw (v vector_l2_ops)"))


def test_rollback_to_base_and_upgrade_again_with_the_real_extension(real):
    migrate(real, "downgrade", "0003")
    migrate(real, "downgrade", "base")
    assert scalar(real, "SELECT count(*) FROM information_schema.tables WHERE table_name = 'card_embeddings'") == 0
    assert (
        scalar(real, "SELECT count(*) FROM pg_extension WHERE extname = 'vector'") == 1
    )  # la extensión no se borra al revertir
    migrate(real, "upgrade", "head")
    assert scalar(real, "SELECT version_num FROM alembic_version") == "0005"


def test_the_extension_exists_only_in_the_test_database(real):
    other = create_engine(URL.rsplit("/", 1)[0] + "/postgres")
    try:
        with other.connect() as c:
            assert c.scalar(text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")) == 0
    finally:
        other.dispose()
