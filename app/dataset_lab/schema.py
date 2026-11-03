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
# Categorías del piloto (solapables: una carta puede pertenecer a varias). Objetivo inicial: >= 5 cartas por categoría.
PILOT_CATEGORIES = ("comun", "holo", "reverse_holo", "full_art", "promo", "similar_dificil")
EXTRA_CATEGORIES = ("antigua", "variante_patron", "otra")
CATEGORY = PILOT_CATEGORIES + EXTRA_CATEGORIES
PATTERN = ("ninguno", "pokeball", "masterball", "desconocido")
ROUNDS = ("A", "B")  # A = calibración, B = evaluación final

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
    cats = c.get("categories")
    if not isinstance(cats, list) or not cats or any(x not in CATEGORY for x in cats):
        errors.append(f"categories={cats!r} inválido (lista no vacía con valores de {CATEGORY})")
    if c.get("pattern") not in PATTERN:
        errors.append(f"pattern={c.get('pattern')!r} inválido ({PATTERN})")
    if c.get("reference_verified_by") and not c.get("reference_object_key"):
        errors.append("reference_verified_by sin reference_object_key")
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


def validate_session(sess: dict) -> list[str]:
    errors = []
    if sess.get("round") not in (None, *ROUNDS):
        errors.append(f"round={sess.get('round')!r} inválida (use {ROUNDS} o null)")
    return errors
