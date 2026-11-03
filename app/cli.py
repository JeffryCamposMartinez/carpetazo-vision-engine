"""CLI de operaciones manuales: python -m app.cli <comando>."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.catalog.providers.tcgdex import ProviderError, TcgdexClient
from app.core.config import get_settings
from app.database.session import get_engine
from app.images.apply import apply_manifest, file_sha256, revert_associations
from app.images.inventory import (
    DEFAULT_PREFIX,
    InventoryRow,
    NumberRules,
    build_inventory,
    inventory_to_dict,
    load_data_jsons,
)
from app.images.matching import match_inventory, read_manifest_csv, summarize, write_manifest
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


def _load_aliases() -> dict[str, str]:
    return {k: v for k, v in _load_json(ALIASES).items() if not k.startswith("_")} if ALIASES.exists() else {}


def _resolve(args):
    inv = _load_json(Path(args.inventory))
    rows = [InventoryRow(**r) for r in inv["rows"]]
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
