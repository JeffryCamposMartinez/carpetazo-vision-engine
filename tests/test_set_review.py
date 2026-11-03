import json

from app.images.aliases import load_aliases
from app.images.inventory import DEFAULT_PREFIX, build_inventory
from app.images.set_review import name_score, proposals, review_sets, write_review
from app.storage.base import ObjectInfo


# FIXTURE sintética: números y nombres inventados para probar reglas, no datos reales de TCGdex.
def cards(prefix, count, name="Card"):
    return [{"id": f"{prefix}-{i}", "local_id": str(i), "name": f"{name} {i}"} for i in range(1, count + 1)]


CATALOG = [
    {"id": "sv99", "name": "Mystery Flames", "cards": cards("sv99", 20)},
    {"id": "base1", "name": "Base Set", "cards": cards("base1", 20, "Base")},
    {"id": "a1", "name": "Alpha", "cards": cards("a1", 20, "Alpha")},
    {"id": "a2", "name": "Beta", "cards": cards("a2", 20, "Alpha")},  # mismos nombres/números que 'a1'
]


def folder_objs(folder, count, name="Card", start=1):
    out = []
    for i in range(start, start + count):
        card = f"{name} {i} ({i:03d}-999)"
        out.append(ObjectInfo(f"{DEFAULT_PREFIX}Pokemon_Images/{folder}/{card}/{card}.webp", 10))
    return out


def review(objs, unresolved, claimed=None, data_json=None, catalog=CATALOG):
    rows, _ = build_inventory(objs)
    return review_sets(rows, catalog, unresolved, data_json or {}, claimed=claimed)


def one(rev, folder):
    return next(f for f in rev["folders"] if f["r2_folder"] == folder)


def test_code_prefix_folder_is_confirmable_by_content():
    rev = review(folder_objs("SV99_ Mystery Flames", 20), ["SV99_ Mystery Flames"])
    f = one(rev, "SV99_ Mystery Flames")
    assert f["recommendation"] == "confirmable" and f["category"] == "code_prefix_name"
    assert f["candidates"][0]["tcgdex_set_id"] == "sv99"
    assert any("20/20" in e for e in f["candidates"][0]["evidence_for"])


def test_alternate_name_found_by_content_not_by_name():
    f = one(review(folder_objs("Totally Different", 20, "Base"), ["Totally Different"]), "Totally Different")
    assert f["candidates"][0]["tcgdex_set_id"] == "base1" and f["recommendation"] == "confirmable"
    assert f["category"] == "alternate_name"


def test_two_equal_candidates_need_review():
    f = one(review(folder_objs("Greek", 20, "Alpha"), ["Greek"]), "Greek")
    assert f["recommendation"] == "needs_review" and "similar" in f["reason"]


def test_small_sample_is_not_confirmable():
    f = one(review(folder_objs("Tiny Base", 5, "Base"), ["Tiny Base"]), "Tiny Base")
    assert f["recommendation"] == "needs_review" and "muestra pequeña" in f["reason"]


def test_no_candidate_when_nothing_matches():
    f = one(review(folder_objs("Zzz Qqq", 20, "Nothing"), ["Zzz Qqq"]), "Zzz Qqq")
    assert f["recommendation"] == "no_candidate" and f["category"] == "missing_in_catalog"


def test_variant_of_claimed_set_goes_to_review():
    rev = review(
        folder_objs("Base Set (Shadowless)", 20, "Base"), ["Base Set (Shadowless)"], claimed={"base1": ["Base Set"]}
    )
    f = one(rev, "Base Set (Shadowless)")
    assert f["category"] == "duplicate_of_claimed_set" and f["recommendation"] == "needs_review"
    assert any("ya está asignado" in e for e in f["candidates"][0]["evidence_against"])


def test_two_folders_to_same_set_are_both_downgraded():
    objs = folder_objs("First Alias", 20, "Base") + folder_objs("Second Alias", 20, "Base")
    rev = review(objs, ["First Alias", "Second Alias"])
    assert {f["recommendation"] for f in rev["folders"]} == {"needs_review"}
    assert all("apuntan a base1" in f["reason"] for f in rev["folders"])


def test_format_duplicate_siblings_detected():
    objs = folder_objs("Some_Set", 3, "Nothing") + folder_objs("Some Set", 3, "Nothing")
    f = one(review(objs, ["Some_Set", "Some Set"]), "Some_Set")
    assert f["category"] == "format_duplicate" and f["same_folder_siblings"] == ["Some Set"]


def test_grouped_large_folder_without_dominant_set():
    objs = folder_objs("Everything", 400, "Nothing")
    f = one(review(objs, ["Everything"]), "Everything")
    assert f["category"] == "grouped_multi_set" and f["recommendation"] == "needs_review"


def test_data_json_is_reported():
    dj = {
        ("some set", "Card 1 (001-999)"): {"set_name": "Some Official Name", "set_abbreviation": "SO", "set_groupId": 7}
    }
    f = one(review(folder_objs("Some Set", 3, "Nothing"), ["Some Set"], data_json=dj), "Some Set")
    assert f["data_json"]["set_names"] == ["Some Official Name"] and f["data_json"]["group_ids"] == [7]


def test_name_score_uses_prefix_variants_and_and():
    assert name_score("SV06_ Twilight Masquerade", "Twilight Masquerade")[0] == 1.0
    assert name_score("Black and White", "Black & White")[0] == 1.0


def test_reports_are_deterministic_and_proposals_have_no_reviewer(tmp_path):
    objs = folder_objs("SV99_ Mystery Flames", 20) + folder_objs("Zzz Qqq", 20, "Nothing")
    rev = review(objs, ["Zzz Qqq", "SV99_ Mystery Flames"])
    write_review(rev, tmp_path / "a")
    write_review(review(objs, ["SV99_ Mystery Flames", "Zzz Qqq"]), tmp_path / "b")
    for name in ("r2-sets-review.json", "r2-sets-review.csv", "r2-sets-review.md", "r2-set-alias-proposals.json"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    entries = proposals(rev)["aliases"]
    assert [e["r2_folder"] for e in entries] == ["SV99_ Mystery Flames"]  # no_candidate queda fuera
    assert (
        entries[0]["status"] == "proposed" and entries[0]["reviewed_by"] is None and entries[0]["reviewed_at"] is None
    )


def test_only_reviewed_confirmed_aliases_are_loaded(tmp_path):
    path = tmp_path / "aliases.json"
    path.write_text(
        json.dumps(
            {
                "aliases": [
                    {"r2_folder": "A", "tcgdex_set_id": "x", "status": "proposed", "reviewed_by": None},
                    {
                        "r2_folder": "B",
                        "tcgdex_set_id": "y",
                        "status": "confirmed",
                        "reviewed_by": None,
                        "reviewed_at": None,
                    },
                    {
                        "r2_folder": "C",
                        "tcgdex_set_id": "z",
                        "status": "confirmed",
                        "reviewed_by": "ana",
                        "reviewed_at": "2026-11-03",
                    },
                    {
                        "r2_folder": "D",
                        "tcgdex_set_id": "w",
                        "status": "rejected",
                        "reviewed_by": "ana",
                        "reviewed_at": "2026-11-03",
                    },
                ]
            }
        )
    )
    aliases, warnings = load_aliases(path)
    assert aliases == {"C": "z"} and len(warnings) == 1 and "'B'" in warnings[0]
    assert load_aliases(tmp_path / "missing.json") == ({}, [])
