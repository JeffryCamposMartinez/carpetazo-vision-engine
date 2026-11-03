# ruff: noqa: E501
"""Extractores de características. El índice no depende de cuál se use: añadir cartas = embeber y agregar (sin reentrenar).

Incluidos:
- phash / grid: líneas base clásicas, sin descargas ni dependencias de deep learning.
- HuggingFaceExtractor (DINOv2, SigLIP): PREPARADO PERO NO EJECUTADO; requiere `torch` y `transformers` y descargar pesos
  (necesita autorización previa; ver docs/recognition/).
"""

from typing import Protocol

import cv2
import numpy as np

CANON_W, CANON_H = 440, 614  # lienzo canónico (proporción 63:88)


class Extractor(Protocol):
    name: str
    dim: int

    def embed(self, bgr: np.ndarray) -> np.ndarray: ...


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return (v / n).astype(np.float32) if n > 0 else v.astype(np.float32)


class PHashExtractor:
    """Hash perceptual DCT de 64 bits como vector ±1 (la similitud coseno equivale a distancia de Hamming)."""

    name = "phash"
    dim = 64

    def embed(self, bgr: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(cv2.resize(bgr, (32, 32), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        dct = cv2.dct(gray.astype(np.float32))[:8, :8].flatten()
        bits = dct > np.median(dct[1:])
        return _unit(np.where(bits, 1.0, -1.0))


class GridExtractor:
    """Color medio por celda + histograma de orientaciones de gradiente por celda (≈ HOG grueso)."""

    name = "grid"
    dim = 8 * 11 * 3 + 4 * 6 * 8

    def embed(self, bgr: np.ndarray) -> np.ndarray:
        color = cv2.resize(bgr, (8, 11), interpolation=cv2.INTER_AREA).astype(np.float32).flatten()
        color = (color - color.mean()) / 255.0
        gray = cv2.cvtColor(cv2.resize(bgr, (96, 132), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(
            np.float32
        )
        gx, gy = cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1)
        mag, ang = cv2.cartToPolar(gx, gy)
        bins = np.minimum((ang / (2 * np.pi) * 8).astype(int), 7)
        ch, cw = 132 // 6, 96 // 4
        hist = np.zeros((6, 4, 8), np.float32)
        for i in range(6):
            for j in range(4):
                b = bins[i * ch : (i + 1) * ch, j * cw : (j + 1) * cw]
                m = mag[i * ch : (i + 1) * ch, j * cw : (j + 1) * cw]
                hist[i, j] = np.bincount(b.ravel(), weights=m.ravel(), minlength=8)
        hist = hist.flatten()
        hist /= hist.sum() + 1e-6
        return _unit(np.concatenate([_unit(color), _unit(hist)]))


class Dinov2Extractor:  # pragma: no cover - requiere torch/transformers y pesos descargados (entorno ML aparte)
    """DINOv2 congelado. Embedding = CLS + media de parches (2 x hidden) o solo CLS.

    Preprocesado propio (sin AutoImageProcessor): redimensiona a (ancho, alto) conservando la proporción de carta
    (por defecto 224x308, múltiplos de 14) SIN recorte central, normaliza con media/desviación de ImageNet.
    """

    def __init__(self, model_id: str = "facebook/dinov2-small", device: str | None = None, pooling: str = "cls_mean",
                 size: tuple[int, int] = (224, 308), cache_dir: str | None = None, local_only: bool = False):  # fmt: skip
        import torch
        from transformers import Dinov2Model

        self._torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = (
            Dinov2Model.from_pretrained(model_id, cache_dir=cache_dir, local_files_only=local_only)
            .eval()
            .to(self.device)
        )
        self.pooling, self.size = pooling, size
        self.name = f"{model_id.split('/')[-1]}-{pooling}-{size[0]}x{size[1]}-{self.device}"
        self._mean = torch.tensor([0.485, 0.456, 0.406], device=self.device).view(1, 3, 1, 1)
        self._std = torch.tensor([0.229, 0.224, 0.225], device=self.device).view(1, 3, 1, 1)
        self.dim = self.model.config.hidden_size * (2 if pooling == "cls_mean" else 1)

    def embed(self, bgr: np.ndarray) -> np.ndarray:
        torch = self._torch
        rgb = cv2.cvtColor(cv2.resize(bgr, self.size, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
        x = torch.from_numpy(rgb).to(self.device).permute(2, 0, 1).unsqueeze(0).float() / 255.0
        with torch.inference_mode():
            h = self.model(pixel_values=(x - self._mean) / self._std).last_hidden_state[0]
            vec = torch.cat([h[0], h[1:].mean(0)]) if self.pooling == "cls_mean" else h[0]
        return _unit(vec.float().cpu().numpy())


REGISTRY = {"phash": PHashExtractor, "grid": GridExtractor}
