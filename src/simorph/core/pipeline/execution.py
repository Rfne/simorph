"""Исполнение конвейера: кадры, узлы, очереди и метрики (T8-02, T8-03, T8-06).

Раннер проходит граф в топологическом порядке и для каждого кадра
выполняет все узлы. Узел получает метку ``t_monotonic_ns`` на входе и
возвращает её на выходе, поэтому задержка измеряется без внешних часов,
а журнал событий стыкуется с потоком симуляции.

Модель времени двухслойная. Плановая стоимость кадра — сумма бюджетов
узлов; фактическая задержка узла равна плану (или задержке бэкенда
инференса). Очередь считается по времени сцены: если конвейер не успевает
за частотой источника, число кадров в работе растёт, а при явном
``realtime_drop`` лишние кадры отбрасываются. По умолчанию отбрасывания
нет: мягкий масштаб сохраняет каждый кадр, как и в симуляции.

Сбой внутри прогона не выходит наружу: отчёт получает состояние
``interrupted`` и причину, а уже собранные метрики остаются читаемыми.
"""

from __future__ import annotations

import hashlib
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from simorph.core.errors import ProjectError
from simorph.core.issues import Severity
from simorph.core.models.pipeline import PipelineNode, PipelineSection
from simorph.core.pipeline.backends import InferenceBackend, RoiChannel, RoiMessage
from simorph.core.pipeline.frames import FramePayload
from simorph.core.pipeline.graph import PipelineGraph, build_pipeline_graph
from simorph.core.pipeline.metrics import NodeLatency, PipelineMetrics, PipelineRunReport
from simorph.core.pipeline.nodes import NODE_SPECS, node_budget_ms
from simorph.core.pipeline.validation import pipeline_issues

__all__ = ["PipelineConfig", "PipelineRunner"]

#: Частота источника по умолчанию, Гц.
DEFAULT_FPS: float = 120.0

#: Число кадров прогона по умолчанию.
DEFAULT_FRAMES: int = 120

#: Размер полезной нагрузки кадра по умолчанию, байт.
DEFAULT_FRAME_BYTES: int = 256

_KNOWN_TYPES = frozenset(spec.type for spec in NODE_SPECS)


@dataclass(slots=True)
class PipelineConfig:
    """Параметры прогона конвейера."""

    frames: int = DEFAULT_FRAMES
    fps: float = DEFAULT_FPS
    seed: int = 20261122
    max_queue: int = 4
    realtime_drop: bool = False
    work_scale: float = 1.0
    measure: bool = False
    frame_bytes: int = DEFAULT_FRAME_BYTES
    emit_events: bool = True
    backend: InferenceBackend | None = None
    roi: RoiChannel | None = None

    def __post_init__(self) -> None:
        if self.frames < 1:
            raise ValueError("число кадров должно быть положительным")
        if self.fps <= 0.0:
            raise ValueError("частота источника должна быть положительной")
        if self.max_queue < 0:
            raise ValueError("глубина очереди не может быть отрицательной")
        if self.work_scale <= 0.0:
            raise ValueError("масштаб работы должен быть положительным")
        if self.frame_bytes < 1:
            raise ValueError("размер кадра должен быть положительным")

    def to_dict(self) -> dict[str, Any]:
        return {
            "frames": self.frames,
            "fps": self.fps,
            "seed": self.seed,
            "max_queue": self.max_queue,
            "realtime_drop": self.realtime_drop,
            "work_scale": self.work_scale,
            "measure": self.measure,
            "frame_bytes": self.frame_bytes,
            "emit_events": self.emit_events,
            "backend": None if self.backend is None else self.backend.name,
            "roi": None if self.roi is None else self.roi.stats(),
        }


class PipelineRunner:
    """Прогон конвейера по кадрам."""

    def __init__(
        self,
        pipeline: PipelineSection,
        config: PipelineConfig | None = None,
        *,
        now_ns: Callable[[], int] | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.config = config if config is not None else PipelineConfig()
        self._now = now_ns if now_ns is not None else time.perf_counter_ns
        self.graph: PipelineGraph = build_pipeline_graph(pipeline)
        self.metrics = PipelineMetrics(
            pipeline_id=pipeline.pipeline_id,
            kind=pipeline.kind,
            critical_path=list(self.graph.critical_path),
            critical_path_ms=self.graph.critical_path_ms,
        )
        self._events: list[dict[str, Any]] = []
        self._state = "idle"
        self._reason: str | None = None
        self._report: PipelineRunReport | None = None

    @property
    def state(self) -> str:
        return self._state

    @property
    def report(self) -> PipelineRunReport | None:
        return self._report

    def run(self) -> PipelineRunReport:
        """Выполняет прогон и возвращает отчёт."""
        if self._state != "idle":
            raise RuntimeError(f"прогон уже выполнялся: {self._state}")
        self._check_ready()
        try:
            self._run_frames()
        except Exception as exc:  # сбой прогона не должен ломать UI
            self._state = "interrupted"
            self._reason = f"сбой конвейера: {exc}"
        else:
            self._state = "finished"
            self._reason = None
        self._report = PipelineRunReport(
            pipeline_id=self.pipeline.pipeline_id,
            kind=self.pipeline.kind,
            state=self._state,
            reason=self._reason,
            config=self.config.to_dict(),
            metrics=self.metrics,
            events=list(self._events),
        )
        return self._report

    def close(self) -> None:
        """Фиксирует снимок канала ROI, если он был передан."""
        if self.config.roi is not None:
            self.metrics.roi = self.config.roi.stats()

    # --- проверки --------------------------------------------------------

    def _check_ready(self) -> None:
        errors = [
            item
            for item in pipeline_issues(self.pipeline, prefix="pipeline")
            if item.severity is Severity.ERROR
        ]
        if errors:
            detail = "; ".join(f"{item.path}: {item.message}" for item in errors)
            raise ProjectError(f"конвейер не проходит валидацию: {detail}")
        if not self.graph.order:
            raise ProjectError("в конвейере нет исполняемых узлов")

    # --- прогон ----------------------------------------------------------

    def _run_frames(self) -> None:
        period_ms = 1000.0 / self.config.fps
        total_work_ms = self._total_work_ms()
        completion_times: deque[float] = deque()
        camera_id, width, height = self._source_format()
        self.metrics.nodes = {
            node_id: NodeLatency(
                node_id=node_id,
                type=self.graph.by_id[node_id].type,
                plan_ms=node_budget_ms(self.graph.by_id[node_id]) * self.config.work_scale,
            )
            for node_id in self.graph.order
            if self.graph.by_id[node_id].type in _KNOWN_TYPES
        }

        for frame_id in range(1, self.config.frames + 1):
            scene_ms = (frame_id - 1) * period_ms
            while completion_times and completion_times[0] <= scene_ms:
                completion_times.popleft()
            inflight = len(completion_times)
            self.metrics.queue_max_depth = max(self.metrics.queue_max_depth, inflight)
            if self.config.max_queue and inflight >= self.config.max_queue:
                if self.config.realtime_drop:
                    self.metrics.frames_dropped += 1
                    self._emit(
                        "frame_dropped",
                        frame_id=frame_id,
                        t_fsin_ns=round(scene_ms * 1e6),
                        t_monotonic_ns=self._now(),
                        cause="queue_full",
                    )
                    continue
                self.metrics.queue_wait_ms += max(0.0, completion_times[0] - scene_ms)
            completion_times.append(scene_ms + total_work_ms)
            self.metrics.frames_attempted += 1
            self._run_frame(frame_id, scene_ms, camera_id, width, height)

    def _run_frame(
        self,
        frame_id: int,
        scene_ms: float,
        camera_id: str,
        width: int,
        height: int,
    ) -> None:
        fsin_ns = round(scene_ms * 1e6)
        started_ns = self._now()
        frame = FramePayload(
            frame_id=frame_id,
            t_fsin_ns=fsin_ns,
            t_monotonic_ns=started_ns,
            camera_id=camera_id,
            width=width,
            height=height,
            data=self._payload(frame_id, fsin_ns),
        )
        t_in_ns = started_ns
        total_ms = 0.0
        for node_id in self.graph.order:
            node = self.graph.by_id[node_id]
            if node.type not in _KNOWN_TYPES:
                continue
            actual_ms = self._node_latency(node, frame)
            total_ms += actual_ms
            t_out_ns = t_in_ns + round(actual_ms * 1e6)
            if t_out_ns < t_in_ns:
                self.metrics.monotonic = False
            self.metrics.nodes[node_id].add(actual_ms)
            if node.type == "roi" and self.config.roi is not None:
                self.config.roi.publish(
                    RoiMessage(
                        frame_id=frame_id,
                        t_monotonic_ns=t_out_ns,
                        rois=((0.0, 0.0, float(width), float(height)),),
                    )
                )
            self._emit(
                "node_finished",
                frame_id=frame_id,
                node=node_id,
                node_type=node.type,
                t_fsin_ns=fsin_ns,
                t_monotonic_ns=t_out_ns,
                latency_ms=round(actual_ms, 3),
            )
            t_in_ns = t_out_ns
        self.metrics.frames_completed += 1
        self._emit(
            "frame_completed",
            frame_id=frame_id,
            t_fsin_ns=fsin_ns,
            t_monotonic_ns=t_in_ns,
            latency_ms=round(total_ms, 3),
        )

    def _node_latency(self, node: PipelineNode, frame: FramePayload) -> float:
        plan_ms = node_budget_ms(node) * self.config.work_scale
        if node.type == "inference" and self.config.backend is not None:
            result = self.config.backend.infer(frame)
            return max(0.0, result.latency_ms)
        if self.config.measure:
            started = self._now()
            elapsed = (self._now() - started) / 1e6
            return max(plan_ms, elapsed)
        return plan_ms

    def _total_work_ms(self) -> float:
        return sum(
            node_budget_ms(self.graph.by_id[node_id]) * self.config.work_scale
            for node_id in self.graph.order
            if self.graph.by_id[node_id].type in _KNOWN_TYPES
        )

    def _source_format(self) -> tuple[str, int, int]:
        for node_id in self.graph.sources:
            node = self.graph.by_id.get(node_id)
            if node is None or node.type not in {"source.frame", "source.stereo"}:
                continue
            camera = str(node.params.get("camera", "sim.camera"))
            width = int(node.params.get("width", 1280 if node.type == "source.stereo" else 640))
            height = int(node.params.get("height", 800 if node.type == "source.stereo" else 480))
            return camera, width, height
        return "sim.camera", 640, 480

    def _payload(self, frame_id: int, fsin_ns: int) -> bytes:
        digest = hashlib.sha256(f"{self.config.seed}:{frame_id}:{fsin_ns}".encode()).digest()
        size = self.config.frame_bytes
        if size <= len(digest):
            return digest[:size]
        repeats = size // len(digest)
        return digest * repeats + digest[: size % len(digest)]

    def _emit(self, kind: str, **payload: Any) -> None:
        if not self.config.emit_events:
            return
        self._events.append({"kind": kind, **payload})
