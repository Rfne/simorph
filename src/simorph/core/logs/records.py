"""Строки журнала и их сборка из отчётов прогонов (T9-02).

Одна запись — один прогон. Отчёт симуляции и отчёт конвейера имеют разную
структуру, но оба раскладываются в одни и те же таблицы: прогон, события,
ряды метрик и артефакты. Поэтому просмотр, фильтры и экспорт работают
одинаково для обоих видов прогонов.

Метрика — это ``(name, value, unit, t_ns, tags)``. Имя составное
(``stage.sensor.actual_ms``, ``node.inf.p95_ms``), поэтому фильтр по
метрике — это фильтр по префиксу или точному имени, а теги хранят
контекст (узел, тип узла, ступень) без отдельной таблицы.

Времена рядов — наносекунды времени сцены (``t_fsin_ns``), а события
несут и метку PC в ``payload_json``: так журнал стыкуется с симуляцией
и replay, не теряя тип целого числа.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

__all__ = [
    "AnnotationRow",
    "ArtifactRow",
    "EventRow",
    "MetricRow",
    "RunBundle",
    "RunRecord",
    "pipeline_bundle",
    "simulation_bundle",
]

#: Вид прогона в журнале.
RUN_KINDS: tuple[str, ...] = ("simulation", "pipeline", "dataset", "training", "evaluation")

#: Статусы прогона, которые пишет журнал.
RUN_STATUSES: tuple[str, ...] = ("running", "finished", "interrupted", "failed")


def utc_now() -> str:
    """Текущее время UTC в виде, который сортируется как строка."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class RunRecord:
    """Запись прогона в таблице ``runs``."""

    run_id: str
    kind: str
    status: str = "running"
    started_at: str = field(default_factory=utc_now)
    finished_at: str | None = None
    project_hash: str | None = None
    exercise_id: str | None = None
    mission_id: str | None = None
    pipeline_id: str | None = None
    seed: int | None = None
    reason: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    artifact_dir: str | None = None
    app_version: str | None = None

    def to_row(self) -> tuple[Any, ...]:
        return (
            self.run_id,
            self.project_hash,
            self.exercise_id,
            self.mission_id,
            self.pipeline_id,
            self.kind,
            self.seed,
            self.started_at,
            self.finished_at,
            self.status,
            self.reason,
            json.dumps(self.config, ensure_ascii=False, sort_keys=True),
            self.artifact_dir,
            self.app_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "project_hash": self.project_hash,
            "exercise_id": self.exercise_id,
            "mission_id": self.mission_id,
            "pipeline_id": self.pipeline_id,
            "kind": self.kind,
            "seed": self.seed,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "reason": self.reason,
            "config": dict(self.config),
            "artifact_dir": self.artifact_dir,
            "app_version": self.app_version,
        }


@dataclass(frozen=True, slots=True)
class EventRow:
    """Строка события ``run_events``."""

    t_ns: int
    source: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_row(self, run_id: str) -> tuple[Any, ...]:
        return (
            run_id,
            int(self.t_ns),
            self.source,
            self.type,
            json.dumps(self.payload, ensure_ascii=False, sort_keys=True, default=str),
        )


@dataclass(frozen=True, slots=True)
class MetricRow:
    """Строка ряда ``metrics``."""

    name: str
    value: float
    t_ns: int
    unit: str | None = None
    tags: dict[str, Any] = field(default_factory=dict)

    def to_row(self, run_id: str) -> tuple[Any, ...]:
        return (
            run_id,
            self.name,
            None if self.value is None else float(self.value),
            self.unit,
            int(self.t_ns),
            json.dumps(self.tags, ensure_ascii=False, sort_keys=True, default=str),
        )


@dataclass(frozen=True, slots=True)
class ArtifactRow:
    """Строка артефакта ``artifacts``."""

    kind: str
    path: str
    hash: str | None = None
    size_bytes: int | None = None

    def to_row(self, run_id: str) -> tuple[Any, ...]:
        return (run_id, self.kind, self.path, self.hash, self.size_bytes)

    @classmethod
    def from_path(cls, path: Path | str, *, kind: str) -> ArtifactRow:
        """Артефакт по файлу: путь, размер и sha256, если файл существует."""
        import hashlib

        target = Path(path)
        if not target.is_file():
            return cls(kind=kind, path=str(target))
        data = target.read_bytes()
        return cls(
            kind=kind,
            path=str(target),
            hash=f"sha256:{hashlib.sha256(data).hexdigest()}",
            size_bytes=len(data),
        )


@dataclass(frozen=True, slots=True)
class AnnotationRow:
    """Строка разметки ``annotations``."""

    frame_id: int | None = None
    class_id: str | None = None
    bbox: tuple[float, float, float, float] | tuple[()] = ()
    keypoints: tuple[tuple[float, float], ...] = ()
    source: str = "user"
    created_at: str = field(default_factory=utc_now)

    def to_row(self, run_id: str) -> tuple[Any, ...]:
        return (
            run_id,
            None if self.frame_id is None else int(self.frame_id),
            self.class_id,
            json.dumps(list(self.bbox), ensure_ascii=False),
            json.dumps([list(item) for item in self.keypoints], ensure_ascii=False),
            self.source,
            self.created_at,
        )


@dataclass(slots=True)
class RunBundle:
    """Полный набор строк одного прогона."""

    run: RunRecord
    events: list[EventRow] = field(default_factory=list)
    metrics: list[MetricRow] = field(default_factory=list)
    artifacts: list[ArtifactRow] = field(default_factory=list)
    annotations: list[AnnotationRow] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "events": len(self.events),
            "metrics": len(self.metrics),
            "artifacts": len(self.artifacts),
            "annotations": len(self.annotations),
        }


def _seed_from_config(config: dict[str, Any]) -> int | None:
    value = config.get("seed")
    return int(value) if isinstance(value, int) else None


def simulation_bundle(
    report: Any,
    *,
    run_id: str | None = None,
    project_hash: str | None = None,
    exercise_id: str | None = None,
    app_version: str | None = None,
) -> RunBundle:
    """Раскладывает отчёт симуляции в строки журнала.

    Метрики интервалов становятся рядами с меткой времени сцены: ``k``,
    ``stage.<имя>.{plan,actual,overrun}_ms``, ``queue_depth``,
    ``queue_wait_ms``. Сводные значения пишутся в конец прогона, чтобы
    фильтр по метрике не смешивал ряды и итоги.
    """
    config = dict(getattr(report, "config", {}) or {})
    run = RunRecord(
        run_id=run_id or uuid4().hex,
        kind="simulation",
        status="finished" if getattr(report, "ok", False) else "interrupted",
        project_hash=project_hash,
        exercise_id=exercise_id,
        mission_id=getattr(report, "mission_id", None),
        seed=_seed_from_config(config),
        reason=getattr(report, "reason", None),
        config=config,
        finished_at=utc_now(),
        app_version=app_version,
    )
    events: list[EventRow] = []
    for item in getattr(report, "events", []) or []:
        payload = {
            "seq": item.get("seq"),
            "t_monotonic_ns": item.get("t_monotonic_ns"),
            "message": item.get("message", ""),
            "data": item.get("data", {}),
        }
        events.append(
            EventRow(
                t_ns=int(item.get("t_scene_ns") or 0),
                source="simulation",
                type=str(item.get("kind", "event")),
                payload=payload,
            )
        )

    metrics: list[MetricRow] = []
    samples = (getattr(report, "metrics", {}) or {}).get("samples", []) or []
    last_ns = 0
    for sample in samples:
        t_ns = round(float(sample.get("scene_time_s", 0.0)) * 1e9)
        last_ns = max(last_ns, t_ns)
        tags = {"interval": sample.get("index")}
        metrics.append(MetricRow("k", float(sample.get("k", 1.0)), t_ns, "ratio", tags))
        metrics.append(
            MetricRow(
                "interval.wall_time_ms",
                float(sample.get("wall_time_s", 0.0)) * 1000.0,
                t_ns,
                "ms",
                tags,
            )
        )
        for stage, values in (sample.get("stages", {}) or {}).items():
            for suffix, unit in (("plan_ms", "ms"), ("actual_ms", "ms"), ("overrun_ms", "ms")):
                if suffix in values:
                    metrics.append(
                        MetricRow(
                            f"stage.{stage}.{suffix}",
                            float(values[suffix]),
                            t_ns,
                            unit,
                            {"stage": stage, **tags},
                        )
                    )
        metrics.append(
            MetricRow(
                "queue_depth",
                float(sample.get("queue_depth", 0)),
                t_ns,
                "frames",
                tags,
            )
        )
        metrics.append(
            MetricRow(
                "queue_wait_ms",
                float(sample.get("queue_wait_ms", 0.0)),
                t_ns,
                "ms",
                tags,
            )
        )

    summary = getattr(report, "metrics", {}) or {}
    k_block = summary.get("k", {}) or {}
    for name, value, unit in (
        ("k.p50", k_block.get("p50"), "ratio"),
        ("k.p95", k_block.get("p95"), "ratio"),
        ("k.max", k_block.get("max"), "ratio"),
        ("k.share_over_plan", k_block.get("share_over_plan"), "ratio"),
        ("frames.dropped", (summary.get("frames", {}) or {}).get("dropped"), "frames"),
        ("frames.pushed", (summary.get("frames", {}) or {}).get("pushed"), "frames"),
        ("frames.consumed", (summary.get("frames", {}) or {}).get("consumed"), "frames"),
    ):
        if isinstance(value, (int, float)):
            metrics.append(MetricRow(name, float(value), last_ns, unit, {"summary": True}))

    artifacts: list[ArtifactRow] = []
    record = getattr(report, "record", None)
    if record:
        artifacts.append(ArtifactRow.from_path(record, kind="stream"))
    status = getattr(report, "status", None)
    if status:
        artifacts.append(ArtifactRow.from_path(status, kind="status"))

    return RunBundle(run=run, events=events, metrics=metrics, artifacts=artifacts)


def pipeline_bundle(
    report: Any,
    *,
    run_id: str | None = None,
    project_hash: str | None = None,
    app_version: str | None = None,
) -> RunBundle:
    """Раскладывает отчёт конвейера в строки журнала.

    Ряды узлов именуются ``node.<id>.<поле>`` с тегами ``node`` и ``type``:
    по ним строятся срезы по узлу и сравнение плана с фактом. События
    несут ``t_fsin_ns`` в ``t_ns`` и метку PC в ``payload_json``.
    """
    config = dict(getattr(report, "config", {}) or {})
    metrics_block = getattr(report, "metrics", None)
    run = RunRecord(
        run_id=run_id or uuid4().hex,
        kind="pipeline",
        status="finished" if getattr(report, "ok", False) else "interrupted",
        project_hash=project_hash,
        pipeline_id=getattr(report, "pipeline_id", None),
        seed=_seed_from_config(config),
        reason=getattr(report, "reason", None),
        config=config,
        finished_at=utc_now(),
        app_version=app_version,
    )

    events: list[EventRow] = []
    last_ns = 0
    for item in getattr(report, "events", []) or []:
        t_ns = int(item.get("t_fsin_ns") or 0)
        last_ns = max(last_ns, t_ns)
        payload = {
            "frame_id": item.get("frame_id"),
            "node": item.get("node"),
            "node_type": item.get("node_type"),
            "t_monotonic_ns": item.get("t_monotonic_ns"),
            "latency_ms": item.get("latency_ms"),
            "cause": item.get("cause"),
        }
        events.append(
            EventRow(
                t_ns=t_ns,
                source="pipeline",
                type=str(item.get("kind", "event")),
                payload=payload,
            )
        )

    metrics: list[MetricRow] = []
    if metrics_block is not None:
        nodes = getattr(metrics_block, "nodes", {}) or {}
        for node_id, item in nodes.items():
            tags = {"node": node_id, "type": item.type}
            for suffix, value, unit in (
                ("plan_ms", item.plan_ms, "ms"),
                ("mean_ms", item.mean_ms, "ms"),
                ("p50_ms", item.p50_ms, "ms"),
                ("p95_ms", item.p95_ms, "ms"),
                ("max_ms", item.max_ms, "ms"),
                ("samples", float(item.count), "count"),
            ):
                metrics.append(
                    MetricRow(f"node.{node_id}.{suffix}", float(value), last_ns, unit, tags)
                )
        for name, value, unit in (
            ("pipeline.critical_path_ms", getattr(metrics_block, "critical_path_ms", 0.0), "ms"),
            ("pipeline.total_work_ms", metrics_block.total_work_ms, "ms"),
            ("frames.attempted", float(metrics_block.frames_attempted), "frames"),
            ("frames.completed", float(metrics_block.frames_completed), "frames"),
            ("frames.dropped", float(metrics_block.frames_dropped), "frames"),
            ("queue.max_depth", float(metrics_block.queue_max_depth), "frames"),
            ("queue.wait_ms", float(metrics_block.queue_wait_ms), "ms"),
        ):
            metrics.append(MetricRow(name, float(value), last_ns, unit, {"summary": True}))

    return RunBundle(run=run, events=events, metrics=metrics, artifacts=[])
