"""Тесты CLI ``simorph agent`` (T5-08, G5-06)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simorph.cli.exit_codes import ExitCode
from simorph.cli.main import main
from simorph.core.factory import new_project
from simorph.core.models.project import ProjectDocument
from simorph.core.repository import save_project


def _broken_project(tmp_path: Path) -> Path:
    """Проект с агентом, у которого бюджет CPU больше цикла управления."""
    document = new_project("broken", seed=1)
    agent = document.agents[0]
    hw = agent.hw.model_copy(
        update={"tc_cmp": agent.hw.tc_cmp.model_copy(update={"cpu_budget_ms": 100.0})}
    )
    broken: ProjectDocument = document.model_copy(
        update={"agents": [agent.model_copy(update={"hw": hw})]}
    )
    path = tmp_path / "broken.json"
    save_project(broken, path, make_backup=False)
    return path


@pytest.mark.integration
def test_build_reports_tree_sensors_and_hw(
    project_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    code = main(["agent", "build", str(project_path), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["status"] == "ok"
    assert payload["tree"]["valid"] is True
    assert payload["tree"]["links"] > 0
    assert payload["tree"]["mass_kg"] > 0.0
    assert payload["sensors"]
    assert payload["actuators"]
    assert payload["hw_constants"] == 35
    assert set(payload["hw"]) == {"TC-DYN", "TC-SEN", "TC-CMP", "TC-ENE", "TC-COM"}


@pytest.mark.integration
def test_build_text_output(project_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["agent", "build", str(project_path)])
    out, _ = capfd.readouterr()

    assert code == int(ExitCode.OK)
    assert "звеньев" in out
    assert "сенсоры" in out
    assert "TC-DYN" in out


@pytest.mark.integration
def test_validate_accepts_demo_agent(project_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["agent", "validate", str(project_path), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["status"] == "ok"
    assert payload["errors"] == 0
    assert payload["launch_allowed"] is True
    assert payload["thrust_margin"] == 0.0


@pytest.mark.integration
def test_validate_rejects_broken_agent(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    path = _broken_project(tmp_path)

    code = main(["agent", "validate", str(path), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.DATA_ERROR)
    assert payload["status"] == "error"
    assert payload["errors"] > 0
    assert payload["launch_allowed"] is False
    assert any("TC-CMP-01" in item["message"] for item in payload["issues"])


@pytest.mark.integration
def test_export_writes_urdf(
    project_path: Path, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "out" / "agent.urdf"

    code = main(["agent", "export", str(project_path), "-o", str(target), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert target.exists()
    assert payload["status"] == "ok"
    assert payload["manifest"]["hw_constants"] == 35
    assert payload["manifest"]["links"] > 0


@pytest.mark.integration
def test_preview_writes_png_and_meta(
    project_path: Path, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "frames" / "agent.png"

    code = main(
        [
            "agent",
            "preview",
            str(project_path),
            "-o",
            str(target),
            "--width",
            "64",
            "--height",
            "48",
            "--json",
        ]
    )
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert target.exists()
    assert target.with_suffix(".json").exists()
    assert payload["status"] == "ok"
    assert payload["links"] > 0
    assert payload["zones"] > 0
    assert payload["build_ms"] >= 0.0


@pytest.mark.integration
def test_preview_without_zones(
    project_path: Path, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "agent",
            "preview",
            str(project_path),
            "-o",
            str(tmp_path / "agent.png"),
            "--no-zones",
            "--json",
        ]
    )
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["zones"] == 0


@pytest.mark.integration
def test_missing_agent_index_fails(project_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["agent", "validate", str(project_path), "--index", "7", "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.DATA_ERROR)
    assert payload["status"] == "error"
    assert "не найден" in payload["error"]


@pytest.mark.integration
def test_missing_action_fails(capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["agent", "--json"])
    capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
