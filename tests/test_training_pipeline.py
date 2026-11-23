"""Тесты головы, тренера, оценки и экспорта ONNX (T10-04…T10-06, T10-08).

Модель маленькая, поэтому тесты обучают её по-настоящему, но на мини-наборе:
проверяется не «качество», а свойства — сходимость потери, сохранение и
возобновление чекпойнта, отмена, метрики в журнале и совпадение ONNX
с PyTorch в допуске гейта.
"""

from __future__ import annotations

from pathlib import Path
from threading import Event

import numpy as np
import pytest
import torch

from simorph.core.dataset import CLASS_NAMES, DatasetConfig, build_dataset, read_frames
from simorph.core.dataset.schema import Box
from simorph.core.logs import open_store
from simorph.core.training import (
    CHECKPOINT_NAME,
    DetectionHead,
    EvalReport,
    ModelSpec,
    OnnxReport,
    TrainConfig,
    Trainer,
    TrainReport,
    average_precision,
    decode_predictions,
    encode_targets,
    evaluate,
    export_and_verify,
    focal_loss,
    nms,
    recall_at_fppi,
    record_evaluation,
    record_training,
)

pytestmark = pytest.mark.integration

#: Мини-датасет: три сцены, из них одна валидационная.
SMALL = DatasetConfig(scenes=2, frames_per_scene=12, val_scenes=1, seed=21, min_visibility=0.2)

SPEC = ModelSpec(classes=CLASS_NAMES, width=96, height=72)


@pytest.fixture(scope="module")
def frames_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("training") / "ds"
    build_dataset(target, SMALL)
    return target


@pytest.fixture(scope="module")
def train_frames(frames_dir: Path) -> list:
    return read_frames(frames_dir, split="train")


def test_encode_targets_marks_center_cell() -> None:
    box = Box(label="tank", x1=20.0, y1=16.0, x2=40.0, y2=32.0)
    targets = encode_targets([box], SPEC)

    assert targets.heatmap.shape == (len(CLASS_NAMES), 18, 24)
    assert targets.positives >= 1
    class_index = CLASS_NAMES.index("tank")
    peak = np.unravel_index(int(np.argmax(targets.heatmap[class_index])), (18, 24))
    assert peak == (6, 7)
    assert targets.heatmap[class_index, 6, 7] == pytest.approx(1.0)
    assert targets.heatmap[CLASS_NAMES.index("pump"), 6, 7] == 0.0
    assert targets.mask[6, 7] == 1.0


def test_decode_roundtrip_restores_box() -> None:
    box = Box(label="pump", x1=20.0, y1=16.0, x2=40.0, y2=32.0)
    targets = encode_targets([box], SPEC)
    logits = torch.logit(torch.from_numpy(targets.heatmap).clamp(1e-4, 1.0 - 1e-4))

    decoded = decode_predictions(
        logits.unsqueeze(0),
        torch.from_numpy(targets.size).unsqueeze(0),
        torch.from_numpy(targets.offset).unsqueeze(0),
        SPEC,
        score_threshold=0.5,
    )

    assert decoded
    best = max(decoded, key=lambda item: item.score)
    assert best.label == "pump"
    assert best.x1 == pytest.approx(box.x1, abs=1.0)
    assert best.y2 == pytest.approx(box.y2, abs=1.0)


def test_focal_loss_prefers_confident_positive() -> None:
    target = torch.zeros((1, 18, 24))
    target[0, 6, 7] = 1.0
    confident = torch.full((1, 18, 24), -6.0)
    confident[0, 6, 7] = 6.0

    assert float(focal_loss(confident, target)) < float(
        focal_loss(torch.zeros_like(target), target)
    )


def test_nms_suppresses_duplicates() -> None:
    boxes = [
        Box("tank", 10.0, 10.0, 30.0, 30.0, score=0.9),
        Box("tank", 11.0, 11.0, 31.0, 31.0, score=0.8),
        Box("pump", 11.0, 11.0, 31.0, 31.0, score=0.7),
    ]

    kept = nms(boxes, iou_threshold=0.5)

    assert {(box.label, box.score) for box in kept} == {("tank", 0.9), ("pump", 0.7)}


def test_trainer_reduces_loss(train_frames: list, tmp_path: Path) -> None:
    model = DetectionHead(SPEC, seed=7)
    config = TrainConfig(epochs=10, batch_size=8, lr=2e-3, seed=7, threads=2)
    report = Trainer(model, config, workdir=tmp_path).fit(train_frames)

    assert report.finished
    assert report.epochs_done == 10
    assert report.history[-1].loss < report.history[0].loss
    assert Path(report.checkpoint or "").is_file()


def test_trainer_cancel_keeps_checkpoint(train_frames: list, tmp_path: Path) -> None:
    model = DetectionHead(SPEC, seed=7)
    cancel = Event()
    cancel.set()
    config = TrainConfig(epochs=5, batch_size=8, seed=7, threads=2)
    report = Trainer(model, config, workdir=tmp_path).fit(train_frames, cancel=cancel)

    assert report.cancelled
    assert not report.finished
    assert report.epochs_done == 0
    checkpoint = tmp_path / CHECKPOINT_NAME
    assert checkpoint.is_file()
    assert Path(report.checkpoint or "") == checkpoint


def test_checkpoint_roundtrip_preserves_outputs(train_frames: list, tmp_path: Path) -> None:
    model = DetectionHead(SPEC, seed=7)
    Trainer(model, TrainConfig(epochs=2, batch_size=8, seed=7, threads=2), workdir=tmp_path).fit(
        train_frames
    )
    restored, extra = DetectionHead.load(tmp_path / CHECKPOINT_NAME)

    assert extra["epoch"] == 2
    assert restored.spec == model.spec
    sample = (
        torch.from_numpy(np.ascontiguousarray(train_frames[0].image, dtype=np.float32) / 255.0)
        .permute(2, 0, 1)
        .unsqueeze(0)
    )
    with torch.no_grad():
        expected = [item.numpy() for item in model(sample)]
        actual = [item.numpy() for item in restored(sample)]
    for left, right in zip(actual, expected, strict=True):
        assert np.array_equal(left, right)


def test_resume_continues_from_epoch(train_frames: list, tmp_path: Path) -> None:
    model = DetectionHead(SPEC, seed=7)
    config = TrainConfig(epochs=3, batch_size=8, seed=7, threads=2)
    Trainer(model, config, workdir=tmp_path).fit(train_frames)

    resumed_model = DetectionHead(SPEC, seed=7)
    trainer = Trainer(
        resumed_model,
        TrainConfig(epochs=6, batch_size=8, seed=7, threads=2),
        workdir=tmp_path,
    )
    assert trainer.resume(tmp_path / CHECKPOINT_NAME) == 3
    report = trainer.fit(train_frames)

    assert report.epochs_done == 3
    assert report.resume_epoch == 6


def test_evaluate_reports_metrics(frames_dir: Path, train_frames: list, tmp_path: Path) -> None:
    model = DetectionHead(SPEC, seed=7)
    Trainer(model, TrainConfig(epochs=6, batch_size=8, seed=7, threads=2), workdir=tmp_path).fit(
        train_frames
    )
    report = evaluate(model, read_frames(frames_dir, split="val"), score_threshold=0.05)

    assert 0.0 <= report.map50 <= 1.0
    assert set(report.per_class) == set(CLASS_NAMES)
    assert set(report.recall_fppi) == {"0.1", "0.5", "1"}
    assert report.p95_latency_ms > 0.0
    assert report.frames > 0


def test_average_precision_and_recall_edges() -> None:
    assert average_precision([1, 1, 0], [0.9, 0.8, 0.1], positives=2) == pytest.approx(1.0)
    assert average_precision([], [], positives=0) == 0.0
    assert average_precision([0, 0], [0.9, 0.8], positives=2) == 0.0
    recall = recall_at_fppi([1, 1, 0], [0.9, 0.8, 0.1], positives=2, frames=2)
    assert recall["1"] == pytest.approx(1.0)


def test_onnx_matches_pytorch(tmp_path: Path) -> None:
    """ONNX совпадает с PyTorch в допуске 1e-4 (G10-05).

    Критерий — эталонная реализация ONNX, поэтому вердикт не зависит от
    свёрточных ядер конкретного CPU. Прогон в OpenVINO пишется отдельно.
    """
    model = DetectionHead(SPEC, seed=7)
    report = export_and_verify(model, tmp_path / "model.onnx")

    assert isinstance(report, OnnxReport)
    assert report.verified
    assert report.max_abs_diff <= 1e-4
    # OpenVINO — справочное число: его расхождение зависит от свёрточных
    # ядер конкретного CPU, поэтому величину не проверяем, только факт прогона.
    assert report.runtime in (None, "openvino")
    if report.runtime is not None:
        assert report.runtime_diff is not None
        assert np.isfinite(report.runtime_diff)
    assert report.input_shape == (1, 3, SPEC.height, SPEC.width)
    assert report.output_shapes == (
        (1, len(CLASS_NAMES), 18, 24),
        (1, 2, 18, 24),
        (1, 2, 18, 24),
    )


def test_journal_records_training_and_evaluation(tmp_path: Path, frames_dir: Path) -> None:
    """Метрики обучения и оценки читаются из БД по префиксам (G10-04)."""
    training = TrainReport(
        epochs_done=3,
        planned=3,
        cancelled=False,
        loss=1.25,
        checkpoint=None,
        resume_epoch=3,
    )
    evaluation = EvalReport(
        map50=0.61,
        per_class={"tank": 0.7, "pump": 0.6, "column": 0.53},
        recall_fppi={"0.1": 0.4, "0.5": 0.6, "1": 0.7},
        p50_latency_ms=2.0,
        p95_latency_ms=3.5,
        frames=10,
        detections=12,
        ground_truth=14,
        iou_threshold=0.5,
        score_threshold=0.05,
    )
    database = tmp_path / "run.sqlite"
    with open_store(database) as store:
        train_id = record_training(
            store,
            training,
            dataset_path=frames_dir,
            seed=21,
            config={"epochs": 3},
        )
        eval_id = record_evaluation(
            store,
            evaluation,
            dataset_path=frames_dir,
            model_path=tmp_path / "checkpoint.pt",
        )

    with open_store(database) as store:
        train_metrics = {item["name"]: item["value"] for item in store.metrics(train_id)}
        eval_metrics = {item["name"]: item["value"] for item in store.metrics(eval_id)}
        assert store.get_run(train_id)["kind"] == "training"  # type: ignore[index]
        assert store.get_run(eval_id)["kind"] == "evaluation"  # type: ignore[index]

    assert train_metrics["train.epochs_done"] == 3.0
    assert train_metrics["train.loss"] == pytest.approx(1.25)
    assert eval_metrics["eval.map50"] == pytest.approx(0.61)
    assert eval_metrics["eval.map50.column"] == pytest.approx(0.53)
    assert eval_metrics["eval.recall_fppi_0.5"] == pytest.approx(0.6)
    assert eval_metrics["eval.latency_p95_ms"] == pytest.approx(3.5)
