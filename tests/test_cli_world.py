"""Тесты CLI ``simorph world``: сборка и снимок (T4-01, T4-08, G4-07)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simorph.cli.exit_codes import ExitCode
from simorph.cli.main import main


@pytest.mark.integration
def test_build_json_reports_scene(project_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["world", "build", str(project_path), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["status"] == "ready"
    assert payload["scene"]["objects"] > 0
    assert payload["scene"]["bodies"] == payload["scene"]["objects"]
    assert payload["scene"]["build_seconds"] >= 0.0
    assert payload["error"] is None
    assert payload["generation_keys"]


@pytest.mark.integration
def test_build_respects_limit(project_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["world", "build", str(project_path), "--limit", "5", "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["scene"]["objects"] == 5
    assert payload["scene"]["truncated"] > 0
    assert payload["warnings"]


@pytest.mark.integration
def test_build_text_output_mentions_counts(
    project_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    code = main(["world", "build", str(project_path)])
    out, _ = capfd.readouterr()

    assert code == int(ExitCode.OK)
    assert "статус   ready" in out
    assert "объектов" in out


@pytest.mark.integration
def test_render_writes_png_and_meta(
    project_path: Path, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "frames" / "scene.png"

    code = main(
        [
            "world",
            "render",
            str(project_path),
            "-o",
            str(target),
            "--width",
            "64",
            "--height",
            "48",
            "--distance",
            "200",
            "--hour",
            "15",
            "--precipitation",
            "snow",
            "--json",
        ]
    )
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["status"] == "ok"
    assert payload["width"] == 64
    assert payload["height"] == 48
    assert payload["depth_max"] >= payload["depth_min"] > 0.0
    assert target.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    meta = json.loads(target.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["sensor"]["width"] == 64
    assert meta["weather"]["precipitation"] == "snow"
    assert meta["scene"]["objects"] == payload["objects"]


@pytest.mark.integration
def test_missing_project_returns_data_error(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    code = main(["world", "build", str(tmp_path / "нет.json"), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.DATA_ERROR)
    assert payload["status"] == "error"
    assert payload["error"]


@pytest.mark.integration
def test_world_without_action_returns_data_error() -> None:
    code = main(["world"])

    assert code == int(ExitCode.DATA_ERROR)
