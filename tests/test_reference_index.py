# ruff: noqa: E501
"""E4: índice de referencias con identificadores de Carpetazo. Lógica con SQLite; pgvector REAL solo en el laboratorio privado (VISION_TEST_POSTGRES_URL, puerto 5498).
Datos SINTÉTICOS: ninguna imagen real, ninguna lectura de R2, ningún catálogo real."""

import hashlib
import json
import uuid
from datetime import UTC, datetime

import cv2
import numpy as np
import pytest
from sqlalchemy import create_engine, delete, event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.catalog_mirror.sync import SyncOptions, run_sync
from app.database.mirror_models import MirrorCard, MirrorGame, MirrorSet
from app.database.models import Base
from app.database.reference_models import (
    EMBEDDING_TABLES,
    AmbiguousReference,
    CandidateReference,
    ReferenceImportRun,
    VerifiedReference,
)
from app.dataset_lab.store import Dataset
from app.recognition.embedding.base import EmbeddingSpec
from app.recognition.embedding.dinov2_client import DINOV2_SPEC
from app.recognition.embedding.grid import GRID_SPEC, GridExtractor
from app.recognition.index import cli as index_cli
from app.recognition.index import store
from app.recognition.index.registry import MODELS, TIERS, slot_for
from app.recognition.index.store import (
    ExactIndex,
    ReferenceImportError,
    import_ambiguous,
    import_candidates,
    import_verified_from_dataset,
)
from app.recognition.normalization import NormalizationConfig
from tests.test_catalog_sync import FakeApi, api_card, make_client
from tests.test_postgres import URL, migrate
from tests.test_recognition_lab import fake_card

BASE_PID = 5000


@pytest.fixture
def session():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def seed_mirror(s, n=8, number_of=lambda i: str(i + 1)):
    """Productos SINTÉTICOS en el espejo (categoría 3, edición 10): ids de prueba, nunca catálogo real."""
    if s.get(MirrorGame, 3) is None:
        s.add(MirrorGame(category_id=3, name="Pokémon"))
        s.add(MirrorSet(category_id=3, group_id=10, name="Set de prueba", digest="d" * 64))
        s.flush()
    for i in range(n):
        s.add(MirrorCard(category_id=3, product_id=BASE_PID + i, group_id=10, name=f"Producto de prueba {i}", number=number_of(i), digest="c" * 64))
    s.commit()


def sha_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_ds(tmp_path, n=3, link=True):
    """Dataset con n cartas SINTÉTICAS verificadas, referencia verificada a mano, archivo local con SHA-256 y (opcional) vínculo a un productId de Carpetazo."""
    ds = Dataset(tmp_path / "ds")
    ds.init()
    refs = []
    for i in range(n):
        extra = dict(catalog_category_id=3, catalog_product_id=BASE_PID + i) if link else {}
        c = ds.add_card(name=f"Carta de prueba {i}", set_name="Set de prueba", collector_number=f"{i + 1:03d}/100", language="es", finish="normal", categories=["comun"], pattern="ninguno", reference_object_key=f"prueba/{i}.png", **extra)
        ds.verify_card(c["card_ref"], "tester", "comparada con la carta física (prueba)")
        path = ds.references / f"{c['card_ref']}.png"
        cv2.imwrite(str(path), fake_card(300 + i))
        cards = ds.cards
        cards[c["card_ref"]]["reference_sha256"] = sha_of(path)
        ds.save_cards(cards)
        ds.verify_reference(c["card_ref"], "tester", "ilustración, número y símbolo coinciden (prueba)")
        refs.append(c["card_ref"])
    return ds, refs


def _edit(ds, ref, **ch):
    cards = ds.cards
    cards[ref].update(ch)
    ds.save_cards(cards)


def counts(s):
    return {t: len(s.scalars(select(m)).all()) for t, m in (("verified", VerifiedReference), ("candidate", CandidateReference), ("ambiguous", AmbiguousReference), ("runs", ReferenceImportRun))}


def cand_items(tmp_path, n=2, base=0, **extra):
    items = []
    for i in range(n):
        p = tmp_path / f"cand{base + i}.png"
        cv2.imwrite(str(p), fake_card(700 + base + i))
        items.append({"storage_key": f"r2/carpeta/{base + i}.png", "image_path": str(p), "claimed_category_id": 3, "claimed_product_id": BASE_PID + i, **extra})
    return items


# ---- importación de referencias VERIFICADAS
def test_verified_references_are_linked_to_carpetazo_products_with_traceability_and_embeddings(session, tmp_path):
    seed_mirror(session)
    ds, refs = make_ds(tmp_path)
    rep = import_verified_from_dataset(session, ds, [GridExtractor()], "Jeffry")
    assert rep["created"] == refs and rep["embeddings_added"] == 3 and not rep["errors"] and not rep["skipped"]
    row = session.scalars(select(VerifiedReference).where(VerifiedReference.source_ref == refs[0])).one()
    assert (row.category_id, row.product_id) == (3, BASE_PID) and row.variant == "unknown" and row.variant_key is None  # nunca se inventa una variante
    assert (row.name, row.set_name, row.collector_number, row.pattern) == ("Carta de prueba 0", "Set de prueba", "001/100", "ninguno")
    assert row.verified_by == "tester" and "comparada con la carta física" in row.verification_note and "ilustración" in row.verification_note
    assert row.storage_key == "prueba/0.png" and row.license_status == "unknown" and row.imported_by == "Jeffry" and row.import_run_id
    run = session.scalars(select(ReferenceImportRun)).one()
    assert run.kind == "verified" and run.counts["created"] == 3 and run.source.startswith("dataset:")
    table = EMBEDDING_TABLES[("verified", GRID_SPEC.key)]
    assert len(session.scalars(select(table)).all()) == 3 and len(session.scalars(select(table)).first().embedding) == GRID_SPEC.dim and session.scalars(select(table.normalization)).first() == "raw"


def test_import_is_idempotent(session, tmp_path):
    seed_mirror(session)
    ds, _ = make_ds(tmp_path)
    import_verified_from_dataset(session, ds, [GridExtractor()], "Jeffry")
    again = import_verified_from_dataset(session, ds, [GridExtractor()], "Jeffry")
    assert again["created"] == [] and len(again["already_present"]) == 3 and again["embeddings_added"] == 0 and counts(session)["verified"] == 3


@pytest.mark.parametrize(
    ("what", "mutate", "reason"),
    [
        ("carta sin verificar", lambda ds, ref: _edit(ds, ref, verification_status="unverified"), "carta física sin verificar"),
        ("referencia sin verificación manual", lambda ds, ref: _edit(ds, ref, reference_verified_by=None), "sin verificación manual"),
        ("sin clave de referencia", lambda ds, ref: _edit(ds, ref, reference_object_key=None), "sin verificación manual"),
        ("sin vínculo con Carpetazo", lambda ds, ref: _edit(ds, ref, catalog_category_id=None, catalog_product_id=None), "sin vínculo con un productId"),
        ("archivo local ausente", lambda ds, ref: (ds.references / f"{ref}.png").unlink(), "no hay archivo local"),
        ("sin SHA-256", lambda ds, ref: _edit(ds, ref, reference_sha256=None), "SHA-256 registrado"),
        ("SHA-256 distinto", lambda ds, ref: _edit(ds, ref, reference_sha256="0" * 64), "no coincide con el registrado"),
        ("carta de ejemplo", lambda ds, ref: _edit(ds, ref, name="EJEMPLO Carta ficticia"), "ejemplo"),
        ("producto que no existe en el espejo", lambda ds, ref: _edit(ds, ref, catalog_product_id=999999), "no existe en el espejo"),
        ("número distinto al del producto", lambda ds, ref: _edit(ds, ref, collector_number="077/100"), "no coincide con el del producto"),
    ],
)
def test_only_fully_verified_and_linked_references_become_recognizable_identities(session, tmp_path, what, mutate, reason):
    seed_mirror(session)
    ds, refs = make_ds(tmp_path, n=2)
    mutate(ds, refs[0])
    rep = import_verified_from_dataset(session, ds, [GridExtractor()], "Jeffry")
    assert [s["card_ref"] for s in rep["skipped"]] == [refs[0]] and reason in rep["skipped"][0]["reason"], what
    assert rep["created"] == [refs[1]] and counts(session)["verified"] == 1


def test_a_product_retired_by_carpetazo_cannot_receive_new_verified_references(session, tmp_path):
    seed_mirror(session)
    session.get(MirrorCard, (3, BASE_PID)).retired_at = datetime.now(UTC)
    session.commit()
    ds, refs = make_ds(tmp_path, n=1)
    rep = import_verified_from_dataset(session, ds, [GridExtractor()], "Jeffry")
    assert "retirado" in rep["skipped"][0]["reason"] and counts(session)["verified"] == 0


def test_dry_run_writes_nothing_and_does_not_embed(session, tmp_path):
    seed_mirror(session)
    ds, refs = make_ds(tmp_path)

    class Boom(GridExtractor):
        def embed(self, _):
            raise AssertionError("no debe calcular embeddings en dry-run")

    rep = import_verified_from_dataset(session, ds, [Boom()], "Jeffry", dry_run=True)
    assert rep["would_create"] == refs and rep["dry_run"] and counts(session) == {"verified": 0, "candidate": 0, "ambiguous": 0, "runs": 0}


def test_import_requires_who_and_a_registered_model(session, tmp_path):
    seed_mirror(session)
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


def test_each_embedding_model_goes_to_its_own_fixed_dimension_table_in_every_tier(session, tmp_path):
    seed_mirror(session)
    ds, _ = make_ds(tmp_path, n=2)

    class FakeDino:  # mismo contrato que DINOv2-small, sin torch
        spec = DINOV2_SPEC

        def embed(self, img):
            v = np.random.default_rng(int(img.sum()) % 2**32).normal(size=768).astype(np.float32)
            return v / np.linalg.norm(v)

        def close(self):
            return None

    import_verified_from_dataset(session, ds, [GridExtractor(), FakeDino()], "Jeffry")
    dims = {key: {len(r) for r in session.scalars(select(EMBEDDING_TABLES[("verified", key)].embedding))} for key in (GRID_SPEC.key, DINOV2_SPEC.key)}
    assert dims == {GRID_SPEC.key: {456}, DINOV2_SPEC.key: {768}} and {v.slot for v in MODELS.values()} == {"grid_g1", "dinov2_small_ed25f3a_p1"}
    assert TIERS == ("verified", "candidate", "ambiguous")
    for tier in TIERS:
        for key, slot in MODELS.items():
            assert EMBEDDING_TABLES[(tier, key)].__table__.c.embedding.type.dim == slot.dim  # dimensión FIJA en cada tabla


def test_normalized_and_raw_embeddings_coexist_and_are_loaded_separately(session, tmp_path):
    seed_mirror(session)
    ds, _ = make_ds(tmp_path, n=2)
    cfg = NormalizationConfig()
    import_verified_from_dataset(session, ds, [GridExtractor()], "Jeffry")
    rep = import_verified_from_dataset(session, ds, [GridExtractor()], "Jeffry", normalization=cfg)
    assert rep["embeddings_added"] == 2 and rep["normalization"] == cfg.tag
    raw, norm = ExactIndex.load(session, GRID_SPEC, "verified", "raw"), ExactIndex.load(session, GRID_SPEC, "verified", cfg.tag)
    assert len(raw) == len(norm) == 2 and not np.allclose(raw.matrix, norm.matrix) and len(ExactIndex.load(session, GRID_SPEC, "verified", "otra-etiqueta")) == 0


# ---- candidatas: vínculo afirmado por Carpetazo, sin revisar
def test_candidates_keep_carpetazos_claim_but_never_carry_verification(session, tmp_path):
    seed_mirror(session)
    rep = import_candidates(session, cand_items(tmp_path), [GridExtractor()], "Jeffry", "vínculo imageKey de Carpetazo (sin revisar)")
    assert rep["created"] == 2 and rep["embeddings_added"] == 2
    row = session.scalars(select(CandidateReference).order_by(CandidateReference.storage_key)).first()
    assert (row.claimed_category_id, row.claimed_product_id, row.status, row.link_source) == (3, BASE_PID, "unreviewed", "carpetazo_catalog")
    cols = {c.name for c in CandidateReference.__table__.columns}
    assert not cols & {"category_id", "product_id", "name", "set_name", "collector_number", "verified_by", "verified_at", "verification_note", "variant", "card_id"}
    assert counts(session)["verified"] == 0 and len(session.scalars(select(EMBEDDING_TABLES[("candidate", GRID_SPEC.key)])).all()) == 2
    assert len(session.scalars(select(EMBEDDING_TABLES[("verified", GRID_SPEC.key)])).all()) == 0


@pytest.mark.parametrize("extra", [{"verified_by": "x"}, {"category_id": 3}, {"product_id": 1}, {"name": "Pikachu"}, {"approved": True}, {"identity": "x"}, {"variant": "holo"}])
def test_a_candidate_cannot_carry_identity_verification_or_variant(session, tmp_path, extra):
    seed_mirror(session)
    rep = import_candidates(session, [{**cand_items(tmp_path, 1)[0], **extra}], [GridExtractor()], "Jeffry", "origen")
    assert rep["created"] == 0 and "no puede traer identidad" in rep["rejected"][0]["reason"] and counts(session)["candidate"] == 0


@pytest.mark.parametrize("claim", [{"claimed_category_id": None, "claimed_product_id": None}, {"claimed_category_id": 3, "claimed_product_id": None}, {"claimed_category_id": 3, "claimed_product_id": 424242}])
def test_a_candidate_needs_a_complete_claim_that_exists_in_the_mirror(session, tmp_path, claim):
    seed_mirror(session)
    rep = import_candidates(session, [{**cand_items(tmp_path, 1)[0], **claim}], [GridExtractor()], "Jeffry", "origen")
    assert rep["created"] == 0 and rep["rejected"] and counts(session)["candidate"] == 0


def test_an_image_lives_in_one_tier_only_and_imports_are_idempotent(session, tmp_path):
    seed_mirror(session)
    ds, refs = make_ds(tmp_path, n=1)
    import_verified_from_dataset(session, ds, [GridExtractor()], "Jeffry")
    same = {"storage_key": "r2/x.png", "image_path": str(ds.references / f"{refs[0]}.png"), "claimed_category_id": 3, "claimed_product_id": BASE_PID}
    assert "ya es una referencia VERIFICADA" in import_candidates(session, [same], [GridExtractor()], "J", "o")["rejected"][0]["reason"]
    items = cand_items(tmp_path, 2, base=10)
    import_candidates(session, items, [GridExtractor()], "Jeffry", "origen")
    again = import_candidates(session, items, [GridExtractor()], "Jeffry", "origen")
    assert again["created"] == 0 and again["already_present"] == 2 and counts(session)["candidate"] == 2
    amb = import_ambiguous(session, [{**items[0], "ambiguity_reason": "dos productos parecidos"}], [GridExtractor()], "Jeffry", "origen")
    assert "ya está registrada como candidata" in amb["rejected"][0]["reason"] and counts(session)["ambiguous"] == 0
    dry = import_candidates(session, cand_items(tmp_path, 3, base=20)[2:], [GridExtractor()], "Jeffry", "origen", dry_run=True)
    assert dry["created"] == 1 and counts(session)["candidate"] == 2


# ---- ambiguas
def test_ambiguous_references_need_a_reason_and_never_confirm_anything(session, tmp_path):
    seed_mirror(session)
    items = cand_items(tmp_path, 3)
    ok = {**items[0], "ambiguity_reason": "dos productos con la misma ilustración"}
    no_claim = {"storage_key": "r2/sin-vinculo.png", "image_path": items[1]["image_path"], "ambiguity_reason": "no se sabe a qué producto pertenece"}
    no_reason = {**items[2], "ambiguity_reason": "  "}
    half_claim = {**items[2], "ambiguity_reason": "x", "claimed_product_id": None}
    rep = import_ambiguous(session, [ok, no_claim, no_reason, half_claim], [GridExtractor()], "Jeffry", "auditoría de alias")
    assert rep["created"] == 2 and len(rep["rejected"]) == 2 and counts(session)["ambiguous"] == 2 and counts(session)["verified"] == 0
    assert len(session.scalars(select(EMBEDDING_TABLES[("ambiguous", GRID_SPEC.key)])).all()) == 2
    assert {r.claimed_product_id for r in session.scalars(select(AmbiguousReference))} == {BASE_PID, None}


# ---- separación estructural entre niveles
def test_there_is_no_way_to_promote_between_tiers_and_tiers_share_no_foreign_keys():
    assert not [n for n in dir(store) if any(w in n.lower() for w in ("promote", "verify_candidate", "approve"))]
    meta = Base.metadata.tables
    targets = lambda t: {fk.column.table.name for fk in meta[t].foreign_keys}  # noqa: E731
    assert targets("verified_references") == {"reference_import_runs", "mirror_cards"}
    assert targets("candidate_references") == {"reference_import_runs", "mirror_cards"} and targets("ambiguous_references") == {"reference_import_runs", "mirror_cards"}
    for (tier, _), table in EMBEDDING_TABLES.items():
        assert {fk.column.table.name for fk in table.__table__.foreign_keys} == {f"{tier}_references"}


def test_database_constraints_protect_the_tiers(session):
    seed_mirror(session, 1)
    base = dict(source_kind="pilot_dataset", source_ref="pc-001", image_sha256="a" * 64, name="X", set_name="S", collector_number="1/1", verified_at="2026-11-05", imported_by="t", category_id=3, product_id=BASE_PID)
    for bad in ({"verified_by": "  ", "verification_note": "n"}, {"verified_by": "t", "verification_note": ""}):
        session.add(VerifiedReference(**{**base, **bad}))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
    session.add(VerifiedReference(**{**base, "verified_by": "t", "verification_note": "n", "product_id": 777}))  # producto que no existe en el espejo
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    session.add(CandidateReference(storage_key="k", image_sha256="b" * 64, claimed_category_id=3, claimed_product_id=BASE_PID, status="verified", imported_by="t"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    session.add(AmbiguousReference(storage_key="k", image_sha256="c" * 64, ambiguity_reason=" ", imported_by="t"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    session.add(AmbiguousReference(storage_key="k", image_sha256="d" * 64, ambiguity_reason="x", claimed_category_id=3, imported_by="t"))
    with pytest.raises(IntegrityError):
        session.flush()


# ---- el catálogo cambia, las referencias y los embeddings se quedan
def test_catalog_syncs_never_lose_references_or_embeddings_and_retired_products_stay_linked(session, tmp_path):
    api = FakeApi()
    client, _ = make_client(api)
    run_sync(session, client)  # espejo SINTÉTICO de la API falsa: productos 1001, 1002, 2001 (cat. 3)
    ds = Dataset(tmp_path / "ds")
    ds.init()
    c = ds.add_card(name="Carta de prueba", set_name="Set A", collector_number="1", language="es", finish="normal", categories=["comun"], pattern="ninguno", reference_object_key="prueba/x.png", catalog_category_id=3, catalog_product_id=1002)
    ds.verify_card(c["card_ref"], "tester", "comparada (prueba)")
    path = ds.references / f"{c['card_ref']}.png"
    cv2.imwrite(str(path), fake_card(1))
    _edit(ds, c["card_ref"], reference_sha256=sha_of(path))
    ds.verify_reference(c["card_ref"], "tester", "coincide (prueba)")
    api.cards[(3, 10)][1] = api_card(1002, 10, number="2")
    session.get(MirrorCard, (3, 1002)).number = "2"  # el número de la carta física y el del producto deben coincidir
    session.commit()
    _edit(ds, c["card_ref"], collector_number="002/100")
    assert import_verified_from_dataset(session, ds, [GridExtractor()], "Jeffry")["created"] == [c["card_ref"]]
    ref_id = session.scalars(select(VerifiedReference.id)).one()
    emb_before = [list(e) for e in session.scalars(select(EMBEDDING_TABLES[("verified", GRID_SPEC.key)].embedding))]
    api.cards[(3, 10)] = [api.cards[(3, 10)][0]]  # Carpetazo retira el producto 1002
    api.cards[(3, 11)] = [api_card(2001, 11, name="Nombre corregido")]
    run_sync(session, make_client(api)[0], SyncOptions(force=True))
    assert session.get(MirrorCard, (3, 1002)).retired_at is not None  # retirado, no borrado
    assert session.get(VerifiedReference, ref_id) is not None and [list(e) for e in session.scalars(select(EMBEDDING_TABLES[("verified", GRID_SPEC.key)].embedding))] == emb_before
    with pytest.raises(IntegrityError):  # el espejo no se puede vaciar por debajo de las referencias
        session.execute(delete(MirrorCard).where(MirrorCard.product_id == 1002, MirrorCard.category_id == 3))
        session.flush()
    session.rollback()


# ---- búsqueda exacta
def test_exact_index_returns_ordered_candidates_per_tier_and_refuses_other_models(session, tmp_path):
    seed_mirror(session)
    ds, refs = make_ds(tmp_path, n=4)
    g = GridExtractor()
    import_verified_from_dataset(session, ds, [g], "Jeffry")
    import_candidates(session, cand_items(tmp_path, 2, base=30), [g], "Jeffry", "origen")
    idx = ExactIndex.load(session, GRID_SPEC, "verified", "raw")
    rows = {r.id: r.source_ref for r in session.scalars(select(VerifiedReference))}
    q = g.embed(cv2.resize(fake_card(302), (440, 614), interpolation=cv2.INTER_AREA))
    hits = idx.search(q, k=3)
    assert rows[hits[0].reference_id] == refs[2] and hits[0].similarity > 0.99 and hits[0].tier == "verified" and [h.similarity for h in hits] == sorted((h.similarity for h in hits), reverse=True)
    with pytest.raises(ValueError, match="otro modelo"):
        idx.search(np.ones(768, np.float32), 3)
    cand = ExactIndex.load(session, GRID_SPEC, "candidate", "raw")
    assert len(cand) == 2 and all(h.tier == "candidate" for h in cand.search(q)) and not {h.reference_id for h in cand.search(q)} & set(rows)  # niveles separados
    assert len(ExactIndex.load(session, GRID_SPEC, "ambiguous", "raw")) == 0
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
    eng = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        seed_mirror(s)
    code = index_cli.main(["import-verified", "--database-url", f"sqlite:///{db}", "--dataset", str(ds.root), "--by", "Jeffry", "--dry-run"])
    assert code == 0 and json.loads(capsys.readouterr().out)["would_create"] == refs
    with Session(create_engine(f"sqlite:///{db}")) as s:
        assert counts(s) == {"verified": 0, "candidate": 0, "ambiguous": 0, "runs": 0}
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
def test_migration_0007_creates_three_tiers_with_fixed_dimension_vectors_and_carpetazo_keys(lab):
    with lab.connect() as c:
        for key, slot in MODELS.items():
            for tier in TIERS:
                typmod = c.scalar(text("SELECT atttypmod FROM pg_attribute WHERE attrelid = CAST(:t AS regclass) AND attname = 'embedding'"), {"t": slot.table(tier)})
                assert typmod == slot.dim, (key, tier)
        fks = c.execute(text("SELECT conrelid::regclass::text, confrelid::regclass::text, confdeltype FROM pg_constraint WHERE contype = 'f' AND confrelid = 'mirror_cards'::regclass")).all()
    assert {r[0] for r in fks} == {"verified_references", "candidate_references", "ambiguous_references"} and {r[2] for r in fks} <= {"r", "a"}
    cols = {c["name"] for c in inspect(lab).get_columns("verified_references")}
    assert {"category_id", "product_id", "variant", "variant_key"} <= cols and not cols & {"card_id", "printing_id", "variant_id"}
    migrate(lab, "downgrade", "0006")
    assert "ambiguous_references" not in inspect(lab).get_table_names() and "card_id" in {c["name"] for c in inspect(lab).get_columns("verified_references")}
    migrate(lab, "downgrade", "0004")
    assert "verified_references" not in inspect(lab).get_table_names() and "mirror_cards" not in inspect(lab).get_table_names() and "card_embeddings" in inspect(lab).get_table_names()
    migrate(lab, "upgrade", "head")
    assert "ambiguous_references" in inspect(lab).get_table_names()


@pg
def test_postgres_refuses_a_vector_of_the_wrong_dimension_and_an_unknown_product(lab, tmp_path):
    ds, _ = make_ds(tmp_path, n=1)
    with Session(lab) as s:
        seed_mirror(s, 1)
        import_verified_from_dataset(s, ds, [GridExtractor()], "Jeffry")
        rid = s.scalar(select(VerifiedReference.id))
        table = MODELS[GRID_SPEC.key].table("verified")
        with pytest.raises(Exception, match="dimensions|expected"):
            s.execute(text(f"INSERT INTO {table} (id, reference_id, normalization, embedding) VALUES (:i, :r, 'x', CAST(:e AS vector))"), {"i": uuid.uuid4(), "r": rid, "e": "[1,2,3]"})
        s.rollback()
        s.add(VerifiedReference(source_kind="pilot_dataset", source_ref="x", image_sha256="e" * 64, name="X", set_name="S", collector_number="1", verified_by="t", verified_at="2026", verification_note="n", imported_by="t", category_id=3, product_id=424242))
        with pytest.raises(IntegrityError):
            s.flush()


@pg
def test_pgvector_search_matches_the_exact_search_and_hnsw_works(lab, tmp_path):
    ds, refs = make_ds(tmp_path, n=8)
    g = GridExtractor()
    with Session(lab) as s:
        seed_mirror(s, 8)
        import_verified_from_dataset(s, ds, [g], "Jeffry")
        exact = ExactIndex.load(s, GRID_SPEC, "verified", "raw")
        for i in range(8):
            q = g.embed(cv2.resize(fake_card(300 + i), (440, 614), interpolation=cv2.INTER_AREA))
            a, b = exact.search(q, 5), store.pgvector_search(s, GRID_SPEC, "verified", "raw", q, 5)
            assert [m.reference_id for m in a] == [m.reference_id for m in b] and [round(m.similarity, 4) for m in a] == [round(m.similarity, 4) for m in b]
        name = store.ensure_hnsw(s, GRID_SPEC, "verified")
        assert s.scalar(text("SELECT count(*) FROM pg_indexes WHERE indexname = :n"), {"n": name}) == 1
        assert store.pgvector_search(s, GRID_SPEC, "verified", "raw", q, 3)[0].similarity > 0.99
        with pytest.raises(ValueError, match="otro modelo"):
            store.pgvector_search(s, GRID_SPEC, "verified", "raw", np.ones(768, np.float32), 3)
        assert store.pgvector_search(s, GRID_SPEC, "candidate", "raw", q, 3) == []
