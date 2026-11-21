"""Тесты вкладки профиля и JSON-редактора (T3-02, G3-02, G3-06)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QComboBox

from simorph.core.models.project import ProjectDocument
from simorph.core.profile import build_reference_profile
from simorph.core.schema_version import CURRENT_VERSION
from simorph.settings import ApplicationSettings
from simorph.ui.commands import DocumentStore
from simorph.ui.json_editor import JsonEditor
from simorph.ui.profile_editor import ProfileEditor


@pytest.fixture
def store(project_document: ProjectDocument) -> DocumentStore:
    document = project_document.model_copy(update={"profile": build_reference_profile("agroforce")})
    return DocumentStore(document)


@pytest.fixture
def editor(
    qtbot: object, store: DocumentStore, isolated_settings: ApplicationSettings
) -> ProfileEditor:
    created = ProfileEditor(store, isolated_settings)
    qtbot.addWidget(created)  # type: ignore[attr-defined]
    return created


def _set_json(editor: ProfileEditor, payload: dict[str, object]) -> None:
    editor.editor.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))


@pytest.mark.ui
def test_table_shows_all_reference_parameters(editor: ProfileEditor) -> None:
    assert editor.table.rowCount() == 75
    assert editor.table.item(0, 0) is not None
    assert editor.table.item(0, 0).text() == "П-01"
    assert isinstance(editor.table.cellWidget(0, 2), QComboBox)


@pytest.mark.ui
def test_metrics_match_reference(editor: ProfileEditor) -> None:
    assert "κ 42/75" in editor.metrics_text
    assert "κ+ 57/75" in editor.metrics_text
    assert "P_gen 17" in editor.metrics_text
    assert editor.projection_table.rowCount() == 52


@pytest.mark.ui
def test_apply_valid_json_updates_document(editor: ProfileEditor, store: DocumentStore) -> None:
    payload = json.loads(editor.editor.toPlainText())
    payload["customer"] = "АО «Новый»"
    _set_json(editor, payload)

    assert editor.apply_text()
    assert store.document.profile.customer == "АО «Новый»"
    assert store.stack.count() == 1

    store.undo()
    assert store.document.profile.customer != "АО «Новый»"


@pytest.mark.ui
def test_broken_json_blocks_apply(editor: ProfileEditor) -> None:
    editor.editor.setPlainText('{\n  "customer": ,\n}')

    assert not editor.apply_text()
    assert "строка 2" in editor.status_text


@pytest.mark.ui
def test_unknown_fields_are_reported(editor: ProfileEditor) -> None:
    payload = json.loads(editor.editor.toPlainText())
    payload["owner"] = "Иванов"
    _set_json(editor, payload)

    assert editor.check_text()
    assert [item.name for item in editor.unknown_fields] == ["owner"]
    assert "неизвестных полей сохранено: 1" in editor.status_text


@pytest.mark.ui
def test_class_combo_writes_document(
    qtbot: object, project_document: ProjectDocument, isolated_settings: ApplicationSettings
) -> None:
    from simorph.core.models.profile import ProfileSection

    section = ProfileSection(
        schema_version=CURRENT_VERSION,
        parameters=[{"id": "П-06", "value": 1.0, "block": "B"}],
    )
    store = DocumentStore(project_document.model_copy(update={"profile": section}))
    created = ProfileEditor(store, isolated_settings)
    qtbot.addWidget(created)  # type: ignore[attr-defined]

    combo = created.table.cellWidget(0, 2)
    assert isinstance(combo, QComboBox)
    combo.setCurrentIndex(combo.findData("C"))

    assert store.document.profile.parameters[0].klass == "C"
    store.undo()
    assert store.document.profile.parameters[0].klass is None


@pytest.mark.ui
def test_export_writes_envelope(editor: ProfileEditor, tmp_path: Path) -> None:
    target = tmp_path / "profile.json"

    assert editor.export_file(target)

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["export_version"] == "1.0"
    assert payload["hash"].startswith("sha256:")
    assert len(payload["profile"]["parameters"]) == 75


@pytest.mark.ui
def test_export_nda_profile_is_refused_outside_root(
    qtbot: object,
    project_document: ProjectDocument,
    isolated_settings: ApplicationSettings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = DocumentStore(
        project_document.model_copy(update={"profile": build_reference_profile("tatneft")})
    )
    created = ProfileEditor(store, isolated_settings)
    qtbot.addWidget(created)  # type: ignore[attr-defined]
    shown: list[str] = []
    monkeypatch.setattr(
        "simorph.ui.profile_editor.QMessageBox.warning",
        lambda _parent, _title, text: shown.append(text),
    )

    assert not created.export_file(tmp_path / "leak.json")
    assert shown
    assert not (tmp_path / "leak.json").exists()


@pytest.mark.ui
def test_import_reads_file_and_applies(
    editor: ProfileEditor, store: DocumentStore, tmp_path: Path
) -> None:
    source = tmp_path / "customer.json"
    source.write_text(
        json.dumps(
            {"customer": "Импорт", "parameters": [], "owner": "Петров"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert editor.import_file(source)
    assert store.document.profile.customer == "Импорт"
    assert [item.name for item in editor.unknown_fields] == ["owner"]


@pytest.mark.ui
def test_dispose_unsubscribes(editor: ProfileEditor, store: DocumentStore) -> None:
    listeners = len(store._listeners)

    editor.dispose()

    assert len(store._listeners) == listeners - 1


@pytest.mark.ui
def test_json_editor_scans_and_toggles_folds(qtbot: object) -> None:
    widget = JsonEditor()
    qtbot.addWidget(widget)  # type: ignore[attr-defined]
    widget.setPlainText('{\n  "a": {\n    "b": 1\n  }\n}\n')

    assert widget.fold_ranges() == {0: 4, 1: 3}

    widget.fold_all()
    assert widget.collapsed_blocks() == {0}

    widget.toggle_fold(0)
    assert widget.collapsed_blocks() == set()

    widget.toggle_fold(1)
    assert widget.collapsed_blocks() == {1}

    widget.unfold_all()
    assert widget.collapsed_blocks() == set()


@pytest.mark.ui
def test_json_editor_highlight_error_moves_cursor(qtbot: object) -> None:
    widget = JsonEditor()
    qtbot.addWidget(widget)  # type: ignore[attr-defined]
    widget.setPlainText('{\n  "a": 1,\n  "b": 2\n}\n')

    widget.highlight_error(3, 3)

    cursor = widget.textCursor()
    assert cursor.blockNumber() == 2
    assert cursor.positionInBlock() == 2


@pytest.mark.ui
def test_main_window_opens_profile_tab(
    qtbot: object, isolated_settings: ApplicationSettings
) -> None:
    from simorph.ui.main_window import MainWindow

    window = MainWindow(isolated_settings)
    qtbot.addWidget(window)  # type: ignore[attr-defined]

    index = window.open_section("profile")
    widget = window.tabs.widget(index)

    assert isinstance(widget, ProfileEditor)
    assert window.open_section("profile") == index
    assert window.tabs.close_editor("profile", confirm=False)
    assert "profile" not in window._section_editors
