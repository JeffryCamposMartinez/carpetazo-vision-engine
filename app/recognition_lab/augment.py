"""Escenas SINTÉTICAS (perspectiva, fondo, luz, reflejos, desenfoque, ruido, JPEG). Determinista por semilla.

Son una aproximación para probar el pipeline: NO sustituyen a fotografías reales de teléfono.
"""

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class Difficulty:
    name: str
    tilt: float  # desplazamiento máximo de esquinas (fracción del tamaño de la carta)
    rotation: float  # grados
    blur: float
    noise: float
    jpeg: int
    glare: float  # intensidad del reflejo 0-1
    p_flip180: float  # probabilidad de carta al revés


EASY = Difficulty("easy", 0.04, 12, 0.0, 2.0, 92, 0.0, 0.0)
NORMAL = Difficulty("normal", 0.10, 30, 1.2, 5.0, 70, 0.25, 0.1)
HARD = Difficulty("hard", 0.20, 180, 2.4, 9.0, 45, 0.6, 0.3)
LEVELS = {d.name: d for d in (EASY, NORMAL, HARD)}


def _background(rng: np.random.Generator, w: int, h: int) -> np.ndarray:
    base = rng.integers(20, 235, size=3).astype(np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    grad = 1 + 0.25 * np.sin(xx / w * rng.uniform(1, 4) + rng.uniform(0, 6)) * np.cos(yy / h * rng.uniform(1, 4))
    img = base[None, None, :] * grad[..., None]
    noise = cv2.resize(
        rng.normal(0, 14, (h // 16 + 1, w // 16 + 1, 3)).astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC
    )
    return np.clip(img + noise, 0, 255)


def make_scene(
    card_bgr: np.ndarray, seed: int, level: Difficulty, size: tuple[int, int] = (1280, 960)
) -> tuple[np.ndarray, np.ndarray]:
    """Devuelve (escena BGR uint8, cuadrilátero verdadero 4x2 ordenado arriba-izq, arriba-der, abajo-der, abajo-izq)."""
    rng = np.random.default_rng(seed)
    w, h = size
    scene = _background(rng, w, h)
    ch = h * rng.uniform(0.5, 0.75)
    cw = ch * 63 / 88
    card = cv2.resize(card_bgr, (int(cw), int(ch)), interpolation=cv2.INTER_AREA)
    if rng.random() < level.p_flip180:
        card = cv2.rotate(card, cv2.ROTATE_180)
    src = np.array([[0, 0], [card.shape[1], 0], [card.shape[1], card.shape[0]], [0, card.shape[0]]], np.float32)
    jitter = rng.uniform(-level.tilt, level.tilt, (4, 2)).astype(np.float32) * np.array(
        [card.shape[1], card.shape[0]], np.float32
    )
    ang = np.deg2rad(rng.uniform(-level.rotation, level.rotation))
    rot = np.array([[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]], np.float32)
    centre = np.array([w * rng.uniform(0.42, 0.58), h * rng.uniform(0.42, 0.58)], np.float32)
    dst = (src - src.mean(0) + jitter) @ rot.T + centre
    # margen para que la carta quede dentro de la imagen
    dst -= np.minimum(dst.min(0) - 5, 0)
    dst -= np.maximum(dst.max(0) - np.array([w - 5, h - 5], np.float32), 0)
    matrix = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(card, matrix, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    mask = cv2.warpPerspective(np.full(card.shape[:2], 255, np.uint8), matrix, (w, h))
    mask = cv2.GaussianBlur(mask, (3, 3), 0).astype(np.float32)[..., None] / 255.0
    scene = scene * (1 - mask) + warped.astype(np.float32) * mask

    # luz: degradado direccional + brillo/contraste
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    theta = rng.uniform(0, 2 * np.pi)
    light = 1 + rng.uniform(0.0, 0.35 if level.name != "easy" else 0.12) * (
        (xx - w / 2) * np.cos(theta) + (yy - h / 2) * np.sin(theta)
    ) / max(w, h)
    scene = scene * light[..., None] * rng.uniform(0.85, 1.1) + rng.uniform(-12, 12)
    if level.glare > 0 and rng.random() < 0.7:  # reflejo especular sobre la carta
        gx, gy = dst.mean(0) + rng.uniform(-0.3, 0.3, 2) * np.array([cw, ch], np.float32)
        blob = np.zeros((h, w), np.float32)
        cv2.ellipse(
            blob,
            (int(gx), int(gy)),
            (int(cw * rng.uniform(0.15, 0.4)), int(ch * rng.uniform(0.05, 0.2))),
            rng.uniform(0, 180),
            0,
            360,
            1.0,
            -1,
        )
        blob = cv2.GaussianBlur(blob, (0, 0), cw * 0.1) * mask[..., 0]
        scene = scene + blob[..., None] * 255 * level.glare
    scene = np.clip(scene, 0, 255).astype(np.uint8)
    if level.blur > 0:
        scene = cv2.GaussianBlur(scene, (0, 0), level.blur)
    scene = np.clip(scene.astype(np.float32) + rng.normal(0, level.noise, scene.shape), 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", scene, [cv2.IMWRITE_JPEG_QUALITY, level.jpeg])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR), dst.astype(np.float32)
