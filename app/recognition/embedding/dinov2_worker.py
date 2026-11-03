# ruff: noqa: E501
"""Proceso de trabajo de DINOv2-small. SE EJECUTA CON EL PYTHON DEL ENTORNO ML (vision-ml-lab\\venv), nunca con el de la API.

Autónomo a propósito: no importa nada de `app` (el entorno ML no tiene las dependencias de la API). Solo usa numpy, torch y transformers.
Sin red: `local_files_only=True` y HF_HUB_OFFLINE=1; si faltan los pesos, falla en lugar de descargarlos.
Modelo CONGELADO (eval, sin gradientes, float32): no hay entrenamiento ni ajuste de pesos.

Protocolo por stdin/stdout (binario, little-endian). stdout se reserva para el protocolo; todo lo demás va a stderr.
  salida (arranque): u32 n + n bytes de JSON {"model_id", "revision", "device", "dim", "torch", "transformers", "vram_free_mb"}
  entrada:           u32 alto, u32 ancho, u32 canales, luego alto*ancho*canales bytes (BGR uint8); alto=0 => terminar
  salida:            u32 estado (0 ok, 1 error), u32 n, y n bytes: [u8 dispositivo (0 cpu, 1 cuda)] + float32[dim]  |  texto UTF-8 si error
"""

import json
import os
import struct
import sys

MODEL_ID = "facebook/dinov2-small"
SIZE = (224, 308)  # (ancho, alto): múltiplos de 14, proporción 63:88, sin recorte central
POOLING = "cls_mean"  # CLS + media de parches: 768 dimensiones


def main() -> int:
    out = sys.stdout.buffer
    sys.stdout = sys.stderr  # nada de print accidental en el canal del protocolo
    args = dict(zip(sys.argv[1::2], sys.argv[2::2], strict=False))
    device_pref = args.get("--device", "auto")
    min_free_mb = float(args.get("--min-free-vram-mb", "700"))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    if "--hf-home" in args:
        os.environ["HF_HOME"] = args["--hf-home"]

    import cv2
    import numpy as np
    import torch
    import transformers
    from transformers import Dinov2Model

    free_mb = None
    if torch.cuda.is_available():
        free_mb = torch.cuda.mem_get_info()[0] / 1e6
    use_cuda = device_pref == "cuda" or (device_pref == "auto" and torch.cuda.is_available() and (free_mb or 0) >= min_free_mb)
    if device_pref == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("se pidió cuda pero no está disponible")
    device = "cuda" if use_cuda else "cpu"
    model = Dinov2Model.from_pretrained(MODEL_ID, local_files_only=True).eval()
    from pathlib import Path

    from huggingface_hub import snapshot_download

    revision = Path(snapshot_download(MODEL_ID, local_files_only=True)).name  # la carpeta de la instantánea es el hash de la revisión
    model = model.to(device)
    mean = torch.tensor([0.485, 0.456, 0.406], device=device).view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], device=device).view(1, 3, 1, 1)
    dim = model.config.hidden_size * 2

    def send(status: int, payload: bytes) -> None:
        out.write(struct.pack("<II", status, len(payload)) + payload)
        out.flush()

    hello = json.dumps({"model_id": MODEL_ID, "revision": revision, "device": device, "dim": dim, "torch": torch.__version__, "transformers": transformers.__version__, "vram_free_mb": None if free_mb is None else round(free_mb)}).encode()
    out.write(struct.pack("<I", len(hello)) + hello)
    out.flush()

    def run(bgr: "np.ndarray", dev: str) -> "np.ndarray":
        rgb = cv2.cvtColor(cv2.resize(bgr, SIZE, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
        x = torch.from_numpy(rgb).to(dev).permute(2, 0, 1).unsqueeze(0).float() / 255.0
        with torch.inference_mode():
            h = model(pixel_values=(x - mean.to(dev)) / std.to(dev)).last_hidden_state[0]
            vec = torch.cat([h[0], h[1:].mean(0)])
        v = vec.float().cpu().numpy()
        n = float(np.linalg.norm(v))
        return (v / n if n > 0 else v).astype(np.float32)

    stdin = sys.stdin.buffer
    while True:
        head = stdin.read(12)
        if len(head) < 12:
            return 0
        h, w, c = struct.unpack("<III", head)
        if h == 0:
            return 0
        try:
            data = stdin.read(h * w * c)
            if len(data) != h * w * c:
                return 0
            bgr = np.frombuffer(data, np.uint8).reshape(h, w, c)
            try:
                vec = run(bgr, device)
            except torch.cuda.OutOfMemoryError:
                # La GTX 960 tiene 2 GB y Windows ya usa parte: se pasa a CPU y se sigue.
                torch.cuda.empty_cache()
                model.to("cpu")
                device = "cpu"
                vec = run(bgr, "cpu")
            send(0, bytes([1 if device == "cuda" else 0]) + vec.tobytes())
        except Exception as exc:  # noqa: BLE001 - se informa al cliente, el proceso sigue vivo
            send(1, f"{type(exc).__name__}: {exc}".encode()[:500])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"worker abortado: {type(exc).__name__}: {exc}\n")
        sys.exit(2)
