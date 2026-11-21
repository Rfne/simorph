"""Тесты инспектора свойств (T2-03, G2-02, G2-05)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)

from simorph.core.models.project import ProjectDocument
from simorph.ui.commands import DocumentStore
from simorph.ui.inspector import InspectorPanel


@pytest.fixture
def store(project_document: ProjectDocument) -> DocumentStore:
    return DocumentStore(project_document)


@pytest.fixture
def panel(qtbot: object, store: DocumentStore) -> InspectorPanel:
    created = InspectorPanel(store)
    qtbot.addWidget(created)  # type: ignore[attr-defined]
    return created


def _editor(panel: InspectorPanel, label: str) -> QWidget:
    """Поле формы по подписи строки."""
    form: QFormLayout = panel._form
    for row in range(form.rowCount()):
        label_item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
        if label_item is None or label_item.widget() is None:
            continue
        if label_item.widget().text() != label:
            continue
        field_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
        if field_item is not None and field_item.widget() is not None:
            return field_item.widget()
    raise AssertionError(f"поле {label!r} не найдено")


def _labels(panel: InspectorPanel) -> list[str]:
    form: QFormLayout = panel._form
    labels: list[str] = []
    for row in range(form.rowCount()):
        item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
        if item is not None and item.widget() is not None:
            labels.append(item.widget().text())
    return labels


@pytest.mark.ui
def test_form_is_built_from_schema(panel: InspectorPanel) -> None:
    panel.show_path("world")

    assert "seed" in _labels(panel)
    assert "рельеф" in _labels(panel)
    assert isinstance(_editor(panel, "seed"), QLineEdit)
    assert "WorldSection" in panel._hint.text()


@pytest.mark.ui
def test_int_bounds_come_from_schema(panel: InspectorPanel) -> None:
    panel.show_path("world")

    # seed разрешает значения до 2**32 - 1, а QSpinBox ограничен int32:
    # такое поле редактируется текстом, но значение всё равно проверяется схемой.
    seed = _editor(panel, "seed")
    assert isinstance(seed, QLineEdit)
    assert seed.text() == "42"


@pytest.mark.ui
def test_large_int_is_committed_through_text(panel: InspectorPanel, store: DocumentStore) -> None:
    panel.show_path("world")

    assert panel.commit("world.seed", 2**32 - 1)
    assert store.document.world.seed == 2**32 - 1

    assert panel.commit("world.seed", 2**32) is False
    assert store.document.world.seed == 2**32 - 1


@pytest.mark.ui
def test_float_bounds_come_from_schema(panel: InspectorPanel) -> None:
    panel.show_path("world.lighting")

    cloudiness = _editor(panel, "облачность")
    assert isinstance(cloudiness, QDoubleSpinBox)
    assert cloudiness.minimum() == 0.0
    assert cloudiness.maximum() == 1.0


@pytest.mark.ui
def test_literal_field_uses_combo(panel: InspectorPanel) -> None:
    panel.show_path("agents.0.sensors.0")

    kind = _editor(panel, "тип")
    assert isinstance(kind, QComboBox)
    assert kind.count() == 9
    assert kind.currentText() == "stereo_camera"


@pytest.mark.ui
def test_bool_field_uses_checkbox(panel: InspectorPanel) -> None:
    panel.show_path("settings")

    offline = _editor(panel, "офлайн")
    assert isinstance(offline, QCheckBox)
    assert offline.isChecked() is True


@pytest.mark.ui
def test_edit_changes_document_and_is_undoable(panel: InspectorPanel, store: DocumentStore) -> None:
    panel.show_path("world")

    assert panel.commit("world.seed", 777)
    assert store.document.world.seed == 777
    assert store.can_undo()

    store.undo()
    assert store.document.world.seed == 42


@pytest.mark.ui
def test_invalid_value_is_rejected_and_highlighted(
    panel: InspectorPanel, store: DocumentStore
) -> None:
    panel.show_path("world")

    assert panel.commit("world.seed", -5) is False
    assert store.document.world.seed == 42
    assert "#e5534b" in panel._status.styleSheet()
    assert all("border" in editor.styleSheet() for editor in panel._editors)


@pytest.mark.ui
def test_nested_row_emits_navigation(panel: InspectorPanel, qtbot: object) -> None:
    panel.show_path("world")
    received: list[str] = []
    panel.navigate_requested.connect(received.append)

    buttons = panel.findChildren(QPushButton)
    target = next(button for button in buttons if button.text() == "Открыть")
    qtbot.mouseClick(target, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]

    assert received == ["world.terrain"]


@pytest.mark.ui
def test_read_only_mode_disables_editing(panel: InspectorPanel, store: DocumentStore) -> None:
    panel.show_path("world")
    panel.set_read_only(True)

    assert panel.read_only is True
    assert all(not editor.isEnabled() for editor in panel._editors)
    assert panel.commit("world.seed", 5) is False
    assert store.document.world.seed == 42
    assert "только для чтения" in panel._status.text()

    panel.set_read_only(False)
    assert all(editor.isEnabled() for editor in panel._editors)


@pytest.mark.ui
def test_unknown_path_reports_unavailable(panel: InspectorPanel) -> None:
    panel.show_path("world.absent")

    assert panel._title.text() == "Объект недоступен"
    assert "absent" in panel._hint.text()
