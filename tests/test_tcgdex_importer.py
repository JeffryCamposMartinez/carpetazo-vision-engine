import json

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.catalog.importers.tcgdex import TcgdexImporter, render_report_md, validate_card
from app.catalog.providers.tcgdex import TcgdexClient
from app.core.config import Settings
from app.database.models import (
    Card,
    CardEquivalence,
    CardPrinting,
    CardSet,
    ExternalReference,
    Game,
    ImageAssociation,
    ImportIssue,
    ImportRun,
    ImportRunItem,
    PrintingVariant,
)


# FIXTURES sintéticas: ids, nombres y variantes inventados para probar reglas; no son datos reales de TCGdex.
def card(cid, local, name, set_id, **extra):
    return {"id": cid, "localId": local, "name": name, "category": "Pokemon", "rarity": "Rare",
            "image": f"https://assets.example/{cid}", "set": {"id": set_id, "name": set_id}, "dexId": [25],
            "illustrator": "Fixture", "hp": 60, "pricing": {"cardmarket": {"avg": 1}}, "updated": "2026-01-01",
            **extra}  # fmt: skip


HOLO_VARIANTS = [
    {"type": "holo", "subtype": "unlimited", "size": "standard", "variantId": "v-unl",
     "thirdParty": {"tcgplayer": 111, "cardmarket": 222}},
    {"type": "holo", "subtype": "shadowless", "stamp": ["1st-edition"], "variantId": "v-sh-1st",
     "thirdParty": {"tcgplayer": 333}},
    {"type": "holo", "subtype": "shadowless", "variantId": "v-sh", "thirdParty": {"tcgplayer": 333}},
    {"type": "reverse", "foil": "pokeball", "variantId": "v-rev-pb", "pricing": {"x": 1}},
    {"type": "reverse", "foil": "masterball", "variantId": "v-rev-mb"},
    {"type": "reverse", "variantId": "v-rev"},
]  # fmt: skip


def make_api():
    return {
        "sets": {"en": ["base1", "sv-test"], "fr": ["base1"]},
        "detail": {
            ("en", "base1"): {
                "id": "base1",
                "name": "Base Set",
                "releaseDate": "1999-01-09",
                "abbreviation": {"official": "BS"},
                "serie": {"id": "base", "name": "Base"},
                "cardCount": {"official": 2, "total": 2},
                "cards": [{"id": "base1-1"}, {"id": "base1-4"}],
                "weirdSetField": 1,
            },
            ("en", "sv-test"): {
                "id": "sv-test",
                "name": "SV Test",
                "releaseDate": "2024-01-01",
                "cards": [{"id": "sv-test-9"}],
            },
            ("fr", "base1"): {
                "id": "base1",
                "name": "Set de Base",
                "releaseDate": "1999-10-01",
                "cards": [{"id": "base1-4"}],
            },
        },
        "cards": {
            ("en", "base1-1"): card("base1-1", "1", "Alakazam", "base1", variants_detailed=HOLO_VARIANTS, newField="x"),
            ("en", "base1-4"): card("base1-4", "4", "Charizard", "base1", variants={"normal": False, "holo": True}),
            ("en", "sv-test-9"): card(
                "sv-test-9", "9", "Charizard", "sv-test", variants={"normal": True, "reverse": True}
            ),
            ("fr", "base1-4"): card("base1-4", "4", "Dracaufeu", "base1", variants={"normal": False, "holo": True}),
        },
    }


def make_importer(db_factory, api, interrupt_on=None):
    def handler(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")[1:]  # [lang, sets|cards, id?]
        lang, kind = parts[0], parts[1]
        if interrupt_on and request.url.path.endswith(interrupt_on):
            raise KeyboardInterrupt
        if kind == "sets" and len(parts) == 2:
            return httpx.Response(200, json=[{"id": i} for i in api["sets"].get(lang, [])])
        key = (lang, parts[2])
        table = api["detail"] if kind == "sets" else api["cards"]
        return httpx.Response(200, json=table[key]) if key in table else httpx.Response(404)

    client = TcgdexClient(
        Settings(provider_min_interval_seconds=0, provider_max_retries=1),
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    return TcgdexImporter(client, db_factory)


def counts(db_factory):
    models = (Game, CardSet, Card, CardPrinting, PrintingVariant, ExternalReference, ImportIssue)
    with db_factory() as db:
        return {m.__tablename__: db.scalar(select(func.count()).select_from(m)) for m in models}


def test_first_import_creates_the_model(db_factory):
    report = make_importer(db_factory, make_api()).run()
    e = report.to_dict()["entities"]
    assert report.status == "completed" and not report.errors
    assert e["set"]["created"] == 2 and e["card"]["created"] == 3 and e["printing"]["created"] == 3
    assert e["variant"]["created"] == 6 + 1 + 2  # 6 detalladas + holo (bandera) + normal y reverse (banderas)
    assert counts(db_factory)["games"] == 1


def test_reimport_is_idempotent(db_factory):
    importer = make_importer(db_factory, make_api())
    importer.run()
    before = counts(db_factory)
    report = importer.run()
    assert counts(db_factory) == before
    d = report.to_dict()["entities"]
    assert d["card"] == {"unchanged": 3} and d["printing"] == {"unchanged": 3} and d["variant"] == {"unchanged": 9}
    assert report.external_references == {}


def test_multiple_variants_per_printing_with_distinct_attributes(db_factory):
    make_importer(db_factory, make_api()).run()
    with db_factory() as db:
        printing = db.scalar(select(CardPrinting).where(CardPrinting.external_id == "base1-1"))
        variants = db.scalars(select(PrintingVariant).where(PrintingVariant.printing_id == printing.id)).all()
    by_id = {v.external_id: v for v in variants}
    assert len(variants) == 6
    assert (by_id["v-sh-1st"].variant_subtype, by_id["v-sh-1st"].stamp) == ("shadowless", "1st-edition")
    assert by_id["v-sh"].stamp == "" and by_id["v-sh"].variant_subtype == "shadowless"
    assert {v.foil for v in variants if v.variant_type == "reverse"} == {"", "pokeball", "masterball"}
    assert all(v.language is None for v in variants)  # el idioma de la variante no está verificado


def test_flag_variants_are_marked_as_coarse(db_factory):
    make_importer(db_factory, make_api()).run()
    with db_factory() as db:
        sources = set(
            db.scalars(select(PrintingVariant.source).join(CardPrinting).where(CardPrinting.external_id == "sv-test-9"))
        )
    assert sources == {"tcgdex_flags"}


def test_reprints_stay_separate_cards_and_no_global_identity_is_invented(db_factory):
    make_importer(db_factory, make_api()).run()
    with db_factory() as db:
        zards = db.scalars(select(Card).where(Card.canonical_name == "Charizard")).all()
        assert len(zards) == 2 and {c.external_id for c in zards} == {"base1-4", "sv-test-9"}
        assert all(c.identity_basis == "provider_card_id" for c in zards)
        assert db.scalar(select(func.count()).select_from(CardEquivalence)) == 0


def test_languages_share_the_card_and_keep_separate_printings(db_factory):
    importer = make_importer(db_factory, make_api())
    importer.run(languages=("en",))
    importer.run(languages=("fr", "en"))
    with db_factory() as db:
        c = db.scalar(select(Card).where(Card.external_id == "base1-4"))
        assert c.canonical_name == "Charizard" and c.meta["names"] == {"en": "Charizard", "fr": "Dracaufeu"}
        printings = db.scalars(select(CardPrinting).where(CardPrinting.card_id == c.id)).all()
        assert sorted((p.language, p.catalog_language, p.name) for p in printings) == [
            ("en", "en", "Charizard"), ("fr", "fr", "Dracaufeu"),
        ]  # fmt: skip
        sets = db.scalars(select(CardSet).where(CardSet.external_id == "base1")).all()
        assert sorted((s.language, s.name) for s in sets) == [("en", "Base Set"), ("fr", "Set de Base")]
        assert db.scalar(select(func.count()).select_from(Card)) == 3  # fr no duplicó cartas


def test_french_first_does_not_make_the_card_name_french_once_english_arrives(db_factory):
    importer = make_importer(db_factory, make_api())
    importer.run(languages=("fr",))
    with db_factory() as db:
        assert db.scalar(select(Card.canonical_name).where(Card.external_id == "base1-4")) == "Dracaufeu"
    importer.run(languages=("en",))
    with db_factory() as db:
        assert db.scalar(select(Card.canonical_name).where(Card.external_id == "base1-4")) == "Charizard"


def test_external_references_and_shared_ids(db_factory):
    report = make_importer(db_factory, make_api()).run()
    with db_factory() as db:
        card_ref = db.scalar(
            select(ExternalReference).where(
                ExternalReference.entity_type == "card", ExternalReference.external_id == "base1-1"
            )
        )
        assert card_ref.provider == "tcgdex" and card_ref.scope == "" and card_ref.card_id is not None
        tp = db.scalars(
            select(ExternalReference).where(
                ExternalReference.provider == "tcgplayer", ExternalReference.external_id == "333"
            )
        ).all()
        assert len(tp) == 2 and all(r.variant_id for r in tp)  # un productId compartido por dos variantes
    assert {
        "kind": "id_externo_compartido",
        "provider": "tcgplayer",
        "entity_type": "variant",
        "external_id": "333",
        "entities": 2,
    } in report.duplicates


def test_unknown_fields_are_recorded_and_pricing_is_ignored_on_purpose(db_factory):
    report = make_importer(db_factory, make_api()).run()
    assert report.unmapped_fields["newField"] == 1 and report.unmapped_fields["weirdSetField"] == 1
    assert report.ignored_fields["pricing"] >= 3
    with db_factory() as db:
        p = db.scalar(select(CardPrinting).where(CardPrinting.external_id == "base1-1"))
        assert p.meta["unmapped"] == {"newField": "x"} and "pricing" not in json.dumps(p.meta)
        assert p.meta["image_base_url"] == "https://assets.example/base1-1"  # solo la referencia, nada descargado


def test_invalid_relation_and_incomplete_data_are_reported_not_fatal(db_factory):
    api = make_api()
    api["cards"][("en", "base1-1")]["set"] = {"id": "otro-set"}
    del api["cards"][("en", "sv-test-9")]  # 404 del proveedor
    api["detail"][("en", "base1")]["cards"].append({"id": "base1-77"})
    api["cards"][("en", "base1-77")] = {"id": "base1-77", "localId": "77", "name": " ", "set": {"id": "base1"}}
    report = make_importer(db_factory, api).run()
    stages = {(e["external_id"], e["stage"]) for e in report.errors}
    assert stages == {("base1-1", "validate"), ("sv-test-9", "fetch"), ("base1-77", "validate")}
    assert any("relación inválida" in e["message"] for e in report.errors)
    assert report.status == "completed_with_errors"
    with db_factory() as db:
        assert {c.external_id for c in db.scalars(select(Card))} == {"base1-4"}
        assert db.scalar(select(func.count()).select_from(ImportIssue)) == 3
    assert validate_card({"id": "a", "localId": "1", "name": "x", "variants_detailed": [{"foo": 1}]}, "a", "s")


def test_interrupted_import_resumes_without_redoing_finished_sets(db_factory):
    api = make_api()
    with pytest.raises(KeyboardInterrupt):
        make_importer(db_factory, api, interrupt_on="/sv-test").run()
    with db_factory() as db:
        run = db.scalar(select(ImportRun))
        assert run.status == "interrupted"
        done = {(i.set_external_id, i.status) for i in db.scalars(select(ImportRunItem))}
        assert done == {("base1", "done")}
    report = make_importer(db_factory, api).run(resume=True)
    assert report.sets_skipped_resume == 1 and report.status == "completed"
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(ImportRun)) == 1  # reutilizó la misma ejecución
        assert db.scalar(select(func.count()).select_from(CardSet)) == 2
    full = counts(db_factory)
    other = make_importer(db_factory, api)
    other.run()
    assert counts(db_factory) == full  # una importación completa posterior no agrega nada


def test_card_level_rollback_keeps_the_rest_of_the_set(db_factory, monkeypatch):
    importer = make_importer(db_factory, make_api())
    original = importer._upsert_variants

    def boom(db, printing, lang, raw, delta, cid):
        if cid == "base1-4":
            raise RuntimeError("fallo simulado")
        return original(db, printing, lang, raw, delta, cid)

    monkeypatch.setattr(importer, "_upsert_variants", boom)
    report = importer.run()
    assert [e["external_id"] for e in report.errors] == ["base1-4"] and report.errors[0]["stage"] == "persist"
    with db_factory() as db:
        ids = {c.external_id for c in db.scalars(select(Card))}
        assert ids == {"base1-1", "sv-test-9"}  # la carta fallida no dejó ni Card ni CardPrinting a medias
        assert db.scalar(select(func.count()).select_from(CardPrinting)) == 2


def test_set_level_rollback_discards_the_whole_set_and_marks_it_failed(db_factory, monkeypatch):
    importer = make_importer(db_factory, make_api())
    original = importer._upsert_set

    def boom(db, game, source, sid, lang, detail, delta):
        if sid == "sv-test":
            raise RuntimeError("fallo simulado de set")
        return original(db, game, source, sid, lang, detail, delta)

    monkeypatch.setattr(importer, "_upsert_set", boom)
    report = importer.run()
    assert report.status == "completed_with_errors"
    with db_factory() as db:
        assert {s.external_id for s in db.scalars(select(CardSet))} == {"base1"}
        items = {(i.set_external_id, i.status) for i in db.scalars(select(ImportRunItem))}
        assert items == {("base1", "done"), ("sv-test", "failed")}
    monkeypatch.undo()
    assert importer.run(resume=True).sets_skipped_resume == 1  # solo reintenta el set fallido
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(CardSet)) == 2


def test_dry_run_persists_nothing_but_reports_what_would_happen(db_factory):
    report = make_importer(db_factory, make_api()).run(dry_run=True)
    assert report.to_dict()["entities"]["card"]["created"] == 3 and report.run_id is None
    assert all(v == 0 for v in counts(db_factory).values())


def test_report_markdown_lists_errors_and_duplicates(db_factory):
    api = make_api()
    del api["cards"][("en", "sv-test-9")]
    text = render_report_md(make_importer(db_factory, api).run().to_dict())
    assert "## Errores (1)" in text and "sv-test-9" in text and "id_externo_compartido" in text


def test_missing_set_in_a_language_is_reported(db_factory):
    report = make_importer(db_factory, make_api()).run(languages=("fr",), set_ids={"base1", "nope"})
    assert [(e["external_id"], e["stage"]) for e in report.errors] == [("nope", "validate")]


# ---- restricciones del modelo (SQLite; las mismas se prueban en PostgreSQL real en test_postgres.py) ----


def test_model_constraints(db_factory):
    make_importer(db_factory, make_api()).run()
    with db_factory() as db:
        p = db.scalar(select(CardPrinting).where(CardPrinting.external_id == "base1-1"))
        db.add(
            CardPrinting(card_id=p.card_id, set_id=p.set_id, external_id="otra", language="en", catalog_language="en")
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
        db.add(PrintingVariant(printing_id=p.id, source="tcgdex", external_id="v-unl", variant_type="holo"))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
        game = db.scalar(select(Game))
        db.add(
            ExternalReference(entity_type="game", entity_key=str(game.id), provider="x", external_id="1")
        )  # sin entidad
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
        db.add(
            ExternalReference(
                entity_type="game", entity_key="k", game_id=game.id, card_id=p.card_id, provider="x", external_id="1"
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
        assert db.scalar(select(func.count()).select_from(ImageAssociation)) == 0
