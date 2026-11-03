# ruff: noqa: E501
"""Experimento DINOv2-small congelado (sin entrenamiento). Se ejecuta en el entorno ML independiente:

    $env:HF_HOME = "$env:USERPROFILE\\vision-ml-lab\\hf-home"
    & "$env:USERPROFILE\\vision-ml-lab\\venv\\Scripts\\python.exe" scripts\\ml\\dinov2_experiment.py

Lee la muestra de R2 ya en caché (datasets/lab-cache/r2-sample). No toca R2, ni producción, ni el PostgreSQL original.
Las escenas son SINTÉTICAS: los resultados NO son precisión con fotos reales.
"""

import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import psutil  # noqa: E402
import torch  # noqa: E402

from app.images.inventory import normalize_name, normalize_number, normalize_set_folder  # noqa: E402
from app.recognition_lab.benchmark import load_bgr, run_benchmark  # noqa: E402
from app.recognition_lab.features import REGISTRY, Dinov2Extractor  # noqa: E402
from app.recognition_lab.rectify import to_canonical  # noqa: E402

CACHE = ROOT / "datasets" / "lab-cache" / "r2-sample"
REPORT = ROOT / "evaluation" / "reports" / "dinov2-small-experiment.json"
MODEL_ID = "facebook/dinov2-small"
SEED, MAX_Q = 11, 200
proc = psutil.Process()


def mb(x: float) -> float:
    return round(x / 1_048_576, 1)


def rss() -> float:
    return mb(proc.memory_info().rss)


def nvidia_used_mb() -> int | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return None


def env_info() -> dict:
    import numpy
    import transformers

    info = {
        "python": sys.version.split()[0], "platform": platform.platform(), "torch": torch.__version__, "torch_cuda_build": torch.version.cuda,
        "transformers": transformers.__version__, "numpy": numpy.__version__, "opencv": cv2.__version__, "psutil": psutil.__version__,
        "cuda_available": torch.cuda.is_available(), "cudnn": torch.backends.cudnn.version() if torch.cuda.is_available() else None,
    }  # fmt: skip
    if torch.cuda.is_available():
        free, total = torch.cuda.mem_get_info()
        info |= {
            "gpu": torch.cuda.get_device_name(0), "compute_capability": ".".join(map(str, torch.cuda.get_device_capability(0))),
            "torch_arch_list": torch.cuda.get_arch_list(), "vram_total_mb": mb(total), "vram_free_at_start_mb": mb(free),
            "nvidia_smi_used_mb_at_start": nvidia_used_mb(),
        }  # fmt: skip
        x = torch.randn(256, 256, device="cuda")
        info["cuda_kernel_smoke_test"] = bool(torch.isfinite((x @ x).sum()).item())
    return info


def model_files() -> list[dict]:
    hub = Path(os.environ["HF_HOME"]) / "hub" / "models--facebook--dinov2-small"
    out = []
    for p in sorted((hub / "snapshots").rglob("*")):
        if p.is_file() or p.is_symlink():
            real = p.resolve()
            out.append(
                {
                    "file": p.name,
                    "snapshot": p.parent.name,
                    "size_mb": mb(real.stat().st_size),
                    "sha256": hashlib.sha256(real.read_bytes()).hexdigest(),
                }
            )
    return out


def verified_keys(items: list[dict]) -> set[str]:
    """Identidad con respaldo documental: el data.json de TCGplayer/tcgcsv coincide en nombre y número con el nombre del objeto."""
    raw = json.loads((ROOT / "evaluation" / "reports" / "r2-datajson.json").read_text(encoding="utf-8"))
    dj = {tuple(k.split("\x1f")): v for k, v in raw.items()}
    ok = set()
    for it in items:
        parts = it["key"].split("/")
        d = dj.get((normalize_set_folder(parts[-3]), parts[-2]))
        if d and d.get("number") and it.get("number"):
            if normalize_name(d["name"]) == normalize_name(it["name"]) and normalize_number(
                str(d["number"]).split("/")[0]
            ) == normalize_number(it["number"]):
                ok.add(it["key"])
    return ok


def latency(ext, image, runs: int) -> dict:
    for _ in range(5):
        ext.embed(image)
    times = []
    for _ in range(runs):
        t = time.perf_counter()
        ext.embed(image)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        times.append((time.perf_counter() - t) * 1000)
    return {
        "runs": runs,
        "p50_ms": round(statistics.median(times), 2),
        "p95_ms": round(float(np.percentile(times, 95)), 2),
    }


def subset_metrics(per_query: list[dict], keys: set[str]) -> dict:
    out = {}
    for level in ("easy", "normal", "hard"):
        q = [p for p in per_query if p["known"] and p["level"] == level and p["truth_id"] in keys]
        out[level] = {
            "n": len(q),
            "card_top1": round(sum(p["card_ok"] for p in q) / len(q), 3) if q else None,
            "exact_top1": round(sum(p["exact_ok"] for p in q) / len(q), 3) if q else None,
        }
    return out


def strip(res: dict) -> dict:
    return {k: v for k, v in res.items() if not k.startswith("_")}


def pgvector_check(index, queries: list[np.ndarray]) -> dict:
    import psycopg
    from pgvector.psycopg import register_vector

    base = {"host": "127.0.0.1", "port": 5498, "user": "vision"}
    try:
        with psycopg.connect(dbname="postgres", autocommit=True, **base) as c:
            if not c.execute("SELECT 1 FROM pg_database WHERE datname = 'vision_ml_lab'").fetchone():
                c.execute("CREATE DATABASE vision_ml_lab")
    except psycopg.OperationalError as exc:
        return {"skipped": f"clúster del laboratorio no disponible en 5498: {exc}"}
    with psycopg.connect(dbname="vision_ml_lab", autocommit=True, **base) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(c)
        version = c.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()[0]
        dim = index.dim
        c.execute("DROP TABLE IF EXISTS lab_dino_refs")
        c.execute(
            f"CREATE TABLE lab_dino_refs (image_key text PRIMARY KEY, card_key text NOT NULL, embedding vector({dim}) NOT NULL)"
        )
        with c.cursor() as cur:
            with cur.copy("COPY lab_dino_refs (image_key, card_key, embedding) FROM STDIN (FORMAT BINARY)") as cp:
                cp.set_types(["text", "text", "vector"])
                for i, key in enumerate(index.ids):
                    cp.write_row((key, index.card_of[key], index._matrix[i]))
        t = time.perf_counter()
        c.execute(
            "CREATE INDEX lab_dino_hnsw ON lab_dino_refs USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
        )
        build_ms = (time.perf_counter() - t) * 1000
        sizes = c.execute("SELECT pg_relation_size('lab_dino_hnsw'), pg_relation_size('lab_dino_refs')").fetchone()

        def run(sql: str, setup: str | None = None):
            ids, times = [], []
            if setup:
                c.execute(setup)
            for q in queries:
                t0 = time.perf_counter()
                rows = c.execute(sql, (q, q)).fetchall()
                times.append((time.perf_counter() - t0) * 1000)
                ids.append([r[0] for r in rows])
            c.execute("RESET enable_indexscan")
            return ids, times

        sql = "SELECT image_key, embedding <=> %s AS d FROM lab_dino_refs ORDER BY embedding <=> %s LIMIT 10"
        exact_ids, exact_t = run(sql, "SET enable_indexscan = off")
        hnsw_ids, hnsw_t = run(sql)
        numpy_ids = [[i for i, _ in index.search(q, 10)] for q in queries]
        recall10 = float(np.mean([len(set(h) & set(e)) / 10 for h, e in zip(hnsw_ids, exact_ids, strict=True)]))
        out = {
            "pgvector_version": version, "rows": len(index), "dim": dim, "queries": len(queries),
            "exact_top1_equals_numpy_top1": float(np.mean([a[0] == b[0] for a, b in zip(exact_ids, numpy_ids, strict=True)])),
            "hnsw_top1_equals_exact_top1": float(np.mean([a[0] == b[0] for a, b in zip(hnsw_ids, exact_ids, strict=True)])),
            "hnsw_recall_at_10_vs_exact": round(recall10, 4),
            "latency_ms_exact": {"p50": round(statistics.median(exact_t), 2), "p95": round(float(np.percentile(exact_t, 95)), 2)},
            "latency_ms_hnsw": {"p50": round(statistics.median(hnsw_t), 2), "p95": round(float(np.percentile(hnsw_t, 95)), 2)},
            "hnsw_build_ms": round(build_ms, 1), "hnsw_index_kb": round(sizes[0] / 1024, 1), "table_kb": round(sizes[1] / 1024, 1),
        }  # fmt: skip
        c.execute("DROP TABLE lab_dino_refs")  # no dejar datos de prueba
        return out


def main() -> None:
    result = {
        "seed": SEED,
        "max_queries_per_level": MAX_Q,
        "warning": "SINTÉTICO: escenas simuladas desde las propias referencias; no es precisión con fotos reales.",
    }
    result["environment"] = env_info()
    result["ram_mb"] = {"after_torch_import": rss()}
    items = json.loads((CACHE / "sample.json").read_text(encoding="utf-8"))
    verified = verified_keys(items)
    result["sample"] = {"images": len(items), "verified_by_datajson": len(verified)}

    vram0 = nvidia_used_mb()
    t = time.perf_counter()
    gpu = Dinov2Extractor(
        MODEL_ID, pooling="cls_mean", size=(224, 308), device="cuda" if torch.cuda.is_available() else "cpu"
    )
    result["model_load_s"] = round(time.perf_counter() - t, 2)
    result["model_files"] = model_files()
    result["ram_mb"]["after_model_load"] = rss()
    result["parameters_m"] = round(sum(p.numel() for p in gpu.model.parameters()) / 1e6, 2)

    canon = to_canonical(load_bgr(CACHE / items[0]["cache"]))
    result["latency_single_embedding"] = {"gpu": latency(gpu, canon, 100) if gpu.device == "cuda" else None}
    cpu = Dinov2Extractor(MODEL_ID, pooling="cls_mean", size=(224, 308), device="cpu", local_only=True)
    result["latency_single_embedding"]["cpu"] = latency(cpu, canon, 30)
    result["torch_threads"] = torch.get_num_threads()
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    variants = {
        "dinov2_small_cls_mean_224x308": gpu,
        "dinov2_small_cls_224x308": Dinov2Extractor(MODEL_ID, pooling="cls", size=(224, 308), local_only=True),
        "dinov2_small_cls_mean_224x224": Dinov2Extractor(
            MODEL_ID, pooling="cls_mean", size=(224, 224), local_only=True
        ),
    }
    runs, kept = {}, None
    for name, ext in variants.items():
        print("==", name, flush=True)
        r = run_benchmark(
            items, CACHE, ext, seed=SEED, max_queries=MAX_Q, keep=(name == "dinov2_small_cls_mean_224x308")
        )
        if name == "dinov2_small_cls_mean_224x308":
            kept = r
        runs[name] = {**strip(r), "verified_subset": subset_metrics(r["per_query"], verified)}
    for name in ("grid", "phash"):
        print("==", name, flush=True)
        r = run_benchmark(items, CACHE, REGISTRY[name](), seed=SEED, max_queries=MAX_Q)
        runs[name] = {**strip(r), "verified_subset": subset_metrics(r["per_query"], verified)}
    result["runs"] = runs

    result["ram_mb"]["after_benchmarks"] = rss()
    result["ram_mb"]["peak_working_set"] = mb(proc.memory_info().peak_wset)
    if torch.cuda.is_available():
        result["vram_mb"] = {
            "torch_peak_allocated": mb(torch.cuda.max_memory_allocated()), "torch_peak_reserved": mb(torch.cuda.max_memory_reserved()),
            "nvidia_smi_used_before_model": vram0, "nvidia_smi_used_now": nvidia_used_mb(),
        }  # fmt: skip

    queries = [q["vec"] for q in kept["_scored"]["normal"]["known"] if q["detected"]][:100]
    result["pgvector"] = pgvector_check(kept["_index"], queries)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True, default=str), encoding="utf-8")
    print("Escrito:", REPORT)


if __name__ == "__main__":
    main()
