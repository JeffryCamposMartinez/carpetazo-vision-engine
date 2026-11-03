# ruff: noqa: E501
"""Comparación `raw` frente a `normalized` con las fotos verificadas del dataset real (para cuando existan). Solo lectura del dataset.

Disciplina: se decide con la ronda A (`on="calibration"`); la ronda B (`on="test"`) exige `confirm_test=True` y debe usarse UNA vez, con la
variante ya elegida. Se niega con datos ficticios salvo `allow_fictitious` (y entonces el informe lo declara).
"""

import cv2

from app.dataset_lab.evaluate import is_fictitious, load_photo, reference_file
from app.dataset_lab.splits import load_split
from app.dataset_lab.store import Dataset, DatasetError, now
from app.recognition.normalization import NormalizationConfig, Normalizer
from app.recognition.normalization_compare import Query, compare_variants

CANON = (440, 614)


def run_normalization_comparison(ds: Dataset, split_name: str, embed, on: str = "calibration", config: NormalizationConfig | None = None, confirm_test: bool = False, allow_fictitious: bool = False, extractor_label: str = "?") -> dict:
    if on not in ("calibration", "test"):
        raise DatasetError("--on debe ser calibration o test")
    if on == "test" and not confirm_test:
        raise DatasetError("La ronda de prueba se toca una sola vez y con la variante ya elegida con la calibración: usa --confirm-test para confirmarlo")
    cfg = config or NormalizationConfig()
    assignment = load_split(ds, split_name)["assignment"]
    cards, photos = ds.cards, ds.photos
    verified = [p for p in photos.values() if p["verification_status"] == "verified" and cards.get(p.get("card_ref"), {}).get("verification_status") == "verified" and assignment.get(p["capture_session_id"]) == on]
    fictitious = any(is_fictitious(p) for p in verified)
    if fictitious and not allow_fictitious:
        raise DatasetError("El dataset contiene datos FICTICIOS: no se calculan métricas (usa --allow-fictitious solo para probar el flujo)")
    refs = {}
    for ref, c in cards.items():
        f = reference_file(ds, ref)
        if c["verification_status"] == "verified" and c.get("reference_verified_by") and f:
            refs[ref] = cv2.resize(load_photo(f, 4000), CANON, interpolation=cv2.INTER_AREA)
    if not refs:
        raise DatasetError("No hay referencias verificadas a mano (card-reference + references-fetch)")
    normalizer, queries, skipped = Normalizer(cfg), [], 0
    for p in verified:
        if p["card_ref"] not in refs:
            continue
        img = load_photo(ds.root / p["file"]["path"])
        det = normalizer.detector.detect(img)
        if not det.found:
            skipped += 1
            continue
        queries.append(Query(p["image_id"], p["card_ref"], normalizer.process(img, det).variants()))
    out = compare_variants(queries, refs, embed, cfg, synthetic=fictitious)
    out.update({"created_at": now(), "split": split_name, "evaluated_on": on, "extractor": extractor_label, "photos_without_detection": skipped, "manifest_photos": len(verified)})
    return out
