# ruff: noqa: E501
"""Importación de fotografías desde una carpeta: COPIA (nunca mueve ni modifica el origen), valida y registra."""

import csv
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from app.dataset_lab import schema
from app.dataset_lab.quality import (
    ALLOWED_FORMATS,
    BLOCKING_FLAGS,
    UnsupportedImage,
    analyze,
    near_duplicate,
    sha256_file,
)
from app.dataset_lab.store import Dataset, DatasetError, now

SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
SIDECAR_FIELDS = (
    "lighting_condition",
    "camera_angle",
    "background",
    "sleeve_condition",
    "glare",
    "focus_quality",
    "notes",
)


@dataclass
class ImportReport:
    imported: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)  # {file, reason}
    dry_run: bool = False

    def to_dict(self) -> dict:
        flagged = [i for i in self.imported if i["flags"]]
        return {
            "dry_run": self.dry_run, "imported": len(self.imported), "skipped": len(self.skipped),
            "needs_review": sum(1 for i in self.imported if i["status"] == "needs_review"),
            "with_flags": len(flagged), "skipped_detail": self.skipped,
            "imported_detail": [{"file": i["original_name"], "image_id": i["image_id"], "status": i["status"], "flags": i["flags"]} for i in self.imported],
        }  # fmt: skip


def read_sidecar(path: Path) -> dict[str, dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = {}
        for row in csv.DictReader(f):
            name = (row.get("filename") or "").strip()
            if not name:
                continue
            clean = {k: (row.get(k) or "").strip() for k in SIDECAR_FIELDS if (row.get(k) or "").strip()}
            for k, v in clean.items():
                if k in schema.ENUMS and v not in schema.ENUMS[k]:
                    raise DatasetError(f"{path.name}: {name}: {k}={v!r} no es válido ({schema.ENUMS[k]})")
            rows[name] = clean
        return rows


def import_folder(
    ds: Dataset,
    folder: Path,
    session_id: str,
    card_ref: str,
    defaults: dict | None = None,
    recursive: bool = False,
    dry_run: bool = False,
    sidecar: Path | None = None,
    imported_by: str | None = None,
) -> ImportReport:
    sessions, cards, photos = ds.sessions, ds.cards, ds.photos
    if session_id not in sessions:
        raise DatasetError(f"No existe la sesión {session_id}. Créala con: dataset session-add")
    if card_ref not in cards:
        raise DatasetError(f"No existe la carta {card_ref}. Regístrala con: dataset card-add")
    defaults = {k: v for k, v in (defaults or {}).items() if v}
    for k, v in defaults.items():
        if k in schema.ENUMS and v not in schema.ENUMS[k]:
            raise DatasetError(f"{k}={v!r} no es válido ({schema.ENUMS[k]})")
    folder = Path(folder)
    if not folder.is_dir():
        raise DatasetError(f"La carpeta {folder} no existe")
    per_file = read_sidecar(sidecar) if sidecar else {}
    card, session = cards[card_ref], sessions[session_id]
    files = sorted(
        p
        for p in (folder.rglob("*") if recursive else folder.glob("*"))
        if p.is_file() and p.suffix.lower() in SUFFIXES
    )
    report = ImportReport(dry_run=dry_run)
    if not files:
        report.skipped.append({"file": str(folder), "reason": "sin archivos .jpg/.jpeg/.png/.webp"})

    seen_sha = {p["file"]["sha256"]: pid for pid, p in photos.items()}
    new_records: list[dict] = []
    for path in files:
        sha = sha256_file(path)
        if sha in seen_sha:
            report.skipped.append({"file": path.name, "reason": f"duplicado exacto de {seen_sha[sha]}"})
            continue
        try:
            q = analyze(path)
        except UnsupportedImage as exc:
            report.skipped.append({"file": path.name, "reason": str(exc)})
            continue
        flags = list(q["flags"])
        near = [pid for pid, p in {**photos, **{r["image_id"]: r for r in new_records}}.items()
                if p.get("card_ref") == card_ref and p["capture_session_id"] != session_id and p["file"].get("dhash") and near_duplicate(q["dhash"], p["file"]["dhash"])]  # fmt: skip
        if near:
            flags.append("posible_duplicado_entre_sesiones")  # riesgo de fuga: misma toma en dos sesiones
        if card["verification_status"] != "verified":
            flags.append("carta_sin_verificar")
        image_id = ds.new_image_id()
        meta = {**defaults, **per_file.get(path.name, {})}
        focus = meta.get("focus_quality") or q["focus_auto"]
        status = "needs_review" if (BLOCKING_FLAGS & set(flags)) or near else "pending"
        record = {
            "image_id": image_id,
            "card_ref": card_ref,
            **{f: card.get(f) for f in schema.IDENTITY_FIELDS},
            "capture_session_id": session_id,
            "device_model": session.get("device_model") or q["exif"]["device"] or "desconocido",
            "lighting_condition": meta.get("lighting_condition", "desconocida"),
            "camera_angle": meta.get("camera_angle", "desconocido"),
            "background": meta.get("background", "desconocido"),
            "sleeve_condition": meta.get("sleeve_condition", "desconocida"),
            "glare": meta.get("glare", "desconocido"),
            "focus_quality": focus, "focus_quality_source": "manual" if meta.get("focus_quality") else "auto",
            "verified_by": None, "verification_status": status, "notes": meta.get("notes") or None,
            "card_corners": None,
            "file": {
                "path": None, "sha256": sha, "bytes": path.stat().st_size, "format": q["format"], "width": q["width"], "height": q["height"],
                "original_name": path.name, "dhash": q["dhash"], "exif": q["exif"],
            },
            "quality": {k: q[k] for k in ("sharpness", "brightness", "megapixels", "glare_fraction")} | {"flags": flags},
            "possible_duplicates": near, "imported_at": now(), "imported_by": imported_by,
        }  # fmt: skip
        rel = ds.original_path(session_id, image_id, ALLOWED_FORMATS[q["format"]]).relative_to(ds.root)
        record["file"]["path"] = rel.as_posix()
        new_records.append(record)
        seen_sha[sha] = image_id
        if not dry_run:
            dest = ds.root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            if sha256_file(dest) != sha:
                dest.unlink()
                raise DatasetError(
                    f"La copia de {path.name} no coincide con el original (SHA-256); se detuvo la importación"
                )
            ds.make_read_only(dest)
        report.imported.append({"original_name": path.name, "image_id": image_id, "status": status, "flags": flags})

    if not dry_run and new_records:
        for r in new_records:
            photos[r["image_id"]] = r
        ds.save_photos(photos)
    return report
