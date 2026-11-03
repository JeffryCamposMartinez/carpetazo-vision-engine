"""CLI de operaciones manuales: python -m app.cli <comando>."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.catalog.importers.tcgdex import TcgdexImporter, configure_import_logging, render_report_md
from app.catalog.providers.tcgdex import ProviderError, TcgdexClient
from app.core.config import get_settings
from app.database.session import get_engine
from app.images.alias_audit import audit_alias, write_audit
from app.images.aliases import load_aliases
from app.images.apply import apply_manifest, file_sha256, revert_associations
from app.images.inventory import (
    DEFAULT_PREFIX,
    InventoryRow,
    NumberRules,
    build_inventory,
    inventory_to_dict,
    load_data_jsons,
    parse_card_folder,
)
from app.images.matching import match_inventory, read_manifest_csv, summarize, write_manifest
from app.images.proposals import load_proposals
from app.images.set_review import review_sets, write_review
from app.images.sets import resolve_sets, sets_report
from app.storage.base import StorageError
from app.storage.diagnose import diagnose, render_text
from app.storage.factory import get_storage

REPORTS = Path("evaluation/reports")
ALIASES = Path("evaluation/set_aliases.json")


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_storage_diagnose(args: argparse.Namespace) -> int:
    storage = get_storage(get_settings())
    report = diagnose(storage.list_objects(prefix=args.prefix, page_size=args.page_size), args.max_objects)
    print(render_text(report, storage.bucket))
    if args.json:
        _write_json(Path(args.json), report.to_dict())
        print(f"\nInforme JSON: {args.json}")
    return 0


def cmd_images_inventory(args: argparse.Namespace) -> int:
    storage = get_storage(get_settings())
    rows, data_keys = build_inventory(storage.list_objects(prefix=args.prefix), args.prefix)
    _write_json(Path(args.out), {"prefix": args.prefix, "rows": inventory_to_dict(rows), "data_json_keys": data_keys})
    kinds = Counter(r.kind for r in rows)
    set_folders = len({r.set_folder for r in rows if r.kind == "card"})
    print(f"Inventario: {dict(kinds)} · data.json: {len(data_keys)} · carpetas de set: {set_folders}")
    print(f"Escrito: {args.out}")
    return 0


def cmd_catalog_snapshot(args: argparse.Namespace) -> int:
    client = TcgdexClient(get_settings())
    snap = client.snapshot(args.language, progress=lambda m: print(m, file=sys.stderr))
    _write_json(Path(args.out), snap)
    print(f"Sets: {len(snap['sets'])} · cartas: {sum(len(s['cards']) for s in snap['sets'])} -> {args.out}")
    return 0


def cmd_catalog_variant_probe(args: argparse.Namespace) -> int:
    client = TcgdexClient(get_settings())
    probe = {"language": args.language, "cards": [client.card_variants(c, args.language) for c in args.card]}
    _write_json(Path(args.out), probe)
    print(f"Variantes consultadas: {len(probe['cards'])} cartas -> {args.out}")
    return 0


def cmd_catalog_import(args: argparse.Namespace) -> int:
    settings = get_settings()
    importer = TcgdexImporter(TcgdexClient(settings), sessionmaker(bind=get_engine(), expire_on_commit=False))
    configure_import_logging()
    try:
        report = importer.run(
            languages=tuple(args.language or ["en"]),
            set_ids=set(args.set) if args.set else None,
            max_cards=args.max_cards,
            resume=args.resume,
            dry_run=args.dry_run,
        )
    except KeyboardInterrupt:
        print("Importación interrumpida: reanuda con --resume y los mismos parámetros.", file=sys.stderr)
        return 130
    data = report.to_dict()
    out = Path(args.report_dir)
    _write_json(out / "tcgdex-import-report.json", data)
    (out / "tcgdex-import-report.md").write_text(render_report_md(data), encoding="utf-8")
    print(json.dumps({k: data[k] for k in ("status", "run_id", "entities", "external_references_created")}, indent=1))
    print(f"Errores: {len(data['errors'])} · duplicados: {len(data['duplicates'])} · reporte: {out}")
    return 0 if report.status in {"completed", "completed_with_errors"} else 1


def cmd_images_load_proposals(args: argparse.Namespace) -> int:
    settings = get_settings()
    with _session() as db:
        result = load_proposals(
            db,
            settings.s3_bucket or "",
            Path(args.manifest),
            Path(args.object_proposals) if args.object_proposals else None,
        )
        db.commit()
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0


def _load_aliases() -> dict[str, str]:
    aliases, warnings = load_aliases(ALIASES)
    for w in warnings:
        print(f"Aviso: {w}", file=sys.stderr)
    return aliases


def _resolve(args):
    inv = _load_json(Path(args.inventory))
    rows = [InventoryRow(**r) for r in inv["rows"]]
    for r in rows:
        if r.kind == "card":  # el parseo se deriva de la carpeta: así se aplica siempre la versión vigente
            r.parsed = parse_card_folder(r.card_folder)
    snap = _load_json(Path(args.snapshot))
    folders = Counter(r.set_folder for r in rows if r.kind == "card")
    res = resolve_sets(dict(folders), snap["sets"], _load_aliases())
    return inv, rows, snap, res


def cmd_images_sets(args: argparse.Namespace) -> int:
    _, _, snap, res = _resolve(args)
    report = sets_report(res, snap["sets"])
    _write_json(Path(args.out), report)
    print(json.dumps({k: report[k] for k in ("catalog_sets", "r2_folders", "counts", "images_by_status")}, indent=1))
    print(f"Escrito: {args.out}")
    return 0


def cmd_images_sets_review(args: argparse.Namespace) -> int:
    _, rows, snap, res = _resolve(args)
    raw = _load_json(Path(args.data_json_cache)) if Path(args.data_json_cache).exists() else {}
    data_json = {tuple(k.split("")): v for k, v in raw.items()}
    unresolved = [f for f, r in res.items() if r.status == "unresolved"]
    claimed: dict[str, list[str]] = {}
    for folder, r in res.items():
        if r.set_id:
            claimed.setdefault(r.set_id, []).append(folder)
    review = review_sets(rows, snap["sets"], unresolved, data_json, claimed=claimed)
    paths = write_review(review, Path(args.out_dir))
    print(json.dumps(review["summary"], indent=1, ensure_ascii=False))
    print("Escrito:", ", ".join(str(p) for p in paths))
    return 0


def cmd_images_alias_audit(args: argparse.Namespace) -> int:
    _, rows, snap, res = _resolve(args)
    raw = _load_json(Path(args.data_json_cache)) if Path(args.data_json_cache).exists() else {}
    data_json = {tuple(k.split("")): v for k, v in raw.items()}
    unresolved = [f for f, r in res.items() if r.status == "unresolved"]
    claimed: dict[str, list[str]] = {}
    for folder, r in res.items():
        if r.set_id:
            claimed.setdefault(r.set_id, []).append(folder)
    review = review_sets(rows, snap["sets"], unresolved, data_json, claimed=claimed)
    probe_path = Path(args.variant_probe)
    probe = _load_json(probe_path) if probe_path.exists() else None
    audit = audit_alias(rows, snap["sets"], res, review, probe)
    paths = write_audit(audit, Path(args.out_dir))
    print(json.dumps(audit["summary"], indent=1, ensure_ascii=False))
    print("Escrito:", ", ".join(str(p) for p in paths))
    return 0


def cmd_images_manifest(args: argparse.Namespace) -> int:
    inv, rows, snap, res = _resolve(args)
    cache = Path(args.data_json_cache)
    if cache.exists():
        raw = _load_json(cache)
        data_json = {tuple(k.split("\x1f")): v for k, v in raw.items()}
    else:
        data_json = load_data_jsons(get_storage(get_settings()), inv["data_json_keys"], inv["prefix"])
        _write_json(cache, {"\x1f".join(k): v for k, v in data_json.items()})
    manifest = match_inventory(rows, res, snap["sets"], data_json, NumberRules())
    jpath, cpath = write_manifest(manifest, Path(args.out_dir), "r2-manifest")
    _write_json(Path(args.out_dir) / "r2-sets-report.json", sets_report(res, snap["sets"]))
    print("Estados:", summarize(manifest))
    print(f"Escrito: {jpath}, {cpath}")
    return 0


def _session():
    return sessionmaker(bind=get_engine(), expire_on_commit=False)()


def cmd_images_apply(args: argparse.Namespace) -> int:
    settings = get_settings()
    path = Path(args.manifest)
    rows = read_manifest_csv(path)
    with _session() as db:
        result = apply_manifest(db, rows, settings.s3_bucket or "", file_sha256(path), args.approved_by)
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0


def cmd_images_revert(args: argparse.Namespace) -> int:
    settings = get_settings()
    sha = file_sha256(Path(args.manifest)) if args.manifest else None
    with _session() as db:
        n = revert_associations(db, settings.s3_bucket or "", args.approved_by, args.object_key or None, sha)
        db.commit()
    print(f"Asociaciones revertidas: {n} (los objetos de R2 no se tocaron)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, func, help_):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(func=func)
        return p

    p = add("storage-diagnose", cmd_storage_diagnose, "Lista el almacenamiento (solo lectura) y resume su estructura")
    p.add_argument("--prefix", default="")
    p.add_argument("--page-size", type=int, default=1000)
    p.add_argument("--max-objects", type=int, default=500_000)
    p.add_argument("--json")

    p = add("images-inventory", cmd_images_inventory, "Inventario de imágenes Pokémon de R2 (solo lectura)")
    p.add_argument("--prefix", default=DEFAULT_PREFIX)
    p.add_argument("--out", default=str(REPORTS / "r2-inventory.json"))

    p = add("catalog-snapshot", cmd_catalog_snapshot, "Copia de sets y cartas de TCGdex a un JSON (solo red)")
    p.add_argument("--language", default="en")
    p.add_argument("--out", default=str(REPORTS / "tcgdex-en-snapshot.json"))

    for name, func, help_ in (
        ("images-sets", cmd_images_sets, "Reporte de resolución de carpetas de set"),
        ("images-manifest", cmd_images_manifest, "Genera el manifiesto de revisión (no toca la base de datos)"),
    ):
        p = add(name, func, help_)
        p.add_argument("--inventory", default=str(REPORTS / "r2-inventory.json"))
        p.add_argument("--snapshot", default=str(REPORTS / "tcgdex-en-snapshot.json"))
        if name == "images-sets":
            p.add_argument("--out", default=str(REPORTS / "r2-sets-report.json"))
        else:
            p.add_argument("--out-dir", default=str(REPORTS))
            p.add_argument("--data-json-cache", default=str(REPORTS / "r2-datajson.json"))

    p = add("catalog-variant-probe", cmd_catalog_variant_probe, "Consulta las variantes de impresión de unas cartas")
    p.add_argument("--card", action="append", required=True)
    p.add_argument("--language", default="en")
    p.add_argument("--out", default=str(REPORTS / "tcgdex-variant-probe.json"))

    p = add("images-sets-review", cmd_images_sets_review, "Revisión humana de carpetas de set sin resolver")
    p.add_argument("--inventory", default=str(REPORTS / "r2-inventory.json"))
    p.add_argument("--snapshot", default=str(REPORTS / "tcgdex-en-snapshot.json"))
    p.add_argument("--data-json-cache", default=str(REPORTS / "r2-datajson.json"))
    p.add_argument("--out-dir", default=str(REPORTS))

    p = add("images-alias-audit", cmd_images_alias_audit, "Auditoría de las propuestas de alias (no confirma nada)")
    p.add_argument("--inventory", default=str(REPORTS / "r2-inventory.json"))
    p.add_argument("--snapshot", default=str(REPORTS / "tcgdex-en-snapshot.json"))
    p.add_argument("--data-json-cache", default=str(REPORTS / "r2-datajson.json"))
    p.add_argument("--variant-probe", default=str(REPORTS / "tcgdex-variant-probe.json"))
    p.add_argument("--out-dir", default=str(REPORTS))

    p = add("catalog-import", cmd_catalog_import, "Importa el catálogo de TCGdex a PostgreSQL (sin imágenes)")
    p.add_argument("--language", action="append", help="Idioma del catálogo; repetible. Por defecto: en")
    p.add_argument("--set", action="append", help="Id de set de TCGdex; repetible")
    p.add_argument("--max-cards", type=int, help="Limita las cartas por set (pruebas)")
    p.add_argument(
        "--resume", action="store_true", help="Continúa la última importación incompleta con el mismo alcance"
    )
    p.add_argument("--dry-run", action="store_true", help="Hace todo dentro de una transacción y la revierte")
    p.add_argument("--report-dir", default=str(REPORTS))

    p = add(
        "images-load-proposals", cmd_images_load_proposals, "Carga propuestas de asociación R2 (no crea asociaciones)"
    )
    p.add_argument("--manifest", default=str(REPORTS / "r2-manifest.json"))
    p.add_argument("--object-proposals", default=str(REPORTS / "r2-object-mapping-proposals.json"))

    p = add("images-apply", cmd_images_apply, "Aplica un manifiesto CSV aprobado (columna approved=yes)")
    p.add_argument("--manifest", required=True)
    p.add_argument("--approved-by", required=True)
    p.add_argument("--dry-run", action="store_true")

    p = add("images-revert", cmd_images_revert, "Revierte asociaciones (sin tocar R2)")
    p.add_argument("--approved-by", required=True)
    p.add_argument("--manifest")
    p.add_argument("--object-key", action="append")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (StorageError, ProviderError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
