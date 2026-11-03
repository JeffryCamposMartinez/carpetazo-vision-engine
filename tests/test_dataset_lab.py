# ruff: noqa: E501
import csv
import hashlib
import json
import os
import stat
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.dataset_lab import schema
from app.dataset_lab.cli import main
from app.dataset_lab.evaluate import run_evaluation
from app.dataset_lab.example import AVISO, build_example
from app.dataset_lab.exporter import backup_dataset, export_dataset
from app.dataset_lab.importer import import_folder
from app.dataset_lab.splits import make_split
from app.dataset_lab.store import Dataset, DatasetError
from app.dataset_lab.validator import validate
from app.recognition_lab.augment import NORMAL, make_scene
from app.recognition_lab.features import GridExtractor, PHashExtractor
from tests.test_recognition_lab import fake_card

DEFAULTS = {
    "lighting_condition": "natural_directa",
    "camera_angle": "frontal",
    "background": "madera",
    "sleeve_condition": "sin_funda",
}


# FIXTURES sintéticas: "cartas" y "fotos" generadas por código; no son fotografías reales.
def write_photo(path: Path, seed: int, quality: int = 92, blur: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    scene, _ = make_scene(fake_card(seed), seed, NORMAL)
    if blur:
        scene = cv2.GaussianBlur(scene, (0, 0), blur)
    cv2.imwrite(str(path), scene, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def ds(tmp_path):
    d = Dataset(tmp_path / "real-photos")
    d.init()
    d.add_session("s-test-a", "2026-11-05", "Teléfono de prueba", None, "tester", None)
    d.add_session("s-test-b", "2026-11-06", "Teléfono de prueba", None, "tester", None)
    return d


def add_verified_card(ds, seed):
    c = ds.add_card(
        name=f"Carta de prueba {seed}",
        set_name="Set de prueba",
        collector_number=f"{seed:03d}/100",
        language="es",
        finish="normal",
        category="comun",
    )
    ds.verify_card(c["card_ref"], "tester", "comparada con la carta física (prueba)")
    return c["card_ref"]


def import_ok(ds, folder, session, ref):
    return import_folder(ds, folder, session, ref, DEFAULTS, imported_by="tester")


def test_init_is_idempotent_and_creates_the_layout(tmp_path):
    d = Dataset(tmp_path / "x")
    assert d.init() is True and d.init() is False
    for part in ("originals", "manifest", "splits", "references", "inbox", "exports"):
        assert (d.root / part).is_dir()
    assert json.loads((d.root / "dataset.json").read_text())["schema_version"] == schema.SCHEMA_VERSION


def test_identity_fields_default_to_unknown_and_reject_invented_ids(ds):
    c = ds.add_card(name="X", set_name="Y", collector_number="1/1")
    assert (
        c["card_id"] is None
        and c["printing_id"] is None
        and c["variant_id"] is None
        and c["verification_status"] == "unverified"
    )
    with pytest.raises(DatasetError, match="UUID"):
        ds.add_card(name="X", set_name="Y", collector_number="1/1", card_id="no-es-un-uuid")
    with pytest.raises(DatasetError, match="--by"):
        ds.verify_card(c["card_ref"], "", "nota")
    with pytest.raises(DatasetError):
        ds.add_session("s-test-a", "2026-11-05", None, None, None, None)  # duplicada


def test_import_copies_read_only_never_touches_the_source_and_records_metadata(ds, tmp_path):
    ref = add_verified_card(ds, 1)
    src = tmp_path / "inbox"
    files = [write_photo(src / f"IMG_{i}.jpg", 10 + i) for i in range(3)]
    before = {f.name: (sha(f), f.stat().st_mtime) for f in files}
    report = import_ok(ds, src, "s-test-a", ref).to_dict()
    assert report["imported"] == 3 and report["skipped"] == 0
    assert {f.name: (sha(f), f.stat().st_mtime) for f in files} == before  # el origen no cambió
    photos = ds.photos
    for p in photos.values():
        dest = ds.root / p["file"]["path"]
        assert dest.exists() and sha(dest) == p["file"]["sha256"]
        assert not os.access(dest, os.W_OK) or not (dest.stat().st_mode & stat.S_IWRITE)  # solo lectura
        assert (
            p["card_id"] is None
            and p["verification_status"] in {"pending", "needs_review"}
            and p["verified_by"] is None
        )
        assert (
            p["lighting_condition"] == "natural_directa"
            and p["glare"] == "desconocido"
            and p["device_model"] == "Teléfono de prueba"
        )
        assert schema.validate_photo(p) == []


def test_dry_run_writes_nothing_and_exact_duplicates_are_skipped(ds, tmp_path):
    ref = add_verified_card(ds, 1)
    src = tmp_path / "inbox"
    write_photo(src / "a.jpg", 5)
    assert import_folder(ds, src, "s-test-a", ref, DEFAULTS, dry_run=True).to_dict()["imported"] == 1
    assert ds.photos == {} and not list(ds.originals.rglob("*.jpg"))
    import_ok(ds, src, "s-test-a", ref)
    again = import_ok(ds, src, "s-test-a", ref).to_dict()
    assert again["imported"] == 0 and "duplicado exacto" in again["skipped_detail"][0]["reason"]
    (src / "copia.jpg").write_bytes((src / "a.jpg").read_bytes())  # mismo contenido, otro nombre
    assert "duplicado exacto" in import_ok(ds, src, "s-test-b", ref).to_dict()["skipped_detail"][0]["reason"]


def test_unsupported_corrupt_and_low_quality_files_are_flagged_not_silently_accepted(ds, tmp_path):
    ref = add_verified_card(ds, 1)
    src = tmp_path / "inbox"
    write_photo(src / "borrosa.jpg", 7, blur=25.0)
    (src / "rota.jpg").write_bytes(b"\xff\xd8\xff garbage")
    (src / "texto.txt").write_text("no es imagen")
    small = np.full((300, 200, 3), 128, np.uint8)
    cv2.imwrite(str(src / "chica.png"), small)
    report = import_ok(ds, src, "s-test-a", ref).to_dict()
    by = {i["file"]: i for i in report["imported_detail"]}
    assert "borrosa" in by["borrosa.jpg"]["flags"] and by["borrosa.jpg"]["status"] == "needs_review"
    assert "resolucion_baja" in by["chica.png"]["flags"] and by["chica.png"]["status"] == "needs_review"
    assert [s["file"] for s in report["skipped_detail"]] == ["rota.jpg"]  # el .txt ni se considera


def test_sidecar_overrides_per_file_conditions_and_rejects_invalid_values(ds, tmp_path):
    ref = add_verified_card(ds, 1)
    src = tmp_path / "inbox"
    write_photo(src / "a.jpg", 5)
    side = tmp_path / "side.csv"
    side.write_text("filename,lighting_condition,notes\na.jpg,flash,con reflejo\n", encoding="utf-8")
    import_folder(ds, src, "s-test-a", ref, DEFAULTS, sidecar=side)
    p = next(iter(ds.photos.values()))
    assert p["lighting_condition"] == "flash" and p["notes"] == "con reflejo" and p["background"] == "madera"
    side.write_text("filename,lighting_condition\nb.jpg,luz_de_luna\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="no es válido"):
        import_folder(ds, src, "s-test-b", ref, DEFAULTS, sidecar=side)


def test_same_shot_saved_twice_across_sessions_is_flagged_as_leak_risk(ds, tmp_path):
    ref = add_verified_card(ds, 1)
    a, b = tmp_path / "a", tmp_path / "b"
    write_photo(a / "x.jpg", 21, quality=95)
    write_photo(b / "x.jpg", 21, quality=70)  # misma toma re-guardada
    import_ok(ds, a, "s-test-a", ref)
    report = import_ok(ds, b, "s-test-b", ref).to_dict()
    assert (
        "posible_duplicado_entre_sesiones" in report["imported_detail"][0]["flags"]
        and report["imported_detail"][0]["status"] == "needs_review"
    )
    assert (
        main(["--root", str(ds.root), "review", "--card", ref, "--status", "verified", "--by", "tester"]) == 1
    )  # bloquea
    assert (
        main(
            [
                "--root",
                str(ds.root),
                "review",
                "--card",
                ref,
                "--status",
                "verified",
                "--by",
                "tester",
                "--accept-duplicates",
            ]
        )
        == 0
    )


def populate(ds, tmp_path, cards=3, per_session=2):
    refs = [add_verified_card(ds, i + 1) for i in range(cards)]
    for ci, ref in enumerate(refs):
        for sess in ("s-test-a", "s-test-b"):
            folder = tmp_path / f"in-{ref}-{sess}"
            for k in range(per_session):
                write_photo(folder / f"{k}.jpg", 100 + ci * 10 + k + (50 if sess.endswith("b") else 0))
            import_ok(ds, folder, sess, ref)
        assert (
            main(
                [
                    "--root",
                    str(ds.root),
                    "review",
                    "--card",
                    ref,
                    "--status",
                    "verified",
                    "--by",
                    "tester",
                    "--accept-quality",
                    "--accept-duplicates",
                ]
            )
            == 0
        )
    return refs


def test_validator_detects_tampering_orphans_and_identity_drift(ds, tmp_path):
    populate(ds, tmp_path)
    assert validate(ds)["ok"]
    p = next(iter(ds.photos.values()))
    path = ds.root / p["file"]["path"]
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    path.write_bytes(path.read_bytes() + b"\x00")  # alteración del original
    (ds.originals / "huerfano.jpg").write_bytes(b"x")
    photos = ds.photos
    other = list(photos.values())[1]
    other["variant_id"] = "123e4567-e89b-12d3-a456-426614174000"  # deriva respecto de la carta
    ds.save_photos(photos)
    result = validate(ds)
    assert not result["ok"]
    text = " ".join(result["errors"])
    assert "SHA-256 no coincide" in text and "variant_id no coincide" in text
    assert any("huérfano" in w for w in result["warnings"])
    assert validate(ds)["summary"]["pilot_cards_missing"] == 27


def test_split_is_by_session_deterministic_and_needs_two_sessions(ds, tmp_path):
    ref = add_verified_card(ds, 1)
    write_photo(tmp_path / "a" / "x.jpg", 1)
    import_ok(ds, tmp_path / "a", "s-test-a", ref)
    main(["--root", str(ds.root), "review", "--card", ref, "--status", "verified", "--by", "t", "--accept-quality"])
    with pytest.raises(DatasetError, match="al menos 2 sesiones"):
        make_split(ds, "p")
    write_photo(tmp_path / "b" / "x.jpg", 2)
    import_ok(ds, tmp_path / "b", "s-test-b", ref)
    main(["--root", str(ds.root), "review", "--card", ref, "--status", "verified", "--by", "t", "--accept-quality"])
    s1, s2 = make_split(ds, "p", seed=3), make_split(ds, "q", seed=3)
    assert s1["assignment"] == s2["assignment"] and sorted(s1["assignment"].values()) == ["calibration", "test"]
    forced = make_split(ds, "m", manual={"s-test-a": "test", "s-test-b": "calibration"})
    assert forced["assignment"] == {"s-test-a": "test", "s-test-b": "calibration"}
    with pytest.raises(DatasetError, match="inválida"):
        make_split(ds, "bad", manual={"nope": "test"})


def test_split_reports_near_duplicates_across_sets_and_export_refuses(ds, tmp_path):
    ref = add_verified_card(ds, 1)
    write_photo(tmp_path / "a" / "x.jpg", 21, quality=95)
    write_photo(tmp_path / "b" / "x.jpg", 21, quality=70)
    import_ok(ds, tmp_path / "a", "s-test-a", ref)
    import_ok(ds, tmp_path / "b", "s-test-b", ref)
    main(
        [
            "--root",
            str(ds.root),
            "review",
            "--card",
            ref,
            "--status",
            "verified",
            "--by",
            "t",
            "--accept-quality",
            "--accept-duplicates",
        ]
    )
    split = make_split(ds, "p", manual={"s-test-a": "calibration", "s-test-b": "test"})
    assert split["leaks"]["blocking"] and len(split["leaks"]["near_duplicate_pairs_across_splits"]) == 1
    with pytest.raises(DatasetError, match="fugas"):
        export_dataset(ds, ds.root / "exports" / "e", "p")
    export_dataset(ds, ds.root / "exports" / "e2", "p", allow_leaks=True, copy_images=False)


def test_export_copies_only_verified_leaves_originals_and_refuses_unsafe_targets(ds, tmp_path):
    refs = populate(ds, tmp_path)
    ds.photos[next(iter(ds.photos))]  # lectura
    photos = ds.photos
    first = next(iter(photos))
    photos[first]["verification_status"] = "pending"
    photos[first]["verified_by"] = None
    ds.save_photos(photos)
    make_split(ds, "p")
    originals = {f: sha(f) for f in ds.originals.rglob("*") if f.is_file()}
    out = tmp_path / "export"
    header = export_dataset(ds, out, "p")
    assert header["photos"] == len(photos) - 1
    rows = list(csv.DictReader((out / "manifest.csv").open(encoding="utf-8-sig")))
    assert (
        len(rows) == header["photos"]
        and {r["verification_status"] for r in rows} == {"verified"}
        and first not in {r["image_id"] for r in rows}
    )
    assert {r["split"] for r in rows} == {"calibration", "test"} and all(r["card_id"] == "" for r in rows)
    sums = (out / "SHA256SUMS.txt").read_text().splitlines()
    assert len(sums) == len(rows) and all(sha(out / line.split("  ", 1)[1]) == line.split("  ", 1)[0] for line in sums)
    assert {f: sha(f) for f in ds.originals.rglob("*") if f.is_file()} == originals
    with pytest.raises(DatasetError, match="no está vacía"):
        export_dataset(ds, out, "p")
    with pytest.raises(DatasetError, match="originals"):
        export_dataset(ds, ds.originals / "dentro", "p")
    prov = export_dataset(ds, tmp_path / "export2", include_pending=True)
    assert prov["photos"] == len(photos) and refs


def test_backup_verifies_every_file_and_cannot_nest_inside_the_dataset(ds, tmp_path):
    populate(ds, tmp_path, cards=1)
    result = backup_dataset(ds, tmp_path / "respaldos")
    assert result["files_verified"] >= 2 + len(ds.photos)
    assert (Path(result["destination"]) / "manifest" / "photos.json").exists()
    with pytest.raises(DatasetError, match="dentro del propio dataset"):
        backup_dataset(ds, ds.root / "backups")


def test_example_manifest_is_clearly_fictitious(tmp_path):
    header = build_example(tmp_path / "ej")
    data = json.loads((tmp_path / "ej" / "manifest.json").read_text(encoding="utf-8"))
    assert header["_AVISO"] == AVISO == data["dataset"]["_AVISO"]
    assert all(r["_ficticio"] and r["image_id"].startswith("EJEMPLO-") and r["card_id"] is None for r in data["photos"])
    assert (
        "_ficticio" in json.dumps(data)
        and len(list(csv.DictReader((tmp_path / "ej" / "manifest.csv").open(encoding="utf-8-sig")))) == 4
    )


def make_eval_dataset(ds, tmp_path, cards=6):
    refs = []
    for i in range(cards):
        ref = add_verified_card(ds, i + 1)
        cv2.imwrite(str(ds.references / f"{ref}.png"), fake_card(300 + i))  # referencia digital de la carta
        refs.append(ref)
        for sess, offset in (("s-test-a", 0), ("s-test-b", 1000)):
            folder = tmp_path / f"e-{ref}-{sess}"
            for k in range(2):
                path = folder / f"{k}.jpg"
                path.parent.mkdir(parents=True, exist_ok=True)
                scene, _ = make_scene(fake_card(300 + i), offset + i * 10 + k, NORMAL)
                cv2.imwrite(str(path), scene, [cv2.IMWRITE_JPEG_QUALITY, 90])
            import_ok(ds, folder, sess, ref)
        main(
            [
                "--root",
                str(ds.root),
                "review",
                "--card",
                ref,
                "--status",
                "verified",
                "--by",
                "t",
                "--accept-quality",
                "--accept-duplicates",
            ]
        )
    return refs


def test_evaluation_flow_is_labelled_exploratory_and_uses_only_the_test_split(ds, tmp_path):
    make_eval_dataset(ds, tmp_path)
    make_split(ds, "p", manual={"s-test-a": "calibration", "s-test-b": "test"})
    result = run_evaluation(ds, "p", {"phash": PHashExtractor(), "grid": GridExtractor()}, holdout_cards=2)
    assert result["banner"].startswith("EXPLORATORIO") and result["evidence"]["level"] == "exploratorio"
    assert any("solo 12 fotos de prueba" in r for r in result["evidence"]["reasons"])
    d = result["dataset"]
    assert (
        d["calibration_photos"] == 12
        and d["test_photos"] == 12
        and len(d["holdout_unknown_cards"]) == 2
        and d["gallery_cards"] == 4
    )
    g = result["extractors"]["grid"]
    assert g["queries_known"] == 8 and g["queries_unknown"] == 4
    assert (
        g["card_identity"]["n"] == 8
        and 0.0 <= g["card_identity"]["top1"] <= 1.0
        and len(g["card_identity"]["top1_ci95"]) == 2
    )
    assert g["printing_identity"]["n"] == 0 and g["variant_identity"]["n"] == 0  # sin ids verificados no se evalúan
    assert g["calibration"] is not None and set(g["rejection"]) == {
        "unknown_falsely_accepted",
        "known_falsely_rejected",
        "known_correct_and_accepted",
    }
    assert g["latency_ms"]["detect"]["p50"] > 0 and "lighting_condition" in g["breakdown_top1"]


def test_evaluation_refuses_fictitious_data_and_missing_references(ds, tmp_path):
    make_eval_dataset(ds, tmp_path, cards=2)
    make_split(ds, "p", manual={"s-test-a": "calibration", "s-test-b": "test"})
    photos = ds.photos
    next(iter(photos.values()))["_ficticio"] = True
    ds.save_photos(photos)
    with pytest.raises(DatasetError, match="FICTICIOS"):
        run_evaluation(ds, "p", {"grid": GridExtractor()})
    out = run_evaluation(ds, "p", {"grid": GridExtractor()}, allow_fictitious=True)
    assert out["evidence"]["is_real"] is False and out["banner"].startswith("EXPLORATORIO")
    for f in ds.references.glob("*"):
        f.unlink()
    with pytest.raises(DatasetError, match="referencias"):
        run_evaluation(ds, "p", {"grid": GridExtractor()}, allow_fictitious=True)


def test_photo_schema_accepts_unknowns_and_rejects_bad_values():
    base = {
        "image_id": "i", "capture_session_id": "s", "card_ref": None, "card_id": None, "printing_id": None, "variant_id": None,
        "lighting_condition": "desconocida", "camera_angle": "desconocido", "background": "desconocido", "sleeve_condition": "desconocida",
        "focus_quality": "desconocida", "glare": "desconocido", "verification_status": "pending",
        "file": {"path": "p", "sha256": "x", "width": 1, "height": 1},
    }  # fmt: skip
    assert schema.validate_photo(base) == []
    assert schema.validate_photo({**base, "lighting_condition": "luna"})
    assert schema.validate_photo({**base, "verification_status": "verified"})  # sin verified_by ni card_ref
    assert schema.validate_photo({**base, "printing_id": "inventado"})
    assert schema.validate_photo({**base, "card_corners": [[0, 0]]})
