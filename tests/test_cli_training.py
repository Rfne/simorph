"""Тесты CLI этапа 10 и гейтов G10-03, G10-06 (T10-07).

Цикл ``dataset → train → eval → export-onnx`` проверяется целиком через
``main()``: если хотя бы один шаг потребует окна или ручного ввода, тест
упадёт. Гейт качества (mAP@0.5 ≥ 0.60) вынесен в отдельный медленный тест
на стандартном датасете — он и есть проверка G10-03.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simorph.cli.exit_codes import ExitCode
from simorph.cli.main import main
from simorph.core.dataset import CLASS_NAMES, DatasetConfig, build_dataset, read_frames
from simorph.core.logs import open_store
from simorph.core.training import DetectionHead, ModelSpec, TrainConfig, Trainer, evaluate

pytestmark = pytest.mark.integration

#: Мини-датасет для быстрых проверок CLI.
SMALL = DatasetConfig(scenes=2, frames_per_scene=8, val_scenes=1, seed=31, min_visibility=0.2)


@pytest.fixture(scope="module")
def dataset_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("cli_training") / "ds"
    build_dataset(target, SMALL)
    return target


def test_dataset_build_and_info_json(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "ds"
    database = tmp_path / "run.sqlite"

    code = main(
        [
            "dataset",
            "build",
            str(target),
            "--scenes",
            "2",
            "--frames",
            "6",
            "--seed",
            "31",
            "--log",
            str(database),
            "--json",
        ]
    )
    out, _ = capfd.readouterr()
    payload = json.loads(out)
    assert code == int(ExitCode.OK)
    assert payload["status"] == "ok"
    assert payload["frames"] > 0
    assert payload["classes"]["tank"] >= 0
    assert payload["run_id"]

    code = main(["dataset", "info", str(target), "--json"])
    out, _ = capfd.readouterr()
    info = json.loads(out)
    assert code == int(ExitCode.OK)
    assert info["manifest_hash"] == payload["manifest_hash"]
    assert info["frames"] == payload["frames"]


def test_dataset_info_without_manifest(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["dataset", "info", str(tmp_path / "missing")])
    _, err = capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert "манифест" in err


def test_dataset_without_action(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["dataset"])
    capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)


def test_train_without_dataset(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["train", str(tmp_path / "missing")])
    _, err = capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert "манифест" in err


def test_eval_without_checkpoint(dataset_dir: Path, capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["eval", str(dataset_dir), "--checkpoint", str(dataset_dir / "nope.pt")])
    _, err = capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert "чекпойнт" in err


def test_full_cycle_headless(
    dataset_dir: Path, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """Датасет → обучение → оценка → ONNX без GUI (G10-06)."""
    database = tmp_path / "run.sqlite"
    workdir = tmp_path / "training"

    code = main(
        [
            "dataset",
            "build",
            str(tmp_path / "ds"),
            "--scenes",
            "2",
            "--frames",
            "6",
            "--seed",
            "31",
            "--log",
            str(database),
        ]
    )
    assert code == int(ExitCode.OK)

    code = main(
        [
            "train",
            str(tmp_path / "ds"),
            "--epochs",
            "4",
            "--workdir",
            str(workdir),
            "--log",
            str(database),
        ]
    )
    out, _ = capfd.readouterr()
    assert code == int(ExitCode.OK)
    assert "обучение готово" in out
    checkpoint = workdir / "checkpoint.pt"
    assert checkpoint.is_file()

    code = main(
        ["eval", str(tmp_path / "ds"), "--checkpoint", str(checkpoint), "--log", str(database)]
    )
    out, _ = capfd.readouterr()
    assert code == int(ExitCode.OK)
    assert "mAP@0.5" in out

    code = main(["export-onnx", str(checkpoint), "-o", str(tmp_path / "model.onnx")])
    out, _ = capfd.readouterr()
    assert code == int(ExitCode.OK)
    assert "совпадает" in out
    assert (tmp_path / "model.onnx").is_file()

    with open_store(database) as store:
        kinds = [item["kind"] for item in store.list_runs(limit=10)]
    assert sorted(kinds) == ["dataset", "evaluation", "training"]


def test_train_resume_after_cancel(
    dataset_dir: Path, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    workdir = tmp_path / "training"
    code = main(["train", str(dataset_dir), "--epochs", "2", "--workdir", str(workdir), "--json"])
    out, _ = capfd.readouterr()
    assert code == int(ExitCode.OK)
    first = json.loads(out)
    assert first["epochs_done"] == 2

    code = main(
        [
            "train",
            str(dataset_dir),
            "--epochs",
            "4",
            "--workdir",
            str(workdir),
            "--resume",
            "--json",
        ]
    )
    out, _ = capfd.readouterr()
    second = json.loads(out)
    assert code == int(ExitCode.OK)
    assert second["epochs_done"] == 2
    assert second["resume_epoch"] == 4


@pytest.mark.perf
def test_gate_map50_on_standard_dataset(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    """Голова дообучается до mAP@0.5 ≥ 0.60 на валидации (G10-03)."""
    target = tmp_path / "ds"
    config = DatasetConfig()
    build_dataset(target, config)
    train_frames = read_frames(target, split="train")
    val_frames = read_frames(target, split="val")

    spec = ModelSpec(classes=CLASS_NAMES, width=config.width, height=config.height)
    model = DetectionHead(spec, seed=7)
    Trainer(
        model,
        TrainConfig(epochs=120, batch_size=16, lr=2e-3, seed=7, threads=2),
        workdir=tmp_path / "run",
    ).fit(train_frames)
    report = evaluate(model, val_frames, score_threshold=0.05)
    capfd.readouterr()

    assert report.map50 >= 0.60, report.summary()
