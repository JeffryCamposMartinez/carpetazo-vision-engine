# ruff: noqa: E501
"""E4: índice de referencias. Lógica con SQLite; pgvector REAL solo en el laboratorio privado (VISION_TEST_POSTGRES_URL, puerto 5498).
Datos SINTÉTICOS: ninguna imagen real, ninguna lectura de R2."""

import hashlib
import json
import uuid

import cv2
import numpy as np
import pytest
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database.models import Base
from app.database.reference_models import EMBEDDING_TABLES, CandidateReference, ReferenceImportRun, VerifiedReference
from app.dataset_lab.store import Dataset
from app.recognition.embedding.base import EmbeddingSpec
from app.recognition.embedding.dinov2_client import DINOV2_SPEC
from app.recognition.embedding.grid import GRID_SPEC, GridExtractor
from app.recognition.index import cli as index_cli
from app.recognition.index import store
from app.recognition.index.registry import MODELS, slot_for
from app.recognition.index.store import (
    ExactIndex,
    ReferenceImportError,
    import_candidates,
    import_verified_from_dataset,
)
from app.recognition.normalization import NormalizationConfig
from tests.test_postgres import URL, migrate
from tests.test_recognition_lab import fake_card


@pytest.fixture
def session():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def sha_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_ds(tmp_path, n=3, **overrides):
    """Dataset con n cartas SINTÉTICAS verificadas, con referencia verificada a mano y archivo local con SHA-256."""
    ds = Dataset(tmp_path / "ds")
    ds.init()
    refs = []
    for i in range(n):
        c = ds.add_card(name=f"Carta de prueba {i}", set_name="Set de prueba", collector_number=f"{i + 1:03d}/100", language="es", finish="normal", categories=["comun"], pattern="ninguno", reference_object_key=f"prueba/{i}.png")
        ds.verify_card(c["card_ref"], "tester", "comparada con la carta física (prueba)")
        path = ds.references / f"{c['card_ref']}.png"
        cv2.imwrite(str(path), fake_card(300 + i))
        cards = ds.cards
        cards[c["card_ref"]]["reference_sha256"] = sha_of(path)
        ds.save_cards(cards)
        ds.verify_reference(c["card_ref"], "tester", "ilustración, número y símbolo coinciden (prueba)")
        refs.append(c["card_ref"])
    return ds, refs


def counts(s):
    return {"verified": len(s.scalars(select(VerifiedReference)).all()), "candidate": len(s.scalars(select(CandidateReference)).all()), "runs": len(s.scalars(select(ReferenceImportRun)).all())}


# ---- importación de referencias VERIFICADAS
def test_verified_references_are_imported_with_identity_traceability_and_embeddings(session, tmp_path):
    ds, refs = make_ds(tmp_path)
    rep = import_verified_from_dataset(session, ds, [GridExtractor()], "Jeffry")
    assert rep["created"] == refs and rep["embeddings_added"] == 3 and not rep["errors"] and not rep["skipped"]
    row = session.scalars(select(VerifiedReference).where(VerifiedReference.source_ref == refs[0])).one()
    assert (row.name, row.set_name, row.collector_number, row.pattern) == ("Carta de prueba 0", "Set de prueba", "001/100", "ninguno")
    assert row.verified_by == "tester" and "comparada con la carta física" in row.verification_note and "ilustración" in row.verification_note
    assert row.card_id is None and row.printing_id is None and row.variant_id is None  # nunca se inventan ids
    assert row.storage_key == "prueba/0.png" and row.license_status == "unknown" and row.imported_by == "Jeffry" and row.import_run_id
    run = session.scalars(select(ReferenceImportRun)).one()
    assert run.kind == "verified" and run.counts["created"] == 3 and run.source.startswith("dataset:")
    table = EMBEDDING_TABLES[("verified", GRID_SPEC.key)]
    assert len(session.scalars(select(table)).all()) == 3 and len(session.scalars(select(table)).first().embedding) == GRID_SPEC.dim
    assert session.scalars(select(table.normalization)).first() == "raw"


def test_import_is_idempotent(session, tmp_path):
    ds, _ = make_ds(tmp_path)
    import_verified_from_dataset(session, ds, [GridExtractor()], "Jeffry")
    again = import_verified_from_dataset(session, ds, [GridExtractor()], "Jeffry")
    assert again["created"] == [] and len(again["already_present"]) == 3 and again["embeddings_added"] == 0
    assert counts(session)["verified"] == 3


@pytest.mark.parametrize(
    ("what", "mutate", "reason"),
    [
        ("carta sin verificar", lambda ds, ref: _edit(ds, ref, verification_status="unverified"), "carta física sin verificar"),
        ("referencia sin verificación manual", lambda ds, ref: _edit(ds, ref, reference_verified_by=None), "sin verificación manual"),
        ("sin clave de referencia", lambda ds, ref: _edit(ds, ref, reference_object_key=None), "sin verificación manual"),
        ("archivo local ausente", lambda ds, ref: (ds.references / f"{ref}.png").unlink(), "no hay archivo local"),
        ("sin SHA-256", lambda ds, ref: _edit(ds, ref, reference_sha256=None), "SHA-256 registrado"),
        ("SHA-256 distinto", lambda ds, ref: _edit(ds, ref, reference_sha256="0" * 64), "no coincide"),
        ("carta de ejemplo", lambda ds, ref: _edit(ds, ref, name="EJEMPLO Carta ficticia"), "ejemplo"),
    ],
)
def test_only_fully_verified_references_become_recognizable_identities(session, tmp_path, what, mutate, reason):
    ds, refs = make_ds(tmp_path, n=2)
    mutate(ds, refs[0])
    rep = import_verified_from_dataset(session, ds, [GridExtractor()], "Jeffry")
    assert [s["card_ref"] for s in rep["skipped"]] == [refs[0]] and reason in rep["skipped"][0]["reason"], what
    assert rep["created"] == [refs[1]] and counts(session)["verified"] == 1


def _edit(ds, ref, **ch):
    cards = ds.cards
    cards[ref].update(ch)
    ds.save_cards(cards)


def test_dry_run_writes_nothing_and_does_not_embed(session, tmp_path):
    ds, refs = make_ds(tmp_path)

    class Boom(GridExtractor):
        def embed(self, _):
            raise AssertionError("no debe calcular embeddings en dry-run")

    rep = import_verified_from_dataset(session, ds, [Boom()], "Jeffry", dry_run=True)
    assert rep["would_create"] == refs and rep["dry_run"] and counts(session) == {"verified": 0, "candidate": 0, "runs": 0}


def test_import_requires_who_and_a_registered_model(session, tmp_path):
    ds, _ = make_ds(tmp_path, n=1)
    with pytest.raises(ReferenceImportError, match="--by"):
        import_verified_from_dataset(session, ds, [GridExtractor()], "  ")

    class Unknown:
        spec = EmbeddingSpec("otro", "v1", 8)

        def embed(self, _):
            return np.ones(8, np.float32)

        def close(self):
            return None

    with pytest.raises(ValueError, match="no tiene tablas"):
        import_verified_from_dataset(session, ds, [Unknown()], "Jeffry")
    assert counts(session)["verified"] == 0


def test_catalog_ids_must_exist_in_the_catalog(session, tmp_path):
    ds, refs = make_ds(tmp_path, n=2)
    _edit(ds, refs[0], card_id=str(uuid.uuid4()))  # UUID con formato válido pero inexistente en el catálogo
    rep = import_verified_from_dataset(session, ds, [GridExtractor()], "Jeffry")
    assert len(rep["errors"]) == 1 and "no existe en el catálogo" in rep["errors"][0]["error"] and rep["created"] == [refs[1]]


def test_each_embedding_model_goes_to_its_own_fixed_dimension_table(session, tmp_path):
    ds, _ = make_ds(tmp_path, n=2)

    class FakeDino:  # mismo contrato que DINOv2-small, sin torch
        spec = DINOV2_SPEC

        def embed(self, img):
            v = np.random.default_rng(int(img.sum()) % 2**32).normal(size=768).astype(np.float32)
            return v / np.linalg.norm(v)

        def close(self):
            return None

    import_verified_from_dataset(session, ds, [GridExtractor(), FakeDino()], "Jeffry")
    dims = {}
    for key in (GRID_SPEC.key, DINOV2_SPEC.key):
        t = EMBEDDING_TABLES[("verified", key)]
        dims[key] = {len(r) for r in session.scalars(select(t.embedding))}
    assert dims == {GRID_SPEC.key: {456}, DINOV2_SPEC.key: {768}}
    assert {v.slot for v in MODELS.values()} == {"grid_g1", "dinov2_small_ed25f3a_p1"}
    for key, slot in MODELS.items():
        assert EMBEDDING_TABLES[("verified", key)].__table__.c.embedding.type.dim == slot.dim  # dimensión FIJA en la tabla


def test_normalized_and_raw_embeddings_coexist_and_are_loaded_separately(session, tmp_path):
    ds, _ = make_ds(tmp_path, n=2)
    cfg = NormalizationConfig()
    import_verified_from_dataset(session, ds, [GridExtractor()], "Jeffry")
    rep = import_verified_from_dataset(session, ds, [GridExtractor()], "Jeffry", normalization=cfg)
    assert rep["embeddings_added"] == 2 and rep["normalization"] == cfg.tag
    raw, norm = ExactIndex.load(session, GRID_SPEC, "verified", "raw"), ExactIndex.load(session, GRID_SPEC, "verified", cfg.tag)
    assert len(raw) == len(norm) == 2 and not np.allclose(raw.matrix, norm.matrix)
    assert len(ExactIndex.load(session, GRID_SPEC, "verified", "otra-etiqueta")) == 0


# ---- candidatas (probables/ambiguas): separadas y sin identidad
def candidate_items(tmp_path, n=2, status="probable"):
    items = []
    for i in range(n):
        p = tmp_path / f"cand{i}.png"
        cv2.imwrite(str(p), fake_card(700 + i))
        items.append({"storage_key": f"r2/carpeta/{i}.png", "image_path": str(p), "claimed_label": f"Carpeta {i}", "proposal_status": status})
    return items


def test_candidates_are_stored_without_identity_in_their_own_tables(session, tmp_path):
    rep = import_candidates(session, candidate_items(tmp_path, status="ambiguous"), [GridExtractor()], "Jeffry", "manifiesto-r2 (propuesta)")
    assert rep["created"] == 2 and rep["embeddings_added"] == 2
    cols = {c.name for c in CandidateReference.__table__.columns}
    assert not cols & {"card_id", "printing_id", "variant_id", "name", "set_name", "collector_number", "verified_by", "verified_at", "verification_note"}
    assert counts(session)["verified"] == 0 and len(session.scalars(select(EMBEDDING_TABLES[("candidate", GRID_SPEC.key)])).all()) == 2
    assert len(session.scalars(select(EMBEDDING_TABLES[("verified", GRID_SPEC.key)])).all()) == 0
    assert session.scalars(select(CandidateReference)).first().claimed_label.startswith("Carpeta")  # texto del origen, no verdad


@pytest.mark.parametrize("extra", [{"verified_by": "x"}, {"card_id": str(uuid.uuid4())}, {"name": "Pikachu"}, {"approved": True}, {"identity": "x"}])
def test_a_candidate_cannot_carry_identity_or_verification(session, tmp_path, extra):
    items = [{**candidate_items(tmp_path, 1)[0], **extra}]
    rep = import_candidates(session, items, [GridExtractor()], "Jeffry", "origen")
    assert rep["created"] == 0 and "no puede traer identidad" in rep["rejected"][0]["reason"] and counts(session)["candidate"] == 0


@pytest.mark.parametrize("status", ["matched", "verified", "confirmed", "recommended", None])
def test_candidate_status_must_be_probable_or_ambiguous(session, tmp_path, status):
    rep = import_candidates(session, [{**candidate_items(tmp_path, 1)[0], "proposal_status": status}], [GridExtractor()], "Jeffry", "origen")
    assert rep["created"] == 0 and rep["rejected"] and counts(session)["candidate"] == 0


def test_an_image_already_verified_is_not_duplicated_as_candidate_and_import_is_idempotent(session, tmp_path):
    ds, refs = make_ds(tmp_path, n=1)
    import_verified_from_dataset(session, ds, [GridExtractor()], "Jeffry")
    same = {"storage_key": "r2/x.png", "image_path": str(ds.references / f"{refs[0]}.png"), "proposal_status": "probable"}
    rep = import_candidates(session, [same], [GridExtractor()], "Jeffry", "origen")
    assert "ya es una referencia VERIFICADA" in rep["rejected"][0]["reason"] and counts(session)["candidate"] == 0
    items = candidate_items(tmp_path, 2)
    import_candidates(session, items, [GridExtractor()], "Jeffry", "origen")
    again = import_candidates(session, items, [GridExtractor()], "Jeffry", "origen")
    assert again["created"] == 0 and again["already_present"] == 2 and counts(session)["candidate"] == 2
    dry = import_candidates(session, candidate_items(tmp_path, 3)[2:], [GridExtractor()], "Jeffry", "origen", dry_run=True)
    assert dry["created"] == 1 and counts(session)["candidate"] == 2


def test_there_is_no_way_to_promote_a_candidate_to_verified():
    assert not [n for n in dir(store) if "promote" in n.lower() or "verify_candidate" in n.lower()]
    meta = Base.metadata.tables
    cand_fk_targets = {fk.column.table.name for fk in meta["candidate_references"].foreign_keys}
    assert cand_fk_targets == {"reference_import_runs"}  # sin vínculo con las verificadas ni con el catálogo
    for (tier, _), table in EMBEDDING_TABLES.items():
        parents = {fk.column.table.name for fk in table.__table__.foreign_keys}
        assert parents == ({"verified_references"} if tier == "verified" else {"candidate_references"})


def test_database_constraints_reject_unverified_identities_and_bad_statuses(session):
    base = dict(source_kind="pilot_dataset", source_ref="pc-001", image_sha256="a" * 64, name="X", set_name="S", collector_number="1/1", verified_at="2026-11-05", imported_by="t")
    for bad in ({"verified_by": "  ", "verification_note": "n"}, {"verified_by": "t", "verification_note": ""}):
        session.add(VerifiedReference(**base, **bad))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
    session.add(CandidateReference(storage_key="k", image_sha256="b" * 64, proposal_status="verified", proposal_source="x", imported_by="t"))
    with pytest.raises(IntegrityError):
        session.flush()


# ---- búsqueda exacta
def test_exact_index_returns_ordered_candidates_and_refuses_other_models(session, tmp_path):
    ds, refs = make_ds(tmp_path, n=4)
    g = GridExtractor()
    import_verified_from_dataset(session, ds, [g], "Jeffry")
    idx = ExactIndex.load(session, GRID_SPEC, "verified", "raw")
    rows = {r.id: r.source_ref for r in session.scalars(select(VerifiedReference))}
    q = g.embed(cv2.resize(fake_card(302), (440, 614), interpolation=cv2.INTER_AREA))
    hits = idx.search(q, k=3)
    assert rows[hits[0].reference_id] == refs[2] and hits[0].similarity > 0.99 and [h.similarity for h in hits] == sorted((h.similarity for h in hits), reverse=True)
    with pytest.raises(ValueError, match="otro modelo"):
        idx.search(np.ones(768, np.float32), 3)
    assert ExactIndex.load(session, GRID_SPEC, "candidate", "raw").search(q) == []  # niveles separados: las candidatas no aparecen aquí
    with pytest.raises(ValueError, match="no tiene tablas"):
        slot_for(EmbeddingSpec("grid", "g2", 456))


# ---- guardia del laboratorio y CLI
@pytest.mark.parametrize("url", ["postgresql+psycopg://u@127.0.0.1:5432/vision_dev", "postgresql+psycopg://u@127.0.0.1/vision", "postgresql+psycopg://u@db.ejemplo.com:5498/x", "postgresql+psycopg://u@192.168.1.15:5498/x", "postgresql+psycopg://u@localhost:5433/x"])
def test_only_the_private_lab_database_is_allowed(url):
    with pytest.raises(index_cli.DatabaseNotAllowed):
        index_cli.assert_lab_database(url)
    assert index_cli.main(["stats", "--database-url", url]) == 2


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///x.db", "postgresql+psycopg://vision@127.0.0.1:5498/vision_ml_lab", "postgresql+psycopg://vision@localhost:5498/x"])
def test_lab_and_sqlite_urls_are_allowed(url):
    index_cli.assert_lab_database(url)


def test_cli_requires_an_explicit_database_and_runs_a_dry_run(tmp_path, capsys):
    with pytest.raises(SystemExit):
        index_cli.main(["stats"])  # sin valor por defecto
    ds, refs = make_ds(tmp_path, n=2)
    db = tmp_path / "lab.db"
    Base.metadata.create_all(create_engine(f"sqlite:///{db}"))
    code = index_cli.main(["import-verified", "--database-url", f"sqlite:///{db}", "--dataset", str(ds.root), "--by", "Jeffry", "--dry-run"])
    assert code == 0 and json.loads(capsys.readouterr().out)["would_create"] == refs
    with Session(create_engine(f"sqlite:///{db}")) as s:
        assert counts(s) == {"verified": 0, "candidate": 0, "runs": 0}
    assert index_cli.main(["import-verified", "--database-url", f"sqlite:///{db}", "--dataset", str(ds.root), "--by", "Jeffry"]) == 0
    capsys.readouterr()
    assert index_cli.main(["stats", "--database-url", f"sqlite:///{db}"]) == 0
    assert json.loads(capsys.readouterr().out)["verified_references"] == 2


# ---- pgvector REAL (solo laboratorio privado)
pg = pytest.mark.skipif(not URL, reason="define VISION_TEST_POSTGRES_URL apuntando al laboratorio (127.0.0.1:5498, base *_test)")


@pytest.fixture
def lab():
    index_cli.assert_lab_database(URL)  # estas pruebas jamás corren contra otro servidor
    eng = create_engine(URL)
    assert (eng.url.database or "").endswith("_test")
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    migrate(eng, "upgrade", "head")
    yield eng
    eng.dispose()


@pg
def test_migration_0005_creates_fixed_dimension_vector_tables_and_downgrades_cleanly(lab):
    with lab.connect() as c:
        for key, slot in MODELS.items():
            for tier in ("verified", "candidate"):
                typmod = c.scalar(text("SELECT atttypmod FROM pg_attribute WHERE attrelid = CAST(:t AS regclass) AND attname = 'embedding'"), {"t": slot.table(tier)})
                assert typmod == slot.dim, (key, tier)
    migrate(lab, "downgrade", "0004")
    assert "verified_references" not in inspect(lab).get_table_names() and "card_embeddings" in inspect(lab).get_table_names()
    migrate(lab, "upgrade", "head")
    assert "verified_references" in inspect(lab).get_table_names()


@pg
def test_postgres_refuses_a_vector_of_the_wrong_dimension(lab, tmp_path):
    ds, _ = make_ds(tmp_path, n=1)
    with Session(lab) as s:
        import_verified_from_dataset(s, ds, [GridExtractor()], "Jeffry")
        rid = s.scalar(select(VerifiedReference.id))
        table = MODELS[GRID_SPEC.key].table("verified")
        with pytest.raises(Exception, match="dimensions|expected"):
            s.execute(text(f"INSERT INTO {table} (id, reference_id, normalization, embedding) VALUES (:i, :r, 'x', CAST(:e AS vector))"), {"i": uuid.uuid4(), "r": rid, "e": "[1,2,3]"})
        s.rollback()


@pg
def test_pgvector_search_matches_the_exact_search_and_hnsw_works(lab, tmp_path):
    ds, refs = make_ds(tmp_path, n=8)
    g = GridExtractor()
    with Session(lab) as s:
        import_verified_from_dataset(s, ds, [g], "Jeffry")
        exact = ExactIndex.load(s, GRID_SPEC, "verified", "raw")
        for i in range(8):
            q = g.embed(cv2.resize(fake_card(300 + i), (440, 614), interpolation=cv2.INTER_AREA))
            a, b = exact.search(q, 5), store.pgvector_search(s, GRID_SPEC, "verified", "raw", q, 5)
            assert [m.reference_id for m in a] == [m.reference_id for m in b]
            assert [round(m.similarity, 4) for m in a] == [round(m.similarity, 4) for m in b]
        name = store.ensure_hnsw(s, GRID_SPEC, "verified")
        assert s.scalar(text("SELECT count(*) FROM pg_indexes WHERE indexname = :n"), {"n": name}) == 1
        assert store.pgvector_search(s, GRID_SPEC, "verified", "raw", q, 3)[0].similarity > 0.99
        with pytest.raises(ValueError, match="otro modelo"):
            store.pgvector_search(s, GRID_SPEC, "verified", "raw", np.ones(768, np.float32), 3)
        assert store.pgvector_search(s, GRID_SPEC, "candidate", "raw", q, 3) == []
