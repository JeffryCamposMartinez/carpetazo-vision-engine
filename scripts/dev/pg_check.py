# ruff: noqa: E501
"""Diagnóstico de PostgreSQL y pgvector para VISION_DATABASE_URL (solo lectura).

Uso: python scripts/dev/pg_check.py [--expect-head]
Sale con código 1 si falta algo imprescindible (conexión, extensión vector disponible, migraciones).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from app.core.config import get_settings  # noqa: E402

EXPECTED_TABLES = {
    "games", "catalog_sources", "sets", "cards", "card_printings", "printing_variants", "external_references",
    "card_equivalences", "card_embeddings", "card_images", "image_associations", "image_mapping_proposals",
    "import_runs", "import_run_items", "import_errors",
}  # fmt: skip
EXPECTED_INDEXES = {
    "ix_extref_lookup",
    "ix_variants_printing",
    "ix_proposals_outcome",
    "ix_cards_name",
    "ix_printings_name",
}
HEAD = "0004"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-head", action="store_true", help="exige que las migraciones estén aplicadas")
    args = parser.parse_args()

    url = get_settings().database_url
    engine = create_engine(url)
    print(f"Destino: {engine.url.render_as_string(hide_password=True)}")
    problems: list[str] = []
    try:
        with engine.connect() as c:
            print("Servidor:", c.scalar(text("SELECT version()")).split(",")[0])
            available = c.execute(
                text("SELECT default_version, installed_version FROM pg_available_extensions WHERE name = 'vector'")
            ).first()
            if available is None:
                print("pgvector: NO está instalado en el servidor (falta vector.control en la carpeta de extensiones)")
                problems.append("pgvector no instalado en el servidor: ver docs/postgres-windows.md, sección pgvector")
            elif available.installed_version is None:
                print(f"pgvector: disponible (v{available.default_version}) pero NO activado en esta base")
                problems.append("falta `CREATE EXTENSION vector;` en esta base (como superusuario)")
            else:
                print(f"pgvector: activado (v{available.installed_version})")

            insp = inspect(c)
            tables = set(insp.get_table_names())
            if "alembic_version" in tables:
                version = c.scalar(text("SELECT version_num FROM alembic_version"))
                print(f"Migraciones: {version} (head esperado {HEAD})")
                if args.expect_head and version != HEAD:
                    problems.append(f"migraciones en {version}, se esperaba {HEAD}: ejecuta scripts\\dev\\migrate.ps1")
            else:
                print("Migraciones: ninguna aplicada todavía")
                if args.expect_head:
                    problems.append("sin migraciones: ejecuta scripts\\dev\\migrate.ps1")
            missing = sorted(EXPECTED_TABLES - tables)
            print(
                f"Tablas: {len(tables & EXPECTED_TABLES)}/{len(EXPECTED_TABLES)}"
                + (f" · faltan {missing}" if missing else "")
            )
            if args.expect_head and missing:
                problems.append(f"faltan tablas: {missing}")
            if not missing:
                found = {i["name"] for t in EXPECTED_TABLES for i in insp.get_indexes(t)}
                lost = sorted(EXPECTED_INDEXES - found)
                print("Índices clave:", "todos presentes" if not lost else f"faltan {lost}")
                if lost:
                    problems.append(f"faltan índices: {lost}")
    except SQLAlchemyError as exc:
        print(f"No se pudo conectar: {type(exc).__name__}: {str(exc).splitlines()[0]}")
        problems.append(
            "sin conexión: revisa VISION_DATABASE_URL, que el servicio de PostgreSQL esté activo y el usuario/base existan"
        )

    if problems:
        print("\nPendiente:")
        print("\n".join(f" - {p}" for p in problems))
        return 1
    print("\nTodo en orden.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
