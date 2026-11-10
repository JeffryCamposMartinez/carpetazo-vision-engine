"""Descarga los pesos de DINOv2-small (revisión auditada) a HF_HOME durante el build de la imagen. El worker los lee sin red."""
import os
from pathlib import Path

from huggingface_hub import snapshot_download

MODEL_ID = "facebook/dinov2-small"
REVISION = "ed25f3a31f01632728cabb09d1542f84ab7b0056"  # la misma que app/recognition/embedding/dinov2_client.py

home = Path(os.environ["HF_HOME"])
snapshot_download(MODEL_ID, revision=REVISION, cache_dir=home / "hub")
refs = home / "hub" / "models--facebook--dinov2-small" / "refs"
refs.mkdir(parents=True, exist_ok=True)
(refs / "main").write_text(REVISION)  # el worker busca la revisión por la referencia «main»
print("pesos listos:", REVISION)
