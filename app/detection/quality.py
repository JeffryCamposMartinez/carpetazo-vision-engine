"""Evaluación básica de calidad del fotograma (nitidez e iluminación)."""

from dataclasses import dataclass

import cv2
import numpy as np

_ANALYSIS_SIDE = 640


@dataclass(frozen=True)
class FrameQuality:
    sharpness: float  # varianza del laplaciano en una versión reducida
    brightness: float  # media de gris, 0-255


def assess_quality(bgr: np.ndarray) -> FrameQuality:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    scale = _ANALYSIS_SIDE / max(gray.shape)
    if scale < 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return FrameQuality(
        sharpness=float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        brightness=float(gray.mean()),
    )
