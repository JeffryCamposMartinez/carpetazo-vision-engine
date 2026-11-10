"""Baja los datos del visor (Git LFS) al volumen del servidor la primera vez; después no vuelve a descargar nada.

Lee visor/datos.manifest.json (ruta, hash y tamaño de cada archivo; lo genera visor/datos_manifest.py) y baja cada archivo de GitHub
(repositorio público), comprueba tamaño y hash y lo deja en VISOR_DATA_HOME (por defecto /data/datos). Reanudable: lo ya bajado se omite.
"""
import hashlib
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

REPO = os.environ.get("VISOR_DATA_REPO", "JeffryCamposMartinez/carpetazo-vision-engine")
REF = os.environ.get("VISOR_DATA_REF", "master")
MANIFEST = Path(__file__).with_name("datos.manifest.json")
DEST = Path(os.environ.get("VISOR_DATA_HOME", "/data/datos"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    files = json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]
    if not files:
        print("el manifiesto del visor está vacío", file=sys.stderr)
        return 1
    for f in files:
        rel, oid, size = f["path"], f["oid"], f["size"]
        out = DEST / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists() and out.stat().st_size == size:
            continue
        url = f"https://media.githubusercontent.com/media/{REPO}/{REF}/visor/datos/{rel}"
        tmp = out.with_suffix(out.suffix + ".part")
        print(f"descargando {rel} ({size / 1e6:.1f} MB)", flush=True)
        with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as fh:  # noqa: S310
            shutil.copyfileobj(r, fh, 1 << 20)
        if tmp.stat().st_size != size or sha256(tmp) != oid:
            tmp.unlink(missing_ok=True)
            print(f"el archivo {rel} no coincide con el manifiesto", file=sys.stderr)
            return 1
        tmp.replace(out)
    print("datos del visor listos", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
