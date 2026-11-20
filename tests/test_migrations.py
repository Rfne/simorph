"""Тесты миграций (T1-03, G1-06)."""

from __future__ import annotations

from typing import Any

import pytest

from simorph.core.errors import MigrationError, UnsupportedVersionError
from simorph.core.migrations import migrate_payload, payload_version, plan_migration
from simorph.core.models.project import ProjectDocument
from simorph.core.schema_version import CURRENT_VERSION, compare_versions, parse_version


def legacy_payload() -> dict[str, Any]:
    """Проект формата 0.9: общее поле ``version`` и ``id`` вместо ``*_id``."""
    return {
        "version": "0.9",
        "project_id": "3f1c2a9e-7b1d-4c2a-9e6f-1a2b3c4d5e6f",
        "name": "legacy",
        "created_at": "2026-01-01T00:00:00Z",
        "profile": {"schema_version": "0.9", "parameters": []},
        "world": {
            "world_id": "W-01",
            "seed": 42,
            "terrain": {"size_m": [2000, 2000]},
        },
        "agents": [{"id": "NRTC-01", "role": "rover"}],
        "exercises": [],
        "pipelines": [
            {
                "id": "PIPE-CMP-01",
                "kind": "computational",
                "nodes": [{"id": "src", "type": "source.frame"}],
            }
        ],
        "reports": [],
    }


@pytest.mark.unit
def test_legacy_project_migrates_to_current() -> None:
    result = migrate_payload(legacy_payload())
    assert result.applied == ["project:0.9→1.0"]
    assert payload_version(result.payload) == CURRENT_VERSION


@pytest.mark.unit
def test_migration_is_idempotent() -> None:
    once = migrate_payload(legacy_payload()).payload
    twice = migrate_payload(once)
    assert twice.applied == []
    assert twice.payload == once


@pytest.mark.unit
def test_migration_keeps_data() -> None:
    payload = migrate_payload(legacy_payload()).payload
    assert payload["project_id"] == "3f1c2a9e-7b1d-4c2a-9e6f-1a2b3c4d5e6f"
    assert payload["name"] == "legacy"
    assert payload["agents"][0]["agent_id"] == "NRTC-01"
    assert payload["pipelines"][0]["pipeline_id"] == "PIPE-CMP-01"
    assert "version" not in payload


@pytest.mark.unit
def test_migration_adds_defaults() -> None:
    payload = migrate_payload(legacy_payload()).payload
    assert payload["transport"]["mode"] == "inproc"
    assert payload["settings"]["theme"] == "dark"
    assert payload["updated_at"]


@pytest.mark.unit
def test_migrated_document_parses() -> None:
    payload = migrate_payload(legacy_payload()).payload
    document = ProjectDocument.model_validate(payload)
    assert document.schema_version == CURRENT_VERSION
    assert document.agents[0].agent_id == "NRTC-01"


@pytest.mark.unit
def test_future_version_is_rejected() -> None:
    payload = legacy_payload()
    payload["version"] = "9.0"
    with pytest.raises(UnsupportedVersionError) as exc_info:
        migrate_payload(payload)
    assert exc_info.value.version == "9.0"
    assert "новее" in str(exc_info.value)


@pytest.mark.unit
def test_unknown_version_is_rejected() -> None:
    payload = legacy_payload()
    payload["version"] = "0.1"
    with pytest.raises(UnsupportedVersionError):
        migrate_payload(payload)


@pytest.mark.unit
def test_missing_version_is_rejected() -> None:
    payload = legacy_payload()
    del payload["version"]
    with pytest.raises(UnsupportedVersionError):
        migrate_payload(payload)


@pytest.mark.unit
def test_current_version_needs_no_migration(project_document: ProjectDocument) -> None:
    payload = project_document.model_dump(mode="json")
    result = migrate_payload(payload)
    assert result.applied == []
    assert result.payload == payload


@pytest.mark.unit
def test_plan_is_empty_for_current_version() -> None:
    assert plan_migration("project", CURRENT_VERSION) == []


@pytest.mark.unit
def test_plan_without_registered_step_fails() -> None:
    with pytest.raises(MigrationError):
        plan_migration("world", "0.9")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [("1.0", "1.0", 0), ("1.9", "1.10", -1), ("2.0", "1.99", 1)],
)
def test_version_comparison_is_numeric(left: str, right: str, expected: int) -> None:
    assert compare_versions(left, right) == expected


@pytest.mark.unit
def test_parse_version_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_version("v1")
