# ruff: noqa: E501
"""Piloto de 30 cartas: estado, carencias, importación de plantillas, rondas A/B, referencias, duplicados e ids.

Todo con datos SINTÉTICOS generados por código (no son cartas ni fotografías reales). El plan se reduce a 2 cartas y
2 fotos por ronda mediante `pilot.json` para que la prueba sea rápida; las reglas son las mismas que con 30 y 5.
"""

import csv
import json

import pytest

from app.dataset_lab import pilot, schema
from app.dataset_lab.cli import main
from app.dataset_lab.pilot import (
    CARD_CSV_COLUMNS,
    SESSION_CSV_COLUMNS,
    declare_gap,
    import_cards_csv,
    import_sessions_csv,
    status,
)
from app.dataset_lab.splits import make_split_by_round
from app.dataset_lab.store import Dataset, DatasetError, atomic_write
from tests.test_dataset_lab import import_ok, write_photo

PER_ROUND = 2


@pytest.fixture
def ds(tmp_path):
    d = Dataset(tmp_path / "real-photos")
    d.init()
    atomic_write(
        d.root / "pilot.json",
        {"cards_target": 2, "photos_per_round": PER_ROUND, "category_targets": {"comun": 1, "holo": 1}, "declared_gaps": []},
    )
    d.add_session("s-test-a", "2026-11-05", "Teléfono de prueba", None, "tester", None, "A")
    d.add_session("s-test-b", "2026-11-06", "Teléfono de prueba", None, "tester", None, "B")
    return d


def review(ds, ref, *extra):
    return main(["--root", str(ds.root), "review", "--card", ref, "--status", "verified", "--by", "t", "--accept-quality", "--accept-duplicates", "--glare", "ninguno", *extra])


def edit(ds, kind, key, **changes):
    data = getattr(ds, kind)
    data[key].update(changes)
    getattr(ds, f"save_{kind}")(data)


def add_card(ds, i, categories, **extra):
    c = ds.add_card(
        pilot_slot=i, name=f"Carta de prueba {i}", set_name="Set de prueba", collector_number=f"{i:03d}/100", language="es",
        finish="normal", categories=categories, pattern="ninguno", reference_object_key=f"prueba/{i}.png", **extra,
    )  # fmt: skip
    ds.verify_card(c["card_ref"], "tester", "comparada con la carta física (prueba)")
    ds.verify_reference(c["card_ref"], "tester", "ilustración, número y símbolo coinciden (prueba)")
    edit(ds, "cards", c["card_ref"], reference_sha256="0" * 64)  # lo que haría references-fetch
    return c["card_ref"]


def add_photos(ds, tmp_path, ref, session, n, seed):
    folder = tmp_path / f"in-{ref}-{session}-{seed}"
    for k in range(n):
        write_photo(folder / f"{k}.jpg", seed + k)
    import_ok(ds, folder, session, ref)


@pytest.fixture
def complete(ds, tmp_path):
    """Piloto reducido y completo: 2 cartas verificadas, referencia verificada, 2+2 fotos verificadas, rondas A/B."""
    refs = [add_card(ds, 1, ["comun"]), add_card(ds, 2, ["holo"])]
    for i, ref in enumerate(refs):
        add_photos(ds, tmp_path, ref, "s-test-a", PER_ROUND, 100 + i * 10)
        add_photos(ds, tmp_path, ref, "s-test-b", PER_ROUND, 200 + i * 10)
        assert review(ds, ref) == 0
    return ds, refs


def failed(st):
    return {c["id"] for c in st["checks"] if not c["ok"]}


# ---- estado del piloto y criterios de «PILOTO COMPLETO»
def test_empty_dataset_is_incomplete_and_never_declared_complete(tmp_path):
    d = Dataset(tmp_path / "vacio")
    d.init()
    st = status(d)
    assert st["verdict"] == "PILOTO INCOMPLETO" and not st["complete"]
    assert {"cartas_registradas", "cartas_verificadas", "fotos_por_carta", "rondas_en_sesiones", "particion_sin_fuga"} <= failed(st)
    assert main(["--root", str(d.root), "pilot-status"]) == 1  # código de salida distinto de 0


def test_default_plan_demands_30_cards_5_photos_per_round_and_5_per_category(tmp_path):
    d = Dataset(tmp_path / "x")
    d.init()
    plan = pilot.load_plan(d)
    assert plan["cards_target"] == 30 and plan["photos_per_round"] == 5 and plan["rounds"] == {"A": "calibration", "B": "test"}
    assert plan["category_targets"] == {c: 5 for c in schema.PILOT_CATEGORIES}


def test_complete_pilot_needs_every_criterion(complete):
    ds, _ = complete
    st = status(ds)
    assert st["complete"] and st["verdict"] == "PILOTO COMPLETO" and not failed(st)
    assert all(c["complete"] for c in st["cards"]) and not st["has_declared_gaps"]
    assert main(["--root", str(ds.root), "pilot-status"]) == 0


@pytest.mark.parametrize(
    ("what", "expected_check", "mutate"),
    [
        ("carta sin verificar", "cartas_verificadas", lambda ds, refs: edit(ds, "cards", refs[0], verification_status="unverified")),
        ("referencia sin verificación manual", "metadatos_de_carta", lambda ds, refs: edit(ds, "cards", refs[0], reference_verified_by=None)),
        ("referencia sin traer (sin sha256)", "metadatos_de_carta", lambda ds, refs: edit(ds, "cards", refs[0], reference_sha256=None)),
        ("patrón sin declarar", "metadatos_de_carta", lambda ds, refs: edit(ds, "cards", refs[0], pattern="desconocido")),
        ("categorías sin asignar", "metadatos_de_carta", lambda ds, refs: edit(ds, "cards", refs[0], categories=["otra"])),
        ("similar sin motivo de dificultad", "metadatos_de_carta", lambda ds, refs: edit(ds, "cards", refs[0], categories=["comun", "similar_dificil"])),
        ("falta una foto verificada", "fotos_por_carta", lambda ds, refs: edit(ds, "photos", next(i for i, p in ds.photos.items() if p["card_ref"] == refs[0]), verification_status="rejected")),
        ("sesión sin ronda", "rondas_en_sesiones", lambda ds, refs: edit(ds, "sessions", "s-test-b", round=None)),
        ("condición desconocida", "condiciones_conocidas", lambda ds, refs: edit(ds, "photos", next(iter(ds.photos)), lighting_condition="desconocida")),
        ("foto con posible duplicado", "sin_duplicados_pendientes", lambda ds, refs: edit(ds, "photos", next(iter(ds.photos)), possible_duplicates=["x"])),
    ],
)  # fmt: skip
def test_each_missing_criterion_blocks_complete(complete, what, expected_check, mutate):
    ds, refs = complete
    mutate(ds, refs)
    st = status(ds)
    assert not st["complete"] and st["verdict"] == "PILOTO INCOMPLETO", what
    assert expected_check in failed(st), what


def test_extra_or_missing_cards_block_complete(complete):
    ds, _ = complete
    ds.add_card(pilot_slot=3, name="Tercera", set_name="Set de prueba", collector_number="003/100", language="es", finish="normal", categories=["comun"], pattern="ninguno")
    assert "cartas_registradas" in failed(status(ds))  # 3 registradas, el objetivo es exactamente 2


def test_category_below_target_is_a_shortfall_that_must_be_declared(complete):
    ds, _ = complete
    plan = json.loads((ds.root / "pilot.json").read_text())
    atomic_write(ds.root / "pilot.json", {**plan, "category_targets": {**plan["category_targets"], "full_art": 1}})
    st = status(ds)
    assert "cobertura_por_categoria" in failed(st) and not st["complete"]
    assert next(c for c in st["coverage_by_category"] if c["category"] == "full_art")["state"] == "carencia_sin_declarar"
    declare_gap(ds, "full_art", "no tengo ninguna full art", "tester")
    st = status(ds)
    assert next(c for c in st["coverage_by_category"] if c["category"] == "full_art")["state"] == "carencia_declarada"
    assert st["complete"] and st["has_declared_gaps"]  # las 2 cartas reales siguen completas; la carencia queda a la vista
    assert "no tengo ninguna full art" in pilot.render_markdown(st) and "no se inventó" in pilot.render_markdown(st)


def test_overlapping_categories_count_in_every_category(complete):
    ds, refs = complete
    edit(ds, "cards", refs[0], categories=["comun", "holo", "similar_dificil"], difficulty_note="se parece a otra")
    st = status(ds)
    cov = {c["category"]: c for c in st["coverage_by_category"]}
    assert cov["comun"]["registered"] == 1 and cov["holo"]["registered"] == 2  # la carta 1 cuenta en comun y en holo
    assert st["cards_with_n_categories"] == {1: 1, 3: 1}


# ---- máximo de fotos verificadas por ronda y carta
def test_more_than_the_allowed_verified_photos_per_round_is_rejected(complete, tmp_path):
    ds, refs = complete
    add_photos(ds, tmp_path, refs[0], "s-test-a", 1, 900)  # una tercera foto en la ronda A (límite 2 en esta prueba)
    extra = next(i for i, p in ds.photos.items() if p["verification_status"] in ("pending", "needs_review"))
    assert review(ds, refs[0]) == 1  # la herramienta se niega
    assert ds.photos[extra]["verification_status"] in ("pending", "needs_review")
    assert status(ds)["complete"]
    # aunque alguien edite el manifiesto a mano, el estado lo detecta como exceso
    edit(ds, "photos", extra, verification_status="verified", verified_by="t")
    st = status(ds)
    card = next(c for c in st["cards"] if c["card_ref"] == refs[0])
    assert not st["complete"] and "ronda A: 3/2 fotos verificadas" in card["problems"]


def test_photos_of_the_other_round_do_not_count_against_the_limit(complete, tmp_path):
    ds, refs = complete
    add_photos(ds, tmp_path, refs[0], "s-test-b", 1, 950)
    assert review(ds, refs[0]) == 1  # B ya tiene 2; A no se toca
    cards = {c["card_ref"]: c for c in status(ds)["cards"]}
    assert cards[refs[0]]["photos_A"] == 2 and cards[refs[0]]["photos_B"] == 2


# ---- carencias (pilot-gap)
def test_pilot_gap_records_a_shortfall_without_inventing_or_substituting_a_card(ds, capsys):
    cards_before = ds.cards
    assert main(["--root", str(ds.root), "pilot-gap", "--category", "reverse_holo", "--reason", "no tengo reverse holo", "--by", "tester"]) == 0
    gaps = json.loads((ds.root / "pilot.json").read_text())["declared_gaps"]
    assert len(gaps) == 1 and gaps[0]["category"] == "reverse_holo" and gaps[0]["declared_by"] == "tester" and gaps[0]["declared_at"]
    assert ds.cards == cards_before == {}  # no se creó ninguna carta
    declare_gap(ds, "reverse_holo", "otro motivo", "otra persona")  # idempotente: no duplica ni pisa
    assert len(json.loads((ds.root / "pilot.json").read_text())["declared_gaps"]) == 1
    assert capsys.readouterr().out  # el comando imprime lo registrado


@pytest.mark.parametrize(
    ("category", "reason", "by"),
    [("inventada", "x", "t"), ("reverse_holo", "", "t"), ("reverse_holo", "   ", "t"), ("reverse_holo", "x", ""), ("antigua", "x", "t")],
)
def test_pilot_gap_rejects_invalid_input(ds, category, reason, by):
    with pytest.raises(DatasetError):
        declare_gap(ds, category, reason, by)
    assert main(["--root", str(ds.root), "pilot-gap", "--category", category, "--reason", reason, "--by", by]) == 1


def test_declared_gap_shows_in_status_and_markdown_report(ds, tmp_path):
    declare_gap(ds, "reverse_holo", "no tengo", "tester", slot=12)
    md = tmp_path / "out" / "estado.md"
    assert main(["--root", str(ds.root), "pilot-status", "--md", str(md)]) == 1
    text = md.read_text(encoding="utf-8")
    assert "PILOTO INCOMPLETO" in text and "reverse_holo" in text and "slot 12" in text and "no tengo" in text


# ---- importación de CSV
def write_csv(path, rows, columns=CARD_CSV_COLUMNS):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)
    return path


def good_row(slot=1, **kw):
    return {"slot": slot, "nombre": f"Carta de prueba {slot}", "set": "Set de prueba", "numero": f"{int(slot) if str(slot).isdigit() else 0:03d}/100", "idioma": "es", "acabado": "normal", "categorias": "comun", "patron": "ninguno", **kw}


def test_blank_template_is_30_empty_rows_that_import_nothing(tmp_path):
    out = tmp_path / "plantillas"
    pilot.write_templates(out)
    rows = list(csv.DictReader((out / "plantilla-30-cartas.csv").open(encoding="utf-8-sig")))
    assert [int(r["slot"]) for r in rows] == list(range(1, 31)) and list(rows[0]) == CARD_CSV_COLUMNS
    sugg = [r["categoria_sugerida"] for r in rows]
    assert {c: sugg.count(c) for c in schema.PILOT_CATEGORIES} == {c: 5 for c in schema.PILOT_CATEGORIES}
    assert all(not v for r in rows for k, v in r.items() if k not in ("slot", "categoria_sugerida"))  # nada inventado
    d = Dataset(tmp_path / "ds")
    d.init()
    for dry in (True, False):
        res = import_cards_csv(d, out / "plantilla-30-cartas.csv", "t", dry_run=dry)
        assert res["ignored_empty"] == 30 and not res["created"] and not res["errors"] and not res["gaps"]
    assert d.cards == {} and not (d.root / "pilot.json").exists()


def test_example_template_cannot_be_imported_as_real_cards(tmp_path):
    out = tmp_path / "plantillas"
    pilot.write_templates(out)
    rows = list(csv.DictReader((out / "plantilla-30-cartas.EJEMPLO.csv").open(encoding="utf-8-sig")))
    assert len(rows) == 3 and all("EJEMPLO" in (r["nombre"] or r["sin_carta_motivo"]) for r in rows)
    d = Dataset(tmp_path / "ds")
    d.init()
    for dry in (True, False):
        res = import_cards_csv(d, out / "plantilla-30-cartas.EJEMPLO.csv", "t", dry_run=dry)
        assert not res["created"] and not res["gaps"] and len(res["errors"]) == 3
        assert all("EJEMPLO" in e["error"] for e in res["errors"])
    assert d.cards == {} and not (d.root / "pilot.json").exists()


def test_valid_rows_import_and_dry_run_writes_nothing(ds, tmp_path):
    csv_path = write_csv(tmp_path / "c.csv", [good_row(1), good_row(2, categorias="holo;similar_dificil", motivo_dificultad="se parece a la 1", similar_a="Carta de prueba 1", acabado="holo", grupo_variante="g1")])
    dry = import_cards_csv(ds, csv_path, "t", dry_run=True)
    assert dry["created"] == [1, 2] and not dry["errors"] and ds.cards == {}
    real = import_cards_csv(ds, csv_path, "t")
    assert len(real["created"]) == 2 and not real["errors"]
    c2 = next(c for c in ds.cards.values() if c["pilot_slot"] == 2)
    assert c2["categories"] == ["holo", "similar_dificil"] and c2["verification_status"] == "unverified"
    assert c2["card_id"] is None and c2["printing_id"] is None and c2["variant_id"] is None  # nunca se inventan ids
    assert import_cards_csv(ds, csv_path, "t")["skipped_existing"] == [1, 2] and len(ds.cards) == 2  # idempotente


def full_size_plan(ds):
    atomic_write(ds.root / "pilot.json", {**json.loads((ds.root / "pilot.json").read_text()), "cards_target": 30})


def test_gap_rows_declare_shortfalls_and_create_no_card(ds, tmp_path):
    full_size_plan(ds)
    csv_path = write_csv(tmp_path / "c.csv", [{"slot": 12, "categoria_sugerida": "reverse_holo", "sin_carta_motivo": "no tengo reverse holo"}, {"slot": 13, "categorias": "reverse_holo;full_art", "sin_carta_motivo": "tampoco"}])
    dry = import_cards_csv(ds, csv_path, "t", dry_run=True)
    assert len(dry["gaps"]) == 3 and not json.loads((ds.root / "pilot.json").read_text())["declared_gaps"] and ds.cards == {}
    real = import_cards_csv(ds, csv_path, "t")
    assert {(g["category"], g["slot"]) for g in real["gaps"]} == {("reverse_holo", 12), ("reverse_holo", 13), ("full_art", 13)}
    assert ds.cards == {}
    assert len(json.loads((ds.root / "pilot.json").read_text())["declared_gaps"]) == 3


@pytest.mark.parametrize(
    ("what", "row", "message"),
    [
        ("categoría inválida", good_row(1, categorias="rara"), "categories"),
        ("patrón inválido", good_row(1, patron="ultraball"), "pattern"),
        ("acabado inválido", good_row(1, acabado="brillante"), "finish"),
        ("idioma inválido", good_row(1, idioma="español de chile"), "language"),
        ("sin set", good_row(1, set=""), "set_name"),
        ("sin número", good_row(1, numero=""), "collector_number"),
        ("slot fuera de rango", good_row(31), "slot"),
        ("slot cero", good_row(0), "slot"),
        ("slot no numérico", good_row("abc"), "slot"),
        ("nombre y carencia a la vez", good_row(1, sin_carta_motivo="no tengo"), "no ambos"),
        ("referencia sin verificador", good_row(1, carta_verificada_por="t", nota_verificacion="ok", clave_referencia_r2="a/b.png"), "referencia_verificada_por"),
        ("referencia sin nota", good_row(1, carta_verificada_por="t", nota_verificacion="ok", clave_referencia_r2="a/b.png", referencia_verificada_por="t"), "nota_referencia"),
        ("referencia en carta sin verificar", good_row(1, clave_referencia_r2="a/b.png", referencia_verificada_por="t", nota_referencia="n"), "ya verificadas"),
        ("verificador sin nota", good_row(1, carta_verificada_por="t"), "nota_verificacion"),
        ("nota sin verificador", good_row(1, nota_verificacion="n"), "carta_verificada_por"),
        ("verificación de referencia sin clave", good_row(1, referencia_verificada_por="t"), "clave_referencia_r2"),
        ("similar sin motivo se acepta pero no completa (solo se avisa en el estado)", good_row(1, categorias="similar_dificil"), None),
    ],
)  # fmt: skip
def test_invalid_rows_are_reported_and_nothing_is_written_even_in_dry_run(ds, tmp_path, what, row, message):
    csv_path = write_csv(tmp_path / "c.csv", [row])
    for dry in (True, False):
        res = import_cards_csv(ds, csv_path, "t", dry_run=dry)
        if message is None:
            assert not res["errors"], what
        else:
            assert len(res["errors"]) == 1 and message in res["errors"][0]["error"], what
            assert not res["created"] and ds.cards == {}, what  # ni siquiera una carta a medias
    assert main(["--root", str(ds.root), "pilot-import-cards", "--csv", str(csv_path), "--by", "t", "--dry-run"]) == (0 if message is None else 1)


def test_gap_row_with_bad_or_missing_category_is_an_error(ds, tmp_path):
    full_size_plan(ds)
    csv_path = write_csv(tmp_path / "c.csv", [{"slot": 40, "sin_carta_motivo": "x"}, {"slot": 1, "categorias": "rara", "sin_carta_motivo": "x"}, {"slot": 99, "categorias": "holo", "sin_carta_motivo": "x"}])
    res = import_cards_csv(ds, csv_path, "t")
    assert len(res["errors"]) == 3 and not res["gaps"] and not json.loads((ds.root / "pilot.json").read_text())["declared_gaps"]


def test_catalog_ids_must_be_empty_or_valid_uuids(ds, tmp_path):
    ok = "123e4567-e89b-12d3-a456-426614174000"
    for column in ("card_id", "printing_id", "variant_id"):
        bad = write_csv(tmp_path / f"{column}.csv", [good_row(1, **{column: "no-es-uuid"})])
        res = import_cards_csv(ds, bad, "t", dry_run=True)
        assert len(res["errors"]) == 1 and "UUID" in res["errors"][0]["error"], column
        assert import_cards_csv(ds, bad, "t")["errors"] and ds.cards == {}
    empty = import_cards_csv(ds, write_csv(tmp_path / "e.csv", [good_row(1, card_id="", printing_id="  ", variant_id="")]), "t")
    card = next(iter(ds.cards.values()))
    assert not empty["errors"] and (card["card_id"], card["printing_id"], card["variant_id"]) == (None, None, None)
    good = write_csv(tmp_path / "g.csv", [good_row(2, card_id=ok)])
    assert not import_cards_csv(ds, good, "t")["errors"]
    assert next(c for c in ds.cards.values() if c["pilot_slot"] == 2)["card_id"] == ok
    with pytest.raises(DatasetError, match="UUID"):
        ds.add_card(name="X", set_name="Y", collector_number="9/9", card_id="")  # cadena vacía no es null ni UUID


def test_verified_row_with_manual_reference_verification_is_imported_verified(ds, tmp_path):
    row = good_row(1, carta_verificada_por="tester", nota_verificacion="nombre, set y número leídos en la carta", clave_referencia_r2="pokemon/x.png", referencia_verificada_por="tester", nota_referencia="ilustración y número coinciden")
    assert not import_cards_csv(ds, write_csv(tmp_path / "c.csv", [row]), "t")["errors"]
    c = next(iter(ds.cards.values()))
    assert c["verification_status"] == "verified" and c["reference_verified_by"] == "tester" and c["reference_verification_note"]


# ---- referencias digitales sin verificación manual
def test_reference_without_manual_verification_is_never_trusted(ds):
    with pytest.raises(DatasetError, match="reference_verified_by sin reference_object_key"):
        ds.add_card(name="A", set_name="S", collector_number="1/1", language="es", finish="normal", categories=["comun"], pattern="ninguno", reference_verified_by="t")
    c = ds.add_card(name="A", set_name="S", collector_number="1/1", language="es", finish="normal", categories=["comun"], pattern="ninguno", reference_object_key="a/b.png")
    with pytest.raises(DatasetError, match="verificada antes"):
        ds.verify_reference(c["card_ref"], "t", "nota")  # la carta física no está verificada
    ds.verify_card(c["card_ref"], "t", "nota")
    for by, note in (("", "nota"), ("t", ""), ("  ", "  ")):
        with pytest.raises(DatasetError, match="--by y --note"):
            ds.verify_reference(c["card_ref"], by, note)
    assert ds.cards[c["card_ref"]]["reference_verified_by"] is None
    ds.add_card(name="B", set_name="S", collector_number="2/2", language="es", finish="normal", categories=["comun"], pattern="ninguno")
    other = next(r for r, x in ds.cards.items() if x["name"] == "B")
    ds.verify_card(other, "t", "nota")
    with pytest.raises(DatasetError, match="card-reference"):
        ds.verify_reference(other, "t", "nota")  # sin clave de referencia


def test_unverified_references_block_the_pilot_and_the_evaluation(complete):
    ds, refs = complete
    edit(ds, "cards", refs[0], reference_verified_by=None, reference_verified_at=None, reference_verification_note=None)
    st = status(ds)
    assert "metadatos_de_carta" in failed(st)
    assert any(p.startswith("referencia sin verificar") for c in st["cards"] for p in c["problems"])


def test_evaluation_ignores_references_without_manual_verification(ds, tmp_path):
    from app.dataset_lab.evaluate import run_evaluation
    from app.dataset_lab.splits import make_split
    from app.recognition_lab.features import GridExtractor
    from tests.test_dataset_lab import make_eval_dataset

    make_eval_dataset(ds, tmp_path, cards=2)
    make_split(ds, "p", manual={"s-test-a": "calibration", "s-test-b": "test"})
    cards = ds.cards
    for c in cards.values():
        c["reference_verified_by"] = None
    ds.save_cards(cards)
    with pytest.raises(DatasetError, match="referencias"):
        run_evaluation(ds, "p", {"grid": GridExtractor()}, allow_fictitious=True)


# ---- cartas y sesiones duplicadas
def test_same_card_cannot_be_registered_twice(ds):
    fields = dict(name="Pikachu", set_name="Base", collector_number="58/102", language="es", finish="normal", categories=["comun"], pattern="ninguno")
    ds.add_card(**fields)
    with pytest.raises(DatasetError, match="duplicada"):
        ds.add_card(**{**fields, "name": "  PIKACHU "})  # mayúsculas y espacios no la distinguen
    ds.add_card(**{**fields, "finish": "reverse_holo", "categories": ["reverse_holo"]})  # otro acabado = otra carta física
    ds.add_card(**{**fields, "pattern": "pokeball"})  # variante de patrón = otra carta física
    assert len(ds.cards) == 3


def test_csv_duplicates_inside_the_file_and_against_the_dataset_are_rejected(ds, tmp_path):
    full_size_plan(ds)
    twice_slot = write_csv(tmp_path / "a.csv", [good_row(1), good_row(1, nombre="Otra", numero="009/100")])
    res = import_cards_csv(ds, twice_slot, "t")
    assert len(res["created"]) == 1 and "repetido" in res["errors"][0]["error"] and len(ds.cards) == 1
    same_card = write_csv(tmp_path / "b.csv", [good_row(2, nombre="Carta de prueba 1", numero="001/100")])
    res = import_cards_csv(ds, same_card, "t", dry_run=True)
    assert "duplicada" in res["errors"][0]["error"]
    in_file = write_csv(tmp_path / "c.csv", [good_row(3), good_row(4, nombre="Carta de prueba 3", numero="003/100")])
    res = import_cards_csv(ds, in_file, "t", dry_run=True)
    assert res["created"] == [3] and "duplicada" in res["errors"][0]["error"]


def test_duplicate_sessions_are_rejected(ds, tmp_path):
    with pytest.raises(DatasetError, match="ya existe"):
        ds.add_session("s-test-a", "2026-11-07", None, None, None, None, "A")
    rows = [{"session_id": "piloto-A-1", "ronda": "A", "fecha": "2026-11-10"}, {"session_id": "piloto-A-1", "ronda": "A", "fecha": "2026-11-10"}, {"session_id": "s-test-a", "ronda": "A", "fecha": "2026-11-10"}]
    csv_path = write_csv(tmp_path / "s.csv", rows, SESSION_CSV_COLUMNS)
    for dry in (True, False):
        d = Dataset(tmp_path / f"d{dry}")
        d.init()
        d.add_session("s-test-a", "2026-11-05", None, None, None, None, "A")
        res = import_sessions_csv(d, csv_path, dry_run=dry)
        assert res["created"] == ["piloto-A-1"] and res["skipped_existing"] == ["s-test-a"] and "repetido" in res["errors"][0]["error"]
        assert set(d.sessions) == ({"s-test-a"} if dry else {"s-test-a", "piloto-A-1"})


def test_session_csv_validates_round_date_and_id(ds, tmp_path):
    rows = [{"session_id": "x", "ronda": "A", "fecha": "2026-11-10"}, {"session_id": "piloto-C-1", "ronda": "C", "fecha": "2026-11-10"}, {"session_id": "piloto-A-9", "ronda": "A", "fecha": ""}, {"session_id": "piloto-B-9", "ronda": "", "fecha": "2026-11-10"}]
    csv_path = write_csv(tmp_path / "s.csv", rows, SESSION_CSV_COLUMNS)
    for dry in (True, False):
        res = import_sessions_csv(ds, csv_path, dry_run=dry)
        assert not res["created"] and len(res["errors"]) == 4
    assert set(ds.sessions) == {"s-test-a", "s-test-b"}


def test_session_template_has_one_round_a_and_one_round_b_and_imports_with_dates(tmp_path):
    out = tmp_path / "t"
    pilot.write_templates(out)
    rows = list(csv.DictReader((out / "plantilla-sesiones.csv").open(encoding="utf-8-sig")))
    assert [(r["session_id"], r["ronda"]) for r in rows] == [("piloto-A-1", "A"), ("piloto-B-1", "B")]
    d = Dataset(tmp_path / "ds")
    d.init()
    assert len(import_sessions_csv(d, out / "plantilla-sesiones.csv", dry_run=True)["errors"]) == 2  # falta la fecha: no inventamos una


# ---- partición A/B
def test_split_by_round_maps_a_to_calibration_and_b_to_test_without_duplicates(complete):
    ds, _ = complete
    split = make_split_by_round(ds, "piloto-1")
    assert split["assignment"] == {"s-test-a": "calibration", "s-test-b": "test"}
    assert not split["leaks"]["blocking"]
    assert split["coverage"]["calibration"]["photos"] == 4 and split["coverage"]["test"]["photos"] == 4
    photos = ds.photos
    ids = {s: {i for i, p in photos.items() if p["capture_session_id"] == s} for s in ("s-test-a", "s-test-b")}
    assert not ids["s-test-a"] & ids["s-test-b"]
    shas = {s: {photos[i]["file"]["sha256"] for i in v} for s, v in ids.items()}
    assert not shas["s-test-a"] & shas["s-test-b"]
    assert main(["--root", str(ds.root), "split", "--name", "p2", "--by-round"]) == 0


def test_split_by_round_blocks_the_same_shot_saved_in_both_rounds(complete, tmp_path):
    ds, refs = complete
    folder_a, folder_b = tmp_path / "ra", tmp_path / "rb"
    write_photo(folder_a / "x.jpg", 777, quality=95)
    write_photo(folder_b / "x.jpg", 777, quality=70)  # la misma toma, re-guardada, en la otra ronda
    import_ok(ds, folder_a, "s-test-a", refs[0])
    import_ok(ds, folder_b, "s-test-b", refs[0])
    # verificamos a mano las dos copias (saltando el límite de la herramienta) para comprobar que la partición las detecta
    new = [i for i, p in ds.photos.items() if p["verification_status"] in ("pending", "needs_review")]
    assert len(new) == 2
    for i in new:
        edit(ds, "photos", i, verification_status="verified", verified_by="t", glare="ninguno")
    result = make_split_by_round(ds, "con-fuga")
    assert result["leaks"]["blocking"] and len(result["leaks"]["near_duplicate_pairs_across_splits"]) == 1
    assert "particion_sin_fuga" in failed(status(ds)) and not status(ds)["complete"]
    assert main(["--root", str(ds.root), "split", "--name", "con-fuga-2", "--by-round"]) == 1


def test_split_by_round_requires_rounds_on_every_used_session_and_both_rounds(complete):
    ds, _ = complete
    edit(ds, "sessions", "s-test-b", round=None)
    with pytest.raises(DatasetError, match="sin ronda"):
        make_split_by_round(ds, "x")
    edit(ds, "sessions", "s-test-b", round="A")  # las dos sesiones en la misma ronda
    with pytest.raises(DatasetError, match="ronda A y una de la ronda B"):
        make_split_by_round(ds, "y")
    with pytest.raises(DatasetError, match="inválida"):
        ds.add_session("s-test-c", "2026-11-07", None, None, None, None, "C")


def test_session_round_must_be_a_b_or_null():
    assert schema.validate_session({"round": "A"}) == [] and schema.validate_session({"round": None}) == []
    assert schema.validate_session({"round": "calibracion"})


# ---- fechas y cartas sin referencia verificada
@pytest.mark.parametrize("bad", ["", "10-11-2026", "2026/11/10", "2026-13-01", "2026-02-30", "ayer", "2026-1-5"])
def test_session_dates_must_be_real_yyyy_mm_dd(ds, tmp_path, bad):
    with pytest.raises(DatasetError, match="AAAA-MM-DD"):
        ds.add_session("piloto-A-7", bad, None, None, None, None, "A")
    csv_path = write_csv(tmp_path / "s.csv", [{"session_id": "piloto-A-7", "ronda": "A", "fecha": bad}], SESSION_CSV_COLUMNS)
    assert len(import_sessions_csv(ds, csv_path, dry_run=True)["errors"]) == 1


def test_verified_card_without_verified_reference_is_excluded_from_the_evaluation(ds, tmp_path):
    from app.dataset_lab.evaluate import run_evaluation
    from app.dataset_lab.splits import make_split
    from app.recognition_lab.features import GridExtractor
    from tests.test_dataset_lab import make_eval_dataset

    refs = make_eval_dataset(ds, tmp_path, cards=3)
    edit(ds, "cards", refs[0], reference_verified_by=None)
    make_split(ds, "p", manual={"s-test-a": "calibration", "s-test-b": "test"})
    d = run_evaluation(ds, "p", {"grid": GridExtractor()}, allow_fictitious=True)["dataset"]
    assert d["gallery_cards"] == 2 and d["test_photos"] == 4 and d["calibration_photos"] == 4
    assert d["excluded_photos_without_verified_reference"] == 4  # ni conocida ni desconocida: fuera


# ---- vínculo con el catálogo de Carpetazo (categoria_id_carpetazo / product_id_carpetazo)
def test_carpetazo_product_link_is_optional_but_always_complete_and_positive(ds, tmp_path):
    full_size_plan(ds)
    ok = write_csv(tmp_path / "ok.csv", [good_row(1, categoria_id_carpetazo="3", product_id_carpetazo="5001"), good_row(2)])
    res = import_cards_csv(ds, ok, "t")
    assert not res["errors"]
    by_slot = {c["pilot_slot"]: c for c in ds.cards.values()}
    assert (by_slot[1]["catalog_category_id"], by_slot[1]["catalog_product_id"]) == (3, 5001) and by_slot[2]["catalog_category_id"] is None and by_slot[2]["catalog_product_id"] is None
    for bad in ({"categoria_id_carpetazo": "3"}, {"product_id_carpetazo": "5001"}, {"categoria_id_carpetazo": "x", "product_id_carpetazo": "5"}, {"categoria_id_carpetazo": "3", "product_id_carpetazo": "0"}, {"categoria_id_carpetazo": "-3", "product_id_carpetazo": "5"}):
        d = Dataset(tmp_path / f"d{abs(hash(str(bad)))}")
        d.init()
        atomic_write(d.root / "pilot.json", {"cards_target": 30, "photos_per_round": 5, "category_targets": {}, "declared_gaps": []})
        res = import_cards_csv(d, write_csv(tmp_path / "bad.csv", [good_row(1, **bad)]), "t")
        assert len(res["errors"]) == 1 and d.cards == {}, bad
        assert import_cards_csv(d, tmp_path / "bad.csv", "t", dry_run=True)["errors"], bad


def test_template_no_longer_asks_for_tcgdex_ids_and_asks_for_carpetazo_ids():
    assert "id_tcgdex" not in CARD_CSV_COLUMNS and {"categoria_id_carpetazo", "product_id_carpetazo"} <= set(CARD_CSV_COLUMNS)


def test_dataset_schema_rejects_half_or_invalid_catalog_links(ds):
    base = dict(name="A", set_name="S", collector_number="1/1", language="es", finish="normal", categories=["comun"], pattern="ninguno")
    for bad in ({"catalog_category_id": 3}, {"catalog_product_id": 5}, {"catalog_category_id": 3, "catalog_product_id": 0}, {"catalog_category_id": True, "catalog_product_id": 5}, {"catalog_category_id": 3, "catalog_product_id": "5"}):
        with pytest.raises(DatasetError):
            ds.add_card(**base, **bad)
    c = ds.add_card(**base, catalog_category_id=3, catalog_product_id=5)
    assert (c["catalog_category_id"], c["catalog_product_id"]) == (3, 5)
