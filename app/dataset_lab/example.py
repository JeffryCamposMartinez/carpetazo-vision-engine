# ruff: noqa: E501
"""Manifiesto de EJEMPLO con datos FICTICIOS (no hay fotografías ni cartas reales detrás). Sirve para ver el formato."""

import hashlib
from pathlib import Path

from app.dataset_lab.exporter import photo_row, write_manifest

AVISO = "DATOS FICTICIOS DE EJEMPLO: no existen estas fotografías ni estas cartas. No usar para medir nada."


def build_example(out_dir: Path) -> dict:
    cards = {
        "EJEMPLO-pc-001": dict(card_ref="EJEMPLO-pc-001", name="Carta Ficticia A", set_name="Set Ficticio", collector_number="001/100", language="es", finish="normal", categories=["comun"], pattern="ninguno", reference_object_key=None),
        "EJEMPLO-pc-002": dict(card_ref="EJEMPLO-pc-002", name="Carta Ficticia B", set_name="Set Ficticio", collector_number="002/100", language="es", finish="holo", categories=["holo", "similar_dificil"], pattern="ninguno", reference_object_key=None),
    }  # fmt: skip
    plan = [
        ("EJEMPLO-pc-001", "EJEMPLO-sesion-1", "natural_directa", "frontal", "neutro_claro", "sin_funda", "buena", "ninguno", "calibration"),
        ("EJEMPLO-pc-001", "EJEMPLO-sesion-2", "interior_calida", "inclinada_media", "madera", "funda_transparente", "aceptable", "leve", "test"),
        ("EJEMPLO-pc-002", "EJEMPLO-sesion-1", "natural_sombra", "frontal", "neutro_oscuro", "sin_funda", "buena", "ninguno", "calibration"),
        ("EJEMPLO-pc-002", "EJEMPLO-sesion-2", "flash", "inclinada_fuerte", "mantel_estampado", "funda_transparente", "borrosa", "fuerte", "test"),
    ]  # fmt: skip
    rows = []
    for i, (ref, sess, light, angle, bg, sleeve, focus, glare, split) in enumerate(plan, 1):
        photo = {
            "image_id": f"EJEMPLO-img-{i:03d}", "card_ref": ref, "card_id": None, "printing_id": None, "variant_id": None,
            "capture_session_id": sess, "device_model": "Teléfono Ficticio X", "lighting_condition": light, "camera_angle": angle,
            "background": bg, "sleeve_condition": sleeve, "glare": glare, "focus_quality": focus, "verified_by": "Persona Ficticia",
            "verification_status": "verified", "notes": "ejemplo",
            "file": {"width": 3024, "height": 4032, "sha256": hashlib.sha256(f"ejemplo-{i}".encode()).hexdigest()},
            "quality": {"sharpness": 120.5, "brightness": 128.0, "flags": ["resolucion_justa"] if focus == "borrosa" else []},
        }  # fmt: skip
        row = photo_row(photo, cards[ref], split, f"images/EJEMPLO-img-{i:03d}.jpg")
        row["_ficticio"] = True
        rows.append(row)
    header = {
        "_AVISO": AVISO,
        "exported_at": "0000-00-00T00:00:00+00:00",
        "split": "ejemplo",
        "photos": len(rows),
        "images_copied": False,
    }
    write_manifest(Path(out_dir), rows, header, cards)
    return header
