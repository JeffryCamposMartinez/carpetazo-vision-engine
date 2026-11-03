"""Revisión de carpetas de set de R2 sin resolver: candidatos de TCGdex con evidencia a favor y en contra.

La recomendación nunca confirma nada: 'confirmable' solo significa que la evidencia es defendible y
que una persona puede aprobar el alias. Salida determinista (sin fechas, todo ordenado).
"""

import csv
import json
import re
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from app.images.aliases import alias_entry
from app.images.inventory import InventoryRow, NumberRules, normalize_name, normalize_number, normalize_set_folder

CONFIRM_RATIO = 0.9  # fracción de imágenes que deben coincidir en número y nombre
CONFIRM_MARGIN = 0.2  # ventaja mínima sobre el segundo candidato
REVIEW_RATIO = 0.3
NAME_REVIEW_SCORE = 0.85
MIN_IMAGES_TO_CONFIRM = 10
OVERSIZE_FACTOR = 1.2  # carpeta con más imágenes que cartas del set -> revisar
GROUPED_COVERAGE = 0.8
GROUPED_MIN_IMAGES = 400
GROUPED_SET_SHARE = 0.15  # un set "pesa" si cubre al menos esta fracción de la carpeta
MIN_IMAGES_TO_REVIEW = 5

_SEP_PREFIX = re.compile(r"^[A-Za-z]{1,6}\d{0,2}_\s+")
_DASH_PREFIX = re.compile(r"^[A-Za-z]{1,6}\s+-\s+")
_SERIES_PREFIX = re.compile(r"^(?:EX|SM|XY|BW|DP|HGSS)\s+")
_TRAILING_PARENS = re.compile(r"\s*\([^)]*\)$")

CATEGORIES = {
    "format_duplicate": "Carpeta duplicada por formato (guion bajo vs espacio)",
    "duplicate_of_claimed_set": "Posible variante de un set ya asignado a otra carpeta",
    "trainer_kit": "Trainer Kits",
    "promos": "Promos",
    "grouped_multi_set": "Posible agrupación de varios sets",
    "translated_or_other_language": "Números coinciden pero los nombres no (¿otro idioma o nombres distintos?)",
    "code_prefix_name": "Nombre con prefijo de código o serie",
    "alternate_name": "Nombre alternativo",
    "missing_in_catalog": "Ausente del catálogo en inglés",
}


def set_key(text: str) -> str:
    return normalize_set_folder(re.sub(r"\band\b", " ", text, flags=re.I))


def name_variants(folder: str) -> list[tuple[str, str]]:
    out = [("carpeta", folder)]
    for label, rx in (
        ("sin prefijo de código", _SEP_PREFIX),
        ("sin prefijo de serie con guion", _DASH_PREFIX),
        ("sin prefijo de serie", _SERIES_PREFIX),
    ):
        text = rx.sub("", folder)
        if text != folder:
            out.append((label, text))
    return out


def name_score(folder: str, catalog_name: str) -> tuple[float, str]:
    target = set_key(catalog_name)
    best = max(
        ((round(SequenceMatcher(None, set_key(t), target).ratio(), 3), label) for label, t in name_variants(folder)),
        key=lambda x: x[0],
    )
    return best


def _pct(n: int, total: int) -> str:
    return f"{n}/{total} ({100 * n / total:.0f}%)" if total else "0/0"


def review_sets(
    rows: list[InventoryRow],
    catalog_sets: list[dict],
    unresolved: list[str],
    data_json: dict,
    rules: NumberRules = NumberRules(),
    claimed: dict[str, list[str]] | None = None,
) -> dict:
    cards_by_folder: dict[str, list[InventoryRow]] = defaultdict(list)
    for r in rows:
        if r.kind == "card":
            cards_by_folder[r.set_folder].append(r)
    all_folders = sorted(cards_by_folder)
    sets_by_id = {s["id"]: s for s in catalog_sets}

    by_number: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for s in catalog_sets:
        for c in s["cards"]:
            by_number[normalize_number(c["local_id"], rules)].append((s["id"], normalize_name(c["name"])))
    cat_names = {s["id"]: {normalize_number(c["local_id"], rules): c["name"] for c in s["cards"]} for s in catalog_sets}

    data_by_folder: dict[str, list[dict]] = defaultdict(list)
    for (set_norm, _), d in sorted(data_json.items()):
        data_by_folder[set_norm].append(d)

    folders = []
    for folder in sorted(unresolved):
        imgs = sorted(cards_by_folder[folder], key=lambda r: r.object_key)
        n = len(imgs)
        num_hits: dict[str, int] = defaultdict(int)
        name_hits: dict[str, int] = defaultdict(int)
        mismatch: dict[str, list[str]] = defaultdict(list)
        per_image_sets: list[set[str]] = []
        no_number = 0
        for r in imgs:
            local = r.parsed.get("local")
            hit_sets: set[str] = set()
            if not local:
                no_number += 1
                per_image_sets.append(hit_sets)
                continue
            names = {normalize_name(r.parsed["name"]), normalize_name(_TRAILING_PARENS.sub("", r.parsed["name"]))}
            for set_id, cname in by_number.get(normalize_number(local, rules), []):
                num_hits[set_id] += 1
                if cname in names:
                    name_hits[set_id] += 1
                    hit_sets.add(set_id)
                elif len(mismatch[set_id]) < 3:
                    real = cat_names[set_id].get(normalize_number(local, rules), "?")
                    mismatch[set_id].append(f"nº {local}: R2 '{r.parsed['name']}' vs TCGdex '{real}'")
            per_image_sets.append(hit_sets)

        pool = sorted(set(sorted(name_hits, key=lambda i: (-name_hits[i], i))[:5]))
        scored = sorted(
            ((name_score(folder, s["name"]), s["id"]) for s in catalog_sets), key=lambda x: (-x[0][0], x[1])
        )
        pool = sorted(set(pool) | {i for _, i in scored[:3]})
        score_of = {i: sc for sc, i in scored}

        cands = []
        for sid in pool:
            s = sets_by_id[sid]
            sc, label = score_of[sid]
            nh, nmh, total_cards = num_hits.get(sid, 0), name_hits.get(sid, 0), len(s["cards"])
            pro, con = [], []
            if nmh:
                pro.append(f"{_pct(nmh, n)} imágenes coinciden en número y nombre")
            if sc >= 0.8:
                pro.append(f"nombre similar ({sc}) usando '{label}'")
            if nh > nmh:
                con.append(f"{_pct(nh - nmh, n)} imágenes coinciden en número pero no en nombre")
            if n - nh > 0:
                con.append(f"{_pct(n - nh, n)} imágenes tienen un número que no existe en el set")
            if total_cards and n > total_cards * OVERSIZE_FACTOR:
                con.append(f"la carpeta tiene {n} imágenes y el set solo {total_cards} cartas")
            if sc < 0.6:
                con.append(f"nombre poco parecido ({sc})")
            con += [f"ej. {m}" for m in mismatch.get(sid, [])]
            cands.append({
                "tcgdex_set_id": sid, "tcgdex_name": s["name"], "set_cards": total_cards, "images": n,
                "number_hits": nh, "name_hits": nmh,
                "name_ratio": round(nmh / n, 3) if n else 0.0, "number_ratio": round(nh / n, 3) if n else 0.0,
                "name_score": sc, "name_score_variant": label, "evidence_for": pro, "evidence_against": con,
            })  # fmt: skip
        cands.sort(key=lambda c: (-c["name_ratio"], -c["name_score"], c["tcgdex_set_id"]))
        cands = cands[:3]

        top3 = sorted(name_hits, key=lambda i: (-name_hits[i], i))[:3]
        covered = sum(1 for s in per_image_sets if s & set(top3))
        coverage = covered / n if n else 0.0
        best = cands[0] if cands else None

        key = normalize_set_folder(folder)
        siblings = [f for f in all_folders if f != folder and normalize_set_folder(f) == key]
        d = data_by_folder.get(key, [])
        data_info = {
            "cards_with_data_json": len(d),
            "set_names": sorted({x["set_name"] for x in d if x.get("set_name")}),
            "abbreviations": sorted({x["set_abbreviation"] for x in d if x.get("set_abbreviation")}),
            "group_ids": sorted({x["set_groupId"] for x in d if x.get("set_groupId")}),
        }

        dominant = bool(best) and best["name_ratio"] >= CONFIRM_RATIO
        strong_sets = [i for i in top3 if name_hits[i] >= GROUPED_SET_SHARE * n]
        grouped = bool(best) and not dominant and best["name_ratio"] < 0.6 and (
            (coverage >= GROUPED_COVERAGE and len(strong_sets) > 1) or n >= GROUPED_MIN_IMAGES
        )  # fmt: skip
        claimed_by = [f for f in (claimed or {}).get(best["tcgdex_set_id"], []) if f != folder] if best else []
        if claimed_by and best["name_ratio"] >= REVIEW_RATIO:
            best["evidence_against"].append(
                f"el set ya está asignado a la carpeta {claimed_by} (nombre exacto o aproximado)"
            )
        lowered = folder.lower()
        if siblings:
            category = "format_duplicate"
        elif claimed_by and best["name_ratio"] >= REVIEW_RATIO:
            category = "duplicate_of_claimed_set"
        elif "trainer kit" in lowered:
            category = "trainer_kit"
        elif "promo" in lowered:
            category = "promos"
        elif grouped:
            category = "grouped_multi_set"
        elif best and best["number_ratio"] >= 0.8 and best["name_ratio"] < REVIEW_RATIO and best["name_score"] >= 0.6:
            category = "translated_or_other_language"
        elif best and best["name_ratio"] >= REVIEW_RATIO and best["name_score_variant"] != "carpeta":
            category = "code_prefix_name"
        elif best and (best["name_ratio"] >= REVIEW_RATIO or best["name_score"] >= NAME_REVIEW_SCORE):
            category = "alternate_name"
        else:
            category = "missing_in_catalog"

        second = cands[1] if len(cands) > 1 else None
        oversize = bool(best) and bool(best["set_cards"]) and n > best["set_cards"] * OVERSIZE_FACTOR
        unique = second is None or best["name_ratio"] - second["name_ratio"] >= CONFIRM_MARGIN if best else False
        if best and dominant and n >= MIN_IMAGES_TO_CONFIRM and unique and not oversize and not claimed_by:
            rec = "confirmable"
            hits = _pct(best["name_hits"], n)
            reason = f"{hits} coinciden en número y nombre con {best['tcgdex_set_id']}; candidato único"
        elif best and (
            (best["name_ratio"] >= REVIEW_RATIO and n >= MIN_IMAGES_TO_REVIEW)
            or best["name_score"] >= NAME_REVIEW_SCORE
            or grouped
        ):
            rec = "needs_review"
            reason = (
                f"evidencia parcial: solapamiento {best['name_ratio']:.0%}, similitud de nombre {best['name_score']}"
            )
            if claimed_by:
                reason += f"; el set ya lo usa {claimed_by} (¿variante o reimpresión?)"
            elif oversize:
                reason += f"; la carpeta tiene más imágenes ({n}) que cartas el set ({best['set_cards']})"
            elif dominant and n < MIN_IMAGES_TO_CONFIRM:
                reason += f"; muestra pequeña ({n} imágenes)"
            elif dominant and not unique:
                reason = "varios candidatos con solapamiento similar"
            elif len(strong_sets) > 1:
                reason += f"; las imágenes se reparten entre {strong_sets} (¿carpeta que une varios sets?)"
            elif grouped:
                reason += "; carpeta grande sin un set dominante (¿agrupa varios sets?)"
        else:
            rec = "no_candidate"
            top_ratio = f"{best['name_ratio']:.0%}" if best else "0%"
            reason = f"ningún set de TCGdex (en) supera el umbral: mejor solapamiento {top_ratio}"

        folders.append({
            "r2_folder": folder,
            "images": n,
            "category": category,
            "recommendation": rec,
            "reason": reason,
            "samples": [f"{r.parsed.get('local') or '?'} {r.parsed['name']}" for r in imgs[:5]],
            "images_without_number": no_number,
            "data_json": data_info,
            "same_folder_siblings": siblings,
            "coverage_top3_sets": round(coverage, 3),
            "candidates": cands,
        })  # fmt: skip

    _flag_shared_targets(folders)

    summary = {
        "unresolved_folders": len(folders),
        "by_recommendation": _count(folders, "recommendation"),
        "by_category": _count(folders, "category"),
        "images_by_recommendation": {
            k: sum(f["images"] for f in folders if f["recommendation"] == k)
            for k in sorted({f["recommendation"] for f in folders})
        },
    }
    return {"summary": summary, "folders": folders}


def _flag_shared_targets(folders: list[dict]) -> None:
    """Dos carpetas 'confirmable' hacia el mismo set no pueden ser ambas el set oficial: pasan a revisión."""
    by_target: dict[str, list[dict]] = defaultdict(list)
    for f in folders:
        if f["recommendation"] == "confirmable":
            by_target[f["candidates"][0]["tcgdex_set_id"]].append(f)
    for target, group in sorted(by_target.items()):
        if len(group) > 1:
            names = sorted(f["r2_folder"] for f in group)
            for f in group:
                f["recommendation"] = "needs_review"
                f["reason"] = f"{len(group)} carpetas apuntan a {target}: {names}"
                f["candidates"][0]["evidence_against"].append(f"otras carpetas apuntan al mismo set: {names}")


def _count(items: list[dict], key: str) -> dict:
    out: dict[str, int] = defaultdict(int)
    for i in items:
        out[i[key]] += 1
    return dict(sorted(out.items()))


def proposals(review: dict) -> dict:
    """Propuestas de alias (status=proposed) para confirmable y needs_review con candidato. Sin revisor."""
    entries = []
    for f in review["folders"]:
        if f["recommendation"] == "no_candidate" or not f["candidates"]:
            continue
        best = f["candidates"][0]
        entries.append(
            alias_entry(
                f["r2_folder"],
                best["tcgdex_set_id"],
                {
                    "recommendation": f["recommendation"],
                    "category": f["category"],
                    "reason": f["reason"],
                    "evidence_for": best["evidence_for"],
                    "evidence_against": best["evidence_against"],
                },
            )
        )
    return {"aliases": entries}


def render_markdown(review: dict) -> str:
    s = review["summary"]
    lines = [
        "# Revisión de sets de R2 sin resolver",
        "",
        f"Carpetas: **{s['unresolved_folders']}** · recomendación: {s['by_recommendation']}",
        "",
        "`confirmable` = evidencia defendible, pero **nada está confirmado** hasta que una persona lo apruebe "
        "en `evaluation/set_aliases.json`.",
        "",
    ]
    for cat, title in CATEGORIES.items():
        group = [f for f in review["folders"] if f["category"] == cat]
        if not group:
            continue
        lines += [f"## {title} ({len(group)})", ""]
        for f in group:
            lines += [
                f"### `{f['r2_folder']}` — {f['images']} imágenes — **{f['recommendation']}**",
                f"- Motivo: {f['reason']}",
            ]
            lines.append(f"- Muestra: {', '.join(f['samples'])}")
            if f["data_json"]["cards_with_data_json"]:
                lines.append(f"- data.json: {f['data_json']}")
            for c in f["candidates"]:
                lines.append(
                    f"- Candidato `{c['tcgdex_set_id']}` {c['tcgdex_name']!r} (set de {c['set_cards']} cartas): "
                    f"solapamiento {c['name_ratio']:.0%}, nombre {c['name_score']}"
                )
                lines += [f"  - ✔ {e}" for e in c["evidence_for"]]
                lines += [f"  - ✘ {e}" for e in c["evidence_against"]]
            lines.append("")
    return "\n".join(lines)


def write_review(review: dict, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [out_dir / "r2-sets-review.json", out_dir / "r2-sets-review.csv", out_dir / "r2-sets-review.md",
             out_dir / "r2-set-alias-proposals.json"]  # fmt: skip
    paths[0].write_text(json.dumps(review, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    fields = ["r2_folder", "images", "category", "recommendation", "reason", "best_candidate_id", "best_candidate_name",
              "name_ratio", "name_score", "second_candidate_id", "evidence_for", "evidence_against", "samples",
              "data_json_set_names"]  # fmt: skip
    with paths[1].open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for f in review["folders"]:
            b = f["candidates"][0] if f["candidates"] else {}
            sec = f["candidates"][1] if len(f["candidates"]) > 1 else {}
            w.writerow({
                "r2_folder": f["r2_folder"], "images": f["images"], "category": f["category"],
                "recommendation": f["recommendation"], "reason": f["reason"],
                "best_candidate_id": b.get("tcgdex_set_id", ""), "best_candidate_name": b.get("tcgdex_name", ""),
                "name_ratio": b.get("name_ratio", ""), "name_score": b.get("name_score", ""),
                "second_candidate_id": sec.get("tcgdex_set_id", ""),
                "evidence_for": " | ".join(b.get("evidence_for", [])),
                "evidence_against": " | ".join(b.get("evidence_against", [])),
                "samples": " | ".join(f["samples"]), "data_json_set_names": " | ".join(f["data_json"]["set_names"]),
            })  # fmt: skip
    paths[2].write_text(render_markdown(review), encoding="utf-8")
    paths[3].write_text(json.dumps(proposals(review), ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return paths
