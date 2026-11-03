# ruff: noqa: E501
"""Almacén local del dataset: JSON atómico + originales de solo lectura. Nunca sube ni borra fotografías."""

import json
import os
import re
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.dataset_lab import schema

DEFAULT_ROOT = Path("datasets/real-photos")
SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,40}$")


class DatasetError(Exception):
    pass


def valid_date(value: str) -> bool:
    """Fecha de captura AAAA-MM-DD real (p. ej. 2026-11-05)."""
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        return False
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def atomic_write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


class Dataset:
    def __init__(self, root: Path = DEFAULT_ROOT):
        self.root = Path(root)
        self.originals = self.root / "originals"
        self.manifest = self.root / "manifest"
        self.splits_dir = self.root / "splits"
        self.references = self.root / "references"

    # ---- ciclo de vida
    def init(self) -> bool:
        created = not (self.root / "dataset.json").exists()
        for d in (
            self.originals,
            self.manifest,
            self.splits_dir,
            self.references,
            self.root / "inbox",
            self.root / "exports",
        ):
            d.mkdir(parents=True, exist_ok=True)
        if created:
            atomic_write(self.root / "dataset.json", {"schema_version": schema.SCHEMA_VERSION, "created_at": now()})
            for name in ("cards", "sessions", "photos"):
                atomic_write(self.manifest / f"{name}.json", {})
        return created

    def require(self) -> None:
        if not (self.root / "dataset.json").exists():
            raise DatasetError(f"No existe el dataset en {self.root}. Ejecuta: dataset init")

    def _load(self, name: str) -> dict:
        self.require()
        return json.loads((self.manifest / f"{name}.json").read_text(encoding="utf-8"))

    def _save(self, name: str, data: dict) -> None:
        atomic_write(self.manifest / f"{name}.json", data)

    cards = property(lambda self: self._load("cards"))
    sessions = property(lambda self: self._load("sessions"))
    photos = property(lambda self: self._load("photos"))

    def save_cards(self, data: dict) -> None:
        self._save("cards", data)

    def save_sessions(self, data: dict) -> None:
        self._save("sessions", data)

    def save_photos(self, data: dict) -> None:
        self._save("photos", data)

    # ---- sesiones
    def add_session(
        self,
        session_id: str,
        date: str,
        device_model: str | None,
        place: str | None,
        operator: str | None,
        notes: str | None,
        round_label: str | None = None,
    ) -> dict:
        if not SESSION_ID.match(session_id):
            raise DatasetError("session_id: 3–41 caracteres (letras, números, _ . -), p. ej. s-2026-11-05-a")
        if not valid_date(date):
            raise DatasetError(f"fecha {date!r} inválida: use AAAA-MM-DD (p. ej. 2026-11-05)")
        sessions = self.sessions
        if session_id in sessions:
            raise DatasetError(f"La sesión {session_id} ya existe")
        record = {
            "capture_session_id": session_id, "date": date, "device_model": device_model, "place": place,
            "operator": operator, "notes": notes, "round": round_label, "created_at": now(),
        }  # fmt: skip
        errors = schema.validate_session(record)
        if errors:
            raise DatasetError("; ".join(errors))
        sessions[session_id] = record
        self.save_sessions(sessions)
        return record

    # ---- cartas físicas
    def add_card(self, persist: bool = True, **fields) -> dict:
        cards = self.cards
        n = max((int(k.split("-")[1]) for k in cards), default=0) + 1
        ref = f"pc-{n:03d}"  # clave LOCAL del dataset, no un id del catálogo
        record = {
            "card_ref": ref, "pilot_slot": None, "name": None, "set_name": None, "collector_number": None, "language": "unknown",
            "finish": "desconocido", "categories": ["otra"], "pattern": "desconocido", "variant_group": None, "similar_to": None,
            "difficulty_note": None, "tcgdex_card_id": None, "catalog_category_id": None, "catalog_product_id": None, "card_id": None, "printing_id": None, "variant_id": None,
            "reference_object_key": None, "reference_sha256": None, "reference_verified_by": None, "reference_verified_at": None,
            "reference_verification_note": None, "condition_notes": None,
            "verification_status": "unverified", "verified_by": None, "verified_at": None, "verification_note": None,
            "created_at": now(),
        }  # fmt: skip
        unknown = set(fields) - set(record)
        if unknown:
            raise DatasetError(f"Campos desconocidos: {sorted(unknown)}")
        record.update({k: v for k, v in fields.items() if v is not None})
        errors = schema.validate_card(record)
        if errors:
            raise DatasetError("; ".join(errors))
        key = self.identity_key(record)
        twin = next((r for r, c in cards.items() if self.identity_key(c) == key), None)
        if twin:
            raise DatasetError(f"Carta duplicada: ya existe {twin} con el mismo nombre, set, número, idioma, acabado y patrón")
        if persist:
            cards[ref] = record
            self.save_cards(cards)
        return record

    @staticmethod
    def identity_key(card: dict) -> tuple:
        """Clave para detectar la misma carta física registrada dos veces (sin distinguir mayúsculas)."""
        fields = ("name", "set_name", "collector_number", "language", "finish", "pattern")
        return tuple(str(card.get(f) or "").strip().casefold() for f in fields)

    def verify_reference(self, card_ref: str, by: str, note: str) -> dict:
        cards = self.cards
        card = cards.get(card_ref)
        if card is None or card["verification_status"] != "verified":
            raise DatasetError("La carta debe existir y estar verificada antes de verificar su referencia")
        if not card.get("reference_object_key"):
            raise DatasetError("Primero indica la referencia con card-reference --key")
        if not by.strip() or not note.strip():
            raise DatasetError(
                "Verificar la referencia exige --by y --note (qué se comparó: ilustración, número, símbolo, patrón)"
            )
        card.update(
            {
                "reference_verified_by": by.strip(),
                "reference_verified_at": now(),
                "reference_verification_note": note.strip(),
            }
        )
        self.save_cards(cards)
        return card

    def verify_card(self, card_ref: str, by: str, note: str) -> dict:
        cards = self.cards
        if card_ref not in cards:
            raise DatasetError(f"No existe la carta {card_ref}")
        if not by.strip() or not note.strip():
            raise DatasetError("Verificar exige --by y --note (qué se comprobó en la carta física)")
        cards[card_ref].update(
            {
                "verification_status": "verified",
                "verified_by": by.strip(),
                "verified_at": now(),
                "verification_note": note.strip(),
            }
        )
        errors = schema.validate_card(cards[card_ref])
        if errors:
            raise DatasetError("; ".join(errors))
        self.save_cards(cards)
        self.sync_identity(card_ref)
        return cards[card_ref]

    def sync_identity(self, card_ref: str) -> int:
        """Copia card_id/printing_id/variant_id del registro a las fotos de esa carta (campos desnormalizados)."""
        card, photos, n = self.cards[card_ref], self.photos, 0
        for p in photos.values():
            if p.get("card_ref") == card_ref:
                for f in schema.IDENTITY_FIELDS:
                    p[f] = card.get(f)
                n += 1
        self.save_photos(photos)
        return n

    # ---- fotos
    @staticmethod
    def new_image_id() -> str:
        return "img_" + uuid.uuid4().hex[:12]

    def original_path(self, session_id: str, image_id: str, ext: str) -> Path:
        return self.originals / session_id / f"{image_id}{ext.lower()}"

    @staticmethod
    def make_read_only(path: Path) -> None:
        os.chmod(path, stat.S_IREAD)
