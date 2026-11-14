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
import uuid
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2  # noqa: E402
import numpy as np  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, Response  # noqa: E402

from app.recognition.detection import CardDetector, _side_lengths, order_quad  # noqa: E402
from app.recognition.embedding.base import CANON_H, CANON_W  # noqa: E402
from app.recognition.embedding.dinov2_client import Dinov2WorkerExtractor  # noqa: E402
from app.recognition.embedding.grid import GridExtractor  # noqa: E402
from app.recognition.embedding.service import EmbeddingService  # noqa: E402
import impreso  # noqa: E402  (lectura del número impreso por juego)

CERT, KEY = (sys.argv[1], sys.argv[2]) if len(sys.argv) > 2 else (None, None)
PORT = int(os.environ.get("VISOR_PORT") or (sys.argv[3] if len(sys.argv) > 3 else 8443))
SERVICE_MODE = os.environ.get("VISOR_MODE", "dev") == "service"
SERVICE_KEYS = [k.strip() for k in os.environ.get("VISOR_SERVICE_KEYS", "").split(",") if k.strip()]
MAX_BODY = 4_000_000  # bytes de la imagen
MAX_PIXELS = 16_000_000
EARLY_STOP = 0.75  # si una candidata ya llega a este parecido no se calculan las demás (cada embedding cuesta CPU)
CARD_RATIO = 63 / 88

svc = EmbeddingService(Dinov2WorkerExtractor(python=os.environ.get("VISOR_ML_PYTHON") or None, hf_home=os.environ.get("VISOR_HF_HOME") or None, device=os.environ.get("VISOR_DEVICE", "auto"), idle_seconds=float(os.environ.get("VISOR_IDLE_SECONDS", "0" if SERVICE_MODE else "120"))), GridExtractor(), 30.0)
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


CONFUSIONS = str.maketrans("OIJLSBZ", "0111582")  # el OCR confunde O/0, I/l/1, S/5, B/8, Z/2 en letra chica; se igualan de los dos lados


def nkey(s: str) -> str:
    """Clave de comparación de un número de coleccionista («OP07-033», «058/102», «0192»): sin signos, sin ceros a la izquierda y con las confusiones del OCR igualadas."""
    k = re.sub(r"[^A-Z0-9]", "", (s or "").upper()).translate(CONFUSIONS)
    return re.sub(r"(?<!\d)0+(?=\d)", "", k)


NAME_IDX: dict = {}
NUM_IDX: dict = {}
NAME_BUCKET: dict = {}
NAME_OF: list = []
PIDX: dict = {}  # (categoría, producto) -> posición en el índice
NUMRAW: list = []  # número tal como lo guarda el catálogo, por posición del índice


def build_meta() -> None:
    con = sqlite3.connect(f"file:{CATDB}?mode=ro", uri=True)
    try:
        info = {(c, p): (n, nu, (sn or "").startswith("Prize Pack")) for c, p, n, nu, sn in con.execute("select c.category_id, c.product_id, c.name, c.number, s.name from mirror_cards c left join mirror_sets s on s.category_id=c.category_id and s.group_id=c.group_id")}
    finally:
        con.close()
    NAME_IDX.clear(); NUM_IDX.clear(); NAME_BUCKET.clear(); NAME_OF.clear(); NUMRAW.clear()
    prize = []
    for i, (c, p) in enumerate(zip(REF_SPEC["cat"].tolist(), REF_SPEC["pid"].tolist())):
        n, nu, is_prize = info.get((c, p), ("", "", False))
        prize.append(is_prize)
        k = nname(n)
        NAME_OF.append(k)
        NUMRAW.append(nu or "")
        if len(k) >= 3:
            NAME_IDX.setdefault(k, []).append(i)
        for nk in {nkey(nu), nkey((nu or "").split(" ")[0])}:  # «BT15-101 SEC»: también sin la rareza
            if len(nk) >= 3:
                NUM_IDX.setdefault(nk, []).append(i)
    for k in NAME_IDX:
        NAME_BUCKET.setdefault(k[0], []).append(k)
    REF_SPEC["prize"] = np.array(prize, dtype=bool)
    PIDX.clear()
    PIDX.update({(c, p): i for i, (c, p) in enumerate(zip(REF_SPEC["cat"].tolist(), REF_SPEC["pid"].tolist()))})


class Ocr:
    """OCR del motor de Windows (en-US / es-ES), proceso PowerShell persistente."""

    def __init__(self):
        self.p = None
        self.lock = threading.Lock()

    def _start(self):
        self.p = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(Path(__file__).with_name("ocr_worker.ps1"))],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)

    def read(self, path: str, lang: str, psm: int = 6, whitelist: str = "") -> list[str]:
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

    LANGS = {"en-US": "eng", "es-ES": "spa", "multi": "eng+spa"}

    def read(self, path: str, lang: str, psm: int = 6, whitelist: str = "") -> list[str]:
        try:
            extra = ["-c", f"tessedit_char_whitelist={whitelist}", "--dpi", "300"] if whitelist else []
            r = subprocess.run(["tesseract", path, "stdout", "-l", self.LANGS.get(lang, "eng"), "--psm", str(psm), *extra], capture_output=True, text=True, timeout=10, check=False, env={**os.environ, "OMP_THREAD_LIMIT": "1"})  # noqa: S603,S607
            return [x.strip() for x in r.stdout.splitlines() if x.strip()]
        except Exception:  # noqa: BLE001
            return []


OCR = Ocr() if os.name == "nt" else OcrTesseract()

# Idioma de una carta Pokémon (inglés, español o japonés). La ilustración es la misma en todos: el idioma se lee del texto del cuadro de ataques.
# Palabras que distinguen un idioma del otro (se evitan las que existen en los dos, como «a» o «de»).
PK_LANG_CAT = {"en": 3, "es": 100, "ja": 85}  # categoría del catálogo de cada idioma
PK_CATS = [3, 100, 85]
WORDS_EN = frozenset("the your opponent this attack damage during may if from into put each that their once flip heads tails active bench prize more than deck hand discard attached coin effect cards".split())
WORDS_ES = frozenset("el la los las tu tus rival este esta ataque daño durante puedes si del con por cada esa sus una mazo mano descarta unido unidas moneda cruz cara activo banca premio más que como turno jugar cartas".split())
WORD_RE = re.compile(r"[a-záéíóúñü']+")


STAMP_PATH = Path(__file__).with_name("league_stamp.png")
STAMP_TPL = cv2.imread(str(STAMP_PATH), cv2.IMREAD_GRAYSCALE) if STAMP_PATH.exists() else None
STAMP_REL_W = 302 / 1629  # ancho del sello respecto del ancho de la carta en la foto de la plantilla
NO_STAMP_MAX = 0.5  # por debajo: sin sello (las 48 cartas normales medidas llegaron a 0,49 como máximo)
STAMP_MIN = 0.6  # medido: 21 de 21 cartas de liga ≥ 0,67 y 0 falsos positivos en 48 cartas normales (máximo 0,49)


def league_stamp(piece: np.ndarray) -> float:
    """Puntaje (0–1) de que la carta lleva el sello de liga (Play! Pokémon); el sello puede estar en cualquier lado de la carta."""
    if STAMP_TPL is None:
        return 0.0
    h, w = piece.shape[:2]
    g = cv2.cvtColor(cv2.resize(piece, (600, max(1, int(h * 600 / w))), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    band = g[int(g.shape[0] * 0.25): int(g.shape[0] * 0.97)]
    best = 0.0
    for f in (0.75, 0.85, 1.0, 1.15, 1.3):
        tw = int(600 * STAMP_REL_W * f); th = int(tw * STAMP_TPL.shape[0] / STAMP_TPL.shape[1])
        if th >= band.shape[0] or tw >= band.shape[1]:
            continue
        best = max(best, float(cv2.matchTemplate(band, cv2.resize(STAMP_TPL, (tw, th), interpolation=cv2.INTER_AREA), cv2.TM_CCOEFF_NORMED).max()))
    return best


def ocr_text(path: str) -> str:
    if isinstance(OCR, OcrTesseract):
        return "\n".join(OCR.read(path, "multi"))
    return "\n".join(OCR.read(path, "en-US") + OCR.read(path, "es-ES"))  # el OCR de Windows lee un idioma por pasada


def body_text(piece: np.ndarray) -> str:
    """Texto de la mitad inferior de la carta (ataques y línea del número)."""
    h, w = piece.shape[:2]
    body = piece[int(h * 0.48): int(h * 0.96)]
    big = body if w >= 800 else cv2.resize(body, (800, int(800 * body.shape[0] / max(w, 1))), interpolation=cv2.INTER_CUBIC)
    path = str(DATA / f"ocr_{uuid.uuid4().hex}.png")
    cv2.imwrite(path, big)
    try:
        return ocr_text(path)
    finally:
        Path(path).unlink(missing_ok=True)


def strip_text(piece: np.ndarray) -> str:
    """Franja inferior de la carta (número de coleccionista y código de edición) ampliada, en modo de texto suelto."""
    h, w = piece.shape[:2]
    band = piece[int(h * 0.86):]
    big = band if w >= 1400 else cv2.resize(band, (1400, int(1400 * band.shape[0] / max(w, 1))), interpolation=cv2.INTER_CUBIC)
    path = str(DATA / f"ocr_{uuid.uuid4().hex}.png")
    cv2.imwrite(path, big)
    try:
        return "\n".join(OCR.read(path, "multi", 11) if isinstance(OCR, OcrTesseract) else OCR.read(path, "en-US"))
    finally:
        Path(path).unlink(missing_ok=True)


NUM_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ/-"


def region_reads(piece: np.ndarray, cats: list) -> dict:
    """Lee el número impreso en la zona que usa cada juego candidato (impreso.REGIONS). -> {juego: (número, sigla, texto leído)}"""
    out = {}
    h, w = piece.shape[:2]
    for game in dict.fromkeys(impreso.GAME_OF_CAT.get(c) for c in cats):
        regions = impreso.REGIONS.get(game)
        if not regions:
            continue
        number, code, text = None, None, ""
        for psm in (6, 11):  # bloque de texto y, si no sale ni un dígito, texto suelto
            texts = []
            for x, y, rw, rh in regions:
                crop = piece[int(y * h): int((y + rh) * h) + 1, int(x * w): int((x + rw) * w) + 1]
                if crop.size == 0:
                    continue
                scale = max(1.0, 900 / max(crop.shape[1], 1))
                big = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale > 1 else crop
                big = cv2.copyMakeBorder(big, 12, 12, 12, 12, cv2.BORDER_REPLICATE)
                path = str(DATA / f"ocr_{uuid.uuid4().hex}.png")
                cv2.imwrite(path, big)
                try:
                    texts += OCR.read(path, "en-US", psm)  # solo inglés: un modelo menos que cargar
                finally:
                    Path(path).unlink(missing_ok=True)
            text = "\n".join(texts)
            number, code = impreso.parse_ocr(game, text)
            if number or re.search(r"\d", text):  # con dígitos leídos no se repite en otro modo
                break
        out[game] = (number, code, text[:120])
    return out


def detect_language(text: str, top_cat: int | None) -> dict:
    """Devuelve {code: en|es|ja|None, conf, source, en, es}. Texto del cuadro de ataques; si no hay texto legible y la carta más parecida es japonesa, japonés."""
    text = text.lower()
    words = WORD_RE.findall(text)
    en = sum(1 for x in words if x in WORDS_EN)
    # tildes y ñ cuentan como español, salvo «Pokémon» (aparece con tilde en todos los idiomas)
    es = sum(1 for x in words if x in WORDS_ES) + sum(1 for x in words if len(x) > 2 and not x.startswith("pok") and re.search(r"[áéíóúñ]", x))
    info = {"code": None, "conf": 0.0, "source": "none", "en": en, "es": es}
    if en + es >= 3:
        p_es = (es + 0.5) / (en + es + 1)
        code, conf = ("es", p_es) if p_es >= 0.5 else ("en", 1 - p_es)
        if conf >= 0.75:
            info.update(code=code, conf=round(conf, 2), source="texto")
    elif top_cat == 85:
        info.update(code="ja", conf=0.6, source="visual")  # sin texto latino legible y la más parecida es la versión japonesa
    return info
NUMRE = re.compile(r"\b(\d{1,3}\s*/\s*[0-9A-Z]{2,5})\b|\b([A-Z0-9]{2,6}-[A-Z]{0,3}\d{2,4})\b|\b(\d{3,4})\s+[CURMSTLP]\b|\b[CURMSTLP]\s+(\d{3,4})\b")  # 182/182 · BT5-032 · «0162 R» y «R 0162» (Magic)


def number_keys(text: str) -> list:
    """Claves de los números de coleccionista que aparecen en el texto leído. Solo formas que parecen un número (el ruido del OCR no suma)."""
    keys = set()
    for m in NUMRE.finditer(text.upper()):
        g = next(x for x in m.groups() if x)
        keys.add(nkey(g))
        if "/" in g:  # Magic imprime «190/271» y el catálogo guarda «190»
            keys.add(nkey(g.split("/")[0]))
    return [k for k in keys if len(k) >= 3]


# Pesos del puntaje final (se afinan con evaluation/scan_bench.py; con VISOR_BENCH=1 la cabecera x-weights los cambia solo en esa petición)
WEIGHTS = {"name": float(os.environ.get("VISOR_W_NAME", "0.20")), "num": float(os.environ.get("VISOR_W_NUM", "0.25")), "both": float(os.environ.get("VISOR_W_BOTH", "0.15"))}
BENCH = os.environ.get("VISOR_BENCH") == "1"
FAST_PATH = os.environ.get("VISOR_FAST", "1") == "1"  # carta clara y sin reimpresiones: no se lee nada (el OCR es el 77 % del tiempo)
LAB = os.environ.get("VISOR_LAB") == "1"  # laboratorio de entrenamiento (solo la instancia de pruebas): página con el visor, guarda cada escaneo y aprende de las confirmaciones
WEIGHTS["learn"] = float(os.environ.get("VISOR_W_LEARN", "0.30"))  # peso de las fotos reales ya confirmadas
LEARN_MIN = 0.80  # parecido mínimo con una foto confirmada para que cuente


def match_names(ls: list) -> tuple:
    """Líneas de texto leído → (índices de cartas cuyo nombre aparece, nombres encontrados)."""
    name_idx, names_found = [], []
    for ln in ls:
        k = nname(ln)
        if len(k) < 4:
            continue
        if k in NAME_IDX:
            name_idx += NAME_IDX[k][:300]; names_found.append(k)
        else:
            # el OCR a veces antepone o deja basura junto al nombre («I Energia del Team Rocket»): se prueban ventanas de palabras, las más largas primero
            toks = k.split()
            hit = next((w for size in range(min(len(toks), 6), 0, -1) for i in range(len(toks) - size + 1) if len(w := " ".join(toks[i:i + size])) >= 6 and w in NAME_IDX), None)
            if hit:
                name_idx += NAME_IDX[hit][:300]; names_found.append(hit)
            elif len(k) >= 6:
                for m in difflib.get_close_matches(k, [x for x in NAME_BUCKET.get(k[0], []) if abs(len(x) - len(k)) <= 3], n=2, cutoff=0.86):
                    name_idx += NAME_IDX[m][:300]; names_found.append(m + "~")
    return name_idx, names_found


def ocr_hits(piece: np.ndarray):
    """Lee el texto de la carta y busca nombres/numeros del indice. Devuelve (indices_nombre, indices_numero, lineas, nombres)."""
    h, w = piece.shape[:2]
    piece = piece[: int(h * 0.30)]  # el nombre está en la franja superior de la carta
    h, w = piece.shape[:2]
    big = piece if w >= 700 else cv2.resize(piece, (700, int(700 * h / w)), interpolation=cv2.INTER_CUBIC)
    path = str(DATA / f"ocr_{uuid.uuid4().hex}.png")
    cv2.imwrite(path, big)
    lines, name_idx, num_idx, names_found = [], [], [], []
    for lang in (("multi",) if isinstance(OCR, OcrTesseract) else ("en-US", "es-ES")):  # Tesseract lee los dos idiomas en una pasada
        ls = OCR.read(path, lang)
        lines += ls
        found_idx, found_names = match_names(ls[:8])
        name_idx += found_idx; names_found += found_names
        for nk in number_keys(" ".join(ls)):
            if nk in NUM_IDX:
                num_idx += NUM_IDX[nk][:300]
        if names_found:
            break
    Path(path).unlink(missing_ok=True)
    return name_idx, num_idx, lines, names_found


def meta(cat: int, pid: int) -> dict:
    con = sqlite3.connect(f"file:{CATDB}?mode=ro", uri=True)
    try:
        r = con.execute("select c.name,c.number,c.rarity,c.image_url,c.relation,s.name,c.group_id from mirror_cards c left join mirror_sets s on s.category_id=c.category_id and s.group_id=c.group_id where c.category_id=? and c.product_id=?", (cat, pid)).fetchone()
    finally:
        con.close()
    return {"name": r[0], "number": r[1], "rarity": r[2], "image": r[3], "relation": r[4], "set": r[5], "lang": LANG.get(cat, str(cat)), "category_id": cat, "product_id": pid, "group_id": r[6]}


app = FastAPI()
HTML = (Path(__file__).with_name("index.html")).read_text(encoding="utf-8")


import json as _json  # noqa: E402

HERE = Path(__file__).parent
DATA = Path(os.environ.get("VISOR_DATA_DIR", str(HERE / "data")))
DATA.mkdir(parents=True, exist_ok=True)
LAB_DIR = DATA / "aprendizaje"  # fotos de los escaneos, su resultado, las confirmaciones y los vectores aprendidos
LEARN = {"mat": None, "cat": None, "pid": None}  # fotos reales confirmadas: cada una enseña cómo se ve esa carta en una foto
LAB_LOCK = threading.Lock()


def load_learn() -> None:
    f = LAB_DIR / "aprendido.npz"
    if f.exists():
        z = np.load(f)
        LEARN.update(mat=z["vec"].astype(np.float32), cat=z["cat"], pid=z["pid"])
        print(f"fotos aprendidas: {len(LEARN['pid'])}", flush=True)
DEFAULT_CFG = {"movMax": 14, "sharpMin": 12, "stillFrames": 2, "sdMin": 14, "meanMin": 25, "meanMax": 245, "glareMax": 0.18, "torchAuto": False, "exposureAuto": True, "votes": 3, "minScore": 0.55, "noCardGapMs": 1200, "window": 5, "scanGapMs": 250}


def authorized(request: Request) -> bool:
    """En modo servicio solo pasa quien manda una clave de servicio válida (comparación en tiempo constante)."""
    if not SERVICE_MODE or LAB:
        return True
    sent = request.headers.get("x-service-key", "")
    return bool(sent) and any(hmac.compare_digest(sent, k) for k in SERVICE_KEYS)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index():
    if LAB:
        return (HERE / "lab.html").read_text(encoding="utf-8")
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


SID_RE = re.compile(r"^[0-9a-f]{32}$")


@app.get("/lab/search")
def lab_search(q: str = "", games: str = ""):
    """Búsqueda por nombre en el catálogo (para corregir cuando el resultado no era la carta)."""
    if not LAB:
        return JSONResponse({}, status_code=404)
    q = q.strip()[:60]
    if len(q) < 2:
        return {"cards": []}
    cats = [int(x) for x in games.split(",") if x.strip().isdigit() and int(x) in LANG] or list(LANG)
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    con = sqlite3.connect(f"file:{CATDB}?mode=ro", uri=True)
    try:
        rows = con.execute(f"select c.category_id,c.product_id,c.name,c.number,c.rarity,c.image_url,s.name from mirror_cards c left join mirror_sets s on s.category_id=c.category_id and s.group_id=c.group_id where c.name like ? escape '\\' and c.category_id in ({','.join('?' * len(cats))}) order by length(c.name) limit 30", (f"%{esc}%", *cats)).fetchall()
    finally:
        con.close()
    return {"cards": [{"category_id": r[0], "product_id": r[1], "name": r[2], "number": r[3], "rarity": r[4], "image": r[5], "set": r[6], "lang": LANG.get(r[0], str(r[0]))} for r in rows]}


@app.post("/lab/feedback")
async def lab_feedback(request: Request):
    """La persona confirma o corrige la carta de un escaneo: queda guardado y su foto enseña al motor cómo se ve esa carta."""
    if not LAB:
        return JSONResponse({}, status_code=404)
    try:
        body = _json.loads((await request.body())[:2000])
        sid, cat, pid = str(body["scan_id"]), int(body["category_id"]), int(body["product_id"])
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "datos inválidos"}, status_code=400)
    if not SID_RE.match(sid) or cat not in LANG or (cat, pid) not in PIDX:
        return JSONResponse({"error": "carta o escaneo desconocido"}, status_code=400)
    meta_f, vec_f = LAB_DIR / f"{sid}.json", LAB_DIR / f"{sid}.npy"
    if not meta_f.exists() or not vec_f.exists():
        return JSONResponse({"error": "escaneo no encontrado"}, status_code=404)
    with LAB_LOCK:
        info = _json.loads(meta_f.read_text(encoding="utf-8"))
        top1 = (info["resultado"]["top"] or [{}])[0]
        info["etiqueta"] = {"category_id": cat, "product_id": pid, "acerto_primero": (top1.get("category_id"), top1.get("product_id")) == (cat, pid), "t": int(time.time())}
        meta_f.write_text(_json.dumps(info, ensure_ascii=False), encoding="utf-8")
        vec = np.load(vec_f).astype(np.float32)[None, :]
        if LEARN["mat"] is None:
            LEARN.update(mat=vec, cat=np.array([cat], np.int32), pid=np.array([pid], np.int64))
        else:
            LEARN.update(mat=np.concatenate([LEARN["mat"], vec]), cat=np.concatenate([LEARN["cat"], [cat]]).astype(np.int32), pid=np.concatenate([LEARN["pid"], [pid]]).astype(np.int64))
        np.savez(LAB_DIR / "aprendido.npz", cat=LEARN["cat"], pid=LEARN["pid"], vec=LEARN["mat"].astype(np.float16))
    return {"ok": True, "acerto_primero": info["etiqueta"]["acerto_primero"], "aprendidas": int(len(LEARN["pid"]))}


@app.post("/lab/reset")
async def lab_reset(request: Request):
    """Borra todo lo guardado en el laboratorio (fotos, etiquetas y lo aprendido). Pide confirmación explícita."""
    if not LAB:
        return JSONResponse({}, status_code=404)
    try:
        ok = _json.loads((await request.body())[:200]).get("confirmar") == "borrar todo"
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        return JSONResponse({"error": "falta la confirmación"}, status_code=400)
    with LAB_LOCK:
        n = 0
        for f in LAB_DIR.glob("*"):
            if f.suffix in (".jpg", ".json", ".npy", ".npz"):
                f.unlink(); n += 1
        LEARN.update(mat=None, cat=None, pid=None)
    return {"ok": True, "borrados": n}


@app.get("/lab/stats")
def lab_stats():
    if not LAB:
        return JSONResponse({}, status_code=404)
    scans, labeled = 0, []
    for f in sorted(LAB_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime):
        scans += 1
        try:
            et = _json.loads(f.read_text(encoding="utf-8")).get("etiqueta")
        except Exception:  # noqa: BLE001
            continue
        if et:
            labeled.append(bool(et["acerto_primero"]))
    last = labeled[-20:]
    return {"escaneos": scans, "etiquetados": len(labeled), "aciertos": sum(labeled), "acierto_pct": round(100 * sum(labeled) / len(labeled), 1) if labeled else None,
            "ultimos20_pct": round(100 * sum(last) / len(last), 1) if last else None, "aprendidas": 0 if LEARN["pid"] is None else int(len(LEARN["pid"]))}


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


STRAIGHT_MAX_H = 1600  # alto máximo de la foto enderezada
OCR_MAX_H = 1300  # alto de la carta enderezada que se le da al OCR


def rectify_hires(arr: np.ndarray, d) -> "np.ndarray | None":
    """La carta enderezada a la resolución de la foto: el OCR lee mejor ahí que sobre la foto entera o el reducido de 440x614. None si el cuadrilátero no parece una carta."""
    m = d.metrics
    if not d.found or m.area_fraction < 0.25 or not 0.62 <= m.aspect_ratio <= 0.80 or m.rectangularity < 0.75 or m.perspective_skew > 0.5:
        return None
    q = order_quad(d.quad)
    top, right, bottom, left = _side_lengths(q)
    if (top + bottom) / 2 > (left + right) / 2:
        q = np.roll(q, -1, axis=0)
        top, right, bottom, left = _side_lengths(q)
    h = int(min(OCR_MAX_H, max(left, right)))
    if h < 300:
        return None
    w = int(round(h * CANON_W / CANON_H))
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float32)
    return cv2.warpPerspective(arr, cv2.getPerspectiveTransform(q.astype(np.float32), dst), (w, h), flags=cv2.INTER_CUBIC)


def visual_certainty(vec: np.ndarray) -> dict:
    """Qué tan inequívoca es la coincidencia solo por imagen: similitud de la mejor y margen sobre la mejor carta de OTRO nombre."""
    sims = REF_SPEC["mat"] @ vec
    if GMASK["m"] is not None:
        sims = np.where(GMASK["m"], sims, -1.0)
    order = np.argsort(-sims)[:40]
    top = int(order[0])
    alt = next((float(sims[j]) for j in order if NAME_OF[int(j)] != NAME_OF[top]), float(sims[top]) - 0.2)
    return {"sim": round(float(sims[top]), 4), "margin": round(float(sims[top]) - alt, 4), "pid": int(REF_SPEC["pid"][top]), "cat": int(REF_SPEC["cat"][top])}


@app.post("/straighten")
async def straighten(request: Request):
    """Endereza y centra la carta de una foto (perspectiva corregida, a la resolución de la foto). Devuelve JPEG en base64 o found=false."""
    if not authorized(request):
        return JSONResponse({"error": "no autorizado"}, status_code=401)
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        return JSONResponse({"error": "imagen demasiado grande"}, status_code=413)
    data = await request.body()
    if len(data) > MAX_BODY:
        return JSONResponse({"error": "imagen demasiado grande"}, status_code=413)
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if arr is None or arr.shape[0] * arr.shape[1] > MAX_PIXELS:
        return JSONResponse({"error": "imagen no válida"}, status_code=400)
    d = det.detect(arr)
    # solo si el cuadrilátero es claramente la carta entera (proporción 63×88, grande, sin cortar por el borde); si no, se deja la foto original
    m = d.metrics
    if not d.found or m.touches_image_border or m.area_fraction < 0.45 or not 0.67 <= m.aspect_ratio <= 0.76 or m.rectangularity < 0.8 or m.perspective_skew > 0.35:
        return {"found": False}
    q = order_quad(d.quad)
    top, right, bottom, left = _side_lengths(q)
    if (top + bottom) / 2 > (left + right) / 2:  # carta tumbada: girar el orden de vértices 90°
        q = np.roll(q, -1, axis=0)
        top, right, bottom, left = _side_lengths(q)
    h = int(min(STRAIGHT_MAX_H, max(left, right)))
    w = int(round(h * CANON_W / CANON_H))
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float32)
    card = cv2.warpPerspective(arr, cv2.getPerspectiveTransform(q.astype(np.float32), dst), (w, h), flags=cv2.INTER_CUBIC)
    ok, jpg = cv2.imencode(".jpg", card, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        return {"found": False}
    return {"found": True, "width": w, "height": h, "image": base64.b64encode(jpg.tobytes()).decode()}


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
    tm, mark = {}, time.perf_counter()
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        return JSONResponse({"error": "imagen no válida"}, status_code=400)
    tm["decodificar"] = time.perf_counter() - mark; mark = time.perf_counter()
    H, W = arr.shape[:2]
    if H * W > MAX_PIXELS:
        return JSONResponse({"error": "imagen demasiado grande"}, status_code=413)
    piece = None
    cands = []  # (etiqueta, imagen canónica)
    e1 = det.detect(arr)
    if e1.found:
        rc = det.rectify(arr, e1)
        cands.append(("E1", rc.upright))
    if len(crop) == 4:
        x, y, w, h = crop
        x0, y0, x1, y1 = int(x * W), int(y * H), int((x + w) * W), int((y + h) * H)
        piece = arr[max(y0, 0):y1, max(x0, 0):x1]
        if piece.size:
            cands.append(("marco", to_canon(piece)))
    if e1.found:
        cands.append(("E1 girada", rc.flipped))  # solo se prueba si las otras no convencen (carta al revés)
    tm["detectar"] = time.perf_counter() - mark; mark = time.perf_counter()
    if not cands:
        return {"found": False, "ms": round((time.perf_counter() - t0) * 1000)}
    hi = rectify_hires(arr, e1) if e1.found else None  # carta enderezada: mejor para el OCR que la foto entera o el marco a ojo
    piece_ocr = hi if hi is not None else (piece if (piece is not None and piece.size) else arr)
    names_box, body_box, reads_box, readers = {"r": ([], [], [], [])}, {}, {"r": {}}, []
    gm0, cat0 = GMASK["m"], REF_SPEC["cat"]
    pokemon_in_play = gm0 is None or bool(np.any(gm0 & np.isin(cat0, PK_CATS)))  # con Pokémon en juego el cuerpo también da el idioma
    best, n_emb, fast = None, 0, False
    with lock:
        for label, img in cands:
            res = svc.embed(norm_luz(img))
            if res.spec.key != REF_SPEC["key"]:
                return JSONResponse({"error": f"el extractor cambió ({res.spec.key}); reinicia el visor"}, status_code=503)
            top = search(res.vector)
            if best is None or top[0][1] > best["top"][0][1]:
                best = {"label": label, "top": top, "img": img, "vec": res.vector}
            n_emb += 1
            if best["top"][0][1] >= EARLY_STOP:
                break
        tm["embedding+búsqueda"] = time.perf_counter() - mark; mark = time.perf_counter()
        # ¿hace falta leer? Una carta que se parece mucho a una sola del catálogo, sin reimpresiones cercanas ni otra carta pegada, no gana nada con el OCR
        top_i, top_sim = best["top"][0]
        top_name = NAME_OF[top_i]
        rivals = [sc for i, sc in best["top"][1:] if NAME_OF[i] == top_name and top_sim - sc < 0.10]
        gap = top_sim - max([sc for i, sc in best["top"][1:] if NAME_OF[i] != top_name] or [0.0])
        fast = FAST_PATH and int(REF_SPEC["cat"][top_i]) not in PK_CATS and top_sim >= 0.88 and not rivals and gap >= 0.06
        if not fast:  # nombre, cuerpo y zonas del número se leen a la vez
            def _names():
                try:
                    names_box["r"] = ocr_hits(piece_ocr)
                except Exception as exc:  # noqa: BLE001
                    print("ocr nombre fallo:", exc, flush=True)

            def _regions():
                try:
                    reads_box["r"] = region_reads(piece_ocr, [int(REF_SPEC["cat"][i]) for i, _ in best["top"][:3]])  # número impreso en la zona de cada juego candidato
                except Exception as exc:  # noqa: BLE001
                    print("zonas fallo:", exc, flush=True)

            readers = [threading.Thread(target=_names, daemon=True), threading.Thread(target=lambda: body_box.update(text=body_text(piece_ocr)), daemon=True), threading.Thread(target=_regions, daemon=True)]
            for th in readers:
                th.start()
        # texto de la carta (nombre / numero) para re-ordenar: la mayoria de los fallos son cartas con plantilla parecida
        ocr_info = {"name": [], "lines": [], "boost": 0, "language": {"code": None, "conf": 0.0, "source": "none"}}
        try:
            for th in readers:
                th.join()
            if best["label"] == "E1 girada" and hi is not None and not fast:  # la carta estaba al revés: se lee otra vez al derecho
                hi180 = cv2.rotate(hi, cv2.ROTATE_180)
                names_box["r"] = ocr_hits(hi180)
                body_box["text"] = body_text(hi180)
                piece_ocr = hi180
                try:
                    reads_box["r"] = region_reads(piece_ocr, [int(REF_SPEC["cat"][i]) for i, _ in best["top"][:3]])
                except Exception as exc:  # noqa: BLE001
                    print("zonas fallo:", exc, flush=True)
                    reads_box["r"] = {}
            name_idx, num_idx, lines, found = names_box["r"]
            reads = reads_box["r"]
            if fast:
                ocr_info["rapido"] = True
            ocr_info["impreso"] = {g: r[0] for g, r in reads.items()}
            if BENCH:
                ocr_info["impreso_texto"] = {g: r[2] for g, r in reads.items()}
            for g, r in reads.items():
                if r[0]:
                    num_idx += NUM_IDX.get(nkey(r[0]), [])[:300]
            for nk in number_keys(body_box.get("text", "")):  # número impreso del cuerpo, en cualquier juego
                if nk in NUM_IDX:
                    num_idx += NUM_IDX[nk][:300]
            if not found:  # el nombre no estaba arriba (One Piece, Lorcana, Digimon…): se busca también en el cuerpo de la carta
                body_idx, body_names = match_names([x for x in body_box.get("text", "").split("\n")[:40]])
                name_idx += body_idx[:600]; found = body_names[:6]
                ocr_info["name_body"] = True
            ocr_info.update(name=found, lines=lines[:4])
            gm = GMASK["m"]
            vis_full = REF_SPEC["mat"] @ best["vec"]
            cat_arr = REF_SPEC["cat"]
            lang_info = {"code": None, "conf": 0.0, "source": "none"}
            if pokemon_in_play:  # la carpeta/juego incluye Pokémon: se detecta el idioma
                body = body_box.get("text", "")
                lang_info = detect_language(body, int(cat_arr[best["top"][0][0]]))
                stamp = league_stamp(piece_ocr)
                is_league = stamp >= STAMP_MIN
                ocr_info["league"] = round(stamp, 2)
                if lang_info["code"]:
                    prune = np.isin(cat_arr, PK_CATS) & (cat_arr != PK_LANG_CAT[lang_info["code"]])  # fuera las versiones de Pokémon en los otros idiomas
                    if is_league:
                        prune &= ~REF_SPEC["prize"]  # las cartas de liga existen sobre todo en la entrada inglesa: se dejan (la web usa su vista en español)
                    gm_new = ~prune if gm is None else (gm & ~prune)
                    # Solo se restringe si en ese idioma hay una carta que se le parezca tanto como la mejor de todos los idiomas. Si no
                    # (p. ej. una carta en español que solo tiene entrada en inglés), se deja todo: el idioma queda anotado y quien llama
                    # usa la ficha inglesa de la edición con el idioma de la carta (en español, la imagen solo si ya existe en español).
                    best_lang = float(np.where(gm_new, vis_full, -1.0).max())
                    best_all = float(vis_full.max() if gm is None else np.where(gm, vis_full, -1.0).max())
                    if best_lang >= 0.55 and best_lang >= best_all - 0.08:
                        gm = gm_new; GMASK["m"] = gm_new
                    else:
                        lang_info["source"] = "sin_entrada_en_ese_idioma"
            ocr_info["language"] = {k: lang_info[k] for k in ("code", "conf", "source")}
            if gm is not None:
                name_idx = [i for i in name_idx if gm[i]]; num_idx = [i for i in num_idx if gm[i]]
            vis_all = vis_full if gm is None else np.where(gm, vis_full, -1.0)
            pool = set(np.argsort(-vis_all)[:50].tolist()) | set(name_idx) | set(num_idx)
            learn_boost = {}  # cartas cuya foto real confirmada se parece a esta
            if LEARN["mat"] is not None:
                ls = LEARN["mat"] @ best["vec"]
                for j in np.argsort(-ls)[:8].tolist():
                    i = PIDX.get((int(LEARN["cat"][j]), int(LEARN["pid"][j])))
                    if ls[j] >= LEARN_MIN and i is not None and (gm is None or bool(gm[i])):
                        learn_boost[i] = max(learn_boost.get(i, 0.0), float((ls[j] - LEARN_MIN) / (1 - LEARN_MIN)))
                pool |= set(learn_boost)
            ids = np.array(sorted(pool))
            sims = REF_SPEC["mat"][ids] @ best["vec"]
            nm, nu = set(name_idx), set(num_idx)
            region_extra = np.zeros(len(ids))  # lo impreso leído en la zona del juego, ya corregido solo hacia números de los candidatos
            if reads:
                top_sim = float(sims.max())
                by_game = {}
                for j, i in enumerate(ids.tolist()):
                    by_game.setdefault(impreso.GAME_OF_CAT.get(int(cat_arr[i])), []).append((j, i))
                for game, members in by_game.items():
                    read = (reads.get(game) or (None,))[0]
                    if not read:
                        continue
                    canon = {i: impreso.canon_number(game, NUMRAW[i]) for _, i in members}
                    corrected = False
                    if read not in {canon[i] for j, i in members if sims[j] >= top_sim - 0.12}:
                        fix = impreso.constrain_to_candidates(read, {canon[i] for j, i in members if sims[j] >= top_sim - 0.12})
                        if fix:
                            read, corrected = fix[0], True
                    for j, i in members:
                        verdict = impreso.compare_canon(read, canon[i])
                        if verdict == "exact" and corrected:
                            region_extra[j] = 0.06  # una lectura «corregida» no confirma, solo orienta
                        elif verdict == "partial":
                            region_extra[j] = 0.04
                        elif verdict == "none" and canon[i]:
                            region_extra[j] = -0.03
            ok_vis = sims >= 0.45  # un nombre leido solo suma si la carta tambien se parece visualmente
            seen_stamp = ocr_info.get("league")  # None: no se midió el sello (no es una búsqueda de Pokémon)
            if seen_stamp is None: league_boost = 0.0
            elif seen_stamp >= STAMP_MIN: league_boost = np.where(REF_SPEC["prize"][ids], 0.25, -0.05)
            elif seen_stamp < NO_STAMP_MAX: league_boost = np.where(REF_SPEC["prize"][ids], -0.05, 0.0)  # sin sello claro: la edición normal gana el empate con su versión de liga
            else: league_boost = 0.0
            league_boost = league_boost * (sims >= sims.max() - 0.10)  # con sello de liga: la versión de liga manda, pero solo entre cartas que ya se parecen a la mejor (el sello no identifica)
            w = dict(WEIGHTS)
            if BENCH and request.headers.get("x-weights"):  # solo para calibrar con el banco de pruebas: «nombre,numero,ambos»
                try:
                    w.update(zip(("name", "num", "both", "learn"), [float(x) for x in request.headers["x-weights"].split(",")]))
                except ValueError:
                    pass
            both = np.array([w["both"] if (i in nm and i in nu) else 0.0 for i in ids.tolist()])  # nombre y número a la vez: lo que distingue una edición de otra
            learned = np.array([learn_boost.get(i, 0.0) for i in ids.tolist()]) * w.get("learn", 0.0)
            final = sims + learned + ok_vis * (np.array([w["name"] if i in nm else 0.0 for i in ids.tolist()]) + np.array([w["num"] if i in nu else 0.0 for i in ids.tolist()]) + both + region_extra + league_boost)
            order = np.argsort(-final)[:5]
            best["top"] = [(int(ids[j]), float(final[j])) for j in order]
            ocr_info["boost"] = round(float(final[order[0]] - sims[order[0]]), 2)
            # margen contra la mejor carta de OTRO nombre (las reimpresiones de la misma carta no cuentan como duda)
            top_name = NAME_OF[int(ids[order[0]])]
            alt = [final[j] for j in np.argsort(-final) if NAME_OF[int(ids[j])] != top_name]
            ocr_info["margin"] = round(float(final[order[0]] - (alt[0] if alt else final[order[0]] - 0.2)), 3)
            # estado (como el visor del navegador): confirmado · probable · ambiguo · dudosa · sin_coincidencias
            ranked = np.argsort(-final)
            first, first_j = int(ids[order[0]]), int(order[0])
            margin = float(final[first_j] - final[ranked[1]]) if len(ranked) > 1 else 9.0
            printed_hit, name_hit = first in nu, first in nm
            rival_same = printed_hit and any(int(ids[j]) in nu and sims[first_j] - sims[j] < 0.05 for j in ranked[1:])  # otra impresión con el mismo número: lo impreso no desambigua
            rival_other = sum(1 for j in ranked[1:8] if final[first_j] - final[j] < 0.04 and NAME_OF[int(ids[j])] != NAME_OF[first]) >= 2
            reasons = []
            if float(sims.max()) < 0.7:
                status = "sin_coincidencias"
            elif sims[first_j] < 0.8 and rival_other and not printed_hit and not name_hit:
                status = "dudosa"; reasons.append("varias cartas distintas igual de parecidas")
            elif printed_hit and not rival_same and margin >= 0.02:
                status = "confirmado"
            elif margin >= 0.04 and not rival_same:
                status = "probable"
            else:
                status = "ambiguo"; reasons.append("varias impresiones con el mismo número impreso" if rival_same else "candidatos demasiado parecidos")
            if not any(r[0] for r in reads.values()):
                reasons.append("sin lectura de número: solo evidencia visual")
            ocr_info.update(status=status, reasons=reasons)
        except Exception as exc:  # noqa: BLE001
            print("ocr fallo:", exc, flush=True)
    tm["ocr+reordenar"] = time.perf_counter() - mark; mark = time.perf_counter()
    # otras impresiones de la misma carta (mismo nombre en el catálogo), las más parecidas primero
    same = []
    try:
        shown = {t[0] for t in best["top"]}
        key = NAME_OF[best["top"][0][0]]
        pool = [i for i in NAME_IDX.get(key, []) if i not in shown and (GMASK["m"] is None or GMASK["m"][i])]
        if pool:
            sims = REF_SPEC["mat"][pool] @ best["vec"]
            for j in np.argsort(-sims)[:20]:
                i = pool[int(j)]
                same.append({**meta(int(REF_SPEC["cat"][i]), int(REF_SPEC["pid"][i])), "score": round(float(sims[j]), 4)})
    except Exception as exc:  # noqa: BLE001
        print("misma carta fallo:", exc, flush=True)
    try:
        if SERVICE_MODE:
            raise OSError  # en el servidor no se guarda registro de escaneos
        with open(DATA / "escaneos.jsonl", "a", encoding="utf-8") as lf:
            lf.write(_json.dumps({"t": int(time.time() * 1000), "src": best["label"], "e1": bool(e1.found), "e1c": round(float(e1.confidence), 3),
                                  "s1": round(best["top"][0][1], 4), "s2": round(best["top"][1][1], 4), "name": meta(int(REF_SPEC["cat"][best["top"][0][0]]), int(REF_SPEC["pid"][best["top"][0][0]]))["name"]}, ensure_ascii=False) + chr(10))
    except Exception:  # noqa: BLE001
        pass
    tm["otras ediciones+datos"] = time.perf_counter() - mark
    if SERVICE_MODE:  # una línea por escaneo en el registro del contenedor: dónde se va el tiempo
        print("scan " + f"total={(time.perf_counter() - t0) * 1000:.0f}ms bytes={len(data)} candidatas={n_emb} " + " ".join(f"{k.replace(' ', '_')}={v * 1000:.0f}ms" for k, v in tm.items()), flush=True)
    prev = cv2.imencode(".jpg", cv2.resize(best["img"], (110, 154)))[1].tobytes()
    out = {"found": True, "source": best["label"], "e1": bool(e1.found), "e1_conf": round(float(e1.confidence), 3),
            "top": [{**meta(int(REF_SPEC["cat"][i]), int(REF_SPEC["pid"][i])), "score": round(s, 4), "group": NAME_OF[i] if i < len(NAME_OF) else ""} for i, s in best["top"]],
            "same": same, "indexed": int(len(REF_SPEC["pid"])), "ocr": ocr_info, "status": ocr_info.get("status"), "reasons": ocr_info.get("reasons", []),
            "preview": "data:image/jpeg;base64," + base64.b64encode(prev).decode(), "ms": round((time.perf_counter() - t0) * 1000)}
    if LAB and not (BENCH and request.headers.get("x-bench")):  # el banco de pruebas manda x-bench y no ensucia el laboratorio
        try:
            sid = uuid.uuid4().hex
            LAB_DIR.mkdir(parents=True, exist_ok=True)
            (LAB_DIR / f"{sid}.jpg").write_bytes(data)
            np.save(LAB_DIR / f"{sid}.npy", np.asarray(best["vec"], np.float16))
            slim = {"found": True, "source": best["label"], "ms": out["ms"], "ocr": out["ocr"], "top": [{k: t.get(k) for k in ("name", "number", "set", "rarity", "lang", "category_id", "product_id", "score")} for t in out["top"]]}
            (LAB_DIR / f"{sid}.json").write_text(_json.dumps({"id": sid, "t": int(time.time()), "crop": request.headers.get("x-crop", ""), "games": request.headers.get("x-games", ""), "aprendidas_antes": 0 if LEARN["pid"] is None else int(len(LEARN["pid"])), "resultado": slim, "etiqueta": None}, ensure_ascii=False), encoding="utf-8")
            out["scan_id"] = sid
        except Exception as exc:  # noqa: BLE001
            print("guardar escaneo fallo:", exc, flush=True)
    return out


def warm_up() -> None:
    try:
        noise = np.random.default_rng(0).integers(0, 255, (CANON_H, CANON_W, 3), dtype=np.uint8)
        t = time.perf_counter()
        with lock:
            svc.embed(noise)
        print(f"modelo listo en {time.perf_counter() - t:.1f} s", flush=True)
    except Exception as exc:  # noqa: BLE001
        print("calentamiento fallo:", exc, flush=True)


if __name__ == "__main__":
    load_index()
    if LAB:
        LAB_DIR.mkdir(parents=True, exist_ok=True)
        load_learn()
        if not CERT:  # la cámara del navegador exige HTTPS: certificado propio (el navegador pedirá aceptarlo una vez)
            CERT, KEY = str(LAB_DIR / "lab.crt"), str(LAB_DIR / "lab.key")
            if not Path(CERT).exists():
                subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650", "-subj", "/CN=carpetazo-lab", "-keyout", KEY, "-out", CERT], check=True, capture_output=True)  # noqa: S603,S607
    STATE["t"] = time.time()
    if SERVICE_MODE:
        threading.Thread(target=warm_up, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=PORT, ssl_certfile=CERT, ssl_keyfile=KEY, log_level="warning")  # sin certificado (CERT=None) corre en HTTP
