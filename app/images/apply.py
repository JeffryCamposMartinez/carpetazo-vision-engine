"""Aplica (o revierte) un manifiesto revisado. Nunca escribe en R2."""

import hashlib
import json
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import CardPrinting, ImageAssociation

APPLICABLE = {"matched", "probable"}
TRUE_VALUES = {"yes", "si", "sí", "true", "1", "x"}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_manifest(db: Session, rows: list[dict], bucket: str, manifest_sha256: str, approved_by: str) -> dict:
    """Idempotente: una fila ya aplicada queda 'unchanged'. Las filas rechazadas no abortan el resto."""
    if not approved_by.strip():
        raise ValueError("approved_by es obligatorio")
    counts: Counter = Counter()
    rejected: list[dict] = []

    def reject(row: dict, why: str) -> None:
        counts["rejected"] += 1
        rejected.append({"object_key": row.get("object_key"), "reason": why})

    for row in rows:
        if (row.get("approved") or "").strip().lower() not in TRUE_VALUES:
            counts["skipped_not_approved"] += 1
            continue
        if row.get("status") not in APPLICABLE:
            reject(row, f"estado '{row.get('status')}' no aplicable")
            continue
        try:
            printing_id = uuid.UUID(row.get("candidate_printing_id") or "")
        except ValueError:
            reject(row, "candidate_printing_id vacío o inválido")
            continue
        printing = db.scalar(select(CardPrinting).where(CardPrinting.id == printing_id))
        if printing is None:
            reject(row, "printing_id inexistente")
            continue
        if printing.set.external_id != row.get("set_id"):
            reject(row, f"el set de la impresión ({printing.set.external_id}) no coincide con {row.get('set_id')}")
            continue
        assoc = db.scalar(
            select(ImageAssociation).where(
                ImageAssociation.printing_id == printing_id,
                ImageAssociation.bucket == bucket,
                ImageAssociation.object_key == row["object_key"],
            )
        )
        if assoc and assoc.status == "approved":
            counts["unchanged"] += 1
            continue
        evidence = json.loads(row.get("evidence") or "{}")
        fields = dict(
            etag=row.get("etag") or None, size=int(row.get("size") or 0), method=row.get("method"), evidence=evidence,
            match_status=row["status"], manifest_sha256=manifest_sha256, approved_by=approved_by, status="approved",
            approved_at=datetime.now(UTC), reverted_at=None, reverted_by=None,
        )  # fmt: skip
        if assoc:
            for k, v in fields.items():
                setattr(assoc, k, v)
            counts["reactivated"] += 1
        else:
            db.add(ImageAssociation(printing_id=printing_id, bucket=bucket, object_key=row["object_key"], **fields))
            counts["created"] += 1
        if not printing.image_reference:
            printing.image_reference = row["object_key"]
    return {"counts": dict(counts), "rejected": rejected}


def revert_associations(db: Session, bucket: str, reverted_by: str, object_keys: list[str] | None = None,
                        manifest_sha256: str | None = None) -> int:  # fmt: skip
    """Marca como 'reverted' (no borra filas ni objetos). Filtra por keys o por manifiesto."""
    if not object_keys and not manifest_sha256:
        raise ValueError("indica object_keys o manifest_sha256")
    stmt = select(ImageAssociation).where(ImageAssociation.bucket == bucket, ImageAssociation.status == "approved")
    if object_keys:
        stmt = stmt.where(ImageAssociation.object_key.in_(object_keys))
    if manifest_sha256:
        stmt = stmt.where(ImageAssociation.manifest_sha256 == manifest_sha256)
    n = 0
    for a in db.scalars(stmt):
        a.status, a.reverted_at, a.reverted_by = "reverted", datetime.now(UTC), reverted_by
        printing = db.get(CardPrinting, a.printing_id)
        if printing and printing.image_reference == a.object_key:
            printing.image_reference = None
        n += 1
    return n
