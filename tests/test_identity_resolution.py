# ruff: noqa: E501
"""E6: resolución de identidad contra el espejo del catálogo de Carpetazo. Datos SINTÉTICOS (API falsa); nunca catálogo real."""

import uuid
from datetime import UTC, datetime

import cv2
import numpy as np
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.catalog_mirror.sync import run_sync
from app.database.mirror_models import MirrorCard
from app.database.models import Base
from app.database.reference_models import VerifiedReference
from app.dataset_lab.store import Dataset
from app.recognition.embedding.grid import GRID_SPEC, GridExtractor
from app.recognition.index.store import (
    ExactIndex,
    Match,
    import_ambiguous,
    import_candidates,
    import_verified_from_dataset,
)
from app.recognition.resolve import ResolvedIdentity, resolve_match, resolve_matches
from tests.test_catalog_sync import FakeApi, api_card, make_client
from tests.test_recognition_lab import fake_card
from tests.test_reference_index import _edit, sha_of


def make_session(fk=True):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    if fk:

        @event.listens_for(engine, "connect")
        def _fk(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return Session(engine)


@pytest.fixture
def world(tmp_path):
    """Espejo sincronizado desde la API FALSA + 2 referencias verificadas + 1 candidata + 1 ambigua."""
    s = make_session()
    api = FakeApi()
    api.cards[(3, 10)] = [api_card(1001, 10, number="1", extra=[{"name": "Rarity", "value": "Holo"}]), api_card(1002, 10, number="2")]
    run_sync(s, make_client(api)[0])
    ds = Dataset(tmp_path / "ds")
    ds.init()
    refs = {}
    for i, (pid, num) in enumerate(((1001, "001/100"), (1002, "002/100"))):
        c = ds.add_card(name=f"Carta de prueba {pid}", set_name="Set A", collector_number=num, language="es", finish="holo" if pid == 1001 else "normal", categories=["holo"], pattern="pokeball" if pid == 1001 else "ninguno", reference_object_key=f"prueba/{pid}.png", catalog_category_id=3, catalog_product_id=pid)
        ds.verify_card(c["card_ref"], "tester", "comparada (prueba)")
        path = ds.references / f"{c['card_ref']}.png"
        cv2.imwrite(str(path), fake_card(300 + i))
        _edit(ds, c["card_ref"], reference_sha256=sha_of(path))
        ds.verify_reference(c["card_ref"], "tester", "coincide (prueba)")
        refs[pid] = c["card_ref"]
    g = GridExtractor()
    import_verified_from_dataset(s, ds, [g], "Jeffry")
    cand = tmp_path / "cand.png"
    cv2.imwrite(str(cand), fake_card(700))
    import_candidates(s, [{"storage_key": "r2/c.png", "image_path": str(cand), "claimed_category_id": 3, "claimed_product_id": 2001}], [g], "Jeffry", "vínculo de Carpetazo sin revisar")
    amb = tmp_path / "amb.png"
    cv2.imwrite(str(amb), fake_card(701))
    import_ambiguous(s, [{"storage_key": "r2/a.png", "image_path": str(amb), "claimed_category_id": 3, "claimed_product_id": 1001, "ambiguity_reason": "dos productos con la misma ilustración"}], [g], "Jeffry", "auditoría")
    yield s, ds, api, g
    s.close()


def verified_match(s, pid):
    rid = s.scalars(select(VerifiedReference.id).where(VerifiedReference.product_id == pid)).one()
    return Match(rid, 0.97, "verified")


def test_a_verified_reference_resolves_to_the_real_carpetazo_product_with_its_edition(world):
    s, *_ = world
    r = resolve_match(s, verified_match(s, 1001))
    assert (r.status, r.confirmable, r.tier) == ("resolved", True, "verified")
    assert (r.category_id, r.product_id, r.name, r.number, r.rarity) == (3, 1001, "Carta de prueba 1001", "1", "Common")
    assert r.set == {"group_id": 10, "name": "Set A", "abbreviation": "SA"} and r.catalog["retired"] is False and r.catalog_relation == "catalog_product"
    assert r.similarity == 0.97 and any("vigente" in x for x in r.reasons)


def test_variant_is_always_unknown_and_reference_hints_are_not_a_claim(world):
    s, *_ = world
    r = resolve_match(s, verified_match(s, 1001))
    assert r.variant == "unknown" and r.variant_evidence is None
    assert r.reference_hints["pattern"] == "pokeball" and r.reference_hints["finish"] == "holo" and "no se afirma" in r.reference_hints["note"]
    assert r.set["name"] == "Set A" and r.set["name"] != r.variant  # la edición no se confunde con la variante


def test_candidates_and_ambiguous_are_never_confirmable_even_with_perfect_similarity(world):
    s, *_ = world
    from app.database.reference_models import AmbiguousReference, CandidateReference

    cand = s.scalars(select(CandidateReference)).one()
    amb = s.scalars(select(AmbiguousReference)).one()
    rc, ra = resolve_match(s, Match(cand.id, 1.0, "candidate")), resolve_match(s, Match(amb.id, 1.0, "ambiguous"))
    assert (rc.status, rc.confirmable) == ("unverified_candidate", False) and (rc.category_id, rc.product_id, rc.name) == (3, 2001, "Carta de prueba 2001")  # se informa lo que AFIRMA Carpetazo, marcado como no verificado
    assert (ra.status, ra.confirmable) == ("ambiguous", False) and ra.product_id == 1001 and "misma ilustración" in " ".join(ra.reasons) and ra.reference_hints is None
    assert rc.variant == ra.variant == "unknown"


@pytest.mark.parametrize("tier", ["candidate", "ambiguous"])
def test_no_unverified_tier_can_ever_produce_a_confirmable_identity(world, tier):
    s, *_ = world
    from app.database.reference_models import AmbiguousReference, CandidateReference

    model = CandidateReference if tier == "candidate" else AmbiguousReference
    ref = s.scalars(select(model)).one()
    for sim in (0.0, 0.5, 0.99, 1.0):
        assert resolve_match(s, Match(ref.id, sim, tier)).confirmable is False


def test_a_product_retired_by_carpetazo_is_incomplete_and_not_confirmable(world):
    s, _, api, _ = world
    s.get(MirrorCard, (3, 1002)).retired_at = datetime.now(UTC)
    s.commit()
    r = resolve_match(s, verified_match(s, 1002))
    assert (r.status, r.confirmable, r.product_id) == ("incomplete", False, 1002) and r.catalog["retired"] is True and any("retirado" in x for x in r.reasons)


def test_a_reference_without_a_mirrored_product_is_an_incomplete_identification_not_an_invented_one():
    s = make_session(fk=False)  # sin FK para simular un espejo al que le falta el producto
    rid = uuid.uuid4()
    s.add(VerifiedReference(id=rid, source_kind="pilot_dataset", source_ref="x", image_sha256="a" * 64, name="X", set_name="S", collector_number="1", verified_by="t", verified_at="2026", verification_note="n", imported_by="t", category_id=3, product_id=999))
    s.commit()
    r = resolve_match(s, Match(rid, 0.9, "verified"))
    assert (r.status, r.confirmable, r.name, r.set) == ("incomplete", False, None, None) and r.product_id == 999 and "no está en el espejo" in r.reasons[0]
    gone = resolve_match(s, Match(uuid.uuid4(), 0.9, "verified"))
    assert gone.status == "incomplete" and "ya no existe" in gone.reasons[0]


def test_no_matches_is_unknown_and_unknown_tiers_are_refused(world):
    s, *_ = world
    r = resolve_matches(s, [])
    assert len(r) == 1 and r[0].status == "unknown" and r[0].confirmable is False and r[0].product_id is None and r[0].similarity is None
    with pytest.raises(ValueError, match="nivel desconocido"):
        resolve_match(s, Match(uuid.uuid4(), 0.5, "otro"))


def test_spanish_catalog_rows_keep_their_relation_and_language(world):
    s, ds, api, g = world
    es = s.get(MirrorCard, (100, 900001))
    assert es.relation == "spanish_only" and es.language == "es" and es.group_id == 9000001
    rid = uuid.uuid4()
    s.add(VerifiedReference(id=rid, source_kind="pilot_dataset", source_ref="es", image_sha256="b" * 64, name="Solo español", set_name="S", collector_number="1", verified_by="t", verified_at="2026", verification_note="n", imported_by="t", category_id=100, product_id=900001))
    s.commit()
    r = resolve_match(s, Match(rid, 0.9, "verified"))
    assert (r.category_id, r.product_id, r.catalog_relation, r.language) == (100, 900001, "spanish_only", "es") and r.set["group_id"] == 9000001
    view = s.get(MirrorCard, (100, 1001))
    assert view.relation == "spanish_view_of_english_product" and view.source_category_id == 3  # misma productId que en la categoría 3, vista en español


def test_resolution_keeps_order_deduplicates_and_never_writes(world):
    s, *_ = world
    a, b = verified_match(s, 1001), verified_match(s, 1002)
    before = (s.scalars(select(VerifiedReference)).all(), len(s.new), len(s.dirty), len(s.deleted))
    out = resolve_matches(s, [a, b, Match(a.reference_id, 0.5, "verified")])
    assert [r.product_id for r in out] == [1001, 1002] and out[0].similarity == 0.97 and isinstance(out[0], ResolvedIdentity)
    assert (len(s.new), len(s.dirty), len(s.deleted)) == before[1:]


def test_end_to_end_with_synthetic_data_search_then_resolve_returns_carpetazo_ids(world):
    s, ds, api, g = world
    idx = ExactIndex.load(s, GRID_SPEC, "verified", "raw")
    q = g.embed(cv2.resize(fake_card(301), (440, 614), interpolation=cv2.INTER_AREA))  # la referencia sintética del producto 1002
    out = resolve_matches(s, idx.search(q, 2))
    assert out[0].product_id == 1002 and out[0].category_id == 3 and out[0].confirmable and out[0].set["name"] == "Set A" and out[0].variant == "unknown"
    assert out[0].similarity > out[1].similarity and out[1].product_id == 1001
    assert np.isfinite(out[0].similarity)  # E5/E7 decidirán si esto se acepta; E6 solo resuelve la identidad
