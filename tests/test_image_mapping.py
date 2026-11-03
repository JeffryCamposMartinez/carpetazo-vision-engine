import uuid

import httpx
import pytest

from app.catalog.providers.tcgdex import ProviderError, TcgdexClient
from app.core.config import Settings
from app.database.models import Card, CardPrinting, CardSet, Game, ImageAssociation
from app.images.apply import apply_manifest, revert_associations
from app.images.inventory import (
    DEFAULT_PREFIX,
    NumberRules,
    build_inventory,
    normalize_name,
    normalize_number,
    parse_card_folder,
)
from app.images.matching import match_inventory, read_manifest_csv, summarize, write_manifest
from app.images.sets import resolve_sets
from app.storage.base import ObjectInfo

P = DEFAULT_PREFIX + "Pokemon_Images/"


def obj(set_folder, card_folder):
    return ObjectInfo(f"{P}{set_folder}/{card_folder}/{card_folder}.webp", 10, "etag")


def catalog():
    # FIXTURE sintética: no son datos reales de TCGdex.
    return [
        {"id": "base1", "name": "Base Set", "cards": [
            {"id": "base1-58", "local_id": "58", "name": "Pikachu"},
            {"id": "base1-4", "local_id": "4", "name": "Charizard"},
        ]},
        {"id": "base2", "name": "Jungle", "cards": [{"id": "base2-60", "local_id": "60", "name": "Pikachu"}]},
        {"id": "swsh1", "name": "Sword & Shield", "cards": [
            {"id": "swsh1-001", "local_id": "001", "name": "Celebi V"},
            {"id": "swsh1-002", "local_id": "002", "name": "Dup"},
            {"id": "swsh1-002b", "local_id": "002", "name": "Dup"},
        ]},
    ]  # fmt: skip


def run(objs, aliases=None, data_json=None, cat=None):
    cat = cat or catalog()
    rows, _ = build_inventory(objs)
    folders = {}
    for r in rows:
        if r.kind == "card":
            folders[r.set_folder] = folders.get(r.set_folder, 0) + 1
    res = resolve_sets(folders, cat, aliases or {})
    return match_inventory(rows, res, cat, data_json or {}), res


def test_parse_card_folder_variants():
    assert parse_card_folder("Pikachu (58-102)") | {} == {
        "name": "Pikachu", "number_raw": "58-102", "local": "58", "total": "102",
        "interpretation": "local-total (separado por '-')", "alt_number_in_name": None, "qualifiers": [],
    }  # fmt: skip
    p = parse_card_folder("Aegislash EX - 65a_119 (065a-119)")
    assert (p["name"], p["local"], p["total"], p["alt_number_in_name"]) == ("Aegislash EX", "065a", "119", "65a_119")
    assert parse_card_folder("Flareon EX (RC6-RC32)")["local"] == "RC6"
    assert parse_card_folder("Mew (12)")["interpretation"] == "token único"
    assert parse_card_folder("Sin numero")["interpretation"] == "sin_numero"


def test_number_normalization_is_configurable():
    assert normalize_number("058") == "58"
    assert normalize_number("tg01") == "TG1"
    assert normalize_number("058", NumberRules(strip_leading_zeros=False)) == "058"
    assert normalize_name("  Pikachú  EX ") == "pikachú ex"


def test_inventory_excludes_products_and_odd_structures():
    objs = [
        ObjectInfo(DEFAULT_PREFIX + "Pokemon_Productos_Images/Box/a/a.webp", 1),
        ObjectInfo(DEFAULT_PREFIX + "Pokemon_Images/Base Set/x.webp", 1),
        ObjectInfo(DEFAULT_PREFIX + "Pokemon_Data/Base Set/Pikachu (58-102)/data.json", 1),
    ]
    rows, data_keys = build_inventory(objs)
    assert [r.exclusion_reason for r in rows] == ["producto sellado", "estructura inesperada"]
    assert len(data_keys) == 1


def test_exact_match_and_name_mismatch_downgrades():
    m, _ = run([obj("Base Set", "Pikachu (58-102)")])
    assert (m[0].status, m[0].catalog_card_id, m[0].method) == ("matched", "base1-58", "set+número exacto")
    assert m[0].confidence == ""
    m, _ = run([obj("Base Set", "Raichu (58-102)")])
    assert m[0].status == "probable" and "difiere" in m[0].reason


def test_normalized_number_match():
    m, _ = run([obj("Sword & Shield", "Celebi V (1-202)")])
    assert (m[0].status, m[0].method, m[0].catalog_card_id) == ("matched", "set+número normalizado", "swsh1-001")


def test_same_name_in_different_sets_is_not_confused():
    m, _ = run([obj("Base Set", "Pikachu (58-102)"), obj("Jungle", "Pikachu (60-64)")])
    assert [x.catalog_card_id for x in m] == ["base1-58", "base2-60"]


def test_ambiguous_number_in_set():
    m, _ = run([obj("Sword & Shield", "Otro (2-202)")])
    assert m[0].status == "ambiguous" and "comparten" in m[0].reason


def test_duplicate_target_marks_both_ambiguous():
    m, _ = run([obj("Base Set", "Pikachu (58-102)"), obj("Base Set", "Pikachu (058-102)")])
    assert [x.status for x in m] == ["ambiguous", "ambiguous"]
    assert "apuntan a base1-58" in m[0].reason


def test_unmatched_number_and_unresolved_set():
    m, _ = run([obj("Base Set", "Pikachu (99-102)"), obj("Set Inexistente Total", "X (1-2)")])
    assert [x.status for x in m] == ["unmatched", "unmatched"]
    assert "set unresolved" in m[1].reason


def test_set_resolution_levels():
    cat = catalog() + [{"id": "dup1", "name": "Jungle", "cards": []}]
    res = resolve_sets(
        {"Base Set": 1, "Sword & Shield": 1, "Jungle": 1, "Base Sett": 1, "Zzz": 1, "Raro": 1},
        cat,
        {"Raro": "base1", "Zzz": "nada"},
    )
    assert res["Base Set"].status == "confirmed" and res["Base Set"].via == "exact_name"
    assert res["Jungle"].status == "ambiguous"
    assert res["Base Sett"].status == "probable"
    assert res["Raro"].via == "alias"
    assert res["Zzz"].status == "unresolved" and "no existe" in res["Zzz"].note


def test_probable_set_caps_status():
    m, res = run([obj("Base Sett", "Pikachu (58-102)")])
    assert res["Base Sett"].status == "probable"
    assert m[0].status == "probable" and "set no confirmado" in m[0].reason


def test_data_json_used_when_folder_number_missing():
    dj = {("base set", "Pikachu"): {"number": "058/102", "productId": 1, "name": "Pikachu", "rarity": "Common",
                                      "set_name": "Base Set", "source": "tcgcsv"}}  # fmt: skip
    m, _ = run([obj("Base Set", "Pikachu")], data_json=dj)
    assert m[0].status == "matched" and "data.json" in m[0].method
    assert m[0].evidence["data_json"]["productId"] == 1


def test_manifest_files_written_without_database(tmp_path):
    m, _ = run([obj("Base Set", "Pikachu (58-102)"), obj("Base Set", "Charizard (99-102)")])
    assert summarize(m) == {"matched": 1, "unmatched": 1}
    jpath, cpath = write_manifest(m, tmp_path, "mf")
    assert jpath.exists() and len(read_manifest_csv(cpath)) == 2
    assert read_manifest_csv(cpath)[0]["approved"] == ""


# ---- aplicación ----


@pytest.fixture
def db(db_factory):
    with db_factory() as s:
        yield s


@pytest.fixture
def printing(db):
    g = Game(id=uuid.uuid4(), name="Pokemon", slug="pokemon")
    st = CardSet(game_id=g.id, external_id="base1", name="Base Set", language="en")
    c = Card(game_id=g.id, canonical_name="Pikachu", external_id="base1-58")
    db.add_all([g, st, c])
    db.flush()
    p = CardPrinting(card_id=c.id, set_id=st.id, external_id="base1-58-en", language="en")
    db.add(p)
    db.commit()
    return p


def row(printing_id, **kw):
    base = {"object_key": "k/a.webp", "status": "matched", "set_id": "base1", "candidate_printing_id": str(printing_id),
            "approved": "yes", "etag": "e", "size": "10", "method": "m", "evidence": "{}"}  # fmt: skip
    return base | kw


def test_apply_is_idempotent_and_records_provenance(db, printing):
    r = apply_manifest(db, [row(printing.id)], "bkt", "sha", "jeffry")
    db.commit()
    assert r["counts"] == {"created": 1}
    assert apply_manifest(db, [row(printing.id)], "bkt", "sha", "jeffry")["counts"] == {"unchanged": 1}
    a = db.query(ImageAssociation).one()
    assert (a.bucket, a.approved_by, a.manifest_sha256, a.provider) == ("bkt", "jeffry", "sha", "r2")
    assert printing.image_reference == "k/a.webp"


def test_apply_rejects_bad_rows(db, printing):
    rows = [
        row(uuid.uuid4(), object_key="a"),
        row(printing.id, object_key="b", status="ambiguous"),
        row(printing.id, object_key="c", set_id="otro"),
        row(printing.id, object_key="d", candidate_printing_id=""),
        row(printing.id, object_key="e", approved=""),
    ]
    r = apply_manifest(db, rows, "bkt", "sha", "jeffry")
    assert r["counts"] == {"rejected": 4, "skipped_not_approved": 1}
    assert "inexistente" in r["rejected"][0]["reason"]
    assert db.query(ImageAssociation).count() == 0


def test_apply_requires_approver(db, printing):
    with pytest.raises(ValueError):
        apply_manifest(db, [row(printing.id)], "bkt", "sha", " ")


def test_revert_keeps_row_and_can_reapply(db, printing):
    apply_manifest(db, [row(printing.id)], "bkt", "sha", "jeffry")
    assert revert_associations(db, "bkt", "jeffry", object_keys=["k/a.webp"]) == 1
    a = db.query(ImageAssociation).one()
    assert a.status == "reverted" and printing.image_reference is None
    assert apply_manifest(db, [row(printing.id)], "bkt", "sha2", "jeffry")["counts"] == {"reactivated": 1}


# ---- cliente TCGdex ----


def make_client(handler):
    s = Settings(provider_min_interval_seconds=0, provider_max_retries=2)
    return TcgdexClient(s, transport=httpx.MockTransport(handler), sleep=lambda _: None)


def test_tcgdex_snapshot_with_retry_on_503():
    calls = {"n": 0}

    def handler(req):
        if req.url.path == "/v2/en/sets":
            return httpx.Response(200, json=[{"id": "base1"}])
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={"id": "base1", "name": "Base Set", "cards": [{"id": "base1-58", "localId": "58", "name": "Pikachu"}]},
        )

    snap = make_client(handler).snapshot("en")
    assert calls["n"] == 2
    assert snap["sets"][0]["cards"][0] == {"id": "base1-58", "local_id": "58", "name": "Pikachu"}


def test_tcgdex_gives_up_and_rejects_non_json():
    with pytest.raises(ProviderError, match="intentos"):
        make_client(lambda req: httpx.Response(500)).snapshot("en")
    with pytest.raises(ProviderError, match="no JSON"):
        make_client(lambda req: httpx.Response(200, text="<html>")).snapshot("ja")
    with pytest.raises(ProviderError, match="404"):
        make_client(lambda req: httpx.Response(404)).snapshot("xx")


def test_parser_separates_variant_qualifiers_and_suffix_numbers():
    p = parse_card_folder("Alomomola (Master Ball Pattern) (024-086)")
    assert (p["name"], p["local"], p["qualifiers"]) == ("Alomomola", "024", ["Master Ball Pattern"])
    p = parse_card_folder("Aegislash - 060 (Prerelease) [Staff]")
    assert (p["name"], p["local"], p["qualifiers"]) == ("Aegislash", "060", ["Prerelease", "Staff"])
    assert parse_card_folder("Aipom - SM244")["local"] == "SM244"
    assert parse_card_folder("Aegislash - 86_146 (Prerelease)")["total"] == "146"
    assert parse_card_folder("Audino - 074_086 (Master Ball Patte (074-086)")["qualifiers"] == ["Master Ball Patte"]
    assert parse_card_folder("Audino (#12) (12-30)")["local"] == "12"


def test_variant_images_do_not_steal_the_base_card():
    m, _ = run([obj("Base Set", "Pikachu (58-102)"), obj("Base Set", "Pikachu (Prerelease) (58-102)")])
    assert m[0].status == "matched" and m[1].status == "ambiguous"
    assert "variante 'Prerelease'" in m[1].reason
