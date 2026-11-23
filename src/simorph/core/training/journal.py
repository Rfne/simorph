"""Запись обучения и оценки в журнал прогонов (T10-05, G10-04).

Обучение и оценка — такие же прогоны, как симуляция и конвейер, поэтому
они пишутся в тот же ``run.sqlite``: прогон, метрики и артефакты.
Отчёт этапа 11 читает метрики из журнала по имени ``eval.*``, а не из
stdout команды: цифра в отчёте и цифра в БД — одна и та же.

Имена метрик фиксированы: ``eval.map50``, ``eval.map50.<класс>``,
``eval.recall_fppi_<порог>``, ``eval.latency_p95_ms``. Префикс ``eval``
позволяет выбрать все метрики оценки одним фильтром.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from simorph.core.logs.records import ArtifactRow, MetricRow, RunRecord
from simorph.core.logs.store import RunStore
from simorph.core.training.evaluate import EvalReport
from simorph.core.training.trainer import TrainReport

__all__ = ["record_evaluation", "record_training"]

#: Префикс метрик обучения.
TRAIN_PREFIX = "train."

#: Префикс метрик оценки.
EVAL_PREFIX = "eval."


def _open_run(
    store: RunStore,
    *,
    kind: str,
    seed: int | None,
    config: dict[str, Any],
    artifact_dir: str | None,
    project_hash: str | None,
    app_version: str | None,
    run_id: str | None,
) -> str:
    record = RunRecord(
        run_id=run_id or uuid4().hex,
        kind=kind,
        status="running",
        project_hash=project_hash,
        seed=seed,
        config=config,
        artifact_dir=artifact_dir,
        app_version=app_version,
    )
    store.start_run(record)
    return record.run_id


def record_training(
    store: RunStore,
    report: TrainReport,
    *,
    dataset_path: Path | str,
    seed: int,
    config: dict[str, Any],
    project_hash: str | None = None,
    app_version: str | None = None,
    run_id: str | None = None,
) -> str:
    """Пишет прогон обучения: метрики эпох, чекпойнт и манифест датасета."""
    manifest = Path(dataset_path) / "manifest.json"
    identifier = _open_run(
        store,
        kind="training",
        seed=seed,
        config=dict(config),
        artifact_dir=str(Path(dataset_path)),
        project_hash=project_hash,
        app_version=app_version,
        run_id=run_id,
    )
    t_ns = int(time.time_ns())
    metrics = [
        MetricRow(name=f"{TRAIN_PREFIX}epochs_done", value=float(report.epochs_done), t_ns=t_ns),
        MetricRow(name=f"{TRAIN_PREFIX}loss", value=float(report.loss), t_ns=t_ns),
        MetricRow(
            name=f"{TRAIN_PREFIX}cancelled",
            value=1.0 if report.cancelled else 0.0,
            t_ns=t_ns,
        ),
        MetricRow(name=f"{TRAIN_PREFIX}seconds", value=float(report.seconds), t_ns=t_ns, unit="s"),
    ]
    metrics.extend(
        MetricRow(
            name=f"{TRAIN_PREFIX}epoch.{item.epoch}.loss",
            value=float(item.loss),
            t_ns=t_ns,
        )
        for item in report.history
    )
    store.write_metrics(identifier, metrics)
    artifacts: list[ArtifactRow] = []
    if report.checkpoint is not None:
        artifacts.append(ArtifactRow.from_path(report.checkpoint, kind="checkpoint"))
    if manifest.is_file():
        artifacts.append(ArtifactRow.from_path(manifest, kind="manifest"))
    if artifacts:
        store.add_artifacts(identifier, artifacts)
    store.finish_run(identifier, status="interrupted" if report.cancelled else "finished")
    return identifier


def record_evaluation(
    store: RunStore,
    report: EvalReport,
    *,
    dataset_path: Path | str,
    model_path: Path | str,
    seed: int | None = None,
    project_hash: str | None = None,
    app_version: str | None = None,
    run_id: str | None = None,
) -> str:
    """Пишет прогон оценки: mAP, recall@FPPI, задержку и модель."""
    identifier = _open_run(
        store,
        kind="evaluation",
        seed=seed,
        config={
            "dataset": str(dataset_path),
            "model": str(model_path),
            "iou_threshold": report.iou_threshold,
            "score_threshold": report.score_threshold,
        },
        artifact_dir=str(Path(dataset_path)),
        project_hash=project_hash,
        app_version=app_version,
        run_id=run_id,
    )
    t_ns = int(time.time_ns())
    metrics = [
        MetricRow(name=f"{EVAL_PREFIX}map50", value=float(report.map50), t_ns=t_ns),
        MetricRow(
            name=f"{EVAL_PREFIX}latency_p50_ms",
            value=float(report.p50_latency_ms),
            t_ns=t_ns,
            unit="ms",
        ),
        MetricRow(
            name=f"{EVAL_PREFIX}latency_p95_ms",
            value=float(report.p95_latency_ms),
            t_ns=t_ns,
            unit="ms",
        ),
        MetricRow(name=f"{EVAL_PREFIX}frames", value=float(report.frames), t_ns=t_ns),
        MetricRow(name=f"{EVAL_PREFIX}detections", value=float(report.detections), t_ns=t_ns),
        MetricRow(name=f"{EVAL_PREFIX}ground_truth", value=float(report.ground_truth), t_ns=t_ns),
    ]
    metrics.extend(
        MetricRow(name=f"{EVAL_PREFIX}map50.{name}", value=float(value), t_ns=t_ns)
        for name, value in report.per_class.items()
    )
    metrics.extend(
        MetricRow(name=f"{EVAL_PREFIX}recall_fppi_{name}", value=float(value), t_ns=t_ns)
        for name, value in report.recall_fppi.items()
    )
    store.write_metrics(identifier, metrics)
    store.add_artifacts(
        identifier,
        [ArtifactRow.from_path(model_path, kind="checkpoint")],
    )
    store.finish_run(identifier, status="finished")
    return identifier
