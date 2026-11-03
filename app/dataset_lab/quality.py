# ruff: noqa: E501
"""Validación de calidad de una fotografía (solo lectura del archivo original)."""

import hashlib
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from app.recognition_lab.audit import dhash, hamming

ALLOWED_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
# Umbrales PROVISIONALES (no calibrados con fotos reales); se ajustan tras el piloto.
MIN_SHORT_SIDE = 720
RECOMMENDED_SHORT_SIDE = 1080
SHARP_BAD, SHARP_OK = 60.0, 150.0  # varianza del laplaciano a ≤ 800 px
DARK_MEAN, BRIGHT_MEAN = 40.0, 215.0
GLARE_FRACTION = 0.03
NEAR_DUPLICATE_BITS = 4
BLOCKING_FLAGS = {"resolucion_baja", "borrosa", "muy_oscura", "sobreexpuesta"}

_EXIF_ORIENTATION, _EXIF_MAKE, _EXIF_MODEL, _EXIF_DATETIME, _EXIF_IFD, _EXIF_GPS = 274, 271, 272, 306, 0x8769, 0x8825


class UnsupportedImage(Exception):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def analyze(path: Path) -> dict:
    """Metadatos técnicos y banderas de calidad. Lanza UnsupportedImage si no se puede leer o el formato no se admite."""
    try:
        with Image.open(path) as im:
            fmt = im.format
            if fmt not in ALLOWED_FORMATS:
                raise UnsupportedImage(f"formato {fmt} no admitido (use JPEG, PNG o WebP; HEIC no se admite)")
            exif = im.getexif()
            gps = bool(exif.get_ifd(_EXIF_GPS)) if exif else False
            exif_ifd = exif.get_ifd(_EXIF_IFD) if exif else {}
            make, model = (exif.get(_EXIF_MAKE) or "").strip(), (exif.get(_EXIF_MODEL) or "").strip()
            oriented = ImageOps.exif_transpose(im).convert("RGB")
    except UnidentifiedImageError as exc:
        raise UnsupportedImage("archivo no reconocido como imagen") from exc
    except OSError as exc:
        raise UnsupportedImage(f"imagen dañada: {exc}") from exc

    w, h = oriented.size
    arr = np.asarray(oriented)
    scale = 800 / max(w, h)
    small = cv2.resize(arr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else arr
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    mean = float(gray.mean())
    gh, gw = small.shape[:2]
    centre = small[int(gh * 0.2) : int(gh * 0.8), int(gw * 0.2) : int(gw * 0.8)]
    glare = float((centre.min(axis=2) >= 250).mean())

    flags = []
    if min(w, h) < MIN_SHORT_SIDE:
        flags.append("resolucion_baja")
    elif min(w, h) < RECOMMENDED_SHORT_SIDE:
        flags.append("resolucion_justa")
    if sharp < SHARP_BAD:
        flags.append("borrosa")
    if mean < DARK_MEAN:
        flags.append("muy_oscura")
    if mean > BRIGHT_MEAN:
        flags.append("sobreexpuesta")
    if glare > GLARE_FRACTION:
        flags.append("posible_reflejo")
    if gps:
        flags.append("contiene_gps")
    return {
        "format": fmt, "width": w, "height": h, "megapixels": round(w * h / 1e6, 2), "sharpness": round(sharp, 1),
        "brightness": round(mean, 1), "glare_fraction": round(glare, 4), "dhash": f"{dhash(gray):016x}", "flags": flags,
        "focus_auto": "borrosa" if sharp < SHARP_BAD else ("aceptable" if sharp < SHARP_OK else "buena"),
        "exif": {"orientation": exif.get(_EXIF_ORIENTATION) if exif else None, "datetime": (exif_ifd.get(36867) or (exif.get(_EXIF_DATETIME) if exif else None)),
                 "device": f"{make} {model}".strip() or None, "has_gps": gps},
    }  # fmt: skip


def near_duplicate(a_hex: str, b_hex: str) -> bool:
    return hamming(int(a_hex, 16), int(b_hex, 16)) <= NEAR_DUPLICATE_BITS
