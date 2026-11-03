"""Alias de sets R2 -> TCGdex. Solo cuentan los revisados por una persona."""

import json
from pathlib import Path

STATUSES = ("proposed", "confirmed", "rejected")


def alias_entry(r2_folder: str, tcgdex_set_id: str | None, evidence: dict) -> dict:
    """Entrada de propuesta. reviewed_by/reviewed_at las llena una persona, nunca el código."""
    return {
        "r2_folder": r2_folder,
        "tcgdex_set_id": tcgdex_set_id,
        "status": "proposed",
        "evidence": evidence,
        "reviewed_by": None,
        "reviewed_at": None,
    }


def load_aliases(path: Path) -> tuple[dict[str, str], list[str]]:
    """Devuelve ({carpeta: set_id} de entradas 'confirmed' con revisor y fecha, avisos)."""
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
        elif folder in aliases and aliases[folder] != e["tcgdex_set_id"]:
            warnings.append(f"alias '{folder}' duplicado con destinos distintos: ignorado")
            aliases.pop(folder)
        else:
            aliases[folder] = e["tcgdex_set_id"]
    return aliases, warnings
