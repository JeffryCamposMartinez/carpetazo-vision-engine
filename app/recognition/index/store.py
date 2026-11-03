# ruff: noqa: E501
"""Importación controlada de referencias y búsqueda (exacta en memoria o con pgvector) sobre el índice de E4, con identificadores de Carpetazo.

Reglas que este módulo hace cumplir:
- Solo una referencia con identidad VERIFICADA por una persona y vinculada a un producto de Carpetazo (`category_id`, `product_id`) entra a `verified_references`:
  carta física verificada + referencia verificada a mano + archivo local con SHA-256 coincidente + producto presente y vigente en el espejo del catálogo.
- Las candidatas (vínculo de Carpetazo sin revisar) y las ambiguas entran a sus propias tablas; no existe ninguna función que las promueva.
- Cada modelo/versión tiene su propia tabla con dimensión fija; nunca se mezclan.
- `dry_run` no escribe nada (ni siquiera calcula embeddings). Nada se descarga: se usan archivos locales ya existentes (R2 no se lee aquí).
"""

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.database.mirror_models import MirrorCard
from app.database.reference_models import (
    EMBEDDING_TABLES,
    AmbiguousReference,
    CandidateReference,
    ReferenceImportRun,
    VerifiedReference,
)
from app.dataset_lab.store import Dataset
from app.recognition.detection import CANON_H, CANON_W
from app.recognition.embedding.base import Extractor
from app.recognition.index.registry import ModelSlot, slot_for
from app.recognition.normalization import NormalizationConfig, normalize_canonical

# Una candidata o ambigua no puede traer identidad ni verificación: solo un vínculo AFIRMADO por Carpetazo (claimed_*).
FORBIDDEN_KEYS = {"verified_by", "verified_at", "verification_note", "card_id", "printing_id", "variant_id", "category_id", "product_id", "name", "set_name", "collector_number", "identity", "approved", "approved_by", "variant"}


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


def _lead_number(value: str | None) -> str | None:
    m = re.match(r"\s*0*(\d+)", value or "")
    return m.group(1) if m else None


def mirror_link_problem(session: Session, category_id: int, product_id: int, collector_number: str | None = None) -> str | None:
    """Motivo por el que un vínculo (category_id, product_id) no sirve para una referencia verificada; None si sirve."""
    m = session.get(MirrorCard, (category_id, product_id))
    if m is None:
        return f"el producto {category_id}/{product_id} no existe en el espejo del catálogo de Carpetazo (sincroniza primero)"
    if m.retired_at is not None:
        return f"el producto {category_id}/{product_id} está retirado del catálogo de Carpetazo"
    a, b = _lead_number(collector_number), _lead_number(m.number)
    if a and b and a != b:
        return f"el número de la carta física ({collector_number}) no coincide con el del producto de Carpetazo ({m.number}); revisar a mano"
    return None


def eligible_dataset_cards(ds: Dataset, session: Session) -> tuple[list[tuple[dict, Path]], list[dict]]:
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
        elif c.get("catalog_category_id") is None or c.get("catalog_product_id") is None:
            reason = "sin vínculo con un productId de Carpetazo (categoria_id_carpetazo / product_id_carpetazo)"
        elif f is None:
            reason = "no hay archivo local de la referencia (references-fetch no ejecutado)"
        elif not c.get("reference_sha256"):
            reason = "la referencia no tiene SHA-256 registrado"
        elif sha256_file(f) != c["reference_sha256"]:
            reason = "el SHA-256 del archivo local no coincide con el registrado"
        else:
            reason = mirror_link_problem(session, c["catalog_category_id"], c["catalog_product_id"], c.get("collector_number"))
        (skipped.append({"card_ref": ref, "reason": reason}) if reason else ok.append((c, f)))
    return ok, skipped


def import_verified_from_dataset(session: Session, ds: Dataset, extractors: list[Extractor], by: str, normalization: NormalizationConfig | None = None, dry_run: bool = False) -> dict:
    if not by.strip():
        raise ReferenceImportError("indica quién importa (--by)")
    for ext in extractors:
        slot_for(ext.spec)  # falla pronto si el modelo no tiene tablas
    ok, skipped = eligible_dataset_cards(ds, session)
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
                row = VerifiedReference(
                    source_kind="pilot_dataset", source_ref=c["card_ref"], storage_key=c["reference_object_key"], image_sha256=digest, width=w, height=h,
                    category_id=c["catalog_category_id"], product_id=c["catalog_product_id"],
                    name=c["name"], set_name=c["set_name"], collector_number=c["collector_number"], language=c.get("language") or "unknown", finish=c.get("finish") or "desconocido", pattern=c.get("pattern") or "desconocido",
                    variant="unknown",  # sin evidencia ni entidad de variante: no se afirma
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


def _import_unverified(session: Session, tier: str, items: list[dict], extractors: list[Extractor], by: str, source: str, normalization: NormalizationConfig | None, dry_run: bool) -> dict:
    """Candidatas y ambiguas: archivos LOCALES con un vínculo AFIRMADO por Carpetazo (`claimed_category_id`/`claimed_product_id`); jamás identidad."""
    model = CandidateReference if tier == "candidate" else AmbiguousReference
    if not by.strip() or not source.strip():
        raise ReferenceImportError("indica quién importa (--by) y el origen")
    for ext in extractors:
        slot_for(ext.spec)
    report = {"dry_run": dry_run, "created": 0, "already_present": 0, "rejected": [], "embeddings_added": 0}
    run = None
    if not dry_run:
        run = ReferenceImportRun(kind="candidate", requested_by=by.strip(), source=f"{tier}:{source}", counts={})
        session.add(run)
        session.flush()
    try:
        for it in items:
            key = it.get("storage_key")
            forbidden = FORBIDDEN_KEYS & set(it)
            if forbidden:
                report["rejected"].append({"storage_key": key, "reason": f"una {tier} no puede traer identidad ni verificación: {sorted(forbidden)} (solo claimed_category_id/claimed_product_id)"})
                continue
            cat, prod = it.get("claimed_category_id"), it.get("claimed_product_id")
            if not key or not it.get("image_path"):
                report["rejected"].append({"storage_key": key, "reason": "se requieren storage_key e image_path"})
                continue
            if (cat is None) != (prod is None) or (tier == "candidate" and cat is None):
                report["rejected"].append({"storage_key": key, "reason": "el vínculo afirmado por Carpetazo (claimed_category_id y claimed_product_id) es obligatorio en candidatas y debe ir completo"})
                continue
            if tier == "ambiguous" and not str(it.get("ambiguity_reason") or "").strip():
                report["rejected"].append({"storage_key": key, "reason": "una ambigua exige ambiguity_reason"})
                continue
            if cat is not None and session.get(MirrorCard, (cat, prod)) is None:
                report["rejected"].append({"storage_key": key, "reason": f"el producto {cat}/{prod} no existe en el espejo del catálogo de Carpetazo"})
                continue
            digest = sha256_file(Path(it["image_path"]))
            if session.scalar(select(VerifiedReference.id).where(VerifiedReference.image_sha256 == digest)):
                report["rejected"].append({"storage_key": key, "reason": "la misma imagen ya es una referencia VERIFICADA: no se duplica en otro nivel"})
                continue
            other = AmbiguousReference if tier == "candidate" else CandidateReference
            if session.scalar(select(other.id).where(other.image_sha256 == digest)):
                report["rejected"].append({"storage_key": key, "reason": f"la misma imagen ya está registrada como {'ambigua' if tier == 'candidate' else 'candidata'}: no se mueve entre niveles"})
                continue
            row = session.scalar(select(model).where(model.image_sha256 == digest))
            if dry_run:
                report["already_present" if row else "created"] += 1
                continue
            canon, _, _ = load_canonical(Path(it["image_path"]))
            if row is None:
                extra = {"link_source": it.get("link_source") or "carpetazo_catalog", "note": it.get("note")} if tier == "candidate" else {"ambiguity_reason": it["ambiguity_reason"]}
                claim = {"claimed_category_id": cat, "claimed_product_id": prod}
                row = model(storage_key=key, image_sha256=digest, imported_by=by.strip(), import_run_id=run.id, **claim, **extra)
                session.add(row)
                session.flush()
                report["created"] += 1
            else:
                report["already_present"] += 1
            report["embeddings_added"] += _add_embeddings(session, tier, row.id, canon, extractors, normalization)
        if run is not None:
            run.counts = {k: report[k] for k in ("created", "already_present", "embeddings_added")} | {"rejected": len(report["rejected"])}
            session.commit()
    except Exception:
        session.rollback()
        raise
    return report


def import_candidates(session: Session, items: list[dict], extractors: list[Extractor], by: str, source: str, normalization: NormalizationConfig | None = None, dry_run: bool = False) -> dict:
    return _import_unverified(session, "candidate", items, extractors, by, source, normalization, dry_run)


def import_ambiguous(session: Session, items: list[dict], extractors: list[Extractor], by: str, source: str, normalization: NormalizationConfig | None = None, dry_run: bool = False) -> dict:
    return _import_unverified(session, "ambiguous", items, extractors, by, source, normalization, dry_run)


# ---- búsqueda
@dataclass(frozen=True)
class Match:
    reference_id: uuid.UUID
    similarity: float  # coseno (los vectores están normalizados)
    tier: str = "verified"


class ExactIndex:
    """Búsqueda exacta por producto matricial en memoria (por defecto: no requiere pgvector). Un índice = un nivel, un modelo y una normalización."""

    def __init__(self, ids: list[uuid.UUID], matrix: np.ndarray, dim: int, tier: str = "verified"):
        self.ids, self.dim, self.tier = ids, dim, tier
        self.matrix = matrix.astype(np.float32).reshape(len(ids), dim)

    @classmethod
    def load(cls, session: Session, spec, tier: str, normalization: str) -> "ExactIndex":
        slot = slot_for(spec)
        table = EMBEDDING_TABLES[(tier, spec.key)]
        rows = session.execute(select(table.reference_id, table.embedding).where(table.normalization == normalization)).all()
        return cls([r[0] for r in rows], np.array([np.asarray(r[1], np.float32) for r in rows]) if rows else np.zeros((0, slot.dim), np.float32), slot.dim, tier)

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
        return [Match(self.ids[i], float(sims[i]), self.tier) for i in top]


def pgvector_search(session: Session, spec, tier: str, normalization: str, query: np.ndarray, k: int = 10) -> list[Match]:
    """Búsqueda con el operador de distancia coseno de pgvector (solo PostgreSQL con la extensión; opcional)."""
    slot: ModelSlot = slot_for(spec)
    q = np.asarray(query, np.float32)
    if q.shape != (slot.dim,):
        raise ValueError(f"la consulta tiene {q.shape} y la tabla es de {slot.dim} dimensiones (otro modelo)")
    table = slot.table(tier)  # nombre sacado del registro fijo, nunca de la entrada del usuario
    vec = "[" + ",".join(f"{float(x):.8g}" for x in q) + "]"
    rows = session.execute(text(f"SELECT reference_id, 1 - (embedding <=> CAST(:q AS vector)) AS sim FROM {table} WHERE normalization = :n ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"), {"q": vec, "n": normalization, "k": k}).all()  # noqa: S608
    return [Match(r[0], float(r[1]), tier) for r in rows]


def ensure_hnsw(session: Session, spec, tier: str, m: int = 16, ef_construction: int = 64) -> str:
    slot = slot_for(spec)
    table = slot.table(tier)
    name = f"ix_{table}_hnsw"
    session.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} USING hnsw (embedding vector_cosine_ops) WITH (m = {int(m)}, ef_construction = {int(ef_construction)})"))  # noqa: S608
    session.commit()
    return name
