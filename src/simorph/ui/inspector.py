"""Инспектор свойств, построенный из схемы (T2-03).

Форма собирается по ``model_fields`` выбранного объекта, поэтому новое поле
в схеме появляется в интерфейсе без правки UI-кода. Редактор выбирается
по аннотации: ``Literal`` — выпадающий список, ``bool`` — флажок, числа —
спинбоксы с границами из метаданных pydantic, строки — поле ввода.

Правка идёт через ``DocumentStore``, то есть одной командой undo. Значение
проверяется всей схемой: невалидное не применяется, поле подсвечивается,
а причина показывается внизу панели.
"""

from __future__ import annotations

import types
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from simorph.core.access import read_value
from simorph.core.errors import ProjectError, ProjectValidationError
from simorph.ui.commands import DocumentStore
from simorph.ui.tree_model import FIELD_TITLES

__all__ = ["InspectorPanel"]

#: Пределы спинбоксов, если схема их не задаёт.
_FALLBACK_LIMIT = 1_000_000.0

#: Границы ``QSpinBox``: схема может разрешать и большие значения (``seed``).
_INT32_LIMIT = 2**31 - 1

#: Красная рамка для поля с ошибкой.
_ERROR_STYLE = "border: 1px solid #e5534b;"


def _strip_optional(annotation: Any) -> Any:
    """Снимает ``| None`` с аннотации."""
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        args = [item for item in get_args(annotation) if item is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _literal_values(annotation: Any) -> tuple[str, ...]:
    """Допустимые значения ``Literal``, если аннотация — ``Literal``."""
    if get_origin(annotation) is Literal:
        return tuple(str(item) for item in get_args(annotation))
    return ()


def _bounds(info: FieldInfo) -> tuple[float | None, float | None]:
    """Границы значения из метаданных pydantic."""
    low: float | None = None
    high: float | None = None
    for item in info.metadata:
        for attribute in ("ge", "gt"):
            value = getattr(item, attribute, None)
            if isinstance(value, (int, float)):
                low = float(value)
        for attribute in ("le", "lt"):
            value = getattr(item, attribute, None)
            if isinstance(value, (int, float)):
                high = float(value)
    return low, high


def _describe(value: Any) -> str:
    """Текст для нередактируемых значений."""
    if isinstance(value, BaseModel):
        return value.__class__.__name__
    if isinstance(value, (list, tuple)):
        return f"{len(value)} эл."
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "да" if value else "нет"
    return str(value)


class InspectorPanel(QWidget):
    """Панель свойств выбранного объекта."""

    #: Запрос перехода к другому объекту (например, к вложенной сущности).
    navigate_requested = Signal(str)

    def __init__(self, store: DocumentStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._path = ""
        self._editors: list[QWidget] = []
        self._read_only = False

        self._title = QLabel("Объект не выбран")
        self._title.setWordWrap(True)
        self._title.setStyleSheet("font-weight: bold;")

        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: #8f949c;")

        self._status = QLabel("")
        self._status.setWordWrap(True)

        self._container = QWidget()
        self._form = QFormLayout(self._container)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._container)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self._title)
        layout.addWidget(self._hint)
        layout.addWidget(scroll, 1)
        layout.addWidget(self._status)

        self._store.add_listener(self._on_document_changed)

    def _on_document_changed(self, _document: object) -> None:
        """Перестраивает форму после правки документа."""
        self.refresh()

    # --- состояние ------------------------------------------------------

    @property
    def path(self) -> str:
        return self._path

    @property
    def read_only(self) -> bool:
        return self._read_only

    def set_read_only(self, value: bool) -> None:
        """Переводит форму в режим просмотра (проект занят другим экземпляром)."""
        self._read_only = bool(value)
        for editor in self._editors:
            editor.setEnabled(not self._read_only)
        if self._read_only:
            self._status.setText("проект открыт только для чтения")
            self._status.setStyleSheet("color: #c69026;")
        elif self._status.text().startswith("проект открыт только для чтения"):
            self._status.setText("")
            self._status.setStyleSheet("")

    def show_path(self, path: str) -> None:
        """Показывает свойства объекта по пути."""
        self._path = path
        self.refresh()

    def refresh(self) -> None:
        """Перестраивает форму по текущему документу."""
        self._clear_form()
        self._status.setText("")
        if not self._path:
            self._title.setText("Объект не выбран")
            self._hint.setText("Выберите узел в дереве проекта.")
            return

        try:
            value = read_value(self._store.document, self._path)
        except ProjectError as exc:
            self._title.setText("Объект недоступен")
            self._hint.setText(str(exc))
            return

        self._title.setText(self._title_for(value))
        if isinstance(value, BaseModel):
            self._hint.setText(f"{value.__class__.__name__} · {self._path}")
            self._build_model_form(value)
        elif isinstance(value, (list, tuple)):
            self._hint.setText(f"Список, элементов: {len(value)}")
            self._build_list_form(value)
        else:
            self._hint.setText(self._path)
            self._build_scalar_form(value)

    def _title_for(self, value: Any) -> str:
        if isinstance(value, BaseModel):
            for key in ("name", "id", "agent_id", "exercise_id", "pipeline_id", "report_id"):
                candidate = getattr(value, key, None)
                if candidate:
                    return f"{FIELD_TITLES.get(key, key)}: {candidate}"
            return value.__class__.__name__
        return self._path.split(".")[-1] if self._path else "Проект"

    # --- построение формы -----------------------------------------------

    def _clear_form(self) -> None:
        while self._form.count():
            item = self._form.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._editors.clear()

    def _build_model_form(self, model: BaseModel) -> None:
        model_type = type(model)
        for name, info in model_type.model_fields.items():
            if name == "schema_version":
                continue
            alias = info.alias or name
            path = f"{self._path}.{alias}" if self._path else alias
            value = getattr(model, name)
            label = FIELD_TITLES.get(alias, alias)
            if isinstance(value, (BaseModel, list, tuple)):
                self._add_nested_row(label, path, value)
            else:
                editor = self._editor_for(info.annotation, value, path, info)
                self._form.addRow(label, editor)

    def _build_list_form(self, values: Any) -> None:
        for index, item in enumerate(values):
            path = f"{self._path}.{index}"
            if isinstance(item, BaseModel):
                self._add_nested_row(f"#{index}", path, item)
            else:
                editor = self._editor_for(type(item), item, path)
                self._form.addRow(f"#{index}", editor)

    def _build_scalar_form(self, value: Any) -> None:
        editor = self._editor_for(type(value), value, self._path)
        self._form.addRow("значение", editor)

    def _add_nested_row(self, label: str, path: str, value: Any) -> None:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        text = QLabel(_describe(value))
        button = QPushButton("Открыть")
        button.setAutoDefault(False)
        button.clicked.connect(
            lambda _checked=False, target=path: self.navigate_requested.emit(target)
        )
        row_layout.addWidget(text, 1)
        row_layout.addWidget(button)
        self._form.addRow(label, row)

    # --- редакторы значений ---------------------------------------------

    def _editor_for(
        self,
        annotation: Any,
        value: Any,
        path: str,
        info: FieldInfo | None = None,
    ) -> QWidget:
        base = _strip_optional(annotation)
        literals = _literal_values(base)
        low, high = _bounds(info) if info is not None else (None, None)

        if literals:
            combo = QComboBox()
            combo.addItems(list(literals))
            if str(value) in literals:
                combo.setCurrentText(str(value))
            combo.currentTextChanged.connect(lambda text, target=path: self._commit(target, text))
            return self._register(combo)

        if base is bool or isinstance(value, bool):
            check = QCheckBox()
            check.setChecked(bool(value))
            check.toggled.connect(lambda state, target=path: self._commit(target, state))
            return self._register(check)

        if base is int or (isinstance(value, int) and not isinstance(value, bool)):
            # QSpinBox ограничен int32, а схема разрешает и большие значения
            # (например ``seed`` до 2**32 - 1): тогда нужен текстовый редактор.
            too_wide = (low is not None and low < -_INT32_LIMIT - 1) or (
                high is not None and high > _INT32_LIMIT
            )
            if too_wide:
                edit = QLineEdit(str(value) if value is not None else "0")
                edit.setPlaceholderText("целое число")
                edit.editingFinished.connect(
                    lambda target=path, widget=edit: self._commit_int(target, widget.text())
                )
                return self._register(edit)
            spin = QSpinBox()
            spin.setRange(
                max(int(low), -_INT32_LIMIT - 1) if low is not None else -_INT32_LIMIT - 1,
                min(int(high), _INT32_LIMIT) if high is not None else _INT32_LIMIT,
            )
            spin.setValue(int(value) if value is not None else 0)
            spin.editingFinished.connect(
                lambda target=path, widget=spin: self._commit(target, widget.value())
            )
            return self._register(spin)

        if base is float or isinstance(value, float):
            double = QDoubleSpinBox()
            double.setDecimals(4)
            double.setRange(
                low if low is not None else -_FALLBACK_LIMIT,
                high if high is not None else _FALLBACK_LIMIT,
            )
            double.setValue(float(value) if value is not None else 0.0)
            double.editingFinished.connect(
                lambda target=path, widget=double: self._commit(target, widget.value())
            )
            return self._register(double)

        if isinstance(value, (list, tuple)):
            edit = QLineEdit(", ".join(str(item) for item in value))
            edit.editingFinished.connect(
                lambda target=path, widget=edit: self._commit_sequence(target, widget.text())
            )
            return self._register(edit)

        edit = QLineEdit("" if value is None else str(value))
        edit.setPlaceholderText("не задано" if value is None else "")
        edit.editingFinished.connect(
            lambda target=path, widget=edit: self._commit_text(target, widget.text(), value)
        )
        return self._register(edit)

    def _register(self, editor: QWidget) -> QWidget:
        """Запоминает редактор и применяет текущий режим доступа."""
        editor.setEnabled(not self._read_only)
        self._editors.append(editor)
        return editor

    # --- запись ---------------------------------------------------------

    def _commit(self, path: str, value: Any) -> None:
        if self.commit(path, value):
            self._status.setText(f"изменено: {path}")
            self._status.setStyleSheet("color: #2f9e6b;")

    def commit(self, path: str, value: Any) -> bool:
        """Записывает значение; ``False``, если схема его не приняла."""
        if self._read_only:
            self._report_error(path, "проект открыт только для чтения")
            return False
        try:
            self._store.set_value(path, value)
        except ProjectValidationError as exc:
            self._report_error(path, exc.issues[0].message if exc.issues else str(exc))
            return False
        except ProjectError as exc:
            self._report_error(path, str(exc))
            return False
        return True

    def _commit_text(self, path: str, text: str, previous: Any) -> None:
        if previous is None and not text.strip():
            return
        self._commit(path, text)

    def _commit_int(self, path: str, text: str) -> None:
        """Записывает целое из текстового поля (значения больше int32)."""
        stripped = text.strip()
        try:
            number = int(stripped)
        except ValueError:
            self._report_error(path, "ожидалось целое число")
            return
        self._commit(path, number)

    def _commit_sequence(self, path: str, text: str) -> None:
        parts = [part.strip() for part in text.split(",") if part.strip()]
        try:
            numbers = [float(part) for part in parts]
        except ValueError:
            self._report_error(path, "ожидался список чисел через запятую")
            return
        self._commit(path, numbers)

    def _report_error(self, path: str, message: str) -> None:
        self._status.setText(f"{path}: {message}")
        self._status.setStyleSheet("color: #e5534b;")
        for editor in self._editors:
            editor.setStyleSheet(_ERROR_STYLE)
