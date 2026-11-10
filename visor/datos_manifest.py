"""Genera visor/datos.manifest.json: la lista (ruta, hash, tamaño) de los archivos de visor/datos que viven en Git LFS.

El servidor (visor/fetch_data.py) baja los datos según este manifiesto. Vuelve a generarlo y súbelo cada vez que cambies el índice o el catálogo:
    python visor/datos_manifest.py
"""
import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
out = subprocess.run(["git", "lfs", "ls-files", "-l"], cwd=root, capture_output=True, text=True, check=True).stdout  # noqa: S603,S607
files = []
for line in out.splitlines():
    oid, _, path = line.split(" ", 2)
    if path.startswith("visor/datos/"):
        files.append({"path": path[len("visor/datos/"):], "oid": oid, "size": (root / path).stat().st_size})
files.sort(key=lambda f: f["path"])
(root / "visor" / "datos.manifest.json").write_text(json.dumps({"files": files}, indent=1), encoding="utf-8")
print(f"{len(files)} archivos, {sum(f['size'] for f in files) / 1e6:.0f} MB")
