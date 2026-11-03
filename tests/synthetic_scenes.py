# ruff: noqa: E501
"""Escenas SINTÉTICAS para probar el detector (cartas ficticias, fondos generados, objetos que no son cartas). No hay fotos reales."""

import cv2
import numpy as np

from app.recognition_lab.augment import EASY, HARD, NORMAL, Difficulty, _background, make_scene
from tests.test_recognition_lab import fake_card

SIZE = (1280, 960)
GLARE_HEAVY = Difficulty("glare", 0.10, 25, 0.8, 4.0, 80, 0.85, 0.0)


def card_scene(seed: int, level: Difficulty = NORMAL) -> tuple[np.ndarray, np.ndarray]:
    return make_scene(fake_card(seed), seed, level, SIZE)


def cluttered_card_scene(seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Carta sobre un mantel a rayas con objetos (rectángulos, círculos y líneas) alrededor, fuera de la carta."""
    scene, quad = make_scene(fake_card(seed), seed, NORMAL, SIZE)
    rng = np.random.default_rng(seed + 7000)
    h, w = scene.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(mask, quad.astype(np.int32), 255)
    mask = cv2.dilate(mask, np.ones((25, 25), np.uint8))
    layer = scene.copy()
    for x in range(0, w, 38):  # rayas del mantel
        cv2.line(layer, (x, 0), (x - 120, h), tuple(int(v) for v in rng.integers(40, 220, 3)), 9)
    for _ in range(10):
        x, y = int(rng.integers(0, w - 150)), int(rng.integers(0, h - 150))
        col = tuple(int(v) for v in rng.integers(0, 255, 3))
        if rng.random() < 0.5:
            cv2.rectangle(layer, (x, y), (x + int(rng.integers(30, 140)), y + int(rng.integers(30, 140))), col, -1)
        else:
            cv2.circle(layer, (x, y), int(rng.integers(15, 70)), col, -1)
    out = np.where(mask[..., None] > 0, scene, layer)
    return out, quad


def blank_wall(seed: int) -> np.ndarray:
    return np.clip(_background(np.random.default_rng(seed), *SIZE), 0, 255).astype(np.uint8)


def noise_image(seed: int) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 255, (SIZE[1], SIZE[0], 3)).astype(np.uint8)


def round_object(seed: int) -> np.ndarray:
    img = blank_wall(seed)
    cv2.circle(img, (SIZE[0] // 2, SIZE[1] // 2), 300, (230, 230, 230), -1)
    return img


def square_object(seed: int) -> np.ndarray:  # proporción 1:1, no 63:88
    img = blank_wall(seed)
    cv2.rectangle(img, (400, 200), (900, 700), (30, 160, 220), -1)
    return img


def wide_banner(seed: int) -> np.ndarray:  # proporción 4:1
    img = blank_wall(seed)
    cv2.rectangle(img, (150, 380), (1130, 620), (240, 240, 240), -1)
    return img


def text_page(seed: int) -> np.ndarray:  # hoja A4 (0,707): proporción parecida a la carta pero cuadrícula de texto; se reporta como lo que es: forma de carta
    img = blank_wall(seed)
    cv2.rectangle(img, (380, 60), (900, 60 + int(520 / 0.707)), (245, 245, 245), -1)
    for y in range(120, 860, 28):
        cv2.line(img, (420, y), (860, y), (20, 20, 20), 3)
    return img


def many_rectangles(seed: int) -> np.ndarray:
    img = blank_wall(seed)
    rng = np.random.default_rng(seed)
    for _ in range(40):
        x, y = int(rng.integers(0, 1100)), int(rng.integers(0, 800))
        cv2.rectangle(img, (x, y), (x + int(rng.integers(15, 90)), y + int(rng.integers(15, 90))), tuple(int(v) for v in rng.integers(0, 255, 3)), -1)
    return img


def card_cut_by_border(seed: int) -> np.ndarray:
    """Carta desplazada: media carta fuera del encuadre."""
    scene, _ = make_scene(fake_card(seed), seed, EASY, SIZE)
    shift = np.float32([[1, 0, 380], [0, 1, 0]])
    return cv2.warpAffine(scene, shift, SIZE, borderMode=cv2.BORDER_REPLICATE)


LEVELS = {"easy": EASY, "normal": NORMAL, "hard": HARD, "glare": GLARE_HEAVY}
NOT_CARDS = {"pared": blank_wall, "ruido": noise_image, "circulo": round_object, "cuadrado": square_object, "banner": wide_banner, "muchos_rectangulos": many_rectangles}
