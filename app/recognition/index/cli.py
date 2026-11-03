# ruff: noqa: E501
"""Herramienta del índice de referencias (E4). Solo contra bases LOCALES de laboratorio: SQLite o PostgreSQL en 127.0.0.1:5498.

No hay valor por defecto de base de datos: se exige --database-url y se rechaza cualquier otro servidor (PostgreSQL original, producción...).
No lee ni escribe R2; las imágenes deben existir ya en disco.
"""

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database.guard import DatabaseNotAllowed, assert_lab_database  # noqa: F401  (re-exportados)
from app.database.reference_models import EMBEDDING_TABLES, CandidateReference, VerifiedReference
from app.dataset_lab.store import Dataset
from app.recognition.index.registry import MODELS
from app.recognition.index.store import import_verified_from_dataset
from app.recognition.normalization import NormalizationConfig


def _extractors(names: list[str]):
    from app.recognition.embedding.dinov2_client import Dinov2WorkerExtractor
    from app.recognition.embedding.grid import GridExtractor

    out = []
    for n in names:
        out.append(GridExtractor() if n == "grid" else Dinov2WorkerExtractor())
    return out


def cmd_import_verified(a) -> int:
    assert_lab_database(a.database_url)
    ds = Dataset(Path(a.dataset))
    ds.require()
    engine = create_engine(a.database_url)
    exts = _extractors(a.extractors)
    try:
        with Session(engine) as s:
            rep = import_verified_from_dataset(s, ds, exts, a.by, None if a.normalization == "raw" else NormalizationConfig(), a.dry_run)
    finally:
        for e in exts:
            e.close()
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    return 1 if rep["errors"] else 0


def cmd_stats(a) -> int:
    assert_lab_database(a.database_url)
    with Session(create_engine(a.database_url)) as s:
        out = {"verified_references": s.scalar(select(func.count()).select_from(VerifiedReference)), "candidate_references": s.scalar(select(func.count()).select_from(CandidateReference)), "embeddings": {}}
        for (tier, key), table in EMBEDDING_TABLES.items():
            out["embeddings"][f"{tier}:{key}"] = {"dim": MODELS[key].dim, "rows": s.scalar(select(func.count()).select_from(table))}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def cmd_compare(a) -> int:
    assert_lab_database(a.database_url)
    from app.recognition.index.compare import run_comparison

    result = run_comparison(create_engine(a.database_url), a.n, a.queries, a.dim, a.seed)
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(text, encoding="utf-8")
    print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.recognition.index.cli")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("import-verified", help="importa SOLO referencias verificadas del dataset del piloto (archivos locales)")
    s.add_argument("--database-url", required=True)
    s.add_argument("--dataset", default="datasets/real-photos")
    s.add_argument("--by", required=True)
    s.add_argument("--extractors", nargs="+", default=["grid"], choices=["grid", "dinov2"])
    s.add_argument("--normalization", default="raw", choices=["raw", "default"])
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_import_verified)
    s = sub.add_parser("stats")
    s.add_argument("--database-url", required=True)
    s.set_defaults(func=cmd_stats)
    s = sub.add_parser("compare-pgvector", help="comparación SINTÉTICA exacta frente a pgvector (tabla temporal)")
    s.add_argument("--database-url", required=True)
    s.add_argument("--n", type=int, default=28000)
    s.add_argument("--queries", type=int, default=200)
    s.add_argument("--dim", type=int, default=768)
    s.add_argument("--seed", type=int, default=11)
    s.add_argument("--report")
    s.set_defaults(func=cmd_compare)
    a = p.parse_args(argv)
    try:
        return a.func(a)
    except DatabaseNotAllowed as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
