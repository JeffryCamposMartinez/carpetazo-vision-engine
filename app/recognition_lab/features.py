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


class HuggingFaceExtractor:  # pragma: no cover - requiere torch/transformers y pesos descargados
    """DINOv2 (CLS + media de parches) o SigLIP (get_image_features). NO ejecutado en este entorno."""

    def __init__(self, model_id: str, device: str = "cpu"):
        import torch
        from transformers import AutoImageProcessor, AutoModel

        self._torch = torch
        self.model_id, self.name = model_id, model_id.split("/")[-1]
        self.processor = AutoImageProcessor.from_pretrained(model_id)
        self.model = AutoModel.from_pretrained(model_id).eval().to(device)
        self.device = device
        self.is_siglip = "siglip" in model_id.lower()
        self.dim = self.embed(np.zeros((CANON_H, CANON_W, 3), np.uint8)).shape[0]

    def embed(self, bgr: np.ndarray) -> np.ndarray:
        torch = self._torch
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        inputs = self.processor(images=rgb, return_tensors="pt").to(self.device)
        with torch.no_grad():
            if self.is_siglip:
                vec = self.model.get_image_features(**inputs)[0]
            else:
                h = self.model(**inputs).last_hidden_state[0]
                vec = torch.cat([h[0], h[1:].mean(0)])
        return _unit(vec.cpu().numpy())


REGISTRY = {"phash": PHashExtractor, "grid": GridExtractor}
