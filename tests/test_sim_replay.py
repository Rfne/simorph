"""Тесты записи потока и replay без физики (T7-07, G7-06)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simorph.core.errors import ProjectError
from simorph.core.simulation.replay import (
    STREAM_VERSION,
    RunRecorder,
    read_stream,
    replay_stream,
    write_stream,
)
from simorph.core.simulation.transport import FramePacket


def _packet(frame_id: int) -> FramePacket:
    return FramePacket(
        frame_id=frame_id,
        t_fsin_ns=frame_id * 1_000_000,
        t_monotonic_ns=frame_id * 2_000_000,
        camera_id="cam_stereo",
        width=64,
        height=48,
        payload=f"frame-{frame_id}".encode(),
    )


def _packets() -> list[FramePacket]:
    return [_packet(index) for index in range(1, 6)]


def test_roundtrip_keeps_frames_and_meta(tmp_path: Path) -> None:
    path = tmp_path / "stream.jsonl"
    write_stream(path, _packets(), meta={"mission_id": "M-01"})

    meta, packets = read_stream(path)

    assert meta == {"mission_id": "M-01"}
    assert [item.frame_id for item in packets] == [1, 2, 3, 4, 5]
    assert [item.checksum for item in packets] == [item.checksum for item in _packets()]


def test_header_contains_version(tmp_path: Path) -> None:
    path = tmp_path / "stream.jsonl"
    with RunRecorder(path) as recorder:
        recorder.append(_packet(1))
        assert recorder.frames == 1

    header = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert header["kind"] == "header"
    assert header["version"] == STREAM_VERSION


def test_replay_matches_recorded_hashes(tmp_path: Path) -> None:
    path = tmp_path / "stream.jsonl"
    write_stream(path, _packets())

    report = replay_stream(path)

    assert report.ok is True
    assert report.frames == 5
    assert report.matched == 5
    assert report.mismatched == []
    assert report.bad_timing == []
    assert report.to_dict()["ok"] is True


def test_replay_reports_tampered_checksum(tmp_path: Path) -> None:
    path = tmp_path / "stream.jsonl"
    write_stream(path, _packets())
    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[2])
    record["checksum"] = "0" * 64
    lines[2] = json.dumps(record, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = replay_stream(path)

    assert report.ok is False
    assert report.mismatched == [1]
    assert report.matched == 4


def test_replay_flags_non_monotonic_labels(tmp_path: Path) -> None:
    packets = [
        _packet(1),
        FramePacket(
            frame_id=2,
            t_fsin_ns=10,
            t_monotonic_ns=10,
            camera_id="cam_stereo",
            width=64,
            height=48,
            payload=b"x",
        ),
    ]
    path = tmp_path / "stream.jsonl"
    write_stream(path, packets)

    report = replay_stream(path)

    assert report.bad_timing == [1]
    assert report.ok is False


def test_replay_can_skip_comparison(tmp_path: Path) -> None:
    path = tmp_path / "stream.jsonl"
    write_stream(path, _packets())

    report = replay_stream(path, compare=False)

    assert report.ok is True
    assert report.matched == 5


def test_missing_file_is_data_error(tmp_path: Path) -> None:
    with pytest.raises(ProjectError):
        read_stream(tmp_path / "absent.jsonl")


def test_corrupt_stream_is_data_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(ProjectError):
        replay_stream(path)


def test_stream_without_frames_is_data_error(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text('{"kind": "header", "version": 1, "meta": {}}\n', encoding="utf-8")
    with pytest.raises(ProjectError):
        read_stream(path)


def test_unknown_record_is_data_error(tmp_path: Path) -> None:
    path = tmp_path / "unknown.jsonl"
    path.write_text(
        '{"kind": "header", "version": 1, "meta": {}}\n{"kind": "other"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ProjectError):
        read_stream(path)
