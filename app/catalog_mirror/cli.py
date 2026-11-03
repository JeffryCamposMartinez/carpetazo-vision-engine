# ruff: noqa: E501
"""Sincronización del catálogo de Carpetazo (opción A). Solo contra SQLite o el laboratorio privado (127.0.0.1:5498); sin valor por defecto de base de datos.

  plan   = --dry-run: pide solo la lista de ediciones (1 petición por categoría) y estima el resto; no escribe nada.
  sync   = sincroniza (paginado por edición, con pausas, reanudable).
  status = resumen del espejo y de las últimas corridas (solo lectura local).
"""

import argparse
import json
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.catalog_mirror.client import DEFAULT_BASE_URL, CarpetazoCatalogClient
from app.catalog_mirror.sync import POKEMON_CATEGORIES, SyncOptions, mirror_stats, plan, run_sync
from app.database.guard import DatabaseNotAllowed, assert_lab_database


def _client(a) -> CarpetazoCatalogClient:
    return CarpetazoCatalogClient(a.base_url, min_interval=a.min_interval, max_requests=getattr(a, "max_requests", None))


def _options(a) -> SyncOptions:
    return SyncOptions(tuple(a.categories), a.max_age_days, a.force, getattr(a, "resume", False), getattr(a, "max_minutes", None))


def cmd_plan(a) -> int:
    assert_lab_database(a.database_url)
    client = _client(a)
    try:
        with Session(create_engine(a.database_url)) as s:
            print(json.dumps(plan(s, client, _options(a)), ensure_ascii=False, indent=1))
    finally:
        client.close()
    return 0


def cmd_sync(a) -> int:
    if a.dry_run:
        return cmd_plan(a)
    assert_lab_database(a.database_url)
    client = _client(a)
    try:
        with Session(create_engine(a.database_url)) as s:
            rep = run_sync(s, client, _options(a))
    finally:
        client.close()
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    return 0 if rep["status"] == "ok" else 1


def cmd_status(a) -> int:
    assert_lab_database(a.database_url)
    with Session(create_engine(a.database_url)) as s:
        print(json.dumps(mirror_stats(s), ensure_ascii=False, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.catalog_mirror.cli")
    sub = p.add_subparsers(dest="command", required=True)

    def common(s, sync=False):
        s.add_argument("--database-url", required=True)
        s.add_argument("--base-url", default=DEFAULT_BASE_URL)
        s.add_argument("--categories", nargs="+", type=int, default=list(POKEMON_CATEGORIES), choices=list(POKEMON_CATEGORIES), help="alcance inicial: Pokémon 3 (inglés) y 100 (español)")
        s.add_argument("--min-interval", type=float, default=1.0, help="segundos mínimos entre peticiones (por defecto 1)")
        s.add_argument("--max-age-days", type=float, default=7.0)
        s.add_argument("--force", action="store_true", help="vuelve a descargar todas las ediciones")
        if sync:
            s.add_argument("--dry-run", action="store_true")
            s.add_argument("--resume", action="store_true", help="continúa la última corrida incompleta")
            s.add_argument("--max-requests", type=int)
            s.add_argument("--max-minutes", type=float)

    s = sub.add_parser("plan")
    common(s)
    s.set_defaults(func=cmd_plan)
    s = sub.add_parser("sync")
    common(s, sync=True)
    s.set_defaults(func=cmd_sync)
    s = sub.add_parser("status")
    s.add_argument("--database-url", required=True)
    s.set_defaults(func=cmd_status)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")  # la consola de Windows (cp1252) no puede imprimir todos los caracteres
    a = p.parse_args(argv)
    try:
        return a.func(a)
    except DatabaseNotAllowed as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
