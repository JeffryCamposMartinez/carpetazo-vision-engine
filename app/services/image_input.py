"""Validación de imágenes entrantes: formato real, tamaño y decodificación."""

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import Settings


class ImageRejected(Exception):
    def __init__(self, code: str, detail: str, http_status: int):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = http_status


@dataclass(frozen=True)
class DecodedImage:
    bgr: np.ndarray
    mime: str
    width: int
    height: int


def sniff_mime(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def decode_image(data: bytes, settings: Settings) -> DecodedImage:
    if not data:
        raise ImageRejected("empty_image", "La imagen está vacía", 422)
    if len(data) > settings.max_image_bytes:
        raise ImageRejected("image_too_large", "La imagen supera el tamaño máximo", 413)

    mime = sniff_mime(data)
    if mime is None:
        raise ImageRejected("unsupported_media_type", "Formato no soportado (JPEG, PNG o WebP)", 415)

    try:
        with Image.open(io.BytesIO(data)) as probe:
            width, height = probe.size  # solo cabecera: permite rechazar bombas de descompresión
            if width * height > settings.max_image_pixels:
                raise ImageRejected("image_dimensions", "La imagen tiene demasiados píxeles", 422)
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
    except ImageRejected:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise ImageRejected("corrupt_image", "La imagen está corrupta o no se pudo decodificar", 422) from None

    width, height = img.size
    if min(width, height) < settings.min_image_side or max(width, height) > settings.max_image_side:
        raise ImageRejected(
            "image_dimensions",
            f"Dimensiones fuera de rango ({settings.min_image_side}-{settings.max_image_side} px)",
            422,
        )
    bgr = np.asarray(img)[:, :, ::-1].copy()
    return DecodedImage(bgr=bgr, mime=mime, width=width, height=height)
