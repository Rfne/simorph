"""Тесты транспорта кадров: кольцо и метки времени (T7-02, G7-02, G7-03)."""

from __future__ import annotations

import pytest

from simorph.core.simulation.transport import (
    MAX_SLOTS,
    MIN_SLOTS,
    FramePacket,
    FrameRing,
    payload_preview,
)


def _packet(
    frame_id: int = 1, *, payload: bytes = b"frame", fsin: int = 100, mono: int = 200
) -> FramePacket:
    return FramePacket(
        frame_id=frame_id,
        t_fsin_ns=fsin,
        t_monotonic_ns=mono,
        camera_id="cam_stereo",
        width=64,
        height=48,
        payload=payload,
    )


def test_checksum_covers_header_and_payload() -> None:
    packet = _packet()
    assert len(packet.checksum) == 64
    assert packet.checksum == _packet().checksum
    assert packet.checksum != _packet(payload=b"other").checksum
    assert packet.checksum != _packet(mono=201).checksum


def test_with_timing_keeps_identity() -> None:
    packet = _packet()
    changed = packet.with_timing(t_monotonic_ns=999, payload=b"new")

    assert changed.frame_id == packet.frame_id
    assert changed.t_fsin_ns == packet.t_fsin_ns
    assert changed.t_monotonic_ns == 999
    assert changed.payload == b"new"
    assert changed.checksum != packet.checksum


def test_ring_preserves_order_and_marks_empty() -> None:
    ring = FrameRing(slots=3, slot_bytes=128)
    try:
        for frame_id in (1, 2, 3):
            assert ring.push(_packet(frame_id)) is True
        assert ring.depth == 3
        assert [item.frame_id for item in ring.drain()] == [1, 2, 3]
        assert ring.pop() is None
        stats = ring.stats()
        assert stats["pushed"] == 3
        assert stats["dropped"] == 0
    finally:
        ring.close()


def test_ring_rejects_when_full_without_drop_mode() -> None:
    ring = FrameRing(slots=2, slot_bytes=128)
    try:
        assert ring.push(_packet(1)) is True
        assert ring.push(_packet(2)) is True
        assert ring.push(_packet(3)) is False
        assert ring.depth == 2
        assert ring.dropped == 0
        assert [item.frame_id for item in ring.drain()] == [1, 2]
    finally:
        ring.close()


def test_ring_overwrites_oldest_only_when_allowed() -> None:
    ring = FrameRing(slots=2, slot_bytes=128)
    try:
        ring.push(_packet(1))
        ring.push(_packet(2))
        assert ring.push(_packet(3), drop_oldest=True) is True
        assert ring.dropped == 1
        assert ring.depth == 2
        assert [item.frame_id for item in ring.drain()] == [2, 3]
    finally:
        ring.close()


def test_ring_truncates_payload_to_slot() -> None:
    ring = FrameRing(slots=2, slot_bytes=64)
    try:
        ring.push(_packet(payload=b"x" * 200))
        packet = ring.pop()
        assert packet is not None
        assert len(packet.payload) == 64
        assert packet.camera_id == "cam_stereo"
    finally:
        ring.close()


def test_ring_validates_slots() -> None:
    with pytest.raises(ValueError):
        FrameRing(slots=MIN_SLOTS - 1)
    with pytest.raises(ValueError):
        FrameRing(slots=MAX_SLOTS + 1)
    with pytest.raises(ValueError):
        FrameRing(slots=2, slot_bytes=8)


def test_payload_preview_is_base64() -> None:
    assert payload_preview(b"abc") == "YWJj"
    assert len(payload_preview(b"abcdef", limit=2)) == 4
