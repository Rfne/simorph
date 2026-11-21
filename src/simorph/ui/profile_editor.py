"""Вкладка профиля заказчика: JSON, таблица параметров и проекция (T3-02…T3-05).

Вкладка показывает раздел ``profile`` в трёх видах:

- JSON профиля с подсветкой, сворачиванием блоков и переходом к ошибке;
- таблицу параметров: класс и целевой вектор правятся прямо в ней и сразу
  попадают в документ (одна команда undo на правку);
- проекцию в ``P_gen``/``P_sim``/``P_hw`` и показатели κ/κ+.

Правка JSON применяется к документу целиком: раздел проходит ту же проверку
схемой, что и при открытии проекта, поэтому невалидный JSON не может попасть
в файл (G3-02). Неизвестные поля при импорте не теряются — они уходят в
``extra`` и возвращаются при экспорте (T3-01).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from simorph.core.errors import ProjectError, ProjectValidationError
from simorph.core.issues import Issue
from simorph.core.models.profile import ProfileParameter, ProfileSection
from simorph.core.profile import (
    BLOCK_TITLES,
    NdaViolation,
    ProjectionResult,
    UnknownField,
    completeness,
    export_profile_payload,
    import_profile_payload,
    parse_profile_json,
    project_profile,
)
from simorph.core.profile.exporting import dump_profile_export
from simorph.settings import ApplicationSettings
from simorph.ui.commands import DocumentStore
from simorph.ui.json_editor import JsonEditor

__all__ = ["ProfileEditor"]

#: Задержка проверки JSON после правки, мс.
CHECK_DELAY_MS = 300

#: Подписи колонок таблицы параметров.
_PARAMETER_COLUMNS = ("№", "Блок", "Класс", "Вектор", "Ключи проекции", "Значение", "Ед.")

#: Подписи колонок таблицы проекции.
_PROJECTION_COLUMNS = ("Параметр", "Класс", "Вектор", "Ключи", "Значение", "Ед.")

#: Пустой вариант в выпадающих списках класса и вектора.
_EMPTY = "—"

#: Фон параметра без класса.
_UNMARKED_COLOR = QColor("#7a3a37")
_UNMARKED_COLOR.setAlpha(90)

#: Цвет текста ошибки.
_ERROR_STYLE = "color: #e5534b;"


class ProfileEditor(QWidget):
    """Редактор раздела ``profile``."""

    def __init__(
        self,
        store: DocumentStore,
        settings: ApplicationSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._settings = settings
        self._suppress = False
        self._dirty = False
        self._unknown: list[UnknownField] = []
        self._issues: list[Issue] = []
        self._projection = ProjectionResult()
        self.setObjectName("editor_profile")

        self._editor = JsonEditor()
        self._editor.textChanged.connect(self._on_text_changed)

        self._table = QTableWidget(0, len(_PARAMETER_COLUMNS))
        self._table.setHorizontalHeaderLabels(list(_PARAMETER_COLUMNS))
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)

        self._projection_table = QTableWidget(0, len(_PROJECTION_COLUMNS))
        self._projection_table.setHorizontalHeaderLabels(list(_PROJECTION_COLUMNS))
        self._projection_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._projection_table.verticalHeader().setVisible(False)

        self._report = QPlainTextEdit()
        self._report.setReadOnly(True)
        self._report.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)

        right = QTabWidget()
        right.addTab(self._table, "Параметры")
        right.addTab(self._projection_table, "Проекция")
        right.addTab(self._report, "Отчёт")

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._editor)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self._kappa_label = QLabel("")
        self._kappa_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(self._build_toolbar())
        layout.addWidget(splitter, 1)
        layout.addWidget(self._kappa_label)
        layout.addWidget(self._status_label)

        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(CHECK_DELAY_MS)
        self._check_timer.timeout.connect(self.check_text)

        self._store.add_listener(self._on_document_changed)
        self.destroyed.connect(self._on_destroyed)
        self.refresh()

    # --- построение -----------------------------------------------------

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._apply_button = QPushButton("Применить JSON")
        self._apply_button.setToolTip("Проверить JSON и записать профиль в проект")
        self._apply_button.clicked.connect(self.apply_text)

        import_button = QPushButton("Импорт…")
        import_button.clicked.connect(lambda: self.import_file())
        export_button = QPushButton("Экспорт…")
        export_button.clicked.connect(lambda: self.export_file())

        fold_button = QPushButton("Свернуть блоки")
        fold_button.clicked.connect(lambda: self._editor.fold_all())
        unfold_button = QPushButton("Развернуть")
        unfold_button.clicked.connect(lambda: self._editor.unfold_all())

        self._nda_badge = QLabel("NDA")
        self._nda_badge.setStyleSheet(
            "color: #ffffff; background-color: #8b3a3a; padding: 2px 8px; border-radius: 3px;"
        )
        self._nda_badge.setVisible(False)

        for widget in (
            self._apply_button,
            import_button,
            export_button,
            fold_button,
            unfold_button,
            self._nda_badge,
        ):
            row.addWidget(widget)
        row.addStretch(1)
        return row

    # --- состояние ------------------------------------------------------

    @property
    def section(self) -> ProfileSection:
        return self._store.document.profile

    @property
    def editor(self) -> JsonEditor:
        return self._editor

    @property
    def table(self) -> QTableWidget:
        return self._table

    @property
    def projection_table(self) -> QTableWidget:
        return self._projection_table

    @property
    def projection(self) -> ProjectionResult:
        return self._projection

    @property
    def unknown_fields(self) -> list[UnknownField]:
        return list(self._unknown)

    @property
    def status_text(self) -> str:
        return self._status_label.text()

    @property
    def metrics_text(self) -> str:
        """Строка κ/κ+ и числа параметров в векторах."""
        return self._kappa_label.text()

    def _on_document_changed(self, _document: object) -> None:
        self.refresh()

    def _on_destroyed(self) -> None:
        self._store.remove_listener(self._on_document_changed)

    def dispose(self) -> None:
        """Отписывается от документа: вкладка закрыта."""
        self._check_timer.stop()
        self._store.remove_listener(self._on_document_changed)

    def refresh(self) -> None:
        """Перестраивает таблицы и, если JSON не правился, текст редактора."""
        if not self._dirty:
            self._suppress = True
            self._editor.setPlainText(_section_text(self.section))
            self._suppress = False
        self._nda_badge.setVisible(self.section.nda)
        self._rebuild_table()
        self.check_text()

    # --- JSON -----------------------------------------------------------

    def _on_text_changed(self) -> None:
        if self._suppress:
            return
        self._dirty = True
        self._check_timer.start()

    def check_text(self) -> bool:
        """Проверяет текст без записи в документ; ``False`` при ошибке."""
        try:
            payload = parse_profile_json(self._editor.toPlainText())
            imported = import_profile_payload(payload)
        except ProjectValidationError as exc:
            self._editor.clear_error()
            self._set_error(_describe_issues(exc.issues))
            return False
        except ProjectError as exc:
            line = getattr(exc, "line", None)
            if line is not None:
                self._editor.highlight_error(line, getattr(exc, "column", 1))
            self._set_error(str(exc))
            return False

        self._editor.clear_error()
        self._unknown = list(imported.unknown)
        self._issues = list(imported.issues)
        self._apply_button.setEnabled(True)
        self._update_preview(imported.section)
        return True

    def apply_text(self) -> bool:
        """Записывает профиль из JSON в документ одной командой undo."""
        try:
            payload = parse_profile_json(self._editor.toPlainText())
            imported = import_profile_payload(payload)
        except ProjectError as exc:
            line = getattr(exc, "line", None)
            if line is not None:
                self._editor.highlight_error(line, getattr(exc, "column", 1))
            self._set_error(str(exc))
            return False
        try:
            self._store.set_value("profile", imported.section.model_dump(mode="python"))
        except ProjectError as exc:
            self._set_error(str(exc))
            return False

        self._dirty = False
        self._unknown = list(imported.unknown)
        self._issues = list(imported.issues)
        self._status_label.setStyleSheet("")
        self._status_label.setText(self._report_text(self.section, applied=True))
        return True

    # --- таблица параметров ---------------------------------------------

    def _rebuild_table(self) -> None:
        parameters = self.section.parameters
        self._table.blockSignals(True)
        self._table.setRowCount(len(parameters))
        for row, parameter in enumerate(parameters):
            self._table.setItem(row, 0, _cell(parameter.id, parameter))
            block = parameter.block
            self._table.setItem(
                row, 1, _cell(BLOCK_TITLES.get(block or "", block or _EMPTY), parameter)
            )
            self._table.setCellWidget(row, 2, self._class_combo(row, parameter.klass))
            self._table.setCellWidget(row, 3, self._target_combo(row, parameter.target))
            self._table.setItem(row, 4, _cell(", ".join(parameter.projection) or _EMPTY, parameter))
            self._table.setItem(row, 5, _cell(_value_text(parameter), parameter))
            self._table.setItem(row, 6, _cell(parameter.unit or _EMPTY, parameter))
        self._table.blockSignals(False)
        self._table.resizeColumnsToContents()
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

    def _class_combo(self, row: int, value: str | None) -> QComboBox:
        combo = _combo((None, "C", "E", "M", "N"), value)
        if value is None:
            combo.setStyleSheet(f"background-color: {_UNMARKED_COLOR.name()};")
        combo.currentIndexChanged.connect(
            lambda _index, target=row, widget=combo: self._set_parameter_value(
                target, "class", widget.currentData()
            )
        )
        return combo

    def _target_combo(self, row: int, value: str | None) -> QComboBox:
        combo = _combo((None, "P_gen", "P_sim", "P_hw"), value)
        combo.currentIndexChanged.connect(
            lambda _index, target=row, widget=combo: self._set_parameter_value(
                target, "target", widget.currentData()
            )
        )
        return combo

    def _set_parameter_value(self, row: int, field: str, value: Any) -> None:
        try:
            self._store.set_value(f"profile.parameters.{row}.{field}", value)
        except ProjectError as exc:
            self._rebuild_table()
            self._set_error(str(exc), blocking=False)

    # --- предпросмотр ---------------------------------------------------

    def _update_preview(self, section: ProfileSection) -> None:
        metrics = completeness(section)
        self._projection = project_profile(section)
        counts = self._projection.counts()
        self._kappa_label.setText(
            f"κ {metrics.kappa_text} ({metrics.kappa_percent:.1f} %)"
            f" · κ+ {metrics.kappa_plus_text} ({metrics.kappa_plus_percent:.1f} %)"
            f" · векторы P_gen {counts['P_gen']} / P_sim {counts['P_sim']}"
            f" / P_hw {counts['P_hw']}"
        )
        self._fill_projection_table(self._projection)
        report = self._report_text(section)
        self._report.setPlainText(report)
        self._status_label.setText(report)
        self._status_label.setStyleSheet("" if self._projection.ready else _ERROR_STYLE)

    def _fill_projection_table(self, projection: ProjectionResult) -> None:
        rows = [
            entry
            for target in ("P_gen", "P_sim", "P_hw")
            for entry in projection.vectors.get(target, [])
        ]
        self._projection_table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            self._projection_table.setItem(row, 0, _plain(entry.parameter_id))
            self._projection_table.setItem(row, 1, _plain(entry.klass))
            self._projection_table.setItem(row, 2, _plain(entry.target))
            self._projection_table.setItem(row, 3, _plain(", ".join(entry.keys) or _EMPTY))
            self._projection_table.setItem(row, 4, _plain(str(entry.value)))
            self._projection_table.setItem(row, 5, _plain(entry.unit or _EMPTY))
        self._projection_table.resizeColumnsToContents()
        self._projection_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )

    def _report_text(self, section: ProfileSection, *, applied: bool = False) -> str:
        metrics = completeness(section)
        lines = [
            f"заказчик: {section.customer or _EMPTY}",
            f"NDA: {'да' if section.nda else 'нет'}",
            f"параметров: {metrics.total}, без класса: {metrics.unmarked}",
            f"κ = {metrics.kappa_text}, κ+ = {metrics.kappa_plus_text}",
        ]
        if self._unknown:
            lines.append(f"неизвестных полей сохранено: {len(self._unknown)}")
            lines.extend(f"  {item.path}.{item.name}" for item in self._unknown)
        if self._projection.uncovered:
            lines.append(f"не покрыто векторами: {len(self._projection.uncovered)}")
        for item in self._projection.issues:
            lines.append(f"  {item.path}: {item.message}")
        for item in self._issues:
            lines.append(f"  {item.path}: {item.message}")
        if not self._projection.ready:
            lines.append("проекция заблокирована: разметьте классы и векторы")
        elif applied:
            lines.append("профиль записан в проект")
        return "\n".join(lines)

    def _set_error(self, message: str, *, blocking: bool = True) -> None:
        if blocking:
            self._apply_button.setEnabled(False)
        self._status_label.setText(message)
        self._status_label.setStyleSheet(_ERROR_STYLE)

    # --- файлы ----------------------------------------------------------

    def import_file(self, path: Path | None = None) -> bool:
        """Читает профиль из файла и применяет его к проекту."""
        target = path
        if target is None:
            chosen = QFileDialog.getOpenFileName(
                self,
                "Импорт профиля",
                str(self._settings.last_dir or Path.cwd()),
                "Профиль (JSON);;JSON (*.json)",
            )
            if not chosen or not chosen[0]:
                return False
            target = Path(chosen[0])
        try:
            text = target.read_text(encoding="utf-8")
        except OSError as exc:
            self._set_error(f"не удалось прочитать {target}: {exc}")
            return False

        self._settings.last_dir = target.parent
        self._suppress = True
        self._editor.setPlainText(text)
        self._suppress = False
        self._dirty = True
        if not self.check_text():
            return False
        if not self.apply_text():
            return False
        self._status_label.setStyleSheet("")
        self._status_label.setText(
            f"импортировано: {target.name}; неизвестных полей {len(self._unknown)}"
        )
        return True

    def export_file(self, path: Path | None = None) -> bool:
        """Пишет профиль с версией и хэшем, соблюдая изоляцию NDA."""
        target = path
        if target is None:
            suggested = (self._settings.last_dir or Path.cwd()) / "profile.json"
            chosen = QFileDialog.getSaveFileName(
                self,
                "Экспорт профиля",
                str(suggested),
                "Профиль (JSON);;JSON (*.json)",
            )
            if not chosen or not chosen[0]:
                return False
            target = Path(chosen[0])
        try:
            payload = export_profile_payload(self.section, destination=target)
        except NdaViolation as exc:
            QMessageBox.warning(self, "Экспорт запрещён", str(exc))
            self._set_error(str(exc), blocking=False)
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dump_profile_export(payload), encoding="utf-8", newline="\n")
        self._settings.last_dir = target.parent
        self._status_label.setStyleSheet("")
        self._status_label.setText(f"экспортировано: {target} · {payload['hash']}")
        return True


def _combo(options: tuple[str | None, ...], value: str | None) -> QComboBox:
    combo = QComboBox()
    for option in options:
        combo.addItem(option or _EMPTY, option)
    index = combo.findData(value)
    combo.setCurrentIndex(index if index >= 0 else 0)
    return combo


def _plain(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


def _cell(text: str, parameter: ProfileParameter) -> QTableWidgetItem:
    item = _plain(text)
    if parameter.klass is None:
        item.setBackground(_UNMARKED_COLOR)
        item.setToolTip("класс не указан: проекция заблокирована")
    return item


def _value_text(parameter: ProfileParameter) -> str:
    if parameter.range is not None:
        return f"{parameter.range[0]}…{parameter.range[1]}"
    return str(parameter.value)


def _describe_issues(issues: list[Issue]) -> str:
    return "\n".join(f"{item.path}: {item.message}" for item in issues)


def _section_text(section: ProfileSection) -> str:
    """Читаемый JSON раздела для редактора."""
    payload = section.model_dump(mode="json", by_alias=True)
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
