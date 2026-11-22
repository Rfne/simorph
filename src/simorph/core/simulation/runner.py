"""Headless-прогон миссии: часы, кадры, супервизор и метрики (T7-05, G7-01).

Раннер — единственный сценарий исполнения: его используют и CLI
(``simorph sim run``), и вкладка «Симуляция». Оконный режим добавляет
только визуализацию, поэтому метрики совпадают.

Один такт делает ровно одно и то же: выпускает кадр с метками времени,
пропускает его через инжекцию деградации, кладёт в кольцо, забирает
готовые кадры, продвигает часы и супервизор, пишет статистику. Такт
никогда не пропускается: при нехватке производительности растягивается
время PC, а не время сцены (мягкий масштаб, FR-SIM-16).

Сбой внутри такта не выходит наружу: прогон переводится в ``interrupted``,
поток кадров и журнал событий остаются читаемыми (FR-SIM-14, G7-07).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from typing import Any

from simorph.core.models.agent import AgentSection, Sensor
from simorph.core.models.exercise import Mission
from simorph.core.simulation.clock import SimClock
from simorph.core.simulation.degradation import Degradation, DegradationInjector
from simorph.core.simulation.events import EventKind, EventStream
from simorph.core.simulation.metrics import (
    DEFAULT_INTERVAL_S,
    RunMetrics,
    ScaleRecorder,
)
from simorph.core.simulation.replay import RunRecorder
from simorph.core.simulation.supervisor import MissionSupervisor, RunState
from simorph.core.simulation.transport import DEFAULT_SLOT_BYTES, FramePacket, FrameRing

__all__ = [
    "RunReport",
    "SimulationConfig",
    "SimulationRunner",
    "effective_state",
    "read_status",
]

#: Длительность прогона, если бюджет миссии пуст, с.
DEFAULT_DURATION_S: float = 10.0

#: Частота источника по умолчанию, Гц.
DEFAULT_SENSOR_HZ: float = 120.0


@dataclass(slots=True)
class SimulationConfig:
    """Параметры прогона: шаг, масштаб, транспорт, деградация, артефакты."""

    sensor_hz: float | None = None
    step_s: float | None = None
    scale: float = 1.0
    duration_s: float | None = None
    slots: int = 3
    slot_bytes: int = DEFAULT_SLOT_BYTES
    frame_bytes: int = 256
    work_ms: float = 0.0
    slowdown: float = 1.0
    realtime_drop: bool = False
    degradation: Degradation = field(default_factory=Degradation)
    record_path: Path | None = None
    report_path: Path | None = None
    interval_s: float = DEFAULT_INTERVAL_S
    stage_plan_ms: dict[str, float] | None = None
    pace: bool = True
    seed: int = 20261121

    def __post_init__(self) -> None:
        if self.sensor_hz is not None and self.sensor_hz <= 0.0:
            raise ValueError("частота источника должна быть положительной")
        if self.step_s is not None and self.step_s <= 0.0:
            raise ValueError("шаг физики должен быть положительным")
        if self.frame_bytes < 1:
            raise ValueError("размер кадра должен быть положительным")
        if self.work_ms < 0.0:
            raise ValueError("искусственная работа не может быть отрицательной")
        if self.slowdown <= 0.0:
            raise ValueError("замедление ступени должно быть положительным")
        if self.duration_s is not None and self.duration_s <= 0.0:
            raise ValueError("длительность прогона должна быть положительной")

    def to_dict(self) -> dict[str, Any]:
        return {
            "sensor_hz": self.sensor_hz,
            "step_s": self.step_s,
            "scale": self.scale,
            "duration_s": self.duration_s,
            "slots": self.slots,
            "slot_bytes": self.slot_bytes,
            "frame_bytes": self.frame_bytes,
            "work_ms": self.work_ms,
            "slowdown": self.slowdown,
            "realtime_drop": self.realtime_drop,
            "degradation": self.degradation.to_dict(),
            "record_path": None if self.record_path is None else str(self.record_path),
            "report_path": None if self.report_path is None else str(self.report_path),
            "interval_s": self.interval_s,
            "stage_plan_ms": None if self.stage_plan_ms is None else dict(self.stage_plan_ms),
            "pace": self.pace,
            "seed": self.seed,
        }


@dataclass(slots=True)
class RunReport:
    """Итог прогона: состояние, метрики, кадры и события."""

    mission_id: str
    agent_id: str
    state: str
    reason: str | None
    config: dict[str, Any]
    clock: dict[str, Any]
    supervisor: dict[str, Any]
    metrics: dict[str, Any]
    frames: dict[str, Any]
    events: list[dict[str, Any]]
    record: str | None = None
    status: str | None = None

    @property
    def ok(self) -> bool:
        return self.state == str(RunState.FINISHED)

    @property
    def loss_ratio(self) -> float:
        return float(self.frames.get("loss_ratio", 0.0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.ok else "error",
            "state": self.state,
            "reason": self.reason,
            "mission_id": self.mission_id,
            "agent_id": self.agent_id,
            "config": dict(self.config),
            "clock": dict(self.clock),
            "supervisor": dict(self.supervisor),
            "metrics": dict(self.metrics),
            "frames": dict(self.frames),
            "events": list(self.events),
            "record": self.record,
            "status_file": self.status,
        }

    def summary(self) -> str:
        """Короткая сводка для CLI и панели прогона."""
        frames = self.frames
        return (
            f"прогон {self.state}: миссия {self.mission_id} ({self.agent_id})"
            f" · сцена {self.clock.get('sim_time_s', 0.0):.1f} с"
            f" · PC {self.clock.get('wall_time_s', 0.0):.1f} с"
            f" · тактов {frames.get('attempted', 0)}"
            f" · кадров {frames.get('pushed', 0)}/{frames.get('consumed', 0)}"
            f" · потерь {frames.get('dropped', 0)}"
            f" ({self.loss_ratio * 100:.2f}%)"
            f" · k p50 {self.metrics.get('k', {}).get('p50', 1.0):.2f},"
            f" p95 {self.metrics.get('k', {}).get('p95', 1.0):.2f}"
            f" · событий {len(self.events)}"
        )


class SimulationRunner:
    """Headless-прогон: такты, кадры, состояния и артефакты."""

    def __init__(
        self,
        mission: Mission,
        *,
        agent: AgentSection | None = None,
        config: SimulationConfig | None = None,
        frame_source: Callable[[int, int], bytes] | None = None,
        now_ns: Callable[[], int] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.config = config if config is not None else SimulationConfig()
        self._mission = mission
        self._agent = agent
        self._frame_source = frame_source
        self._now = now_ns if now_ns is not None else time.perf_counter_ns
        self._sleep = sleep if sleep is not None else time.sleep

        sensor = _pick_sensor(agent)
        self.sensor_hz = float(
            self.config.sensor_hz
            if self.config.sensor_hz is not None
            else (sensor.rate_hz if sensor is not None else DEFAULT_SENSOR_HZ)
        )
        self.camera_id = sensor.id if sensor is not None else "sim.camera"
        self.frame_size = _frame_size(sensor)
        step_s = self.config.step_s if self.config.step_s is not None else 1.0 / self.sensor_hz
        self.clock = SimClock(
            step_s=step_s,
            scale=self.config.scale,
            realtime_drop=self.config.realtime_drop,
        )
        self.events = EventStream(clock_ns=self._now)
        self.supervisor = MissionSupervisor(mission, self.events, total_s=self._total_s())
        self.ring = FrameRing(slots=self.config.slots, slot_bytes=self.config.slot_bytes)
        self.injector = DegradationInjector(self.config.degradation)
        self.recorder = ScaleRecorder(self.config.interval_s)
        self.metrics = RunMetrics(
            realtime_drop=self.config.realtime_drop,
            degradation=self.injector.stats(),
        )
        self.stage_plan = self._stage_plan()
        self._slow_stage = "sensor" if "sensor" in self.stage_plan else next(iter(self.stage_plan))
        self._run_recorder = (
            RunRecorder(self.config.record_path, meta=self._stream_meta())
            if self.config.record_path is not None
            else None
        )
        self._state = RunState.IDLE
        self._frame_id = 0
        self._push_times: dict[int, int] = {}
        self._pending_wait_ms = 0.0
        self._stop = Event()
        self._pause = Event()
        self._lock = Lock()
        self._status_lock = Lock()
        self._report: RunReport | None = None

    # --- состояние ------------------------------------------------------

    @property
    def state(self) -> RunState:
        return self._state

    @property
    def report(self) -> RunReport | None:
        return self._report

    @property
    def status_path(self) -> Path | None:
        if self.config.report_path is None:
            return None
        target = Path(self.config.report_path)
        return target.with_name(f"{target.stem}.status.json")

    def pause(self) -> bool:
        """Пауза: такты прекращаются, состояние супервизора меняется."""
        if self.supervisor.state is not RunState.RUNNING:
            return False
        self._pause.set()
        self.clock.pause()
        self.supervisor.pause()
        self._state = self.supervisor.state
        self._write_status()
        return True

    def resume(self) -> bool:
        """Продолжение после паузы."""
        if self.supervisor.state is not RunState.PAUSED:
            return False
        self._pause.clear()
        self.supervisor.resume()
        self.clock.resume()
        self._state = self.supervisor.state
        self._write_status()
        return True

    def request_stop(self) -> None:
        """Просит прогон завершиться: поток останавливается на границе такта."""
        self._stop.set()

    def interrupt(self, reason: str) -> None:
        """Прерывает прогон и сохраняет артефакты."""
        self._stop.set()
        self.supervisor.interrupt(reason)
        self._write_status()

    def step_once(self) -> bool:
        """Один такт вручную (шаг вперёд) при паузе."""
        with self._lock:
            if self.supervisor.state is not RunState.PAUSED:
                return False
            self.supervisor.resume()
            self.clock.resume()
            previous = self.clock.sim_time_s
            completed = self._tick(previous, paced=False)
            self.clock.pause()
            self.supervisor.pause()
            if completed:
                self.supervisor.finish()
            self._state = self.supervisor.state
            self._write_status()
            return True

    # --- прогон ---------------------------------------------------------

    def run(self) -> RunReport:
        """Выполняет прогон до конца миссии или до сбоя."""
        if self._state is not RunState.IDLE:
            raise RuntimeError(f"прогон уже выполнялся: {self._state}")
        self.clock.start()
        self.supervisor.start()
        self._state = RunState.RUNNING
        self._write_status()
        previous = 0.0
        try:
            while not self._stop.is_set():
                self._wait_if_paused()
                if self._stop.is_set():
                    break
                completed = self._tick(previous, paced=True)
                previous = self.clock.sim_time_s
                self._state = self.supervisor.state
                if completed:
                    self.supervisor.finish()
                    break
            if self._stop.is_set() and self.supervisor.state not in (
                RunState.FINISHED,
                RunState.INTERRUPTED,
            ):
                self.supervisor.interrupt("прогон остановлен пользователем")
        except Exception as exc:  # сбой симуляции не должен ломать UI (FR-SIM-14)
            self.supervisor.interrupt(f"сбой симуляции: {exc}")
        finally:
            self._state = self.supervisor.state
            self._finalize()
        assert self._report is not None
        return self._report

    # --- такт -----------------------------------------------------------

    def _tick(self, previous_s: float, *, paced: bool) -> bool:
        """Один такт: кадр, транспорт, часы, супервизор и метрики."""
        started_ns = self._now()
        t_scene_s = self.clock.sim_time_s
        self._frame_id += 1
        frame_id = self._frame_id
        fsin_ns = round(t_scene_s * 1e9)
        packet = FramePacket(
            frame_id=frame_id,
            t_fsin_ns=fsin_ns,
            t_monotonic_ns=started_ns,
            camera_id=self.camera_id,
            width=self.frame_size[0],
            height=self.frame_size[1],
            payload=self._payload(frame_id, fsin_ns),
        )
        self._push_frame(packet, fsin_ns)
        self._consume_frames()

        if self.config.work_ms > 0.0:
            self._sleep(self.config.work_ms / 1000.0)
        work_ns = max(0, self._now() - started_ns)
        stages, wall_ms = self._stage_actuals(work_ns)
        wall_ns = round(wall_ms * 1e6)
        target_ns = round(self.clock.target_interval_s * 1e9)
        if paced and self.config.pace:
            wall_ns = max(wall_ns, target_ns)
            spent_ns = max(0, self._now() - started_ns)
            if wall_ns > spent_ns:
                self._sleep((wall_ns - spent_ns) / 1e9)
        self.clock.tick(wall_ns / 1e9)
        self.metrics.ticks = self.clock.ticks
        self.metrics.pc_time_s = self.clock.wall_time_s
        self.metrics.scene_time_s = self.clock.sim_time_s
        self.supervisor.advance(previous_s, self.clock.sim_time_s)
        self.recorder.observe_tick(
            stages,
            queue_depth=self.ring.depth,
            queue_wait_ms=self._pending_wait_ms,
        )
        sample = self.recorder.maybe_close(self.clock.sim_time_s, self.clock.wall_time_s)
        if sample is not None:
            self.metrics.intervals.append(sample)
        return self.supervisor.complete(self.clock.sim_time_s)

    def _push_frame(self, packet: FramePacket, fsin_ns: int) -> None:
        injected = self.injector.apply(packet)
        if injected is None:
            self.metrics.frames_dropped += 1
            self.events.emit(
                EventKind.FRAME_DROPPED,
                f"кадр {packet.frame_id} потерян при инжекции",
                t_scene_ns=fsin_ns,
                frame_id=packet.frame_id,
                cause="degradation",
            )
            return
        dropped_before = self.ring.dropped
        pushed = self.ring.push(injected, drop_oldest=self.config.realtime_drop)
        overwritten = self.ring.dropped - dropped_before
        if overwritten:
            self.metrics.frames_dropped += overwritten
            self.events.emit(
                EventKind.FRAME_DROPPED,
                f"перезаписано кадров: {overwritten}",
                t_scene_ns=fsin_ns,
                frame_id=packet.frame_id,
                cause="realtime_drop",
            )
        if not pushed:
            self.metrics.frames_dropped += 1
            self.events.emit(
                EventKind.FRAME_DROPPED,
                f"кадр {packet.frame_id} не поместился в кольцо",
                t_scene_ns=fsin_ns,
                frame_id=packet.frame_id,
                cause="full",
            )
            return
        self.metrics.frames_pushed += 1
        self._push_times[packet.frame_id] = self._now()
        self.events.emit(
            EventKind.FRAME_PUSHED,
            f"кадр {packet.frame_id}",
            t_scene_ns=fsin_ns,
            frame_id=packet.frame_id,
            camera_id=packet.camera_id,
            depth=self.ring.depth,
        )

    def _consume_frames(self) -> None:
        for item in self.ring.drain():
            self.metrics.frames_consumed += 1
            pushed_ns = self._push_times.pop(item.frame_id, item.t_monotonic_ns)
            wait_ms = max(0.0, (self._now() - pushed_ns) / 1e6)
            self._pending_wait_ms = max(self._pending_wait_ms, wait_ms)
            if self._run_recorder is not None:
                self._run_recorder.append(item)
            self.events.emit(
                EventKind.FRAME_CONSUMED,
                f"кадр {item.frame_id}",
                t_scene_ns=item.t_fsin_ns,
                frame_id=item.frame_id,
                wait_ms=round(wait_ms, 3),
            )

    # --- служебное ------------------------------------------------------

    def _wait_if_paused(self) -> None:
        while self._pause.is_set() and not self._stop.is_set():
            self._sleep(0.005)

    def _total_s(self) -> float:
        probe = MissionSupervisor(self._mission, self.events)
        if self.config.duration_s is not None:
            return float(self.config.duration_s)
        if probe.total_s > 0.0:
            return probe.total_s
        return DEFAULT_DURATION_S

    def _stage_plan(self) -> dict[str, float]:
        if self.config.stage_plan_ms is not None:
            plans = {name: float(value) for name, value in self.config.stage_plan_ms.items()}
            if not plans:
                raise ValueError("план ступеней пуст")
            if any(value <= 0.0 for value in plans.values()):
                raise ValueError("план ступени должен быть положительным")
            return plans
        return {"sensor": 1000.0 / self.sensor_hz}

    def _stage_actuals(self, work_ns: int) -> tuple[dict[str, tuple[float, float]], float]:
        """Раскладывает фактическое время по ступеням и добавляет замедление."""
        total_plan = sum(self.stage_plan.values())
        actual_total_ms = work_ns / 1e6
        stages: dict[str, tuple[float, float]] = {}
        wall_ms = actual_total_ms
        for name, plan in self.stage_plan.items():
            share = plan / total_plan
            actual_ms = actual_total_ms * share
            if name == self._slow_stage and self.config.slowdown != 1.0:
                extra = actual_ms * (self.config.slowdown - 1.0)
                actual_ms += extra
                wall_ms += extra
            stages[name] = (plan, actual_ms)
        return stages, wall_ms

    def _payload(self, frame_id: int, fsin_ns: int) -> bytes:
        if self._frame_source is not None:
            return self._frame_source(frame_id, fsin_ns)
        digest = hashlib.sha256(f"{self.config.seed}:{frame_id}:{fsin_ns}".encode()).digest()
        size = self.config.frame_bytes
        if size <= len(digest):
            return digest[:size]
        repeats = size // len(digest)
        return digest * repeats + digest[: size % len(digest)]

    def _stream_meta(self) -> dict[str, Any]:
        return {
            "mission_id": self._mission.mission_id,
            "agent_id": self._mission.agent_id,
            "sensor_hz": self.sensor_hz,
            "step_s": self.clock.step_s,
            "scale": self.clock.scale,
            "camera_id": self.camera_id,
            "width": self.frame_size[0],
            "height": self.frame_size[1],
            "seed": self.config.seed,
            "realtime_drop": self.config.realtime_drop,
            "degradation": self.config.degradation.to_dict(),
        }

    def _finalize(self) -> None:
        """Собирает остатки, закрывает поток и пишет отчёт."""
        self._consume_frames()
        sample = self.recorder.flush(self.clock.sim_time_s, self.clock.wall_time_s)
        if sample is not None:
            self.metrics.intervals.append(sample)
        self.metrics.ticks = self.clock.ticks
        self.metrics.pc_time_s = self.clock.wall_time_s
        self.metrics.scene_time_s = self.clock.sim_time_s
        self.metrics.queue = self.supervisor.queue.to_dict()
        self.metrics.queue["frame_max_depth"] = max(
            (item.queue_depth for item in self.metrics.intervals), default=0
        )
        self.metrics.queue["frame_max_wait_ms"] = round(
            max((item.queue_wait_ms for item in self.metrics.intervals), default=0.0), 3
        )
        self.metrics.degradation = self.injector.stats()
        if self._run_recorder is not None:
            self._run_recorder.close()
        self._report = self._build_report()
        if self.config.report_path is not None:
            _write_json(self.config.report_path, self._report.to_dict())
        self._write_status()

    def _build_report(self) -> RunReport:
        attempted = self.clock.ticks
        dropped = self.metrics.frames_dropped
        frames = {
            "attempted": attempted,
            "pushed": self.metrics.frames_pushed,
            "consumed": self.metrics.frames_consumed,
            "dropped": dropped,
            "loss_ratio": round(dropped / attempted, 6) if attempted else 0.0,
            "ring": self.ring.stats(),
        }
        return RunReport(
            mission_id=self._mission.mission_id,
            agent_id=self._mission.agent_id,
            state=str(self._state),
            reason=self.supervisor.reason,
            config=self.config.to_dict(),
            clock=self.clock.to_dict(),
            supervisor=self.supervisor.to_dict(),
            metrics=self.metrics.to_dict(),
            frames=frames,
            events=self.events.to_payload(),
            record=None if self.config.record_path is None else str(self.config.record_path),
            status=None if self.status_path is None else str(self.status_path),
        )

    def _write_status(self) -> None:
        path = self.status_path
        if path is None:
            return
        payload = {
            "state": str(self.supervisor.state),
            "reason": self.supervisor.reason,
            "pid": os.getpid(),
            "updated_ns": self._now(),
            "mission_id": self._mission.mission_id,
        }
        with self._status_lock:
            _write_json(path, payload)

    def close(self) -> None:
        """Освобождает кольцо и файл потока."""
        if self._run_recorder is not None:
            self._run_recorder.close()
        self.ring.close()


def _pick_sensor(agent: AgentSection | None) -> Sensor | None:
    if agent is None:
        return None
    for sensor in agent.sensors:
        if sensor.type in {"camera", "stereo_camera"}:
            return sensor
    return agent.sensors[0] if agent.sensors else None


def _frame_size(sensor: Sensor | None) -> tuple[int, int]:
    if sensor is None or sensor.resolution is None:
        return (640, 480)
    width, height = sensor.resolution
    return (int(width), int(height))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Пишет JSON атомарно: недописанный файл не попадёт на место готового."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def read_status(path: Path) -> dict[str, Any]:
    """Читает файл состояния прогона; отсутствие файла — ``interrupted``."""
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": str(RunState.INTERRUPTED), "reason": "файл состояния недоступен"}
    return payload if isinstance(payload, dict) else {"state": str(RunState.INTERRUPTED)}


def effective_state(status: dict[str, Any], *, alive: bool | None = None) -> str:
    """Состояние с учётом живости процесса.

    Если процесс умер, а состояние осталось ``running``, прогон считается
    прерванным: артефакты могли не дописаться, но журнал читаем (G7-07).
    """
    state = str(status.get("state", RunState.INTERRUPTED))
    if state == str(RunState.RUNNING) and alive is False:
        return str(RunState.INTERRUPTED)
    return state
