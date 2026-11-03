# ruff: noqa: E501
"""Laboratorio de reconocimiento (experimental, aislado de la API). python -m app.recognition_lab.cli <comando>"""

import argparse
import json
import sys
from pathlib import Path

from app.core.config import get_settings
from app.images.inventory import InventoryRow, parse_card_folder
from app.recognition_lab.audit import audit_sample, inventory_duplicates
from app.recognition_lab.sampling import download_sample, select_sample
from app.storage.factory import get_storage

REPORTS = Path("evaluation/reports")
CACHE = Path("datasets/lab-cache/r2-sample")


def load_rows(path: Path) -> list[InventoryRow]:
    rows = [InventoryRow(**r) for r in json.loads(path.read_text(encoding="utf-8"))["rows"]]
    for r in rows:
        if r.kind == "card":
            r.parsed = parse_card_folder(r.card_folder)
    return rows


def cmd_sample(args: argparse.Namespace) -> int:
    rows = load_rows(Path(args.inventory))
    items = select_sample(rows, seed=args.seed)
    est = sum(i["size_bytes"] for i in items) / 1_048_576
    print(
        f"Muestra: {len(items)} imágenes, ≈{est:.1f} MB; por estrato:",
        {s: sum(1 for i in items if i["stratum"] == s) for s in sorted({i["stratum"] for i in items})},
    )
    if args.dry_run:
        return 0
    print(download_sample(get_storage(get_settings()), items, CACHE))
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    rows = load_rows(Path(args.inventory))
    items = json.loads((CACHE / "sample.json").read_text(encoding="utf-8"))
    result = {"inventory_duplicates": inventory_duplicates(rows), "sample": audit_sample(items, CACHE)}
    out = REPORTS / "recognition-r2-audit.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    s = result["sample"]
    print(
        json.dumps(
            {k: v for k, v in s.items() if k != "per_image" and k != "aspect_outside_ratio"},
            ensure_ascii=False,
            indent=1,
        )
    )
    print(
        "Duplicados exactos (ETag) en todo el inventario:",
        json.dumps(result["inventory_duplicates"], ensure_ascii=False),
    )
    print(f"Escrito: {out}")
    return 0


def cmd_benchmark(args: argparse.Namespace) -> int:
    from app.recognition_lab.benchmark import run_benchmark
    from app.recognition_lab.features import REGISTRY

    items = json.loads((CACHE / "sample.json").read_text(encoding="utf-8"))
    report = {"seed": args.seed, "sample_images": len(items), "results": {}}
    for name in args.extractor:
        if name not in REGISTRY:
            print(
                f"Extractor '{name}' no disponible sin instalar torch/transformers y descargar pesos (no autorizado todavía)."
            )
            continue
        print(f"== {name}")
        report["results"][name] = run_benchmark(
            items, CACHE, REGISTRY[name](), seed=args.seed, max_queries=args.max_queries
        )
    out = REPORTS / "recognition-lab-benchmark.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    print(f"Escrito: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.recognition_lab.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("sample", cmd_sample), ("audit", cmd_audit)):
        p = sub.add_parser(name)
        p.add_argument("--inventory", default=str(REPORTS / "r2-inventory.json"))
        p.set_defaults(func=func)
        if name == "sample":
            p.add_argument("--seed", type=int, default=7)
            p.add_argument("--dry-run", action="store_true", help="solo muestra la selección y el tamaño estimado")
    p = sub.add_parser("benchmark")
    p.add_argument("--extractor", action="append", default=None)
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--max-queries", type=int, default=240)
    p.set_defaults(func=cmd_benchmark)
    args = parser.parse_args(argv)
    if args.command == "benchmark" and not args.extractor:
        args.extractor = ["phash", "grid"]
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
