# ruff: noqa: E501
"""Herramienta local del dataset de fotografías reales.  python -m app.dataset_lab.cli [--root RUTA] <comando>

Todo ocurre en disco local (por defecto datasets/real-photos). No sube nada a R2 ni a ningún servicio; solo `references-fetch`
LEE de R2 (GET) las imágenes de referencia que una persona indicó explícitamente.
"""

import argparse
import json
import random
import sys
from pathlib import Path

from app.dataset_lab import schema
from app.dataset_lab.example import build_example
from app.dataset_lab.exporter import backup_dataset, export_dataset
from app.dataset_lab.importer import import_folder
from app.dataset_lab.quality import BLOCKING_FLAGS, sha256_file
from app.dataset_lab.splits import make_split
from app.dataset_lab.store import DEFAULT_ROOT, Dataset, DatasetError, atomic_write
from app.dataset_lab.validator import validate

REPORTS = Path("evaluation/reports")
LAB_CACHE = Path("datasets/lab-cache/r2-sample")


def jprint(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def cmd_init(ds: Dataset, a) -> int:
    print("Dataset creado en" if ds.init() else "Ya existía:", ds.root)
    return 0


def cmd_session_add(ds: Dataset, a) -> int:
    jprint(ds.add_session(a.id, a.date, a.device, a.place, a.operator, a.notes, a.round))
    return 0


def cmd_card_add(ds: Dataset, a) -> int:
    jprint(
        ds.add_card(
            name=a.name,
            set_name=a.set,
            collector_number=a.number,
            language=a.language,
            finish=a.finish,
            categories=a.categories,
            pattern=a.pattern,
            variant_group=a.variant_group,
            similar_to=a.similar_to,
            difficulty_note=a.difficulty_note,
            pilot_slot=a.slot,
            tcgdex_card_id=a.tcgdex_id,
            card_id=a.card_id,
            printing_id=a.printing_id,
            variant_id=a.variant_id,
            reference_object_key=a.reference_object_key,
            condition_notes=a.condition_notes,
        )  # fmt: skip
    )
    return 0


def cmd_card_verify(ds: Dataset, a) -> int:
    jprint(ds.verify_card(a.card_ref, a.by, a.note))
    return 0


def cmd_card_reference(ds: Dataset, a) -> int:
    cards = ds.cards
    card = cards.get(a.card_ref)
    if card is None or card["verification_status"] != "verified":
        raise DatasetError("La carta debe existir y estar verificada antes de asignarle una referencia")
    card["reference_object_key"], card["reference_sha256"] = a.key, None
    for f in ("reference_verified_by", "reference_verified_at", "reference_verification_note"):
        card[f] = None
    ds.save_cards(cards)
    ds.verify_reference(a.card_ref, a.by, a.note)
    print(f"{a.card_ref}: referencia verificada por {a.by} (ejecuta references-fetch para traerla)")
    return 0


def cmd_card_list(ds: Dataset, a) -> int:
    for ref, c in sorted(ds.cards.items()):
        n = sum(1 for p in ds.photos.values() if p.get("card_ref") == ref)
        print(
            f"{ref}  {c['verification_status']:10} {';'.join(c['categories']):28} {c['name']} · {c['set_name']} {c['collector_number']} · {n} fotos"
        )
    return 0


def cmd_import(ds: Dataset, a) -> int:
    defaults = {"lighting_condition": a.lighting, "camera_angle": a.angle, "background": a.background, "sleeve_condition": a.sleeve, "glare": a.glare, "focus_quality": a.focus, "notes": a.notes}  # fmt: skip
    report = import_folder(
        ds,
        Path(a.source),
        a.session,
        a.card,
        defaults,
        a.recursive,
        a.dry_run,
        Path(a.sidecar) if a.sidecar else None,
        a.by,
    )
    jprint(report.to_dict())
    return 0 if report.imported or not report.skipped else 1


def cmd_review(ds: Dataset, a) -> int:
    photos, cards = ds.photos, ds.cards
    ids = list(a.image_ids)
    if a.card:
        ids += [
            i
            for i, p in photos.items()
            if p.get("card_ref") == a.card and p["verification_status"] in ("pending", "needs_review")
        ]
    if not ids:
        raise DatasetError("Indica image_id(s) o --card")
    changed = 0
    for i in ids:
        p = photos.get(i)
        if p is None:
            raise DatasetError(f"No existe la foto {i}")
        if a.status == "verified":
            if not a.by:
                raise DatasetError("verified exige --by")
            if cards.get(p.get("card_ref"), {}).get("verification_status") != "verified":
                raise DatasetError(f"{i}: la carta {p.get('card_ref')} no está verificada")
            blocking = BLOCKING_FLAGS & set(p["quality"]["flags"])
            if blocking and not a.accept_quality:
                raise DatasetError(
                    f"{i}: banderas de calidad {sorted(blocking)}; revisa la foto o usa --accept-quality"
                )
            if p.get("possible_duplicates") and not a.accept_duplicates:
                raise DatasetError(
                    f"{i}: posible duplicado entre sesiones {p['possible_duplicates']}; revisa o usa --accept-duplicates"
                )
            rnd = ds.sessions.get(p["capture_session_id"], {}).get("round")
            from app.dataset_lab.pilot import load_plan

            limit = load_plan(ds)["photos_per_round"]
            if rnd and p["verification_status"] != "verified":
                same_round = sum(
                    1
                    for q in photos.values()
                    if q.get("card_ref") == p.get("card_ref")
                    and q["verification_status"] == "verified"
                    and ds.sessions.get(q["capture_session_id"], {}).get("round") == rnd
                )
                if same_round >= limit:
                    raise DatasetError(
                        f"{i}: la carta {p.get('card_ref')} ya tiene {same_round} fotos verificadas en la ronda {rnd} (máximo {limit}); no se verifica otra"
                    )
        p["verification_status"] = a.status
        if a.status == "verified":
            p["verified_by"] = a.by
        for flag, field in (
            ("lighting", "lighting_condition"),
            ("angle", "camera_angle"),
            ("background", "background"),
            ("sleeve", "sleeve_condition"),
            ("glare", "glare"),
            ("focus", "focus_quality"),
        ):
            value = getattr(a, flag)
            if value:
                if value not in schema.ENUMS[field]:
                    raise DatasetError(f"{field}={value!r} no es válido ({schema.ENUMS[field]})")
                p[field] = value
                if field == "focus_quality":
                    p["focus_quality_source"] = "manual"
        if a.notes:
            p["notes"] = a.notes
        errors = schema.validate_photo(p)
        if errors:
            raise DatasetError(f"{i}: {errors}")
        changed += 1
    ds.save_photos(photos)
    print(f"{changed} fotos actualizadas a {a.status}")
    return 0


def cmd_validate(ds: Dataset, a) -> int:
    result = validate(ds, check_hashes=not a.no_hashes)
    jprint(result)
    return 0 if result["ok"] else 1


def cmd_pilot_gap(ds: Dataset, a) -> int:
    from app.dataset_lab.pilot import declare_gap

    jprint(declare_gap(ds, a.category, a.reason, a.by, a.slot))
    return 0


def cmd_pilot_status(ds: Dataset, a) -> int:
    from app.dataset_lab.pilot import render_markdown, status

    st = status(ds)
    if a.md:
        Path(a.md).parent.mkdir(parents=True, exist_ok=True)
        Path(a.md).write_text(render_markdown(st), encoding="utf-8")
    if a.json:
        jprint(st)
    else:
        print(st["verdict"])
        for c in st["checks"]:
            print(f"  {'OK ' if c['ok'] else 'FALTA'} {c['id']}: {c['detail']}")
        for c in st["coverage_by_category"]:
            print(
                f"  cobertura {c['category']:16} {c['verified']}/{c['target']} verificadas, {c['registered']} registradas -> {c['state']}"
            )
        if a.md:
            print("Informe Markdown:", a.md)
    return 0 if st["complete"] else 1


def cmd_pilot_import_cards(ds: Dataset, a) -> int:
    from app.dataset_lab.pilot import import_cards_csv

    result = import_cards_csv(ds, Path(a.csv), a.by, a.dry_run)
    jprint(result)
    return 1 if result["errors"] else 0


def cmd_pilot_import_sessions(ds: Dataset, a) -> int:
    from app.dataset_lab.pilot import import_sessions_csv

    result = import_sessions_csv(ds, Path(a.csv), a.dry_run)
    jprint(result)
    return 1 if result["errors"] else 0


def cmd_pilot_templates(ds: Dataset, a) -> int:
    from app.dataset_lab.pilot import write_templates

    for path in write_templates(Path(a.out)):
        print(path)
    return 0


def cmd_split(ds: Dataset, a) -> int:
    if a.by_round:
        from app.dataset_lab.splits import make_split_by_round

        result = make_split_by_round(ds, a.name)
        jprint({k: result[k] for k in ("name", "rule", "assignment", "coverage", "leaks")})
        return 1 if result["leaks"]["blocking"] else 0
    manual = dict(x.split("=", 1) for x in (a.assign or []))
    result = make_split(ds, a.name, a.calibration_fraction, a.seed, manual)
    jprint({k: result[k] for k in ("name", "assignment", "coverage", "leaks")})
    return 1 if result["leaks"]["blocking"] else 0


def cmd_export(ds: Dataset, a) -> int:
    jprint(export_dataset(ds, Path(a.out), a.split, a.include_pending, not a.no_images, a.allow_leaks))
    return 0


def cmd_backup(ds: Dataset, a) -> int:
    jprint(backup_dataset(ds, Path(a.dest)))
    return 0


def cmd_references_fetch(ds: Dataset, a) -> int:
    from app.core.config import get_settings
    from app.storage.factory import get_storage

    storage, cards, done = get_storage(get_settings()), ds.cards, []
    for ref, c in sorted(cards.items()):
        key = c.get("reference_object_key")
        if c["verification_status"] != "verified" or not key:
            continue
        dest = ds.references / f"{ref}{Path(key).suffix.lower()}"
        if not dest.exists():
            dest.write_bytes(storage.get(key, 3_000_000))  # GET de solo lectura de UN objeto indicado por una persona
        c["reference_sha256"] = sha256_file(dest)
        done.append({"card_ref": ref, "file": dest.name})
    ds.save_cards(cards)
    jprint({"references": done})
    return 0


def cmd_evaluate(ds: Dataset, a) -> int:
    from app.dataset_lab.evaluate import render_markdown, run_evaluation
    from app.recognition_lab.benchmark import load_bgr  # noqa: F401
    from app.recognition_lab.features import REGISTRY

    extractors = {}
    for name in a.extractor:
        if name in REGISTRY:
            extractors[name] = REGISTRY[name]()
        elif name == "dinov2":
            try:
                from app.recognition_lab.features import Dinov2Extractor

                extractors[name] = Dinov2Extractor("facebook/dinov2-small", local_only=True)
            except Exception as exc:  # sin torch / sin pesos en este entorno
                print(
                    f"dinov2 omitido: {type(exc).__name__}: {str(exc)[:120]} (ejecútalo en el entorno ML, ver docs/dataset/05)",
                    file=sys.stderr,
                )
        else:
            raise DatasetError(f"Extractor desconocido: {name}")
    distractors = []
    if a.distractors and (LAB_CACHE / "sample.json").exists():
        used = {c.get("reference_object_key") for c in ds.cards.values()}
        pool = [d for d in json.loads((LAB_CACHE / "sample.json").read_text(encoding="utf-8")) if d["key"] not in used]
        random.Random(a.seed).shuffle(pool)
        distractors = [{**d, "path": LAB_CACHE / d["cache"]} for d in pool[: a.distractors]]
    result = run_evaluation(
        ds, a.split, extractors, distractors, a.holdout_calibration, a.holdout_test, a.seed, a.allow_fictitious
    )
    out = REPORTS / f"real-photo-eval-{a.split}"
    REPORTS.mkdir(parents=True, exist_ok=True)
    atomic_write(out.with_suffix(".json"), result)
    out.with_suffix(".md").write_text(render_markdown(result), encoding="utf-8")
    print(result["banner"])
    for reason in result["evidence"]["reasons"]:
        print(" -", reason)
    print("Informe:", out.with_suffix(".json"))
    return 0


def cmd_example(ds: Dataset, a) -> int:
    jprint(build_example(Path(a.out)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.dataset_lab.cli")
    p.add_argument("--root", default=str(DEFAULT_ROOT), help="carpeta del dataset (por defecto datasets/real-photos)")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, func, **kw):
        s = sub.add_parser(name, **kw)
        s.set_defaults(func=func)
        return s

    add("init", cmd_init)
    s = add("session-add", cmd_session_add)
    s.add_argument("--id", required=True)
    s.add_argument("--date", required=True, help="AAAA-MM-DD")
    s.add_argument("--device")
    s.add_argument("--place")
    s.add_argument("--operator")
    s.add_argument("--notes")
    s.add_argument("--round", choices=schema.ROUNDS, help="A = calibración, B = evaluación final")
    s = add("card-add", cmd_card_add)
    s.add_argument("--name", required=True)
    s.add_argument("--set", required=True)
    s.add_argument("--number", required=True)
    s.add_argument("--language", default="unknown")
    s.add_argument("--finish", default="desconocido", choices=schema.FINISH)
    s.add_argument("--categories", nargs="+", choices=schema.CATEGORY, help="una o varias (se solapan)")
    s.add_argument(
        "--pattern", choices=schema.PATTERN, help="ninguno / pokeball / masterball (decláralo explícitamente)"
    )
    s.add_argument("--variant-group", help="agrupa la carta base y sus patrones del mismo número")
    s.add_argument("--similar-to", help="carta registrada o texto con la que se confunde")
    s.add_argument("--difficulty-note", help="por qué es difícil (obligatoria para similar_dificil)")
    s.add_argument("--slot", type=int, help="posición 1-30 de la plantilla")
    for opt in ("tcgdex-id", "card-id", "printing-id", "variant-id", "reference-object-key", "condition-notes"):
        s.add_argument(f"--{opt}")
    s = add("card-verify", cmd_card_verify)
    s.add_argument("card_ref")
    s.add_argument("--by", required=True)
    s.add_argument(
        "--note",
        required=True,
        help="qué se comprobó en la carta física (nombre, símbolo de set, número, idioma, acabado)",
    )
    s = add("card-reference", cmd_card_reference)
    s.add_argument("card_ref")
    s.add_argument(
        "--key", required=True, help="clave completa del objeto de R2 que una persona comparó con la carta física"
    )
    s.add_argument("--by", required=True, help="quién verificó la referencia")
    s.add_argument("--note", required=True, help="qué se comparó: ilustración, número, símbolo de set, patrón")
    add("card-list", cmd_card_list)
    s = add("import", cmd_import)
    s.add_argument("--from", dest="source", required=True)
    s.add_argument("--session", required=True)
    s.add_argument("--card", required=True)
    s.add_argument("--recursive", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--sidecar", help="CSV con filename y condiciones por archivo")
    s.add_argument("--by", help="quién importa")
    for opt, dest in (
        ("lighting", "lighting"),
        ("angle", "angle"),
        ("background", "background"),
        ("sleeve", "sleeve"),
        ("glare", "glare"),
        ("focus", "focus"),
        ("notes", "notes"),
    ):
        s.add_argument(f"--{opt}", dest=dest)
    s = add("review", cmd_review)
    s.add_argument("image_ids", nargs="*")
    s.add_argument("--card", help="aplica a todas las fotos pending/needs_review de esa carta")
    s.add_argument("--status", required=True, choices=schema.PHOTO_STATUS)
    s.add_argument("--by")
    s.add_argument("--accept-quality", action="store_true")
    s.add_argument("--accept-duplicates", action="store_true")
    for opt in ("lighting", "angle", "background", "sleeve", "glare", "focus", "notes"):
        s.add_argument(f"--{opt}", dest=opt)
    s = add("validate", cmd_validate)
    s.add_argument("--no-hashes", action="store_true")
    s = add("split", cmd_split)
    s.add_argument("--name", required=True)
    s.add_argument("--calibration-fraction", type=float, default=0.4)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--assign", action="append", help="sesion=calibration|test (repetible)")
    s.add_argument("--by-round", action="store_true", help="ronda A = calibración, ronda B = prueba (piloto)")
    s = add("pilot-gap", cmd_pilot_gap)
    s.add_argument("--category", required=True)
    s.add_argument("--reason", required=True, help="por qué falta (no se inventan cartas ni se sustituye)")
    s.add_argument("--by", required=True)
    s.add_argument("--slot", type=int)
    s = add("pilot-templates", cmd_pilot_templates)
    s.add_argument("--out", default="docs/dataset/piloto")
    s = add("pilot-status", cmd_pilot_status)
    s.add_argument("--md", help="escribe el informe Markdown en esta ruta")
    s.add_argument("--json", action="store_true")
    s = add("pilot-import-cards", cmd_pilot_import_cards)
    s.add_argument("--csv", required=True)
    s.add_argument("--by", required=True, help="quién declara las carencias (filas sin_carta_motivo)")
    s.add_argument("--dry-run", action="store_true")
    s = add("pilot-import-sessions", cmd_pilot_import_sessions)
    s.add_argument("--csv", required=True)
    s.add_argument("--dry-run", action="store_true")
    s = add("export", cmd_export)
    s.add_argument("--out", required=True)
    s.add_argument("--split")
    s.add_argument("--include-pending", action="store_true")
    s.add_argument("--no-images", action="store_true")
    s.add_argument("--allow-leaks", action="store_true")
    s = add("backup", cmd_backup)
    s.add_argument("--dest", required=True)
    add("references-fetch", cmd_references_fetch)
    s = add("evaluate", cmd_evaluate)
    s.add_argument("--split", required=True)
    s.add_argument("--extractor", nargs="+", default=["phash", "grid", "dinov2"])
    s.add_argument(
        "--holdout-calibration", type=int, default=0, help="cartas desconocidas para calibrar (solo sus fotos A)"
    )
    s.add_argument(
        "--holdout-test",
        type=int,
        default=0,
        help="cartas desconocidas para probar (solo sus fotos B); disjuntas de las anteriores",
    )
    s.add_argument("--distractors", type=int, default=0)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--allow-fictitious", action="store_true")
    s = add("example", cmd_example)
    s.add_argument("--out", default="docs/dataset/example")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(Dataset(Path(args.root)), args)
    except DatasetError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
