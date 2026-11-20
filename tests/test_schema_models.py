"""Тесты схемы разделов проекта (T1-01)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from simorph.core.models.agent import AgentSection, Sensor
from simorph.core.models.profile import ProfileParameter, ProfileSection
from simorph.core.models.project import ProjectDocument
from simorph.core.validation import parse_document


@pytest.mark.unit
def test_new_project_has_seven_sections(project_document: ProjectDocument) -> None:
    assert set(project_document.sections()) == {
        "project",
        "profile",
        "world",
        "agent",
        "exercise",
        "pipeline",
        "report",
    }


@pytest.mark.unit
def test_sections_have_own_schema_version(project_document: ProjectDocument) -> None:
    assert project_document.schema_version == "1.0"
    assert project_document.profile.schema_version == "1.0"
    assert project_document.world.schema_version == "1.0"
    assert project_document.agents[0].schema_version == "1.0"
    assert project_document.exercises[0].schema_version == "1.0"
    assert project_document.pipelines[0].schema_version == "1.0"
    assert project_document.reports[0].schema_version == "1.0"


@pytest.mark.unit
def test_unknown_field_is_rejected_with_path(project_document: ProjectDocument) -> None:
    payload = project_document.model_dump(mode="json", by_alias=True)
    payload["world"]["mystery"] = 1

    with pytest.raises(ValidationError) as exc_info:
        ProjectDocument.model_validate(payload)

    errors = exc_info.value.errors()
    assert errors[0]["type"] == "extra_forbidden"
    assert "mystery" in errors[0]["loc"]


@pytest.mark.unit
def test_unknown_top_level_field_is_rejected(project_document: ProjectDocument) -> None:
    payload = project_document.model_dump(mode="json", by_alias=True)
    payload["dashboards"] = []

    with pytest.raises(ValidationError):
        ProjectDocument.model_validate(payload)


@pytest.mark.unit
def test_invalid_type_reports_field_path(project_document: ProjectDocument) -> None:
    payload = project_document.model_dump(mode="json", by_alias=True)
    payload["world"]["seed"] = "много"

    with pytest.raises(ValidationError) as exc_info:
        ProjectDocument.model_validate(payload)

    loc = exc_info.value.errors()[0]["loc"]
    assert loc[:2] == ("world", "seed")


@pytest.mark.unit
def test_missing_required_field_is_rejected(project_document: ProjectDocument) -> None:
    payload = project_document.model_dump(mode="json", by_alias=True)
    del payload["world"]["terrain"]

    with pytest.raises(ValidationError) as exc_info:
        ProjectDocument.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "missing"


@pytest.mark.unit
def test_range_violation_is_rejected(project_document: ProjectDocument) -> None:
    payload = project_document.model_dump(mode="json", by_alias=True)
    payload["world"]["lighting"]["cloudiness"] = 1.5

    with pytest.raises(ValidationError):
        ProjectDocument.model_validate(payload)


@pytest.mark.unit
def test_profile_parameter_requires_value_or_range() -> None:
    with pytest.raises(ValidationError):
        ProfileParameter(id="П-99")


@pytest.mark.unit
def test_profile_parameter_accepts_class_alias() -> None:
    parameter = ProfileParameter.model_validate({"id": "П-06", "value": 1.5, "class": "C"})
    assert parameter.klass == "C"
    assert parameter.model_dump(by_alias=True)["class"] == "C"


@pytest.mark.unit
def test_profile_parameter_range_order_is_checked() -> None:
    with pytest.raises(ValidationError):
        ProfileParameter(id="П-07", range=(10.0, 1.0))


@pytest.mark.unit
def test_stereo_camera_requires_baseline() -> None:
    with pytest.raises(ValidationError):
        Sensor(id="cam", type="stereo_camera", rate_hz=120.0, resolution=(1280, 800))


@pytest.mark.unit
def test_actuator_requires_joint_for_drive_axis() -> None:
    payload = {
        "schema_version": "1.0",
        "agent_id": "A-01",
        "actuators": [{"id": "drive", "type": "drive_axis"}],
    }
    with pytest.raises(ValidationError):
        AgentSection.model_validate(payload)


@pytest.mark.unit
def test_profile_section_requires_schema_version() -> None:
    with pytest.raises(ValidationError):
        ProfileSection.model_validate({"parameters": []})


@pytest.mark.unit
def test_parse_document_reports_all_issues(project_document: ProjectDocument) -> None:
    payload = project_document.model_dump(mode="json", by_alias=True)
    payload["world"]["seed"] = "нет"
    payload["profile"]["extra"] = True

    from simorph.core.errors import ProjectValidationError

    with pytest.raises(ProjectValidationError) as exc_info:
        parse_document(payload)

    assert len(exc_info.value.issues) >= 2


@pytest.mark.unit
def test_new_project_is_valid(project_document: ProjectDocument) -> None:
    from simorph.core.validation import validate_document

    issues = validate_document(project_document)
    assert [item for item in issues if item.severity == "error"] == []
