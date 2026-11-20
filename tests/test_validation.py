"""Тесты валидатора: шесть классов ошибок (T1-05, G1-05)."""

from __future__ import annotations

from typing import Any

import pytest

from simorph.core.issues import IssueCode
from simorph.core.models.project import ProjectDocument
from simorph.core.validation import check_document, describe, validate_payload


def as_payload(document: ProjectDocument) -> dict[str, Any]:
    return document.model_dump(mode="json", by_alias=True)


def codes(document: ProjectDocument) -> set[IssueCode]:
    return {item.code for item in check_document(document)}


@pytest.mark.unit
def test_valid_project_has_no_errors(project_document: ProjectDocument) -> None:
    issues = check_document(project_document)
    assert [item for item in issues if item.severity == "error"] == []


@pytest.mark.unit
def test_missing_field_is_found(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    del payload["world"]["terrain"]
    issues = validate_payload(payload)
    assert IssueCode.MISSING_FIELD in {item.code for item in issues}


@pytest.mark.unit
def test_invalid_type_is_found(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    payload["world"]["seed"] = "сорок два"
    issues = validate_payload(payload)
    assert IssueCode.INVALID_TYPE in {item.code for item in issues}


@pytest.mark.unit
def test_invalid_range_is_found(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    payload["world"]["lighting"]["cloudiness"] = 2.0
    issues = validate_payload(payload)
    assert IssueCode.INVALID_RANGE in {item.code for item in issues}


@pytest.mark.unit
def test_dangling_reference_is_found(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    payload["exercises"][0]["world_id"] = "W-99"
    issues = validate_payload(payload)
    dangling = [item for item in issues if item.code == IssueCode.DANGLING_REFERENCE]
    assert dangling
    assert dangling[0].path == "exercises.0.world_id"
    assert "W-99" in dangling[0].message


@pytest.mark.unit
def test_dangling_sensor_reference_is_found(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    payload["exercises"][0]["missions"][0]["elements"][0] = {
        "id": "start",
        "type": "start",
        "next": ["obs"],
    }
    payload["exercises"][0]["missions"][0]["elements"].append(
        {
            "id": "obs",
            "type": "observe",
            "zone": "Z-01",
            "sensor": "cam_missing",
            "next": [],
        }
    )
    issues = validate_payload(payload)
    assert any(
        item.code == IssueCode.DANGLING_REFERENCE and "cam_missing" in item.message
        for item in issues
    )


@pytest.mark.unit
def test_duplicate_id_is_found(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    payload["agents"].append(payload["agents"][0])
    issues = validate_payload(payload)
    duplicates = [item for item in issues if item.code == IssueCode.DUPLICATE_ID]
    assert duplicates
    assert duplicates[0].path == "agents.1.agent_id"


@pytest.mark.unit
def test_duplicate_zone_is_found(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    payload["world"]["zones"].append(payload["world"]["zones"][0])
    issues = validate_payload(payload)
    assert IssueCode.DUPLICATE_ID in {item.code for item in issues}


@pytest.mark.unit
def test_unknown_version_is_found(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    payload["schema_version"] = "7.5"
    issues = validate_payload(payload)
    assert [item.code for item in issues] == [IssueCode.UNKNOWN_VERSION]
    assert "7.5" in issues[0].message


@pytest.mark.unit
def test_legacy_version_requires_migration(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    payload["schema_version"] = "0.9"
    issues = validate_payload(payload)
    assert issues[0].code == IssueCode.UNKNOWN_VERSION
    assert "миграции" in issues[0].message


@pytest.mark.unit
def test_missing_version_is_found(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    del payload["schema_version"]
    issues = validate_payload(payload)
    assert issues[0].code == IssueCode.MISSING_FIELD
    assert issues[0].path == "schema_version"


@pytest.mark.unit
def test_unknown_field_is_found(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    payload["world"]["secret"] = True
    issues = validate_payload(payload)
    assert IssueCode.UNKNOWN_FIELD in {item.code for item in issues}


@pytest.mark.unit
def test_all_issue_classes_are_reachable(project_document: ProjectDocument) -> None:
    """Шесть классов из G1-05 действительно обнаруживаются.

    Ошибки схемы и ошибки связности собираются в разных проходах: пока
    документ не соответствует схеме, проверять ссылки не на чем.
    """
    schema_payload = as_payload(project_document)
    del schema_payload["world"]["terrain"]
    schema_payload["world"]["lighting"]["cloudiness"] = 5.0
    schema_payload["world"]["secret"] = True

    schema_issues = validate_payload(schema_payload)
    found = {item.code for item in schema_issues}
    assert {
        IssueCode.MISSING_FIELD,
        IssueCode.INVALID_RANGE,
        IssueCode.UNKNOWN_FIELD,
    } <= found

    link_payload = as_payload(project_document)
    link_payload["exercises"][0]["world_id"] = "W-99"
    link_payload["agents"].append(link_payload["agents"][0])
    found |= {item.code for item in validate_payload(link_payload)}
    assert {IssueCode.DANGLING_REFERENCE, IssueCode.DUPLICATE_ID} <= found

    type_payload = as_payload(project_document)
    type_payload["world"]["seed"] = "сорок два"
    found |= {item.code for item in validate_payload(type_payload)}

    version_payload = as_payload(project_document)
    version_payload["schema_version"] = "7.5"
    found |= {item.code for item in validate_payload(version_payload)}
    assert {
        IssueCode.MISSING_FIELD,
        IssueCode.INVALID_TYPE,
        IssueCode.UNKNOWN_FIELD,
        IssueCode.INVALID_RANGE,
        IssueCode.DANGLING_REFERENCE,
        IssueCode.DUPLICATE_ID,
        IssueCode.UNKNOWN_VERSION,
    } <= found


@pytest.mark.unit
def test_mission_requires_single_start(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    payload["exercises"][0]["missions"][0]["elements"] = [
        {"id": "a", "type": "return_to_base", "next": []},
        {"id": "b", "type": "return_to_base", "next": []},
    ]
    issues = validate_payload(payload)
    assert any(item.code == IssueCode.INVALID_GRAPH for item in issues)


@pytest.mark.unit
def test_mission_cycle_without_loop_is_error(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    payload["exercises"][0]["missions"][0]["elements"] = [
        {"id": "start", "type": "start", "next": ["wp"]},
        {"id": "wp", "type": "waypoint", "pose": [1, 2, 0], "next": ["start"]},
    ]
    issues = validate_payload(payload)
    cycles = [item for item in issues if item.code == IssueCode.INVALID_GRAPH]
    assert cycles
    assert any("loop" in item.message for item in cycles)


@pytest.mark.unit
def test_mission_cycle_via_loop_is_allowed(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    payload["exercises"][0]["missions"][0]["elements"] = [
        {"id": "start", "type": "start", "next": ["loop"]},
        {"id": "loop", "type": "loop", "iterations": 3, "next": ["start"]},
    ]
    issues = validate_payload(payload)
    assert not [
        item for item in issues if item.code == IssueCode.INVALID_GRAPH and "loop" in item.message
    ]


@pytest.mark.unit
def test_unreachable_element_is_warning(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    payload["exercises"][0]["missions"][0]["elements"].append(
        {"id": "lost", "type": "return_to_base", "next": []}
    )
    issues = validate_payload(payload)
    warnings = [item for item in issues if item.severity == "warning"]
    assert any(item.path.endswith(".elements.2") for item in warnings)


@pytest.mark.unit
def test_pipeline_cycle_is_found(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    payload["pipelines"][0]["edges"] = [
        {"from": "src.frame", "to": "sink.events"},
        {"from": "sink.events", "to": "src.frame"},
    ]
    issues = validate_payload(payload)
    assert any(item.code == IssueCode.INVALID_GRAPH for item in issues)


@pytest.mark.unit
def test_pipeline_dangling_edge_is_found(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    payload["pipelines"][0]["edges"] = [{"from": "ghost.frame", "to": "sink.events"}]
    issues = validate_payload(payload)
    assert any(
        item.code == IssueCode.DANGLING_REFERENCE and item.path.endswith("edges.0.from")
        for item in issues
    )


@pytest.mark.unit
def test_report_links_to_missing_pipeline(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    payload["reports"][0]["pipeline_ids"] = ["PIPE-NOPE"]
    issues = validate_payload(payload)
    assert any(item.path == "reports.0.pipeline_ids.0" for item in issues)


@pytest.mark.unit
def test_joint_to_missing_link_is_found(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    payload["agents"][0]["joints"][0]["child"] = "ghost"
    issues = validate_payload(payload)
    assert any(item.path == "agents.0.joints.0.child" for item in issues)


@pytest.mark.unit
def test_building_to_missing_zone_is_found(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    payload["world"]["buildings"] = [
        {"id": "B-01", "zone": "Z-99", "footprint": [40, 25], "floors": 3}
    ]
    issues = validate_payload(payload)
    assert any(item.path == "world.buildings.0.zone" for item in issues)


@pytest.mark.unit
def test_describe_lists_paths(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    payload["exercises"][0]["world_id"] = "W-99"
    issues = validate_payload(payload)
    text = describe(issues)
    assert "exercises.0.world_id" in text


@pytest.mark.unit
def test_issue_serializes_to_dict(project_document: ProjectDocument) -> None:
    payload = as_payload(project_document)
    payload["exercises"][0]["world_id"] = "W-99"
    issues = validate_payload(payload)
    as_dict = issues[0].to_dict()
    assert set(as_dict) == {"code", "path", "message", "severity"}
