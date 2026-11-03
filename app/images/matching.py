"""Asociación imagen R2 -> carta del catálogo, por etapas y sin inventar confianza."""

import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.images.inventory import InventoryRow, NumberRules, normalize_name, normalize_number, normalize_set_folder
from app.images.sets import SetResolution

STATUSES = ("matched", "probable", "ambiguous", "unmatched", "excluded")
CSV_FIELDS = [
    "object_key", "status", "method", "reason", "set_folder", "set_id", "set_resolution", "catalog_card_id",
    "candidate_printing_id", "collector_number", "language", "extracted_name", "number_raw", "number_interpretation",
    "qualifiers", "etag", "size", "evidence", "confidence", "approved", "approved_by",
]  # fmt: skip


@dataclass
class ManifestRow:
    object_key: str
    status: str
    method: str = ""
    reason: str = ""
    set_folder: str = ""
    set_id: str = ""
    set_resolution: str = ""
    catalog_card_id: str = ""
    candidate_printing_id: str = ""  # se completa cuando el catálogo esté en PostgreSQL
    collector_number: str = ""
    language: str = ""  # no verificado: el idioma de las imágenes de R2 no está confirmado
    extracted_name: str = ""
    number_raw: str = ""
    number_interpretation: str = ""
    qualifiers: str = ""  # Prerelease, Master Ball Pattern, [Staff]...: variantes de la misma carta
    etag: str = ""
    size: int = 0
    evidence: dict = field(default_factory=dict)
    confidence: str = ""  # vacío: no hay regla explícita de confianza numérica
    approved: str = ""
    approved_by: str = ""


class SetIndex:
    def __init__(self, cards: list[dict], rules: NumberRules):
        self.cards = cards
        self.rules = rules
        self.exact: dict[str, list[dict]] = defaultdict(list)
        self.normalized: dict[str, list[dict]] = defaultdict(list)
        self.digits: dict[str, list[dict]] = defaultdict(list)
        for c in cards:
            self.exact[c["local_id"]].append(c)
            self.normalized[normalize_number(c["local_id"], rules)].append(c)
            d = re.sub(r"\D", "", c["local_id"])
            if d:
                self.digits[d.lstrip("0") or "0"].append(c)


def _digits(token: str) -> str:
    d = re.sub(r"\D", "", token)
    return (d.lstrip("0") or "0") if d else ""


def _lookup(idx: SetIndex, token: str, name_norm: str) -> tuple[str, list[dict]] | None:
    """Etapas 1-3 para un token de número. Devuelve (método, candidatos) o None si ninguna etapa encuentra algo."""
    if idx.exact.get(token):
        return "set+número exacto", idx.exact[token]
    norm = normalize_number(token, idx.rules)
    if idx.normalized.get(norm):
        return "set+número normalizado", idx.normalized[norm]
    d = _digits(token)
    if d:
        same_name = [c for c in idx.digits.get(d, []) if normalize_name(c["name"]) == name_norm]
        if same_name:
            return "set+nombre+número (solo dígitos)", same_name
    return None


def _decide(
    row: InventoryRow, method: str, cands: list[dict], name_norm: str, cap: str
) -> tuple[str, str, dict | None, str]:
    by_name = [c for c in cands if normalize_name(c["name"]) == name_norm]
    digits_only = method.endswith("(solo dígitos)")
    if len(cands) > 1 and len(by_name) == 1:
        cands, note = by_name, "varios candidatos por número; desempatado por nombre idéntico"
    elif len(cands) > 1:
        return "ambiguous", method, None, f"{len(cands)} cartas del set comparten ese número y el nombre no desempata"
    else:
        note = ""
    card = cands[0]
    name_ok = normalize_name(card["name"]) == name_norm
    status = "matched" if (name_ok and not digits_only) else "probable"
    reason = note or ("" if name_ok else f"el nombre difiere: R2='{row.parsed['name']}' catálogo='{card['name']}'")
    if digits_only:
        reason = "número comparado solo por dígitos; nombre idéntico"
    if cap == "probable" and status == "matched":
        status, reason = "probable", (reason + "; set no confirmado").strip("; ")
    return status, method, card, reason


def match_inventory(
    rows: list[InventoryRow],
    resolutions: dict[str, SetResolution],
    catalog_sets: list[dict],
    data_json: dict[tuple[str, str], dict],
    rules: NumberRules = NumberRules(),
) -> list[ManifestRow]:
    indexes = {s["id"]: SetIndex(s["cards"], rules) for s in catalog_sets}
    set_names = {s["id"]: s["name"] for s in catalog_sets}
    out: list[ManifestRow] = []
    for r in rows:
        base = ManifestRow(
            object_key=r.object_key, status="unmatched", set_folder=r.set_folder or "", etag=r.etag or "", size=r.size,
            extracted_name=r.parsed.get("name", ""), number_raw=r.parsed.get("number_raw") or "",
            number_interpretation=r.parsed.get("interpretation", ""),
            qualifiers=" | ".join(r.parsed.get("qualifiers", [])),
        )  # fmt: skip
        if r.kind == "excluded":
            base.status, base.reason = "excluded", r.exclusion_reason or ""
            out.append(base)
            continue
        res = resolutions.get(r.set_folder)
        base.set_resolution = res.status if res else "unresolved"
        if res is None or res.status in {"unresolved", "ambiguous"}:
            base.status = "ambiguous" if res and res.status == "ambiguous" else "unmatched"
            base.reason = f"set {base.set_resolution}" + (f": {res.note}" if res and res.note else "")
            out.append(base)
            continue
        base.set_id = res.set_id
        cap = "probable" if res.status == "probable" else "matched"
        idx, name_norm = indexes[res.set_id], normalize_name(r.parsed["name"])
        data = data_json.get((normalize_set_folder(r.set_folder), r.card_folder))
        tokens = [("carpeta", r.parsed.get("local"))]
        if data and data.get("number"):
            tokens.append(("data.json", str(data["number"]).split("/")[0]))
        found = None
        for source, token in tokens:
            if token and (hit := _lookup(idx, token, name_norm)):
                found = (source, token, *hit)
                break
        if found is None:
            base.reason = "ninguna etapa encontró una carta con ese número en el set"
            out.append(base)
            continue
        source, token, method, cands = found
        status, method, card, reason = _decide(
            r, method + ("" if source == "carpeta" else " (vía data.json)"), cands, name_norm, cap
        )
        base.status, base.method, base.reason = status, method, reason
        base.evidence = {
            "número_usado": token,
            "origen_número": source,
            "set_catálogo": set_names[res.set_id],
            "vía_set": res.via,
        }
        if data:
            base.evidence["data_json"] = {
                k: data[k] for k in ("productId", "name", "number", "rarity", "set_name", "source")
            }
        if card:
            base.catalog_card_id, base.collector_number = card["id"], card["local_id"]
            base.evidence["nombre_catálogo"] = card["name"]
        else:
            base.evidence["candidatos"] = [c["id"] for c in cands]
        out.append(base)
    _flag_duplicate_targets(out)
    return out


def _flag_duplicate_targets(rows: list[ManifestRow]) -> None:
    """Varios objetos hacia la misma carta: la imagen base conserva su estado; el resto queda ambiguo."""
    claims = defaultdict(list)
    for m in rows:
        if m.catalog_card_id and m.status in {"matched", "probable"}:
            claims[m.catalog_card_id].append(m)
    for card_id, group in claims.items():
        if len(group) < 2:
            continue
        base = [m for m in group if not m.qualifiers]
        for m in group:
            if m.qualifiers and len(base) == 1:
                m.status = "ambiguous"
                m.reason = f"variante '{m.qualifiers}' de {card_id}: necesita una impresión de variante en el catálogo"
            elif m.qualifiers or len(base) > 1:
                m.status, m.reason = "ambiguous", f"{len(group)} objetos de R2 apuntan a {card_id}"


def summarize(rows: list[ManifestRow]) -> dict:
    return dict(Counter(m.status for m in rows))


def write_manifest(rows: list[ManifestRow], out_dir: Path, stem: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jpath, cpath = out_dir / f"{stem}.json", out_dir / f"{stem}.csv"
    jpath.write_text(json.dumps([asdict(m) for m in rows], ensure_ascii=False, indent=1), encoding="utf-8")
    with cpath.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for m in rows:
            d = asdict(m)
            d["evidence"] = json.dumps(d["evidence"], ensure_ascii=False)
            w.writerow(d)
    return jpath, cpath


def read_manifest_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))
