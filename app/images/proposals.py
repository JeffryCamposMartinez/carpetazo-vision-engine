# ruff: noqa: E501
"""Carga de propuestas de asociación objeto R2 -> carta. Solo escribe image_mapping_proposals.

Las propuestas nunca se convierten en asociaciones aprobadas aquí (eso lo hace images-apply con aprobación humana).
Un alias de carpeta tampoco decide la impresión: cada objeto lleva sus candidatos y banderas.
"""

import json
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Card, ImageMappingProposal

OUTCOMES = ("unique_candidate", "multiple_candidates", "no_candidate")


def _classify(row: dict) -> tuple[str, list[str], list[str]]:
    """(outcome, candidatos, banderas) de una fila del manifiesto."""
    flags = ["unknown_language"]  # el idioma de las imágenes de R2 no está verificado
    cid = row.get("catalog_card_id") or ""
    candidates = [cid] if cid else []
    status = row.get("status")
    if row.get("qualifiers"):
        flags.append("variant_needed")  # Prerelease, Master Ball Pattern...: no debe ir a la impresión base
    if row.get("set_resolution") == "probable":
        flags.append("set_alias_not_confirmed")
    if status in {"matched", "probable"} and cid:
        outcome = "unique_candidate"
        if status == "probable":
            flags.append("probable_match")
    elif status == "ambiguous":
        outcome = "multiple_candidates"
        if "apuntan a" in (row.get("reason") or "") or "variante" in (row.get("reason") or ""):
            flags.append("duplicate_target")
        if not cid:
            flags.append("set_unresolved")
    else:
        outcome = "no_candidate"
        flags.append("set_unresolved" if "set " in (row.get("reason") or "") else "no_number_match")
    return outcome, candidates, flags


def load_proposals(db: Session, bucket: str, manifest_path: Path, object_proposals_path: Path | None = None) -> dict:
    proposals: list[dict] = []
    skipped_excluded = 0
    for row in json.loads(manifest_path.read_text(encoding="utf-8")):
        if row.get("status") == "excluded":
            skipped_excluded += 1
            continue
        outcome, candidates, flags = _classify(row)
        proposals.append({
            "object_key": row["object_key"], "r2_folder": row.get("set_folder") or None, "source": "manifest",
            "outcome": outcome, "candidates": candidates, "flags": flags, "qualifiers": row.get("qualifiers") or None,
            "evidence": {"method": row.get("method"), "reason": row.get("reason"), "manifest_status": row.get("status"),
                         "set_id": row.get("set_id"), "set_resolution": row.get("set_resolution")},
        })  # fmt: skip
    if object_proposals_path and object_proposals_path.exists():
        for o in json.loads(object_proposals_path.read_text(encoding="utf-8")).get("objects", []):
            proposals.append({
                "object_key": o["object_key"], "r2_folder": o.get("evidence", {}).get("r2_folder"),
                "source": "audit_per_object", "outcome": "unique_candidate", "candidates": [o["tcgdex_card_id"]],
                "flags": ["unknown_language", "folder_not_single_set"], "qualifiers": None, "evidence": o.get("evidence", {}),
            })  # fmt: skip

    # Objetos distintos que apuntan a la misma carta (misma fuente): se marcan, no se deciden.
    claims: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for p in proposals:
        if p["outcome"] == "unique_candidate":
            claims[(p["source"], p["candidates"][0])].append(p)
    for group in claims.values():
        if len(group) > 1:
            for p in group:
                if "duplicate_across_objects" not in p["flags"]:
                    p["flags"].append("duplicate_across_objects")

    wanted = sorted({c for p in proposals for c in p["candidates"]})
    resolved: dict[str, str] = {}
    for i in range(0, len(wanted), 500):
        chunk = wanted[i : i + 500]
        for ext, cid in db.execute(select(Card.external_id, Card.id).where(Card.external_id.in_(chunk))):
            resolved[ext] = str(cid)
    for p in proposals:
        p["resolved"] = [resolved[c] for c in p["candidates"] if c in resolved]
        if p["candidates"] and not p["resolved"]:
            p["flags"].append("card_not_in_catalog")

    existing = {
        (e.object_key, e.source): e
        for e in db.scalars(select(ImageMappingProposal).where(ImageMappingProposal.bucket == bucket))
    }
    counts: Counter = Counter()
    for p in proposals:
        values = dict(r2_folder=p["r2_folder"], outcome=p["outcome"], candidate_card_ids=p["candidates"],
                      resolved_card_ids=p["resolved"], qualifiers=p["qualifiers"], flags=sorted(p["flags"]),
                      evidence=p["evidence"])  # fmt: skip
        cur = existing.get((p["object_key"], p["source"]))
        if cur is None:
            db.add(ImageMappingProposal(bucket=bucket, object_key=p["object_key"], source=p["source"], **values))
            counts["created"] += 1
        elif cur.status == "proposed" and any(getattr(cur, k) != v for k, v in values.items()):
            for k, v in values.items():
                setattr(cur, k, v)
            counts["updated"] += 1
        else:
            counts["unchanged"] += 1
    return {
        "proposals": dict(counts),
        "excluded_not_loaded": skipped_excluded,
        "by_outcome": dict(Counter(p["outcome"] for p in proposals)),
        "flags": dict(sorted(Counter(f for p in proposals for f in p["flags"]).items())),
        "note": "ninguna propuesta se convirtió en asociación aprobada",
    }
