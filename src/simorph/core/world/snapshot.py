"""Снимок сцены: PNG без внешних зависимостей (T4-08, FR-WLD-14).

Снимок должен открываться на любой машине, поэтому PNG пишется вручную:
стандартные чанки IHDR/IDAT/IEND и zlib из стандартной библиотеки. Это
дешевле, чем тянуть графическую библиотеку в core, и не зависит от того,
есть ли в окружении OpenCV с нужными кодеками.

Рядом с картинкой кладётся JSON с камерой, статистикой и диапазоном
глубины: по нему снимок можно воспроизвести.
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["png_bytes", "save_snapshot", "write_png"]

#: Сигнатура PNG.
_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(tag: bytes, data: bytes) -> bytes:
    payload = tag + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload))


def png_bytes(rgb: np.ndarray) -> bytes:
    """PNG из массива ``(h, w, 3)`` без альфа-канала."""
    array = np.ascontiguousarray(rgb, dtype=np.uint8)
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("ожидался массив (h, w, 3)")
    height, width = int(array.shape[0]), int(array.shape[1])
    raw = b"".join(b"\x00" + array[row].tobytes() for row in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"".join(
        (
            _SIGNATURE,
            _chunk(b"IHDR", header),
            _chunk(b"IDAT", zlib.compress(raw, level=6)),
            _chunk(b"IEND", b""),
        )
    )


def write_png(path: Path, rgb: np.ndarray) -> Path:
    """Пишет PNG и создаёт каталог назначения."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_bytes(rgb))
    return path


def save_snapshot(
    path: Path,
    rgb: np.ndarray,
    meta: dict[str, Any],
) -> tuple[Path, Path]:
    """Пишет кадр и JSON-описание рядом с ним."""
    image = write_png(path, rgb)
    sidecar = path.with_suffix(".json")
    sidecar.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return image, sidecar
