"""Alias de sets R2 -> TCGdex (esquema v2). Solo cuentan los revisados por una persona.

relation:
- same_set: la carpeta completa es un set de TCGdex (único tipo que resuelve carpetas automáticamente).
- printing_variant: la carpeta es una edición/variante de impresión de un set (p. ej. Shadowless); se asocia a
  impresiones de variante y nunca se fusiona con la carpeta base.
- per_card: la carpeta mezcla cartas de varios sets; se resuelve objeto por objeto (ver object_mappings).
"""

import json
from pathlib import Path

STATUSES = ("proposed", "confirmed", "rejected")
RELATIONS = ("same_set", "printing_variant", "per_card")


def alias_entry(
    r2_folder: str,
    tcgdex_set_id: str | None,
    evidence: dict,
    conflicts: list[str] | None = None,
    relation: str = "same_set",
) -> dict:
    """Entrada de propuesta. reviewed_by/reviewed_at las llena una persona, nunca el código."""
    return {
        "r2_folder": r2_folder,
        "tcgdex_set_id": tcgdex_set_id,
        "relation": relation,
        "status": "proposed",
        "evidence": evidence,
        "conflicts": conflicts or [],
        "reviewed_by": None,
        "reviewed_at": None,
    }


def load_aliases(path: Path) -> tuple[dict[str, str], list[str]]:
    """Devuelve ({carpeta: set_id} de entradas same_set 'confirmed' con revisor y fecha, avisos)."""
    if not path.exists():
        return {}, []
    data = json.loads(path.read_text(encoding="utf-8"))
    aliases: dict[str, str] = {}
    warnings: list[str] = []
    for e in data.get("aliases", []):
        folder = e.get("r2_folder")
        if e.get("status") != "confirmed":
            continue
        if not (e.get("reviewed_by") and e.get("reviewed_at") and e.get("tcgdex_set_id")):
            warnings.append(f"alias '{folder}' marcado confirmed sin reviewed_by/reviewed_at/tcgdex_set_id: ignorado")
        elif e.get("relation", "same_set") != "same_set":
            warnings.append(f"alias '{folder}' ({e['relation']}) no resuelve la carpeta entera: se asocia por objeto")
        elif folder in aliases and aliases[folder] != e["tcgdex_set_id"]:
            warnings.append(f"alias '{folder}' duplicado con destinos distintos: ignorado")
            aliases.pop(folder)
        else:
            aliases[folder] = e["tcgdex_set_id"]
    return aliases, warnings
