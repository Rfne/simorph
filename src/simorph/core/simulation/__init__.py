"""Симуляция: часы, транспорт кадров, супервизор, деградация и метрики (этап 7).

Пакет не зависит от Qt: те же классы использует CLI ``simorph sim``
и вкладка «Симуляция». Раннер — единственный сценарий исполнения, поэтому
оконный и headless-режимы дают одинаковые метрики.
"""

from __future__ import annotations

from simorph.core.simulation.clock import (
    DEFAULT_STEP_S,
    MAX_SCALE,
    MIN_SCALE,
    SimClock,
)
from simorph.core.simulation.degradation import (
    DEFAULT_SEED,
    MAX_DESYNC_MS,
    Degradation,
    DegradationInjector,
)
from simorph.core.simulation.events import EventKind, EventStream, SimEvent
from simorph.core.simulation.metrics import (
    DEFAULT_INTERVAL_S,
    IntervalSample,
    RunMetrics,
    ScaleRecorder,
    StageSample,
    histogram_buckets,
)
from simorph.core.simulation.replay import (
    STREAM_VERSION,
    ReplayReport,
    RunRecorder,
    read_stream,
    replay_stream,
    write_stream,
)
from simorph.core.simulation.runner import (
    RunReport,
    SimulationConfig,
    SimulationRunner,
    effective_state,
    read_status,
)
from simorph.core.simulation.supervisor import (
    ALLOWED_TRANSITIONS,
    BackgroundQueue,
    ElementWindow,
    MissionSupervisor,
    RunState,
    TransactionTimer,
)
from simorph.core.simulation.transport import (
    DEFAULT_SLOT_BYTES,
    MAX_SLOTS,
    MIN_SLOTS,
    FramePacket,
    FrameRing,
    payload_preview,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "DEFAULT_INTERVAL_S",
    "DEFAULT_SEED",
    "DEFAULT_SLOT_BYTES",
    "DEFAULT_STEP_S",
    "MAX_DESYNC_MS",
    "MAX_SCALE",
    "MAX_SLOTS",
    "MIN_SCALE",
    "MIN_SLOTS",
    "STREAM_VERSION",
    "BackgroundQueue",
    "Degradation",
    "DegradationInjector",
    "ElementWindow",
    "EventKind",
    "EventStream",
    "FramePacket",
    "FrameRing",
    "IntervalSample",
    "MissionSupervisor",
    "ReplayReport",
    "RunMetrics",
    "RunRecorder",
    "RunReport",
    "RunState",
    "ScaleRecorder",
    "SimClock",
    "SimEvent",
    "SimulationConfig",
    "SimulationRunner",
    "StageSample",
    "TransactionTimer",
    "effective_state",
    "histogram_buckets",
    "payload_preview",
    "read_status",
    "read_stream",
    "replay_stream",
    "write_stream",
]
