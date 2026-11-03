# ruff: noqa: E501
"""Importación controlada de referencias y búsqueda (exacta en memoria o con pgvector) sobre el índice de E4.

Reglas que este módulo hace cumplir:
- Solo una referencia con identidad VERIFICADA por una persona entra a `verified_references` (carta verificada + referencia verificada a mano + archivo local
  con SHA-256 coincidente). Nada se descarga: se usan archivos locales ya existentes (R2 no se lee aquí).
- Las candidatas (probables/ambiguas) entran a su propia tabla, sin identidad; no existe ninguna función que las promueva.
- Cada modelo/versión tiene su propia tabla con dimensión fija; nunca se mezclan.
- `dry_run` no escribe nada (ni siquiera calcula embeddings).
"""

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.database.models import Card, CardPrinting, PrintingVariant
from app.database.reference_models import EMBEDDING_TABLES, CandidateReference, ReferenceImportRun, VerifiedReference
from app.dataset_lab.store import Dataset
from app.recognition.detection import CANON_H, CANON_W
from app.recognition.embedding.base import Extractor
from app.recognition.index.registry import ModelSlot, slot_for
from app.recognition.normalization import NormalizationConfig, normalize_canonical

CANDIDATE_FORBIDDEN_KEYS = {"verified_by", "verified_at", "verification_note", "card_id", "printing_id", "variant_id", "name", "set_name", "collector_number", "identity", "approved", "approved_by"}
CANDIDATE_STATUSES = ("probable", "ambiguous")


class ReferenceImportError(Exception):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_canonical(path: Path) -> tuple[np.ndarray, int, int]:
    with Image.open(path) as im:
        rgb = np.asarray(ImageOps.exif_transpose(im).convert("RGB"))
    h, w = rgb.shape[:2]
    return cv2.resize(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), (CANON_W, CANON_H), interpolation=cv2.INTER_AREA), w, h


def _variant(canon: np.ndarray, cfg: NormalizationConfig | None) -> tuple[np.ndarray, str]:
    return (canon, "raw") if cfg is None else (normalize_canonical(canon, cfg)[0], cfg.tag)


def _vec_param(v: np.ndarray) -> list[float]:
    return [float(x) for x in v]


def _add_embeddings(session: Session, tier: str, reference_id: uuid.UUID, canon: np.ndarray, extractors: list[Extractor], cfg: NormalizationConfig | None) -> int:
    added = 0
    img, tag = _variant(canon, cfg)
    for ext in extractors:
        slot = slot_for(ext.spec)
        table = EMBEDDING_TABLES[(tier, ext.spec.key)]
        if session.scalar(select(table.id).where(table.reference_id == reference_id, table.normalization == tag)):
            continue
        vec = np.asarray(ext.embed(img), np.float32)
        if vec.shape != (slot.dim,) or not np.isfinite(vec).all():
            raise ReferenceImportError(f"el extractor {ext.spec.key} devolvió un vector inválido (se esperaban {slot.dim} dimensiones)")
        session.add(table(reference_id=reference_id, normalization=tag, embedding=_vec_param(vec)))
        added += 1
    return added


def eligible_dataset_cards(ds: Dataset) -> tuple[list[tuple[dict, Path]], list[dict]]:
    """Cartas del dataset del piloto que cumplen TODO para ser referencia verificada, y las que no (con el motivo)."""
    ok, skipped = [], []
    for ref, c in sorted(ds.cards.items()):
        reason = None
        f = next(iter(sorted(ds.references.glob(f"{ref}.*"))), None)
        if str(c.get("name") or "").upper().startswith("EJEMPLO"):
            reason = "carta de ejemplo (ficticia)"
        elif c.get("verification_status") != "verified":
            reason = "carta física sin verificar"
        elif not (c.get("reference_verified_by") and c.get("reference_object_key")):
            reason = "referencia digital sin verificación manual"
        elif f is None:
            reason = "no hay archivo local de la referencia (references-fetch no ejecutado)"
        elif not c.get("reference_sha256"):
            reason = "la referencia no tiene SHA-256 registrado"
        elif sha256_file(f) != c["reference_sha256"]:
            reason = "el SHA-256 del archivo local no coincide con el registrado"
        (skipped.append({"card_ref": ref, "reason": reason}) if reason else ok.append((c, f)))
    return ok, skipped


def import_verified_from_dataset(session: Session, ds: Dataset, extractors: list[Extractor], by: str, normalization: NormalizationConfig | None = None, dry_run: bool = False) -> dict:
    if not by.strip():
        raise ReferenceImportError("indica quién importa (--by)")
    for ext in extractors:
        slot_for(ext.spec)  # falla pronto si el modelo no tiene tablas
    ok, skipped = eligible_dataset_cards(ds)
    report = {"dry_run": dry_run, "eligible": len(ok), "skipped": skipped, "created": [], "already_present": [], "embeddings_added": 0, "errors": [], "normalization": "raw" if normalization is None else normalization.tag}
    if dry_run:
        report["would_create"] = [c["card_ref"] for c, f in ok if not session.scalar(select(VerifiedReference.id).where(VerifiedReference.image_sha256 == sha256_file(f)))]
        return report
    run = ReferenceImportRun(kind="verified", requested_by=by.strip(), source=f"dataset:{ds.root.name}:{hashlib.sha256((ds.manifest / 'cards.json').read_bytes()).hexdigest()[:16]}", counts={})
    session.add(run)
    session.flush()
    try:
        for c, f in ok:
            digest = sha256_file(f)
            row = session.scalar(select(VerifiedReference).where(VerifiedReference.image_sha256 == digest))
            canon, w, h = load_canonical(f)
            if row is None:
                missing = [k for k, model in (("card_id", Card), ("printing_id", CardPrinting), ("variant_id", PrintingVariant)) if c.get(k) and not session.get(model, uuid.UUID(c[k]))]
                if missing:
                    report["errors"].append({"card_ref": c["card_ref"], "error": f"{missing} no existe en el catálogo"})
                    continue
                row = VerifiedReference(
                    source_kind="pilot_dataset", source_ref=c["card_ref"], storage_key=c["reference_object_key"], image_sha256=digest, width=w, height=h,
                    name=c["name"], set_name=c["set_name"], collector_number=c["collector_number"], language=c.get("language") or "unknown", finish=c.get("finish") or "desconocido", pattern=c.get("pattern") or "desconocido",
                    card_id=uuid.UUID(c["card_id"]) if c.get("card_id") else None, printing_id=uuid.UUID(c["printing_id"]) if c.get("printing_id") else None, variant_id=uuid.UUID(c["variant_id"]) if c.get("variant_id") else None,
                    verified_by=c["reference_verified_by"], verified_at=c.get("reference_verified_at") or c["verified_at"],
                    verification_note=f"carta: {c['verification_note']} | referencia: {c.get('reference_verification_note') or '(sin nota)'}",
                    imported_by=by.strip(), import_run_id=run.id,
                )  # fmt: skip
                session.add(row)
                session.flush()
                report["created"].append(c["card_ref"])
            else:
                report["already_present"].append(c["card_ref"])
            report["embeddings_added"] += _add_embeddings(session, "verified", row.id, canon, extractors, normalization)
        run.counts = {"created": len(report["created"]), "already_present": len(report["already_present"]), "skipped": len(skipped), "embeddings_added": report["embeddings_added"], "errors": len(report["errors"])}
        session.commit()
    except Exception:
        session.rollback()
        raise
    return report


def import_candidates(session: Session, items: list[dict], extractors: list[Extractor], by: str, source: str, normalization: NormalizationConfig | None = None, dry_run: bool = False) -> dict:
    """Imágenes probables/ambiguas con archivos LOCALES. Sin identidad: se rechaza cualquier elemento que intente traerla."""
    if not by.strip() or not source.strip():
        raise ReferenceImportError("indica quién importa (--by) y el origen")
    report = {"dry_run": dry_run, "created": 0, "already_present": 0, "rejected": [], "embeddings_added": 0}
    run = None
    if not dry_run:
        run = ReferenceImportRun(kind="candidate", requested_by=by.strip(), source=source, counts={})
        session.add(run)
        session.flush()
    try:
        for it in items:
            forbidden = CANDIDATE_FORBIDDEN_KEYS & set(it)
            if forbidden:
                report["rejected"].append({"storage_key": it.get("storage_key"), "reason": f"una candidata no puede traer identidad ni verificación: {sorted(forbidden)}"})
                continue
            if it.get("proposal_status") not in CANDIDATE_STATUSES or not it.get("storage_key") or not it.get("image_path"):
                report["rejected"].append({"storage_key": it.get("storage_key"), "reason": f"proposal_status debe ser {CANDIDATE_STATUSES} y se requieren storage_key e image_path"})
                continue
            digest = sha256_file(Path(it["image_path"]))
            if session.scalar(select(VerifiedReference.id).where(VerifiedReference.image_sha256 == digest)):
                report["rejected"].append({"storage_key": it["storage_key"], "reason": "la misma imagen ya es una referencia VERIFICADA: no se duplica como candidata"})
                continue
            row = session.scalar(select(CandidateReference).where(CandidateReference.image_sha256 == digest))
            if dry_run:
                report["already_present" if row else "created"] += 1
                continue
            canon, _, _ = load_canonical(Path(it["image_path"]))
            if row is None:
                row = CandidateReference(storage_key=it["storage_key"], image_sha256=digest, claimed_label=it.get("claimed_label"), proposal_status=it["proposal_status"], proposal_source=source, imported_by=by.strip(), import_run_id=run.id)
                session.add(row)
                session.flush()
                report["created"] += 1
            else:
                report["already_present"] += 1
            report["embeddings_added"] += _add_embeddings(session, "candidate", row.id, canon, extractors, normalization)
        if run is not None:
            run.counts = {k: report[k] for k in ("created", "already_present", "embeddings_added")} | {"rejected": len(report["rejected"])}
            session.commit()
    except Exception:
        session.rollback()
        raise
    return report


# ---- búsqueda
@dataclass(frozen=True)
class Match:
    reference_id: uuid.UUID
    similarity: float  # coseno (los vectores están normalizados)


class ExactIndex:
    """Búsqueda exacta por producto matricial en memoria (por defecto: no requiere pgvector)."""

    def __init__(self, ids: list[uuid.UUID], matrix: np.ndarray, dim: int):
        self.ids, self.dim = ids, dim
        self.matrix = matrix.astype(np.float32).reshape(len(ids), dim)

    @classmethod
    def load(cls, session: Session, spec, tier: str, normalization: str) -> "ExactIndex":
        slot = slot_for(spec)
        table = EMBEDDING_TABLES[(tier, spec.key)]
        rows = session.execute(select(table.reference_id, table.embedding).where(table.normalization == normalization)).all()
        return cls([r[0] for r in rows], np.array([np.asarray(r[1], np.float32) for r in rows]) if rows else np.zeros((0, slot.dim), np.float32), slot.dim)

    def __len__(self) -> int:
        return len(self.ids)

    def search(self, query: np.ndarray, k: int = 10) -> list[Match]:
        q = np.asarray(query, np.float32)
        if q.shape != (self.dim,):
            raise ValueError(f"la consulta tiene {q.shape} y este índice es de {self.dim} dimensiones (otro modelo)")
        if not len(self.ids):
            return []
        sims = self.matrix @ q
        top = np.argsort(-sims)[:k]
        return [Match(self.ids[i], float(sims[i])) for i in top]


def pgvector_search(session: Session, spec, tier: str, normalization: str, query: np.ndarray, k: int = 10) -> list[Match]:
    """Búsqueda con el operador de distancia coseno de pgvector (solo PostgreSQL con la extensión)."""
    slot: ModelSlot = slot_for(spec)
    q = np.asarray(query, np.float32)
    if q.shape != (slot.dim,):
        raise ValueError(f"la consulta tiene {q.shape} y la tabla es de {slot.dim} dimensiones (otro modelo)")
    table = slot.table(tier)  # nombre sacado del registro fijo, nunca de la entrada del usuario
    vec = "[" + ",".join(f"{float(x):.8g}" for x in q) + "]"
    rows = session.execute(text(f"SELECT reference_id, 1 - (embedding <=> CAST(:q AS vector)) AS sim FROM {table} WHERE normalization = :n ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"), {"q": vec, "n": normalization, "k": k}).all()  # noqa: S608
    return [Match(r[0], float(r[1])) for r in rows]


def ensure_hnsw(session: Session, spec, tier: str, m: int = 16, ef_construction: int = 64) -> str:
    slot = slot_for(spec)
    table = slot.table(tier)
    name = f"ix_{table}_hnsw"
    session.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} USING hnsw (embedding vector_cosine_ops) WITH (m = {int(m)}, ef_construction = {int(ef_construction)})"))  # noqa: S608
    session.commit()
    return name
