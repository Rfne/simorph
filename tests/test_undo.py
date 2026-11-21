"""Тесты undo/redo на QUndoStack (T2-05, G2-03)."""

from __future__ import annotations

import pytest

from simorph.core.models.project import ProjectDocument
from simorph.ui.commands import UNDO_LIMIT, DocumentStore


@pytest.mark.ui
def test_edit_is_undoable(project_document: ProjectDocument) -> None:
    store = DocumentStore(project_document)
    store.set_value("world.seed", 7)

    assert store.document.world.seed == 7
    assert store.can_undo()

    store.undo()
    assert store.document.world.seed == 42
    assert store.can_redo()

    store.redo()
    assert store.document.world.seed == 7


@pytest.mark.ui
def test_hundred_steps_are_undone_and_redone(project_document: ProjectDocument) -> None:
    store = DocumentStore(project_document)
    for value in range(100):
        store.set_value("world.seed", value)

    assert store.stack.count() == 100
    for _ in range(100):
        store.undo()
    assert store.document.world.seed == 42

    for _ in range(100):
        store.redo()
    assert store.document.world.seed == 99


@pytest.mark.ui
def test_history_is_limited(project_document: ProjectDocument) -> None:
    store = DocumentStore(project_document)
    for value in range(UNDO_LIMIT + 20):
        store.set_value("world.seed", value)

    assert store.stack.count() == UNDO_LIMIT


@pytest.mark.ui
def test_save_does_not_add_step(project_document: ProjectDocument) -> None:
    store = DocumentStore(project_document)
    store.set_value("world.seed", 7)
    before = store.stack.count()

    store.mark_saved()

    assert store.stack.count() == before
    assert store.is_modified is False


@pytest.mark.ui
def test_edit_after_save_marks_modified(project_document: ProjectDocument) -> None:
    store = DocumentStore(project_document)
    store.mark_saved()
    assert store.is_modified is False

    store.set_value("world.seed", 5)
    assert store.is_modified is True


@pytest.mark.ui
def test_reset_clears_history(project_document: ProjectDocument) -> None:
    store = DocumentStore(project_document)
    store.set_value("world.seed", 7)

    store.reset(project_document)

    assert store.stack.count() == 0
    assert store.can_undo() is False


@pytest.mark.ui
def test_listener_sees_new_document(project_document: ProjectDocument) -> None:
    store = DocumentStore(project_document)
    seen: list[ProjectDocument] = []
    store.add_listener(seen.append)

    store.set_value("world.seed", 3)

    assert seen
    assert seen[-1].world.seed == 3
