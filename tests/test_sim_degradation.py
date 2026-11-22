"""Тесты инжекции деградации (T7-06, G7-05)."""

from __future__ import annotations

import pytest

from simorph.core.simulation.degradation import (
    MAX_DESYNC_MS,
    Degradation,
    DegradationInjector,
)
from simorph.core.simulation.transport import FramePacket


def _packet() -> FramePacket:
    return FramePacket(
        frame_id=7,
        t_fsin_ns=1_000,
        t_monotonic_ns=2_000,
        camera_id="cam_stereo",
        width=64,
        height=48,
        payload=b"frame",
    )


def test_inactive_degradation_passes_packet_through() -> None:
    injector = DegradationInjector()
    packet = _packet()

    assert injector.apply(packet) is packet
    assert injector.applied == 0
    assert injector.stats()["config"]["active"] is False


def test_full_loss_drops_every_frame() -> None:
    injector = DegradationInjector(Degradation(frame_loss_ratio=1.0))

    assert injector.apply(_packet()) is None
    assert injector.lost == 1
    assert injector.applied == 0


def test_latency_and_desync_shift_monotonic_label() -> None:
    injector = DegradationInjector(Degradation(sensor_latency_ms=5.0, desync_ms=0.25))
    result = injector.apply(_packet())

    assert result is not None
    assert result.t_monotonic_ns == 2_000 + 5_000_000 + 250_000
    assert injector.max_desync_ns == 250_000


def test_jitter_changes_payload_and_is_counted() -> None:
    injector = DegradationInjector(Degradation(exposure_jitter_us=20.0))
    result = injector.apply(_packet())

    assert result is not None
    assert result.payload != _packet().payload
    assert len(result.payload) == len(_packet().payload) + 4
    assert injector.max_jitter_us <= 20.0


def test_seed_makes_injection_reproducible() -> None:
    config = Degradation(frame_loss_ratio=0.5, exposure_jitter_us=10.0, seed=7)
    first = DegradationInjector(config)
    second = DegradationInjector(config)

    outcomes = [(first.apply(_packet()), second.apply(_packet())) for _ in range(20)]
    assert [item is None for item, _ in outcomes] == [item is None for _, item in outcomes]


def test_degradation_is_validated() -> None:
    with pytest.raises(ValueError):
        Degradation(sensor_latency_ms=-1.0)
    with pytest.raises(ValueError):
        Degradation(frame_loss_ratio=1.5)
    with pytest.raises(ValueError):
        Degradation(desync_ms=MAX_DESYNC_MS + 0.1)
    with pytest.raises(ValueError):
        Degradation(exposure_jitter_us=-1.0)


def test_stats_report_config_and_counters() -> None:
    injector = DegradationInjector(Degradation(frame_loss_ratio=1.0))
    injector.apply(_packet())
    stats = injector.stats()

    assert stats["lost"] == 1
    assert stats["applied"] == 0
    assert stats["config"]["frame_loss_ratio"] == pytest.approx(1.0)
