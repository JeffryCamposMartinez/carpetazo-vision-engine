# ruff: noqa: E501
"""Comparación pareada de variantes de normalización (`raw` frente a `normalized`) con LAS MISMAS fotos y referencias.

No asume que normalizar ayude: mide acierto Top-1/Top-5 por variante, las discordancias pareadas (lo que gana una y pierde la otra),
un contraste exacto de signos (McNemar exacto) y un intervalo bootstrap de la diferencia de Top-1. Se espera usar con fotos reales:
elegir la variante con la ronda A (calibración) y confirmarla UNA vez con la ronda B. Con datos sintéticos o pocos casos el informe se etiqueta EXPLORATORIO.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from app.recognition.normalization import NormalizationConfig, normalize_canonical

MIN_QUERIES_FOR_EVIDENCE = 150


@dataclass(frozen=True)
class Query:
    query_id: str
    truth: str  # id de la carta correcta (verificada)
    variants: dict[str, tuple[np.ndarray, np.ndarray]]  # variante -> (carta 0°, carta 180°) rectificadas


def _exact_sign_test(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2**n)


def compare_variants(
    queries: list[Query],
    references: dict[str, np.ndarray],  # id de carta -> referencia digital ya a 440x614 (sin normalizar)
    embed: Callable[[np.ndarray], np.ndarray],
    config: NormalizationConfig,
    variants: tuple[str, ...] = ("raw", "normalized"),
    seed: int = 7,
    synthetic: bool = False,
) -> dict:
    ref_ids = sorted(references)
    gallery = {}
    for v in variants:
        gallery[v] = np.stack([embed(references[r] if v == "raw" else normalize_canonical(references[r], config)[0]) for r in ref_ids])
    hits: dict[str, list[tuple[bool, bool]]] = {v: [] for v in variants}  # (top1, top5)
    for q in queries:
        for v in variants:
            best = None
            for img in q.variants[v]:
                sims = gallery[v] @ embed(img)
                if best is None or sims.max() > best.max():
                    best = sims
            order = np.argsort(-best)
            top = [ref_ids[i] for i in order[:5]]
            hits[v].append((top[0] == q.truth, q.truth in top))
    n = len(queries)
    result = {"n_queries": n, "n_references": len(ref_ids), "config": {"tag": config.tag, **config.__dict__}, "variants": {}}
    for v in variants:
        result["variants"][v] = {"top1": round(sum(h[0] for h in hits[v]) / n, 4) if n else None, "top5": round(sum(h[1] for h in hits[v]) / n, 4) if n else None}
    if len(variants) == 2 and n:
        a, b = variants
        t1a = np.array([h[0] for h in hits[a]])
        t1b = np.array([h[0] for h in hits[b]])
        only_a, only_b = int((t1a & ~t1b).sum()), int((~t1a & t1b).sum())
        rng = np.random.default_rng(seed)
        diffs = [float((t1b[i].astype(int) - t1a[i].astype(int)).mean()) for i in (rng.integers(0, n, n) for _ in range(2000))]
        result["paired_top1"] = {"only_" + a + "_correct": only_a, "only_" + b + "_correct": only_b, "mcnemar_exact_p": round(_exact_sign_test(only_a, only_b), 4),
                                 "diff_" + b + "_minus_" + a: round(float(t1b.mean() - t1a.mean()), 4), "bootstrap95": [round(float(np.percentile(diffs, 2.5)), 4), round(float(np.percentile(diffs, 97.5)), 4)]}  # fmt: skip
    reasons = []
    if synthetic:
        reasons.append("datos sintéticos")
    if n < MIN_QUERIES_FOR_EVIDENCE:
        reasons.append(f"solo {n} consultas (mínimo {MIN_QUERIES_FOR_EVIDENCE})")
    result["evidence"] = {"level": "exploratorio" if reasons else "piloto", "reasons": reasons, "note": "No se asume que normalizar mejore el reconocimiento: decide la diferencia pareada en fotos reales."}
    return result
