# -*- coding: utf-8 -*-
"""Índice de embeddings del catálogo de Carpetazo: lee cada imagen por su URL pública, en memoria (nunca se guarda en disco; sin tokens, sin escribir en R2).
Reanudable: guarda trozos en <salida>/chunk_NNNN.npz (ids + vectores float16). Uso: python build_index.py <catalogo.sqlite> <salida> [limite]
"""
import io
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from app.recognition.embedding.base import CANON_H, CANON_W  # noqa: E402
from app.recognition.embedding.dinov2_client import Dinov2WorkerExtractor  # noqa: E402
from app.recognition.embedding.service import EmbeddingService  # noqa: E402

DB, OUT = sys.argv[1], Path(sys.argv[2])
LIMIT = int(sys.argv[3]) if len(sys.argv) > 3 else None
OUT.mkdir(parents=True, exist_ok=True)
CHUNK = 1000

con = sqlite3.connect(DB)
rows = con.execute("select category_id, product_id, group_id, image_url from mirror_cards where image_origin='carpetazo_r2' and image_url like 'https://%' order by group_id desc").fetchall()
# round-robin entre categorías (ediciones más nuevas primero) para que el índice sea útil desde temprano
cats_all = sorted({r[0] for r in rows})
by = {c: [r for r in rows if r[0] == c] for c in cats_all}
PRIO = [100, 3, 85, 99, 71, 89, 68, 63, 2, 1]  # Pokemon primero, luego juegos chicos y al final los grandes
order = [r for c in PRIO + [c for c in cats_all if c not in PRIO] for r in by.get(c, [])]

done = set()
for f in OUT.glob("chunk_*.npz"):
    z = np.load(f)
    done.update(zip(z["cat"].tolist(), z["pid"].tolist()))
todo = [r for r in order if (r[0], r[1]) not in done]
if LIMIT:
    todo = todo[:LIMIT]
print(f"en el catálogo: {len(order)} · ya indexadas: {len(done)} · por hacer: {len(todo)}", flush=True)

svc = EmbeddingService(Dinov2WorkerExtractor(), None, 30.0)
SPEC = None


def fetch(r):
    for attempt in range(4):
        try:
            req = urllib.request.Request(r[3], headers={"User-Agent": "carpetazo-vision-lab"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = resp.read()
            with Image.open(io.BytesIO(data)) as im:
                rgb = np.asarray(im.convert("RGB"))
            return r, cv2.resize(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), (CANON_W, CANON_H), interpolation=cv2.INTER_AREA), None
        except urllib.error.HTTPError as e:
            if e.code in (404, 403):
                return r, None, f"HTTP {e.code}"
            time.sleep(2 * (attempt + 1))
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            time.sleep(1 + attempt)
    return r, None, locals().get("err", "fallo de red")


n_chunk = len(list(OUT.glob("chunk_*.npz")))
buf_c, buf_p, buf_v, failed, t0, ok = [], [], [], 0, time.time(), 0
failf = open(OUT / "fallidas.txt", "a", encoding="utf-8")


def flush():
    global n_chunk, buf_c, buf_p, buf_v
    if buf_v:
        np.savez(OUT / f"chunk_{n_chunk:04d}.npz", cat=np.array(buf_c, np.int32), pid=np.array(buf_p, np.int64), vec=np.stack(buf_v).astype(np.float16), spec=SPEC)
        n_chunk += 1
        buf_c, buf_p, buf_v = [], [], []


with ThreadPoolExecutor(8) as ex:
    for i in range(0, len(todo), 48):
        for r, img, err in ex.map(fetch, todo[i:i + 48]):
            if img is None:
                failed += 1
                failf.write(f"{r[0]}\t{r[1]}\t{err}\n")
                continue
            res = svc.embed(img)
            if res.fallback_used:
                print("EXTRACTOR PRIMARIO NO DISPONIBLE:", res.fallback_reason, flush=True)
                flush()
                sys.exit(3)
            SPEC = res.spec.key
            buf_c.append(r[0]); buf_p.append(r[1]); buf_v.append(res.vector); ok += 1
            if len(buf_v) >= CHUNK:
                flush()
        if (i // 48) % 10 == 0:
            el = time.time() - t0
            print(f"{ok} ok · {failed} fallidas · {ok / max(el, 1):.1f} img/s · quedan ~{(len(todo) - i) / max(ok / max(el, 1), 0.1) / 60:.0f} min", flush=True)
flush()
failf.close()
print(f"FIN: {ok} indexadas, {failed} fallidas, {time.time() - t0:.0f}s", flush=True)
