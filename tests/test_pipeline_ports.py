"""Тесты портов конвейера и совместимости типов (T8-01, G8-01)."""

from __future__ import annotations

import pytest

from simorph.core.pipeline import (
    PORT_KINDS,
    PORT_TITLES,
    PortKind,
    accepts,
    format_port_ref,
    parse_port_ref,
    port_title,
)


def test_registry_has_twelve_unique_kinds() -> None:
    assert len(PORT_KINDS) == 12
    assert len(set(PORT_KINDS)) == 12
    assert set(PORT_KINDS) == set(PortKind)


def test_every_kind_has_russian_title() -> None:
    assert set(PORT_TITLES) == set(PortKind)
    assert all(PORT_TITLES[kind].strip() for kind in PortKind)


def test_events_accepts_every_kind() -> None:
    for kind in PortKind:
        assert accepts(PortKind.EVENTS, kind) is True


def test_mono_frame_accepts_only_mono() -> None:
    assert accepts(PortKind.FRAME_MONO, PortKind.FRAME_MONO) is True
    assert accepts(PortKind.FRAME_MONO, PortKind.FRAME_COLOR) is False
    assert accepts(PortKind.FRAME_MONO, PortKind.DEPTH) is False


def test_color_frame_accepts_mono() -> None:
    assert accepts(PortKind.FRAME_COLOR, PortKind.FRAME_COLOR) is True
    assert accepts(PortKind.FRAME_COLOR, PortKind.FRAME_MONO) is True
    assert accepts(PortKind.FRAME_COLOR, PortKind.FRAME_STEREO) is False


def test_depth_chain_is_compatible() -> None:
    assert accepts(PortKind.DISPARITY, PortKind.FRAME_STEREO) is True
    assert accepts(PortKind.DEPTH, PortKind.DISPARITY) is True
    assert accepts(PortKind.POINTS, PortKind.DEPTH) is True
    assert accepts(PortKind.POINTS, PortKind.DISPARITY) is False


def test_detections_do_not_accept_frames() -> None:
    assert accepts(PortKind.DETECTIONS, PortKind.DETECTIONS) is True
    assert accepts(PortKind.DETECTIONS, PortKind.FRAME_COLOR) is False


def test_parse_and_format_round_trip() -> None:
    parts = parse_port_ref("sparse.detect.features")

    assert parts.node == "sparse"
    assert parts.port == "detect.features"
    assert parts.text == "sparse.detect.features"
    assert format_port_ref("src", "frame") == "src.frame"


@pytest.mark.parametrize("ref", ["", "src", ".frame", "src."])
def test_parse_rejects_malformed_reference(ref: str) -> None:
    with pytest.raises(ValueError, match=r"<узел>\.<порт>"):
        parse_port_ref(ref)


def test_port_title_falls_back_to_value() -> None:
    assert port_title(PortKind.DEPTH) == "глубина"
    assert port_title("custom") == "custom"  # type: ignore[arg-type]
