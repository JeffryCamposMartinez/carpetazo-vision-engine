# ruff: noqa: E501
"""Extractor clásico `grid` (respaldo): color por celdas + histogramas de orientación de gradiente. Sin dependencias pesadas ni descargas."""

import cv2
import numpy as np

from app.recognition.embedding.base import EmbeddingSpec, unit, validate_card_image

GRID_SPEC = EmbeddingSpec("grid", "g1-8x11c-4x6x8g-p1", 8 * 11 * 3 + 4 * 6 * 8)


class GridExtractor:
    spec = GRID_SPEC

    def embed(self, card_bgr: np.ndarray) -> np.ndarray:
        validate_card_image(card_bgr)
        color = cv2.resize(card_bgr, (8, 11), interpolation=cv2.INTER_AREA).astype(np.float32).flatten()
        color = (color - color.mean()) / 255.0
        gray = cv2.cvtColor(cv2.resize(card_bgr, (96, 132), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.float32)
        gx, gy = cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1)
        mag, ang = cv2.cartToPolar(gx, gy)
        bins = np.minimum((ang / (2 * np.pi) * 8).astype(int), 7)
        ch, cw = 132 // 6, 96 // 4
        hist = np.zeros((6, 4, 8), np.float32)
        for i in range(6):
            for j in range(4):
                b, m = bins[i * ch : (i + 1) * ch, j * cw : (j + 1) * cw], mag[i * ch : (i + 1) * ch, j * cw : (j + 1) * cw]
                hist[i, j] = np.bincount(b.ravel(), weights=m.ravel(), minlength=8)
        hist = hist.flatten()
        hist /= hist.sum() + 1e-6
        return unit(np.concatenate([unit(color), unit(hist)]))

    def close(self) -> None:
        return None
