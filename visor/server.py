# -*- coding: utf-8 -*-
"""Visor de PRUEBA (exploratorio): cámara del móvil -> carta -> candidatas del catálogo de Carpetazo (EN/ES/JP y otros TCG).

- Compara la carta con un índice de embeddings DINOv2 (visor/build_index.py) y lo combina con el texto leído (OCR de Windows).
- Solo lectura: no escribe en Carpetazo ni en R2; las identidades son candidatas SIN verificar.
Uso (desde la raíz del repositorio):
    python visor/server.py <cert.pem> <key.pem> [puerto]
Variables: VISOR_CATALOG_DB (SQLite del espejo), VISOR_INDEX_DIR (trozos del índice), VISOR_DATA_DIR (telemetría y umbrales).

MODO SERVICIO (VISOR_MODE=service, para el servidor): HTTP sin certificado (lo pone el proxy), sin página ni telemetría, y /scan y /status
exigen la cabecera X-Service-Key con una de VISOR_SERVICE_KEYS (separadas por coma). Lo llama el backend de Carpetazo, nunca el navegador.
Otras variables: VISOR_PORT, VISOR_ML_PYTHON y VISOR_HF_HOME (entorno ML), VISOR_DEVICE (auto|cpu|cuda).
"""
import base64
import difflib
import hmac
import re
import subprocess
import unicodedata
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2  # noqa: E402
import numpy as np  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, Response  # noqa: E402

from app.recognition.detection import CardDetector  # noqa: E402
from app.recognition.embedding.base import CANON_H, CANON_W  # noqa: E402
from app.recognition.embedding.dinov2_client import Dinov2WorkerExtractor  # noqa: E402
from app.recognition.embedding.grid import GridExtractor  # noqa: E402
from app.recognition.embedding.service import EmbeddingService  # noqa: E402

CERT, KEY = (sys.argv[1], sys.argv[2]) if len(sys.argv) > 2 else (None, None)
PORT = int(os.environ.get("VISOR_PORT") or (sys.argv[3] if len(sys.argv) > 3 else 8443))
SERVICE_MODE = os.environ.get("VISOR_MODE", "dev") == "service"
SERVICE_KEYS = [k.strip() for k in os.environ.get("VISOR_SERVICE_KEYS", "").split(",") if k.strip()]
MAX_BODY = 4_000_000  # bytes de la imagen
MAX_PIXELS = 16_000_000
CARD_RATIO = 63 / 88

svc = EmbeddingService(Dinov2WorkerExtractor(python=os.environ.get("VISOR_ML_PYTHON") or None, hf_home=os.environ.get("VISOR_HF_HOME") or None, device=os.environ.get("VISOR_DEVICE", "auto")), GridExtractor(), 30.0)
det = CardDetector()
lock = threading.Lock()
REF_SPEC = {"key": None}


def to_canon(bgr: np.ndarray) -> np.ndarray:
    return cv2.resize(bgr, (CANON_W, CANON_H), interpolation=cv2.INTER_AREA)


IDX = Path(os.environ.get("VISOR_INDEX_DIR", "visor/datos/indice"))
CATDB = os.environ.get("VISOR_CATALOG_DB", "visor/datos/catalogo.sqlite")
LANG = {3: "Pokémon EN", 100: "Pokémon ES", 85: "Pokémon JP", 1: "Magic", 2: "Yu-Gi-Oh", 63: "Digimon", 68: "One Piece", 71: "Lorcana", 89: "Riftbound", 99: "Mitos y Leyendas"}
STATE = {"n_chunks": -1, "t": 0.0}


def load_index() -> None:
    files = sorted(IDX.glob("chunk_*.npz"))
    if len(files) == STATE["n_chunks"]:
        return
    cats, pids, vecs, spec = [], [], [], None
    for f in files:
        z = np.load(f)
        cats.append(z["cat"]); pids.append(z["pid"]); vecs.append(z["vec"]); spec = str(z["spec"])
    if not vecs:
        return
    REF_SPEC.update(key=spec, backend="indice del catalogo", mat=np.concatenate(vecs).astype(np.float32),
                    cat=np.concatenate(cats), pid=np.concatenate(pids))
    build_meta()
    STATE["n_chunks"] = len(files)
    print(f"indice cargado: {len(REF_SPEC['pid'])} cartas ({spec})", flush=True)


def nname(s: str) -> str:
    """Nombre base normalizado: sin acentos, sin [variante], (nota) ni ' - numero'."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", s)
    if " - " in s and re.search(r"\d", s.rsplit(" - ", 1)[1]):
        s = s.rsplit(" - ", 1)[0]
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", s)).strip()


def nnum(s: str) -> str:
    return re.sub(r"(?<!\d)0+(?=\d)", "", re.sub(r"\s+", "", (s or "").upper()))


NAME_IDX: dict = {}
NUM_IDX: dict = {}
NAME_BUCKET: dict = {}
NAME_OF: list = []


def build_meta() -> None:
    con = sqlite3.connect(f"file:{CATDB}?mode=ro", uri=True)
    try:
        info = {(c, p): (n, nu) for c, p, n, nu in con.execute("select category_id, product_id, name, number from mirror_cards")}
    finally:
        con.close()
    NAME_IDX.clear(); NUM_IDX.clear(); NAME_BUCKET.clear(); NAME_OF.clear()
    for i, (c, p) in enumerate(zip(REF_SPEC["cat"].tolist(), REF_SPEC["pid"].tolist())):
        n, nu = info.get((c, p), ("", ""))
        k = nname(n)
        NAME_OF.append(k)
        if len(k) >= 3:
            NAME_IDX.setdefault(k, []).append(i)
        nk = nnum(nu)
        if len(nk) >= 3:
            NUM_IDX.setdefault(nk, []).append(i)
    for k in NAME_IDX:
        NAME_BUCKET.setdefault(k[0], []).append(k)


class Ocr:
    """OCR del motor de Windows (en-US / es-ES), proceso PowerShell persistente."""

    def __init__(self):
        self.p = None
        self.lock = threading.Lock()

    def _start(self):
        self.p = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(Path(__file__).with_name("ocr_worker.ps1"))],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)

    def read(self, path: str, lang: str) -> list[str]:
        with self.lock:
            try:
                if self.p is None or self.p.poll() is not None:
                    self._start()
                self.p.stdin.write(f"{path}|{lang}" + chr(10)); self.p.stdin.flush()
                out = _json.loads(self.p.stdout.readline() or "{}")
                return out.get("lines", []) if out.get("ok") else []
            except Exception:  # noqa: BLE001
                self.p = None
                return []


class OcrTesseract:
    """OCR en Linux con Tesseract (paquetes del sistema tesseract-ocr, eng y spa)."""

    LANGS = {"en-US": "eng", "es-ES": "spa"}

    def read(self, path: str, lang: str) -> list[str]:
        try:
            r = subprocess.run(["tesseract", path, "stdout", "-l", self.LANGS.get(lang, "eng"), "--psm", "6"], capture_output=True, text=True, timeout=10, check=False)  # noqa: S603,S607
            return [x.strip() for x in r.stdout.splitlines() if x.strip()]
        except Exception:  # noqa: BLE001
            return []


OCR = Ocr() if os.name == "nt" else OcrTesseract()
NUMRE = re.compile(r"\b(\d{1,3}\s*/\s*[0-9A-Z]{2,5})\b|\b([A-Z0-9]{2,6}-[A-Z]{0,3}\d{2,4})\b")


def ocr_hits(piece: np.ndarray):
    """Lee el texto de la carta y busca nombres/numeros del indice. Devuelve (indices_nombre, indices_numero, lineas, nombres)."""
    h, w = piece.shape[:2]
    piece = piece[: int(h * 0.30)]  # el nombre está en la franja superior de la carta
    h, w = piece.shape[:2]
    big = piece if w >= 700 else cv2.resize(piece, (700, int(700 * h / w)), interpolation=cv2.INTER_CUBIC)
    path = str(DATA / "ocr_tmp.png")
    cv2.imwrite(path, big)
    lines, name_idx, num_idx, names_found = [], [], [], []
    for lang in ("en-US", "es-ES"):
        ls = OCR.read(path, lang)
        lines += ls
        for ln in ls[:8]:
            k = nname(ln)
            if len(k) < 4:
                continue
            if k in NAME_IDX:
                name_idx += NAME_IDX[k][:300]; names_found.append(k)
            elif len(k) >= 6:
                for m in difflib.get_close_matches(k, [x for x in NAME_BUCKET.get(k[0], []) if abs(len(x) - len(k)) <= 3], n=2, cutoff=0.86):
                    name_idx += NAME_IDX[m][:300]; names_found.append(m + "~")
        for ln in ls:
            for m in NUMRE.finditer(ln.upper()):
                nk = nnum(m.group(0))
                if nk in NUM_IDX:
                    num_idx += NUM_IDX[nk][:300]
        if names_found:
            break
    return name_idx, num_idx, lines, names_found


def meta(cat: int, pid: int) -> dict:
    con = sqlite3.connect(f"file:{CATDB}?mode=ro", uri=True)
    try:
        r = con.execute("select c.name,c.number,c.rarity,c.image_url,c.relation,s.name from mirror_cards c left join mirror_sets s on s.category_id=c.category_id and s.group_id=c.group_id where c.category_id=? and c.product_id=?", (cat, pid)).fetchone()
    finally:
        con.close()
    return {"name": r[0], "number": r[1], "rarity": r[2], "image": r[3], "relation": r[4], "set": r[5], "lang": LANG.get(cat, str(cat)), "category_id": cat, "product_id": pid}


app = FastAPI()
HTML = (Path(__file__).with_name("index.html")).read_text(encoding="utf-8")


import json as _json  # noqa: E402

HERE = Path(__file__).parent
DATA = Path(os.environ.get("VISOR_DATA_DIR", str(HERE / "data")))
DATA.mkdir(parents=True, exist_ok=True)
DEFAULT_CFG = {"movMax": 14, "sharpMin": 12, "stillFrames": 2, "sdMin": 14, "meanMin": 25, "meanMax": 245, "glareMax": 0.18, "torchAuto": False, "exposureAuto": True, "votes": 3, "minScore": 0.55, "noCardGapMs": 1200, "window": 5, "scanGapMs": 250}


def authorized(request: Request) -> bool:
    """En modo servicio solo pasa quien manda una clave de servicio válida (comparación en tiempo constante)."""
    if not SERVICE_MODE:
        return True
    sent = request.headers.get("x-service-key", "")
    return bool(sent) and any(hmac.compare_digest(sent, k) for k in SERVICE_KEYS)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index():
    if SERVICE_MODE:
        return HTMLResponse("", status_code=404)
    return (HERE / "index.html").read_text(encoding="utf-8")


@app.get("/config")
def config():
    if SERVICE_MODE:
        return JSONResponse({}, status_code=404)
    try:
        return {**DEFAULT_CFG, **_json.loads((DATA / "umbrales.json").read_text(encoding="utf-8"))}
    except Exception:  # noqa: BLE001
        return DEFAULT_CFG


@app.post("/telemetry")
async def telemetry(request: Request):
    if SERVICE_MODE:
        return JSONResponse({}, status_code=404)
    body = (await request.body())[:20000].decode("utf-8", "ignore")
    with open(DATA / "telemetria.jsonl", "a", encoding="utf-8") as f:
        f.write(body.replace(chr(10), " ") + chr(10))
    return {"ok": True}


@app.get("/status")
def status(request: Request):
    if not authorized(request):
        return JSONResponse({"error": "no autorizado"}, status_code=401)
    load_index()
    return {"cartas_indexadas": int(len(REF_SPEC["pid"])) if "pid" in REF_SPEC else 0, "spec": REF_SPEC.get("key"), "embedding": svc.status()}


def norm_luz(img: np.ndarray) -> np.ndarray:
    """Auto-niveles en L (Lab) solo para imagenes muy claras: en pruebas ayuda con exceso de luz y perjudica con luz normal."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    if float(lab[..., 0].mean()) < 165:
        return img
    L = lab[..., 0].astype(np.float32)
    lo, hi = np.percentile(L, 2), np.percentile(L, 98)
    L = np.clip((L - lo) / max(hi - lo, 20), 0, 1)
    med = float(np.median(L))
    g = np.log(0.47) / np.log(max(med, 0.02)) if 0.02 < med < 0.98 else 1.0
    lab[..., 0] = (np.clip(L ** np.clip(g, 0.4, 2.5), 0, 1) * 255).astype(np.uint8)
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


GMASK = {"m": None}


def search(vec: np.ndarray, k: int = 5):
    sims = REF_SPEC["mat"] @ vec
    if GMASK["m"] is not None:
        sims = np.where(GMASK["m"], sims, -1.0)
    order = np.argsort(-sims)[:k]
    return [(int(i), float(sims[i])) for i in order]


@app.post("/scan")
async def scan(request: Request):
    t0 = time.perf_counter()
    if not authorized(request):
        return JSONResponse({"error": "no autorizado"}, status_code=401)
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        return JSONResponse({"error": "imagen demasiado grande"}, status_code=413)
    if time.time() - STATE["t"] > 60:
        STATE["t"] = time.time()
        load_index()
    if "mat" not in REF_SPEC:
        return JSONResponse({"error": "el indice aun no tiene cartas"}, status_code=503)
    games = [int(x) for x in request.headers.get("x-games", "").split(",") if x.strip().isdigit() and int(x) in LANG]
    GMASK["m"] = np.isin(REF_SPEC["cat"], games) if games else None
    data = await request.body()
    if len(data) > MAX_BODY:
        return JSONResponse({"error": "imagen demasiado grande"}, status_code=413)
    try:
        crop = [float(x) for x in request.headers.get("x-crop", "").split(",") if x]
    except ValueError:
        return JSONResponse({"error": "recorte no válido"}, status_code=400)
    if crop and (len(crop) != 4 or any(not 0.0 <= v <= 1.0 for v in crop) or crop[2] <= 0 or crop[3] <= 0):
        return JSONResponse({"error": "recorte no válido"}, status_code=400)
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        return JSONResponse({"error": "imagen no válida"}, status_code=400)
    H, W = arr.shape[:2]
    if H * W > MAX_PIXELS:
        return JSONResponse({"error": "imagen demasiado grande"}, status_code=413)
    piece = None
    cands = []  # (etiqueta, imagen canónica)
    e1 = det.detect(arr)
    if e1.found:
        rc = det.rectify(arr, e1)
        cands += [("E1", rc.upright), ("E1 girada", rc.flipped)]
    if len(crop) == 4:
        x, y, w, h = crop
        x0, y0, x1, y1 = int(x * W), int(y * H), int((x + w) * W), int((y + h) * H)
        piece = arr[max(y0, 0):y1, max(x0, 0):x1]
        if piece.size:
            cands.append(("marco", to_canon(piece)))
    if not cands:
        return {"found": False, "ms": round((time.perf_counter() - t0) * 1000)}
    best = None
    with lock:
        for label, img in cands:
            res = svc.embed(norm_luz(img))
            if res.spec.key != REF_SPEC["key"]:
                return JSONResponse({"error": f"el extractor cambió ({res.spec.key}); reinicia el visor"}, status_code=503)
            top = search(res.vector)
            if best is None or top[0][1] > best["top"][0][1]:
                best = {"label": label, "top": top, "img": img, "vec": res.vector}
        # texto de la carta (nombre / numero) para re-ordenar: la mayoria de los fallos son cartas con plantilla parecida
        ocr_info = {"name": [], "lines": [], "boost": 0}
        try:
            piece_ocr = piece if (piece is not None and piece.size) else arr
            name_idx, num_idx, lines, found = ocr_hits(piece_ocr)
            ocr_info.update(name=found, lines=lines[:4])
            gm = GMASK["m"]
            if gm is not None:
                name_idx = [i for i in name_idx if gm[i]]; num_idx = [i for i in num_idx if gm[i]]
            vis_all = REF_SPEC["mat"] @ best["vec"]
            if gm is not None:
                vis_all = np.where(gm, vis_all, -1.0)
            pool = set(np.argsort(-vis_all)[:50].tolist()) | set(name_idx) | set(num_idx)
            ids = np.array(sorted(pool))
            sims = REF_SPEC["mat"][ids] @ best["vec"]
            nm, nu = set(name_idx), set(num_idx)
            ok_vis = sims >= 0.45  # un nombre leido solo suma si la carta tambien se parece visualmente
            final = sims + ok_vis * (np.array([0.20 if i in nm else 0.0 for i in ids.tolist()]) + np.array([0.25 if i in nu else 0.0 for i in ids.tolist()]))
            order = np.argsort(-final)[:5]
            best["top"] = [(int(ids[j]), float(final[j])) for j in order]
            ocr_info["boost"] = round(float(final[order[0]] - sims[order[0]]), 2)
            # margen contra la mejor carta de OTRO nombre (las reimpresiones de la misma carta no cuentan como duda)
            top_name = NAME_OF[int(ids[order[0]])]
            alt = [final[j] for j in np.argsort(-final) if NAME_OF[int(ids[j])] != top_name]
            ocr_info["margin"] = round(float(final[order[0]] - (alt[0] if alt else final[order[0]] - 0.2)), 3)
        except Exception as exc:  # noqa: BLE001
            print("ocr fallo:", exc, flush=True)
    try:
        if SERVICE_MODE:
            raise OSError  # en el servidor no se guarda registro de escaneos
        with open(DATA / "escaneos.jsonl", "a", encoding="utf-8") as lf:
            lf.write(_json.dumps({"t": int(time.time() * 1000), "src": best["label"], "e1": bool(e1.found), "e1c": round(float(e1.confidence), 3),
                                  "s1": round(best["top"][0][1], 4), "s2": round(best["top"][1][1], 4), "name": meta(int(REF_SPEC["cat"][best["top"][0][0]]), int(REF_SPEC["pid"][best["top"][0][0]]))["name"]}, ensure_ascii=False) + chr(10))
    except Exception:  # noqa: BLE001
        pass
    prev = cv2.imencode(".jpg", cv2.resize(best["img"], (110, 154)))[1].tobytes()
    return {"found": True, "source": best["label"], "e1": bool(e1.found), "e1_conf": round(float(e1.confidence), 3),
            "top": [{**meta(int(REF_SPEC["cat"][i]), int(REF_SPEC["pid"][i])), "score": round(s, 4), "group": NAME_OF[i] if i < len(NAME_OF) else ""} for i, s in best["top"]],
            "indexed": int(len(REF_SPEC["pid"])), "ocr": ocr_info,
            "preview": "data:image/jpeg;base64," + base64.b64encode(prev).decode(), "ms": round((time.perf_counter() - t0) * 1000)}


if __name__ == "__main__":
    load_index()
    STATE["t"] = time.time()
    uvicorn.run(app, host="0.0.0.0", port=PORT, ssl_certfile=CERT, ssl_keyfile=KEY, log_level="warning")  # sin certificado (CERT=None) corre en HTTP
