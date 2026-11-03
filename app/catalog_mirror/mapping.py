# ruff: noqa: E501
"""Normalización de las respuestas de la API de Carpetazo y cálculo de digests. No se inventa ningún campo: lo que falta queda en null."""

import hashlib
import json
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

PLACEHOLDER_SUFFIX = "Sin_Imagen/Carpetazo_TCG_Sin_Imagen.webp"
TCGPLAYER_CDN = "tcgplayer-cdn.tcgplayer.com"
TCGDEX_HOST = "assets.tcgdex.net"


class InvalidRecord(ValueError):
    pass


def digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _int(value, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int | str) or not str(value).strip().isdigit() or int(value) <= 0:
        raise InvalidRecord(f"{what} inválido: {value!r}")
    return int(value)


def _text(value, limit: int) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s[:limit] if s else None


def classify_image(url: str | None) -> tuple[str, str | None]:
    """(origen, clave). Solo se CLASIFICA la URL entregada por la API; nunca se visita."""
    if not url:
        return "none", None
    p = urlparse(url)
    path = unquote(p.path).lstrip("/")
    if path.endswith(PLACEHOLDER_SUFFIX):
        return "placeholder", None
    if p.hostname == TCGPLAYER_CDN:
        return "tcgplayer_cdn", None
    if p.hostname == TCGDEX_HOST:
        return "tcgdex", None  # respaldo de imagen que entrega Carpetazo; no es una fuente de catálogo ni de identidad
    if path.startswith("Carpetazo.cl/"):
        return "carpetazo_r2", path
    return "other", None


@dataclass(frozen=True)
class SetRecord:
    category_id: int
    group_id: int
    name: str
    abbreviation: str | None
    published_on: str | None
    modified_on: str | None
    digest: str = field(default="")


@dataclass(frozen=True)
class CardRecord:
    category_id: int
    product_id: int
    group_id: int
    name: str
    number: str | None
    rarity: str | None
    language: str | None
    source_category_id: int | None
    game_name: str | None
    extended_data: list
    tcgplayer_product_id: int | None
    image_url: str | None
    image_origin: str
    image_key: str | None
    has_image: bool
    digest: str
    group: dict


def set_record(category_id: int, row: dict) -> SetRecord:
    group_id = _int(row.get("groupId"), "groupId")
    name = _text(row.get("name"), 400)
    if not name:
        raise InvalidRecord(f"edición {group_id} sin nombre")
    rec = dict(category_id=category_id, group_id=group_id, name=name, abbreviation=_text(row.get("abbreviation"), 40), published_on=_text(row.get("publishedOn"), 40), modified_on=_text(row.get("modifiedOn"), 40))
    return SetRecord(**rec, digest=digest(rec))


def card_record(category_id: int, row: dict) -> CardRecord:
    product_id, group_id = _int(row.get("productId"), "productId"), _int(row.get("groupId"), "groupId")
    name = _text(row.get("name"), 1000)
    if not name:
        raise InvalidRecord(f"producto {product_id} sin nombre")
    source = _int(row["categoryId"], "categoryId") if row.get("categoryId") is not None else None  # en la categoría 100 la API devuelve productos ingleses con categoryId 3
    ext = row.get("extendedData") if isinstance(row.get("extendedData"), list) else []
    ext = sorted(({"name": str(i["name"]), "value": str(i.get("value", ""))} for i in ext if isinstance(i, dict) and i.get("name")), key=lambda i: (i["name"], i["value"]))
    by_name = {i["name"]: i["value"] for i in ext}
    tp = by_name.get("TcgplayerProductId")
    lang = _text(by_name.get("Language"), 10)
    image_url = _text(row.get("imageUrl"), 2000)
    origin, key = classify_image(image_url)
    rec = dict(
        category_id=category_id, product_id=product_id, group_id=group_id, name=name, number=_text(row.get("number"), 40), rarity=_text(row.get("rarity"), 80),
        language=lang.lower() if lang else None, game_name=_text(row.get("game"), 200), extended_data=ext,
        tcgplayer_product_id=int(tp) if tp and tp.isdigit() and int(tp) > 0 else None,
        image_url=image_url, image_origin=origin, image_key=key, has_image=bool(row.get("hasImage")), source_category_id=source,
    )  # fmt: skip
    group = row.get("group") if isinstance(row.get("group"), dict) else {}
    return CardRecord(**rec, digest=digest(rec), group=group)


def relation_of(category_id: int, source_category_id: int | None) -> str | None:
    """Relación DERIVADA de lo que entrega la API (categoryId de origen de la fila); null si no se puede afirmar."""
    if category_id != 100:
        return "catalog_product"
    return {3: "spanish_view_of_english_product", 100: "spanish_only"}.get(source_category_id)


def listing_digest(records: list[CardRecord]) -> str:
    return digest({"cards": sorted((r.product_id, r.digest) for r in records)})
