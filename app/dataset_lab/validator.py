# ruff: noqa: E501
"""Validación de integridad del dataset (solo lectura): esquema, archivos, hashes, identidades y cobertura del piloto."""

from collections import Counter, defaultdict

from app.dataset_lab import schema
from app.dataset_lab.quality import sha256_file
from app.dataset_lab.store import Dataset

PILOT = {"cards": 30, "photos_per_card": 10, "sessions_per_card": 2}


def validate(ds: Dataset, check_hashes: bool = True) -> dict:
    cards, sessions, photos = ds.cards, ds.sessions, ds.photos
    errors: list[str] = []
    warnings: list[str] = []

    for ref, c in cards.items():
        errors += [f"carta {ref}: {e}" for e in schema.validate_card(c)]
    known_files = set()
    for pid, p in photos.items():
        errors += [f"foto {pid}: {e}" for e in schema.validate_photo(p)]
        if p["capture_session_id"] not in sessions:
            errors.append(f"foto {pid}: la sesión {p['capture_session_id']} no existe")
        card = cards.get(p.get("card_ref"))
        if p.get("card_ref") and card is None:
            errors.append(f"foto {pid}: la carta {p['card_ref']} no existe")
        if card:
            for f in schema.IDENTITY_FIELDS:
                if p.get(f) != card.get(f):
                    errors.append(f"foto {pid}: {f} no coincide con el registro de la carta (ejecuta card-verify/sync)")
            if p["verification_status"] == "verified" and card["verification_status"] != "verified":
                errors.append(f"foto {pid}: está verified pero la carta {p['card_ref']} no está verificada")
        path = ds.root / p["file"]["path"]
        known_files.add(path.resolve())
        if not path.exists():
            errors.append(f"foto {pid}: falta el archivo {p['file']['path']}")
        elif check_hashes and sha256_file(path) != p["file"]["sha256"]:
            errors.append(f"foto {pid}: el SHA-256 no coincide (el original fue alterado)")
        if p.get("possible_duplicates") and p["verification_status"] == "verified":
            warnings.append(f"foto {pid}: verified con posibles duplicados entre sesiones {p['possible_duplicates']}")
    if ds.originals.exists():
        orphans = [f for f in ds.originals.rglob("*") if f.is_file() and f.resolve() not in known_files]
        warnings += [
            f"archivo huérfano (no está en el manifiesto): {o.relative_to(ds.root).as_posix()}" for o in orphans
        ]

    per_card = defaultdict(list)
    for p in photos.values():
        if p["verification_status"] == "verified" and p.get("card_ref"):
            per_card[p["card_ref"]].append(p)
    verified_cards = [r for r, c in cards.items() if c["verification_status"] == "verified"]
    short = {r: len(per_card.get(r, [])) for r in verified_cards if len(per_card.get(r, [])) < PILOT["photos_per_card"]}
    one_session = [
        r
        for r in verified_cards
        if len({p["capture_session_id"] for p in per_card.get(r, [])}) < PILOT["sessions_per_card"]
    ]
    summary = {
        "cards_total": len(cards), "cards_verified": len(verified_cards), "sessions": len(sessions), "photos_total": len(photos),
        "photos_by_status": dict(Counter(p["verification_status"] for p in photos.values())),
        "verified_photos": sum(len(v) for v in per_card.values()),
        "pilot_target": PILOT,
        "pilot_cards_missing": max(0, PILOT["cards"] - len(verified_cards)),
        "cards_below_photo_target": short,
        "cards_with_fewer_than_2_sessions": one_session,
        "verified_photos_by_condition": {
            f: dict(Counter(p[f] for v in per_card.values() for p in v)) for f in ("lighting_condition", "background", "sleeve_condition", "camera_angle")
        },
        "unknown_conditions_in_verified": sum(1 for v in per_card.values() for p in v for f in schema.CONDITION_FIELDS if str(p[f]).startswith("desconoc")),
    }  # fmt: skip
    if verified_cards and (short or one_session):
        warnings.append("el piloto aún no cumple: hay cartas con menos fotos o sesiones de las previstas (ver summary)")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "summary": summary}
