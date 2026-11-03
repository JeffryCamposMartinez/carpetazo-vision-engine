import json

from sqlalchemy import func, select

from app.database.models import Card, Game, ImageAssociation, ImageMappingProposal
from app.images.proposals import load_proposals


def row(key, status, card="", **kw):
    return {"object_key": key, "status": status, "catalog_card_id": card, "set_folder": "F", "qualifiers": "",
            "reason": "", "method": "m", "set_id": "s", "set_resolution": "confirmed", **kw}  # fmt: skip


def seed(db_factory, *card_ids):
    with db_factory() as db:
        g = Game(name="Pokemon", slug="pokemon")
        db.add(g)
        db.flush()
        for cid in card_ids:
            db.add(Card(game_id=g.id, external_id=cid, canonical_name=cid))
        db.commit()


def write(tmp_path, rows, objects=None):
    m = tmp_path / "m.json"
    m.write_text(json.dumps(rows))
    o = None
    if objects is not None:
        o = tmp_path / "o.json"
        o.write_text(json.dumps({"objects": objects}))
    return m, o


def test_outcomes_flags_and_no_approved_associations(db_factory, tmp_path):
    seed(db_factory, "base1-1", "base1-2")
    rows = [
        row("a", "matched", "base1-1"),
        row("b", "probable", "base1-2", set_resolution="probable"),
        row("c", "ambiguous", "base1-1", qualifiers="Master Ball Pattern", reason="variante 'x' de base1-1"),
        row("d", "unmatched", reason="set unresolved"),
        row("e", "excluded"),
        row("f", "matched", "zzz-9"),  # carta que no existe en el catálogo importado
    ]
    manifest, _ = write(tmp_path, rows)
    with db_factory() as db:
        result = load_proposals(db, "bkt", manifest)
        db.commit()
        by_key = {p.object_key: p for p in db.scalars(select(ImageMappingProposal))}
        assert db.scalar(select(func.count()).select_from(ImageAssociation)) == 0
    assert result["excluded_not_loaded"] == 1 and "e" not in by_key
    assert by_key["a"].outcome == "unique_candidate" and by_key["a"].resolved_card_ids
    assert "probable_match" in by_key["b"].flags and "set_alias_not_confirmed" in by_key["b"].flags
    assert by_key["c"].outcome == "multiple_candidates" and {"variant_needed", "duplicate_target"} <= set(
        by_key["c"].flags
    )
    assert by_key["d"].outcome == "no_candidate" and "set_unresolved" in by_key["d"].flags
    assert "card_not_in_catalog" in by_key["f"].flags and by_key["f"].resolved_card_ids == []
    assert all("unknown_language" in p.flags and p.image_language == "unknown" for p in by_key.values())


def test_duplicate_objects_for_same_card_are_flagged_not_decided(db_factory, tmp_path):
    seed(db_factory, "base1-1")
    manifest, _ = write(tmp_path, [row("a", "matched", "base1-1"), row("b", "matched", "base1-1", set_folder="Otra")])
    with db_factory() as db:
        load_proposals(db, "bkt", manifest)
        db.commit()
        assert all("duplicate_across_objects" in p.flags for p in db.scalars(select(ImageMappingProposal)))


def test_per_object_proposals_are_kept_separate_from_manifest_ones(db_factory, tmp_path):
    seed(db_factory, "base1-1")
    objects = [{"object_key": "a", "tcgdex_card_id": "base1-1", "status": "proposed", "evidence": {"r2_folder": "Kit"},
                "reviewed_by": None, "reviewed_at": None}]  # fmt: skip
    manifest, objs = write(tmp_path, [row("a", "unmatched", reason="set unresolved")], objects)
    with db_factory() as db:
        load_proposals(db, "bkt", manifest, objs)
        db.commit()
        got = {(p.source, p.outcome): p for p in db.scalars(select(ImageMappingProposal))}
    assert set(got) == {("manifest", "no_candidate"), ("audit_per_object", "unique_candidate")}
    assert "folder_not_single_set" in got[("audit_per_object", "unique_candidate")].flags


def test_loading_twice_is_idempotent_and_keeps_rejections(db_factory, tmp_path):
    seed(db_factory, "base1-1")
    manifest, _ = write(tmp_path, [row("a", "matched", "base1-1")])
    with db_factory() as db:
        assert load_proposals(db, "bkt", manifest)["proposals"] == {"created": 1}
        db.commit()
        db.scalar(select(ImageMappingProposal)).status = "rejected"
        db.commit()
        again = load_proposals(db, "bkt", manifest)["proposals"]
        db.commit()
        assert again == {"unchanged": 1} and db.scalar(select(ImageMappingProposal)).status == "rejected"
        assert db.scalar(select(func.count()).select_from(ImageMappingProposal)) == 1
