# -*- coding: utf-8 -*-
"""Banco de precisión del escáner: toma cartas del catálogo (identidad conocida), las convierte en «fotos» (fondo, perspectiva, giro,
desenfoque, brillo, reflejo, JPEG) y las manda a /scan. Mide si la PRIMERA candidata es la impresión exacta (misma categoría y product_id).

Con BENCH_CROP=1 manda también el marco de la carta (x-crop), como hace la app.
Uso:  VISOR_URL=http://192.168.1.19:8001 VISOR_KEY=... python evaluation/scan_bench.py [cartas_por_juego] [semilla] [salida.json]
Mitad de la muestra son cartas con reimpresiones (mismo nombre en varias ediciones): es lo difícil de acertar.
"""
import io
import json
import os
import random
import sqlite3
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parents[1]
DB = os.environ.get("VISOR_CATALOG_DB", str(HERE / "visor/datos/catalogo.sqlite"))
URL = os.environ.get("VISOR_URL", "http://127.0.0.1:8001").rstrip("/")
KEY = os.environ.get("VISOR_KEY", "")
PER_GAME = int(sys.argv[1]) if len(sys.argv) > 1 else 20
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 7
OUT = sys.argv[3] if len(sys.argv) > 3 else str(HERE / "evaluation/reports/scan_bench.json")
LANG = {3: "Pokémon EN", 100: "Pokémon ES", 85: "Pokémon JP", 1: "Magic", 2: "Yu-Gi-Oh", 63: "Digimon", 68: "One Piece", 71: "Lorcana", 89: "Riftbound", 99: "Mitos y Leyendas"}
rng = random.Random(SEED)
nprng = np.random.default_rng(SEED)


def sample():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out = []
    only = [int(x) for x in os.environ.get("BENCH_CATS", "").split(",") if x]
    for cat in LANG:
        if only and cat not in only:
            continue
        rows = con.execute("select category_id, product_id, name, number, image_url from mirror_cards where category_id=? and image_origin='carpetazo_r2' and image_url like 'https://%'", (cat,)).fetchall()
        groups = defaultdict(list)
        for r in rows:
            groups[r[2].split(" - ")[0].strip().lower()].append(r)
        multi = [g[0] for g in groups.values() if len(g) > 1]
        single = [g[0] for g in groups.values() if len(g) == 1]
        pick = rng.sample(multi, min(PER_GAME // 2, len(multi))) + rng.sample(single, min(PER_GAME - PER_GAME // 2, len(single)))
        out += [dict(cat=r[0], pid=int(r[1]), name=r[2], number=r[3], url=r[4], reprint=len(groups[r[2].split(" - ")[0].strip().lower()]) > 1) for r in pick]
    return out


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "carpetazo-scan-bench"})
    with urllib.request.urlopen(req, timeout=30) as r:
        with Image.open(io.BytesIO(r.read())) as im:
            return cv2.cvtColor(np.asarray(im.convert("RGB")), cv2.COLOR_RGB2BGR)


def photo(card):
    """Carta → foto de móvil vertical 1200x1600: fondo de mesa, perspectiva, giro leve, luz, reflejo, desenfoque y JPEG."""
    W, H = 1200, 1600
    bg = np.full((H, W, 3), nprng.integers(30, 200, 3), np.uint8)
    bg = (bg.astype(np.int16) + nprng.normal(0, 10, bg.shape)).clip(0, 255).astype(np.uint8)
    ch = int(H * rng.uniform(0.55, 0.78)); cw = int(ch * card.shape[1] / card.shape[0])
    c = cv2.resize(card, (cw, ch), interpolation=cv2.INTER_AREA)
    pad = 6
    c = cv2.copyMakeBorder(c, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(10, 10, 10))
    ch, cw = c.shape[:2]
    cx, cy = W / 2 + rng.uniform(-80, 80), H / 2 + rng.uniform(-80, 80)
    ang = np.deg2rad(rng.uniform(-9, 9))
    src = np.float32([[0, 0], [cw, 0], [cw, ch], [0, ch]])
    pts = np.float32([[-cw / 2, -ch / 2], [cw / 2, -ch / 2], [cw / 2, ch / 2], [-cw / 2, ch / 2]])
    rot = np.array([[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]], np.float32)
    dst = pts @ rot.T + np.float32([cx, cy]) + nprng.uniform(-0.035, 0.035, (4, 2)).astype(np.float32) * np.float32([cw, ch])  # perspectiva
    M = cv2.getPerspectiveTransform(src, dst.astype(np.float32))
    warped = cv2.warpPerspective(c, M, (W, H), flags=cv2.INTER_LINEAR)
    mask = cv2.warpPerspective(np.full(c.shape[:2], 255, np.uint8), M, (W, H))
    img = np.where(mask[..., None] > 0, warped, bg)
    img = (img.astype(np.float32) * rng.uniform(0.72, 1.18) + rng.uniform(-18, 18)).clip(0, 255)
    if rng.random() < 0.5:  # reflejo de luz
        gx, gy = rng.uniform(0.3, 0.7) * W, rng.uniform(0.3, 0.7) * H
        yy, xx = np.mgrid[0:H, 0:W]
        g = np.exp(-(((xx - gx) / (W * 0.18)) ** 2 + ((yy - gy) / (H * 0.10)) ** 2)) * rng.uniform(60, 130)
        img = (img + g[..., None]).clip(0, 255)
    img = img.astype(np.uint8)
    s = rng.choice([0, 0, 0.8, 1.3])
    if s:
        img = cv2.GaussianBlur(img, (0, 0), s)
    ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(62, 88)])
    xs, ys = dst[:, 0], dst[:, 1]
    m = 0.03  # el marco de la app no es exacto: margen y desvío leves
    x0, y0 = max(0.0, xs.min() / W - m + rng.uniform(-0.01, 0.01)), max(0.0, ys.min() / H - m + rng.uniform(-0.01, 0.01))
    x1, y1 = min(1.0, xs.max() / W + m + rng.uniform(-0.01, 0.01)), min(1.0, ys.max() / H + m + rng.uniform(-0.01, 0.01))
    return jpg.tobytes(), f"{x0:.4f},{y0:.4f},{x1 - x0:.4f},{y1 - y0:.4f}"


def scan(data, crop=None):
    headers = {"x-service-key": KEY, "content-type": "application/octet-stream"}
    if crop and os.environ.get("BENCH_CROP") == "1":
        headers["x-crop"] = crop  # como la app: marco aproximado de la carta
    if os.environ.get("BENCH_WEIGHTS"):
        headers["x-weights"] = os.environ["BENCH_WEIGHTS"]  # «nombre,numero,ambos»; el motor lo acepta solo con VISOR_BENCH=1
    req = urllib.request.Request(URL + "/scan", data=data, method="POST", headers=headers)
    t = time.time()
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r), time.time() - t


if __name__ == "__main__":
    items = sample()
    print(f"{len(items)} cartas ({PER_GAME} por juego, mitad con reimpresiones)", flush=True)
    res, stats = [], defaultdict(lambda: defaultdict(int))
    for n, it in enumerate(items, 1):
        try:
            data, crop = photo(fetch(it["url"]))
            r, dt = scan(data, crop)
        except Exception as e:  # noqa: BLE001
            res.append({**it, "error": f"{type(e).__name__}: {e}"})
            stats[it["cat"]]["error"] += 1
            continue
        top = r.get("top") or []
        t1 = top[0] if top else {}
        exact1 = bool(r.get("found")) and (t1.get("category_id"), t1.get("product_id")) == (it["cat"], it["pid"])
        exact5 = any((t.get("category_id"), t.get("product_id")) == (it["cat"], it["pid"]) for t in top[:5])
        name1 = bool(t1) and t1.get("name", "").split(" - ")[0].strip().lower() == it["name"].split(" - ")[0].strip().lower()
        s = stats[it["cat"]]
        s["n"] += 1; s["exact1"] += exact1; s["exact5"] += exact5; s["name1"] += name1; s["notfound"] += not r.get("found")
        if it["reprint"]:
            s["rn"] += 1; s["rexact1"] += exact1
        s["ms"] += dt
        res.append({**it, "found": r.get("found"), "exact1": exact1, "exact5": exact5, "name1": name1, "got": {k: t1.get(k) for k in ("name", "number", "set", "product_id", "category_id", "score")}, "ocr": r.get("ocr"), "ms": round(dt * 1000)})
        if n % 25 == 0:
            print(f"  {n}/{len(items)}", flush=True)
    Path(OUT).parent.mkdir(parents=True, exist_ok=True)
    Path(OUT).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    pct = lambda a, b: f"{100 * a / b:5.1f}%" if b else "   -  "
    print(f"\n{'juego':18}{'n':>4} {'exacta#1':>9} {'exacta#5':>9} {'nombre#1':>9} {'reimpr.#1':>10} {'sin carta':>10} {'ms':>6}")
    tot = defaultdict(int)
    for cat, s in stats.items():
        n = s["n"]
        print(f"{LANG[cat]:18}{n:>4} {pct(s['exact1'], n):>9} {pct(s['exact5'], n):>9} {pct(s['name1'], n):>9} {pct(s['rexact1'], s['rn']):>10} {s['notfound']:>10} {s['ms'] / max(n, 1) * 1000:>6.0f}")
        for k, v in s.items():
            tot[k] += v
    n = tot["n"]
    print(f"{'TOTAL':18}{n:>4} {pct(tot['exact1'], n):>9} {pct(tot['exact5'], n):>9} {pct(tot['name1'], n):>9} {pct(tot['rexact1'], tot['rn']):>10} {tot['notfound']:>10} {tot['ms'] / max(n, 1) * 1000:>6.0f}  errores={tot['error']}")
