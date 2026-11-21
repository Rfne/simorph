"""Вкладка «Миссия»: граф, выбранный элемент и параметры (T6-03, T6-04).

Три панели показывают один и тот же документ с разных сторон:

1. **граф** — элементы по слоям раскладки; цикл, недостижимость и битые
   ссылки видны сразу, а не только после проверки;
2. **элемент** — поля выбранного узла: тайминги, поза, зона, сенсор,
   переходы; правка применяется одной командой undo;
3. **параметры** — бюджет миссии, расписание с критическим путём и
   замечания валидатора по этой миссии.

Панели синхронны: выбор узла в графе открывает его в панели элемента
и подсвечивает строку расписания, а правка поля сразу перестраивает
граф и бюджет.

Компактный режим убирает панель графа, оставляя элемент и параметры;
выбранный узел, индексы упражнения и миссии при переключении не теряются.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from simorph.core.errors import ProjectError, ProjectValidationError
from simorph.core.issues import Issue, Severity
from simorph.core.mission import (
    ELEMENT_TYPES,
    build_graph,
    element_spec,
    import_mission,
    mission_budget,
    mission_issues,
    required_fields,
    write_payload,
)
from simorph.core.mission.exporting import exercise_payload, mission_payload
from simorph.core.mission.graph import MissionGraph
from simorph.core.mission.timings import MissionBudget
from simorph.core.models.exercise import ExerciseSection, Mission, MissionElement
from simorph.core.models.project import ProjectDocument
from simorph.settings import ApplicationSettings
from simorph.ui.commands import DocumentStore

__all__ = ["MissionView"]

#: Красный текст для ошибок.
_ERROR_STYLE = "color: #e5534b;"

#: Серый текст для подсказок.
_MUTED_STYLE = "color: #8f949c;"

#: Цвет узла на недопустимом цикле.
_CYCLE_COLOR = QColor("#e5534b")

#: Цвет недостижимого узла.
_UNREACHABLE_COLOR = QColor("#8f949c")

#: Поля элемента, доступные для правки, и их подписи.
_FIELDS: tuple[tuple[str, str], ...] = (
    ("duration_s", "Длительность, с"),
    ("ttl_s", "TTL, с"),
    ("priority", "Приоритет"),
    ("speed_mps", "Скорость, м/с"),
    ("iterations", "Итерации"),
    ("pose", "Поза x, y, z"),
    ("zone", "Зона"),
    ("sensor", "Сенсор"),
    ("condition", "Условие"),
    ("next", "Переходы"),
)

#: Типы сенсоров, которые считаются камерой.
_CAMERA_SENSORS = frozenset({"camera", "stereo_camera"})


class MissionView(QWidget):
    """Редактор миссии: граф, элемент и параметры."""

    def __init__(
        self,
        store: DocumentStore,
        settings: ApplicationSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._settings = settings
        self._document = store.document
        self._exercise_index = 0
        self._mission_index = 0
        self._selected_id: str | None = None
        self._compact = False
        self._saved_sizes: list[int] = [280, 320, 320]
        self._refreshing = False
        self._disposed = False
        self.setObjectName("editor_mission")

        self._graph = MissionGraph()
        self._budget = MissionBudget()
        self._issues: list[Issue] = []

        self._graph_tree = QTreeWidget()
        self._graph_tree.setHeaderLabels(["Элемент", "Тип"])
        self._graph_tree.setColumnWidth(0, 190)
        self._graph_tree.currentItemChanged.connect(self._on_graph_selection)

        self._type_combo = QComboBox()
        for kind in ELEMENT_TYPES:
            self._type_combo.addItem(element_spec(kind).title, kind)
        self._add_button = QPushButton("Добавить")
        self._add_button.clicked.connect(self.add_element)
        self._remove_button = QPushButton("Удалить")
        self._remove_button.clicked.connect(self.remove_element)

        self._element_title = QLabel("Элемент не выбран")
        self._element_title.setWordWrap(True)
        self._element_title.setStyleSheet("font-weight: bold;")
        self._element_hint = QLabel("")
        self._element_hint.setWordWrap(True)
        self._element_hint.setStyleSheet(_MUTED_STYLE)
        self._element_issues = QLabel("")
        self._element_issues.setWordWrap(True)
        self._element_issues.setStyleSheet(_ERROR_STYLE)

        self._fields: dict[str, QLineEdit] = {}
        self._field_rows: list[tuple[str, QLineEdit]] = []
        for name, title in _FIELDS:
            editor = QLineEdit()
            editor.setPlaceholderText("—")
            editor.editingFinished.connect(lambda value=name: self._on_field_edited(value))
            self._fields[name] = editor
            self._field_rows.append((title, editor))

        self._budget_label = QLabel("—")
        self._budget_label.setWordWrap(True)
        self._timeline = QTableWidget(0, 3)
        self._timeline.setHorizontalHeaderLabels(["Элемент", "Старт, с", "Конец, с"])
        self._timeline.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._timeline.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._timeline.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._timeline.setColumnWidth(0, 150)
        self._timeline.cellClicked.connect(self._on_timeline_clicked)
        self._mission_issues = QListWidget()

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)

        self._exercise_combo = QComboBox()
        self._exercise_combo.currentIndexChanged.connect(self._on_exercise_changed)
        self._mission_combo = QComboBox()
        self._mission_combo.currentIndexChanged.connect(self._on_mission_changed)
        self._compact_box = QCheckBox("Компактный режим")
        self._compact_box.toggled.connect(self.set_compact)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._graph_panel = self._build_graph_panel()
        self._element_panel = self._build_element_panel()
        self._params_panel = self._build_params_panel()
        for panel in (self._graph_panel, self._element_panel, self._params_panel):
            self._splitter.addWidget(panel)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setStretchFactor(2, 1)
        self._splitter.setSizes(self._saved_sizes)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(self._build_toolbar())
        layout.addWidget(self._splitter, 1)
        layout.addWidget(self._status_label)

        self._refresh_all()

    # --- построение -----------------------------------------------------

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("Упражнение:"))
        row.addWidget(self._exercise_combo)
        row.addWidget(QLabel("Миссия:"))
        row.addWidget(self._mission_combo)
        row.addWidget(self._compact_box)
        export_button = QPushButton("Экспорт…")
        export_button.clicked.connect(lambda: self.save_export())
        import_button = QPushButton("Импорт (только чтение)…")
        import_button.clicked.connect(lambda: self.import_preview())
        row.addWidget(export_button)
        row.addWidget(import_button)
        row.addStretch(1)
        return row

    def _build_graph_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Граф миссии")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)
        layout.addWidget(self._graph_tree, 1)

        add_row = QHBoxLayout()
        add_row.addWidget(self._type_combo, 1)
        add_row.addWidget(self._add_button)
        add_row.addWidget(self._remove_button)
        layout.addLayout(add_row)
        return panel

    def _build_element_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Элемент")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)
        layout.addWidget(self._element_title)
        layout.addWidget(self._element_hint)
        layout.addWidget(self._element_issues)
        form_holder = QWidget()
        form_holder.setLayout(self._form_layout())
        layout.addWidget(form_holder)
        layout.addStretch(1)
        return panel

    def _form_layout(self) -> QFormLayout:
        form = QFormLayout()
        for title, editor in self._field_rows:
            form.addRow(title, editor)
        return form

    def _build_params_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Параметры и тайминги")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)
        layout.addWidget(self._budget_label)
        layout.addWidget(self._timeline, 1)
        issues_title = QLabel("Замечания миссии")
        issues_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(issues_title)
        layout.addWidget(self._mission_issues, 1)
        return panel

    # --- состояние ------------------------------------------------------

    @property
    def document(self) -> ProjectDocument:
        return self._document

    @property
    def exercise_index(self) -> int:
        return self._exercise_index

    @property
    def mission_index(self) -> int:
        return self._mission_index

    @property
    def selected_id(self) -> str | None:
        return self._selected_id

    @property
    def compact(self) -> bool:
        return self._compact

    @property
    def graph(self) -> MissionGraph:
        return self._graph

    @property
    def budget(self) -> MissionBudget:
        return self._budget

    @property
    def issues(self) -> list[Issue]:
        return list(self._issues)

    @property
    def status_text(self) -> str:
        return self._status_label.text()

    @property
    def summary_text(self) -> str:
        """Сводка бюджета и замечаний в панели параметров."""
        return self._budget_label.text()

    @property
    def element_title(self) -> str:
        """Заголовок панели элемента."""
        return self._element_title.text()

    @property
    def graph_panel_visible(self) -> bool:
        """Видна ли панель графа (в компактном режиме она скрыта)."""
        return not self._graph_panel.isHidden()

    @property
    def exercise(self) -> ExerciseSection | None:
        if 0 <= self._exercise_index < len(self._document.exercises):
            return self._document.exercises[self._exercise_index]
        return None

    @property
    def mission(self) -> Mission | None:
        exercise = self.exercise
        if exercise is None or not 0 <= self._mission_index < len(exercise.missions):
            return None
        return exercise.missions[self._mission_index]

    def element_field_path(self, name: str) -> str:
        """Путь поля выбранного элемента в документе."""
        return (
            f"exercises.{self._exercise_index}.missions.{self._mission_index}"
            f".elements.{self._element_index()}.{name}"
        )

    def graph_rows(self) -> list[tuple[int, str]]:
        """Строки панели графа: номер слоя и идентификатор элемента."""
        rows: list[tuple[int, str]] = []
        for layer in range(self._graph_tree.topLevelItemCount()):
            header = self._graph_tree.topLevelItem(layer)
            if header is None:
                continue
            for index in range(header.childCount()):
                child = header.child(index)
                if child is None:
                    continue
                name = child.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(name, str):
                    rows.append((layer, name))
        return rows

    def timeline_rows(self) -> list[tuple[str, float, float]]:
        """Строки расписания: элемент, старт и окончание."""
        rows: list[tuple[str, float, float]] = []
        for row in range(self._timeline.rowCount()):
            name_item = self._timeline.item(row, 0)
            start_item = self._timeline.item(row, 1)
            end_item = self._timeline.item(row, 2)
            if name_item is None or start_item is None or end_item is None:
                continue
            rows.append(
                (
                    str(name_item.data(Qt.ItemDataRole.UserRole)),
                    float(start_item.text()),
                    float(end_item.text()),
                )
            )
        return rows

    def field_text(self, name: str) -> str:
        """Текст поля панели элемента."""
        return self._fields[name].text()

    def submit_field(self, name: str, text: str) -> bool:
        """Вводит текст в поле и применяет его как правку документа."""
        editor = self._fields[name]
        editor.blockSignals(True)
        editor.setText(text)
        editor.blockSignals(False)
        try:
            value = _parse_value(name, text)
        except ValueError as exc:
            self._set_status(f"{name}: {exc}", error=True)
            self._show_element(self._selected_id)
            return False
        return self.apply_field(name, value)

    def _element_index(self) -> int:
        mission = self.mission
        if mission is None or self._selected_id is None:
            return 0
        for index, element in enumerate(mission.elements):
            if element.id == self._selected_id:
                return index
        return 0

    # --- документ -------------------------------------------------------

    def rebuild(self, document: ProjectDocument) -> None:
        """Перестраивает все панели под новый документ."""
        self._document = document
        self._refresh_all()

    def select_element(self, element_id: str | None) -> bool:
        """Выбирает элемент в графе и открывает его в панели элемента."""
        mission = self.mission
        if mission is None:
            return False
        if element_id is not None and all(element.id != element_id for element in mission.elements):
            return False
        self._selected_id = element_id
        self._sync_selection()
        return True

    def set_selection(self, exercise: int, mission: int) -> bool:
        """Переключает упражнение и миссию по индексам."""
        if not 0 <= exercise < len(self._document.exercises):
            return False
        missions = self._document.exercises[exercise].missions
        if not 0 <= mission < len(missions):
            return False
        self._exercise_index = exercise
        self._mission_index = mission
        self._selected_id = None
        self._refresh_all()
        return True

    def set_compact(self, enabled: bool) -> None:
        """Включает или выключает компактный режим без потери выбора."""
        enabled = bool(enabled)
        if enabled == self._compact:
            self._compact_box.setChecked(enabled)
            return
        if enabled:
            self._saved_sizes = self._splitter.sizes()
        self._compact = enabled
        self._compact_box.setChecked(enabled)
        self._graph_panel.setVisible(not enabled)
        if not enabled:
            self._splitter.setSizes(self._saved_sizes)

    def _refresh_all(self) -> None:
        if self._refreshing or self._disposed:
            return
        self._refreshing = True
        try:
            self._sync_combos()
            self._recalculate()
            self._fill_graph()
            self._fill_timeline()
            self._sync_selection()
        finally:
            self._refreshing = False

    def _sync_combos(self) -> None:
        exercises = self._document.exercises
        self._exercise_index = min(self._exercise_index, max(0, len(exercises) - 1))
        self._exercise_combo.blockSignals(True)
        self._exercise_combo.clear()
        for index, exercise in enumerate(exercises):
            self._exercise_combo.addItem(exercise.exercise_id, index)
        self._exercise_combo.setCurrentIndex(self._exercise_index if exercises else -1)
        self._exercise_combo.blockSignals(False)

        missions = exercises[self._exercise_index].missions if exercises else []
        self._mission_index = min(self._mission_index, max(0, len(missions) - 1))
        self._mission_combo.blockSignals(True)
        self._mission_combo.clear()
        for index, mission in enumerate(missions):
            self._mission_combo.addItem(mission.mission_id, index)
        self._mission_combo.setCurrentIndex(self._mission_index if missions else -1)
        self._mission_combo.blockSignals(False)

    def _recalculate(self) -> None:
        mission = self.mission
        if mission is None:
            self._graph = MissionGraph()
            self._budget = MissionBudget()
            self._issues = []
            return
        self._graph = build_graph(mission.elements)
        self._budget = mission_budget(mission.elements)
        self._issues = mission_issues(
            mission,
            world=self._document.world,
            agents=self._document.agents,
            prefix=f"exercises.{self._exercise_index}.missions.{self._mission_index}",
        )

    # --- панели ---------------------------------------------------------

    def _fill_graph(self) -> None:
        self._graph_tree.blockSignals(True)
        self._graph_tree.clear()
        mission = self.mission
        if mission is None:
            self._graph_tree.blockSignals(False)
            self._set_status("в упражнении нет миссий", error=True)
            return
        by_id = {element.id: element for element in mission.elements}
        grouped: dict[int, list[str]] = {}
        for name in self._graph.order:
            grouped.setdefault(self._graph.layers.get(name, 0), []).append(name)
        for layer in sorted(grouped):
            header = QTreeWidgetItem([f"Слой {layer}", ""])
            header.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._graph_tree.addTopLevelItem(header)
            for name in grouped[layer]:
                element = by_id[name]
                spec = element_spec(element.type)
                item = QTreeWidgetItem([f"{spec.title} · {name}", spec.title])
                item.setData(0, Qt.ItemDataRole.UserRole, name)
                self._decorate(item, element)
                header.addChild(item)
        self._graph_tree.expandAll()
        self._graph_tree.blockSignals(False)

    def _decorate(self, item: QTreeWidgetItem, element: MissionElement) -> None:
        """Подсвечивает проблемные узлы прямо в графе."""
        if element.id in self._graph.illegal_cycles:
            item.setForeground(0, QBrush(_CYCLE_COLOR))
            item.setToolTip(0, "цикл допустим только через элемент 'loop'")
        elif element.id not in self._graph.reachable:
            item.setForeground(0, QBrush(_UNREACHABLE_COLOR))
            item.setToolTip(0, "элемент недостижим из 'start'")
        elif element.id in self._graph.cycles:
            item.setToolTip(0, "участок повторяется через 'loop'")
        missing = [
            name for name in required_fields(element.type) if getattr(element, name, None) is None
        ]
        if missing:
            item.setToolTip(0, f"не заполнено: {', '.join(missing)}")

    def _sync_selection(self) -> None:
        """Приводит выбор, панель элемента и расписание к одному узлу."""
        mission = self.mission
        if mission is None:
            self._selected_id = None
            self._show_empty_element("в упражнении нет миссий")
            self._update_summary()
            return
        ids = [element.id for element in mission.elements]
        if self._selected_id not in ids:
            self._selected_id = ids[0] if ids else None
        self._select_tree_item(self._selected_id)
        self._show_element(self._selected_id)
        self._highlight_timeline(self._selected_id)
        self._update_summary()

    def _select_tree_item(self, element_id: str | None) -> None:
        self._graph_tree.blockSignals(True)
        try:
            for top in range(self._graph_tree.topLevelItemCount()):
                header = self._graph_tree.topLevelItem(top)
                if header is None:
                    continue
                for index in range(header.childCount()):
                    child = header.child(index)
                    if child is not None and (
                        child.data(0, Qt.ItemDataRole.UserRole) == element_id
                    ):
                        self._graph_tree.setCurrentItem(child)
                        return
        finally:
            self._graph_tree.blockSignals(False)

    def _show_element(self, element_id: str | None) -> None:
        mission = self.mission
        if mission is None or element_id is None:
            self._show_empty_element("элемент не выбран")
            return
        element = next((item for item in mission.elements if item.id == element_id), None)
        if element is None:
            self._show_empty_element("элемент не выбран")
            return
        spec = element_spec(element.type)
        self._element_title.setText(f"{spec.title} · {element.id}")
        path = self._graph.path_to(element.id)
        incoming = self._graph.parents.get(element.id, [])
        parts = [
            f"тип {element.type} · категория {spec.category}",
            f"путь: {' → '.join(path) if path else '—'}",
            f"входы: {', '.join(incoming) if incoming else '—'}",
            f"переходы: {', '.join(element.next) if element.next else '—'}",
        ]
        if spec.terminal:
            parts.append("терминальный: исходящих переходов быть не должно")
        self._element_hint.setText("\n".join(parts))
        related = [
            item.message for item in self._issues if item.path.startswith(self._element_prefix())
        ]
        self._element_issues.setText("\n".join(related))
        for name, editor in self._fields.items():
            editor.blockSignals(True)
            editor.setText(_format_value(name, getattr(element, name)))
            editor.blockSignals(False)

    def _element_prefix(self) -> str:
        return (
            f"exercises.{self._exercise_index}.missions.{self._mission_index}"
            f".elements.{self._element_index()}"
        )

    def _show_empty_element(self, message: str) -> None:
        self._element_title.setText(message)
        self._element_hint.setText("")
        self._element_issues.setText("")
        for editor in self._fields.values():
            editor.blockSignals(True)
            editor.clear()
            editor.blockSignals(False)

    def _fill_timeline(self) -> None:
        timeline = sorted(
            (
                (name, self._budget.starts.get(name, 0.0), self._budget.ends.get(name, 0.0))
                for name in self._budget.starts
            ),
            key=lambda row: (row[1], row[2], row[0]),
        )
        critical = set(self._budget.critical_path)
        self._timeline.setRowCount(len(timeline))
        for row, (name, start, end) in enumerate(timeline):
            for column, text in enumerate((name, f"{start:.1f}", f"{end:.1f}")):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, name)
                if name in critical:
                    font = QFont(item.font())
                    font.setBold(True)
                    item.setFont(font)
                self._timeline.setItem(row, column, item)

    def _update_summary(self) -> None:
        mission = self.mission
        if mission is None:
            self._budget_label.setText("бюджет: —")
            self._mission_issues.clear()
            return
        errors = sum(1 for item in self._issues if item.severity is Severity.ERROR)
        warnings = len(self._issues) - errors
        ttl = "—" if self._budget.ttl_s is None else f"{self._budget.ttl_s:.1f} с"
        over = " · бюджет больше TTL" if self._budget.over_ttl else ""
        self._budget_label.setText(
            f"бюджет {self._budget.total_s:.1f} с · сумма {self._budget.sequential_s:.1f} с"
            f" · параллелизм {self._budget.parallel_width} · TTL {ttl}{over}\n"
            f"критический путь: {' → '.join(self._budget.critical_path) or '—'}\n"
            f"элементов {len(mission.elements)} · слоёв {self._graph.layer_count}"
            f" · ошибок {errors} · предупреждений {warnings}"
        )
        self._mission_issues.clear()
        for item in self._issues:
            entry = QListWidgetItem(f"{item.severity}: {item.path}: {item.message}")
            if item.severity is Severity.ERROR:
                entry.setForeground(QBrush(_CYCLE_COLOR))
            self._mission_issues.addItem(entry)
        self._set_status(
            f"миссия {mission.mission_id}: ошибок {errors}, предупреждений {warnings}",
            error=errors > 0,
        )

    def _highlight_timeline(self, element_id: str | None) -> None:
        self._timeline.blockSignals(True)
        try:
            self._timeline.clearSelection()
            for row in range(self._timeline.rowCount()):
                item = self._timeline.item(row, 0)
                name = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
                if name == element_id:
                    self._timeline.selectRow(row)
                    break
        finally:
            self._timeline.blockSignals(False)

    # --- выбор ----------------------------------------------------------

    def _on_graph_selection(self, current: QTreeWidgetItem | None, _previous: Any = None) -> None:
        if current is None:
            return
        element_id = current.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(element_id, str):
            return
        self._selected_id = element_id
        self._show_element(element_id)
        self._highlight_timeline(element_id)

    def _on_timeline_clicked(self, row: int, _column: int) -> None:
        item = self._timeline.item(row, 0)
        if item is None:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(name, str):
            self.select_element(name)

    def _on_exercise_changed(self, index: int) -> None:
        if self._refreshing or index < 0:
            return
        self._exercise_index = index
        self._mission_index = 0
        self._selected_id = None
        self._refresh_all()

    def _on_mission_changed(self, index: int) -> None:
        if self._refreshing or index < 0:
            return
        self._mission_index = index
        self._selected_id = None
        self._refresh_all()

    # --- правка ---------------------------------------------------------

    def _on_field_edited(self, name: str) -> None:
        self.submit_field(name, self._fields[name].text())

    def apply_field(self, name: str, value: Any) -> bool:
        """Применяет правку поля выбранного элемента одной командой undo."""
        if self._selected_id is None or self.mission is None:
            self._set_status("элемент не выбран", error=True)
            return False
        try:
            self._store.set_value(self.element_field_path(name), value)
        except ProjectValidationError as exc:
            detail = exc.issues[0].message if exc.issues else str(exc)
            self._set_status(f"{name}: {detail}", error=True)
            self._show_element(self._selected_id)
            return False
        except ProjectError as exc:
            self._set_status(f"{name}: {exc}", error=True)
            self._show_element(self._selected_id)
            return False
        self.rebuild(self._store.document)
        self._set_status(f"{name}: применено")
        return True

    def add_element(self, kind: str | None = None) -> bool:
        """Добавляет элемент выбранного типа в конец миссии."""
        mission = self.mission
        if mission is None:
            self._set_status("в упражнении нет миссий", error=True)
            return False
        target = kind or self._type_combo.currentData()
        if not isinstance(target, str):
            return False
        element = self._default_element(target, mission)
        if element is None:
            self._set_status(f"для типа {target!r} в проекте нет зоны или камеры", error=True)
            return False
        payload = [item.model_dump(mode="python") for item in mission.elements]
        payload.append(element.model_dump(mode="python"))
        if not self._write_elements(payload):
            return False
        self._selected_id = element.id
        self.rebuild(self._store.document)
        self._set_status(f"добавлен элемент {element.id}")
        return True

    def remove_element(self, element_id: str | None = None) -> bool:
        """Удаляет элемент и ссылки на него из переходов."""
        mission = self.mission
        if mission is None:
            self._set_status("в упражнении нет миссий", error=True)
            return False
        target = element_id or self._selected_id
        if target is None:
            self._set_status("элемент не выбран", error=True)
            return False
        remaining = [item for item in mission.elements if item.id != target]
        if len(remaining) == len(mission.elements):
            self._set_status(f"элемент {target!r} не найден", error=True)
            return False
        payload = [
            item.model_copy(
                update={"next": [name for name in item.next if name != target]}
            ).model_dump(mode="python")
            for item in remaining
        ]
        if not self._write_elements(payload):
            return False
        self._selected_id = None
        self.rebuild(self._store.document)
        self._set_status(f"удалён элемент {target}")
        return True

    def _write_elements(self, payload: list[dict[str, Any]]) -> bool:
        path = f"exercises.{self._exercise_index}.missions.{self._mission_index}.elements"
        try:
            self._store.set_value(path, payload)
        except ProjectValidationError as exc:
            detail = exc.issues[0].message if exc.issues else str(exc)
            self._set_status(f"элементы: {detail}", error=True)
            return False
        except ProjectError as exc:
            self._set_status(f"элементы: {exc}", error=True)
            return False
        return True

    def _default_element(self, kind: str, mission: Mission) -> MissionElement | None:
        """Новый элемент с значениями, которые не ломают схему."""
        existing = {item.id for item in mission.elements}
        payload: dict[str, Any] = {
            "id": _unique_id(kind, existing),
            "type": kind,
        }
        if kind in ("waypoint", "transit"):
            payload["pose"] = (0.0, 0.0, 10.0)
        if kind == "dwell":
            payload["duration_s"] = 5.0
        if kind in ("observe", "activate_sensor", "capture"):
            sensor = self._first_camera(mission.agent_id)
            if sensor is None:
                return None
            payload["sensor"] = sensor
        if kind == "observe":
            zones = [zone.id for zone in self._document.world.zones]
            if not zones:
                return None
            payload["zone"] = zones[0]
            payload["pose"] = (10.0, 10.0, 10.0)
        if kind in ("abort", "condition"):
            payload["condition"] = "аварийное завершение" if kind == "abort" else "условие"
        if kind == "loop":
            payload["iterations"] = 2
        try:
            return MissionElement(**payload)
        except ValueError:
            return None

    def _first_camera(self, agent_id: str) -> str | None:
        for agent in self._document.agents:
            if agent.agent_id != agent_id:
                continue
            for sensor in agent.sensors:
                if sensor.type in _CAMERA_SENSORS:
                    return sensor.id
        return None

    # --- обмен ----------------------------------------------------------

    def save_export(self, path: Path | None = None) -> bool:
        """Экспортирует миссию в JSON или YAML (формат по расширению)."""
        mission = self.mission
        if mission is None:
            self._set_status("в упражнении нет миссий", error=True)
            return False
        target = path
        if target is None:
            suggested = (self._settings.last_dir or Path.cwd()) / f"{mission.mission_id}.yaml"
            chosen = QFileDialog.getSaveFileName(
                self,
                "Экспорт миссии",
                str(suggested),
                "Миссия YAML (*.yaml *.yml);;Миссия JSON (*.json);;Все файлы (*)",
            )
            if not chosen or not chosen[0]:
                return False
            target = Path(chosen[0])
        try:
            written = write_payload(mission_payload(mission), target)
        except OSError as exc:  # экспорт не должен ронять окно
            self._set_status(f"экспорт не выполнен: {exc}", error=True)
            return False
        self._settings.last_dir = target.parent
        self._set_status(f"экспорт: {written.name}")
        return True

    def export_exercise(self, path: Path) -> Path | None:
        """Экспортирует упражнение целиком (для отчётов и тестов)."""
        exercise = self.exercise
        if exercise is None:
            return None
        return write_payload(exercise_payload(exercise), path)

    def import_preview(self, path: Path | None = None) -> bool:
        """Read-only импорт: показывает содержимое файла, ничего не записывая."""
        target = path
        if target is None:
            chosen = QFileDialog.getOpenFileName(
                self,
                "Импорт миссии (только чтение)",
                str(self._settings.last_dir or Path.cwd()),
                "Миссия (*.yaml *.yml *.json);;Все файлы (*)",
            )
            if not chosen or not chosen[0]:
                return False
            target = Path(chosen[0])
        try:
            imported = import_mission(target)
        except ProjectError as exc:
            self._set_status(f"импорт не выполнен: {exc}", error=True)
            return False
        self._settings.last_dir = target.parent
        summary = [
            f"источник: {imported.source}",
            "режим: только чтение",
            f"элементов: {len(imported.elements)}",
        ]
        if imported.fields:
            summary.append(f"поля пакета: {', '.join(sorted(imported.fields))}")
        summary.extend(f"предупреждение: {text}" for text in imported.warnings)
        self._set_status(" · ".join(summary))
        if self.isVisible():
            QMessageBox.information(self, "Импорт миссии", "\n".join(summary))
        return True

    # --- служебное ------------------------------------------------------

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self._status_label.setStyleSheet(_ERROR_STYLE if error else "")
        self._status_label.setText(text)

    def dispose(self) -> None:
        """Отключает сигналы: закрытая вкладка не должна получать правки."""
        self._disposed = True
        self._graph_tree.blockSignals(True)
        self._timeline.blockSignals(True)


def _format_value(name: str, value: Any) -> str:
    """Текст поля элемента для панели."""
    if value is None:
        return ""
    if name == "pose":
        return ", ".join(f"{part:g}" for part in value)
    if name == "next":
        return ", ".join(str(item) for item in value)
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _parse_value(name: str, text: str) -> Any:
    """Значение поля из текста; пустая строка — отсутствие значения."""
    raw = text.strip()
    if name == "next":
        return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]
    if not raw:
        return None
    if name == "pose":
        parts = [part for part in raw.replace(";", ",").split(",") if part.strip()]
        if len(parts) != 3:
            raise ValueError("нужны три координаты: x, y, z")
        try:
            return tuple(float(part) for part in parts)
        except ValueError as exc:
            raise ValueError("координаты должны быть числами") from exc
    if name in ("zone", "sensor", "condition"):
        return raw
    if name == "priority" or name == "iterations":
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError("ожидалось целое число") from exc
    try:
        return float(raw.replace(",", "."))
    except ValueError as exc:
        raise ValueError("ожидалось число") from exc


def _unique_id(kind: str, existing: set[str]) -> str:
    """Свободный идентификатор вида ``kind-1``."""
    index = 1
    while f"{kind}-{index}" in existing:
        index += 1
    return f"{kind}-{index}"
