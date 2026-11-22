"""Запись эталонного потока и replay без физики (T7-07, FR-SIM-12…13).

Поток пишется построчно JSON: заголовок с конфигурацией прогона, затем
кадры с метками времени, нагрузкой в base64 и хэшем кадра. Хэш считается
по заголовку и нагрузке, поэтому replay может проверить не только
количество кадров, но и их содержимое.

Replay не запускает физику и не строит сцену: он читает файл, восстанавливает
кадры и сверяет хэши. Расхождение — это отчёт, а не исключение: видно,
какой кадр и какая метка не совпали (G7-06).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from simorph.core.errors import ProjectError
from simorph.core.simulation.transport import FramePacket

__all__ = [
    "STREAM_VERSION",
    "ReplayReport",
    "RunRecorder",
    "read_stream",
    "replay_stream",
    "write_stream",
]

#: Версия формата потока.
STREAM_VERSION: int = 1


@dataclass(slots=True)
class ReplayReport:
    """Итог replay: сколько кадров совпало и где расхождение."""

    source: Path
    frames: int = 0
    matched: int = 0
    mismatched: list[int] = field(default_factory=list)
    bad_timing: list[int] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.frames > 0 and not self.mismatched and not self.bad_timing

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.source),
            "frames": self.frames,
            "matched": self.matched,
            "mismatched": list(self.mismatched),
            "bad_timing": list(self.bad_timing),
            "ok": self.ok,
            "meta": dict(self.meta),
        }


class RunRecorder:
    """Построчная запись потока кадров."""

    def __init__(self, path: Path, *, meta: dict[str, Any] | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", encoding="utf-8", newline="\n")
        self._frames = 0
        header = {
            "kind": "header",
            "version": STREAM_VERSION,
            "meta": meta or {},
        }
        self._handle.write(json.dumps(header, ensure_ascii=False) + "\n")
        self._handle.flush()

    @property
    def frames(self) -> int:
        return self._frames

    def append(self, packet: FramePacket) -> None:
        """Пишет кадр и сразу сбрасывает буфер: файл переживёт сбой."""
        record = {
            "kind": "frame",
            "frame_id": packet.frame_id,
            "t_fsin_ns": packet.t_fsin_ns,
            "t_monotonic_ns": packet.t_monotonic_ns,
            "camera_id": packet.camera_id,
            "width": packet.width,
            "height": packet.height,
            "payload": base64.b64encode(packet.payload).decode("ascii"),
            "checksum": packet.checksum,
        }
        self._handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._handle.flush()
        self._frames += 1

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    def __enter__(self) -> RunRecorder:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def write_stream(
    path: Path, packets: list[FramePacket], *, meta: dict[str, Any] | None = None
) -> Path:
    """Записывает готовый список кадров в файл потока."""
    with RunRecorder(path, meta=meta) as recorder:
        for packet in packets:
            recorder.append(packet)
    return Path(path)


def read_stream(path: Path) -> tuple[dict[str, Any], list[FramePacket]]:
    """Читает поток: метаданные и кадры.

    Raises:
        ProjectError: файл отсутствует, повреждён или это не поток кадров.
    """
    meta, packets, _checksums = _read_records(path)
    return meta, packets


def replay_stream(path: Path, *, compare: bool = True) -> ReplayReport:
    """Воспроизводит поток без физики и сверяет хэши кадров."""
    meta, packets, checksums = _read_records(path)
    report = ReplayReport(source=Path(path), frames=len(packets), meta=meta)
    previous_fsin = -1
    previous_mono = -1
    for index, packet in enumerate(packets):
        if compare and checksums[index] != packet.checksum:
            report.mismatched.append(index)
        else:
            report.matched += 1
        if packet.t_fsin_ns < previous_fsin or packet.t_monotonic_ns < previous_mono:
            report.bad_timing.append(index)
        previous_fsin = packet.t_fsin_ns
        previous_mono = packet.t_monotonic_ns
    return report


def _read_records(path: Path) -> tuple[dict[str, Any], list[FramePacket], list[str]]:
    """Читает поток один раз: метаданные, кадры и записанные хэши."""
    target = Path(path)
    if not target.exists():
        raise ProjectError(f"файл потока не найден: {target}")
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ProjectError(f"файл потока не читается: {target}: {exc}") from exc
    meta: dict[str, Any] = {}
    packets: list[FramePacket] = []
    checksums: list[str] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProjectError(f"поток повреждён: строка {number}: {exc}") from exc
        if not isinstance(record, dict):
            raise ProjectError(f"поток повреждён: строка {number} не объект")
        kind = record.get("kind")
        if kind == "header":
            raw_meta = record.get("meta")
            meta = raw_meta if isinstance(raw_meta, dict) else {}
            continue
        if kind != "frame":
            raise ProjectError(f"поток повреждён: неизвестная запись {kind!r}")
        try:
            packets.append(
                FramePacket(
                    frame_id=int(record["frame_id"]),
                    t_fsin_ns=int(record["t_fsin_ns"]),
                    t_monotonic_ns=int(record["t_monotonic_ns"]),
                    camera_id=str(record["camera_id"]),
                    width=int(record["width"]),
                    height=int(record["height"]),
                    payload=base64.b64decode(record.get("payload", "")),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProjectError(f"поток повреждён: строка {number}: {exc}") from exc
        checksums.append(str(record.get("checksum", "")))
    if not packets:
        raise ProjectError(f"в потоке нет кадров: {target}")
    return meta, packets, checksums
