# ruff: noqa: E501
"""Sincronización controlada del catálogo de Carpetazo hacia el espejo local (solo lectura de la API; solo escribe en la base local).

Propiedades:
- Paginada por edición: 1 petición de ediciones por categoría + 1 de cartas por edición que haga falta. Sin imágenes.
- Idempotente: cada registro se compara por `digest`; lo que no cambió no se reescribe (solo se refresca `last_seen_at`).
- Incremental: una edición se vuelve a descargar solo si cambió su `modified_on`, nunca se descargó, o su última descarga supera `max_age_days` (`force` la fuerza).
- Reanudable: cada edición se confirma en su propia transacción y queda en `cursor`; `resume` continúa la última corrida incompleta sin repetir lo hecho.
- Nada se borra: lo que Carpetazo deja de entregar (en una descarga completa y no vacía) se marca `retired_at`; si reaparece, se limpia y queda en la bitácora.
- Registra fecha, duración, solicitudes, resultados y errores de cada corrida en `mirror_sync_runs`.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog_mirror.client import CarpetazoCatalogClient, CatalogApiError, RequestBudgetExceeded
from app.catalog_mirror.mapping import (
    CardRecord,
    InvalidRecord,
    SetRecord,
    card_record,
    listing_digest,
    relation_of,
    set_record,
)
from app.database.mirror_models import MirrorCard, MirrorChange, MirrorGame, MirrorSet, MirrorSyncRun

log = logging.getLogger("vision.catalog_mirror")
POKEMON_CATEGORIES = (3, 100)  # alcance por defecto: Pokémon inglés y español
SUPPORTED_CATEGORIES = (3, 100, 85, 1, 2, 63, 68, 71, 89, 99)  # + Pokémon JP (85), Magic (1), Yu-Gi-Oh (2), Digimon (63), One Piece (68), Lorcana (71), Riftbound (89), Mitos y Leyendas (99); se eligen con --categories
SPANISH_VIRTUAL_GROUP_BASE = 9_000_000
INCOMPLETE = ("running", "interrupted", "failed", "partial")


@dataclass
class SyncOptions:
    categories: tuple[int, ...] = POKEMON_CATEGORIES
    max_age_days: float = 7.0
    force: bool = False
    resume: bool = False
    max_minutes: float | None = None
    max_consecutive_errors: int = 5


def _aware(dt: datetime | None) -> datetime | None:
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=UTC))


def _now() -> datetime:
    return datetime.now(UTC)


def _needs_cards(s: MirrorSet | None, rec: SetRecord, o: SyncOptions, now: datetime) -> tuple[bool, str]:
    if o.force:
        return True, "forzado"
    if s is None or s.cards_synced_at is None:
        return True, "nunca descargada"
    if s.retired_at is not None:
        return True, "reapareció"
    if s.cards_modified_on_at_sync != rec.modified_on:
        return True, "cambió modifiedOn"
    if now - _aware(s.cards_synced_at) > timedelta(days=o.max_age_days):
        return True, f"descarga con más de {o.max_age_days:g} días"
    return False, "sin cambios"


def plan(session: Session, client: CarpetazoCatalogClient, options: SyncOptions, now: datetime | None = None, interval: float | None = None) -> dict:
    """Modo --dry-run: pide SOLO la lista de ediciones de cada categoría (1 petición por categoría) y estima el resto. No escribe nada en la base."""
    now = now or _now()
    interval = client.min_interval if interval is None else interval
    out: dict = {"dry_run": True, "base_url": client.base_url, "categories": {}, "estimated_requests": 0, "notes": []}
    total = 0
    for cat in options.categories:
        raw = client.get_sets(cat)
        recs, invalid = [], 0
        for row in raw:
            try:
                recs.append(set_record(cat, row))
            except InvalidRecord:
                invalid += 1
        existing = {s.group_id: s for s in session.scalars(select(MirrorSet).where(MirrorSet.category_id == cat))}
        reasons: dict[str, int] = {}
        fetch = 0
        for rec in recs:
            need, why = _needs_cards(existing.get(rec.group_id), rec, options, now)
            reasons[why] = reasons.get(why, 0) + 1
            fetch += need
        info = {"sets_listed": len(recs), "sets_invalid": invalid, "sets_to_fetch_cards": fetch, "sets_skipped_unchanged": len(recs) - fetch, "reasons": reasons, "sets_new": sum(1 for r in recs if r.group_id not in existing), "sets_missing_from_listing": sum(1 for g in existing if g not in {r.group_id for r in recs} and existing[g].retired_at is None)}
        out["categories"][cat] = info
        total += 1 + fetch
    out["requests_already_made_for_estimate"] = client.requests_made
    out["estimated_requests"] = total
    out["estimated_minutes_at_interval"] = round(total * max(interval, 0) / 60, 1)
    out["rate_limit_note"] = "El limitador de Carpetazo permite 2000 solicitudes por 15 minutos por IP (valor por defecto del backend); con una pausa de 1 s se usan ≈ 900 por ventana."
    out["notes"].append("La estimación no incluye reintentos. El número de cartas por edición no se conoce hasta descargarla (una edición = una petición, sin paginación interna).")
    return out


@dataclass
class _Counters:
    sets: dict = field(default_factory=lambda: {"inserted": 0, "updated": 0, "unchanged": 0, "retired": 0, "reappeared": 0, "fetched_cards": 0, "skipped_fresh": 0})
    cards: dict = field(default_factory=lambda: {"inserted": 0, "updated": 0, "unchanged": 0, "retired": 0, "reappeared": 0, "invalid": 0, "duplicates": 0})


def _log_change(session: Session, run_id, entity: str, cat: int, kind: str, group: int | None = None, product: int | None = None, before=None, after=None) -> None:
    session.add(MirrorChange(run_id=run_id, entity=entity, category_id=cat, group_id=group, product_id=product, kind=kind, before=before, after=after))


def _card_fields(r: CardRecord) -> dict:
    return dict(group_id=r.group_id, name=r.name, number=r.number, rarity=r.rarity, source_category_id=r.source_category_id, language=r.language, game_name=r.game_name, extended_data=r.extended_data, tcgplayer_product_id=r.tcgplayer_product_id, image_url=r.image_url, image_origin=r.image_origin, image_key=r.image_key, has_image=r.has_image, relation=relation_of(r.category_id, r.source_category_id), digest=r.digest)


def _brief(c: MirrorCard | dict) -> dict:
    g = (lambda k: c[k]) if isinstance(c, dict) else (lambda k: getattr(c, k))
    return {k: g(k) for k in ("name", "number", "rarity", "image_url", "image_origin", "digest")}


def _upsert_sets(session: Session, run: MirrorSyncRun, cat: int, recs: list[SetRecord], now: datetime, c: _Counters) -> dict[int, MirrorSet]:
    existing = {s.group_id: s for s in session.scalars(select(MirrorSet).where(MirrorSet.category_id == cat))}
    for rec in recs:
        s = existing.get(rec.group_id)
        fields = dict(name=rec.name, abbreviation=rec.abbreviation, published_on=rec.published_on, modified_on=rec.modified_on, digest=rec.digest)
        if s is None:
            s = MirrorSet(category_id=cat, group_id=rec.group_id, first_seen_at=now, last_seen_at=now, last_changed_at=now, **fields)
            session.add(s)
            existing[rec.group_id] = s
            _log_change(session, run.id, "set", cat, "inserted", rec.group_id, after=fields)
            c.sets["inserted"] += 1
            continue
        s.last_seen_at = now
        if s.retired_at is not None:
            s.retired_at = None
            _log_change(session, run.id, "set", cat, "reappeared", rec.group_id)
            c.sets["reappeared"] += 1
        if s.digest != rec.digest:
            before = {k: getattr(s, k) for k in fields}
            for k, v in fields.items():
                setattr(s, k, v)
            s.last_changed_at = now
            _log_change(session, run.id, "set", cat, "updated", rec.group_id, before=before, after=fields)
            c.sets["updated"] += 1
        else:
            c.sets["unchanged"] += 1
    listed = {r.group_id for r in recs}
    if recs:  # una lista vacía no retira nada (podría ser un fallo transitorio)
        for gid, s in existing.items():
            if gid not in listed and s.retired_at is None:
                s.retired_at = now
                _log_change(session, run.id, "set", cat, "retired", gid)
                c.sets["retired"] += 1
    session.flush()
    return existing


def _sync_cards(session: Session, run: MirrorSyncRun, cat: int, s: MirrorSet, rows: list[dict], now: datetime, c: _Counters, errors: list) -> None:
    recs: dict[int, CardRecord] = {}
    for row in rows:
        try:
            r = card_record(cat, row)
        except InvalidRecord as exc:
            c.cards["invalid"] += 1
            errors.append({"where": f"{cat}/{s.group_id}", "error": str(exc)})
            continue
        if r.group_id != s.group_id:
            c.cards["invalid"] += 1
            errors.append({"where": f"{cat}/{s.group_id}", "error": f"producto {r.product_id} trae groupId {r.group_id} distinto de la edición pedida"})
            continue
        if r.product_id in recs:
            c.cards["duplicates"] += 1  # se conserva la primera aparición
            continue
        recs[r.product_id] = r
    existing = {m.product_id: m for m in session.scalars(select(MirrorCard).where(MirrorCard.category_id == cat, MirrorCard.group_id == s.group_id))}
    for pid, r in recs.items():
        m = existing.get(pid)
        f = _card_fields(r)
        if m is None:
            other = session.get(MirrorCard, (cat, pid))  # mismo producto ya espejado bajo otra edición: no se pisa
            if other is not None:
                c.cards["invalid"] += 1
                errors.append({"where": f"{cat}/{s.group_id}", "error": f"producto {pid} ya existe en la edición {other.group_id}"})
                continue
            session.add(MirrorCard(category_id=cat, product_id=pid, first_seen_at=now, last_seen_at=now, last_changed_at=now, last_run_id=run.id, **f))
            _log_change(session, run.id, "card", cat, "inserted", s.group_id, pid, after=_brief(f))
            c.cards["inserted"] += 1
            continue
        m.last_seen_at, m.last_run_id = now, run.id
        if m.retired_at is not None:
            m.retired_at = None
            _log_change(session, run.id, "card", cat, "reappeared", s.group_id, pid)
            c.cards["reappeared"] += 1
        if m.digest != r.digest:
            before = _brief(m)
            for k, v in f.items():
                setattr(m, k, v)
            m.last_changed_at = now
            _log_change(session, run.id, "card", cat, "updated", s.group_id, pid, before=before, after=_brief(f))
            c.cards["updated"] += 1
        else:
            c.cards["unchanged"] += 1
    active_missing = [m for pid, m in existing.items() if pid not in recs and m.retired_at is None]
    if active_missing and not recs:
        errors.append({"where": f"{cat}/{s.group_id}", "warning": "la descarga llegó vacía; no se retira nada (posible fallo transitorio)"})
    elif recs or not active_missing:
        for m in active_missing:
            m.retired_at = now
            _log_change(session, run.id, "card", cat, "retired", s.group_id, m.product_id, before=_brief(m))
            c.cards["retired"] += 1
    names = {r.game_name for r in recs.values() if r.game_name}
    g = session.get(MirrorGame, cat)
    if g is not None and len(names) == 1 and g.name != next(iter(names)):
        g.name = next(iter(names))
    s.cards_synced_at, s.cards_modified_on_at_sync, s.cards_count = now, s.modified_on, len(recs)
    s.cards_listing_digest = listing_digest(list(recs.values()))
    session.flush()


def run_sync(session: Session, client: CarpetazoCatalogClient, options: SyncOptions | None = None, now_fn=_now, clock=time.monotonic) -> dict:
    o = options or SyncOptions()
    started = clock()
    now = now_fn()
    previous = session.scalars(select(MirrorSyncRun).where(MirrorSyncRun.status.in_(INCOMPLETE), MirrorSyncRun.dry_run.is_(False)).order_by(MirrorSyncRun.started_at.desc())).first() if o.resume else None
    done: set[str] = set((previous.cursor or {}).get("done", [])) if previous else set()
    run = MirrorSyncRun(id=uuid.uuid4(), source_base_url=client.base_url, scope={"categories": list(o.categories), "max_age_days": o.max_age_days, "force": o.force, "resume": o.resume, "min_interval": client.min_interval}, status="running", started_at=now, cursor={"done": sorted(done)}, resumed_from=previous.id if previous else None)
    session.add(run)
    session.commit()
    c, errors, consecutive, status = _Counters(), [], 0, "ok"
    skipped_resume = 0
    try:
        for cat in o.categories:
            if session.get(MirrorGame, cat) is None:
                session.add(MirrorGame(category_id=cat, first_seen_at=now, last_seen_at=now))
            else:
                session.get(MirrorGame, cat).last_seen_at = now
                session.get(MirrorGame, cat).retired_at = None
            raw = client.get_sets(cat)
            recs = []
            for row in raw:
                try:
                    recs.append(set_record(cat, row))
                except InvalidRecord as exc:
                    errors.append({"where": f"{cat}/sets", "error": str(exc)})
            sets = _upsert_sets(session, run, cat, recs, now, c)
            session.commit()
            for rec in recs:
                key = f"{cat}:{rec.group_id}"
                if key in done:
                    skipped_resume += 1
                    continue
                s = sets[rec.group_id]
                need, _why = _needs_cards(s, rec, o, now)
                if not need:
                    c.sets["skipped_fresh"] += 1
                    done.add(key)
                    continue
                if o.max_minutes is not None and (clock() - started) / 60 > o.max_minutes:
                    raise RequestBudgetExceeded(f"se alcanzó el límite de {o.max_minutes:g} minutos")
                try:
                    rows = client.get_cards(cat, rec.group_id)
                    _sync_cards(session, run, cat, s, rows, now, c, errors)
                    c.sets["fetched_cards"] += 1
                    consecutive = 0
                    done.add(key)
                except CatalogApiError as exc:
                    session.rollback()
                    errors.append({"where": key, "error": str(exc), "status": exc.status})
                    status, consecutive = "partial", consecutive + 1
                    if consecutive >= o.max_consecutive_errors:
                        raise
                run = session.merge(run)
                run.cursor, run.requests_made, run.counts = {"done": sorted(done)}, client.requests_made, {"sets": dict(c.sets), "cards": dict(c.cards)}
                session.commit()
    except RequestBudgetExceeded as exc:
        session.rollback()
        status = "interrupted"
        errors.append({"where": "budget", "error": str(exc)})
    except KeyboardInterrupt:
        session.rollback()
        status = "interrupted"
        errors.append({"where": "user", "error": "interrumpida por el usuario"})
    except CatalogApiError as exc:
        session.rollback()
        status = "failed"
        errors.append({"where": "abort", "error": f"{exc} (demasiados errores seguidos)"})
    run = session.merge(run)
    if status == "ok" and any("error" in e for e in errors):
        status = "partial"
    run.status, run.finished_at = status, _now()
    run.duration_ms = int((clock() - started) * 1000)
    run.requests_made, run.cursor = client.requests_made, {"done": sorted(done)}
    run.counts, run.errors = {"sets": dict(c.sets), "cards": dict(c.cards), "skipped_by_resume": skipped_resume, "retries": client.retries, "throttled_seconds": round(client.throttled_seconds, 1)}, errors[:200]
    session.commit()
    return {"run_id": str(run.id), "status": status, "duration_ms": run.duration_ms, "requests_made": client.requests_made, "counts": run.counts, "errors": errors[:50], "resumed_from": str(previous.id) if previous else None}


def mirror_stats(session: Session) -> dict:
    from sqlalchemy import func

    def n(model, *where):
        return session.scalar(select(func.count()).select_from(model).where(*where))

    return {
        "games": n(MirrorGame), "sets": n(MirrorSet), "sets_retired": n(MirrorSet, MirrorSet.retired_at.is_not(None)),
        "cards": n(MirrorCard), "cards_retired": n(MirrorCard, MirrorCard.retired_at.is_not(None)),
        "cards_by_category": {cat: n(MirrorCard, MirrorCard.category_id == cat) for (cat,) in session.execute(select(MirrorCard.category_id).distinct())},
        "last_runs": [{"id": str(r.id), "status": r.status, "started_at": str(r.started_at), "duration_ms": r.duration_ms, "requests": r.requests_made, "dry_run": r.dry_run} for r in session.scalars(select(MirrorSyncRun).order_by(MirrorSyncRun.started_at.desc()).limit(5))],
    }

