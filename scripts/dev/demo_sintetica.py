# ruff: noqa: E501
"""Demostración técnica LOCAL con datos 100 % sintéticos (cartas y escenas generadas por código).

No usa fotos reales, red, R2 ni PostgreSQL: la API se levanta con claves desechables y una URL de base de datos inexistente
(los endpoints de catálogo, que necesitan base de datos, no se prueban aquí). Las cifras de búsqueda son de una prueba de
humo con 12 cartas ficticias y NO miden precisión real.

Uso:  .\\.venv\\Scripts\\python.exe scripts\\dev\\demo_sintetica.py [--out CARPETA] [--port 8011]
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import cv2
import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.recognition_lab.augment import NORMAL, make_scene  # noqa: E402
from app.recognition_lab.benchmark import load_bgr  # noqa: E402
from app.recognition_lab.features import GridExtractor, PHashExtractor  # noqa: E402
from app.recognition_lab.index import RefIndex  # noqa: E402
from app.recognition_lab.rectify import find_card_quad, to_canonical, warp_card  # noqa: E402
from tests.test_recognition_lab import fake_card  # noqa: E402

results: list[dict] = []


def record(group: str, name: str, ok: bool, detail: str) -> None:
    results.append({"grupo": group, "prueba": name, "ok": ok, "detalle": detail})
    print(f"[{'OK ' if ok else 'FALLA'}] {group} · {name}: {detail}")


def api_tests(out: Path, port: int) -> None:
    fx = out / "fixtures"
    fx.mkdir(parents=True, exist_ok=True)
    good = fx / "escena_valida.jpg"
    scene, _ = make_scene(fake_card(1), 1, NORMAL)
    cv2.imwrite(str(good), scene, [cv2.IMWRITE_JPEG_QUALITY, 90])
    (fx / "no_es_imagen.txt").write_text("hola")
    (fx / "corrupta.jpg").write_bytes(b"\xff\xd8\xff garbage")
    (fx / "vacia.jpg").write_bytes(b"")
    (fx / "formato_gif.gif").write_bytes(b"GIF89a" + b"\x00" * 50)
    cv2.imwrite(str(fx / "muy_pequena.png"), np.full((100, 100, 3), 128, np.uint8))
    cv2.imwrite(str(fx / "borrosa.jpg"), cv2.GaussianBlur(scene, (0, 0), 30))

    key = "demo-" + secrets.token_hex(8)
    env = {
        **os.environ,
        "VISION_ENVIRONMENT": "development",
        "VISION_SECRET_KEY": secrets.token_urlsafe(48),
        "VISION_SERVICE_API_KEYS": key,
        "VISION_DATABASE_URL": "postgresql+psycopg://nadie:nadie@127.0.0.1:59999/ninguna",  # inexistente a propósito
    }
    log = (out / "servidor.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )  # fmt: skip
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            record("API", "arranque", False, "no respondió en 30 s (ver servidor.log)")
            return
        record("API", "arranque", True, f"escuchando en {base}")
        r = httpx.get(f"{base}/health")
        record("API", "GET /health", r.status_code == 200, f"{r.status_code} {r.text[:80]}")
        r = httpx.get(f"{base}/v1/version")
        record("API", "GET /v1/version", r.status_code == 200, f"{r.status_code} {r.text[:80]}")
        r = httpx.get(f"{base}/docs")
        record("API", "GET /docs (Swagger, solo en desarrollo)", r.status_code == 200, str(r.status_code))
        r = httpx.get(f"{base}/")
        record("Interfaz web", "GET / (¿hay interfaz web?)", False, f"{r.status_code}: NO existe interfaz web; solo API y /docs")
        r = httpx.post(f"{base}/v1/sessions")
        record("Seguridad", "crear sesión sin clave de servicio", r.status_code == 401, str(r.status_code))
        r = httpx.post(f"{base}/v1/sessions", headers={"X-Service-Key": "incorrecta"})
        record("Seguridad", "crear sesión con clave incorrecta", r.status_code == 401, str(r.status_code))
        r = httpx.post(f"{base}/v1/sessions", headers={"X-Service-Key": key})
        record("API", "POST /v1/sessions con clave válida", r.status_code == 201, f"{r.status_code}, token recibido: {'token' in r.json()}")
        token = r.json()["token"]
        auth = {"Authorization": f"Bearer {token}"}

        def scan(path: Path | None, headers=auth, name="file"):
            if path is None:
                return httpx.post(f"{base}/v1/scan", headers=headers)
            return httpx.post(f"{base}/v1/scan", headers=headers, files={name: (path.name, path.read_bytes())})

        r = scan(good)
        j = r.json()
        record("Escaneo", "imagen sintética válida", r.status_code == 200 and j["recognition_available"] is False, f"{r.status_code} status={j.get('status')} calidad={j.get('quality')} recognition_available={j.get('recognition_available')}")
        sid = j.get("scan_id")
        r = httpx.get(f"{base}/v1/scan/{sid}", headers=auth)
        record("Escaneo", "consultar resultado por scan_id", r.status_code == 200, str(r.status_code))
        r = httpx.get(f"{base}/v1/scan/no-existe", headers=auth)
        record("Errores", "scan_id inexistente", r.status_code == 404, f"{r.status_code} {r.json()['detail']['error']}")
        for fname, expected in (("no_es_imagen.txt", 415), ("corrupta.jpg", 422), ("formato_gif.gif", 415), ("vacia.jpg", 422), ("muy_pequena.png", None), ("borrosa.jpg", None)):
            time.sleep(0.2)
            r = scan(fx / fname)
            body = r.json()
            code = body.get("detail", {}).get("error") if "detail" in body else body.get("status")
            ok = (r.status_code == expected) if expected else r.status_code in (200, 422)
            record("Errores", f"archivo {fname}", ok, f"{r.status_code} {code}")
        r = scan(None)
        record("Errores", "sin archivo", r.status_code == 422, str(r.status_code))
        r = scan(good, headers={})
        record("Seguridad", "escaneo sin token", r.status_code == 401, str(r.status_code))
        r = scan(good, headers={"Authorization": "Bearer falso"})
        record("Seguridad", "escaneo con token falso", r.status_code == 401, str(r.status_code))
        big = fx / "enorme.jpg"
        big.write_bytes(b"\xff\xd8\xff" + os.urandom(5_000_000))
        r = scan(big)
        record("Errores", "archivo de 5 MB (límite 4 MB)", r.status_code == 413, str(r.status_code))
        big.unlink()
        r = httpx.get(f"{base}/v1/catalog/search?q=pikachu", timeout=15)
        record("Catálogo", "búsqueda sin credenciales", r.status_code == 401, f"{r.status_code}; el catálogo necesita PostgreSQL y no se prueba aquí (lo cubren las pruebas pytest)")
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        record("API", "parada del servidor", proc.poll() is not None, f"código de salida {proc.returncode}")


def retrieval_tests(out: Path) -> None:
    n = 12
    cards = {f"carta-ficticia-{i:02d}": fake_card(i) for i in range(n)}
    for ext in (PHashExtractor(), GridExtractor()):
        idx = RefIndex(ext.dim)
        t0 = time.perf_counter()
        idx.add([(cid, cid, ext.embed(img)) for cid, img in cards.items()])
        record("Embeddings", f"{ext.name}: {len(idx)} referencias", True, f"dim={ext.dim}, norma={float(np.linalg.norm(ext.embed(cards['carta-ficticia-00']))):.3f}, {1000 * (time.perf_counter() - t0):.0f} ms")
        hits = misses = 0
        for i, (cid, img) in enumerate(cards.items()):
            scene, _ = make_scene(img, 500 + i, NORMAL)
            quad, _ = find_card_quad(scene)
            if quad is None:
                misses += 1
                continue
            card = warp_card(scene, quad)
            best = max((idx.search(ext.embed(c), 3) for c in (card, cv2.rotate(card, cv2.ROTATE_180))), key=lambda r: r[0][1])
            hits += best[0][0] == cid
        record("Búsqueda (SINTÉTICA)", f"{ext.name}: top-1 en escenas simuladas", hits >= n // 2, f"{hits}/{n} correctas, {misses} sin carta detectada. Humo con datos ficticios: NO es precisión real")
        res = idx.search(ext.embed(cards["carta-ficticia-03"]), 3)
        record("Búsqueda (SINTÉTICA)", f"{ext.name}: consulta exacta devuelve su propia carta", res[0][0] == "carta-ficticia-03" and res[0][1] > 0.99, f"{res[0][0]} score={res[0][1]:.3f}")
        extranjera = ext.embed(fake_card(999))
        d = idx.decide(idx.search(extranjera, 5), min_score=0.97, min_margin=0.02)
        record("Búsqueda (SINTÉTICA)", f"{ext.name}: carta desconocida con umbral estricto", d.status == "unknown", f"decisión={d.status} score={d.top_score:.3f} margen={d.margin:.3f}")
        try:
            idx.add([("x", "x", np.zeros(ext.dim + 1, np.float32))])
            record("Errores", f"{ext.name}: vector de dimensión errónea", False, "se aceptó")
        except ValueError as exc:
            record("Errores", f"{ext.name}: vector de dimensión errónea", True, f"rechazado: {exc}")
    vacio = RefIndex(8)
    r = vacio.search(np.ones(8, np.float32), 5)
    record("Errores", "búsqueda en índice vacío", r == [], f"resultados: {r} (sin resultados, sin error)")
    for label, path in (("archivo inexistente", out / "no_existe.png"), ("archivo no imagen", out / "fixtures" / "no_es_imagen.txt")):
        try:
            img = load_bgr(path)
            record("Errores", f"load_bgr con {label}", False, f"devolvió {type(img)}")
        except Exception as exc:  # noqa: BLE001
            record("Errores", f"load_bgr con {label}", True, f"{type(exc).__name__}: {str(exc)[:70]}")
    q = to_canonical(fake_card(1))
    record("Embeddings", "imagen canónica de carta", q is not None and q.ndim == 3, f"forma {q.shape}")
    scene = np.full((800, 600, 3), 200, np.uint8)
    quad, _ = find_card_quad(scene)
    record("Errores", "escena sin carta (fondo liso)", quad is None, "no se detecta ninguna carta" if quad is None else f"detectó {quad.tolist()}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "evaluation" / "reports" / "demo-sintetica"))
    ap.add_argument("--port", type=int, default=8011)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    api_tests(out, a.port)
    retrieval_tests(out)
    (out / "resultados.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    bad = [r for r in results if not r["ok"] and r["grupo"] != "Interfaz web"]
    print(f"\n{len(results) - len(bad)}/{len(results)} comprobaciones OK; evidencia en {out}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
