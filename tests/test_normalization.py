# ruff: noqa: E501
"""E2: normalización con datos SINTÉTICOS. Se comprueba que el original no se toca, que se registran las transformaciones y que el color no se altera;
NO se afirma que normalizar mejore el reconocimiento (eso lo decide la comparación pareada con fotos reales)."""

import hashlib

import cv2
import numpy as np
import pytest

from app.dataset_lab.store import DatasetError
from app.recognition.detection import CANON_H, CANON_W, CardDetector, corner_error
from app.recognition.embedding.grid import GridExtractor
from app.recognition.normalization import (
    RAW_CONFIG,
    NormalizationConfig,
    Normalizer,
    glare_mask,
    normalize_canonical,
    refine_quad,
)
from app.recognition.normalization_compare import Query, compare_variants
from tests import synthetic_scenes as S
from tests.test_recognition_lab import fake_card

detector = CardDetector()


def sha(a: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()


def lab(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)


def canon(seed):
    return cv2.resize(fake_card(seed), (CANON_W, CANON_H), interpolation=cv2.INTER_AREA)


def test_original_photo_is_never_modified_and_is_kept_read_only():
    img, _ = S.card_scene(3, S.LEVELS["normal"])
    before = sha(img)
    res = Normalizer(NormalizationConfig(refine_corners=True)).process(img)
    assert sha(img) == before and res.original_sha256 == before
    assert res.original.flags.writeable is False and np.array_equal(res.original, img)
    with pytest.raises(ValueError):
        res.original[0, 0, 0] = 1
    assert res.normalized is not img and res.rectified_raw.upright is not res.normalized
    assert img.flags.writeable  # la foto del llamador sigue siendo suya


def test_normalize_canonical_does_not_write_on_its_input():
    card = canon(2)
    before = sha(card)
    out, _ = normalize_canonical(card, NormalizationConfig())
    assert sha(card) == before and out.shape == card.shape and out is not card


def test_every_transformation_is_recorded_with_parameters_and_measured_effect():
    img, _ = S.card_scene(3, S.LEVELS["normal"])
    res = Normalizer(NormalizationConfig(refine_corners=True)).process(img)
    names = [t.name for t in res.transforms]
    assert names == ["corner_refinement", "perspective_warp", "border_crop", "illumination_flatten", "highlight_compression"]
    byname = {t.name: t for t in res.transforms}
    assert "mean_shift_px" in byname["corner_refinement"].effect and byname["perspective_warp"].params["rotation_180"].startswith("unresolved")
    assert {"mean_before", "mean_after", "std_after"} <= set(byname["illumination_flatten"].effect) and byname["highlight_compression"].params["inpainting"] is False
    assert res.config.tag.startswith("n1-") and len(res.config.tag) == 11
    assert NormalizationConfig().config_id == NormalizationConfig().config_id != NormalizationConfig(illumination_strength=0.9).config_id


def test_raw_variant_is_pure_geometry_identical_to_the_detector_rectification():
    img, _ = S.card_scene(4, S.LEVELS["normal"])
    det = detector.detect(img)
    res = Normalizer(RAW_CONFIG).process(img, det)
    assert np.array_equal(res.rectified_raw.upright, detector.rectify(img, det).upright)
    assert np.array_equal(res.normalized, res.rectified_raw.upright) and [t.name for t in res.transforms] == ["perspective_warp"]
    v = res.variants()
    assert set(v) == {"raw", "normalized"} and np.array_equal(v["normalized"][1], cv2.rotate(v["normalized"][0], cv2.ROTATE_180))


def test_color_channels_are_not_altered_by_the_photometric_steps():
    rng = np.random.default_rng(1)
    card = np.clip(canon(1).astype(np.int16) + rng.integers(-20, 20, (CANON_H, CANON_W, 1)), 0, 255).astype(np.uint8)
    out, _ = normalize_canonical(card, NormalizationConfig(border_crop=0.0))
    a0, a1 = lab(card), lab(out)
    assert np.abs(a0[..., 1:] - a1[..., 1:]).mean() < 1.5  # solo el redondeo de ida y vuelta Lab<->BGR


def test_holo_like_rainbow_keeps_its_hue_distribution():
    x = np.linspace(0, 179, CANON_W, dtype=np.float32)
    v = np.tile(np.linspace(110, 250, CANON_H, dtype=np.float32)[:, None], (1, CANON_W))
    hsv = np.stack([np.tile(x, (CANON_H, 1)), np.full((CANON_H, CANON_W), 200, np.float32), v], -1).astype(np.uint8)
    holo = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    out, _ = normalize_canonical(holo, NormalizationConfig(border_crop=0.0))
    h0 = np.histogram(cv2.cvtColor(holo, cv2.COLOR_BGR2HSV)[..., 0], bins=18, range=(0, 180))[0] / holo[..., 0].size
    h1 = np.histogram(cv2.cvtColor(out, cv2.COLOR_BGR2HSV)[..., 0], bins=18, range=(0, 180))[0] / holo[..., 0].size
    assert np.minimum(h0, h1).sum() > 0.93  # intersección de histogramas de tono


def test_uneven_lighting_is_flattened_and_zero_strength_changes_nothing():
    card = canon(5)
    ramp = np.linspace(0.45, 1.25, CANON_W, dtype=np.float32)[None, :, None]
    lit = np.clip(card.astype(np.float32) * ramp, 0, 255).astype(np.uint8)
    cfg = NormalizationConfig(border_crop=0.0, highlight_compression=False, illumination_strength=0.8)
    out, _ = normalize_canonical(lit, cfg)

    def low_freq_spread(img):
        L = lab(img)[..., 0]
        return float(np.std([L[:, i * 88 : (i + 1) * 88].mean() for i in range(5)]))

    assert low_freq_spread(out) < 0.75 * low_freq_spread(lit)
    same, _ = normalize_canonical(lit, NormalizationConfig(border_crop=0.0, illumination=False, highlight_compression=False))
    assert np.array_equal(same, lit)  # sin pasos activos no cambia nada


def test_highlights_are_compressed_not_inpainted_and_glare_is_only_reported():
    card = canon(6).copy()
    card[100:160, 100:200] = 255  # reflejo especular blanco
    out, recs = normalize_canonical(card, NormalizationConfig(border_crop=0.0, illumination=False))
    L0, L1 = lab(card)[..., 0], lab(out)[..., 0]
    dark = L0 <= 235
    assert np.abs(L0[dark] - L1[dark]).max() <= 1  # nada por debajo de la rodilla cambia
    assert 235 < L1[100:160, 100:200].mean() < L0[100:160, 100:200].mean()  # se comprime, no se inventa contenido
    assert recs[0].effect["fraction_of_pixels"] > 0
    m = glare_mask(card)
    assert m[100:160, 100:200].mean() > 250 and m[300:400, 300:400].mean() < 5


def test_corner_refinement_is_optional_bounded_and_helps_on_cluttered_backgrounds():
    before = after = 0.0
    n = 0
    for s in range(15):
        img, quad = S.cluttered_card_scene(s)
        det = detector.detect(img)
        if not det.found:
            continue
        rq, ok, shift = refine_quad(img, det.quad)
        n += 1
        before += corner_error(det.quad, quad)
        after += corner_error(rq, quad)
        assert (not ok) or shift < 0.03 * 960
    assert n >= 10 and after < before  # límite medido: en escenas limpias empeora ≈1,5 px (por eso es opcional); aquí mejora
    assert NormalizationConfig().refine_corners is False


def test_refinement_falls_back_when_there_are_not_enough_edges():
    blank = np.full((600, 800, 3), 128, np.uint8)
    quad = np.array([[200, 100], [600, 100], [600, 500], [200, 500]], np.float32)
    rq, ok, shift = refine_quad(blank, quad)
    assert not ok and np.array_equal(rq, quad) and shift == 0.0


def test_not_a_card_cannot_be_normalized():
    with pytest.raises(ValueError, match="not_a_card"):
        Normalizer().process(S.blank_wall(1))


def test_normalize_canonical_requires_a_rectified_card():
    with pytest.raises(ValueError, match="rectificada"):
        normalize_canonical(np.zeros((50, 50, 3), np.uint8), NormalizationConfig())


# ---- comparación pareada raw frente a normalized
def build_queries(cards):
    norm = Normalizer()
    qs = []
    for cid in cards:
        img, _ = S.card_scene(cid, S.LEVELS["normal"])
        qs.append(Query(f"q{cid}", str(cid), norm.process(img).variants()))
    return qs


def test_paired_comparison_reports_both_variants_and_consistent_counts():
    cards = {i: canon(i) for i in range(10)}
    refs = {str(k): v for k, v in cards.items()}
    g = GridExtractor()
    out = compare_variants(build_queries(cards), refs, g.embed, NormalizationConfig(), synthetic=True)
    assert out["n_queries"] == 10 and out["n_references"] == 10 and set(out["variants"]) == {"raw", "normalized"}
    p = out["paired_top1"]
    n_raw, n_norm = round(out["variants"]["raw"]["top1"] * 10), round(out["variants"]["normalized"]["top1"] * 10)
    assert p["only_raw_correct"] - p["only_normalized_correct"] == n_raw - n_norm and 0 <= p["mcnemar_exact_p"] <= 1
    assert out["evidence"]["level"] == "exploratorio" and "datos sintéticos" in out["evidence"]["reasons"]
    assert out["config"]["tag"] == NormalizationConfig().tag
    assert compare_variants(build_queries(cards), refs, g.embed, NormalizationConfig(), synthetic=True) == out  # determinista


def test_comparing_a_variant_with_itself_shows_no_difference():
    cards = {i: canon(i) for i in range(6)}
    g = GridExtractor()
    qs = [Query(q.query_id, q.truth, {"raw": q.variants["raw"], "normalized": q.variants["raw"]}) for q in build_queries(cards)]
    out = compare_variants(qs, {str(k): v for k, v in cards.items()}, g.embed, RAW_CONFIG)
    assert out["paired_top1"]["only_raw_correct"] == 0 == out["paired_top1"]["only_normalized_correct"] and out["paired_top1"]["mcnemar_exact_p"] == 1.0


def test_dataset_comparison_is_read_only_and_guards_the_test_round(tmp_path):
    from app.dataset_lab.normalization_eval import run_normalization_comparison
    from app.dataset_lab.splits import make_split
    from app.dataset_lab.store import Dataset
    from tests.test_dataset_lab import make_eval_dataset

    ds = Dataset(tmp_path / "ds")
    ds.init()
    ds.add_session("s-test-a", "2026-11-05", "t", None, "t", None, "A")
    ds.add_session("s-test-b", "2026-11-06", "t", None, "t", None, "B")
    make_eval_dataset(ds, tmp_path, cards=4)
    make_split(ds, "p", manual={"s-test-a": "calibration", "s-test-b": "test"})
    g = GridExtractor()
    photos_before = (ds.manifest / "photos.json").read_bytes()
    out = run_normalization_comparison(ds, "p", g.embed, "calibration", extractor_label="grid")
    assert out["evaluated_on"] == "calibration" and out["n_references"] == 4 and out["n_queries"] > 0 and out["evidence"]["level"] == "exploratorio"
    assert (ds.manifest / "photos.json").read_bytes() == photos_before
    with pytest.raises(DatasetError, match="confirm-test"):
        run_normalization_comparison(ds, "p", g.embed, "test")
    assert run_normalization_comparison(ds, "p", g.embed, "test", confirm_test=True)["evaluated_on"] == "test"
    photos = ds.photos
    for p in photos.values():  # todas: el orden de las fotos es aleatorio y la comparación solo mira la ronda pedida
        p["_ficticio"] = True
    ds.save_photos(photos)
    with pytest.raises(DatasetError, match="FICTICIOS"):
        run_normalization_comparison(ds, "p", g.embed, "calibration")
