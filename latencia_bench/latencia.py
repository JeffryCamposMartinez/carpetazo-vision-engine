# -*- coding: utf-8 -*-
"""Compara, desde el mismo lugar (el VPS), el tiempo de un escaneo: motor del VPS, motor del notebook por Tailscale y la ruta real de producción."""
import io, json, os, statistics, time, urllib.request
from PIL import Image

UA = {"User-Agent": "Mozilla/5.0"}
N = int(os.environ.get("N", "12"))
PHOTO = "https://pub-1ba0fdf5c93c4412abbc2b234780b3c6.r2.dev/Carpetazo.cl/Pokemon_Espanol/Pokemon_Espanol_DB/Pokemon_Espanol_Images/Equilibrio%20Perfecto/Energ%C3%ADa%20Psychic%20Telep%C3%A1tica%20%28088%29/Energ%C3%ADa%20Psychic%20Telep%C3%A1tica%20%28088%29.webp"
im = Image.open(io.BytesIO(urllib.request.urlopen(urllib.request.Request(PHOTO, headers=UA), timeout=30).read())).convert("RGB")
buf = io.BytesIO(); im.save(buf, "JPEG", quality=85); DATA = buf.getvalue()
print("foto", len(DATA) // 1024, "KB", flush=True)

TARGETS = {
    "motor del VPS (misma maquina)": (os.environ["VPS_URL"].rstrip("/") + "/scan", {"x-service-key": os.environ["VPS_KEY"], "content-type": "application/octet-stream"}),
    "motor del notebook por Tailscale": (os.environ["NB_URL"].rstrip("/") + "/scan", {"x-service-key": os.environ["NB_KEY"], "content-type": "application/octet-stream"}),
    "produccion: backend -> notebook (ruta real)": (os.environ["API_URL"], {"Content-Type": "image/jpeg"}),
}


def run(name):
    url, headers = TARGETS[name]
    t = time.time()
    req = urllib.request.Request(url, data=DATA, headers={**headers, "x-games": "3,100,85", **UA})
    d = json.load(urllib.request.urlopen(req, timeout=90))
    return time.time() - t, d.get("ms"), bool(d.get("found"))


names = list(TARGETS)
for n in names:
    try: run(n)
    except Exception as e: print("calentamiento fallo", n, e, flush=True)
res = {n: [] for n in names}
for i in range(N):
    for n in (names if i % 2 == 0 else names[::-1]):
        try: res[n].append(run(n))
        except Exception as e: res[n].append((None, None, str(e)[:60]))
out = {}
for n, rows in res.items():
    tot = sorted(r[0] for r in rows if r[0])
    ms = [r[1] for r in rows if r[1]]
    out[n] = {"n": len(tot), "mediana_s": round(statistics.median(tot), 3) if tot else None, "min_s": round(tot[0], 3) if tot else None, "max_s": round(tot[-1], 3) if tot else None,
              "p90_s": round(tot[max(int(len(tot) * 0.9) - 1, 0)], 3) if tot else None, "motor_interno_ms_mediana": statistics.median(ms) if ms else None, "todos_s": [round(x, 2) for x in tot]}
    print("RES|" + n + "|" + json.dumps(out[n], ensure_ascii=False), flush=True)
print("FIN", flush=True)
while True:
    time.sleep(3600)
