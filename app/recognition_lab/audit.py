# ruff: noqa: E501
"""Auditoría de las imágenes de referencia: resolución, formato, calidad, recortes y duplicados."""

from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

CARD_ASPECT = 63 / 88  # 0,716 (ancho/alto de una carta Pokémon)


def dhash(gray: np.ndarray, size: int = 8) -> int:
    small = cv2.resize(gray, (size + 1, size), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def image_stats(path: Path) -> dict:
    with Image.open(path) as im:
        fmt, mode, (w, h) = im.format, im.mode, im.size
        has_alpha = "A" in im.getbands()
        rgb = np.asarray(im.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    side = max(1, int(min(w, h) * 0.03))
    frame = np.concatenate([gray[:side].ravel(), gray[-side:].ravel(), gray[:, :side].ravel(), gray[:, -side:].ravel()])
    aspect = w / h
    return {
        "width": w, "height": h, "format": fmt, "mode": mode, "has_alpha": has_alpha, "aspect": round(aspect, 4),
        "aspect_ok": abs(aspect - CARD_ASPECT) <= 0.03,
        "sharpness": round(float(cv2.Laplacian(cv2.resize(gray, (256, int(256 / aspect))), cv2.CV_64F).var()), 1),
        "frame_mean": round(float(frame.mean()), 1), "frame_std": round(float(frame.std()), 1),
        "dhash": dhash(gray),
    }  # fmt: skip


def pct(values: list[float], q: float) -> float:
    return float(np.percentile(values, q)) if values else 0.0


def audit_sample(items: list[dict], cache_dir: Path) -> dict:
    stats = []
    for it in items:
        s = image_stats(cache_dir / it["cache"])
        stats.append({**it, **s})
    w = [s["width"] for s in stats]
    h = [s["height"] for s in stats]
    by_stratum = defaultdict(list)
    for s in stats:
        by_stratum[s["stratum"]].append(s)

    # Duplicados visuales dentro de la muestra, separando las repeticiones esperadas (misma carta)
    near_same_card, near_other_card = [], []
    for i in range(len(stats)):
        for j in range(i + 1, len(stats)):
            if hamming(stats[i]["dhash"], stats[j]["dhash"]) <= 3:
                pair = (stats[i]["key"].split("/", 3)[-1], stats[j]["key"].split("/", 3)[-1])
                (near_same_card if stats[i]["card_key"] == stats[j]["card_key"] else near_other_card).append(pair)

    pattern = [s for s in stats if s["stratum"] == "pattern_variants"]
    variant_dist = []
    for ck in sorted({s["card_key"] for s in pattern}):
        grp = [s for s in pattern if s["card_key"] == ck]
        base = [s for s in grp if not s["qualifiers"]]
        if base:
            variant_dist += [hamming(base[0]["dhash"], s["dhash"]) for s in grp if s["qualifiers"]]
    return {
        "images": len(stats),
        "formats": dict(Counter(s["format"] for s in stats)),
        "modes": dict(Counter(s["mode"] for s in stats)),
        "with_alpha": sum(s["has_alpha"] for s in stats),
        "width_px": {"min": min(w), "p05": pct(w, 5), "median": pct(w, 50), "p95": pct(w, 95), "max": max(w)},
        "height_px": {"min": min(h), "p05": pct(h, 5), "median": pct(h, 50), "p95": pct(h, 95), "max": max(h)},
        "below_600px_height": sum(1 for x in h if x < 600),
        "aspect_outside_card_ratio": [
            {"key": s["key"].split("/", 3)[-1], "aspect": s["aspect"]} for s in stats if not s["aspect_ok"]
        ][:12],
        "aspect_outside_count": sum(1 for s in stats if not s["aspect_ok"]),
        "sharpness_laplacian_var": {"p05": pct([s["sharpness"] for s in stats], 5), "median": pct([s["sharpness"] for s in stats], 50)},
        "by_stratum": {k: len(v) for k, v in sorted(by_stratum.items())},
        "near_duplicates_same_card": len(near_same_card),
        "near_duplicates_other_card": len(near_other_card),
        "near_duplicate_examples_other_card": near_other_card[:8],
        "pattern_variant_vs_base_dhash_hamming": {
            "pairs": len(variant_dist), "median": float(np.median(variant_dist)) if variant_dist else None,
            "max": max(variant_dist) if variant_dist else None,
        },
        "per_image": [{k: v for k, v in s.items() if k not in {"dhash"}} for s in stats],
    }  # fmt: skip


def inventory_duplicates(rows) -> dict:
    """Duplicados exactos por ETag en TODO el inventario (sin descargar nada)."""
    groups = defaultdict(list)
    for r in rows:
        if r.kind == "card" and r.etag:
            groups[r.etag].append(r)
    dup = [g for g in groups.values() if len(g) > 1]
    same_folder = sum(1 for g in dup if len({r.set_folder for r in g}) == 1)
    return {
        "card_images": sum(len(g) for g in groups.values()),
        "etag_groups_with_duplicates": len(dup),
        "images_in_duplicate_groups": sum(len(g) for g in dup),
        "groups_inside_one_folder": same_folder,
        "groups_across_folders": len(dup) - same_folder,
        "largest_group": max((len(g) for g in dup), default=0),
        "examples": [[r.object_key.split("Pokemon_Images/")[-1] for r in g[:3]] for g in dup[:5]],
    }
