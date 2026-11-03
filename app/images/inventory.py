"""Inventario de imágenes Pokémon en R2: parseo de keys, números y data.json de TCGplayer/tcgcsv."""

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field

from app.storage.base import ObjectInfo, ObjectStorage

DEFAULT_PREFIX = "Carpetazo.cl/Pokemon/Pokemon_DB/"
CARD_IMAGES = "Pokemon_Images"
DATA_DIR = "Pokemon_Data"

_ALT_NUMBER = re.compile(r"^(?P<name>.*) - (?P<alt>[A-Za-z0-9]+_[A-Za-z0-9]+)$")
_LOCAL_TOTAL = re.compile(r"^(?P<local>[A-Za-z0-9]+)-(?P<total>[A-Za-z0-9]+)$")


@dataclass(frozen=True)
class NumberRules:
    """Cómo se comparan números de colección. Configurable porque '058' y '58' pueden ser el mismo número."""

    strip_leading_zeros: bool = True
    case_insensitive: bool = True


def normalize_number(token: str, rules: NumberRules = NumberRules()) -> str:
    out = unicodedata.normalize("NFKC", token).strip()
    if rules.case_insensitive:
        out = out.upper()
    if rules.strip_leading_zeros:
        out = re.sub(r"^0+(?=\d)", "", out)
        out = re.sub(r"(?<=[A-Z])0+(?=\d)", "", out)
    return out


def normalize_name(name: str) -> str:
    """Conservador: Unicode NFKC, mayúsculas/minúsculas y espacios. No quita sufijos como EX, V o Alolan."""
    text = unicodedata.normalize("NFKC", name).replace("’", "'").replace("‘", "'").replace("´", "'")
    return " ".join(text.casefold().split())


_NUM = r"#?[A-Za-z]{0,6}\d+[A-Za-z]?"
_NUMBER_LIKE = re.compile(rf"^{_NUM}(?:-{_NUM})?$")
_TRAIL_BRACKET = re.compile(r"\s*\[([^\]]*)\]$")
_TRAIL_PAREN = re.compile(r"\s*\(([^()]*)\)$")
_UNTERMINATED = re.compile(r"\s*[(\[]([^()\[\]]*)$")  # calificador truncado: 'Audino (Master Ball Patte'
_SUFFIX_NUMBER = re.compile(rf"^(?P<name>.*?) - (?P<num>{_NUM})$")


def parse_card_folder(folder: str) -> dict:
    """'Aegislash EX - 65a_119 (065a-119)' -> nombre, número crudo, calificadores y cómo se interpretó.

    Los paréntesis finales que no parecen un número (Prerelease, Master Ball Pattern, [Staff]) son
    calificadores de variante, no parte del nombre ni del número.
    """
    s, quals, raw, alts = folder.strip(), [], None, []
    while True:
        if m := _TRAIL_BRACKET.search(s):
            quals.insert(0, m.group(1).strip())
        elif m := _TRAIL_PAREN.search(s):
            tok = m.group(1).strip()
            if _NUMBER_LIKE.match(tok):
                if raw is None:
                    raw = tok
                else:
                    alts.append(tok)
            else:
                quals.insert(0, tok)
        elif m := _UNTERMINATED.search(s):
            quals.insert(0, m.group(1).strip())
        else:
            break
        s = s[: m.start()]

    how = "token único"
    if raw is not None:
        alt = _ALT_NUMBER.match(s)
        name = alt.group("name") if alt else s
        alt_in_name = alt.group("alt") if alt else None
        lt = _LOCAL_TOTAL.match(raw)
        local, total = (lt.group("local"), lt.group("total")) if lt else (raw, None)
        if lt:
            how = "local-total (separado por '-')"
    elif alt := _ALT_NUMBER.match(s):
        name, alt_in_name = alt.group("name"), alt.group("alt")
        local, _, total = alt_in_name.partition("_")
        raw, how = alt_in_name, "'a_b' del nombre como a/b"
    elif suf := _SUFFIX_NUMBER.match(s):
        name, alt_in_name, local, total = suf.group("name"), None, suf.group("num"), None
        raw, how = suf.group("num"), "sufijo ' - número' en el nombre"
    else:
        return {
            "name": s.strip(), "number_raw": None, "local": None, "total": None, "interpretation": "sin_numero",
            "alt_number_in_name": None, "qualifiers": quals,
        }  # fmt: skip
    return {
        "name": name.strip(), "number_raw": raw, "local": local.lstrip("#") if local else None,
        "total": total, "interpretation": how, "alt_number_in_name": alt_in_name or (alts[0] if alts else None),
        "qualifiers": quals,
    }  # fmt: skip


@dataclass
class InventoryRow:
    object_key: str
    kind: str  # card | excluded
    exclusion_reason: str | None
    set_folder: str | None
    card_folder: str | None
    filename: str | None
    extension: str | None
    etag: str | None
    size: int
    parsed: dict = field(default_factory=dict)


def classify(obj: ObjectInfo, prefix: str = DEFAULT_PREFIX) -> InventoryRow | None:
    """None si el objeto es data.json (se procesa aparte)."""
    rel = obj.key[len(prefix) :] if obj.key.startswith(prefix) else obj.key
    parts = rel.split("/")
    ext = parts[-1].rpartition(".")[2].lower() if "." in parts[-1] else None
    base = dict(object_key=obj.key, etag=obj.etag, size=obj.size, extension=ext)
    if parts[0] == DATA_DIR:
        return None
    if parts[0] != CARD_IMAGES:
        reason = "producto sellado" if "Productos" in parts[0] else "miniatura o carpeta no-carta"
        return InventoryRow(
            **base, kind="excluded", exclusion_reason=reason, set_folder=None, card_folder=None, filename=None
        )
    if len(parts) != 4 or ext != "webp":
        return InventoryRow(
            **base,
            kind="excluded",
            exclusion_reason="estructura inesperada",
            set_folder=None,
            card_folder=None,
            filename=parts[-1],
        )
    return InventoryRow(
        **base,
        kind="card",
        exclusion_reason=None,
        set_folder=parts[1],
        card_folder=parts[2],
        filename=parts[3],
        parsed=parse_card_folder(parts[2]),
    )


def build_inventory(objects, prefix: str = DEFAULT_PREFIX) -> tuple[list[InventoryRow], list[str]]:
    rows, data_keys = [], []
    for obj in objects:
        row = classify(obj, prefix)
        if row is None:
            if obj.key.endswith("data.json"):
                data_keys.append(obj.key)
        else:
            rows.append(row)
    return rows, data_keys


def load_data_jsons(
    storage: ObjectStorage, keys: list[str], prefix: str = DEFAULT_PREFIX
) -> dict[tuple[str, str], dict]:
    """Descarga solo los data.json (pequeños). Clave: (carpeta de set normalizada, carpeta de carta)."""
    out: dict[tuple[str, str], dict] = {}
    for key in keys:
        parts = key[len(prefix) :].split("/")
        if len(parts) != 4:
            continue
        data = json.loads(storage.get(key, 500_000).decode("utf-8"))
        out[(normalize_set_folder(parts[1]), parts[2])] = _slim(data, key)
    return out


def normalize_set_folder(folder: str) -> str:
    return " ".join(re.sub(r"[^0-9a-z]+", " ", unicodedata.normalize("NFKD", folder).casefold()).split())


def _slim(data: dict, key: str) -> dict:
    s = data.get("set") or {}
    return {
        "key": key,
        "source": data.get("source"),
        "productId": data.get("productId"),
        "name": data.get("name"),
        "number": data.get("number"),
        "rarity": data.get("rarity"),
        "set_name": s.get("name"),
        "set_abbreviation": s.get("abbreviation"),
        "set_groupId": s.get("groupId"),
    }


def inventory_to_dict(rows: list[InventoryRow]) -> list[dict]:
    return [asdict(r) for r in rows]
