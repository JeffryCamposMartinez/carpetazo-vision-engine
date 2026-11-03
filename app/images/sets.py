"""Resolución de carpetas de set de R2 a sets del catálogo. Un alias ambiguo nunca se resuelve solo."""

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher

from app.images.inventory import normalize_set_folder

PROBABLE_RATIO = 0.88
PROBABLE_GAP = 0.05


@dataclass
class SetResolution:
    folder: str
    images: int
    status: str  # confirmed | probable | ambiguous | unresolved
    set_id: str | None = None
    via: str | None = None  # alias | exact_name | fuzzy_name
    candidates: list = field(default_factory=list)  # [{id, name, score}]
    note: str | None = None


def resolve_sets(
    folders: dict[str, int], catalog_sets: list[dict], aliases: dict[str, str]
) -> dict[str, SetResolution]:
    by_id = {s["id"]: s for s in catalog_sets}
    by_norm: dict[str, list[dict]] = defaultdict(list)
    for s in catalog_sets:
        by_norm[normalize_set_folder(s["name"])].append(s)

    out: dict[str, SetResolution] = {}
    for folder, count in sorted(folders.items()):
        norm = normalize_set_folder(folder)
        scored = sorted(
            (
                {
                    "id": s["id"],
                    "name": s["name"],
                    "score": round(SequenceMatcher(None, norm, normalize_set_folder(s["name"])).ratio(), 3),
                }
                for s in catalog_sets
            ),
            key=lambda c: -c["score"],
        )[:3]
        res = SetResolution(folder, count, "unresolved", candidates=scored)
        if folder in aliases:
            target = aliases[folder]
            if target in by_id:
                res.status, res.set_id, res.via = "confirmed", target, "alias"
            else:
                res.note = f"el alias apunta a '{target}', que no existe en el catálogo"
        elif len(by_norm.get(norm, [])) == 1:
            res.status, res.set_id, res.via = "confirmed", by_norm[norm][0]["id"], "exact_name"
        elif len(by_norm.get(norm, [])) > 1:
            res.status, res.note = "ambiguous", "varios sets del catálogo tienen el mismo nombre normalizado"
            res.candidates = [{"id": s["id"], "name": s["name"], "score": 1.0} for s in by_norm[norm]]
        elif (
            scored
            and scored[0]["score"] >= PROBABLE_RATIO
            and (len(scored) < 2 or scored[0]["score"] - scored[1]["score"] >= PROBABLE_GAP)
        ):
            res.status, res.set_id, res.via = "probable", scored[0]["id"], "fuzzy_name"
            res.note = "coincidencia aproximada de nombre: confirmar con un alias"
        out[folder] = res
    return out


def sets_report(resolutions: dict[str, SetResolution], catalog_sets: list[dict]) -> dict:
    groups = defaultdict(list)
    for r in resolutions.values():
        if r.set_id:
            groups[r.set_id].append(r.folder)
    by_status = defaultdict(list)
    for r in resolutions.values():
        by_status[r.status].append(asdict(r))
    return {
        "catalog_sets": len(catalog_sets),
        "r2_folders": len(resolutions),
        "counts": {s: len(v) for s, v in by_status.items()},
        "images_by_status": {s: sum(r["images"] for r in v) for s, v in by_status.items()},
        "confirmed": by_status["confirmed"],
        "probable": by_status["probable"],
        "ambiguous": by_status["ambiguous"],
        "unresolved": by_status["unresolved"],
        "possible_duplicates_or_variants": {sid: f for sid, f in groups.items() if len(f) > 1},
        "unmapped_catalog_sets": [s["id"] for s in catalog_sets if s["id"] not in groups],
    }
