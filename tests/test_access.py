"""Тесты доступа к значениям по пути (T2-03, T2-05)."""

from __future__ import annotations

import pytest

from simorph.core.access import read_value, split_path, write_value
from simorph.core.errors import ProjectError, ProjectValidationError
from simorph.core.models.project import ProjectDocument


@pytest.mark.unit
def test_split_path_drops_empty_parts() -> None:
    assert split_path("agents..0.id") == ["agents", "0", "id"]


@pytest.mark.unit
def test_read_value_by_path(project_document: ProjectDocument) -> None:
    assert read_value(project_document, "agents.0.agent_id") == "NRTC-01"
    assert read_value(project_document, "world.terrain.size_m") == (2000.0, 2000.0)


@pytest.mark.unit
def test_read_value_reports_missing_field(project_document: ProjectDocument) -> None:
    with pytest.raises(ProjectError) as exc_info:
        read_value(project_document, "world.absent")
    assert "absent" in str(exc_info.value)


@pytest.mark.unit
def test_read_value_reports_index_out_of_range(project_document: ProjectDocument) -> None:
    with pytest.raises(ProjectError) as exc_info:
        read_value(project_document, "agents.9.agent_id")
    assert "вне диапазона" in str(exc_info.value)


@pytest.mark.unit
def test_write_value_returns_new_document(project_document: ProjectDocument) -> None:
    updated = write_value(project_document, "world.seed", 7)

    assert updated.world.seed == 7
    assert project_document.world.seed == 42
    assert updated is not project_document


@pytest.mark.unit
def test_write_value_validates_schema(project_document: ProjectDocument) -> None:
    with pytest.raises(ProjectValidationError) as exc_info:
        write_value(project_document, "world.seed", -1)
    assert exc_info.value.issues


@pytest.mark.unit
def test_write_value_rejects_unknown_field(project_document: ProjectDocument) -> None:
    with pytest.raises(ProjectError) as exc_info:
        write_value(project_document, "world.unknown_field", 1)
    assert "отсутствует" in str(exc_info.value)


@pytest.mark.unit
def test_write_value_into_list_item(project_document: ProjectDocument) -> None:
    updated = write_value(project_document, "agents.0.sensors.0.rate_hz", 60.0)

    assert updated.agents[0].sensors[0].rate_hz == 60.0


@pytest.mark.unit
def test_write_value_rejects_empty_path(project_document: ProjectDocument) -> None:
    with pytest.raises(ProjectError):
        write_value(project_document, "", 1)
