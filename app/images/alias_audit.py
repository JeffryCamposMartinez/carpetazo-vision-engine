# ruff: noqa: E501
"""Auditoría de propuestas de alias R2 -> TCGdex. Nunca confirma nada: solo clasifica y explica.

Estados: recommended | needs_visual_review | conflict | rejected | insufficient_evidence.
Una coincidencia alta de nombre+número nunca basta por sí sola: se contrasta con el total impreso, las variantes,
los duplicados, los conflictos con otras carpetas y el tamaño esperado del set.
"""

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from app.images.aliases import alias_entry
from app.images.inventory import InventoryRow, NumberRules, normalize_name, normalize_number
from app.images.sets import SetResolution

RECOMMEND_RATIO = 0.9
MIN_IMAGES = 10
MIN_DISTINCT_NAMES = 10
OVERSIZE_TOLERANCE = 1.05
TOTAL_SHARE_FOR_CHECK = 0.5
OVERLAP_CONFLICT = 0.5
GROUPED_MIN_IMAGES = 400
WEAK_RATIO = 0.3
WEAK_NAME_SCORE = 0.85
VARIANT_MARKERS = re.compile(r"shadowless|1st edition|first edition|unlimited|reverse|prerelease", re.I)
STATUSES = ("recommended", "needs_visual_review", "conflict", "rejected", "insufficient_evidence")


def _keys(imgs: list[InventoryRow], rules: NumberRules):
    for r in imgs:
        local = r.parsed.get("local")
        if local:
            yield normalize_number(local, rules), normalize_name(r.parsed["name"]), r


def _set_index(s: dict, rules: NumberRules) -> dict[str, str]:
    return {normalize_number(c["local_id"], rules): normalize_name(c["name"]) for c in s["cards"]}


def _total_check(imgs: list[InventoryRow], s: dict) -> dict:
    totals = [int(r.parsed["total"]) for r in imgs if (r.parsed.get("total") or "").isdigit()]
    official = (s.get("card_count") or {}).get("official")
    if len(totals) < TOTAL_SHARE_FOR_CHECK * len(imgs) or not totals:
        return {
            "ok": None,
            "detail": f"{len(totals)}/{len(imgs)} imágenes traen un total impreso; no se puede comprobar",
        }
    mode, count = Counter(totals).most_common(1)[0]
    ok = official is not None and mode == official
    return {
        "ok": ok,
        "detail": f"total impreso más frecuente {mode} ({count}/{len(totals)}) vs oficial en TCGdex {official}",
    }


def _per_object(imgs: list[InventoryRow], sets: list[dict], rules: NumberRules) -> list[dict]:
    """Candidatos por imagen: cartas de los sets dados con mismo número y nombre."""
    index: dict[tuple[str, str], list[str]] = defaultdict(list)
    for s in sets:
        for c in s["cards"]:
            index[(normalize_number(c["local_id"], rules), normalize_name(c["name"]))].append(c["id"])
    out = []
    for r in imgs:
        local = r.parsed.get("local")
        cands = (
            sorted(index.get((normalize_number(local, rules), normalize_name(r.parsed["name"])), [])) if local else []
        )
        out.append({
            "object_key": r.object_key, "r2_folder": r.set_folder, "name": r.parsed["name"], "number": local,
            "qualifiers": " | ".join(r.parsed.get("qualifiers", [])), "candidate_cards": cands,
            "outcome": "unique" if len(cands) == 1 else ("multiple" if cands else "none"),
        })  # fmt: skip
    return out


def _coverage(c: dict) -> float:
    """Fracción de las cartas del set que la carpeta cubre (no confundir con la fracción de la carpeta que coincide)."""
    return min(1.0, c["name_hits"] / c["set_cards"]) if c["set_cards"] else 0.0


def audit_alias(
    rows: list[InventoryRow],
    catalog_sets: list[dict],
    resolutions: dict[str, SetResolution],
    review: dict,
    probe: dict | None = None,
    rules: NumberRules = NumberRules(),
) -> dict:
    sets_by_id = {s["id"]: s for s in catalog_sets}
    cards_by_folder: dict[str, list[InventoryRow]] = defaultdict(list)
    for r in rows:
        if r.kind == "card":
            cards_by_folder[r.set_folder].append(r)
    keys_by_folder = {f: {(a, b) for a, b, _ in _keys(imgs, rules)} for f, imgs in cards_by_folder.items()}
    etags_by_folder = {f: {r.etag for r in imgs if r.etag} for f, imgs in cards_by_folder.items()}
    probe_cards = {c["id"]: c for c in (probe or {}).get("cards", [])}
    # Reclamos reales de un set: solo carpetas con solapamiento significativo que no son kits ni agrupaciones.
    best_of = {
        f["r2_folder"]: (f["candidates"][0]["tcgdex_set_id"], _coverage(f["candidates"][0]))
        for f in review["folders"]
        if f["candidates"]
        and f["candidates"][0]["name_ratio"] >= WEAK_RATIO
        and f["category"] != "grouped_multi_set"
        and f["images"] >= MIN_IMAGES
        and "trainer kit" not in f["r2_folder"].lower()
    }

    entries, object_rows = [], []
    for f in review["folders"]:
        folder, n = f["r2_folder"], f["images"]
        imgs = sorted(cards_by_folder[folder], key=lambda r: r.object_key)
        cand = f["candidates"][0] if f["candidates"] else None
        s = sets_by_id.get(cand["tcgdex_set_id"]) if cand else None
        checks: dict[str, dict] = {}
        conflicts: list[str] = []
        notes: list[str] = []
        evidence: list[str] = []
        resolution, status, why = "none", "insufficient_evidence", ""

        # base = una imagen por (número, nombre); variants = repeticiones. Un calificador como "Full Art" en una
        # carta sin gemela no la hace variante: es una carta distinta del set (p. ej. secret rare).
        seen: set[tuple[str, str]] = set()
        base, variants = [], []
        for r in sorted(imgs, key=lambda r: (bool(r.parsed.get("qualifiers")), r.object_key)):
            local = r.parsed.get("local")
            if not local:
                base.append(r)
                continue
            key = (normalize_number(local, rules), normalize_name(r.parsed["name"]))
            (variants if key in seen else base).append(r)
            seen.add(key)
        qual_counts = Counter(q for r in variants for q in r.parsed.get("qualifiers", []))
        dup_base = sum(1 for r in variants if not r.parsed.get("qualifiers"))
        distinct_names = len({normalize_name(r.parsed["name"]) for r in imgs})
        metrics = {
            "images": n, "base_images": len(base), "variant_images": len(variants),
            "qualifiers": dict(qual_counts.most_common(6)), "duplicate_base_images": dup_base,
            "distinct_names": distinct_names,
        }  # fmt: skip

        # --- 6. duplicados y contenido compartido con otras carpetas
        mine = keys_by_folder.get(folder, set())
        sharing = []
        for other, okeys in sorted(keys_by_folder.items()):
            if other == folder or not mine or not okeys:
                continue
            shared = len(mine & okeys)
            if shared and shared / min(len(mine), len(okeys)) >= OVERLAP_CONFLICT:
                et = len(etags_by_folder[folder] & etags_by_folder[other])
                res = resolutions.get(other)
                sharing.append({
                    "folder": other, "shared_number_name": shared, "shared_etags": et,
                    "resolved_to": res.set_id if res and res.set_id else None, "resolution": res.status if res else None,
                })  # fmt: skip
        checks["duplicate_folders"] = {
            "ok": not sharing,
            "detail": "; ".join(f"{x['folder']}: {x['shared_number_name']} num+nombre y {x['shared_etags']} archivos idénticos" for x in sharing)
            or "ningún otro folder comparte >=50% del contenido",
        }  # fmt: skip
        sib_info = []
        for sib in f["same_folder_siblings"]:
            sk = len(mine & keys_by_folder.get(sib, set()))
            se = len(etags_by_folder[folder] & etags_by_folder.get(sib, set()))
            smaller = min(len(cards_by_folder[folder]), len(cards_by_folder[sib])) or 1
            same = se >= 0.9 * smaller
            sib_info.append({"folder": sib, "images": len(cards_by_folder[sib]), "shared_number_name": sk,
                             "shared_identical_files": se, "same_content": same})  # fmt: skip
            notes.append(
                f"misma carpeta con otro formato de nombre: {sib!r} ({len(cards_by_folder[sib])} imágenes); comparten "
                f"{sk} pares número+nombre y {se} archivos idénticos (ETag): "
                + ("posible copia" if same else "contenido distinto, no tratar como copia")
            )
        if sib_info:
            metrics["sibling_comparison"] = sib_info

        if cand is None or s is None:
            checks["candidate_in_snapshot"] = {"ok": False, "detail": "no hay candidato en el snapshot de TCGdex"}
            why = "ningún set de TCGdex en inglés tiene contenido compatible"
            status = "insufficient_evidence"
        else:
            checks["candidate_in_snapshot"] = {
                "ok": True,
                "detail": f"{cand['tcgdex_set_id']} existe ({len(s['cards'])} cartas)",
            }
            idx = _set_index(s, rules)
            matched = [r for a, b, r in _keys(imgs, rules) if idx.get(a) == b]
            matched_ids = {id(r) for r in matched}
            m_base = sum(1 for r in base if id(r) in matched_ids)
            ratio = len(matched) / n if n else 0.0
            base_ratio = m_base / len(base) if base else 0.0
            numbers_ok = sum(1 for a, _, _ in _keys(imgs, rules) if a in idx) / n if n else 0.0
            unmatched = [
                f"{r.parsed['local']} {r.parsed['name']}" for a, b, r in _keys(imgs, rules) if idx.get(a) != b
            ][:4]
            total = _total_check(imgs, s)
            distinct_numbers = len({a for a, _, _ in _keys(base, rules)})
            set_cards = len(s["cards"])
            metrics |= {"matched_ratio": round(ratio, 3), "base_matched_ratio": round(base_ratio, 3),
                        "numbers_in_set_ratio": round(numbers_ok, 3), "set_cards": set_cards,
                        "distinct_base_numbers": distinct_numbers}  # fmt: skip
            checks["numbers_compatible"] = {
                "ok": numbers_ok >= RECOMMEND_RATIO and total["ok"] is not False,
                "detail": f"{numbers_ok:.0%} de los números existen en el set; {total['detail']}",
            }
            checks["filename_evidence"] = {
                "ok": base_ratio >= RECOMMEND_RATIO and distinct_names >= MIN_DISTINCT_NAMES and n >= MIN_IMAGES,
                "detail": f"{base_ratio:.0%} de las imágenes base coinciden en número+nombre; {distinct_names} nombres distintos; "
                          f"ej. sin coincidencia: {unmatched or 'ninguno'}",
            }  # fmt: skip
            checks["expected_size"] = {
                "ok": len(base) <= set_cards * OVERSIZE_TOLERANCE and dup_base == 0,
                "detail": f"{n} imágenes = {len(base)} cartas distintas + {len(variants)} repeticiones "
                          f"({dup_base} sin calificador que las explique) vs {set_cards} cartas en el set",
            }  # fmt: skip

            claimed = [
                o
                for o, r in resolutions.items()
                if r.set_id == cand["tcgdex_set_id"] and r.status in {"confirmed", "probable"}
            ]
            dominant = _coverage(cand) >= RECOMMEND_RATIO
            shared_target = [
                o
                for o, (sid, r) in sorted(best_of.items())
                if sid == cand["tcgdex_set_id"] and o != folder and (not dominant or r >= RECOMMEND_RATIO)
            ]
            weak = cand["name_ratio"] < WEAK_RATIO and cand["name_score"] < WEAK_NAME_SCORE
            overlap_other_set = [
                x["folder"] for x in sharing if x["resolved_to"] and x["resolved_to"] != cand["tcgdex_set_id"]
            ]
            if claimed:
                conflicts.append(f"el set {cand['tcgdex_set_id']} ya está asignado a {claimed}")
            if shared_target:
                conflicts.append(f"otras carpetas sin resolver apuntan al mismo set: {shared_target}")
            if overlap_other_set:
                conflicts.append(f"comparte contenido con carpetas resueltas a otro set: {overlap_other_set}")
            checks["conflicts"] = {"ok": not conflicts, "detail": "; ".join(conflicts) or "sin conflictos"}
            checks["already_assigned"] = {"ok": not claimed, "detail": f"asignado a {claimed}" if claimed else "libre"}

            markers = sorted({m.lower() for m in VARIANT_MARKERS.findall(folder)})
            checks["variants_or_editions"] = {
                "ok": not markers,
                "detail": f"marcadores en el nombre: {markers or 'ninguno'}; calificadores de repeticiones: {metrics['qualifiers'] or 'ninguno'}",
            }
            evidence = [
                f"{ratio:.0%} de {n} imágenes coinciden en número+nombre con {cand['tcgdex_set_id']} ({cand['tcgdex_name']!r})",
                total["detail"],
            ]
            if variants:
                evidence.append(
                    f"{len(variants)} imágenes repiten una carta ya presente (variantes: {metrics['qualifiers'] or 'sin calificador'})"
                )

            lowered = folder.lower()
            second = f["candidates"][1] if len(f["candidates"]) > 1 else None
            margin_ok = second is None or cand["name_ratio"] - second["name_ratio"] >= 0.2

            if "trainer kit" in lowered:
                tk = [
                    x
                    for x in catalog_sets
                    if x["id"].startswith("tk-") and x["id"] in {c["tcgdex_set_id"] for c in f["candidates"]}
                ]
                tk = tk or [x for x in catalog_sets if x["id"].startswith("tk-")]
                objs = _per_object(imgs, tk, rules)
                object_rows += objs
                resolution = "per_card"
                oc = Counter(o["outcome"] for o in objs)
                status = (
                    "needs_visual_review" if (oc["unique"] + oc["multiple"]) / n >= 0.5 else "insufficient_evidence"
                )
                per_set = Counter(
                    c.rsplit("-", 1)[0] for o in objs if o["outcome"] == "unique" for c in o["candidate_cards"]
                )
                metrics["per_object"] = dict(oc) | {"unique_by_set": dict(per_set)}
                why = (f"la carpeta mezcla varios kits: {dict(oc)} imágenes con 1/varios/ningún candidato; "
                       f"no se asigna un único set; las cartas idénticas de dos kits solo se distinguen visualmente")  # fmt: skip
            elif folder.lower().find("shadowless") >= 0 or (claimed and claimed[0] and VARIANT_MARKERS.search(folder)):
                resolution, status = "printing_variant", "needs_visual_review"
                claimed_folder = claimed[0] if claimed else None
                shared = len(mine & keys_by_folder.get(claimed_folder, set())) if claimed_folder else 0
                etag_shared = (
                    len(etags_by_folder[folder] & etags_by_folder.get(claimed_folder, set())) if claimed_folder else 0
                )
                sub = sorted({
                    v.get("subtype") for c in probe_cards.values() if c["id"].startswith(cand["tcgdex_set_id"] + "-")
                    for v in c["variants_detailed"] if v.get("subtype")
                })  # fmt: skip
                metrics["variant_analysis"] = {
                    "same_set_folder": claimed_folder, "shared_number_name": shared, "shared_identical_files": etag_shared,
                    "tcgdex_variant_subtypes_seen": sub,
                }  # fmt: skip
                why = (f"no es otro set: {shared}/{len(mine)} pares número+nombre coinciden con {claimed_folder!r} pero "
                       f"{etag_shared} archivos son idénticos (imágenes distintas). TCGdex modela esta edición como subtipo de "
                       f"variante de {cand['tcgdex_set_id']} ({sub or 'sin sondeo'}), por lo que debe asociarse a impresiones "
                       f"de variante, no como alias de set")  # fmt: skip
                conflicts.append("fusionar con la carpeta base perdería la identidad de la variante")
            elif f["category"] == "grouped_multi_set" or (
                n >= GROUPED_MIN_IMAGES and cand["name_ratio"] < RECOMMEND_RATIO
            ):
                objs = _per_object(imgs, catalog_sets, rules)
                object_rows += objs
                oc = Counter(o["outcome"] for o in objs)
                top = Counter(
                    c.rsplit("-", 1)[0] for o in objs if o["outcome"] == "unique" for c in o["candidate_cards"]
                )
                metrics["per_object"] = dict(oc) | {"unique_top_sets": dict(top.most_common(8))}
                resolution = "per_card"
                covered = (oc["unique"] + oc["multiple"]) / n if n else 0
                status = "needs_visual_review" if covered >= 0.5 else "insufficient_evidence"
                why = (f"carpeta agrupada: {dict(oc)} imágenes con 1/varios/ningún candidato entre todos los sets; "
                       f"requiere asociación por carta, no un alias de carpeta")  # fmt: skip
            elif weak:
                conflicts = []
                if f["recommendation"] == "no_candidate":
                    status = "insufficient_evidence"
                    why = f"sin candidato defendible: el mejor ({cand['tcgdex_set_id']}) solapa solo {ratio:.0%}"
                else:
                    status = "rejected"
                    why = f"el candidato propuesto contradice el contenido (solapamiento {ratio:.0%}, números {numbers_ok:.0%})"
            elif f["recommendation"] != "no_candidate" and total["ok"] is False and ratio < RECOMMEND_RATIO:
                status = "rejected"
                why = f"el total impreso contradice el set candidato y el contenido es parcial ({total['detail']})"
            elif conflicts:
                status, why = "conflict", "; ".join(conflicts)
            elif (
                all(checks[k]["ok"] for k in ("numbers_compatible", "filename_evidence", "expected_size"))
                and margin_ok
                and not markers
            ):
                status = "recommended"
                why = "nombre+número, total impreso y tamaño son compatibles, sin conflictos ni duplicados"
                if variants:
                    why += (
                        f". Las {n} imágenes = {len(base)} cartas distintas ({set_cards} en el set) + {len(variants)} "
                        f"repeticiones {metrics['qualifiers']}: esas repeticiones son variantes y deben ir a impresiones "
                        f"de variante, nunca a la impresión base"
                    )
                    resolution = "set_alias_with_variants"
                else:
                    resolution = "set_alias"
                if total["ok"] is None:
                    notes.append("no hay total impreso para contrastar con el oficial")
            elif n < MIN_IMAGES or distinct_names < MIN_DISTINCT_NAMES:
                status, why = (
                    "insufficient_evidence",
                    f"muestra pequeña ({n} imágenes, {distinct_names} nombres distintos)",
                )
            elif ratio >= 0.3 or cand["name_score"] >= 0.85:
                status = "needs_visual_review"
                failed = [k for k, v in checks.items() if v["ok"] is False]
                why = f"evidencia parcial; no pasan: {failed or ['margen entre candidatos']}"
                resolution = "set_alias"
            else:
                status, why = "insufficient_evidence", f"solapamiento bajo ({ratio:.0%})"

        # --- promos
        promo_kind = None
        if "promo" in folder.lower():
            ratio = metrics.get("base_matched_ratio", 0)
            promo_kind = (
                "colección promocional presente en TCGdex" if cand and ratio >= RECOMMEND_RATIO
                else "parcialmente presente en TCGdex" if cand and ratio >= 0.3
                else "no existe en TCGdex (inglés)"
            )  # fmt: skip

        entries.append({
            "r2_folder": folder, "images": n, "audit_status": status, "resolution": resolution,
            "candidate_set_id": cand["tcgdex_set_id"] if cand else None,
            "candidate_name": cand["tcgdex_name"] if cand else None,
            "previous_recommendation": f["recommendation"], "category": f["category"], "promo_kind": promo_kind,
            "checks": checks, "metrics": metrics, "evidence": evidence, "conflicts": conflicts, "notes": notes,
            "justification": why,
        })  # fmt: skip

    summary = {
        "folders": len(entries),
        "by_status": {k: sum(1 for e in entries if e["audit_status"] == k) for k in STATUSES},
        "images_by_status": {k: sum(e["images"] for e in entries if e["audit_status"] == k) for k in STATUSES},
        "by_resolution": dict(sorted(Counter(e["resolution"] for e in entries).items())),
        "previous_confirmable_audited": dict(
            sorted(Counter(e["audit_status"] for e in entries if e["previous_recommendation"] == "confirmable").items())
        ),
    }
    return {"summary": summary, "folders": entries, "per_object": object_rows}


def proposals_v2(audit: dict) -> dict:
    out = []
    for e in audit["folders"]:
        if not e["candidate_set_id"] or e["audit_status"] in {"rejected", "insufficient_evidence"}:
            continue
        relation = {"printing_variant": "printing_variant", "per_card": "per_card"}.get(e["resolution"], "same_set")
        out.append(alias_entry(
            e["r2_folder"], e["candidate_set_id"], {"audit_status": e["audit_status"], "evidence": e["evidence"],
                                                      "justification": e["justification"]},
            conflicts=e["conflicts"], relation=relation,
        ))  # fmt: skip
    return {"aliases": out}


def object_proposals(audit: dict) -> dict:
    """Asociación individual por objeto, solo cuando hay un único candidato. Sin revisor."""
    return {
        "objects": [
            {"object_key": o["object_key"], "tcgdex_card_id": o["candidate_cards"][0], "status": "proposed",
             "evidence": {"method": "número+nombre exacto dentro del conjunto candidato", "r2_folder": o["r2_folder"]},
             "reviewed_by": None, "reviewed_at": None}
            for o in audit["per_object"] if o["outcome"] == "unique"
        ]
    }  # fmt: skip


def render_markdown(audit: dict) -> str:
    s = audit["summary"]
    lines = ["# Auditoría de alias de sets R2 -> TCGdex", "",
             f"Carpetas: **{s['folders']}** · estados: {s['by_status']} · imágenes: {s['images_by_status']}", "",
             "Nada está confirmado. `recommended` = evidencia documental sólida y sin conflictos; aun así lo aprueba una persona.",
             ""]  # fmt: skip
    titles = {
        "recommended": "Recomendadas (evidencia documental sólida)",
        "needs_visual_review": "Requieren revisión visual",
        "conflict": "Con conflicto",
        "rejected": "Rechazadas",
        "insufficient_evidence": "Evidencia insuficiente",
    }
    for st in STATUSES:
        group = [e for e in audit["folders"] if e["audit_status"] == st]
        if not group:
            continue
        lines += [f"## {titles[st]} ({len(group)})", ""]
        for e in group:
            cand = f"`{e['candidate_set_id']}` {e['candidate_name']!r}" if e["candidate_set_id"] else "sin candidato"
            lines += [f"### `{e['r2_folder']}` — {e['images']} imágenes — {cand} — resolución: {e['resolution']}",
                      f"- Justificación: {e['justification']}"]  # fmt: skip
            if e["promo_kind"]:
                lines.append(f"- Promo: {e['promo_kind']}")
            lines += [f"- ✔ {x}" for x in e["evidence"]]
            lines += [f"- ⚠ conflicto: {x}" for x in e["conflicts"]]
            lines += [f"- ℹ {x}" for x in e["notes"]]
            failing = [f"{k}: {v['detail']}" for k, v in e["checks"].items() if v["ok"] is False]
            lines += [f"- ✘ {x}" for x in failing]
            if "variant_analysis" in e["metrics"] or "per_object" in e["metrics"]:
                lines.append(
                    f"- Detalle: {json.dumps({k: e['metrics'][k] for k in ('variant_analysis', 'per_object') if k in e['metrics']}, ensure_ascii=False)}"
                )  # noqa: E501
            lines.append("")
    return "\n".join(lines)


def write_audit(audit: dict, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    names = ["r2-alias-audit.json", "r2-alias-audit.csv", "r2-alias-audit.md", "r2-alias-audit-objects.csv",
             "r2-alias-audit-proposals.json", "r2-object-mapping-proposals.json"]  # fmt: skip
    paths = [out_dir / n for n in names]
    paths[0].write_text(
        json.dumps(
            {"summary": audit["summary"], "folders": audit["folders"]}, ensure_ascii=False, indent=1, sort_keys=True
        ),
        encoding="utf-8",
    )  # noqa: E501
    fields = ["r2_folder", "images", "audit_status", "resolution", "candidate_set_id", "candidate_name", "previous_recommendation",
              "category", "promo_kind", "matched_ratio", "base_images", "variant_images", "set_cards", "conflicts", "evidence",
              "justification", "notes"]  # fmt: skip
    with paths[1].open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for e in audit["folders"]:
            m = e["metrics"]
            w.writerow({
                **{k: e[k] for k in fields if k in e and k not in {"conflicts", "evidence", "notes"}},
                "matched_ratio": m.get("matched_ratio", ""), "base_images": m["base_images"],
                "variant_images": m["variant_images"], "set_cards": m.get("set_cards", ""),
                "conflicts": " | ".join(e["conflicts"]), "evidence": " | ".join(e["evidence"]), "notes": " | ".join(e["notes"]),
            })  # fmt: skip
    paths[2].write_text(render_markdown(audit), encoding="utf-8")
    ofields = ["r2_folder", "object_key", "name", "number", "qualifiers", "outcome", "candidate_cards"]
    with paths[3].open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=ofields, extrasaction="ignore")
        w.writeheader()
        for o in audit["per_object"]:
            w.writerow({**o, "candidate_cards": " | ".join(o["candidate_cards"])})
    paths[4].write_text(json.dumps(proposals_v2(audit), ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    paths[5].write_text(
        json.dumps(object_proposals(audit), ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )
    return paths
