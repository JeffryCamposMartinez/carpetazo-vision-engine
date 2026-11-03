# ruff: noqa: E501
"""Detección de UNA carta en una fotografía: esquinas, confianza, rechazo `not_a_card` y corrección de perspectiva.

Detector clásico (sin aprendizaje, sin descargas): varias estrategias generan candidatos a cuadrilátero y cada candidato se puntúa
con evidencias geométricas independientes (área, proporción 63:88, rectangularidad, ángulos y soporte de borde a lo largo del contorno).

Honestidad sobre los umbrales: `DEFAULT_MIN_CONFIDENCE` es PROVISIONAL y se ajustó solo con escenas sintéticas; debe recalibrarse con
fotografías reales (ronda A del piloto) antes de confiar en él. La confianza es una puntuación de forma, NO una probabilidad.

Orientación: se corrige la de 90° (carta tumbada). La de 180° no se puede decidir con geometría; `rectify` devuelve ambas versiones
(`RectifiedCard.upright` y `.flipped`) y la etapa de embeddings elige la que mejor coincida.
"""

from dataclasses import dataclass, field, replace

import cv2
import numpy as np

CANON_W, CANON_H = 440, 614  # lienzo canónico (proporción 63:88), igual que las referencias
CARD_ASPECT = 63 / 88
DEFAULT_MIN_CONFIDENCE = 0.62  # provisional: ajustado con escenas sintéticas
WORK_SIDE = 800  # lado largo de trabajo para detectar
MIN_AREA_FRACTION = 0.04
MIN_ASPECT_FIT = 0.75  # puertas duras: por debajo, el candidato ni se puntúa
MIN_RECTANGULARITY = 0.82
MAX_CORNER_DEVIATION = 38.0
EDGE_MARGIN = 0.012  # fracción del lado: un vértice así de cerca del borde indica carta recortada


@dataclass(frozen=True)
class DetectionMetrics:
    area_fraction: float
    aspect_ratio: float  # lado corto / lado largo del cuadrilátero (carta ideal: 0,716)
    aspect_fit: float  # 0-1
    rectangularity: float  # 0-1: contorno frente a su cuadrilátero
    corner_angle_deviation_deg: float  # máxima desviación respecto de 90°
    perspective_skew: float  # 0 = sin perspectiva; razón entre lados opuestos menos 1
    border_support: float  # 0-1: fracción del perímetro respaldada por bordes de la imagen
    touches_image_border: bool
    strong_candidates: int  # cuadriláteros distintos con confianza cercana a la mejor (>1: varias cartas u objetos)


@dataclass(frozen=True)
class Detection:
    found: bool  # False = `not_a_card`
    confidence: float  # 0-1, puntuación de forma (no es una probabilidad)
    quad: np.ndarray | None  # 4x2, coordenadas de la imagen original: arriba-izq, arriba-der, abajo-der, abajo-izq
    metrics: DetectionMetrics | None
    reason: str | None = None  # por qué se rechaza o qué advertencia tiene
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def status_hint(self) -> str:
        return "card" if self.found else "not_a_card"


@dataclass(frozen=True)
class RectifiedCard:
    upright: np.ndarray  # BGR 440x614
    flipped: np.ndarray  # la misma rotada 180°
    sharpness: float  # varianza del laplaciano de la carta rectificada
    brightness: float  # 0-255
    contrast: float  # desviación típica de la luminancia
    glare_fraction: float  # fracción de píxeles casi blancos y sin color (brillo especular)
    orientation_resolved: bool = False  # siempre False: el 180° lo decide la etapa de embeddings


def order_quad(pts: np.ndarray) -> np.ndarray:
    """Orden: arriba-izq, arriba-der, abajo-der, abajo-izq."""
    pts = pts.reshape(4, 2).astype(np.float32)
    s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
    return np.array([pts[s.argmin()], pts[d.argmin()], pts[s.argmax()], pts[d.argmax()]], np.float32)


def _side_lengths(q: np.ndarray) -> tuple[float, float, float, float]:
    return tuple(float(np.linalg.norm(q[(i + 1) % 4] - q[i])) for i in range(4))  # arriba, derecha, abajo, izquierda


def _corner_deviation(q: np.ndarray) -> float:
    worst = 0.0
    for i in range(4):
        a, b = q[i - 1] - q[i], q[(i + 1) % 4] - q[i]
        cosang = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
        worst = max(worst, abs(float(np.degrees(np.arccos(np.clip(cosang, -1, 1)))) - 90.0))
    return worst


def _edge_maps(small: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    med = float(np.median(gray))
    lo, hi = int(max(10, 0.66 * med)), int(min(250, 1.33 * med + 20))
    edges = cv2.Canny(gray, lo, max(hi, lo + 30))
    return gray, edges


def _candidate_contours(small: np.ndarray, gray: np.ndarray, edges: np.ndarray) -> list[np.ndarray]:
    k5 = np.ones((5, 5), np.uint8)
    masks = [cv2.morphologyEx(edges, cv2.MORPH_CLOSE, k5, iterations=2)]
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    for m in (otsu, 255 - otsu):
        masks.append(cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_OPEN, k5), cv2.MORPH_CLOSE, k5, iterations=2))
    # distancia de color respecto del fondo estimado en el marco de la imagen
    h, w = small.shape[:2]
    t = max(3, int(0.03 * min(h, w)))
    border = np.concatenate([small[:t].reshape(-1, 3), small[-t:].reshape(-1, 3), small[:, :t].reshape(-1, 3), small[:, -t:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    dist = np.linalg.norm(small.astype(np.float32) - bg.astype(np.float32), axis=2)
    _, fg = cv2.threshold(np.clip(dist, 0, 255).astype(np.uint8), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    masks.append(cv2.morphologyEx(cv2.morphologyEx(fg, cv2.MORPH_OPEN, k5), cv2.MORPH_CLOSE, k5, iterations=2))
    contours: list[np.ndarray] = []
    for m in masks:
        found, _ = cv2.findContours(m, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        contours += sorted(found, key=cv2.contourArea, reverse=True)[:8]
    return contours


def _border_support(q: np.ndarray, edges: np.ndarray) -> float:
    dil = cv2.dilate(edges, np.ones((5, 5), np.uint8))
    h, w = dil.shape
    hits = total = 0
    for i in range(4):
        for t in np.linspace(0.05, 0.95, 24):
            x, y = (q[i] * (1 - t) + q[(i + 1) % 4] * t).astype(int)
            if 0 <= x < w and 0 <= y < h:
                total += 1
                hits += int(dil[y, x] > 0)
    return hits / total if total else 0.0


def _score(contour: np.ndarray, shape: tuple[int, int], edges: np.ndarray) -> tuple[float, np.ndarray, DetectionMetrics] | None:
    h, w = shape
    area = cv2.contourArea(contour)
    if area < MIN_AREA_FRACTION * h * w:
        return None
    hull = cv2.convexHull(contour)
    approx = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True)
    quad = approx.reshape(-1, 2) if len(approx) == 4 else cv2.boxPoints(cv2.minAreaRect(hull))
    q = order_quad(quad)
    top, right, bottom, left = _side_lengths(q)
    if min(top, right, bottom, left) < 20:
        return None
    wd, ht = (top + bottom) / 2, (left + right) / 2
    aspect = min(wd, ht) / max(wd, ht)
    aspect_fit = max(0.0, 1 - abs(aspect - CARD_ASPECT) / CARD_ASPECT)
    if aspect_fit < MIN_ASPECT_FIT:
        return None  # la proporción no es la de una carta (círculos, cuadrados, banderolas...)
    quad_area = max(cv2.contourArea(q), 1.0)
    rect = float(min(1.0, cv2.contourArea(hull) / quad_area, quad_area / max(cv2.contourArea(hull), 1.0)))
    if rect < MIN_RECTANGULARITY:
        return None
    dev = _corner_deviation(q)
    if dev > MAX_CORNER_DEVIATION:
        return None
    skew = max(max(top, bottom) / max(min(top, bottom), 1e-6), max(left, right) / max(min(left, right), 1e-6)) - 1
    support = _border_support(q, edges)
    frac = quad_area / (h * w)
    touches = bool(np.any(q[:, 0] < EDGE_MARGIN * w) or np.any(q[:, 0] > (1 - EDGE_MARGIN) * w) or np.any(q[:, 1] < EDGE_MARGIN * h) or np.any(q[:, 1] > (1 - EDGE_MARGIN) * h))
    area_s = min(1.0, frac / 0.15) * (0.6 if frac > 0.97 else 1.0)  # una carta que es TODA la imagen es sospechosa
    angle_s = max(0.0, 1 - dev / 35.0)
    # media geométrica ponderada: una evidencia muy mala hunde la confianza
    conf = (area_s**0.8) * (aspect_fit**1.6) * (rect**1.0) * (angle_s**1.0) * ((0.25 + 0.75 * support) ** 1.2)
    conf = float(conf ** (1 / 5.6))
    if touches:
        conf *= 0.75
    m = DetectionMetrics(round(frac, 4), round(aspect, 4), round(aspect_fit, 4), round(rect, 4), round(dev, 2), round(skew, 4), round(support, 4), touches, 1)
    return conf, q, m


def _iou(a: np.ndarray, b: np.ndarray, shape: tuple[int, int]) -> float:
    ma, mb = np.zeros(shape, np.uint8), np.zeros(shape, np.uint8)
    cv2.fillConvexPoly(ma, a.astype(np.int32), 1)
    cv2.fillConvexPoly(mb, b.astype(np.int32), 1)
    inter, union = int((ma & mb).sum()), int((ma | mb).sum())
    return inter / union if union else 0.0


class CardDetector:
    def __init__(self, min_confidence: float = DEFAULT_MIN_CONFIDENCE):
        self.min_confidence = min_confidence

    def detect(self, bgr: np.ndarray) -> Detection:
        if bgr is None or bgr.ndim != 3 or bgr.shape[2] != 3 or min(bgr.shape[:2]) < 32:
            return Detection(False, 0.0, None, None, "imagen inválida o demasiado pequeña")
        scale = WORK_SIDE / max(bgr.shape[:2])
        small = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else bgr
        scale = min(scale, 1.0)
        gray, edges = _edge_maps(small)
        scored = []
        for c in _candidate_contours(small, gray, edges):
            r = _score(c, small.shape[:2], edges)
            if r:
                scored.append(r)
        if not scored:
            return Detection(False, 0.0, None, None, "no hay ningún cuadrilátero candidato")
        scored.sort(key=lambda r: r[0], reverse=True)
        conf, q, metrics = scored[0]
        distinct = [q]
        for c2, q2, _ in scored[1:]:
            if c2 >= 0.85 * conf and all(_iou(q2, d, small.shape[:2]) < 0.6 for d in distinct):
                distinct.append(q2)
        metrics = replace(metrics, strong_candidates=len(distinct))
        warnings = []
        if metrics.touches_image_border:
            warnings.append("la carta toca el borde de la imagen: esquinas poco fiables")
        if len(distinct) > 1:
            warnings.append(f"{len(distinct)} candidatos con confianza parecida (varias cartas u objetos)")
        if metrics.perspective_skew > 0.45:
            warnings.append("perspectiva muy fuerte")
        conf = round(min(1.0, conf), 4)
        if conf < self.min_confidence:
            return Detection(False, conf, None, metrics, f"confianza de forma {conf:.2f} por debajo del mínimo {self.min_confidence:.2f}", tuple(warnings))
        return Detection(True, conf, (q / scale).astype(np.float32), metrics, None, tuple(warnings))

    @staticmethod
    def rectify(bgr: np.ndarray, detection: Detection) -> RectifiedCard:
        if not detection.found or detection.quad is None:
            raise ValueError("no hay carta que rectificar (detección rechazada)")
        q = order_quad(detection.quad)
        top, right, bottom, left = _side_lengths(q)
        if (top + bottom) / 2 > (left + right) / 2:  # carta tumbada: girar el orden de vértices 90°
            q = np.roll(q, -1, axis=0)
        dst = np.array([[0, 0], [CANON_W - 1, 0], [CANON_W - 1, CANON_H - 1], [0, CANON_H - 1]], np.float32)
        card = cv2.warpPerspective(bgr, cv2.getPerspectiveTransform(q, dst), (CANON_W, CANON_H), flags=cv2.INTER_AREA)
        gray = cv2.cvtColor(card, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(card, cv2.COLOR_BGR2HSV)
        glare = float(np.mean((hsv[..., 2] > 245) & (hsv[..., 1] < 40)))
        return RectifiedCard(card, cv2.rotate(card, cv2.ROTATE_180), float(cv2.Laplacian(gray, cv2.CV_64F).var()), float(gray.mean()), float(gray.std()), round(glare, 4))


def corner_error(pred: np.ndarray, truth: np.ndarray) -> float:
    """Error medio de esquinas relativo al alto de la carta (ambos 4x2 en el mismo orden)."""
    t = order_quad(truth)
    height = (np.linalg.norm(t[3] - t[0]) + np.linalg.norm(t[2] - t[1])) / 2
    return float(np.mean(np.linalg.norm(order_quad(pred) - t, axis=1)) / max(height, 1e-6))
