"""Esquema de anotación del dataset de fotografías reales. Los campos de identidad admiten None (= desconocido)."""

import re
import uuid

SCHEMA_VERSION = 1

LIGHTING = ("natural_directa", "natural_sombra", "interior_calida", "interior_fria", "flash", "mixta", "desconocida")
ANGLE = ("frontal", "inclinada_leve", "inclinada_media", "inclinada_fuerte", "cenital", "desconocido")
BACKGROUND = (
    "neutro_claro",
    "neutro_oscuro",
    "madera",
    "mantel_estampado",
    "alfombrilla",
    "mano",
    "otro",
    "desconocido",
)
SLEEVE = ("sin_funda", "funda_transparente", "funda_mate", "toploader", "desconocida")
FOCUS = ("buena", "aceptable", "borrosa", "desconocida")
GLARE = ("ninguno", "leve", "fuerte", "desconocido")
PHOTO_STATUS = ("pending", "verified", "rejected", "needs_review")
CARD_STATUS = ("unverified", "verified")
FINISH = ("normal", "holo", "reverse_holo", "full_art", "promo", "otro", "desconocido")
CATEGORY = ("comun", "full_art", "holo", "reverse_holo", "promo", "antigua", "variante_patron", "arte_similar", "otra")

ENUMS = {
    "lighting_condition": LIGHTING, "camera_angle": ANGLE, "background": BACKGROUND, "sleeve_condition": SLEEVE,
    "focus_quality": FOCUS, "glare": GLARE, "verification_status": PHOTO_STATUS,
}  # fmt: skip
CONDITION_FIELDS = ("lighting_condition", "camera_angle", "background", "sleeve_condition", "glare")
IDENTITY_FIELDS = ("card_id", "printing_id", "variant_id")
_LANG = re.compile(r"^(unknown|[a-z]{2}(-[A-Za-z]{2,4})?)$")


def is_uuid(value: object) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


def validate_photo(p: dict) -> list[str]:
    errors = []
    for field in ("image_id", "capture_session_id", "file"):
        if not p.get(field):
            errors.append(f"falta {field}")
    for field, allowed in ENUMS.items():
        if p.get(field) not in allowed:
            errors.append(f"{field}={p.get(field)!r} no es uno de {allowed}")
    for field in IDENTITY_FIELDS:
        if p.get(field) is not None and not is_uuid(p[field]):
            errors.append(f"{field} debe ser null o un UUID del catálogo (no se inventan ids)")
    f = p.get("file") or {}
    for field in ("path", "sha256", "width", "height"):
        if f.get(field) in (None, ""):
            errors.append(f"file.{field} vacío")
    if p.get("verification_status") == "verified":
        if not p.get("verified_by"):
            errors.append("una foto verified necesita verified_by")
        if not p.get("card_ref"):
            errors.append("una foto verified necesita card_ref")
    corners = p.get("card_corners")
    if corners is not None and not (
        isinstance(corners, list) and len(corners) == 4 and all(len(c) == 2 for c in corners)
    ):
        errors.append("card_corners debe ser null o 4 puntos [x, y] (arriba-izq, arriba-der, abajo-der, abajo-izq)")
    return errors


def validate_card(c: dict) -> list[str]:
    errors = []
    for field in ("card_ref", "name", "set_name", "collector_number"):
        if not c.get(field):
            errors.append(f"falta {field}")
    if c.get("verification_status") not in CARD_STATUS:
        errors.append(f"verification_status={c.get('verification_status')!r} inválido")
    if c.get("finish") not in FINISH:
        errors.append(f"finish={c.get('finish')!r} inválido")
    if c.get("category") not in CATEGORY:
        errors.append(f"category={c.get('category')!r} inválida")
    if not _LANG.match(str(c.get("language", ""))):
        errors.append(f"language={c.get('language')!r} inválido (use 'unknown' o un código como 'en')")
    for field in IDENTITY_FIELDS:
        if c.get(field) is not None and not is_uuid(c[field]):
            errors.append(f"{field} debe ser null o un UUID del catálogo (no se inventan ids)")
    if c.get("verification_status") == "verified":
        for field in ("verified_by", "verified_at", "verification_note"):
            if not c.get(field):
                errors.append(f"una carta verified necesita {field}")
    return errors
