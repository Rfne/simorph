"""Тесты канонизации и хэша (T1-02, G1-07)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from simorph.core.canonical import (
    canonical_bytes,
    canonical_payload,
    canonical_text,
    compute_hash,
    dump_document,
)
from simorph.core.models.project import ProjectDocument


@pytest.mark.unit
def test_hash_is_independent_of_key_order(project_document: ProjectDocument) -> None:
    payload = project_document.model_dump(mode="json", by_alias=True)
    reversed_payload = json.loads(json.dumps(dict(reversed(list(payload.items())))))
    other = ProjectDocument.model_validate(reversed_payload)

    assert compute_hash(project_document) == compute_hash(other)


@pytest.mark.unit
def test_hash_is_independent_of_formatting(project_document: ProjectDocument) -> None:
    compact = canonical_text(project_document)
    spaced = json.dumps(json.loads(compact), indent=4, ensure_ascii=False)
    other = ProjectDocument.model_validate(json.loads(spaced))

    assert compute_hash(project_document) == compute_hash(other)
    assert canonical_bytes(project_document) == canonical_bytes(other)


@pytest.mark.unit
def test_hash_has_sha256_prefix(project_document: ProjectDocument) -> None:
    digest = compute_hash(project_document)
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


@pytest.mark.unit
def test_canonical_text_has_no_spaces(project_document: ProjectDocument) -> None:
    text = canonical_text(project_document)
    assert ", " not in text
    assert ": " not in text
    assert "\n" not in text


@pytest.mark.unit
def test_hash_changes_with_content(project_document: ProjectDocument) -> None:
    changed = project_document.model_copy(update={"name": "другое имя"})
    assert compute_hash(project_document) != compute_hash(changed)


@pytest.mark.unit
def test_hash_ignores_timezone_representation(project_document: ProjectDocument) -> None:
    shifted = project_document.created_at.astimezone(timezone(timedelta(hours=3)))
    other = project_document.model_copy(update={"created_at": shifted})
    assert compute_hash(project_document) == compute_hash(other)


@pytest.mark.unit
def test_canonical_payload_is_plain_dict(project_document: ProjectDocument) -> None:
    payload = canonical_payload(project_document)
    assert isinstance(payload, dict)
    assert isinstance(payload["created_at"], str)
    assert payload["created_at"].endswith("Z")


@pytest.mark.unit
def test_dump_document_is_readable_and_sorted(project_document: ProjectDocument) -> None:
    text = dump_document(project_document)
    assert text.endswith("\n")
    assert '\n  "agents"' in text or '\n  "created_at"' in text
    keys = [line.split('"')[1] for line in text.splitlines() if line.startswith('  "')]
    assert keys == sorted(keys)


@pytest.mark.unit
def test_dump_document_uses_lf(project_document: ProjectDocument) -> None:
    assert "\r\n" not in dump_document(project_document)


@pytest.mark.unit
def test_naive_datetime_is_rejected(project_document: ProjectDocument) -> None:
    naive = project_document.model_copy(update={"created_at": datetime(2026, 1, 1)})  # noqa: DTZ001
    with pytest.raises(ValueError, match="часового пояса"):
        canonical_text(naive)


@pytest.mark.unit
def test_same_project_on_two_machines_gives_same_hash(
    project_document: ProjectDocument,
) -> None:
    first = compute_hash(project_document)
    second = compute_hash(ProjectDocument.model_validate(project_document.model_dump(mode="json")))
    assert first == second


@pytest.mark.unit
def test_utc_moment_is_preserved(project_document: ProjectDocument) -> None:
    assert canonical_payload(project_document)["created_at"] == "2026-11-20T10:00:00Z"
    assert project_document.created_at.tzinfo == UTC
