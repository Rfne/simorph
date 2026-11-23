"""PNG для датасета: запись и чтение без внешних зависимостей (T10-01).

Кадры пишутся тем же ручным PNG-энкодером, что и снимки сцены: без OpenCV
и без графической библиотеки, поэтому датасет собирается и читается в
headless-окружении. Маска пишется 8-битным серым PNG (значение — индекс
класса), цвет — 8-битным RGB.

Читатель понимает все пять фильтров PNG, а не только тот, который пишет
энкодер: файл датасета может быть пересжат сторонним инструментом, и это
не должно ломать обучение.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Final

import numpy as np

from simorph.core.world.snapshot import png_bytes

__all__ = ["read_png", "write_gray_png", "write_rgb_png"]

#: Сигнатура PNG.
_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"


def _chunk(tag: bytes, data: bytes) -> bytes:
    payload = tag + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload))


def _gray_png_bytes(array: np.ndarray) -> bytes:
    values = np.ascontiguousarray(array, dtype=np.uint8)
    if values.ndim != 2:
        raise ValueError("ожидался двумерный массив маски")
    height, width = int(values.shape[0]), int(values.shape[1])
    raw = b"".join(b"\x00" + values[row].tobytes() for row in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"".join(
        (
            _SIGNATURE,
            _chunk(b"IHDR", header),
            _chunk(b"IDAT", zlib.compress(raw, level=6)),
            _chunk(b"IEND", b""),
        )
    )


def write_rgb_png(path: Path, image: np.ndarray) -> Path:
    """Пишет цветной кадр ``(h, w, 3)``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_bytes(image))
    return path


def write_gray_png(path: Path, array: np.ndarray) -> Path:
    """Пишет 8-битную маску ``(h, w)``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_gray_png_bytes(array))
    return path


def _unfilter(raw: bytes, *, height: int, width: int, channels: int) -> np.ndarray:
    """Снимает построчные фильтры PNG."""
    stride = width * channels
    result = np.zeros((height, stride), dtype=np.uint8)
    position = 0
    for row in range(height):
        filter_type = raw[position]
        position += 1
        line = np.frombuffer(raw[position : position + stride], dtype=np.uint8).copy()
        position += stride
        previous = result[row - 1] if row else np.zeros(stride, dtype=np.uint8)
        if filter_type == 0:
            current = line
        elif filter_type == 1:
            current = line
            for index in range(channels, stride):
                current[index] = (int(current[index]) + int(current[index - channels])) & 0xFF
        elif filter_type == 2:
            current = (line.astype(np.int16) + previous.astype(np.int16)).astype(np.uint8)
        elif filter_type == 3:
            current = line
            for index in range(stride):
                left = int(current[index - channels]) if index >= channels else 0
                current[index] = (int(current[index]) + (left + int(previous[index])) // 2) & 0xFF
        elif filter_type == 4:
            current = line
            for index in range(stride):
                left = int(current[index - channels]) if index >= channels else 0
                up = int(previous[index])
                up_left = int(previous[index - channels]) if index >= channels else 0
                estimate = left + up - up_left
                pa = abs(estimate - left)
                pb = abs(estimate - up)
                pc = abs(estimate - up_left)
                if pa <= pb and pa <= pc:
                    predictor = left
                elif pb <= pc:
                    predictor = up
                else:
                    predictor = up_left
                current[index] = (int(current[index]) + predictor) & 0xFF
        else:
            raise ValueError(f"неизвестный фильтр PNG: {filter_type}")
        result[row] = current
    return result


def read_png(path: Path) -> np.ndarray:
    """Читает 8-битный PNG: ``(h, w, 3)`` для цвета, ``(h, w)`` для маски."""
    data = path.read_bytes()
    if not data.startswith(_SIGNATURE):
        raise ValueError(f"не PNG: {path}")
    position = len(_SIGNATURE)
    header: tuple[int, int, int, int] | None = None
    payload = bytearray()
    while position < len(data):
        length = struct.unpack(">I", data[position : position + 4])[0]
        tag = data[position + 4 : position + 8]
        body = data[position + 8 : position + 8 + length]
        position += 12 + length
        if tag == b"IHDR":
            width, height, depth, color_type = struct.unpack(">IIBB", body[:10])
            if depth != 8 or color_type not in (0, 2):
                raise ValueError(
                    f"поддерживается только 8-битный PNG без альфы, получено {depth}/{color_type}"
                )
            header = (width, height, color_type, 0)
        elif tag == b"IDAT":
            payload.extend(body)
        elif tag == b"IEND":
            break
    if header is None:
        raise ValueError(f"в PNG нет заголовка: {path}")
    width, height, color_type, _ = header
    channels = 3 if color_type == 2 else 1
    raw = zlib.decompress(bytes(payload))
    rows = _unfilter(raw, height=height, width=width, channels=channels)
    image = rows.reshape(height, width, channels)
    if channels == 1:
        return image[:, :, 0]
    return image
