# ruff: noqa: E501
"""Evaluación con fotografías REALES, sin fuga de datos y sin entrenamiento.

- Galería = imágenes digitales de referencia verificadas (una por carta registrada) + distractores opcionales (identidad NO usada como verdad).
- Consultas = fotos verified de sesiones del conjunto de PRUEBA. Los umbrales de rechazo se calibran SOLO con el conjunto de CALIBRACIÓN.
- Cartas «desconocidas» = cartas verificadas cuya referencia se retira de la galería. Hay DOS grupos disjuntos: las de calibración
  (solo se usan sus fotos de calibración) y las de prueba (solo sus fotos de prueba); ninguna carta se usa en ambos.
- Se niega a producir métricas si los datos son ficticios (salvo --allow-fictitious, y entonces el informe lo declara) y marca como
  EXPLORATORIO todo lo que no alcance el mínimo de fotos de prueba.
"""

import hashlib
import math
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from app.dataset_lab.splits import load_split
from app.dataset_lab.store import Dataset, DatasetError, now
from app.recognition_lab.benchmark import KS, corner_error, load_bgr, pctl, query_once
from app.recognition_lab.index import RefIndex
from app.recognition_lab.rectify import to_canonical

MIN_TEST_PHOTOS, MIN_TEST_CARDS = 150, 20
BREAKDOWN_FIELDS = ("lighting_condition", "background", "sleeve_condition", "camera_angle")


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def load_photo(path: Path, max_side: int = 1600) -> np.ndarray:
    with Image.open(path) as im:
        rgb = np.asarray(ImageOps.exif_transpose(im).convert("RGB"))
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    scale = max_side / max(bgr.shape[:2])
    return cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else bgr


def is_fictitious(p: dict) -> bool:
    return bool(p.get("_ficticio")) or str(p.get("image_id", "")).startswith("EJEMPLO")


def reference_file(ds: Dataset, card_ref: str) -> Path | None:
    found = sorted(ds.references.glob(f"{card_ref}.*"))
    return found[0] if found else None


def evidence_level(test_photos: int, test_cards: int, sessions: int, fictitious: bool) -> dict:
    reasons = []
    if fictitious:
        reasons.append("los datos son FICTICIOS/de prueba: no son fotografías reales")
    if test_photos < MIN_TEST_PHOTOS:
        reasons.append(f"solo {test_photos} fotos de prueba (mínimo del piloto: {MIN_TEST_PHOTOS})")
    if test_cards < MIN_TEST_CARDS:
        reasons.append(f"solo {test_cards} cartas en prueba (mínimo del piloto: {MIN_TEST_CARDS})")
    if sessions < 1:
        reasons.append("ninguna sesión de prueba")
    return {"level": "exploratorio" if reasons else "piloto", "reasons": reasons, "is_real": not fictitious}


def choose_threshold(known: list[tuple[float, float]], unknown: list[tuple[float, float]]) -> dict | None:
    """Mejor (umbral, margen) por índice de Youden con consultas de CALIBRACIÓN. None si falta algún tipo."""
    if not known or not unknown:
        return None
    best = (-9.0, 0.0, 0.0)
    for t in sorted({round(s, 3) for s, _ in known + unknown}):
        for m in (0.0, 0.01, 0.03, 0.05):
            tpr = np.mean([s >= t and mg >= m for s, mg in known])
            fpr = np.mean([s >= t and mg >= m for s, mg in unknown])
            if tpr - fpr > best[0]:
                best = (float(tpr - fpr), t, m)
    return {
        "min_score": best[1],
        "min_margin": best[2],
        "youden_j": round(best[0], 3),
        "known": len(known),
        "unknown": len(unknown),
    }


def run_evaluation(
    ds: Dataset,
    split_name: str,
    extractors: dict,
    distractors: list[dict] | None = None,
    holdout_calibration: int = 0,
    holdout_test: int = 0,
    seed: int = 7,
    allow_fictitious: bool = False,
    max_side: int = 1600,
) -> dict:
    split = load_split(ds, split_name)
    assignment = split["assignment"]
    cards, photos = ds.cards, ds.photos
    verified = [
        p
        for p in photos.values()
        if p["verification_status"] == "verified"
        and cards.get(p.get("card_ref"), {}).get("verification_status") == "verified"
    ]
    fictitious = any(is_fictitious(p) for p in verified)
    if fictitious and not allow_fictitious:
        raise DatasetError(
            "El dataset contiene datos FICTICIOS: no se calculan métricas (usa --allow-fictitious solo para probar el flujo)"
        )
    cal = [p for p in verified if assignment.get(p["capture_session_id"]) == "calibration"]
    test = [p for p in verified if assignment.get(p["capture_session_id"]) == "test"]
    if not test:
        raise DatasetError("La partición no tiene fotos verificadas en el conjunto de prueba")
    # Defensa extra contra fuga: ninguna sesión en los dos conjuntos
    if {p["capture_session_id"] for p in cal} & {p["capture_session_id"] for p in test}:
        raise DatasetError("Una sesión aparece en calibración y prueba: partición inválida")

    with_ref = sorted(
        r
        for r in cards
        if cards[r]["verification_status"] == "verified"
        and cards[r].get("reference_verified_by")
        and reference_file(ds, r)
    )
    # una carta verificada SIN referencia verificada no puede ser ni conocida ni desconocida controlada: se excluye por completo
    no_ref_photos = sum(1 for p in cal + test if p["card_ref"] not in with_ref)
    cal = [p for p in cal if p["card_ref"] in with_ref]
    test = [p for p in test if p["card_ref"] in with_ref]
    if not test:
        raise DatasetError("No hay fotos de prueba de cartas con referencias verificadas (verifica las referencias y ejecuta references-fetch)")
    ranked = sorted(with_ref, key=lambda r: hashlib.sha256(f"{seed}:{r}".encode()).hexdigest())
    cal_holdout = set(ranked[:holdout_calibration])
    test_holdout = set(ranked[holdout_calibration : holdout_calibration + holdout_test])
    holdout = cal_holdout | test_holdout
    excluded = [p for p in cal if p["card_ref"] in test_holdout] + [p for p in test if p["card_ref"] in cal_holdout]
    cal = [
        p for p in cal if p["card_ref"] not in test_holdout
    ]  # las cartas desconocidas de prueba no se tocan en calibración
    test = [p for p in test if p["card_ref"] not in cal_holdout]
    if not test:
        raise DatasetError("No quedan fotos de prueba tras apartar las cartas desconocidas de calibración")
    gallery_cards = [r for r in with_ref if r not in holdout]
    if not gallery_cards:
        raise DatasetError(
            "No hay referencias verificadas en la galería (verifica las referencias y ejecuta references-fetch)"
        )

    def truth(p: dict) -> str | None:
        return p["card_ref"] if p["card_ref"] in gallery_cards else None

    evid = evidence_level(
        len(test), len({p["card_ref"] for p in test}), len({p["capture_session_id"] for p in test}), fictitious
    )
    out = {
        "banner": "FOTOS REALES — piloto" if evid["is_real"] and evid["level"] == "piloto" else "EXPLORATORIO — no citar como precisión",
        "created_at": now(), "evidence": evid,
        "dataset": {"manifest_sha256": hashlib.sha256((ds.manifest / "photos.json").read_bytes()).hexdigest(), "split": split_name,
                    "seed": seed, "calibration_photos": len(cal), "test_photos": len(test), "test_cards": len({p["card_ref"] for p in test}),
                    "gallery_cards": len(gallery_cards), "holdout_calibration_cards": sorted(cal_holdout), "holdout_test_cards": sorted(test_holdout), "excluded_photos": len(excluded), "distractors": len(distractors or []),
                    "excluded_photos_without_verified_reference": no_ref_photos},
        "extractors": {},
    }  # fmt: skip

    for name, ext in extractors.items():
        index = RefIndex(ext.dim)
        items = [
            (f"ref:{r}", f"card:{r}", ext.embed(to_canonical(load_bgr(reference_file(ds, r))))) for r in gallery_cards
        ]
        items += [
            (f"dist:{d['key']}", f"dist:{d['card_key']}", ext.embed(to_canonical(load_bgr(d["path"]))))
            for d in (distractors or [])
        ]
        index.add(items)
        timings = {"detect": [], "rectify_embed_search": []}

        def query(p: dict, timings=timings, index=index, ext=ext):
            t0 = time.perf_counter()
            img = load_photo(ds.root / p["file"]["path"], max_side)
            r = query_once(img, ext, index, timings)
            r["load_ms"] = (time.perf_counter() - t0) * 1000
            return r

        def score_margin(r: dict, index=index) -> tuple[float, float]:
            if not r["detected"]:
                return (-1.0, -1.0)
            (top_id, top), rest = r["results"][0], r["results"][1:]
            other = next((s for i, s in rest if index.card_of[i] != index.card_of[top_id]), 0.0)
            return (top, top - other)

        cal_q = [(p, query(p)) for p in cal]
        thr = choose_threshold(
            [score_margin(r) for p, r in cal_q if truth(p)],
            [score_margin(r) for p, r in cal_q if p["card_ref"] in cal_holdout],
        )
        test_q = [(p, query(p)) for p in test if p["card_ref"] in with_ref]
        known = [(p, r) for p, r in test_q if truth(p)]
        unknown = [(p, r) for p, r in test_q if p["card_ref"] in test_holdout]
        n = len(known)

        def hit(p, r, k, field=None, index=index):
            if not r["detected"]:
                return False
            ids = [i for i, _ in r["results"][:k]]
            if field is None:
                return f"ref:{p['card_ref']}" in ids
            return any(cards[i.split(":", 1)[1]].get(field) == p[field] for i in ids if i.startswith("ref:"))

        def topk(subset, field=None):
            if field:
                subset = [
                    (p, r)
                    for p, r in subset
                    if p.get(field) is not None and cards[p["card_ref"]].get(field) is not None
                ]
            m = len(subset)
            if m == 0:
                return {"n": 0, "nota": "sin etiquetas verificadas de este tipo"}
            res = {"n": m}
            for k in KS:
                hits = sum(hit(p, r, k, field) for p, r in subset)
                res[f"top{k}"] = round(hits / m, 3)
                res[f"recall_at_{k}"] = round(hits / m, 3)  # una referencia por carta: Recall@K = Top-K
            res["top1_ci95"] = wilson(sum(hit(p, r, 1, field) for p, r in subset), m)
            return res

        detected = [(p, r) for p, r in known if r["detected"]]
        with_corners = [(p, r) for p, r in detected if p.get("card_corners")]
        errs = [corner_error(r["quad"], np.array(p["card_corners"], np.float32)) for p, r in with_corners]
        res = {
            "queries_known": n, "queries_unknown": len(unknown),
            "card_identity": topk(known), "printing_identity": topk(known, "printing_id"), "variant_identity": topk(known, "variant_id"),
            "detection": {"detected_rate": round(len(detected) / n, 3) if n else None,
                          "annotated_corners": len(with_corners), "corner_error_median": round(float(np.median(errs)), 3) if errs else None,
                          "within_5pct": round(sum(e <= 0.05 for e in errs) / len(errs), 3) if errs else None},
            "rejection": None, "calibration": thr,
            "latency_ms": {k: {"p50": pctl(v, 50), "p95": pctl(v, 95)} for k, v in timings.items()},
            "breakdown_top1": {},
        }  # fmt: skip
        if thr:

            def accepted(r, thr=thr):
                s, m = score_margin(r)
                return r["detected"] and s >= thr["min_score"] and m >= thr["min_margin"]

            res["rejection"] = {
                "unknown_test_photos": len(unknown),
                "unknown_falsely_accepted_ci95": wilson(sum(accepted(r) for _, r in unknown), len(unknown)),
                "unknown_falsely_accepted": round(sum(accepted(r) for _, r in unknown) / len(unknown), 3)
                if unknown
                else None,
                "known_falsely_rejected": round(sum(1 for _, r in known if r["detected"] and not accepted(r)) / n, 3)
                if n
                else None,
                "known_correct_and_accepted": round(sum(1 for p, r in known if accepted(r) and hit(p, r, 1)) / n, 3)
                if n
                else None,
            }
        for f in BREAKDOWN_FIELDS:
            groups = defaultdict(list)
            for p, r in known:
                groups[p[f]].append(hit(p, r, 1))
            res["breakdown_top1"][f] = {
                k: {"n": len(v), "top1": round(sum(v) / len(v), 3), "ci95": wilson(sum(v), len(v))}
                for k, v in sorted(groups.items())
            }
        out["extractors"][name] = res
    return out


def render_markdown(r: dict) -> str:
    lines = [f"# Evaluación con fotografías — {r['banner']}", ""]
    if r["evidence"]["reasons"]:
        lines += ["**Limitaciones de esta evaluación:**", *[f"- {x}" for x in r["evidence"]["reasons"]], ""]
    d = r["dataset"]
    lines += [f"Partición `{d['split']}` · {d['test_photos']} fotos de prueba · {d['test_cards']} cartas · {d['gallery_cards']} referencias en la galería · "
              f"{len(d['holdout_calibration_cards'])}+{len(d['holdout_test_cards'])} cartas desconocidas (calibración+prueba) · {d['distractors']} distractores", ""]  # fmt: skip
    for name, e in r["extractors"].items():
        ci = e["card_identity"]
        lines += [f"## {name}", f"- Identidad de carta: {ci}", f"- Impresión: {e['printing_identity']}", f"- Variante: {e['variant_identity']}",
                  f"- Detección/perspectiva: {e['detection']}", f"- Rechazo: {e['rejection']} (calibración: {e['calibration']})", f"- Latencia: {e['latency_ms']}", ""]  # fmt: skip
    return "\n".join(lines)
