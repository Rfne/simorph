"""Тесты CLI ``simorph mission``: build, validate, export, import (T6-08)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from simorph.cli.exit_codes import ExitCode
from simorph.cli.main import main
from simorph.core.factory import new_project
from simorph.core.models.exercise import ExerciseSection, Mission, MissionElement
from simorph.core.models.project import ProjectDocument
from simorph.core.repository import save_project


def _exercise() -> ExerciseSection:
    """Упражнение с двумя миссиями: одна с параллельными ветвями."""
    return ExerciseSection(
        schema_version="1.0",
        exercise_id="EX-01",
        seed=1,
        world_id="W-01",
        agents=["NRTC-01"],
        missions=[
            Mission(
                mission_id="M-01",
                agent_id="NRTC-01",
                elements=[
                    MissionElement(id="start", type="start", next=["wp1"]),
                    MissionElement(
                        id="wp1",
                        type="waypoint",
                        pose=(100.0, 100.0, 20.0),
                        next=["split"],
                    ),
                    MissionElement(id="split", type="parallel", next=["look", "wp2"]),
                    MissionElement(
                        id="look",
                        type="observe",
                        zone="Z-01",
                        sensor="cam_stereo",
                        pose=(200.0, 200.0, 15.0),
                        duration_s=10.0,
                        next=["ret"],
                    ),
                    MissionElement(
                        id="wp2",
                        type="waypoint",
                        pose=(900.0, 900.0, 20.0),
                        duration_s=4.0,
                        next=["ret"],
                    ),
                    MissionElement(id="ret", type="return_to_base"),
                ],
            ),
            Mission(
                mission_id="M-02",
                agent_id="NRTC-01",
                elements=[
                    MissionElement(id="start", type="start", next=["ret"]),
                    MissionElement(id="ret", type="return_to_base"),
                ],
            ),
        ],
    )


def _project(tmp_path: Path, *, broken: bool = False) -> Path:
    document = new_project("mission", seed=1)
    exercise = _exercise()
    if broken:
        exercise = exercise.model_copy(
            update={
                "missions": [
                    exercise.missions[0].model_copy(
                        update={
                            "elements": [
                                MissionElement(id="s1", type="start"),
                                MissionElement(id="s2", type="start"),
                            ]
                        }
                    )
                ]
            }
        )
    project: ProjectDocument = document.model_copy(update={"exercises": [exercise]})
    path = tmp_path / "project.json"
    save_project(project, path, make_backup=False)
    return path


@pytest.mark.integration
def test_build_reports_graph_and_budget(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    path = _project(tmp_path)

    code = main(["mission", "build", str(path), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["status"] == "ok"
    assert payload["exercise_id"] == "EX-01"
    assert payload["mission_id"] == "M-01"
    assert payload["elements"] == 6
    assert payload["graph"]["layer_count"] == 5
    assert payload["graph"]["valid"] is True
    assert payload["budget"]["total_s"] == pytest.approx(10.0)
    assert payload["budget"]["sequential_s"] == pytest.approx(14.0)
    assert payload["budget"]["parallel_width"] == 2
    assert payload["budget"]["critical_path"] == ["start", "wp1", "split", "look", "ret"]
    assert payload["errors"] == 0
    assert "observe" in payload["types"]


@pytest.mark.integration
def test_build_text_output(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    path = _project(tmp_path)

    code = main(["mission", "build", str(path)])
    out, _ = capfd.readouterr()

    assert code == int(ExitCode.OK)
    assert "упражнение EX-01" in out
    assert "слоёв 5" in out
    assert "критический путь" in out
    assert "типы:" in out


@pytest.mark.integration
def test_build_selects_second_mission(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    path = _project(tmp_path)

    code = main(["mission", "build", str(path), "--mission", "1", "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["mission_id"] == "M-02"
    assert payload["elements"] == 2


@pytest.mark.integration
def test_validate_accepts_clean_mission(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    path = _project(tmp_path)

    code = main(["mission", "validate", str(path), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["status"] == "ok"
    assert payload["can_run"] is True
    assert payload["issues"] == []


@pytest.mark.integration
def test_validate_rejects_broken_mission(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    path = _project(tmp_path, broken=True)

    code = main(["mission", "validate", str(path), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.DATA_ERROR)
    assert payload["status"] == "error"
    assert payload["can_run"] is False
    assert payload["errors"] >= 1
    assert any(item["code"] == "invalid_graph" for item in payload["issues"])


@pytest.mark.integration
def test_validate_text_lists_issues(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    path = _project(tmp_path, broken=True)

    code = main(["mission", "validate", str(path)])
    out, _ = capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert "ошибок" in out
    assert "invalid_graph" not in out
    assert "ровно один" in out


@pytest.mark.integration
def test_export_mission_as_json(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    path = _project(tmp_path)
    target = tmp_path / "out" / "mission.json"

    code = main(["mission", "export", str(path), "-o", str(target), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["kind"] == "mission"
    assert payload["id"] == "M-01"
    assert payload["bytes"] > 0
    exported = json.loads(target.read_text(encoding="utf-8"))
    assert exported["mission_id"] == "M-01"
    assert len(exported["elements"]) == 6


@pytest.mark.integration
def test_export_exercise_as_yaml(tmp_path: Path) -> None:
    path = _project(tmp_path)
    target = tmp_path / "exercise.yaml"

    code = main(["mission", "export", str(path), "-o", str(target), "--exercise-only"])

    assert code == int(ExitCode.OK)
    payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert payload["exercise_id"] == "EX-01"
    assert [item["mission_id"] for item in payload["missions"]] == ["M-01", "M-02"]


@pytest.mark.integration
def test_export_then_import_round_trip(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    path = _project(tmp_path)
    target = tmp_path / "mission.yaml"
    main(["mission", "export", str(path), "-o", str(target)])
    capfd.readouterr()

    code = main(["mission", "import", str(target), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["read_only"] is True
    assert payload["mission_id"] == "M-01"
    assert payload["elements"] == 6
    assert payload["warnings"] == []


@pytest.mark.integration
def test_import_command_loop_package(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    package = {
        "tasks": [
            {"id": "start", "type": "start", "next": ["ret"]},
            {"id": "ret", "type": "return_to_base"},
        ],
        "route": {"profile": "safe"},
        "coordination": {"mode": "leader-follower"},
    }
    source = tmp_path / "mission.yaml"
    source.write_text(yaml.safe_dump(package), encoding="utf-8")

    code = main(["mission", "import", str(source)])
    out, _ = capfd.readouterr()

    assert code == int(ExitCode.OK)
    assert "только чтение" in out
    assert "поля пакета: coordination, route" in out


@pytest.mark.integration
def test_import_missing_file_fails(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["mission", "import", str(tmp_path / "absent.yaml"), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.DATA_ERROR)
    assert payload["status"] == "error"
    assert "не найден" in payload["error"]


@pytest.mark.integration
def test_unknown_exercise_index_fails(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    path = _project(tmp_path)

    code = main(["mission", "build", str(path), "--exercise", "5", "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.DATA_ERROR)
    assert "упражнение с номером 5 не найдено" in payload["error"]
