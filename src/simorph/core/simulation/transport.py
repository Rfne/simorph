"""Транспорт кадров: кольцевой буфер в разделяемой памяти (T7-02, FR-SIM-04).

Кадр идёт от источника (сенсор) к конвейеру через кольцевой буфер на 2–4
кадра. Каждый кадр несёт ``frame_id``, ``t_fsin_ns`` (время сцены) и
``t_monotonic_ns`` (время PC): по этим меткам видно и синхронность
стереопары, и задержку доставки.

Буфер живёт в разделяемой памяти: производитель и потребитель могут быть
разными процессами, а имя буфера — часть контракта. Заголовок хранит
индексы чтения и записи и счётчики, поэтому второй процесс видит, сколько
кадров отправлено и потеряно.

По умолчанию буфер не перезаписывает непрочитанный кадр: ``push``
возвращает ``False``, и вызывающий решает, что делать. Перезапись самого
старого кадра — только явный режим ``realtime_drop`` (FR-SIM-19).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import struct
from dataclasses import dataclass
from multiprocessing import shared_memory
from threading import Lock
from typing import Any

__all__ = ["MAX_SLOTS", "MIN_SLOTS", "FramePacket", "FrameRing"]

#: Допустимое число слотов кольца: 2–4 кадра (TRD 4.4).
MIN_SLOTS: int = 2
MAX_SLOTS: int = 4

#: Размер полезной нагрузки слота по умолчанию, байт.
DEFAULT_SLOT_BYTES: int = 4096

#: Заголовок буфера: запись, чтение, заполнено, слотов, отправлено, потеряно.
_HEADER = struct.Struct("<IIIIQQ")
_HEADER_SIZE = _HEADER.size

#: Заголовок слота: id, t_fsin_ns, t_monotonic_ns, ширина, высота, камера, длина.
_SLOT = struct.Struct("<QqqII32sI")
_SLOT_SIZE = _SLOT.size

#: Имя камеры в слоте: фиксированная длина.
_CAMERA_BYTES = 32


@dataclass(frozen=True, slots=True)
class FramePacket:
    """Кадр в транспорте."""

    frame_id: int
    t_fsin_ns: int
    t_monotonic_ns: int
    camera_id: str
    width: int
    height: int
    payload: bytes = b""

    @property
    def checksum(self) -> str:
        """SHA-256 заголовка и полезной нагрузки: хэш кадра для replay."""
        digest = hashlib.sha256()
        digest.update(
            struct.pack(
                "<QqqII",
                self.frame_id,
                self.t_fsin_ns,
                self.t_monotonic_ns,
                self.width,
                self.height,
            )
        )
        digest.update(self.camera_id.encode("utf-8"))
        digest.update(self.payload)
        return digest.hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "t_fsin_ns": self.t_fsin_ns,
            "t_monotonic_ns": self.t_monotonic_ns,
            "camera_id": self.camera_id,
            "width": self.width,
            "height": self.height,
            "bytes": len(self.payload),
            "checksum": self.checksum,
        }

    def with_timing(
        self, *, t_monotonic_ns: int | None = None, payload: bytes | None = None
    ) -> FramePacket:
        """Копия с новыми меткой доставки и/или нагрузкой (инжекция деградации)."""
        return FramePacket(
            frame_id=self.frame_id,
            t_fsin_ns=self.t_fsin_ns,
            t_monotonic_ns=self.t_monotonic_ns if t_monotonic_ns is None else t_monotonic_ns,
            camera_id=self.camera_id,
            width=self.width,
            height=self.height,
            payload=self.payload if payload is None else payload,
        )


class FrameRing:
    """Кольцевой буфер кадров в разделяемой памяти."""

    def __init__(
        self,
        *,
        slots: int = 3,
        slot_bytes: int = DEFAULT_SLOT_BYTES,
        name: str | None = None,
    ) -> None:
        if not MIN_SLOTS <= int(slots) <= MAX_SLOTS:
            raise ValueError(f"слотов должно быть {MIN_SLOTS}…{MAX_SLOTS}, получено {slots}")
        if slot_bytes < 64:
            raise ValueError("слот меньше 64 байт: кадр не поместится")
        self.slots = int(slots)
        self.slot_bytes = int(slot_bytes)
        self._lock = Lock()
        self._owner = True
        self._shm: shared_memory.SharedMemory | None = None
        self._buffer: Any = None
        size = _HEADER_SIZE + self.slots * (_SLOT_SIZE + self.slot_bytes)
        try:
            self._shm = shared_memory.SharedMemory(
                name=name or _unique_name(), create=True, size=size
            )
        except (OSError, FileExistsError):
            # Окружение без разделяемой памяти: буфер остаётся в процессе.
            self._shm = None
        if self._shm is not None:
            self._buffer = self._shm.buf
            _HEADER.pack_into(self._buffer, 0, 0, 0, 0, self.slots, 0, 0)
        else:
            self._buffer = bytearray(size)
            _HEADER.pack_into(self._buffer, 0, 0, 0, 0, self.slots, 0, 0)

    # --- состояние ------------------------------------------------------

    @property
    def name(self) -> str:
        """Имя разделяемой памяти (пустое, если буфер в процессе)."""
        return self._shm.name if self._shm is not None else ""

    @property
    def shared(self) -> bool:
        return self._shm is not None

    @property
    def depth(self) -> int:
        """Сколько кадров ждёт потребителя."""
        with self._lock:
            return int(_HEADER.unpack_from(self._buffer, 0)[2])

    @property
    def pushed(self) -> int:
        with self._lock:
            return int(_HEADER.unpack_from(self._buffer, 0)[4])

    @property
    def dropped(self) -> int:
        with self._lock:
            return int(_HEADER.unpack_from(self._buffer, 0)[5])

    def stats(self) -> dict[str, Any]:
        with self._lock:
            write, read, count, slots, pushed, dropped = _HEADER.unpack_from(self._buffer, 0)
        return {
            "slots": int(slots),
            "depth": int(count),
            "pushed": int(pushed),
            "dropped": int(dropped),
            "shared": self.shared,
            "name": self.name,
            "write_index": int(write),
            "read_index": int(read),
            "slot_bytes": self.slot_bytes,
        }

    # --- обмен ----------------------------------------------------------

    def push(self, packet: FramePacket, *, drop_oldest: bool = False) -> bool:
        """Кладёт кадр; ``False`` — буфер полон и перезапись запрещена."""
        encoded = packet.camera_id.encode("utf-8")[:_CAMERA_BYTES]
        payload = packet.payload
        if len(payload) > self.slot_bytes:
            payload = payload[: self.slot_bytes]
        with self._lock:
            write, read, count, _slots, pushed, dropped = _HEADER.unpack_from(self._buffer, 0)
            if count >= self.slots:
                if not drop_oldest:
                    return False
                read = (read + 1) % self.slots
                count -= 1
                dropped += 1
            offset = _HEADER_SIZE + write * (_SLOT_SIZE + self.slot_bytes)
            _SLOT.pack_into(
                self._buffer,
                offset,
                packet.frame_id,
                packet.t_fsin_ns,
                packet.t_monotonic_ns,
                packet.width,
                packet.height,
                encoded,
                len(payload),
            )
            start = offset + _SLOT_SIZE
            self._buffer[start : start + len(payload)] = payload
            write = (write + 1) % self.slots
            count += 1
            pushed += 1
            _HEADER.pack_into(self._buffer, 0, write, read, count, self.slots, pushed, dropped)
        return True

    def pop(self) -> FramePacket | None:
        """Забирает самый старый кадр или ``None``."""
        with self._lock:
            write, read, count, _slots, pushed, dropped = _HEADER.unpack_from(self._buffer, 0)
            if count == 0:
                return None
            offset = _HEADER_SIZE + read * (_SLOT_SIZE + self.slot_bytes)
            frame_id, fsin, mono, width, height, camera, length = _SLOT.unpack_from(
                self._buffer, offset
            )
            start = offset + _SLOT_SIZE
            payload = bytes(self._buffer[start : start + int(length)])
            read = (read + 1) % self.slots
            count -= 1
            _HEADER.pack_into(self._buffer, 0, write, read, count, self.slots, pushed, dropped)
        camera_id = camera.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        return FramePacket(
            frame_id=int(frame_id),
            t_fsin_ns=int(fsin),
            t_monotonic_ns=int(mono),
            camera_id=camera_id,
            width=int(width),
            height=int(height),
            payload=payload,
        )

    def drain(self) -> list[FramePacket]:
        """Забирает все кадры в порядке поступления."""
        packets: list[FramePacket] = []
        while True:
            packet = self.pop()
            if packet is None:
                return packets
            packets.append(packet)

    # --- жизненный цикл -------------------------------------------------

    def close(self) -> None:
        """Освобождает разделяемую память."""
        if self._shm is None:
            return
        with self._lock:
            name = self._shm.name
            self._shm.close()
            if self._owner:
                with contextlib.suppress(FileNotFoundError, OSError):
                    shared_memory.SharedMemory(name=name).unlink()
        self._shm = None

    def __enter__(self) -> FrameRing:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _unique_name() -> str:
    """Уникальное имя разделяемой памяти (до 14 символов в POSIX)."""
    import os
    import time

    return f"sm{os.getpid() % 100000:05d}{time.perf_counter_ns() % 100000:05d}"


def payload_preview(payload: bytes, limit: int = 16) -> str:
    """Короткое представление нагрузки для отчёта и лога."""
    return base64.b64encode(payload[:limit]).decode("ascii")
