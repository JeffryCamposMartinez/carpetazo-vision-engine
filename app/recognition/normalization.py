# ruff: noqa: E501
"""E2: normalización de la carta para reconocimiento. La imagen original NUNCA se modifica.

Cada resultado conserva (1) la foto original sin tocar (vista de solo lectura + SHA-256), (2) la carta rectificada «cruda» (solo geometría,
sin cambios fotométricos) y (3) la carta normalizada, más el registro de cada transformación con su efecto medido. Así `raw` y `normalized`
se pueden comparar con las mismas fotos (ver `normalization_compare.py`); ninguna mejora se da por supuesta.

Principios sobre el color: la iluminación se corrige SOLO en la luminancia (canal L de Lab); los canales de color (a, b) no se tocan. No se
rellenan reflejos (no se inventa contenido): se reporta una máscara de brillo especular y solo se comprime suavemente el rango muy claro.
Así el aspecto de cartas Holo y Reverse Holo no se altera artificialmente. Valores por defecto = hipótesis a validar con fotos reales.
"""

import hashlib
from dataclasses import dataclass, field, replace

import cv2
import numpy as np

from app.recognition.detection import CANON_H, CANON_W, CardDetector, Detection, RectifiedCard, order_quad

NORMALIZATION_VERSION = "n1"


@dataclass(frozen=True)
class NormalizationConfig:
    refine_corners: bool = False  # off por defecto: en escenas limpias sintéticas empeora ~1,5 px; con fondos complejos mejora (docs 08)
    border_crop: float = 0.02  # fracción de cada lado que se recorta (funda, canto, fondo)
    illumination: bool = True
    illumination_sigma: float = 0.25  # desviación del desenfoque que estima la iluminación, como fracción del ancho
    illumination_strength: float = 0.5  # 0 = nada, 1 = aplanado total (el arte también tiene variaciones de luz propias)
    highlight_compression: bool = True
    highlight_knee: int = 235  # por encima de este valor de L se comprime a la mitad

    @property
    def config_id(self) -> str:
        return hashlib.sha256(repr(self).encode()).hexdigest()[:8]

    @property
    def tag(self) -> str:
        return f"{NORMALIZATION_VERSION}-{self.config_id}"


RAW_CONFIG = NormalizationConfig(refine_corners=False, border_crop=0.0, illumination=False, highlight_compression=False)


@dataclass(frozen=True)
class TransformRecord:
    name: str
    params: dict
    effect: dict


@dataclass(frozen=True)
class NormalizationResult:
    original: np.ndarray  # la foto de entrada, vista de solo lectura (nunca modificada)
    original_sha256: str
    quad: np.ndarray  # esquinas usadas, en coordenadas de la foto original
    quad_refined: bool
    rectified_raw: RectifiedCard  # solo geometría (perspectiva y 90°)
    normalized: np.ndarray  # BGR 440x614, versión normalizada (orientación 0°)
    glare_mask: np.ndarray  # uint8 440x614: 255 donde hay brillo especular (informativo; no se rellena)
    transforms: tuple[TransformRecord, ...]
    config: NormalizationConfig
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def normalized_flipped(self) -> np.ndarray:
        return cv2.rotate(self.normalized, cv2.ROTATE_180)  # las operaciones son equivariantes a la rotación de 180°

    def variants(self) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        """Pares (0°, 180°) por variante, listos para comparar `raw` frente a `normalized` con las mismas fotos."""
        return {"raw": (self.rectified_raw.upright, self.rectified_raw.flipped), "normalized": (self.normalized, self.normalized_flipped)}


def refine_quad(bgr: np.ndarray, quad: np.ndarray, band: float = 0.03, max_shift: float = 0.025) -> tuple[np.ndarray, bool, float]:
    """Ajusta cada lado a una recta usando los bordes de la imagen a su alrededor y recalcula las esquinas como intersecciones.

    Solo acepta el ajuste si cada esquina se mueve menos de `max_shift` del alto de la carta; si no, devuelve el cuadrilátero original.
    """
    q = order_quad(quad)
    scale = 1600 / max(bgr.shape[:2])
    img = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else bgr
    s = min(scale, 1.0)
    qs = q * s
    gray = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (3, 3), 0)
    med = float(np.median(gray))
    edges = cv2.Canny(gray, int(max(10, 0.66 * med)), int(min(250, 1.33 * med + 30)))
    ys, xs = np.nonzero(edges)
    pts = np.stack([xs, ys], 1).astype(np.float32)
    height = float((np.linalg.norm(qs[3] - qs[0]) + np.linalg.norm(qs[2] - qs[1])) / 2)
    lines = []
    for i in range(4):
        a, b = qs[i], qs[(i + 1) % 4]
        d = b - a
        length = float(np.linalg.norm(d))
        d /= max(length, 1e-6)
        n = np.array([-d[1], d[0]], np.float32)
        rel = pts - a
        t, dist = rel @ d, rel @ n
        sel = (np.abs(dist) < band * height) & (t > 0.1 * length) & (t < 0.9 * length)
        if int(sel.sum()) < 30:
            return quad, False, 0.0
        vx, vy, x0, y0 = cv2.fitLine(pts[sel], cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        lines.append((np.array([x0, y0], np.float32), np.array([vx, vy], np.float32)))
    out = []
    for i in range(4):
        (p1, d1), (p2, d2) = lines[i - 1], lines[i]  # esquina i = intersección del lado i-1 con el lado i
        m = np.array([[d1[0], -d2[0]], [d1[1], -d2[1]]], np.float32)
        if abs(np.linalg.det(m)) < 1e-4:
            return quad, False, 0.0
        t = np.linalg.solve(m, p2 - p1)
        out.append(p1 + d1 * t[0])
    refined = np.array(out, np.float32)
    shifts = np.linalg.norm(refined - qs, axis=1) / max(height, 1e-6)
    if float(shifts.max()) > max_shift:
        return quad, False, 0.0
    return (refined / s).astype(np.float32), True, float(shifts.mean() * height / s)


def _lab(bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)


def glare_mask(card_bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(card_bgr, cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 2] > 245) & (hsv[..., 1] < 40)).astype(np.uint8) * 255
    return cv2.dilate(m, np.ones((5, 5), np.uint8))


def normalize_canonical(card_bgr: np.ndarray, cfg: NormalizationConfig) -> tuple[np.ndarray, list[TransformRecord]]:
    """Normaliza una carta YA rectificada (440x614). Se usa igual para fotos y para imágenes de referencia, para que sean comparables."""
    if card_bgr.shape != (CANON_H, CANON_W, 3) or card_bgr.dtype != np.uint8:
        raise ValueError("se espera una carta rectificada BGR uint8 de 614x440")
    work = card_bgr.copy()  # nunca se escribe sobre la entrada
    records: list[TransformRecord] = []
    if cfg.border_crop > 0:
        cx, cy = int(round(CANON_W * cfg.border_crop)), int(round(CANON_H * cfg.border_crop))
        work = cv2.resize(work[cy : CANON_H - cy, cx : CANON_W - cx], (CANON_W, CANON_H), interpolation=cv2.INTER_AREA)
        records.append(TransformRecord("border_crop", {"fraction": cfg.border_crop, "pixels_x": cx, "pixels_y": cy}, {}))
    if cfg.illumination or cfg.highlight_compression:
        lab = _lab(work)
        L = lab[..., 0].astype(np.float32)
        ab_before = lab[..., 1:].copy()
        before_mean, before_std = float(L.mean()), float(L.std())
        if cfg.illumination and cfg.illumination_strength > 0:
            sigma = cfg.illumination_sigma * CANON_W
            illum = cv2.GaussianBlur(L, (0, 0), sigma)
            ratio = np.clip(float(illum.mean()) / np.maximum(illum, 1.0), 0.6, 1.6)
            L = np.clip(L * ratio**cfg.illumination_strength, 0, 255)
            records.append(TransformRecord("illumination_flatten", {"channel": "L", "sigma_fraction": cfg.illumination_sigma, "strength": cfg.illumination_strength, "ratio_clip": [0.6, 1.6]},
                                           {"mean_before": round(before_mean, 2), "mean_after": round(float(L.mean()), 2), "std_before": round(before_std, 2), "std_after": round(float(L.std()), 2)}))  # fmt: skip
        if cfg.highlight_compression:
            knee = float(cfg.highlight_knee)
            over = L > knee
            frac = float(over.mean())
            L = np.where(over, knee + (L - knee) * 0.5, L)
            records.append(TransformRecord("highlight_compression", {"channel": "L", "knee": cfg.highlight_knee, "slope_above_knee": 0.5, "inpainting": False}, {"fraction_of_pixels": round(frac, 4)}))
        lab[..., 0] = np.clip(np.round(L), 0, 255).astype(np.uint8)
        assert np.array_equal(lab[..., 1:], ab_before)  # el color (a, b) no se toca
        work = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    return work, records


class Normalizer:
    def __init__(self, config: NormalizationConfig | None = None, detector: CardDetector | None = None):
        self.config = config or NormalizationConfig()
        self.detector = detector or CardDetector()

    def process(self, bgr: np.ndarray, detection: Detection | None = None) -> NormalizationResult:
        """Normaliza una fotografía. `bgr` no se modifica (se verifica con SHA-256 en las pruebas)."""
        original = bgr.view()
        original.flags.writeable = False
        digest = hashlib.sha256(np.ascontiguousarray(bgr).tobytes()).hexdigest()
        det = detection or self.detector.detect(bgr)
        if not det.found or det.quad is None:
            raise ValueError("no hay carta que normalizar (detección rechazada: not_a_card)")
        records: list[TransformRecord] = []
        warnings = list(det.warnings)
        quad, refined = det.quad, False
        if self.config.refine_corners:
            new_quad, refined, shift = refine_quad(bgr, det.quad)
            records.append(TransformRecord("corner_refinement", {"method": "line_fit_huber"}, {"applied": refined, "mean_shift_px": round(shift, 2)}))
            if refined:
                quad = new_quad
        raw = self.detector.rectify(bgr, det)  # «crudo»: esquinas del detector, sin refinar ni cambios fotométricos
        geometry = self.detector.rectify(bgr, replace(det, quad=quad)) if refined else raw
        records.append(TransformRecord("perspective_warp", {"canvas": [CANON_W, CANON_H], "rotation_90": "automatic", "rotation_180": "unresolved (both candidates kept)"}, {"detection_confidence": det.confidence}))
        normalized, more = normalize_canonical(geometry.upright, self.config)
        records += more
        return NormalizationResult(original, digest, quad, refined, raw, normalized, glare_mask(geometry.upright), tuple(records), self.config, tuple(warnings))
