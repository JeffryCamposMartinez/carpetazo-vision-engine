# ruff: noqa: E501
"""Benchmark SINTÉTICO del pipeline detección → rectificación → embedding → búsqueda → rechazo.

Las escenas son simuladas a partir de las propias imágenes de referencia: miden si el pipeline funciona y comparan
extractores, pero NO estiman la precisión con fotos reales de teléfono (no existen aún; ver docs/recognition/).
"""

import random
import time
import tracemalloc
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

from app.recognition_lab.augment import LEVELS, make_scene
from app.recognition_lab.features import Extractor
from app.recognition_lab.index import RefIndex
from app.recognition_lab.rectify import find_card_quad, to_canonical, warp_card

KS = (1, 5, 10)


def load_bgr(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:  # respaldo si OpenCV no trae WebP
        from PIL import Image

        img = cv2.cvtColor(np.asarray(Image.open(path).convert("RGB")), cv2.COLOR_RGB2BGR)
    return img


def corner_error(pred: np.ndarray, truth: np.ndarray) -> float:
    """Error medio de esquinas / alto de la carta; mínimo sobre los 4 desplazamientos cíclicos (orientación libre)."""
    height = np.linalg.norm(truth[3] - truth[0])
    return min(float(np.linalg.norm(np.roll(pred, s, axis=0) - truth, axis=1).mean() / height) for s in range(4))


def pctl(values: list[float], q: float) -> float:
    return round(float(np.percentile(values, q)), 2) if values else 0.0


def query_once(scene, ext: Extractor, index: RefIndex, timings: dict) -> dict:
    t0 = time.perf_counter()
    quad, score = find_card_quad(scene)
    t1 = time.perf_counter()
    timings["detect"].append((t1 - t0) * 1000)
    if quad is None:
        return {"detected": False}
    card = warp_card(scene, quad)
    best = None
    for rotated in (card, cv2.rotate(card, cv2.ROTATE_180)):  # la orientación de 180° no se conoce de antemano
        vec = ext.embed(rotated)
        res = index.search(vec, max(KS))
        if best is None or res[0][1] > best[0][1]:
            best = res
    t2 = time.perf_counter()
    timings["rectify_embed_search"].append((t2 - t1) * 1000)
    return {"detected": True, "quad": quad, "quad_score": score, "results": best}


def evaluate(queries: list[dict], index: RefIndex, items_by_id: dict, group_size: dict) -> dict:
    n = len(queries)
    detected = [q for q in queries if q["detected"]]
    out = {"queries": n, "detection_rate": round(len(detected) / n, 3) if n else 0.0}
    if detected:
        errs = [q["corner_err"] for q in detected]
        out["corner_error_median"] = round(float(np.median(errs)), 3)
        out["detected_within_5pct"] = round(sum(e <= 0.05 for e in errs) / n, 3)
    exact = {k: 0 for k in KS}
    card = {k: 0 for k in KS}
    recall = {k: 0.0 for k in KS}
    variant_hits = variant_total = 0
    for q in detected:
        ids = [i for i, _ in q["results"]]
        truth_card = index.card_of[q["truth_id"]]
        for k in KS:
            top = ids[:k]
            exact[k] += q["truth_id"] in top
            cards = [index.card_of[i] for i in top]
            card[k] += truth_card in cards
            recall[k] += sum(1 for i in top if index.card_of[i] == truth_card) / group_size[truth_card]
        if group_size[truth_card] > 1 and index.card_of[ids[0]] == truth_card:
            variant_total += 1
            variant_hits += ids[0] == q["truth_id"]
    for k in KS:  # los no detectados cuentan como fallo
        out[f"exact_image_top{k}"] = round(exact[k] / n, 3) if n else 0.0
        out[f"card_identity_top{k}"] = round(card[k] / n, 3) if n else 0.0
        out[f"card_recall_at_{k}"] = round(recall[k] / n, 3) if n else 0.0
    out["variant_exact_given_card_top1"] = {
        "n": variant_total,
        "accuracy": round(variant_hits / variant_total, 3) if variant_total else None,
    }
    return out


def run_benchmark(
    items: list[dict],
    cache_dir: Path,
    ext: Extractor,
    seed: int = 11,
    max_queries: int = 240,
    unknown_fraction: float = 0.2,
) -> dict:
    rng = random.Random(seed)
    cards = sorted({i["card_key"] for i in items})
    rng.shuffle(cards)
    n_unknown = max(1, int(len(cards) * unknown_fraction))
    unknown_cards, gallery_cards = set(cards[:n_unknown]), set(cards[n_unknown:])
    gallery = [i for i in items if i["card_key"] in gallery_cards]
    unknown = [i for i in items if i["card_key"] in unknown_cards]
    images = {i["key"]: load_bgr(cache_dir / i["cache"]) for i in items}
    group_size = defaultdict(int)
    for i in gallery:
        group_size[i["card_key"]] += 1

    # 1) Construir el índice (memoria + tiempo de indexado)
    index = RefIndex(ext.dim)
    tracemalloc.start()
    t0 = time.perf_counter()
    index.add([(i["key"], i["card_key"], ext.embed(to_canonical(images[i["key"]]))) for i in gallery])
    build_s = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # 2) Consultas: escenas de las imágenes de la galería (conocidas) y de cartas fuera del índice (desconocidas)
    timings = {"detect": [], "rectify_embed_search": []}
    pick = lambda pool, n, salt: random.Random(seed + salt).sample(pool, min(n, len(pool)))  # noqa: E731
    results: dict = {"levels": {}, "strata": {}}
    scored: dict[str, dict] = {}
    for li, (lname, level) in enumerate(LEVELS.items()):
        known_q, unknown_q = [], []
        for pool, bucket, truth in (
            (pick(gallery, max_queries, li), known_q, True),
            (pick(unknown, max_queries, 100 + li), unknown_q, False),
        ):
            for n_i, it in enumerate(pool):
                scene, gt = make_scene(
                    images[it["key"]], seed * 1000 + li * 100000 + n_i + (0 if truth else 50000), level
                )
                r = query_once(scene, ext, index, timings)
                r |= {"truth_id": it["key"] if truth else None, "stratum": it["stratum"], "card_key": it["card_key"]}
                if r["detected"]:
                    r["corner_err"] = corner_error(r["quad"], gt)
                bucket.append(r)
        scored[lname] = {"known": known_q, "unknown": unknown_q}
        results["levels"][lname] = evaluate(known_q, index, {}, dict(group_size))
        by_stratum = defaultdict(list)
        for q in known_q:
            by_stratum[q["stratum"]].append(q)
        results["strata"][lname] = {s: evaluate(v, index, {}, dict(group_size)) for s, v in sorted(by_stratum.items())}

    # 3) Rechazo de desconocidas: umbral calibrado en escenas 'normal' distintas (otra semilla) y evaluado en todas
    cal_known = [
        query_once(
            make_scene(images[it["key"]], 7_000_000 + n_i, LEVELS["normal"])[0],
            ext,
            index,
            {"detect": [], "rectify_embed_search": []},
        )
        for n_i, it in enumerate(pick(gallery, 80, 900))
    ]
    cal_unknown = [
        query_once(
            make_scene(images[it["key"]], 8_000_000 + n_i, LEVELS["normal"])[0],
            ext,
            index,
            {"detect": [], "rectify_embed_search": []},
        )
        for n_i, it in enumerate(pick(unknown, 80, 901))
    ]

    def top(q):
        return (
            (
                q["results"][0][1],
                q["results"][0][1]
                - next((s for i, s in q["results"][1:] if index.card_of[i] != index.card_of[q["results"][0][0]]), 0.0),
            )
            if q["detected"]
            else (-1.0, -1.0)
        )

    cand = sorted({round(top(q)[0], 3) for q in cal_known + cal_unknown})
    best = (-9.0, 0.0, 0.0)
    for t in cand:
        for m in (0.0, 0.01, 0.03):
            tpr = np.mean([top(q)[0] >= t and top(q)[1] >= m for q in cal_known])
            fpr = np.mean([top(q)[0] >= t and top(q)[1] >= m for q in cal_unknown])
            if tpr - fpr > best[0]:
                best = (float(tpr - fpr), t, m)
    _, t_score, t_margin = best
    results["rejection"] = {
        "calibration": {
            "min_score": t_score,
            "min_margin": t_margin,
            "youden_j": round(best[0], 3),
            "known_scenes": len(cal_known),
            "unknown_scenes": len(cal_unknown),
        }
    }
    for lname, parts in scored.items():

        def accepted(q):
            return q["detected"] and index.decide(q["results"], t_score, t_margin).status == "known"

        known_ok = [
            q
            for q in parts["known"]
            if accepted(q) and index.card_of[q["results"][0][0]] == index.card_of[q["truth_id"]]
        ]
        results["rejection"][lname] = {
            "known_correct_and_accepted": round(len(known_ok) / max(1, len(parts["known"])), 3),
            "known_falsely_rejected": round(
                sum(1 for q in parts["known"] if q["detected"] and not accepted(q)) / max(1, len(parts["known"])), 3
            ),
            "unknown_falsely_accepted": round(
                sum(1 for q in parts["unknown"] if accepted(q)) / max(1, len(parts["unknown"])), 3
            ),
            "unknown_queries": len(parts["unknown"]),
        }

    results["performance"] = {
        "extractor": ext.name,
        "embedding_dim": ext.dim,
        "gallery_images": len(gallery),
        "gallery_cards": len(gallery_cards),
        "unknown_cards": len(unknown_cards),
        "index_build_s": round(build_s, 2),
        "index_matrix_kb": round(index.memory_bytes / 1024, 1),
        "index_build_peak_kb_python": round(peak / 1024, 1),
        "latency_ms": {k: {"p50": pctl(v, 50), "p95": pctl(v, 95)} for k, v in timings.items()},
        "extrapolated_matrix_mb_for_28k_images": round(index.memory_bytes / max(1, len(index)) * 28000 / 1_048_576, 1),
    }
    results["warning"] = (
        "SINTÉTICO: escenas simuladas desde las mismas imágenes de referencia; no es precisión con fotos reales."
    )
    return results
