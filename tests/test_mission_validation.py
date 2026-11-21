"""Тесты валидатора миссии: пять классов замечаний (T6-06, FR-MSN-06…09)."""

from __future__ import annotations

import pytest

from simorph.core.issues import IssueCode, Severity
from simorph.core.mission.validation import (
    MAX_ALTITUDE_M,
    MISSION_BUDGET_LIMIT_S,
    can_run,
    exercise_issues,
    mission_issues,
)
from simorph.core.models.agent import Sensor
from simorph.core.models.exercise import ExerciseSection, Mission, MissionElement
from simorph.core.models.project import ProjectDocument

#: Агент по умолчанию в демо-проекте.
AGENT_ID = "NRTC-01"


def _mission(elements: list[MissionElement]) -> Mission:
    return Mission(mission_id="M-T", agent_id=AGENT_ID, elements=elements)


def _issues(document: ProjectDocument, elements: list[MissionElement]) -> list[object]:
    return mission_issues(
        _mission(elements),
        world=document.world,
        agents=document.agents,
        prefix="mission",
    )


def _messages(issues: list[object]) -> list[str]:
    return [str(item.message) for item in issues]


def _paths(issues: list[object]) -> list[str]:
    return [str(item.path) for item in issues]


@pytest.mark.unit
def test_demo_mission_is_clean(project_document: ProjectDocument) -> None:
    mission = project_document.exercises[0].missions[0]

    issues = mission_issues(
        mission,
        world=project_document.world,
        agents=project_document.agents,
        prefix="mission",
    )

    assert issues == []
    assert can_run(mission, world=project_document.world, agents=project_document.agents) is True


@pytest.mark.unit
def test_dangling_next_is_reference_error(project_document: ProjectDocument) -> None:
    issues = _issues(
        project_document,
        [
            MissionElement(id="start", type="start", next=["ghost"]),
            MissionElement(id="ret", type="return_to_base"),
        ],
    )

    dangling = [item for item in issues if item.code is IssueCode.DANGLING_REFERENCE]
    assert _paths(dangling) == ["mission.elements.0.next"]


@pytest.mark.unit
def test_unknown_zone_is_reference_error(project_document: ProjectDocument) -> None:
    issues = _issues(
        project_document,
        [MissionElement(id="start", type="start", zone="Z-99")],
    )

    dangling = [item for item in issues if item.code is IssueCode.DANGLING_REFERENCE]
    assert _paths(dangling) == ["mission.elements.0.zone"]


@pytest.mark.unit
def test_unknown_sensor_is_reference_error(project_document: ProjectDocument) -> None:
    issues = _issues(
        project_document,
        [MissionElement(id="start", type="start", sensor="lidar-9")],
    )

    dangling = [item for item in issues if item.code is IssueCode.DANGLING_REFERENCE]
    assert _paths(dangling) == ["mission.elements.0.sensor"]


@pytest.mark.unit
def test_two_starts_are_graph_error(project_document: ProjectDocument) -> None:
    issues = _issues(
        project_document,
        [
            MissionElement(id="s1", type="start", next=[]),
            MissionElement(id="s2", type="start", next=[]),
        ],
    )

    graph = [item for item in issues if item.code is IssueCode.INVALID_GRAPH]
    assert any("ровно один" in item.message for item in graph)


@pytest.mark.unit
def test_illegal_cycle_points_to_element_field(project_document: ProjectDocument) -> None:
    issues = _issues(
        project_document,
        [
            MissionElement(id="start", type="start", next=["wp"]),
            MissionElement(id="wp", type="waypoint", pose=(1.0, 1.0, 1.0), next=["start"]),
        ],
    )

    graph = [item for item in issues if item.code is IssueCode.INVALID_GRAPH]
    assert any(item.path == "mission.elements.1.next" for item in graph)
    assert any("loop" in item.message for item in graph)


@pytest.mark.unit
def test_cycle_via_loop_passes(project_document: ProjectDocument) -> None:
    issues = _issues(
        project_document,
        [
            MissionElement(id="start", type="start", next=["loop"]),
            MissionElement(id="loop", type="loop", iterations=2, next=["start"]),
        ],
    )

    assert not [item for item in issues if item.code is IssueCode.INVALID_GRAPH]


@pytest.mark.unit
def test_unreachable_element_is_warning(project_document: ProjectDocument) -> None:
    issues = _issues(
        project_document,
        [
            MissionElement(id="start", type="start", next=["ret"]),
            MissionElement(id="ret", type="return_to_base"),
            MissionElement(id="lost", type="return_to_base"),
        ],
    )

    warnings = [item for item in issues if item.severity is Severity.WARNING]
    assert _paths(warnings) == ["mission.elements.2"]
    assert can_run(
        _mission(
            [
                MissionElement(id="start", type="start", next=["ret"]),
                MissionElement(id="ret", type="return_to_base"),
                MissionElement(id="lost", type="return_to_base"),
            ]
        ),
        world=project_document.world,
        agents=project_document.agents,
    )


@pytest.mark.unit
def test_missing_required_field_is_value_error(project_document: ProjectDocument) -> None:
    issues = _issues(
        project_document,
        [
            MissionElement(id="start", type="start", next=["wait"]),
            MissionElement(id="wait", type="dwell"),
        ],
    )

    values = [item for item in issues if item.code is IssueCode.INVALID_VALUE]
    assert _paths(values) == ["mission.elements.1.duration_s"]
    assert "duration_s" in values[0].message


@pytest.mark.unit
def test_abort_requires_condition(project_document: ProjectDocument) -> None:
    issues = _issues(
        project_document,
        [
            MissionElement(id="start", type="start", next=["stop"]),
            MissionElement(id="stop", type="abort"),
        ],
    )

    values = [item for item in issues if item.code is IssueCode.INVALID_VALUE]
    assert any(item.path == "mission.elements.1.condition" for item in values)


@pytest.mark.unit
def test_parallel_requires_two_branches(project_document: ProjectDocument) -> None:
    issues = _issues(
        project_document,
        [
            MissionElement(id="start", type="start", next=["split"]),
            MissionElement(id="split", type="parallel", next=["ret"]),
            MissionElement(id="ret", type="return_to_base"),
        ],
    )

    assert any("две ветви" in message for message in _messages(issues))


@pytest.mark.unit
def test_terminal_element_must_not_have_successors(project_document: ProjectDocument) -> None:
    issues = _issues(
        project_document,
        [
            MissionElement(id="start", type="start", next=["stop"]),
            MissionElement(
                id="stop",
                type="abort",
                condition="связь потеряна",
                next=["start"],
            ),
        ],
    )

    assert any("терминальный" in message for message in _messages(issues))


@pytest.mark.unit
def test_ttl_shorter_than_duration(project_document: ProjectDocument) -> None:
    issues = _issues(
        project_document,
        [
            MissionElement(id="start", type="start", next=["wait"]),
            MissionElement(id="wait", type="dwell", duration_s=20.0, ttl_s=10.0),
        ],
    )

    values = [item for item in issues if item.code is IssueCode.INVALID_VALUE]
    assert any(item.path == "mission.elements.1.ttl_s" for item in values)
    assert any("меньше длительности" in item.message for item in values)


@pytest.mark.unit
def test_budget_over_ttl_is_error(project_document: ProjectDocument) -> None:
    issues = _issues(
        project_document,
        [
            MissionElement(id="start", type="start", next=["wait"]),
            MissionElement(id="wait", type="dwell", duration_s=20.0, next=["ret"]),
            MissionElement(id="ret", type="return_to_base", ttl_s=15.0),
        ],
    )

    errors = [item for item in issues if item.severity is Severity.ERROR]
    assert any("превышает TTL" in item.message for item in errors)
    assert any(item.path == "mission.elements.2.ttl_s" for item in errors)


@pytest.mark.unit
def test_long_budget_is_warning(project_document: ProjectDocument) -> None:
    issues = _issues(
        project_document,
        [
            MissionElement(id="start", type="start", next=["wait"]),
            MissionElement(
                id="wait",
                type="dwell",
                duration_s=MISSION_BUDGET_LIMIT_S + 10.0,
            ),
        ],
    )

    warnings = [item for item in issues if item.severity is Severity.WARNING]
    assert any("превышает предел" in item.message for item in warnings)


@pytest.mark.unit
def test_pose_outside_site_is_error(project_document: ProjectDocument) -> None:
    issues = _issues(
        project_document,
        [MissionElement(id="start", type="start", pose=(2500.0, 10.0, 10.0))],
    )

    assert any("вне участка" in message for message in _messages(issues))


@pytest.mark.unit
def test_altitude_above_limit_is_error(project_document: ProjectDocument) -> None:
    issues = _issues(
        project_document,
        [MissionElement(id="start", type="start", pose=(10.0, 10.0, MAX_ALTITUDE_M + 1.0))],
    )

    assert any("вне диапазона" in message for message in _messages(issues))


@pytest.mark.unit
def test_pose_outside_zone_is_error(project_document: ProjectDocument) -> None:
    issues = _issues(
        project_document,
        [
            MissionElement(
                id="start",
                type="start",
                pose=(900.0, 900.0, 10.0),
                zone="Z-01",
            )
        ],
    )

    assert any("вне зоны" in message for message in _messages(issues))


@pytest.mark.unit
def test_observe_requires_camera(project_document: ProjectDocument) -> None:
    agent = project_document.agents[0]
    lidar = Sensor(id="lidar", type="distance", rate_hz=10.0)
    document = project_document.model_copy(
        update={"agents": [agent.model_copy(update={"sensors": [*agent.sensors, lidar]})]}
    )

    issues = mission_issues(
        _mission(
            [
                MissionElement(
                    id="start",
                    type="start",
                    next=["look"],
                ),
                MissionElement(id="look", type="observe", zone="Z-01", sensor="lidar"),
            ]
        ),
        world=document.world,
        agents=document.agents,
        prefix="mission",
    )

    values = [item for item in issues if item.code is IssueCode.INVALID_VALUE]
    assert any("требует камеру" in item.message for item in values)


@pytest.mark.unit
def test_observe_with_camera_passes(project_document: ProjectDocument) -> None:
    issues = _issues(
        project_document,
        [
            MissionElement(id="start", type="start", next=["look"]),
            MissionElement(
                id="look",
                type="observe",
                zone="Z-01",
                sensor="cam_stereo",
                pose=(100.0, 100.0, 20.0),
            ),
        ],
    )

    assert [item for item in issues if item.severity is Severity.ERROR] == []


@pytest.mark.unit
def test_duplicate_element_ids(project_document: ProjectDocument) -> None:
    issues = _issues(
        project_document,
        [
            MissionElement(id="start", type="start", next=[]),
            MissionElement(id="start", type="return_to_base"),
        ],
    )

    duplicates = [item for item in issues if item.code is IssueCode.DUPLICATE_ID]
    assert _paths(duplicates) == ["mission.elements.1.id"]


@pytest.mark.unit
def test_exercise_checks_world_and_agents(project_document: ProjectDocument) -> None:
    exercise = project_document.exercises[0]
    broken = exercise.model_copy(
        update={
            "world_id": "W-99",
            "agents": ["NRTC-77"],
            "missions": [
                Mission(
                    mission_id="M-01", agent_id=AGENT_ID, elements=exercise.missions[0].elements
                ),
                Mission(
                    mission_id="M-01", agent_id="NRTC-77", elements=exercise.missions[0].elements
                ),
            ],
        }
    )

    issues = exercise_issues(
        broken,
        world=project_document.world,
        agents=project_document.agents,
        prefix="exercises.0",
    )

    codes = {item.code for item in issues}
    assert IssueCode.DANGLING_REFERENCE in codes
    assert IssueCode.DUPLICATE_ID in codes
    assert any(item.path == "exercises.0.world_id" for item in issues)
    assert any(item.path == "exercises.0.missions.1.agent_id" for item in issues)


@pytest.mark.unit
def test_exercise_missions_are_validated_with_prefix(project_document: ProjectDocument) -> None:
    exercise: ExerciseSection = project_document.exercises[0]
    broken = exercise.model_copy(
        update={
            "missions": [
                Mission(
                    mission_id="M-01",
                    agent_id=AGENT_ID,
                    elements=[
                        MissionElement(id="start", type="start", next=["ghost"]),
                    ],
                )
            ]
        }
    )

    issues = exercise_issues(
        broken,
        world=project_document.world,
        agents=project_document.agents,
        prefix="exercises.0",
    )

    assert any(item.path == "exercises.0.missions.0.elements.0.next" for item in issues)
