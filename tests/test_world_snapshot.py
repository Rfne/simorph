"""Тесты снимка: PNG без зависимостей и JSON рядом (T4-08, FR-WLD-14)."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import pytest

from simorph.core.world import png_bytes, save_snapshot, write_png

SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _rgb(width: int = 8, height: int = 4) -> np.ndarray:
    return np.arange(width * height * 3, dtype=np.uint8).reshape(height, width, 3)


def test_png_has_signature_and_header() -> None:
    data = png_bytes(_rgb(12, 6))

    assert data.startswith(SIGNATURE)
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (12, 6)
    assert data.endswith(b"IEND\xaeB`\x82")


def test_png_rejects_wrong_shape() -> None:
    with pytest.raises(ValueError):
        png_bytes(np.zeros((4, 4), dtype=np.uint8))


def test_write_png_creates_directories(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "frame.png"

    written = write_png(target, _rgb())

    assert written == target
    assert target.read_bytes().startswith(SIGNATURE)


def test_save_snapshot_writes_sidecar_json(tmp_path: Path) -> None:
    target = tmp_path / "frame.png"
    meta = {"camera": {"distance": 10.0}, "scene": {"objects": 3}}

    image, sidecar = save_snapshot(target, _rgb(), meta)

    assert image == target
    assert sidecar == target.with_suffix(".json")
    assert json.loads(sidecar.read_text(encoding="utf-8")) == meta
    assert sidecar.read_text(encoding="utf-8").endswith("\n")
