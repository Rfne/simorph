"""Golden-тесты ядра: эталонный JSON и стабильный хэш (T1-08, G1-07).

Эталон лежит в репозитории с переводами строк ``\\n``. Если тест падает
на Windows и проходит на Linux (или наоборот), значит форматирование
перестало быть каноническим.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simorph.core.canonical import compute_hash, dump_document
from simorph.core.models.project import ProjectDocument
from simorph.core.repository import load_project, save_project

GOLDEN = Path(__file__).parent / "golden" / "demo_project_v1.json"

#: Хэш эталонного проекта: меняется только вместе с эталоном и ADR.
GOLDEN_HASH = "sha256:df6ba519c8543c32ad2e7626280c662d28e98a1a4df7b231eed7dd0b396997d9"


@pytest.mark.regression
def test_dump_matches_golden(project_document: ProjectDocument) -> None:
    document = project_document.model_copy(update={"name": "demo"})
    expected = GOLDEN.read_text(encoding="utf-8")
    assert dump_document(document) == expected


@pytest.mark.regression
def test_golden_has_no_crlf() -> None:
    assert b"\r\n" not in GOLDEN.read_bytes()


@pytest.mark.regression
def test_golden_parses_and_validates() -> None:
    loaded = load_project(GOLDEN)
    assert loaded.document.name == "demo"
    assert loaded.document.schema_version == "1.0"
    assert loaded.applied_migrations == []


@pytest.mark.regression
def test_save_load_save_is_byte_stable(tmp_path: Path, project_document: ProjectDocument) -> None:
    path = tmp_path / "project.json"
    save_project(project_document, path, make_backup=False)
    first = path.read_bytes()

    save_project(load_project(path).document, path, make_backup=False)
    assert path.read_bytes() == first


@pytest.mark.regression
def test_hash_is_reproducible_from_golden() -> None:
    first = compute_hash(load_project(GOLDEN).document)
    second = compute_hash(load_project(GOLDEN).document)
    assert first == second
    assert first.startswith("sha256:")
    assert len(first) == 71


@pytest.mark.regression
def test_golden_hash_is_stable() -> None:
    """Хэш эталона фиксирован: расхождение означает смену формата."""
    digest = compute_hash(load_project(GOLDEN).document)
    assert digest == GOLDEN_HASH
