# ruff: noqa: E501
"""Muestra estratificada y reproducible de imágenes de referencia de R2 (solo lectura).

La identidad usada aquí sale del propio nombre del objeto en R2 (carpeta de set + número + nombre); NO se usan las
asociaciones con TCGdex pendientes de revisión como verdad de referencia.
"""

import hashlib
import json
import random
import re
from collections import defaultdict
from pathlib import Path

from app.images.inventory import InventoryRow, normalize_name, normalize_number
from app.storage.base import ObjectStorage

OLD_SET_MARKERS = ("base set", "jungle", "fossil", "team rocket", "gym ", "neo ", "aquapolis", "skyridge", "expedition",
                   "legendary collection")  # fmt: skip
DEFAULT_SIZES = {"common": 100, "full_art_secret": 60, "old_sets": 60, "promos": 60, "pattern_variants": 25,
                 "same_name_reprints": 20}  # fmt: skip


def card_key(r: InventoryRow) -> str:
    """Identidad de carta según R2: set + número + nombre (sin calificadores de variante)."""
    return f"{r.set_folder}|{normalize_number(r.parsed.get('local') or '')}|{normalize_name(r.parsed['name'])}"


def cache_name(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:16] + ".webp"


def select_sample(rows: list[InventoryRow], seed: int = 7, sizes: dict | None = None) -> list[dict]:
    sizes = {**DEFAULT_SIZES, **(sizes or {})}
    rng = random.Random(seed)
    cards = sorted((r for r in rows if r.kind == "card" and r.extension == "webp"), key=lambda r: r.object_key)
    picked: dict[str, dict] = {}

    def take(stratum: str, items: list[InventoryRow]) -> None:
        for r in items:
            if r.object_key not in picked:
                picked[r.object_key] = {
                    "key": r.object_key, "stratum": stratum, "set_folder": r.set_folder, "name": r.parsed["name"],
                    "number": r.parsed.get("local"), "qualifiers": r.parsed.get("qualifiers", []), "etag": r.etag,
                    "size_bytes": r.size, "card_key": card_key(r), "cache": cache_name(r.object_key),
                }  # fmt: skip

    def pick(pool: list[InventoryRow], n: int) -> list[InventoryRow]:
        return rng.sample(pool, min(n, len(pool)))

    plain = [r for r in cards if not r.parsed.get("qualifiers") and r.parsed.get("local")]
    lowered = {r.object_key: (r.set_folder or "").lower() for r in cards}

    # 1. variantes de patrón: la carta base + sus versiones Poké Ball / Master Ball (mismo set, número y nombre)
    groups: dict[str, list[InventoryRow]] = defaultdict(list)
    for r in cards:
        if r.parsed.get("local"):
            groups[card_key(r)].append(r)
    pattern_groups = [
        g for g in (groups[k] for k in sorted(groups))
        if any(not x.parsed["qualifiers"] for x in g) and any("ball pattern" in " ".join(x.parsed["qualifiers"]).lower() for x in g)
    ]  # fmt: skip
    for g in rng.sample(pattern_groups, min(sizes["pattern_variants"], len(pattern_groups))):
        take("pattern_variants", sorted(g, key=lambda x: x.object_key))

    # 2. mismo Pokémon en varios sets (ilustraciones parecidas)
    by_name: dict[str, dict[str, InventoryRow]] = defaultdict(dict)
    for r in plain:
        by_name[normalize_name(r.parsed["name"])].setdefault(r.set_folder, r)
    multi = [sorted(v.values(), key=lambda x: x.object_key) for k, v in sorted(by_name.items()) if len(v) >= 3]
    for g in rng.sample(multi, min(sizes["same_name_reprints"], len(multi))):
        take("same_name_reprints", rng.sample(g, 3))

    fancy = [
        r for r in cards if re.search(r"full art|secret|alternate", " ".join(r.parsed.get("qualifiers", [])), re.I)
    ]
    take("full_art_secret", pick(fancy, sizes["full_art_secret"]))
    take("promos", pick([r for r in plain if "promo" in lowered[r.object_key]], sizes["promos"]))
    take(
        "old_sets",
        pick([r for r in plain if any(m in lowered[r.object_key] for m in OLD_SET_MARKERS)], sizes["old_sets"]),
    )
    rest = [
        r
        for r in plain
        if "promo" not in lowered[r.object_key] and not any(m in lowered[r.object_key] for m in OLD_SET_MARKERS)
    ]
    take("common", pick(rest, sizes["common"]))
    return sorted(picked.values(), key=lambda d: d["key"])


def download_sample(storage: ObjectStorage, items: list[dict], cache_dir: Path, max_bytes: int = 3_000_000) -> dict:
    """GET de solo lectura de cada objeto de la muestra (se salta lo ya descargado)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    fetched = skipped = total = 0
    for it in items:
        path = cache_dir / it["cache"]
        if path.exists():
            skipped += 1
        else:
            path.write_bytes(storage.get(it["key"], max_bytes))
            fetched += 1
        total += path.stat().st_size
    (cache_dir / "sample.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    return {
        "images": len(items),
        "downloaded_now": fetched,
        "already_cached": skipped,
        "total_mb": round(total / 1_048_576, 1),
    }
