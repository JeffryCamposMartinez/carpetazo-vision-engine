# ruff: noqa: E501
import cv2
import numpy as np
import pytest

from app.images.inventory import InventoryRow, parse_card_folder
from app.recognition_lab.augment import EASY, HARD, make_scene
from app.recognition_lab.benchmark import corner_error, evaluate
from app.recognition_lab.features import GridExtractor, PHashExtractor
from app.recognition_lab.index import RefIndex
from app.recognition_lab.rectify import find_card_quad, to_canonical, warp_card
from app.recognition_lab.sampling import select_sample


# FIXTURE sintética: "carta" de bloques de color y texto generada con una semilla (no es una carta real).
def fake_card(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.full((614, 440, 3), 235, np.uint8)
    for _ in range(14):
        x, y = int(rng.integers(0, 380)), int(rng.integers(0, 560))
        cv2.rectangle(
            img,
            (x, y),
            (x + int(rng.integers(30, 160)), y + int(rng.integers(30, 160))),
            rng.integers(0, 255, 3).tolist(),
            -1,
        )
    cv2.putText(img, f"CARD {seed}", (30, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (20, 20, 20), 4)
    cv2.rectangle(img, (0, 0), (439, 613), (30, 30, 30), 8)
    return img


def test_scenes_are_deterministic_and_the_ground_truth_quad_is_inside():
    card = fake_card(1)
    a, quad_a = make_scene(card, 5, HARD)
    b, quad_b = make_scene(card, 5, HARD)
    c, _ = make_scene(card, 6, HARD)
    assert np.array_equal(a, b) and np.array_equal(quad_a, quad_b) and not np.array_equal(a, c)
    h, w = a.shape[:2]
    assert (quad_a >= -1).all() and (quad_a[:, 0] <= w + 1).all() and (quad_a[:, 1] <= h + 1).all()


def test_detector_finds_the_card_in_an_easy_scene_and_the_warp_is_canonical():
    card = fake_card(2)
    scene, truth = make_scene(card, 11, EASY)
    quad, score = find_card_quad(scene)
    assert quad is not None and score > 0.1 and corner_error(quad, truth) < 0.06
    assert warp_card(scene, quad).shape == (614, 440, 3)


def test_extractors_are_deterministic_and_discriminate():
    a, b = to_canonical(fake_card(3)), to_canonical(fake_card(4))
    for ext in (PHashExtractor(), GridExtractor()):
        va, va2, vb = ext.embed(a), ext.embed(a), ext.embed(b)
        assert va.shape == (ext.dim,) and np.allclose(np.linalg.norm(va), 1, atol=1e-5)
        assert np.array_equal(va, va2) and float(va @ vb) < 0.999


def test_new_cards_can_be_added_without_retraining_and_are_found():
    ext = GridExtractor()
    index = RefIndex(ext.dim)
    index.add([(f"img{i}", f"card{i}", ext.embed(fake_card(i))) for i in range(5)])
    query_new = ext.embed(fake_card(99))
    assert index.search(query_new, 1)[0][0] != "img99"  # aún no existe en el índice
    index.add([("img99", "card99", query_new)])  # agregar = embeber + append, sin entrenar
    assert index.search(query_new, 1)[0][0] == "img99" and len(index) == 6
    with pytest.raises(ValueError):
        index.add([("bad", "cardx", np.ones(7, np.float32))])


def test_unknown_decision_uses_score_and_margin_but_variants_of_one_card_do_not_hurt():
    index = RefIndex(2)
    v = lambda x, y: np.array([x, y], np.float32) / np.hypot(x, y)  # noqa: E731
    index.add([("a-base", "A", v(1, 0)), ("a-foil", "A", v(1, 0.01)), ("b", "B", v(0, 1))])
    res = index.search(v(1, 0.002), 3)
    assert index.decide(res, 0.9, 0.2).status == "known"  # a-foil casi igual que a-base: misma carta, no cuenta
    far = index.search(v(1, 1), 3)
    assert index.decide(far, 0.95, 0.0).status == "unknown"  # similitud baja
    close_call = [("a-base", 0.96), ("b", 0.95)]
    assert index.decide(close_call, 0.9, 0.05).status == "unknown"  # otra carta muy cerca: ambiguo


def test_metrics_separate_exact_image_from_card_identity():
    index = RefIndex(2)
    index.add([("base", "A", np.array([1, 0], np.float32)), ("foil", "A", np.array([0, 1], np.float32))])
    queries = [
        {"detected": True, "truth_id": "base", "results": [("foil", 0.9), ("base", 0.8)], "corner_err": 0.01},
        {"detected": True, "truth_id": "base", "results": [("base", 0.9), ("foil", 0.8)], "corner_err": 0.01},
        {"detected": False, "truth_id": "base"},
    ]
    m = evaluate(queries, index, {}, {"A": 2})
    assert m["exact_image_top1"] == pytest.approx(1 / 3, abs=1e-3) and m["card_identity_top1"] == pytest.approx(
        2 / 3, abs=1e-3
    )
    assert m["exact_image_top5"] == pytest.approx(2 / 3, abs=1e-3) and m["detection_rate"] == pytest.approx(
        2 / 3, abs=1e-3
    )
    assert m["variant_exact_given_card_top1"] == {"n": 2, "accuracy": 0.5}


def row(folder, card, **kw):
    return InventoryRow(
        object_key=f"p/Pokemon_Images/{folder}/{card}/{card}.webp", kind="card", exclusion_reason=None, set_folder=folder,
        card_folder=card, filename=f"{card}.webp", extension="webp", etag="e", size=100, parsed=parse_card_folder(card), **kw,
    )  # fmt: skip


def test_sample_is_reproducible_and_keeps_pattern_variants_together():
    rows = [row("SV_ Black Bolt", f"Mon{i} (00{i}-086)") for i in range(1, 5)]
    for i in range(1, 5):
        rows += [
            row("SV_ Black Bolt", f"Mon{i} (Poke Ball Pattern) (00{i}-086)"),
            row("SV_ Black Bolt", f"Mon{i} (Master Ball Pattern) (00{i}-086)"),
        ]
    rows += [row(f"Set {s}", "Pikachu (025-100)") for s in "ABC"] + [
        row("Base Set", "Chansey (003-102)"),
        row("Misc Promos", "Eevee (SM01)"),
    ]
    first = select_sample(rows, seed=3)
    assert first == select_sample(rows, seed=3)
    pattern = [i for i in first if i["stratum"] == "pattern_variants"]
    assert len(pattern) == 12 and all(
        len([p for p in pattern if p["card_key"] == k]) == 3 for k in {p["card_key"] for p in pattern}
    )
    assert {"same_name_reprints", "old_sets"} <= {i["stratum"] for i in first}
    assert len({i["key"] for i in first}) == len(first)  # sin repetidos entre estratos
