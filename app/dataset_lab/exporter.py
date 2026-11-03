# ruff: noqa: E501
"""Exportación y respaldo SIN modificar los originales (siempre copias, nunca mueve ni borra)."""

import csv
import shutil
from pathlib import Path

from app.dataset_lab.quality import sha256_file
from app.dataset_lab.splits import load_split
from app.dataset_lab.store import Dataset, DatasetError, atomic_write, now

CSV_COLUMNS = [
    "image_id", "card_ref", "card_id", "printing_id", "variant_id", "name", "set_name", "collector_number", "language", "finish",
    "category", "capture_session_id", "split", "device_model", "lighting_condition", "camera_angle", "background", "sleeve_condition",
    "glare", "focus_quality", "verified_by", "verification_status", "notes", "width", "height", "sha256", "path", "sharpness",
    "brightness", "quality_flags", "reference_object_key", "provisional",
]  # fmt: skip


def photo_row(p: dict, card: dict | None, split: str | None, path: str, provisional: bool = False) -> dict:
    card = card or {}
    q = p.get("quality", {})
    return {
        "image_id": p["image_id"], "card_ref": p.get("card_ref"), "card_id": p.get("card_id"), "printing_id": p.get("printing_id"),
        "variant_id": p.get("variant_id"), "name": card.get("name"), "set_name": card.get("set_name"),
        "collector_number": card.get("collector_number"), "language": card.get("language"), "finish": card.get("finish"),
        "category": card.get("category"), "capture_session_id": p["capture_session_id"], "split": split,
        "device_model": p.get("device_model"), "lighting_condition": p.get("lighting_condition"), "camera_angle": p.get("camera_angle"),
        "background": p.get("background"), "sleeve_condition": p.get("sleeve_condition"), "glare": p.get("glare"),
        "focus_quality": p.get("focus_quality"), "verified_by": p.get("verified_by"), "verification_status": p["verification_status"],
        "notes": p.get("notes"), "width": p["file"]["width"], "height": p["file"]["height"], "sha256": p["file"]["sha256"], "path": path,
        "sharpness": q.get("sharpness"), "brightness": q.get("brightness"), "quality_flags": ";".join(q.get("flags", [])),
        "reference_object_key": card.get("reference_object_key"), "provisional": provisional,
    }  # fmt: skip


def write_manifest(out_dir: Path, rows: list[dict], header: dict, cards: dict | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(out_dir / "manifest.json", {"dataset": header, "cards": cards or {}, "photos": rows})
    with (out_dir / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def export_dataset(
    ds: Dataset,
    out_dir: Path,
    split_name: str | None = None,
    include_pending: bool = False,
    copy_images: bool = True,
    allow_leaks: bool = False,
) -> dict:
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise DatasetError(f"{out_dir} ya existe y no está vacía: elige una carpeta nueva (no se sobrescribe nada)")
    if out_dir.resolve().is_relative_to(ds.originals.resolve()):
        raise DatasetError("La exportación no puede estar dentro de originals/")
    cards, photos = ds.cards, ds.photos
    assignment, split = {}, None
    if split_name:
        split = load_split(ds, split_name)
        assignment = split["assignment"]
        if split["leaks"]["blocking"] and not allow_leaks:
            raise DatasetError(
                "La partición tiene posibles fugas (cuasi-duplicados entre conjuntos). Revisa 'leaks' o usa --allow-leaks"
            )
    rows, chosen = [], []
    for p in sorted(photos.values(), key=lambda x: x["image_id"]):
        card = cards.get(p.get("card_ref"))
        ok = p["verification_status"] == "verified" and card and card["verification_status"] == "verified"
        provisional = False
        if not ok and include_pending and p["verification_status"] == "pending":
            provisional = True
        elif not ok:
            continue
        if split_name and p["capture_session_id"] not in assignment:
            continue
        ext = Path(p["file"]["path"]).suffix
        rel = f"images/{p['image_id']}{ext}" if copy_images else p["file"]["path"]
        rows.append(photo_row(p, card, assignment.get(p["capture_session_id"]), rel, provisional))
        chosen.append((p, rel))
    if copy_images:
        for p, rel in chosen:
            dest = out_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ds.root / p["file"]["path"], dest)
    header = {"exported_at": now(), "split": split_name, "photos": len(rows), "images_copied": copy_images, "includes_pending": include_pending,
              "split_rule": split["rule"] if split else None}  # fmt: skip
    used = {r["card_ref"] for r in rows}
    write_manifest(out_dir, rows, header, {k: v for k, v in cards.items() if k in used})
    if copy_images:
        (out_dir / "SHA256SUMS.txt").write_text(
            "".join(f"{r['sha256']}  {r['path']}\n" for r in rows), encoding="utf-8"
        )
    (out_dir / "LEEME.txt").write_text(
        "Exportación del dataset de fotografías reales de Carpetazo Vision Engine.\n"
        "Contiene copias; los originales siguen intactos en datasets/real-photos/originals.\n"
        "Datos personales: las fotos conservan su EXIF (puede incluir ubicación). No compartir fuera de este equipo sin limpiarlo.\n",
        encoding="utf-8",
    )
    return header


def backup_dataset(ds: Dataset, dest_parent: Path) -> dict:
    """Copia todo el dataset a <dest_parent>/real-photos-backup-<fecha> y verifica cada archivo por SHA-256. No borra nada."""
    ds.require()
    dest = Path(dest_parent) / f"real-photos-backup-{now().replace(':', '').replace('-', '')[:15]}"
    if dest.exists():
        raise DatasetError(f"{dest} ya existe")
    if dest.resolve().is_relative_to(ds.root.resolve()):
        raise DatasetError("El respaldo no puede estar dentro del propio dataset")
    shutil.copytree(ds.root, dest, ignore=shutil.ignore_patterns("exports", "inbox", "*.tmp"))
    checked = 0
    for src in ds.root.rglob("*"):
        if (
            src.is_file()
            and not any(part in {"exports", "inbox"} for part in src.relative_to(ds.root).parts)
            and src.suffix != ".tmp"
        ):
            if sha256_file(src) != sha256_file(dest / src.relative_to(ds.root)):
                raise DatasetError(f"El respaldo de {src.name} no coincide con el original")
            checked += 1
    return {"destination": str(dest), "files_verified": checked}
