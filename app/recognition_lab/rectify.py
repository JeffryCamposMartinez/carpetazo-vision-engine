"""Detección clásica de UNA carta y corrección de perspectiva (línea base; no es el detector final)."""

import cv2
import numpy as np

from app.recognition_lab.features import CANON_H, CANON_W

CARD_ASPECT = CANON_W / CANON_H


def order_quad(pts: np.ndarray) -> np.ndarray:
    """Orden: arriba-izq, arriba-der, abajo-der, abajo-izq."""
    pts = pts.reshape(4, 2).astype(np.float32)
    s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
    return np.array([pts[s.argmin()], pts[d.argmin()], pts[s.argmax()], pts[d.argmax()]], np.float32)


def find_card_quad(bgr: np.ndarray) -> tuple[np.ndarray | None, float]:
    """Devuelve (cuadrilátero en coordenadas de la imagen original, puntuación 0-1)."""
    scale = 640 / max(bgr.shape[:2])
    small = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else bgr
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    edges = cv2.Canny(gray, 40, 120)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    area_img = small.shape[0] * small.shape[1]
    best, best_score = None, 0.0
    for c in contours:
        area = cv2.contourArea(c)
        if area < 0.05 * area_img:
            continue
        hull = cv2.convexHull(c)
        approx = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True)
        quad = approx.reshape(-1, 2) if len(approx) == 4 else cv2.boxPoints(cv2.minAreaRect(hull))
        q = order_quad(quad)
        w = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
        h = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
        if min(w, h) < 20:
            continue
        aspect = min(w, h) / max(w, h)
        fit = max(0.0, 1 - abs(aspect - CARD_ASPECT) / CARD_ASPECT)
        fill = area / max(cv2.contourArea(q.astype(np.float32)), 1.0)
        score = min(1.0, area / area_img * 2) * fit * min(1.0, fill)
        if score > best_score:
            best, best_score = q, score
    return (best / scale if best is not None else None), best_score


def warp_card(bgr: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Endereza el cuadrilátero a 440x614 (retrato). La orientación de 180° no se resuelve aquí."""
    q = order_quad(quad)
    w = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
    h = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
    if w > h:  # carta tumbada: girar el orden de vértices 90°
        q = np.roll(q, -1, axis=0)
    dst = np.array([[0, 0], [CANON_W - 1, 0], [CANON_W - 1, CANON_H - 1], [0, CANON_H - 1]], np.float32)
    return cv2.warpPerspective(bgr, cv2.getPerspectiveTransform(q, dst), (CANON_W, CANON_H), flags=cv2.INTER_AREA)


def to_canonical(reference_bgr: np.ndarray) -> np.ndarray:
    """Las imágenes de referencia ya son la carta entera: solo se reescalan."""
    return cv2.resize(reference_bgr, (CANON_W, CANON_H), interpolation=cv2.INTER_AREA)
