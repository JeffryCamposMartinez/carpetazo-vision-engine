# ruff: noqa: E501
"""Espejo local del catálogo de Carpetazo (esquema). Datos SINTÉTICOS de prueba: nunca son catálogo real."""

import pytest
from sqlalchemy import create_engine, delete, event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database.mirror_models import MirrorCard, MirrorChange, MirrorGame, MirrorSet, MirrorSyncRun
from app.database.models import Base
from tests.test_postgres import URL, migrate

MIRROR_TABLES = {"mirror_sync_runs", "mirror_games", "mirror_sets", "mirror_cards", "mirror_changes"}


@pytest.fixture
def session():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def seed(s):
    s.add_all([MirrorGame(category_id=3, name="Pokémon"), MirrorGame(category_id=100, name="Pokémon (Español)")])
    s.add_all([MirrorSet(category_id=3, group_id=10, name="Set de prueba", digest="d" * 64), MirrorSet(category_id=100, group_id=10, name="Set de prueba", digest="d" * 64)])
    s.flush()


def card(cat, pid, group=10, **kw):
    return MirrorCard(category_id=cat, product_id=pid, group_id=group, name=f"Carta de prueba {pid}", digest="c" * 64, **kw)


def test_all_mirror_tables_exist_and_keep_carpetazo_identifiers_as_keys(session):
    assert MIRROR_TABLES <= set(inspect(session.bind).get_table_names())
    pk = lambda t: [c.name for c in Base.metadata.tables[t].primary_key.columns]  # noqa: E731
    assert pk("mirror_games") == ["category_id"] and pk("mirror_sets") == ["category_id", "group_id"] and pk("mirror_cards") == ["category_id", "product_id"]


def test_same_product_id_can_exist_in_category_3_and_in_the_spanish_category_100(session):
    seed(session)
    session.add_all([card(3, 555), card(100, 555, relation="spanish_view_of_english_product")])
    session.commit()
    assert len(session.scalars(select(MirrorCard).where(MirrorCard.product_id == 555)).all()) == 2
    session.add(card(3, 555))
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_card_must_belong_to_a_mirrored_set_and_game(session):
    seed(session)
    session.add(card(3, 1, group=999))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    session.add(card(7, 1))
    with pytest.raises(IntegrityError):
        session.flush()


def test_the_mirror_never_cascades_deletes(session):
    seed(session)
    session.add(card(3, 1))
    session.commit()
    for model, where in ((MirrorSet, (MirrorSet.group_id == 10, MirrorSet.category_id == 3)), (MirrorGame, (MirrorGame.category_id == 3,))):
        with pytest.raises(IntegrityError):
            session.execute(delete(model).where(*where))
            session.flush()
        session.rollback()
    assert session.get(MirrorCard, (3, 1)) is not None
    for fk in (fk for t in MIRROR_TABLES for fk in Base.metadata.tables[t].foreign_keys):
        assert (fk.ondelete or "").upper() in ("RESTRICT", "")  # ningún CASCADE: retirar es marcar `retired_at`, no borrar


def test_retirement_is_a_flag_and_changes_are_logged_with_before_and_after(session):
    seed(session)
    run = MirrorSyncRun(source_base_url="https://ejemplo.invalid/api", scope={"categories": [3]})
    session.add_all([run, card(3, 1)])
    session.flush()
    session.add(MirrorChange(run_id=run.id, entity="card", category_id=3, group_id=10, product_id=1, kind="retired", before={"name": "x"}, after=None))
    c = session.get(MirrorCard, (3, 1))
    c.retired_at = c.last_seen_at
    session.commit()
    assert session.get(MirrorCard, (3, 1)).retired_at is not None and session.scalars(select(MirrorChange)).one().kind == "retired"
    assert {col.name for col in MirrorCard.__table__.columns} >= {"digest", "first_seen_at", "last_seen_at", "last_changed_at", "retired_at", "last_run_id", "image_url", "image_origin", "tcgplayer_product_id", "relation", "extended_data"}


def test_no_image_bytes_are_stored_only_traceability_fields():
    cols = {c.name for c in MirrorCard.__table__.columns}
    assert not {c for c in cols if "blob" in c or c.endswith("_bytes") or c == "image_data"} and {"image_url", "image_key"} <= cols


pg = pytest.mark.skipif(not URL, reason="define VISION_TEST_POSTGRES_URL apuntando al laboratorio (127.0.0.1:5498, base *_test)")


@pg
def test_migration_0006_creates_the_mirror_in_the_lab_and_downgrades_cleanly():
    from app.recognition.index import cli as index_cli

    index_cli.assert_lab_database(URL)
    eng = create_engine(URL)
    assert (eng.url.database or "").endswith("_test")
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    migrate(eng, "upgrade", "head")
    assert MIRROR_TABLES <= set(inspect(eng).get_table_names())
    with eng.connect() as c:
        confdeltype = c.execute(text("SELECT DISTINCT confdeltype FROM pg_constraint WHERE contype = 'f' AND conrelid IN ('mirror_cards'::regclass, 'mirror_sets'::regclass)")).scalars().all()
    assert set(confdeltype) <= {"r", "a"}  # restrict / no action
    migrate(eng, "downgrade", "0005")
    assert not (MIRROR_TABLES & set(inspect(eng).get_table_names()))
    migrate(eng, "upgrade", "head")
    eng.dispose()
