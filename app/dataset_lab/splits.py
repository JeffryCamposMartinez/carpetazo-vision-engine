# ruff: noqa: E501
"""Particiones sin fuga: la unidad es la SESIÓN de captura. Todas las fotos de una sesión van al mismo conjunto."""

import hashlib
import json
import math
from collections import Counter, defaultdict

from app.dataset_lab.quality import near_duplicate
from app.dataset_lab.store import Dataset, DatasetError, atomic_write, now

SPLITS = ("calibration", "test")


def eligible_sessions(ds: Dataset) -> list[str]:
    cards = ds.cards
    return sorted(
        {
            p["capture_session_id"]
            for p in ds.photos.values()
            if p["verification_status"] == "verified"
            and cards.get(p.get("card_ref"), {}).get("verification_status") == "verified"
        }
    )


def leak_report(ds: Dataset, assignment: dict[str, str]) -> dict:
    """Busca cuasi-duplicados de la misma carta entre fotos de conjuntos distintos (la misma toma guardada dos veces)."""
    photos = [
        p
        for p in ds.photos.values()
        if p["capture_session_id"] in assignment and p["verification_status"] == "verified"
    ]
    by_card = defaultdict(list)
    for p in photos:
        by_card[p["card_ref"]].append(p)
    pairs = []
    for ref, group in by_card.items():
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                if assignment[a["capture_session_id"]] != assignment[b["capture_session_id"]] and near_duplicate(
                    a["file"]["dhash"], b["file"]["dhash"]
                ):
                    pairs.append(
                        {
                            "card_ref": ref,
                            "a": a["image_id"],
                            "b": b["image_id"],
                            "sessions": [a["capture_session_id"], b["capture_session_id"]],
                        }
                    )
    return {"near_duplicate_pairs_across_splits": pairs, "blocking": bool(pairs)}


def coverage(ds: Dataset, assignment: dict[str, str]) -> dict:
    out = {}
    for split in SPLITS:
        ps = [
            p
            for p in ds.photos.values()
            if assignment.get(p["capture_session_id"]) == split and p["verification_status"] == "verified"
        ]
        per_card = Counter(p["card_ref"] for p in ps)
        out[split] = {"sessions": sorted(s for s, v in assignment.items() if v == split), "photos": len(ps), "cards": len(per_card),
                      "cards_with_3_or_more_photos": sum(1 for n in per_card.values() if n >= 3)}  # fmt: skip
    return out


def make_split(
    ds: Dataset, name: str, calibration_fraction: float = 0.4, seed: int = 7, manual: dict[str, str] | None = None
) -> dict:
    sessions = eligible_sessions(ds)
    if len(sessions) < 2:
        raise DatasetError(
            f"Hacen falta al menos 2 sesiones con fotos verificadas para separar calibración y prueba (hay {len(sessions)})"
        )
    manual = manual or {}
    for s, v in manual.items():
        if s not in sessions or v not in SPLITS:
            raise DatasetError(
                f"Asignación manual inválida: {s}={v} (sesiones elegibles: {sessions}; conjuntos: {SPLITS})"
            )
    free = sorted(
        (s for s in sessions if s not in manual), key=lambda s: hashlib.sha256(f"{seed}:{s}".encode()).hexdigest()
    )
    n_cal = max(
        0,
        min(
            len(free),
            math.ceil(calibration_fraction * len(sessions)) - sum(1 for v in manual.values() if v == "calibration"),
        ),
    )
    assignment = dict(manual)
    for i, s in enumerate(free):
        assignment[s] = "calibration" if i < n_cal else "test"
    if len(set(assignment.values())) < 2:
        # garantizar al menos una sesión en cada conjunto
        last = sorted(free)[-1] if free else sorted(assignment)[-1]
        assignment[last] = "test" if "test" not in assignment.values() else "calibration"
    result = {
        "name": name, "created_at": now(), "seed": seed, "unit": "capture_session",
        "rule": "SHA-256(seed:session) ordena las sesiones; las primeras ceil(fracción×N) van a calibración, el resto a prueba",
        "assignment": dict(sorted(assignment.items())), "coverage": coverage(ds, assignment), "leaks": leak_report(ds, assignment),
    }  # fmt: skip
    atomic_write(ds.splits_dir / f"{name}.json", result)
    return result


def make_split_by_round(ds: Dataset, name: str) -> dict:
    """Ronda A -> calibración, ronda B -> prueba. Cada sesión con fotos verificadas debe tener ronda."""
    sessions = ds.sessions
    used = eligible_sessions(ds)
    no_round = [s for s in used if not sessions[s].get("round")]
    if no_round:
        raise DatasetError(f"Sesiones con fotos verificadas sin ronda A/B: {no_round} (corrige la sesión)")
    mapping = {"A": "calibration", "B": "test"}
    assignment = {s: mapping[sessions[s]["round"]] for s in used}
    if set(assignment.values()) != {"calibration", "test"}:
        raise DatasetError("Hace falta al menos una sesión de la ronda A y una de la ronda B con fotos verificadas")
    result = {
        "name": name, "created_at": now(), "seed": None, "unit": "capture_session",
        "rule": "ronda A = calibración, ronda B = evaluación final (sin azar)",
        "assignment": dict(sorted(assignment.items())), "coverage": coverage(ds, assignment), "leaks": leak_report(ds, assignment),
    }  # fmt: skip
    atomic_write(ds.splits_dir / f"{name}.json", result)
    return result


def load_split(ds: Dataset, name: str) -> dict:
    path = ds.splits_dir / f"{name}.json"
    if not path.exists():
        raise DatasetError(f"No existe la partición {name}")
    return json.loads(path.read_text(encoding="utf-8"))
