# ruff: noqa: E501
"""Plan del piloto de 30 cartas: carencias declaradas, importación de plantillas, cobertura y validador de «piloto completo».

Nada aquí inventa datos: una categoría sin cartas suficientes se registra como CARENCIA con motivo, quién y cuándo.
"""

import csv
from collections import Counter, defaultdict
from pathlib import Path

from app.dataset_lab import schema
from app.dataset_lab.splits import leak_report
from app.dataset_lab.store import SESSION_ID, Dataset, DatasetError, atomic_write, now
from app.dataset_lab.validator import validate

DEFAULT_PLAN = {
    "schema_version": 1,
    "cards_target": 30,
    "photos_per_round": 5,
    "rounds": {"A": "calibration", "B": "test"},
    "category_targets": {c: 5 for c in schema.PILOT_CATEGORIES},
    "declared_gaps": [],
}
GAP_CATEGORIES = schema.PILOT_CATEGORIES + ("variante_patron",)
SUGGESTED = {**{i: "comun" for i in range(1, 6)}, **{i: "holo" for i in range(6, 11)}, **{i: "reverse_holo" for i in range(11, 16)},
             **{i: "full_art" for i in range(16, 21)}, **{i: "promo" for i in range(21, 26)}, **{i: "similar_dificil" for i in range(26, 31)}}  # fmt: skip

CARD_CSV_COLUMNS = [
    "slot", "categoria_sugerida", "nombre", "set", "numero", "idioma", "acabado", "categorias", "patron", "grupo_variante",
    "similar_a", "motivo_dificultad", "notas_estado", "carta_verificada_por", "nota_verificacion", "clave_referencia_r2",
    "referencia_verificada_por", "nota_referencia", "id_tcgdex", "card_id", "printing_id", "variant_id", "sin_carta_motivo",
]  # fmt: skip
SESSION_CSV_COLUMNS = ["session_id", "ronda", "fecha", "telefono", "lugar", "operador", "notas"]


def load_plan(ds: Dataset) -> dict:
    path = ds.root / "pilot.json"
    if not path.exists():
        return {**DEFAULT_PLAN, "declared_gaps": []}
    import json

    return {**DEFAULT_PLAN, **json.loads(path.read_text(encoding="utf-8"))}


def declare_gap(ds: Dataset, category: str, reason: str, by: str, slot: int | None = None) -> dict:
    """Registra una carencia (p. ej. «no tengo reverse holo»). No crea cartas ni sustituye nada."""
    if category not in GAP_CATEGORIES:
        raise DatasetError(f"Categoría inválida {category!r}; use una de {GAP_CATEGORIES}")
    if not reason.strip() or not by.strip():
        raise DatasetError("Declarar una carencia exige --reason y --by")
    plan = load_plan(ds)
    if any(g["category"] == category and g.get("slot") == slot for g in plan["declared_gaps"]):
        return next(g for g in plan["declared_gaps"] if g["category"] == category and g.get("slot") == slot)
    gap = {
        "category": category,
        "slot": slot,
        "reason": reason.strip(),
        "declared_by": by.strip(),
        "declared_at": now(),
    }
    plan["declared_gaps"].append(gap)
    atomic_write(ds.root / "pilot.json", plan)
    return gap


def _split_list(value: str) -> list[str]:
    return [x.strip() for x in value.replace(",", ";").split(";") if x.strip()]


def _check_row(g, slot: int | None, target: int) -> None:
    """Validaciones de una fila con carta o carencia; son las mismas en dry-run y en la importación real."""
    texts = (g("nombre"), g("sin_carta_motivo"), g("nota_verificacion"), g("nota_referencia"), g("carta_verificada_por"))
    if any(t.upper().startswith("EJEMPLO") for t in texts):
        raise DatasetError("La fila es de EJEMPLO (dato ficticio): no se importa al dataset real")
    if slot is None or not 1 <= slot <= target:
        raise DatasetError(f"slot debe ser un número entre 1 y {target}")
    if g("nombre") and g("sin_carta_motivo"):
        raise DatasetError("Una fila lleva nombre (carta) o sin_carta_motivo (carencia), no ambos")
    if not g("nombre"):
        return
    if g("clave_referencia_r2") and not (g("referencia_verificada_por") and g("nota_referencia")):
        raise DatasetError("clave_referencia_r2 exige referencia_verificada_por y nota_referencia (verificación manual)")
    if (g("referencia_verificada_por") or g("nota_referencia")) and not g("clave_referencia_r2"):
        raise DatasetError("referencia_verificada_por/nota_referencia sin clave_referencia_r2")
    if g("carta_verificada_por") and not g("nota_verificacion"):
        raise DatasetError("carta_verificada_por exige nota_verificacion")
    if g("nota_verificacion") and not g("carta_verificada_por"):
        raise DatasetError("nota_verificacion sin carta_verificada_por")
    if g("clave_referencia_r2") and not g("carta_verificada_por"):
        raise DatasetError("La referencia solo se verifica en cartas ya verificadas (rellena carta_verificada_por)")


def import_cards_csv(ds: Dataset, path: Path, by: str, dry_run: bool = False) -> dict:
    """Importa la plantilla de 30 cartas. Idempotente por `slot`. Filas vacías se ignoran; `sin_carta_motivo` declara carencia.

    Una fila inválida se informa y se omite sin escribir nada de ella (también en dry-run: mismas validaciones).
    """
    out = {"created": [], "skipped_existing": [], "gaps": [], "ignored_empty": 0, "errors": []}
    target = load_plan(ds)["cards_target"]
    existing_slots = {c.get("pilot_slot") for c in ds.cards.values()}
    seen_slots: set[int] = set()
    seen_keys: set[tuple] = {Dataset.identity_key(c) for c in ds.cards.values()}
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        g = lambda k, row=row: (row.get(k) or "").strip()  # noqa: E731
        slot = int(g("slot")) if g("slot").isdigit() else None
        if not g("nombre") and not g("sin_carta_motivo"):
            out["ignored_empty"] += 1
            continue
        try:
            _check_row(g, slot, target)
            if slot in seen_slots:
                raise DatasetError(f"slot {slot} repetido en el CSV")
            seen_slots.add(slot)
            if not g("nombre"):
                cats = _split_list(g("categorias")) or ([SUGGESTED[slot]] if slot in SUGGESTED else [])
                bad = [c for c in cats if c not in GAP_CATEGORIES]
                if bad or not cats:
                    raise DatasetError(f"Carencia sin categoría válida (categorias={bad or cats}; use {GAP_CATEGORIES})")
                for cat in cats:
                    out["gaps"].append({"category": cat, "slot": slot} if dry_run else declare_gap(ds, cat, g("sin_carta_motivo"), by, slot))
                continue
            if slot in existing_slots:
                out["skipped_existing"].append(slot)
                continue
            fields = dict(
                pilot_slot=slot, name=g("nombre"), set_name=g("set"), collector_number=g("numero"), language=g("idioma") or "unknown",
                finish=g("acabado") or "desconocido", categories=_split_list(g("categorias")) or None, pattern=g("patron") or None,
                variant_group=g("grupo_variante") or None, similar_to=g("similar_a") or None, difficulty_note=g("motivo_dificultad") or None,
                condition_notes=g("notas_estado") or None, tcgdex_card_id=g("id_tcgdex") or None, card_id=g("card_id") or None,
                printing_id=g("printing_id") or None, variant_id=g("variant_id") or None, reference_object_key=g("clave_referencia_r2") or None,
            )  # fmt: skip
            key = Dataset.identity_key({**fields, "set_name": fields["set_name"], "pattern": fields["pattern"] or "desconocido"})
            if key in seen_keys:
                raise DatasetError("Carta duplicada: otra fila o carta ya registrada tiene el mismo nombre, set, número, idioma, acabado y patrón")
            card = ds.add_card(persist=not dry_run, **fields)
            seen_keys.add(key)
            if dry_run:
                out["created"].append(slot)
                continue
            if g("carta_verificada_por"):
                ds.verify_card(card["card_ref"], g("carta_verificada_por"), g("nota_verificacion"))
                if g("clave_referencia_r2"):
                    ds.verify_reference(card["card_ref"], g("referencia_verificada_por"), g("nota_referencia"))
            out["created"].append(card["card_ref"])
        except DatasetError as exc:
            out["errors"].append({"slot": slot, "nombre": g("nombre"), "error": str(exc)})
    return out


def import_sessions_csv(ds: Dataset, path: Path, dry_run: bool = False) -> dict:
    out = {"created": [], "skipped_existing": [], "errors": []}
    known = set(ds.sessions)
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            g = lambda k, row=row: (row.get(k) or "").strip()  # noqa: E731
            sid = g("session_id")
            if not sid:
                continue
            try:
                if sid in known:
                    if sid in out["created"]:
                        raise DatasetError(f"session_id {sid} repetido en el CSV")
                    out["skipped_existing"].append(sid)
                    continue
                if not SESSION_ID.match(sid):
                    raise DatasetError("session_id: 3–41 caracteres (letras, números, _ . -)")
                if g("ronda") not in schema.ROUNDS:
                    raise DatasetError("ronda debe ser A o B")
                if not g("fecha"):
                    raise DatasetError("fecha es obligatoria")
                if not dry_run:
                    ds.add_session(sid, g("fecha"), g("telefono") or None, g("lugar") or None, g("operador") or None, g("notas") or None, g("ronda"))
                known.add(sid)
                out["created"].append(sid)
            except DatasetError as exc:
                out["errors"].append({"session_id": sid, "error": str(exc)})
    return out


def round_of(sessions: dict, photo: dict) -> str | None:
    return (sessions.get(photo["capture_session_id"]) or {}).get("round")


def status(ds: Dataset) -> dict:
    plan = load_plan(ds)
    cards, sessions, photos = ds.cards, ds.sessions, ds.photos
    target, per_round = plan["cards_target"], plan["photos_per_round"]
    gaps_by_cat = defaultdict(list)
    for g in plan["declared_gaps"]:
        gaps_by_cat[g["category"]].append(g)

    counts: dict[str, Counter] = defaultdict(Counter)
    other_status = defaultdict(Counter)
    for p in photos.values():
        if p["verification_status"] == "verified":
            counts[p.get("card_ref")][round_of(sessions, p) or "sin_ronda"] += 1
        else:
            other_status[p.get("card_ref")][p["verification_status"]] += 1

    table = []
    for ref, c in sorted(cards.items()):
        a, b = counts[ref]["A"], counts[ref]["B"]
        problems = []
        if c["verification_status"] != "verified":
            problems.append("carta sin verificar")
        if not (c.get("reference_verified_by") and c.get("reference_sha256")):
            problems.append("referencia sin verificar o sin traer (card-reference + references-fetch)")
        if c.get("pattern") == "desconocido":
            problems.append("patrón sin declarar (ninguno/pokeball/masterball)")
        if c.get("categories") == ["otra"]:
            problems.append("categorías sin asignar")
        if "similar_dificil" in (c.get("categories") or []) and not c.get("difficulty_note"):
            problems.append("falta motivo_dificultad")
        if a != per_round:
            problems.append(f"ronda A: {a}/{per_round} fotos verificadas")
        if b != per_round:
            problems.append(f"ronda B: {b}/{per_round} fotos verificadas")
        if counts[ref]["sin_ronda"]:
            problems.append("fotos verificadas en sesiones sin ronda")
        table.append({"slot": c.get("pilot_slot"), "card_ref": ref, "name": c["name"], "categories": c.get("categories"), "pattern": c.get("pattern"),
                      "verified": c["verification_status"] == "verified", "photos_A": a, "photos_B": b,
                      "photos_not_verified": dict(other_status[ref]), "complete": not problems, "problems": problems})  # fmt: skip

    coverage = []
    for cat, tgt in plan["category_targets"].items():
        members = [c for c in cards.values() if cat in (c.get("categories") or [])]
        verified = [c for c in members if c["verification_status"] == "verified"]
        complete = [
            r
            for r, c in cards.items()
            if cat in (c.get("categories") or []) and next(t for t in table if t["card_ref"] == r)["complete"]
        ]
        declared = bool(gaps_by_cat.get(cat))
        state = "cumple" if len(verified) >= tgt else ("carencia_declarada" if declared else "carencia_sin_declarar")
        coverage.append({"category": cat, "target": tgt, "registered": len(members), "verified": len(verified), "with_all_photos": len(complete),
                         "state": state, "declared_gaps": gaps_by_cat.get(cat, [])})  # fmt: skip
    overlaps = Counter(len(c.get("categories") or []) for c in cards.values() if c.get("categories") != ["otra"])
    patterns = Counter(c.get("pattern") for c in cards.values())
    groups = defaultdict(list)
    for r, c in cards.items():
        if c.get("variant_group"):
            groups[c["variant_group"]].append({"card_ref": r, "pattern": c.get("pattern")})

    split_leaks = None
    rounds_ok = all(s.get("round") for s in sessions.values())
    if sessions and rounds_ok:
        assignment = {
            sid: plan["rounds"][s["round"]] for sid, s in sessions.items() if s.get("round") in plan["rounds"]
        }
        split_leaks = leak_report(ds, assignment)
    photos_verified_dups = [
        p["image_id"]
        for p in photos.values()
        if p["verification_status"] == "verified" and p.get("possible_duplicates")
    ]
    unknown_cond = sum(
        1
        for p in photos.values()
        if p["verification_status"] == "verified"
        for f in schema.CONDITION_FIELDS
        if str(p[f]).startswith("desconoc")
    )
    val = validate(ds)

    checks = [
        ("cartas_registradas", len(cards) == target, f"{len(cards)} registradas (objetivo exacto {target})"),
        ("cartas_verificadas", sum(1 for c in cards.values() if c["verification_status"] == "verified") == target, f"{sum(1 for c in cards.values() if c['verification_status'] == 'verified')} verificadas físicamente"),
        ("fotos_por_carta", bool(table) and all(not [x for x in t["problems"] if "ronda" in x or "fotos" in x] for t in table), f"{sum(1 for t in table if t['photos_A'] == per_round and t['photos_B'] == per_round)} cartas con {per_round}+{per_round} fotos verificadas"),
        ("metadatos_de_carta", bool(table) and all(not [x for x in t["problems"] if x.startswith(('referencia', 'patrón', 'categorías', 'falta motivo'))] for t in table), "referencia verificada, patrón, categorías y motivo de dificultad"),
        ("rondas_en_sesiones", rounds_ok and bool(sessions), f"{sum(1 for s in sessions.values() if s.get('round'))}/{len(sessions)} sesiones con ronda"),
        ("particion_sin_fuga", split_leaks is not None and not split_leaks["blocking"], "sin cuasi-duplicados entre ronda A y B" if split_leaks is not None else "no evaluable (faltan rondas)"),
        ("cobertura_por_categoria", all(c["state"] != "carencia_sin_declarar" for c in coverage), "; ".join(f"{c['category']}={c['verified']}/{c['target']}({c['state']})" for c in coverage)),
        ("condiciones_conocidas", unknown_cond == 0, f"{unknown_cond} campos de condición 'desconocido' en fotos verificadas"),
        ("sin_duplicados_pendientes", not photos_verified_dups, f"{len(photos_verified_dups)} fotos verified con posibles duplicados"),
        ("validate_sin_errores", val["ok"], f"{len(val['errors'])} errores de validate"),
    ]  # fmt: skip
    complete = all(ok for _, ok, _ in checks)
    return {
        "complete": complete, "verdict": "PILOTO COMPLETO" if complete else "PILOTO INCOMPLETO",
        "has_declared_gaps": bool(plan["declared_gaps"]), "checks": [{"id": i, "ok": ok, "detail": d} for i, ok, d in checks],
        "plan": {k: plan[k] for k in ("cards_target", "photos_per_round", "category_targets")}, "declared_gaps": plan["declared_gaps"],
        "coverage_by_category": coverage, "cards_with_n_categories": dict(sorted(overlaps.items())),
        "patterns": {"pokeball": patterns["pokeball"], "masterball": patterns["masterball"], "ninguno": patterns["ninguno"],
                     "variant_groups": dict(groups), "nota": "Poké Ball/Master Ball son opcionales ('si tenemos acceso'); no bloquean el piloto"},  # fmt: skip
        "rounds": {r: sorted(s for s, v in sessions.items() if v.get("round") == r) for r in schema.ROUNDS},
        "cards": table, "validate_errors": val["errors"][:20],
    }  # fmt: skip


def render_markdown(st: dict) -> str:
    lines = [
        f"# Estado del piloto — {st['verdict']}",
        "",
        "## Comprobaciones",
        "| | Comprobación | Detalle |",
        "|---|---|---|",
    ]
    lines += [f"| {'✅' if c['ok'] else '❌'} | `{c['id']}` | {c['detail']} |" for c in st["checks"]]
    lines += [
        "",
        "## Cobertura por categoría (las categorías se solapan)",
        "| Categoría | Objetivo | Registradas | Verificadas | Con todas las fotos | Estado |",
        "|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {c['category']} | {c['target']} | {c['registered']} | {c['verified']} | {c['with_all_photos']} | {c['state']} |"
        for c in st["coverage_by_category"]
    ]
    if st["declared_gaps"]:
        lines += ["", "## Carencias declaradas (no se inventó ni sustituyó nada)"]
        lines += [
            f"- **{g['category']}** (slot {g.get('slot')}): {g['reason']} — {g['declared_by']}, {g['declared_at']}"
            for g in st["declared_gaps"]
        ]
    pat = st["patterns"]
    lines += ["", f"## Variantes de patrón\nPoké Ball: {pat['pokeball']} · Master Ball: {pat['masterball']} · grupos: {pat['variant_groups'] or 'ninguno'}", "", "## Cartas",
              "| Slot | Carta | Categorías | Patrón | A | B | Pendiente |", "|---|---|---|---|---|---|---|"]  # fmt: skip
    lines += [
        f"| {c['slot']} | {c['card_ref']} {c['name']} | {';'.join(c['categories'] or [])} | {c['pattern']} | {c['photos_A']}/5 | {c['photos_B']}/5 | {'; '.join(c['problems']) or '✅'} |"
        for c in st["cards"]
    ]
    return "\n".join(lines) + "\n"


def write_templates(out_dir: Path) -> list[Path]:
    """Genera las plantillas CSV (UTF-8 con BOM para Excel). La de 30 cartas va VACÍA: solo slot y categoría sugerida."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def write(name: str, columns: list[str], rows: list[dict]) -> Path:
        path = out_dir / name
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=columns)
            w.writeheader()
            w.writerows(rows)
        return path

    blank = [{"slot": i, "categoria_sugerida": SUGGESTED[i]} for i in range(1, 31)]
    example = [
        {"slot": 1, "categoria_sugerida": "comun", "nombre": "EJEMPLO Carta ficticia A", "set": "EJEMPLO Set", "numero": "001/100", "idioma": "es",
         "acabado": "normal", "categorias": "comun", "patron": "ninguno", "carta_verificada_por": "EJEMPLO", "nota_verificacion": "EJEMPLO: dato ficticio, no es una carta real"},
        {"slot": 26, "categoria_sugerida": "similar_dificil", "nombre": "EJEMPLO Carta ficticia B", "set": "EJEMPLO Set", "numero": "002/100", "idioma": "es",
         "acabado": "holo", "categorias": "holo;similar_dificil", "patron": "ninguno", "similar_a": "EJEMPLO Carta ficticia A",
         "motivo_dificultad": "EJEMPLO: arte casi igual a la otra carta", "carta_verificada_por": "EJEMPLO", "nota_verificacion": "EJEMPLO: dato ficticio"},
        {"slot": 12, "categoria_sugerida": "reverse_holo", "sin_carta_motivo": "EJEMPLO: no tengo ninguna reverse holo para fotografiar"},
    ]  # fmt: skip
    sessions = [{"session_id": "piloto-A-1", "ronda": "A"}, {"session_id": "piloto-B-1", "ronda": "B"}]
    return [
        write("plantilla-30-cartas.csv", CARD_CSV_COLUMNS, blank),
        write("plantilla-30-cartas.EJEMPLO.csv", CARD_CSV_COLUMNS, example),
        write("plantilla-sesiones.csv", SESSION_CSV_COLUMNS, sessions),
    ]
