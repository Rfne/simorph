"""Тесты сборки датасета, сплитов и аугментации (T10-01…T10-03, G10-01, G10-02).

Проверяется то, что заявлено в WP как свойство, а не как договорённость:
разметка кадра совпадает с маской, сплиты не пересекаются по сценам,
а сборка и аугментация воспроизводятся по seed.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from simorph.core.dataset import (
    CLASS_NAMES,
    FRAME_STEP_NS,
    AugmentConfig,
    DatasetConfig,
    SyntheticSceneSource,
    augment_frame,
    build_dataset,
    load_manifest,
    manifest_hash,
    plan_splits,
    read_frames,
    report_from_manifest,
    scene_seed,
)
from simorph.core.dataset.png import read_png, write_gray_png, write_rgb_png
from simorph.core.dataset.splits import split_of
from simorph.core.logs import open_store

pytestmark = pytest.mark.integration

#: Маленький датасет для тестов: две обучающие сцены и одна валидационная.
SMALL = DatasetConfig(scenes=2, frames_per_scene=6, val_scenes=1, seed=11, min_visibility=0.2)


@pytest.fixture(scope="module")
def dataset_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Собранный мини-датасет, общий для тестов модуля."""
    target = tmp_path_factory.mktemp("dataset") / "ds"
    build_dataset(target, SMALL)
    return target


def test_png_roundtrip_rgb_and_gray(tmp_path: Path) -> None:
    rgb = np.random.default_rng(1).integers(0, 256, (18, 24, 3), dtype=np.uint8)
    gray = np.random.default_rng(2).integers(0, 4, (18, 24), dtype=np.uint8)

    write_rgb_png(tmp_path / "rgb.png", rgb)
    write_gray_png(tmp_path / "gray.png", gray)

    assert np.array_equal(read_png(tmp_path / "rgb.png"), rgb)
    assert np.array_equal(read_png(tmp_path / "gray.png"), gray)


def test_scene_rendering_is_deterministic() -> None:
    first = SyntheticSceneSource(SMALL).render_scene(0)
    second = SyntheticSceneSource(SMALL).render_scene(0)

    assert first and len(first) == len(second)
    for left, right in zip(first, second, strict=True):
        assert np.array_equal(left.image, right.image)
        assert np.array_equal(left.mask, right.mask)
        assert [box.to_dict() for box in left.boxes] == [box.to_dict() for box in right.boxes]


def test_boxes_match_mask_pixels() -> None:
    """Рамка каждого объекта — точный охват его пикселей в маске (G10-01)."""
    frames = SyntheticSceneSource(SMALL).render_scene(0)
    assert frames

    for frame in frames:
        assert frame.mask is not None
        assert frame.image.shape[:2] == frame.mask.shape
        for box in frame.boxes:
            class_index = CLASS_NAMES.index(box.label) + 1
            x1, y1 = int(box.x1), int(box.y1)
            x2, y2 = int(box.x2), int(box.y2)
            assert 0 <= x1 <= x2 < frame.width
            assert 0 <= y1 <= y2 < frame.height
            inside = frame.mask[y1 : y2 + 1, x1 : x2 + 1] == class_index
            rows, columns = np.where(inside)
            assert rows.size >= SMALL.min_mask_px
            assert (rows.min(), rows.max()) == (0, y2 - y1)
            assert (columns.min(), columns.max()) == (0, x2 - x1)


def test_keypoints_and_timestamps() -> None:
    frames = SyntheticSceneSource(SMALL).render_scene(0)
    assert frames

    for index, frame in enumerate(frames):
        assert frame.t_ns == index * FRAME_STEP_NS
        for points in frame.keypoints.values():
            # Углы за пределами кадра отбрасываются: у объекта на краю
            # остаются только видимые вершины габарита.
            assert 1 <= len(points) <= 8
            for u, v, depth in points:
                assert 0.0 <= u < frame.width
                assert 0.0 <= v < frame.height
                assert depth > 0.0


def test_manifest_is_reproducible_by_seed(tmp_path: Path, dataset_dir: Path) -> None:
    """Один seed — один манифест, другой seed — другой (G10-01)."""
    again = build_dataset(tmp_path / "again", SMALL)
    other = build_dataset(tmp_path / "other", DatasetConfig(**{**SMALL.to_dict(), "seed": 12}))

    assert again.manifest_hash == report_from_manifest(dataset_dir).manifest_hash
    assert other.manifest_hash != again.manifest_hash


def test_manifest_hash_ignores_provenance(dataset_dir: Path) -> None:
    """Время сборки и номер прогона не меняют хэш содержимого (G10-01).

    Иначе две сборки с одним seed, разделённые секундой, давали бы разные
    хэши, и воспроизводимость зависела бы от часов.
    """
    manifest = load_manifest(dataset_dir)
    provenance = {**manifest, "created_at": "2000-01-01T00:00:00Z", "run_id": "other-run"}
    content = {
        **manifest,
        "counts": {**manifest["counts"], "frames": manifest["counts"]["frames"] + 1},
    }

    assert manifest_hash(provenance) == manifest_hash(manifest)
    assert manifest_hash(content) != manifest_hash(manifest)


def test_splits_have_no_scene_leak(dataset_dir: Path) -> None:
    """Кадры сцены не попадают в оба сплита (G10-02)."""
    manifest = load_manifest(dataset_dir)
    train = set(manifest["splits"]["train"])
    val = set(manifest["splits"]["val"])

    assert train and val
    assert not train & val
    seen: dict[int, str] = {}
    for record in manifest["frames"]:
        scene = int(record["scene_id"])
        split = str(record["split"])
        assert seen.setdefault(scene, split) == split
        assert scene in (train if split == "train" else val)


def test_split_plan_rejects_overlap() -> None:
    scene_ids = [scene_seed(SMALL.seed, index) for index in range(SMALL.scene_count)]
    plan = plan_splits(scene_ids, seed=SMALL.seed, val_scenes=1)

    assert set(plan.train).isdisjoint(plan.val)
    assert split_of(plan, plan.val[0]) == "val"
    assert split_of(plan, plan.train[0]) == "train"
    with pytest.raises(KeyError):
        split_of(plan, 999999)


def test_read_frames_roundtrip(dataset_dir: Path) -> None:
    manifest = load_manifest(dataset_dir)
    frames = read_frames(dataset_dir, split="val")

    assert len(frames) == manifest["counts"]["val"]
    assert all(frame.boxes for frame in frames)
    record = next(item for item in manifest["frames"] if item["split"] == "val")
    label = json.loads((dataset_dir / record["label"]).read_text(encoding="utf-8"))
    assert len(frames[0].boxes) == len(label["boxes"])
    assert frames[0].boxes[0].label in CLASS_NAMES


def test_augmentation_is_deterministic_by_seed(dataset_dir: Path) -> None:
    """Параметры и пиксели аугментации воспроизводимы по seed (T10-03)."""
    frame = read_frames(dataset_dir, split="train", limit=1)[0]
    config = AugmentConfig(seed=5)

    first, first_params = augment_frame(frame, config)
    second, second_params = augment_frame(frame, config)
    third, third_params = augment_frame(frame, AugmentConfig(seed=6))

    assert first is not None and second is not None and third is not None
    assert first_params == second_params
    assert np.array_equal(first.image, second.image)
    assert [box.to_dict() for box in first.boxes] == [box.to_dict() for box in second.boxes]
    assert first_params != third_params
    assert not np.array_equal(first.image, third.image)


def test_augmentation_drops_and_flips(dataset_dir: Path) -> None:
    frame = read_frames(dataset_dir, split="train", limit=1)[0]

    dropped, params = augment_frame(frame, AugmentConfig(seed=5, drop_probability=1.0))
    assert dropped is None
    assert params.dropped

    flipped, params = augment_frame(
        frame,
        AugmentConfig(
            seed=5,
            flip_probability=1.0,
            noise_sigma=0.0,
            gain_range=(1.0, 1.0),
            bias_range=(0.0, 0.0),
            blur_range=(0.0, 0.0),
            desync_ns=0,
        ),
    )
    assert flipped is not None
    assert params.flipped
    assert flipped.t_ns == frame.t_ns
    assert np.array_equal(flipped.image, frame.image[:, ::-1])
    for box, original in zip(flipped.boxes, frame.boxes, strict=True):
        assert box.x1 == frame.width - 1 - original.x2
        assert box.x2 == frame.width - 1 - original.x1


def test_dataset_run_is_written_to_journal(tmp_path: Path) -> None:
    """Сборка датасета попадает в журнал вместе с манифестом (G10-04)."""
    database = tmp_path / "run.sqlite"
    with open_store(database) as store:
        report = build_dataset(tmp_path / "ds", SMALL, store=store)

    assert report.run_id
    with open_store(database) as store:
        run = store.get_run(report.run_id)
        assert run is not None
        assert run["kind"] == "dataset"
        assert run["status"] == "finished"
        assert run["seed"] == SMALL.seed
        metrics = {item["name"]: item["value"] for item in store.metrics(report.run_id)}
        assert metrics["dataset.frames"] == report.frames
        assert metrics["dataset.train"] == report.splits["train"]
        assert metrics["dataset.class.tank"] == report.classes["tank"]
        artifacts = store.artifacts(report.run_id)
        assert [item["kind"] for item in artifacts] == ["manifest"]
