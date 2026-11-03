# ruff: noqa: E501
"""Comparación SINTÉTICA entre búsqueda exacta (NumPy en memoria) y pgvector (exacta con operador `<=>` y HNSW) en el laboratorio privado.

Usa una tabla temporal propia (no toca las tablas del índice) que se elimina al terminar. Los vectores son sintéticos: mide equivalencia y latencia
de cada implementación, NO precisión de reconocimiento.
"""

import time

import numpy as np
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session


def synthetic_vectors(n: int, dim: int, seed: int) -> np.ndarray:
    """Cartas sintéticas con 1–4 imágenes cada una (variantes cercanas), vectores unitarios: imita grupos de impresiones parecidas."""
    rng = np.random.default_rng(seed)
    centers = rng.normal(size=(n // 3 + 1, dim)).astype(np.float32)
    idx = rng.integers(0, len(centers), n)
    v = centers[idx] + rng.normal(scale=0.35, size=(n, dim)).astype(np.float32)
    return (v / np.linalg.norm(v, axis=1, keepdims=True)).astype(np.float32)


def _pctl(xs: list[float], q: float) -> float:
    return round(float(np.percentile(xs, q)), 3)


def _vec(v: np.ndarray) -> str:
    return "[" + ",".join(f"{float(x):.8g}" for x in v) + "]"


def run_comparison(engine: Engine, n: int = 28000, n_queries: int = 200, dim: int = 768, seed: int = 11, k: int = 10, ef_search_values: tuple[int, ...] = (40, 100)) -> dict:
    data = synthetic_vectors(n, dim, seed)
    rng = np.random.default_rng(seed + 1)
    src = rng.integers(0, n, n_queries)
    queries = data[src] + rng.normal(scale=0.2, size=(n_queries, dim)).astype(np.float32)
    queries /= np.linalg.norm(queries, axis=1, keepdims=True)
    table = f"cmp_vectors_{seed}_{n}_{dim}"
    out: dict = {"synthetic": True, "n_vectors": n, "dim": dim, "n_queries": n_queries, "k": k}

    # exacta en memoria
    exact_ids, lat = [], []
    for q in queries:
        t = time.perf_counter()
        sims = data @ q
        top = np.argpartition(-sims, k)[:k]
        top = top[np.argsort(-sims[top])]
        lat.append((time.perf_counter() - t) * 1000)
        exact_ids.append(top)
    out["exact_numpy"] = {"latency_ms_p50": _pctl(lat, 50), "latency_ms_p95": _pctl(lat, 95), "memory_mb": round(data.nbytes / 1e6, 1)}

    with Session(engine) as s:
        s.execute(text(f"DROP TABLE IF EXISTS {table}"))
        s.execute(text(f"CREATE TABLE {table} (id integer PRIMARY KEY, embedding vector({dim}) NOT NULL)"))
        t = time.perf_counter()
        for start in range(0, n, 1000):
            rows = [{"i": i, "e": _vec(data[i])} for i in range(start, min(n, start + 1000))]
            s.execute(text(f"INSERT INTO {table} (id, embedding) VALUES (:i, CAST(:e AS vector))"), rows)
        s.commit()
        out["load_seconds"] = round(time.perf_counter() - t, 1)

        def run(label: str, setup: str | None = None) -> dict:
            got, lat = [], []
            for q in queries:
                if setup:
                    s.execute(text(setup))
                t = time.perf_counter()
                rows = s.execute(text(f"SELECT id FROM {table} ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"), {"q": _vec(q), "k": k}).all()
                lat.append((time.perf_counter() - t) * 1000)
                got.append([r[0] for r in rows])
            top1 = float(np.mean([g[0] == e[0] for g, e in zip(got, exact_ids, strict=True)]))
            recall = float(np.mean([len(set(g) & set(e.tolist())) / k for g, e in zip(got, exact_ids, strict=True)]))
            return {"top1_agreement_with_numpy": round(top1, 4), "recall_at_k": round(recall, 4), "latency_ms_p50": _pctl(lat, 50), "latency_ms_p95": _pctl(lat, 95)}

        s.execute(text("SET enable_indexscan = off"))  # sin índice: recorrido secuencial exacto
        out["pgvector_exact_seqscan"] = run("seq")
        s.execute(text("RESET enable_indexscan"))
        t = time.perf_counter()
        s.execute(text(f"CREATE INDEX ix_{table}_hnsw ON {table} USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"))
        s.commit()
        out["hnsw_build_seconds"] = round(time.perf_counter() - t, 1)
        out["hnsw_index_mb"] = round(s.scalar(text(f"SELECT pg_relation_size('ix_{table}_hnsw')")) / 1e6, 1)
        out["table_mb"] = round(s.scalar(text(f"SELECT pg_relation_size('{table}')")) / 1e6, 1)
        out["pgvector_hnsw"] = {f"ef_search={ef}": run("hnsw", f"SET hnsw.ef_search = {int(ef)}") for ef in ef_search_values}
        s.execute(text(f"DROP TABLE {table}"))
        s.commit()
    return out
