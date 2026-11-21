"""Тесты экспорта и импорта миссии: JSON, YAML, read-only пакет (T6-07)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from simorph.core.errors import ProjectError
from simorph.core.mission.exporting import (
    exercise_payload,
    mission_payload,
    to_json,
    to_yaml,
    write_exercise,
    write_mission,
    write_payload,
)
from simorph.core.mission.importing import (
    COMMAND_LOOP_FIELDS,
    import_mission,
    load_payload,
    mission_from_payload,
)
from simorph.core.models.exercise import Mission, MissionElement
from simorph.core.models.project import ProjectDocument


def _mission() -> Mission:
    return Mission(
        mission_id="M-EX",
        agent_id="NRTC-01",
        elements=[
            MissionElement(id="start", type="start", next=["wp"]),
            MissionElement(
                id="wp",
                type="waypoint",
                pose=(120.0, 80.0, 25.0),
                speed_mps=6.0,
                next=["ret"],
            ),
            MissionElement(id="ret", type="return_to_base", ttl_s=120.0),
        ],
    )


@pytest.mark.unit
def test_mission_payload_round_trip_through_json() -> None:
    mission = _mission()

    payload = mission_payload(mission)
    restored = mission_from_payload(json.loads(to_json(payload)))

    assert restored == mission


@pytest.mark.unit
def test_mission_payload_round_trip_through_yaml() -> None:
    mission = _mission()

    text = to_yaml(mission_payload(mission))
    restored = mission_from_payload(yaml.safe_load(text))

    assert restored == mission
    assert "миссия" not in text


@pytest.mark.unit
def test_json_is_indented_and_ends_with_newline() -> None:
    text = to_json(mission_payload(_mission()))

    assert text.endswith("\n")
    assert '\n  "mission_id": "M-EX"' in text


@pytest.mark.unit
def test_yaml_keeps_field_order_and_unicode() -> None:
    text = to_yaml({"mission_id": "M-EX", "описание": "полёт"})

    assert text.startswith("mission_id:")
    assert "описание: полёт" in text


@pytest.mark.unit
def test_write_mission_chooses_format_by_suffix(tmp_path: Path) -> None:
    mission = _mission()

    json_path = write_mission(mission, tmp_path / "mission.json")
    yaml_path = write_mission(mission, tmp_path / "nested" / "mission.yaml")

    assert json.loads(json_path.read_text(encoding="utf-8"))["mission_id"] == "M-EX"
    assert yaml.safe_load(yaml_path.read_text(encoding="utf-8"))["mission_id"] == "M-EX"
    assert json_path.read_text(encoding="utf-8").count("\r") == 0


@pytest.mark.unit
def test_write_exercise_contains_missions(project_document: ProjectDocument) -> None:
    path = write_exercise(project_document.exercises[0], Path("exercise.yaml"))

    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert payload["exercise_id"] == "EX-01"
        assert payload["missions"][0]["mission_id"] == "M-01"
        assert payload == exercise_payload(project_document.exercises[0])
    finally:
        path.unlink()


@pytest.mark.unit
def test_write_payload_is_reproducible(tmp_path: Path) -> None:
    payload = mission_payload(_mission())

    first = write_payload(payload, tmp_path / "a.json").read_bytes()
    second = write_payload(payload, tmp_path / "b.json").read_bytes()

    assert first == second


@pytest.mark.unit
def test_import_mission_from_json_file(tmp_path: Path) -> None:
    mission = _mission()
    path = write_mission(mission, tmp_path / "mission.json")

    imported = import_mission(path)

    assert imported.read_only is True
    assert imported.mission == mission
    assert imported.elements == mission.elements
    assert imported.warnings == []
    assert imported.to_dict()["mission_id"] == "M-EX"


@pytest.mark.unit
def test_import_takes_first_mission_of_exercise(
    tmp_path: Path, project_document: ProjectDocument
) -> None:
    path = write_exercise(project_document.exercises[0], tmp_path / "exercise.yaml")

    imported = import_mission(path)

    assert imported.mission is not None
    assert imported.mission.mission_id == "M-01"
    assert imported.read_only is True


@pytest.mark.unit
def test_import_does_not_modify_source(tmp_path: Path) -> None:
    path = write_mission(_mission(), tmp_path / "mission.json")
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    import_mission(path)

    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.unit
def test_import_command_loop_package_is_read_only(tmp_path: Path) -> None:
    package = {
        "mission_id": "M-CL",
        "agent_id": "NRTC-01",
        "tasks": [
            {"id": "start", "type": "start", "next": ["wp"]},
            {"id": "wp", "type": "waypoint", "pose": [10, 20, 30], "next": ["ret"]},
            {"id": "ret", "type": "return_to_base"},
            {"id": "ai", "type": "llm_plan", "next": []},
        ],
        "route": {"profile": "safe"},
        "constraints": {"max_altitude_m": 50},
        "coordination": {"mode": "leader-follower"},
        "degradation": {"on_loss": "return_home"},
    }
    path = tmp_path / "mission.yaml"
    path.write_text(yaml.safe_dump(package, allow_unicode=True), encoding="utf-8")
    before = path.read_bytes()

    imported = import_mission(path)

    assert imported.read_only is True
    assert path.read_bytes() == before
    assert imported.mission is not None
    assert imported.mission.mission_id == "M-CL"
    assert [item.id for item in imported.elements] == ["start", "wp", "ret"]
    assert set(imported.fields) == set(COMMAND_LOOP_FIELDS)
    assert imported.warnings and "llm_plan" in imported.warnings[0]
    assert "coordination" in imported.to_dict()["fields"]


@pytest.mark.unit
def test_import_command_loop_package_without_known_tasks(tmp_path: Path) -> None:
    path = tmp_path / "mission.yaml"
    path.write_text(
        yaml.safe_dump({"tasks": [{"id": "x", "type": "unknown"}], "route": {"a": 1}}),
        encoding="utf-8",
    )

    imported = import_mission(path)

    assert imported.mission is None
    assert imported.elements == []
    assert imported.warnings


@pytest.mark.unit
def test_load_payload_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="не найден"):
        load_payload(tmp_path / "absent.json")


@pytest.mark.unit
def test_load_payload_rejects_corrupted_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(ProjectError, match="повреждён"):
        load_payload(path)


@pytest.mark.unit
def test_load_payload_rejects_non_object_root(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(ProjectError, match="объектом"):
        load_payload(path)


@pytest.mark.unit
def test_import_rejects_file_without_mission(tmp_path: Path) -> None:
    path = tmp_path / "other.json"
    path.write_text(json.dumps({"hello": "world"}), encoding="utf-8")

    with pytest.raises(ProjectError, match="ни миссии"):
        import_mission(path)


@pytest.mark.unit
def test_import_rejects_invalid_mission(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"mission_id": "M", "elements": []}), encoding="utf-8")

    with pytest.raises(ProjectError, match="схеме"):
        import_mission(path)
