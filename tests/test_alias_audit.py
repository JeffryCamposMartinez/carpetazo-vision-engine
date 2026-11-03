from app.images.alias_audit import audit_alias, object_proposals, proposals_v2, write_audit
from app.images.inventory import DEFAULT_PREFIX, build_inventory
from app.images.set_review import review_sets
from app.images.sets import resolve_sets
from app.storage.base import ObjectInfo


# FIXTURES sintéticas: nombres y números inventados para probar reglas, no datos reales de TCGdex.
def cards(prefix, count, name):
    return [{"id": f"{prefix}-{i}", "local_id": str(i), "name": f"{name} {i}"} for i in range(1, count + 1)]


def tset(set_id, name, count, card_name, official=None):
    return {"id": set_id, "name": name, "cards": cards(set_id, count, card_name),
            "card_count": {"official": official or count, "total": count}}  # fmt: skip


CATALOG = [
    tset("sv99", "Mystery Flames", 20, "Flame"),
    tset("base1", "Base Set", 20, "Base"),
    tset("promo1", "Alpha Promos", 20, "Promo"),
    tset("tk-a", "Kit Alpha", 10, "KA"),
    tset("tk-b", "Kit Beta", 10, "KB"),
]
P = DEFAULT_PREFIX + "Pokemon_Images/"


def objs(folder, name, count, total=20, etag="", quals=None, start=1):
    out = []
    for i in range(start, start + count):
        q = f" ({quals})" if quals else ""
        card = f"{name} {i}{q} ({i:03d}-{total:03d})"
        out.append(ObjectInfo(f"{P}{folder}/{card}/{card}.webp", 10, f"{etag}{i}"))
    return out


def audit(objects, aliases=None, catalog=CATALOG, probe=None):
    rows, _ = build_inventory(objects)
    folders = {}
    for r in rows:
        if r.kind == "card":
            folders[r.set_folder] = folders.get(r.set_folder, 0) + 1
    res = resolve_sets(folders, catalog, aliases or {})
    unresolved = [f for f, r in res.items() if r.status == "unresolved"]
    claimed = {}
    for f, r in res.items():
        if r.set_id:
            claimed.setdefault(r.set_id, []).append(f)
    review = review_sets(rows, catalog, unresolved, {}, claimed=claimed)
    result = audit_alias(rows, catalog, res, review, probe)
    return {e["r2_folder"]: e for e in result["folders"]}, result


def test_clean_proposal_is_recommended():
    a, _ = audit(objs("SV99_ Mystery Flames", "Flame", 20))
    e = a["SV99_ Mystery Flames"]
    assert e["audit_status"] == "recommended" and e["resolution"] == "set_alias" and e["conflicts"] == []
    assert (
        e["checks"]["numbers_compatible"]["ok"]
        and "oficial en TCGdex 20" in e["checks"]["numbers_compatible"]["detail"]
    )


def test_repeated_cards_with_qualifier_are_variants_and_explained():
    o = objs("SV99_ Mystery Flames", "Flame", 20) + objs("SV99_ Mystery Flames", "Flame", 10, quals="Poke Ball Pattern")
    e = audit(o)[0]["SV99_ Mystery Flames"]
    assert e["audit_status"] == "recommended" and e["resolution"] == "set_alias_with_variants"
    assert e["metrics"]["variant_images"] == 10 and e["metrics"]["base_images"] == 20
    assert "nunca a la impresión base" in e["justification"]


def test_oversize_without_explanation_is_not_recommended():
    # 20 cartas distintas del set + 10 nombres que no existen en él, sin calificador
    o = objs("SV99_ Mystery Flames", "Flame", 20) + objs("SV99_ Mystery Flames", "Ghost", 10, start=21)
    e = audit(o)[0]["SV99_ Mystery Flames"]
    assert e["audit_status"] != "recommended"
    assert e["checks"]["expected_size"]["ok"] is False


def test_unqualified_duplicates_block_recommendation():
    # "Flame 1 (1-020)" y "Flame 1 (001-020)" son la misma carta con el número escrito distinto
    same = objs("SV99_ Mystery Flames", "Flame", 20) + [
        ObjectInfo(f"{P}SV99_ Mystery Flames/Flame 1 (1-020)/Flame 1 (1-020).webp", 10, "zz")
    ]
    e = audit(same)[0]["SV99_ Mystery Flames"]
    assert e["metrics"]["duplicate_base_images"] == 1 and e["audit_status"] != "recommended"


def test_set_already_assigned_is_a_conflict_for_the_partial_folder():
    o = objs("Base Set", "Base", 20) + objs("Base Set Extras", "Base", 12, start=1)
    a, _ = audit(o)
    assert a["Base Set Extras"]["audit_status"] == "conflict"
    assert "ya está asignado" in a["Base Set Extras"]["justification"]


def test_two_unresolved_folders_same_set_only_partial_one_conflicts():
    o = objs("Totally Flames", "Flame", 20) + objs("Odd Flames Box", "Flame", 12)
    a, _ = audit(o)
    assert a["Totally Flames"]["audit_status"] == "recommended"  # dominante: no la penaliza una parcial
    assert a["Odd Flames Box"]["audit_status"] == "conflict"
    assert any("Totally Flames" in c for c in a["Odd Flames Box"]["conflicts"])


def test_shadowless_is_a_printing_variant_not_a_set_alias():
    o = objs("Base Set", "Base", 20, etag="a") + objs("Base Set (Shadowless)", "Base", 20, etag="b")
    probe = {"cards": [{"id": "base1-1", "variants_detailed": [{"type": "holo", "subtype": "shadowless"}]}]}
    e = audit(o, probe=probe)[0]["Base Set (Shadowless)"]
    assert e["audit_status"] == "needs_visual_review" and e["resolution"] == "printing_variant"
    va = e["metrics"]["variant_analysis"]
    assert va["same_set_folder"] == "Base Set" and va["shared_number_name"] == 20 and va["shared_identical_files"] == 0
    assert "shadowless" in va["tcgdex_variant_subtypes_seen"]
    assert "perdería la identidad" in " ".join(e["conflicts"])
    entry = proposals_v2({"folders": [e]})["aliases"][0]
    assert entry["relation"] == "printing_variant" and entry["reviewed_by"] is None


def test_format_duplicate_folders_are_compared_not_assumed_equal():
    o = objs("Some_Set", "Nothing", 12, etag="x") + objs("Some Set", "Nothing", 12, etag="y")
    a, _ = audit(o)
    cmp_ = a["Some_Set"]["metrics"]["sibling_comparison"][0]
    assert cmp_["folder"] == "Some Set" and cmp_["shared_number_name"] == 12 and cmp_["shared_identical_files"] == 0
    assert cmp_["same_content"] is False and "no tratar como copia" in a["Some_Set"]["notes"][0]
    o = objs("Some_Set", "Nothing", 12, etag="x") + objs("Some Set", "Nothing", 12, etag="x")
    assert audit(o)[0]["Some_Set"]["metrics"]["sibling_comparison"][0]["same_content"] is True


def test_trainer_kit_folder_is_split_per_card_never_one_alias():
    o = objs("XY Trainer Kit_ A & B", "KA", 10, total=10) + objs("XY Trainer Kit_ A & B", "KB", 10, total=10)
    e = audit(o)[0]["XY Trainer Kit_ A & B"]
    assert e["resolution"] == "per_card" and e["audit_status"] == "needs_visual_review"
    assert e["metrics"]["per_object"]["unique_by_set"] == {"tk-a": 10, "tk-b": 10}


def test_kit_missing_from_catalog_is_insufficient():
    e = audit(objs("SM Trainer Kit_ X & Y", "Nobody", 12, total=12))[0]["SM Trainer Kit_ X & Y"]
    assert e["audit_status"] == "insufficient_evidence"


def test_grouped_folder_needs_per_card_resolution():
    big = []
    for _src, name in (("sv99", "Flame"), ("base1", "Base")):
        big += objs("Prize Pack Cards", name, 20, start=1)
    big += objs("Prize Pack Cards", "Other", 400, start=1)
    a, res = audit(big)
    e = a["Prize Pack Cards"]
    assert e["resolution"] == "per_card" and e["audit_status"] in {"needs_visual_review", "insufficient_evidence"}
    assert res["per_object"] and {"unique"} & {o["outcome"] for o in res["per_object"]}
    props = object_proposals(res)["objects"]
    assert all(p["status"] == "proposed" and p["reviewed_by"] is None for p in props)


def test_promo_kinds():
    full = audit(objs("Alpha Special Promos", "Promo", 20))[0]["Alpha Special Promos"]
    assert full["promo_kind"] == "colección promocional presente en TCGdex"
    none = audit(objs("Ghost Promos", "Nothing", 15))[0]["Ghost Promos"]
    assert none["promo_kind"] == "no existe en TCGdex (inglés)" and none["audit_status"] == "insufficient_evidence"


def test_printed_total_contradiction_rejects_a_partial_proposal():
    o = objs("Alpha Promos Box", "Promo", 12, total=100) + objs("Alpha Promos Box", "Zzz", 8, total=100, start=13)
    e = audit(o)[0]["Alpha Promos Box"]
    assert e["audit_status"] == "rejected" and "total impreso" in e["justification"]


def test_small_sample_is_insufficient_even_with_perfect_match():
    e = audit(objs("SV99_ Mystery Flames", "Flame", 5))[0]["SV99_ Mystery Flames"]
    assert e["audit_status"] == "insufficient_evidence"


def test_proposals_exclude_rejected_and_keep_conflicts_unreviewed(tmp_path):
    o = objs("Base Set", "Base", 20) + objs("Base Set Extras", "Base", 12) + objs("Ghost Promos", "Nothing", 15)
    a, res = audit(o)
    entries = proposals_v2(res)["aliases"]
    folders = {e["r2_folder"]: e for e in entries}
    assert "Ghost Promos" not in folders
    assert folders["Base Set Extras"]["conflicts"] and folders["Base Set Extras"]["status"] == "proposed"
    assert all(e["reviewed_by"] is None and e["reviewed_at"] is None for e in entries)
    paths = write_audit(res, tmp_path / "a")
    write_audit(res, tmp_path / "b")
    assert {p.name for p in paths} >= {"r2-alias-audit.md", "r2-alias-audit.json", "r2-alias-audit.csv"}
    for p in paths:
        assert p.read_bytes() == (tmp_path / "b" / p.name).read_bytes()  # determinista
