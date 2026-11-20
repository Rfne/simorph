"""Общие фикстуры тестов simorph."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

# Qt должен узнать про offscreen до первого импорта виджетов.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Временный каталог с минимальным ``project.json``."""
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "project_id": "00000000-0000-0000-0000-000000000000",
        "name": "test",
        "profile": {"path": "profiles/demo.json", "hash": "sha256:0"},
        "world": {"seed": 42, "preset": "industrial_lite", "backend": "pybullet"},
        "agents": [],
        "exercises": [],
        "pipelines": [],
        "transport": {"mode": "inproc", "domain_id": 0},
        "reports": [],
        "dashboards": [],
        "settings": {"theme": "dark", "language": "ru", "offline": True},
    }
    path = tmp_path / "project.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return tmp_path
