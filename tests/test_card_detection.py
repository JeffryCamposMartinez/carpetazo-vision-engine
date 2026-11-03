# ruff: noqa: E501
"""E1: detector de cartas con escenas SINTÉTICAS (no hay fotos reales). Los umbrales del detector son provisionales."""

import cv2
import numpy as np
import pytest

from app.recognition.detection import CANON_H, CANON_W, DEFAULT_MIN_CONFIDENCE, CardDetector, corner_error
from tests import synthetic_scenes as S
from tests.test_recognition_lab import fake_card

SEEDS = range(12)
detector = CardDetector()


def detection_rate(make, max_error=0.05):
    ok = 0
    for s in SEEDS:
        img, quad = make(s)
        d = detector.detect(img)
        ok += d.found and corner_error(d.quad, quad) <= max_error
    return ok / len(SEEDS)


@pytest.mark.parametrize("level", ["easy", "normal", "glare"])
def test_tilted_cards_with_glare_are_found_with_accurate_corners(level):
    assert detection_rate(lambda s: S.card_scene(s, S.LEVELS[level])) >= 0.9


def test_card_on_a_cluttered_background_is_found():
    assert detection_rate(S.cluttered_card_scene) >= 0.8


def test_hard_scenes_are_reported_honestly_not_asserted_as_solved():
    """Nivel difícil (rotación libre, perspectiva fuerte, desenfoque, JPEG 45): mejora parcial; solo se exige no inventar cartas con confianza alta y errónea."""
    wrong_but_confident = 0
    for s in SEEDS:
        img, quad = S.card_scene(s, S.LEVELS["hard"])
        d = detector.detect(img)
        wrong_but_confident += d.found and corner_error(d.quad, quad) > 0.10 and d.confidence >= 0.9
    assert wrong_but_confident == 0


@pytest.mark.parametrize("name", list(S.NOT_CARDS))
def test_objects_that_are_not_cards_are_rejected(name):
    for s in range(10):
        d = detector.detect(S.NOT_CARDS[name](s))
        assert not d.found and d.status_hint == "not_a_card" and d.quad is None, f"{name} seed {s}: conf={d.confidence}"
        assert d.reason and d.confidence < DEFAULT_MIN_CONFIDENCE


def test_known_limit_a_rectangular_page_with_card_proportions_is_detected_as_a_card():
    """Límite documentado: la geometría no distingue una hoja A4 de una carta (0,707 frente a 0,716). Lo resuelve el rechazo por similitud (E7)."""
    d = detector.detect(S.text_page(1))
    assert d.found and d.confidence > 0.9


def test_card_cut_by_the_image_border_is_flagged_or_rejected():
    for s in range(6):
        d = detector.detect(S.card_cut_by_border(s))
        assert (not d.found) or d.metrics.touches_image_border or d.confidence >= 0.9
        if d.found and d.metrics.touches_image_border:
            assert any("borde" in w for w in d.warnings)


def test_rectify_returns_the_canonical_upright_card_and_its_180_flip():
    card = fake_card(5)
    img, quad = S.card_scene(5, S.LEVELS["easy"])
    d = detector.detect(img)
    assert d.found
    r = detector.rectify(img, d)
    assert r.upright.shape == (CANON_H, CANON_W, 3) and r.flipped.shape == r.upright.shape
    assert np.array_equal(r.flipped, cv2.rotate(r.upright, cv2.ROTATE_180)) and r.orientation_resolved is False
    ref = cv2.resize(card, (CANON_W, CANON_H), interpolation=cv2.INTER_AREA).astype(np.float32)
    best = min(np.abs(v.astype(np.float32) - ref).mean() for v in (r.upright, r.flipped))
    assert best < 40  # se parece a la carta original (la luz y el JPEG de la escena la alteran)


def test_sideways_card_is_rotated_to_portrait():
    img, _ = S.card_scene(3, S.LEVELS["easy"])
    sideways = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    d = detector.detect(sideways)
    assert d.found
    r = detector.rectify(sideways, d)
    card = cv2.resize(fake_card(3), (CANON_W, CANON_H), interpolation=cv2.INTER_AREA).astype(np.float32)
    assert min(np.abs(v.astype(np.float32) - card).mean() for v in (r.upright, r.flipped)) < 40


def test_metrics_and_quality_values_are_reported():
    img, _ = S.card_scene(2, S.LEVELS["normal"])
    d = detector.detect(img)
    m = d.metrics
    assert 0.1 < m.area_fraction < 1 and 0.5 < m.aspect_ratio < 0.9 and 0 <= m.border_support <= 1
    assert m.strong_candidates >= 1 and m.perspective_skew >= 0 and m.corner_angle_deviation_deg >= 0
    r = detector.rectify(img, d)
    assert r.sharpness > 0 and 0 < r.brightness < 255 and r.contrast > 0 and 0 <= r.glare_fraction <= 1
    sharp = detector.rectify(*(lambda i: (i, detector.detect(i)))(S.card_scene(2, S.LEVELS["easy"])[0]))
    blurry_img = cv2.GaussianBlur(S.card_scene(2, S.LEVELS["easy"])[0], (0, 0), 6)
    blurry = detector.rectify(blurry_img, detector.detect(blurry_img))
    assert blurry.sharpness < sharp.sharpness
    glare_img, _ = S.card_scene(4, S.LEVELS["glare"])
    assert detector.rectify(glare_img, detector.detect(glare_img)).glare_fraction >= 0


def test_rectify_refuses_a_rejected_detection():
    wall = S.blank_wall(1)
    with pytest.raises(ValueError, match="rechazada"):
        detector.rectify(wall, detector.detect(wall))


@pytest.mark.parametrize("bad", [None, np.zeros((10, 10, 3), np.uint8), np.zeros((300, 300), np.uint8), np.zeros((300, 300, 4), np.uint8)])
def test_invalid_inputs_are_rejected_without_crashing(bad):
    d = detector.detect(bad)
    assert not d.found and d.reason


def test_detection_is_deterministic():
    img, _ = S.card_scene(8, S.LEVELS["normal"])
    a, b = detector.detect(img), detector.detect(img)
    assert a.confidence == b.confidence and np.array_equal(a.quad, b.quad)


def test_threshold_is_configurable():
    img, _ = S.card_scene(8, S.LEVELS["normal"])
    assert detector.detect(img).found and not CardDetector(min_confidence=0.999).detect(img).found
