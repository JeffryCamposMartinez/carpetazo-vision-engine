# ruff: noqa: E501
"""Importador TCGdex -> catálogo. Idempotente, reanudable y sin descargar imágenes.

Modelo: Game > Set (por idioma de catálogo) > CardPrinting (carta + set) > PrintingVariant, con la identidad
lógica en Card (una por id de carta de TCGdex, independiente del idioma) y los ids de proveedores en
ExternalReference. Nada se borra: lo que el proveedor deje de enviar solo se cuenta en el reporte.
"""

import hashlib
import json
import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.catalog.providers.tcgdex import ProviderError, TcgdexClient
from app.database.models import (
    Card,
    CardPrinting,
    CardSet,
    CatalogSource,
    ExternalReference,
    Game,
    ImportIssue,
    ImportRun,
    ImportRunItem,
    PrintingVariant,
)

log = logging.getLogger("vision.import")
PROVIDER = "tcgdex"
ATTRIBUTION = "TCGdex (tcgdex.dev). Base de datos bajo licencia MIT. Nombres e imágenes: The Pokémon Company."

CARD_STRUCTURED = {"id", "localId", "name", "image", "rarity", "variants", "variants_detailed", "set", "dexId",
                   "illustrator", "category", "updated"}  # fmt: skip
CARD_DETAILS = {"hp", "types", "stage", "description", "attacks", "abilities", "weaknesses", "resistances", "retreat",
                "evolveFrom", "regulationMark", "trainerType", "effect", "energyType", "suffix", "item", "legal",
                "level"}  # fmt: skip
CARD_IGNORED = {"pricing"}  # volátil; se cuenta en el reporte pero no se guarda
SET_STRUCTURED = {"id", "name", "releaseDate", "serie", "cardCount", "logo", "symbol", "abbreviation", "tcgOnline",
                  "legal", "cards"}  # fmt: skip
VARIANT_STRUCTURED = {"type", "subtype", "foil", "stamp", "size", "variantId", "thirdParty"}
VARIANT_IGNORED = {"pricing"}
THIRD_PARTY_PROVIDERS = {"tcgplayer", "cardmarket"}
_ENTITY_COLUMN = {
    "game": "game_id",
    "set": "set_id",
    "card": "card_id",
    "printing": "printing_id",
    "variant": "variant_id",
}


def emit(event: str, **fields: Any) -> None:
    """Log estructurado: una línea JSON por evento."""
    log.info(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str))


@dataclass
class ImportReport:
    provider: str = PROVIDER
    dry_run: bool = False
    languages: list[str] = field(default_factory=list)
    status: str = "running"
    started_at: str = ""
    finished_at: str = ""
    run_id: str | None = None
    entities: dict[str, Counter] = field(default_factory=dict)
    errors: list[dict] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)
    ignored_fields: Counter = field(default_factory=Counter)
    unmapped_fields: Counter = field(default_factory=Counter)
    external_references: Counter = field(default_factory=Counter)
    sets_skipped_resume: int = 0
    variants_not_in_provider: int = 0

    def bump(self, entity: str, outcome: str) -> None:
        self.entities.setdefault(entity, Counter())[outcome] += 1

    def duplicate(self, item: dict) -> None:
        if item not in self.duplicates:
            self.duplicates.append(item)

    def error(self, entity_type: str, external_id: str, language: str | None, stage: str, message: str) -> None:
        self.errors.append({"entity_type": entity_type, "external_id": external_id, "language": language,
                            "stage": stage, "message": message})  # fmt: skip
        emit("import_error", entity_type=entity_type, external_id=external_id, language=language, stage=stage,
             message=message)  # fmt: skip

    def merge(self, other: "ImportReport", counters: bool = True) -> None:
        self.errors += other.errors
        for d in other.duplicates:
            self.duplicate(d)
        if counters:
            for entity, c in other.entities.items():
                self.entities.setdefault(entity, Counter()).update(c)
            self.ignored_fields.update(other.ignored_fields)
            self.unmapped_fields.update(other.unmapped_fields)
            self.external_references.update(other.external_references)
            self.variants_not_in_provider += other.variants_not_in_provider

    def to_dict(self) -> dict:
        return {
            "provider": self.provider, "dry_run": self.dry_run, "languages": self.languages, "status": self.status,
            "run_id": self.run_id, "started_at": self.started_at, "finished_at": self.finished_at,
            "entities": {k: dict(sorted(v.items())) for k, v in sorted(self.entities.items())},
            "external_references_created": dict(sorted(self.external_references.items())),
            "errors": self.errors, "duplicates": self.duplicates,
            "duplicates_summary": dict(sorted(Counter(
                f"{d.get('kind')}:{d.get('provider', '')}".rstrip(":") for d in self.duplicates).items())),
            "ignored_fields": dict(sorted(self.ignored_fields.items())),
            "unmapped_fields": dict(sorted(self.unmapped_fields.items())),
            "sets_skipped_resume": self.sets_skipped_resume,
            "variants_not_in_provider": self.variants_not_in_provider,
        }  # fmt: skip


def render_report_md(r: dict) -> str:
    lines = [f"# Reporte de importación {r['provider']}", "",
             f"Estado: **{r['status']}** · dry-run: {r['dry_run']} · idiomas: {r['languages']} · run: {r['run_id']}",
             f"Inicio: {r['started_at']} · Fin: {r['finished_at']}", "", "## Entidades",
             "| Entidad | created | updated | unchanged | skipped |", "|---|---|---|---|---|"]  # fmt: skip
    for entity, c in r["entities"].items():
        lines.append(
            f"| {entity} | {c.get('created', 0)} | {c.get('updated', 0)} | {c.get('unchanged', 0)} | {c.get('skipped', 0)} |"
        )
    lines += ["", f"Referencias externas creadas: {r['external_references_created']}",
              f"Sets omitidos por reanudación: {r['sets_skipped_resume']}",
              f"Variantes en la base que el proveedor ya no envía (no se borran): {r['variants_not_in_provider']}",
              f"Campos ignorados a propósito: {r['ignored_fields']}",
              f"Campos desconocidos guardados en metadata.unmapped: {r['unmapped_fields']}", "",
              f"## Duplicados detectados ({len(r['duplicates'])}) {r['duplicates_summary']}",
              "Un id externo compartido por varias entidades es normal (p. ej. un productId de TCGplayer para la "
              "variante normal y la reverse); se informa, no se corrige."]  # fmt: skip
    lines += [f"- {json.dumps(d, ensure_ascii=False)}" for d in r["duplicates"][:50]]
    lines += ["", f"## Errores ({len(r['errors'])})"]
    lines += [
        f"- {e['entity_type']} `{e['external_id']}` ({e['language']}) [{e['stage']}]: {e['message']}"
        for e in r["errors"][:100]
    ]
    return "\n".join(lines) + "\n"


def _assign(obj: Any, **values: Any) -> bool:
    changed = False
    for key, value in values.items():
        if getattr(obj, key) != value:
            setattr(obj, key, value)
            changed = True
    return changed


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def validate_card(raw: dict, expected_id: str, set_id: str) -> list[str]:
    problems = []
    if raw.get("id") != expected_id:
        problems.append(f"id inesperado: {raw.get('id')!r}")
    if not raw.get("localId"):
        problems.append("falta localId")
    if not isinstance(raw.get("name"), str) or not raw.get("name", "").strip():
        problems.append("falta name")
    card_set = raw.get("set")
    if isinstance(card_set, dict) and card_set.get("id") not in (None, set_id):
        problems.append(f"relación inválida: la carta declara el set {card_set.get('id')!r}, no {set_id!r}")
    detailed = raw.get("variants_detailed")
    if detailed is not None and not (
        isinstance(detailed, list) and all(isinstance(v, dict) and v.get("type") for v in detailed)
    ):
        problems.append("variants_detailed mal formado (cada variante necesita type)")
    return problems


class TcgdexImporter:
    def __init__(self, client: TcgdexClient, session_factory: sessionmaker[Session]):
        self._client = client
        self._session_factory = session_factory
        self._game_counted = False

    # ------------------------------------------------------------------ ejecución
    def run(
        self,
        languages: tuple[str, ...] = ("en",),
        set_ids: set[str] | None = None,
        max_cards: int | None = None,
        resume: bool = False,
        dry_run: bool = False,
    ) -> ImportReport:
        self._game_counted = False
        report = ImportReport(dry_run=dry_run, languages=sorted(languages), started_at=datetime.now(UTC).isoformat())
        scope = {
            "provider": PROVIDER,
            "languages": sorted(languages),
            "set_ids": sorted(set_ids or []),
            "max_cards": max_cards,
        }
        scope_hash = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
        with self._session_factory() as db:
            run_row, done = (None, set()) if dry_run else self._start_run(db, scope, scope_hash, resume)
            report.run_id = str(run_row.id) if run_row else None
            emit("import_start", run_id=report.run_id, scope=scope, resume=resume, dry_run=dry_run)
            try:
                for lang in sorted(languages, key=lambda x: (x != "en", x)):
                    self._import_language(db, run_row, lang, set_ids, max_cards, done, report)
                report.status = "completed_with_errors" if report.errors else "completed"
            except KeyboardInterrupt:
                db.rollback()
                report.status = "interrupted"
                self._finish(db, run_row, report)
                raise
            except Exception as exc:
                db.rollback()
                report.status = "failed"
                report.error(
                    "run", str(run_row.id) if run_row else "-", None, "persist", f"{type(exc).__name__}: {exc}"
                )
                self._finish(db, run_row, report)
                raise
            self._finish(db, run_row, report)
        emit("import_end", run_id=report.run_id, status=report.status, errors=len(report.errors))
        return report

    def _start_run(self, db: Session, scope: dict, scope_hash: str, resume: bool):
        run_row, done = None, set()
        if resume:
            run_row = db.scalar(
                select(ImportRun)
                .where(
                    ImportRun.provider == PROVIDER, ImportRun.scope_hash == scope_hash, ImportRun.status != "completed"
                )
                .order_by(ImportRun.started_at.desc())
            )
            if run_row:
                run_row.status = "running"
                done = {
                    (i.set_external_id, i.language)
                    for i in db.scalars(
                        select(ImportRunItem).where(ImportRunItem.run_id == run_row.id, ImportRunItem.status == "done")
                    )
                }
        if run_row is None:
            run_row = ImportRun(
                id=uuid.uuid4(), provider=PROVIDER, scope=scope, scope_hash=scope_hash, status="running", dry_run=False
            )
            db.add(run_row)
        db.commit()
        return run_row, done

    def _finish(self, db: Session, run_row: ImportRun | None, report: ImportReport) -> None:
        report.finished_at = datetime.now(UTC).isoformat()
        if run_row is None:
            return
        run_row.status, run_row.finished_at, run_row.summary = report.status, datetime.now(UTC), report.to_dict()
        db.commit()

    # ------------------------------------------------------------------ sets
    def _import_language(self, db, run_row, lang, set_ids, max_cards, done, report) -> None:
        try:
            summaries = self._client.list_sets(lang)
        except ProviderError as exc:
            report.error("set_list", lang, lang, "fetch", str(exc))
            self._record_issues(db, run_row, report.errors[-1:], report.dry_run)
            return
        ids = sorted({s["id"] for s in summaries if isinstance(s, dict) and s.get("id")})
        if set_ids:
            for missing in sorted(set_ids - set(ids)):
                report.error("set", missing, lang, "validate", "el set no existe en el catálogo de este idioma")
                self._record_issues(db, run_row, report.errors[-1:], report.dry_run)
            ids = [i for i in ids if i in set_ids]
        for sid in ids:
            if (sid, lang) in done:
                report.sets_skipped_resume += 1
                emit("set_skipped_resume", set=sid, language=lang)
                continue
            self._import_set(db, run_row, sid, lang, max_cards, report)

    def _import_set(
        self, db: Session, run_row, sid: str, lang: str, max_cards: int | None, report: ImportReport
    ) -> None:
        delta = ImportReport(dry_run=report.dry_run)
        try:
            detail = self._client.get_set(sid, lang)
            if detail.get("id") != sid or not detail.get("name"):
                raise ProviderError(f"detalle de set inválido para {sid}")
        except ProviderError as exc:
            delta.error("set", sid, lang, "fetch", str(exc))
            self._fail_set(db, run_row, sid, lang, delta, report, str(exc))
            return
        try:
            self._persist_set(db, sid, lang, detail, max_cards, delta)
            if report.dry_run:
                db.rollback()
            else:
                if run_row is not None:
                    # con errores de carta el set queda 'failed': --resume lo reintenta (los datos buenos ya están)
                    status = "failed" if delta.errors else "done"
                    self._mark_item(
                        db, run_row, sid, lang, status, f"{len(delta.errors)} errores" if delta.errors else None
                    )
                    self._record_issues(db, run_row, delta.errors, False, commit=False)
                db.commit()
            report.merge(delta)
            emit("set_done", set=sid, language=lang, errors=len(delta.errors))
        except Exception as exc:  # fallo de persistencia: se descarta el set completo, no el resto de la importación
            db.rollback()
            delta.error("set", sid, lang, "persist", f"{type(exc).__name__}: {exc}")
            self._fail_set(db, run_row, sid, lang, delta, report, str(exc))

    def _fail_set(self, db, run_row, sid, lang, delta, report, message) -> None:
        report.merge(delta, counters=False)
        if not report.dry_run and run_row is not None:
            self._mark_item(db, run_row, sid, lang, "failed", message[:2000])
            self._record_issues(db, run_row, delta.errors, False, commit=False)
            db.commit()

    def _mark_item(self, db, run_row, sid, lang, status, error) -> None:
        item = db.scalar(
            select(ImportRunItem).where(
                ImportRunItem.run_id == run_row.id, ImportRunItem.set_external_id == sid, ImportRunItem.language == lang
            )
        )
        if item is None:
            db.add(ImportRunItem(run_id=run_row.id, set_external_id=sid, language=lang, status=status, error=error))
        else:
            item.status, item.error = status, error

    def _record_issues(self, db, run_row, errors, dry_run, commit: bool = True) -> None:
        if dry_run or run_row is None:
            return
        for e in errors:
            db.add(ImportIssue(run_id=run_row.id, entity_type=e["entity_type"], external_id=e["external_id"][:200],
                               language=e["language"], stage=e["stage"], message=e["message"][:4000]))  # fmt: skip
        if commit:
            db.commit()

    def _persist_set(
        self, db: Session, sid: str, lang: str, detail: dict, max_cards: int | None, delta: ImportReport
    ) -> None:
        game = self._game(db, delta)
        source = self._source(db, sid, lang)
        cset = self._upsert_set(db, game, source, sid, lang, detail, delta)
        cards = detail.get("cards") or []
        for brief in cards[:max_cards]:
            cid = brief.get("id") if isinstance(brief, dict) else None
            if not cid:
                delta.error("card", f"{sid}:?", lang, "validate", "entrada de carta sin id en el set")
                delta.bump("card", "skipped")
                continue
            try:
                raw = self._client.get_card(cid, lang)
            except ProviderError as exc:
                delta.error("card", cid, lang, "fetch", str(exc))
                delta.bump("card", "skipped")
                continue
            if problems := validate_card(raw, cid, sid):
                delta.error("card", cid, lang, "validate", "; ".join(problems))
                delta.bump("card", "skipped")
                continue
            sub = ImportReport()
            try:
                with db.begin_nested():
                    self._upsert_card_bundle(db, game, source, cset, lang, raw, sub)
            except Exception as exc:  # solo se pierde esta carta
                delta.error("card", cid, lang, "persist", f"{type(exc).__name__}: {exc}")
                delta.bump("card", "skipped")
                continue
            delta.merge(sub)

    # ------------------------------------------------------------------ entidades
    def _game(self, db: Session, delta: ImportReport) -> Game:
        game = db.scalar(select(Game).where(Game.slug == self._client.game_slug))
        created = game is None
        if created:
            game = Game(id=uuid.uuid4(), name=self._client.game_name, slug=self._client.game_slug)
            db.add(game)
        if not self._game_counted:  # el juego se cuenta una sola vez por ejecución
            self._game_counted = True
            delta.bump("game", "created" if created else "unchanged")
        db.flush()
        self._ref(db, delta, "game", game, PROVIDER, "", self._client.game_slug)
        return game

    def _source(self, db: Session, sid: str, lang: str) -> CatalogSource:
        ext = f"set:{sid}:{lang}"
        source = db.scalar(
            select(CatalogSource).where(CatalogSource.provider == PROVIDER, CatalogSource.external_id == ext)
        )
        url = f"{self._client.base_url}/{lang}/sets/{sid}"
        if source is None:
            source = CatalogSource(id=uuid.uuid4(), provider=PROVIDER, external_id=ext, source_url=url,
                                   license_status="permitted", attribution=ATTRIBUTION)  # fmt: skip
            db.add(source)
        else:
            source.imported_at = datetime.now(UTC)
        db.flush()
        return source

    def _upsert_set(self, db, game, source, sid, lang, detail, delta) -> CardSet:
        abbreviation = detail.get("abbreviation") or {}
        unmapped = {k: v for k, v in detail.items() if k not in SET_STRUCTURED}
        delta.unmapped_fields.update(unmapped.keys())
        meta = {
            "series": detail.get("serie"), "card_count": detail.get("cardCount"), "logo": detail.get("logo"),
            "symbol": detail.get("symbol"), "abbreviation": abbreviation or None, "tcg_online": detail.get("tcgOnline"),
            "legal": detail.get("legal"), "unmapped": unmapped,
        }  # fmt: skip
        values = dict(source_id=source.id, name=detail["name"], code=abbreviation.get("official") or detail.get("tcgOnline"),
                      release_date=_parse_date(detail.get("releaseDate")), meta=meta)  # fmt: skip
        cset = db.scalar(
            select(CardSet).where(CardSet.game_id == game.id, CardSet.external_id == sid, CardSet.language == lang)
        )
        if cset is None:
            cset = CardSet(id=uuid.uuid4(), game_id=game.id, external_id=sid, language=lang, **values)
            db.add(cset)
            delta.bump("set", "created")
        else:
            delta.bump("set", "updated" if _assign(cset, **values) else "unchanged")
        db.flush()
        self._ref(db, delta, "set", cset, PROVIDER, lang, sid)
        return cset

    def _upsert_card_bundle(self, db, game, source, cset, lang, raw, delta) -> None:
        cid, name = raw["id"], raw["name"].strip()
        delta.ignored_fields.update(k for k in raw if k in CARD_IGNORED)
        unmapped = {k: v for k, v in raw.items() if k not in CARD_STRUCTURED | CARD_DETAILS | CARD_IGNORED}
        delta.unmapped_fields.update(unmapped.keys())

        card = db.scalar(select(Card).where(Card.game_id == game.id, Card.external_id == cid))
        old = dict(card.meta or {}) if card else {}
        names = {**old.get("names", {}), lang: name}
        by_language = {
            **old.get("by_language", {}),
            lang: {"category": raw.get("category"), "rarity": raw.get("rarity")},
        }
        provider_updated = {**old.get("provider_updated", {}), lang: raw.get("updated")}
        meta = {**old, "names": names, "by_language": by_language, "provider_updated": provider_updated,
                "dex_ids": raw.get("dexId") or old.get("dex_ids"), "illustrator": raw.get("illustrator") or old.get("illustrator"),
                "local_id": raw["localId"], "set_external_id": cset.external_id}  # fmt: skip
        if card is None:
            card = Card(id=uuid.uuid4(), game_id=game.id, source_id=source.id, external_id=cid, canonical_name=name,
                        identity_basis="provider_card_id", meta=meta)  # fmt: skip
            db.add(card)
            delta.bump("card", "created")
        else:
            canonical = name if lang == "en" or not card.canonical_name else card.canonical_name
            delta.bump("card", "updated" if _assign(card, canonical_name=canonical, meta=meta) else "unchanged")
        db.flush()
        self._ref(db, delta, "card", card, PROVIDER, "", cid)

        details = {k: raw[k] for k in CARD_DETAILS if k in raw}
        pmeta = {"image_base_url": raw.get("image"), "details": details, "unmapped": unmapped,
                 "provider_updated": raw.get("updated")}  # fmt: skip
        pvalues = dict(source_id=source.id, external_id=cid, name=name, collector_number=str(raw["localId"])[:30],
                       language=lang, catalog_language=lang, rarity=raw.get("rarity"), meta=pmeta)  # fmt: skip
        printing = db.scalar(
            select(CardPrinting).where(CardPrinting.card_id == card.id, CardPrinting.set_id == cset.id)
        )
        if printing is None:
            printing = CardPrinting(id=uuid.uuid4(), card_id=card.id, set_id=cset.id, **pvalues)
            db.add(printing)
            delta.bump("printing", "created")
        else:
            delta.bump("printing", "updated" if _assign(printing, **pvalues) else "unchanged")
        db.flush()
        self._ref(db, delta, "printing", printing, PROVIDER, lang, cid)
        self._upsert_variants(db, printing, lang, raw, delta, cid)

    def _variant_specs(self, raw: dict) -> list[dict]:
        detailed = raw.get("variants_detailed")
        if isinstance(detailed, list) and detailed:
            specs = []
            for v in detailed:
                stamp = "+".join(sorted(str(x) for x in (v.get("stamp") or [])))
                attrs = {"variant_type": v["type"], "variant_subtype": v.get("subtype") or "", "foil": v.get("foil") or "",
                         "stamp": stamp, "size": v.get("size") or ""}  # fmt: skip
                key = v.get("variantId") or "|".join(attrs.values())
                specs.append({"attrs": attrs, "source": "tcgdex", "external_id": key,
                              "third_party": v.get("thirdParty") or {},
                              "unmapped": {k: x for k, x in v.items() if k not in VARIANT_STRUCTURED | VARIANT_IGNORED},
                              "ignored": [k for k in v if k in VARIANT_IGNORED]})  # fmt: skip
            return specs
        flags = raw.get("variants")
        if isinstance(flags, dict):  # variantes gruesas: solo banderas, sin foil ni sellos
            return [{"attrs": {"variant_type": k, "variant_subtype": "", "foil": "", "stamp": "", "size": ""},
                     "source": "tcgdex_flags", "external_id": f"flag:{k}", "third_party": {}, "unmapped": {}, "ignored": []}
                    for k in sorted(flags) if flags[k] is True]  # fmt: skip
        return []

    def _upsert_variants(self, db, printing, lang, raw, delta, cid) -> None:
        seen: set[tuple[str, str]] = set()
        for spec in self._variant_specs(raw):
            key = (spec["source"], spec["external_id"])
            if key in seen:
                delta.duplicate(
                    {
                        "kind": "variant_repetida_en_la_carta",
                        "card": cid,
                        "language": lang,
                        "variant": spec["external_id"],
                    }
                )
                continue
            seen.add(key)
            delta.ignored_fields.update(spec["ignored"])
            delta.unmapped_fields.update(spec["unmapped"].keys())
            values = {**spec["attrs"], "meta": {"unmapped": spec["unmapped"]}}
            variant = db.scalar(select(PrintingVariant).where(PrintingVariant.printing_id == printing.id,
                                                              PrintingVariant.source == spec["source"],
                                                              PrintingVariant.external_id == spec["external_id"]))  # fmt: skip
            if variant is None:
                variant = PrintingVariant(id=uuid.uuid4(), printing_id=printing.id, source=spec["source"],
                                          external_id=spec["external_id"], **values)  # fmt: skip
                db.add(variant)
                delta.bump("variant", "created")
            else:
                delta.bump("variant", "updated" if _assign(variant, **values) else "unchanged")
            db.flush()
            if spec["source"] == "tcgdex" and not spec["external_id"].count("|"):
                self._ref(db, delta, "variant", variant, PROVIDER, lang, spec["external_id"])
            for provider, pid in sorted(spec["third_party"].items()):
                if provider in THIRD_PARTY_PROVIDERS and pid not in (None, ""):
                    self._ref(db, delta, "variant", variant, provider, "", str(pid))
                elif provider not in THIRD_PARTY_PROVIDERS:
                    delta.unmapped_fields[f"thirdParty.{provider}"] += 1
        existing = set(
            tuple(r)
            for r in db.execute(
                select(PrintingVariant.source, PrintingVariant.external_id).where(
                    PrintingVariant.printing_id == printing.id
                )
            ).all()
        )
        delta.variants_not_in_provider += len(existing - seen)

    def _ref(
        self,
        db: Session,
        delta: ImportReport,
        entity_type: str,
        entity: Any,
        provider: str,
        scope: str,
        external_id: str,
    ) -> None:
        key = str(entity.id)
        where = (ExternalReference.provider == provider, ExternalReference.scope == scope,
                 ExternalReference.external_id == external_id, ExternalReference.entity_type == entity_type)  # fmt: skip
        if db.scalar(select(ExternalReference.id).where(*where, ExternalReference.entity_key == key)) is None:
            db.add(ExternalReference(id=uuid.uuid4(), entity_type=entity_type, entity_key=key, provider=provider,
                                     scope=scope, external_id=external_id, **{_ENTITY_COLUMN[entity_type]: entity.id}))  # fmt: skip
            db.flush()
            delta.external_references[provider] += 1
        shared = db.scalar(select(func.count()).select_from(ExternalReference).where(*where))
        if shared and shared > 1:
            delta.duplicate({"kind": "id_externo_compartido", "provider": provider, "entity_type": entity_type,
                             "external_id": external_id, "entities": shared})  # fmt: skip


def configure_import_logging() -> None:
    """Una línea JSON por evento en stderr (para el CLI)."""
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
