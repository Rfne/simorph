"""Вкладка «Конвейеры»: граф, узлы, метрики и read-only исходники (T8-04).

Вкладка показывает один конвейер с четырёх сторон:

1. **граф** — узлы по слоям, рёбра и диагностика несовместимых портов;
2. **узел** — тип, бюджет и параметры выбранного узла;
3. **метрики** — сводка прогона, задержки по узлам и журнал событий;
4. **исходники** — дерево каталога, текст только для чтения, снимок хэшей
   и статус ``stale`` после изменения файла.

Правки идут через ``DocumentStore``: добавление, удаление и перестановка
узлов, рёбра и параметры становятся одной командой undo. Несовместимое
ребро и цикл блокируются до записи — пользователь видит причину сразу.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRegularExpression, Qt, QUrl
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
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
from simorph.core.models.pipeline import PipelineEdge, PipelineNode, PipelineSection
from simorph.core.models.project import ProjectDocument
from simorph.core.pipeline import (
    NODE_TYPES,
    NODE_TYPES_BY_KIND,
    PipelineConfig,
    PipelineGraph,
    PipelineRunner,
    PipelineRunReport,
    SourceStatus,
    SourceTree,
    accepts,
    build_pipeline_graph,
    can_run,
    check_stale,
    default_params,
    load_snapshot,
    make_backend,
    node_budget_ms,
    node_input_kind,
    node_output_kind,
    node_spec,
    parse_port_ref,
    pipeline_issues,
    port_title,
    save_snapshot,
    scan_sources,
    snapshot_path,
)
from simorph.settings import ApplicationSettings
from simorph.ui.commands import DocumentStore

__all__ = ["PipelineView"]

_ERROR_STYLE = "color: #e5534b;"
_MUTED_STYLE = "color: #8f949c;"
_CYCLE_COLOR = QColor("#e5534b")
_MUTED_COLOR = QColor("#8f949c")
_WARN_COLOR = QColor("#c69026")


class PipelineView(QWidget):
    """Редактор конвейера: граф, узел, метрики и исходники."""

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
        self._pipeline_index = 0
        self._selected_id: str | None = None
        self._refreshing = False
        self._disposed = False
        self._graph = PipelineGraph()
        self._issues: list[Issue] = []
        self._report: PipelineRunReport | None = None
        self._source_root: Path | None = None
        self._source_tree: SourceTree | None = None
        self._source_status: SourceStatus | None = None
        self._snapshot_file: Path | None = None
        self.setObjectName("editor_pipeline")

        self._pipeline_combo = QComboBox()
        self._pipeline_combo.currentIndexChanged.connect(self._on_pipeline_changed)
        self._kind_combo = QComboBox()
        self._kind_combo.addItems(["computational", "neural"])
        self._kind_combo.currentTextChanged.connect(self._on_kind_changed)
        self._node_type_combo = QComboBox()
        self._add_button = QPushButton("Добавить")
        self._add_button.clicked.connect(lambda: self.add_node())
        self._remove_button = QPushButton("Удалить")
        self._remove_button.clicked.connect(lambda: self.remove_node())
        self._up_button = QPushButton("Вверх")
        self._up_button.clicked.connect(lambda: self.move_node(-1))
        self._down_button = QPushButton("Вниз")
        self._down_button.clicked.connect(lambda: self.move_node(1))
        self._validate_button = QPushButton("Проверить")
        self._validate_button.clicked.connect(lambda: self.validate_pipeline())
        self._run_button = QPushButton("Прогон")
        self._run_button.clicked.connect(lambda: self.run_pipeline())

        self._graph_tree = QTreeWidget()
        self._graph_tree.setHeaderLabels(["Узел", "Тип"])
        self._graph_tree.setColumnWidth(0, 180)
        self._graph_tree.currentItemChanged.connect(self._on_graph_selection)
        self._edge_list = QListWidget()
        self._edge_src = QLineEdit()
        self._edge_src.setPlaceholderText("источник: узел.порт")
        self._edge_dst = QLineEdit()
        self._edge_dst.setPlaceholderText("приёмник: узел.порт")
        self._edge_add_button = QPushButton("Связать")
        self._edge_add_button.clicked.connect(lambda: self.add_edge())
        self._edge_remove_button = QPushButton("Удалить связь")
        self._edge_remove_button.clicked.connect(lambda: self.remove_edge())

        self._node_title = QLabel("Узел не выбран")
        self._node_title.setWordWrap(True)
        self._node_title.setStyleSheet("font-weight: bold;")
        self._node_hint = QLabel("")
        self._node_hint.setWordWrap(True)
        self._node_hint.setStyleSheet(_MUTED_STYLE)
        self._budget_edit = QLineEdit()
        self._budget_edit.setPlaceholderText("бюджет, мс")
        self._params_edit = QLineEdit()
        self._params_edit.setPlaceholderText("параметры JSON")
        self._apply_button = QPushButton("Применить")
        self._apply_button.clicked.connect(lambda: self.apply_node())

        self._summary_label = QLabel("прогон не выполнялся")
        self._summary_label.setWordWrap(True)
        self._node_table = QTableWidget(0, 5)
        self._node_table.setHorizontalHeaderLabels(["Узел", "План", "p50", "p95", "max"])
        self._node_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._node_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._node_table.setColumnWidth(0, 130)
        self._events_list = QListWidget()

        self._source_root_label = QLabel("каталог не выбран")
        self._source_root_label.setStyleSheet(_MUTED_STYLE)
        self._source_choose_button = QPushButton("Каталог…")
        self._source_choose_button.clicked.connect(lambda: self.choose_source_root())
        self._snapshot_button = QPushButton("Снимок")
        self._snapshot_button.clicked.connect(lambda: self.snapshot_sources())
        self._check_button = QPushButton("Проверить")
        self._check_button.clicked.connect(lambda: self.check_sources())
        self._source_open_button = QPushButton("Открыть")
        self._source_open_button.clicked.connect(lambda: self.open_source())
        self._source_status_label = QLabel("снимок не сделан")
        self._source_status_label.setWordWrap(True)
        self._source_tree_widget = QTreeWidget()
        self._source_tree_widget.setHeaderLabels(["Файл", "Язык", "Размер"])
        self._source_tree_widget.setColumnWidth(0, 240)
        self._source_tree_widget.currentItemChanged.connect(self._on_source_selected)
        self._source_text = QPlainTextEdit()
        self._source_text.setReadOnly(True)
        self._source_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._highlighter = _SourceHighlighter(self._source_text.document())

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)

        self._main_splitter = QSplitter(Qt.Orientation.Vertical)
        self._top_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._graph_panel = self._build_graph_panel()
        self._node_panel = self._build_node_panel()
        self._metrics_panel = self._build_metrics_panel()
        for panel in (self._graph_panel, self._node_panel, self._metrics_panel):
            self._top_splitter.addWidget(panel)
        self._top_splitter.setSizes([360, 320, 360])
        self._sources_panel = self._build_sources_panel()
        self._main_splitter.addWidget(self._top_splitter)
        self._main_splitter.addWidget(self._sources_panel)
        self._main_splitter.setSizes([520, 260])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(self._build_toolbar())
        layout.addWidget(self._main_splitter, 1)
        layout.addWidget(self._status_label)

        self._refresh_all()

    # --- построение -----------------------------------------------------

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("Конвейер:"))
        row.addWidget(self._pipeline_combo)
        row.addWidget(QLabel("Вид:"))
        row.addWidget(self._kind_combo)
        row.addSpacing(8)
        row.addWidget(self._node_type_combo, 1)
        for button in (
            self._add_button,
            self._remove_button,
            self._up_button,
            self._down_button,
            self._validate_button,
            self._run_button,
        ):
            row.addWidget(button)
        return row

    def _build_graph_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Граф конвейера")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)
        layout.addWidget(self._graph_tree, 1)
        edges_title = QLabel("Связи")
        edges_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(edges_title)
        layout.addWidget(self._edge_list, 1)
        edge_row = QHBoxLayout()
        edge_row.addWidget(self._edge_src, 1)
        edge_row.addWidget(self._edge_dst, 1)
        edge_row.addWidget(self._edge_add_button)
        edge_row.addWidget(self._edge_remove_button)
        layout.addLayout(edge_row)
        return panel

    def _build_node_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Узел")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)
        layout.addWidget(self._node_title)
        layout.addWidget(self._node_hint)
        form = QFormLayout()
        form.addRow("Бюджет, мс", self._budget_edit)
        form.addRow("Параметры", self._params_edit)
        layout.addLayout(form)
        layout.addWidget(self._apply_button)
        layout.addStretch(1)
        return panel

    def _build_metrics_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Метрики прогона")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)
        layout.addWidget(self._summary_label)
        layout.addWidget(self._node_table, 1)
        events_title = QLabel("События")
        events_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(events_title)
        layout.addWidget(self._events_list, 1)
        return panel

    def _build_sources_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        title = QLabel("Исходники (только чтение)")
        title.setStyleSheet("font-weight: bold;")
        header.addWidget(title)
        header.addWidget(self._source_root_label, 1)
        for button in (
            self._source_choose_button,
            self._snapshot_button,
            self._check_button,
            self._source_open_button,
        ):
            header.addWidget(button)
        layout.addLayout(header)
        layout.addWidget(self._source_status_label)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._source_tree_widget)
        splitter.addWidget(self._source_text)
        splitter.setSizes([320, 720])
        layout.addWidget(splitter, 1)
        return panel

    # --- состояние ------------------------------------------------------

    @property
    def document(self) -> ProjectDocument:
        return self._document

    @property
    def pipeline_index(self) -> int:
        return self._pipeline_index

    @property
    def pipeline(self) -> PipelineSection | None:
        if 0 <= self._pipeline_index < len(self._document.pipelines):
            return self._document.pipelines[self._pipeline_index]
        return None

    @property
    def selected_id(self) -> str | None:
        return self._selected_id

    @property
    def graph(self) -> PipelineGraph:
        return self._graph

    @property
    def issues(self) -> list[Issue]:
        return list(self._issues)

    @property
    def report(self) -> PipelineRunReport | None:
        return self._report

    @property
    def source_tree(self) -> SourceTree | None:
        return self._source_tree

    @property
    def source_status(self) -> SourceStatus | None:
        return self._source_status

    @property
    def status_text(self) -> str:
        return self._status_label.text()

    @property
    def summary_text(self) -> str:
        return self._summary_label.text()

    def graph_rows(self) -> list[tuple[int, str]]:
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

    def edge_rows(self) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for row in range(self._edge_list.count()):
            item = self._edge_list.item(row)
            pair = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(pair, tuple) and len(pair) == 2:
                rows.append((str(pair[0]), str(pair[1])))
        return rows

    def field_text(self, name: str) -> str:
        if name == "budget_ms":
            return self._budget_edit.text()
        if name == "params":
            return self._params_edit.text()
        return ""

    def node_ids(self) -> list[str]:
        pipeline = self.pipeline
        return [node.id for node in pipeline.nodes] if pipeline is not None else []

    # --- документ -------------------------------------------------------

    def rebuild(self, document: ProjectDocument) -> None:
        self._document = document
        self._refresh_all()

    def set_project_path(self, path: Path | None) -> None:
        """Привязывает снимок исходников к пути проекта."""
        self._snapshot_file = None if path is None else snapshot_path(path)

    def select_node(self, node_id: str | None) -> bool:
        pipeline = self.pipeline
        if pipeline is None:
            return False
        if node_id is not None and all(node.id != node_id for node in pipeline.nodes):
            return False
        self._selected_id = node_id
        self._sync_selection()
        return True

    def _refresh_all(self) -> None:
        if self._refreshing or self._disposed:
            return
        self._refreshing = True
        try:
            self._sync_combos()
            self._recalculate()
            self._fill_graph()
            self._fill_edges()
            self._sync_selection()
            self._update_metrics()
            self._update_actions()
        finally:
            self._refreshing = False

    def _sync_combos(self) -> None:
        pipelines = self._document.pipelines
        self._pipeline_index = min(self._pipeline_index, max(0, len(pipelines) - 1))
        self._pipeline_combo.blockSignals(True)
        self._pipeline_combo.clear()
        for index, pipeline in enumerate(pipelines):
            self._pipeline_combo.addItem(pipeline.pipeline_id, index)
        self._pipeline_combo.setCurrentIndex(self._pipeline_index if pipelines else -1)
        self._pipeline_combo.blockSignals(False)
        kind = pipelines[self._pipeline_index].kind if pipelines else "computational"
        self._kind_combo.blockSignals(True)
        self._kind_combo.setCurrentText(kind)
        self._kind_combo.blockSignals(False)
        self._sync_node_types(kind)

    def _sync_node_types(self, kind: str) -> None:
        current = self._node_type_combo.currentData()
        self._node_type_combo.blockSignals(True)
        self._node_type_combo.clear()
        for name in NODE_TYPES_BY_KIND.get(kind, ()):
            spec = node_spec(name)
            self._node_type_combo.addItem(f"{spec.title} · {name}", name)
        index = self._node_type_combo.findData(current)
        self._node_type_combo.setCurrentIndex(index if index >= 0 else 0)
        self._node_type_combo.blockSignals(False)

    def _recalculate(self) -> None:
        pipeline = self.pipeline
        if pipeline is None:
            self._graph = PipelineGraph()
            self._issues = []
            return
        self._graph = build_pipeline_graph(pipeline)
        self._issues = pipeline_issues(pipeline, prefix=f"pipelines.{self._pipeline_index}")

    def _update_actions(self) -> None:
        has_pipeline = self.pipeline is not None
        for widget in (
            self._add_button,
            self._remove_button,
            self._up_button,
            self._down_button,
            self._validate_button,
            self._run_button,
            self._edge_add_button,
            self._edge_remove_button,
        ):
            widget.setEnabled(has_pipeline)

    # --- панели ---------------------------------------------------------

    def _fill_graph(self) -> None:
        self._graph_tree.blockSignals(True)
        self._graph_tree.clear()
        pipeline = self.pipeline
        if pipeline is None:
            self._graph_tree.blockSignals(False)
            self._set_status("в проекте нет конвейеров", error=True)
            return
        by_id = {node.id: node for node in pipeline.nodes}
        grouped: dict[int, list[str]] = {}
        for name in self._graph.order:
            grouped.setdefault(self._graph.layers.get(name, 0), []).append(name)
        for layer in sorted(grouped):
            header = QTreeWidgetItem([f"Слой {layer}", ""])
            header.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._graph_tree.addTopLevelItem(header)
            for name in grouped[layer]:
                node = by_id[name]
                spec = node_spec(node.type) if node.type in NODE_TYPES else None
                title = spec.title if spec is not None else node.type
                item = QTreeWidgetItem([f"{title} · {name}", node.type])
                item.setData(0, Qt.ItemDataRole.UserRole, name)
                self._decorate(item, node)
                header.addChild(item)
        self._graph_tree.expandAll()
        self._graph_tree.blockSignals(False)

    def _decorate(self, item: QTreeWidgetItem, node: PipelineNode) -> None:
        if node.id in self._graph.unknown_types:
            item.setForeground(0, QBrush(_CYCLE_COLOR))
            item.setToolTip(0, "неизвестный тип узла")
        elif node.id in self._graph.cycles:
            item.setForeground(0, QBrush(_CYCLE_COLOR))
            item.setToolTip(0, "узел входит в цикл")
        elif node.id not in self._graph.sources and node.id not in self._graph.sinks:
            item.setForeground(0, QBrush(_MUTED_COLOR))
        related = [
            issue.message
            for issue in self._issues
            if issue.path.endswith(f"nodes.{self._node_index(node.id)}")
        ]
        if related:
            item.setToolTip(0, "\n".join(related))

    def _fill_edges(self) -> None:
        self._edge_list.clear()
        pipeline = self.pipeline
        if pipeline is None:
            return
        for edge in pipeline.edges:
            item = QListWidgetItem(f"{edge.src} → {edge.dst}")
            item.setData(Qt.ItemDataRole.UserRole, (edge.src, edge.dst))
            self._edge_list.addItem(item)

    def _sync_selection(self) -> None:
        pipeline = self.pipeline
        if pipeline is None:
            self._selected_id = None
            self._show_empty_node("в проекте нет конвейеров")
            self._update_summary()
            return
        ids = [node.id for node in pipeline.nodes]
        if self._selected_id not in ids:
            self._selected_id = ids[0] if ids else None
        self._select_tree_item(self._selected_id)
        self._show_node(self._selected_id)
        self._update_summary()

    def _select_tree_item(self, node_id: str | None) -> None:
        self._graph_tree.blockSignals(True)
        try:
            for top in range(self._graph_tree.topLevelItemCount()):
                header = self._graph_tree.topLevelItem(top)
                if header is None:
                    continue
                for index in range(header.childCount()):
                    child = header.child(index)
                    if child is not None and child.data(0, Qt.ItemDataRole.UserRole) == node_id:
                        self._graph_tree.setCurrentItem(child)
                        return
        finally:
            self._graph_tree.blockSignals(False)

    def _show_node(self, node_id: str | None) -> None:
        pipeline = self.pipeline
        if pipeline is None or node_id is None:
            self._show_empty_node("узел не выбран")
            return
        node = next((item for item in pipeline.nodes if item.id == node_id), None)
        if node is None:
            self._show_empty_node("узел не выбран")
            return
        if node.type in NODE_TYPES:
            spec = node_spec(node.type)
            self._node_title.setText(f"{spec.title} · {node.id}")
            inputs = ", ".join(f"{name}: {port_title(kind)}" for name, kind in spec.inputs.items())
            outputs = ", ".join(
                f"{name}: {port_title(kind)}" for name, kind in spec.outputs.items()
            )
            self._node_hint.setText(
                f"тип {node.type} · {spec.description}\n"
                f"входы: {inputs or '—'}\nвыходы: {outputs or '—'}"
            )
        else:
            self._node_title.setText(f"неизвестный тип · {node.id}")
            self._node_hint.setText(f"тип {node.type} отсутствует в реестре")
        self._budget_edit.blockSignals(True)
        self._budget_edit.setText(f"{node_budget_ms(node):g}")
        self._budget_edit.blockSignals(False)
        self._params_edit.blockSignals(True)
        self._params_edit.setText(json.dumps(node.params, ensure_ascii=False, sort_keys=True))
        self._params_edit.blockSignals(False)

    def _show_empty_node(self, message: str) -> None:
        self._node_title.setText(message)
        self._node_hint.setText("")
        self._budget_edit.clear()
        self._params_edit.clear()

    def _update_summary(self) -> None:
        pipeline = self.pipeline
        if pipeline is None:
            self._summary_label.setText("прогон не выполнялся")
            self._status_label.clear()
            return
        errors = sum(1 for item in self._issues if item.severity is Severity.ERROR)
        warnings = len(self._issues) - errors
        self._summary_label.setText(
            f"план {self._graph.critical_path_ms:.1f} мс · узлов {len(pipeline.nodes)}"
            f" · рёбер {len(pipeline.edges)} · слоёв {self._graph.layer_count}"
            f" · ошибок {errors} · предупреждений {warnings}\n"
            f"критический путь: {' → '.join(self._graph.critical_path) or '—'}"
        )
        self._set_status(
            f"конвейер {pipeline.pipeline_id}: ошибок {errors}, предупреждений {warnings}",
            error=errors > 0,
        )

    # --- выбор ----------------------------------------------------------

    def _on_graph_selection(self, current: QTreeWidgetItem | None, _previous: Any = None) -> None:
        if current is None:
            return
        node_id = current.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(node_id, str):
            self._selected_id = node_id
            self._show_node(node_id)

    def _on_pipeline_changed(self, index: int) -> None:
        if self._refreshing or index < 0:
            return
        self._pipeline_index = index
        self._selected_id = None
        self._report = None
        self._refresh_all()

    def _on_kind_changed(self, kind: str) -> None:
        if self._refreshing or not kind:
            return
        self._sync_node_types(kind)

    # --- правка ---------------------------------------------------------

    def _node_index(self, node_id: str) -> int:
        pipeline = self.pipeline
        if pipeline is None:
            return 0
        for index, node in enumerate(pipeline.nodes):
            if node.id == node_id:
                return index
        return 0

    def apply_node(self) -> bool:
        """Применяет бюджет и параметры выбранного узла."""
        if self._selected_id is None or self.pipeline is None:
            self._set_status("узел не выбран", error=True)
            return False
        index = self._node_index(self._selected_id)
        try:
            budget_text = self._budget_edit.text().strip()
            budget = float(budget_text.replace(",", ".")) if budget_text else None
            params = json.loads(self._params_edit.text() or "{}")
            if not isinstance(params, dict):
                raise ValueError("параметры должны быть объектом JSON")
        except (ValueError, json.JSONDecodeError) as exc:
            self._set_status(f"узел: {exc}", error=True)
            return False
        node = self.pipeline.nodes[index]
        payload = node.model_dump(mode="python")
        payload["budget_ms"] = budget
        payload["params"] = params
        nodes = list(self.pipeline.nodes)
        nodes[index] = PipelineNode.model_validate(payload)
        if not self._write_pipeline(nodes=nodes, edges=list(self.pipeline.edges)):
            return False
        self.rebuild(self._store.document)
        self._set_status("узел обновлён")
        return True

    def submit_field(self, name: str, text: str) -> bool:
        if name == "budget_ms":
            self._budget_edit.setText(text)
        elif name == "params":
            self._params_edit.setText(text)
        else:
            return False
        return self.apply_node()

    def add_node(self, kind: str | None = None) -> bool:
        pipeline = self.pipeline
        if pipeline is None:
            self._set_status("в проекте нет конвейеров", error=True)
            return False
        target = kind or self._node_type_combo.currentData()
        if not isinstance(target, str):
            return False
        if target not in NODE_TYPES:
            self._set_status(f"неизвестный тип узла {target!r}", error=True)
            return False
        existing = {node.id for node in pipeline.nodes}
        node = PipelineNode.model_validate(
            {
                "id": _unique_id(target, existing),
                "type": target,
                "params": default_params(target),
                "budget_ms": node_spec(target).budget_ms,
            }
        )
        nodes = [*pipeline.nodes, node]
        if not self._write_pipeline(nodes=nodes, edges=list(pipeline.edges)):
            return False
        self._selected_id = node.id
        self.rebuild(self._store.document)
        self._set_status(f"добавлен узел {node.id}")
        return True

    def remove_node(self, node_id: str | None = None) -> bool:
        pipeline = self.pipeline
        if pipeline is None:
            self._set_status("в проекте нет конвейеров", error=True)
            return False
        target = node_id or self._selected_id
        if target is None:
            self._set_status("узел не выбран", error=True)
            return False
        remaining = [item for item in pipeline.nodes if item.id != target]
        if len(remaining) == len(pipeline.nodes):
            self._set_status(f"узел {target!r} не найден", error=True)
            return False
        if not remaining:
            self._set_status("нельзя удалить последний узел конвейера", error=True)
            return False
        edges = [
            edge
            for edge in pipeline.edges
            if _edge_node(edge.src) != target and _edge_node(edge.dst) != target
        ]
        if not self._write_pipeline(nodes=remaining, edges=edges):
            return False
        self._selected_id = None
        self.rebuild(self._store.document)
        self._set_status(f"удалён узел {target}")
        return True

    def move_node(self, delta: int) -> bool:
        pipeline = self.pipeline
        if pipeline is None or self._selected_id is None:
            self._set_status("узел не выбран", error=True)
            return False
        nodes = list(pipeline.nodes)
        index = self._node_index(self._selected_id)
        target = index + delta
        if not 0 <= target < len(nodes):
            return False
        nodes[index], nodes[target] = nodes[target], nodes[index]
        if not self._write_pipeline(nodes=nodes, edges=list(pipeline.edges)):
            return False
        self.rebuild(self._store.document)
        self._set_status(f"узел {self._selected_id} перемещён")
        return True

    def add_edge(self, src: str | None = None, dst: str | None = None) -> bool:
        pipeline = self.pipeline
        if pipeline is None:
            self._set_status("в проекте нет конвейеров", error=True)
            return False
        source = (src or self._edge_src.text()).strip()
        target = (dst or self._edge_dst.text()).strip()
        if not source or not target:
            self._set_status("нужны оба порта: узел.порт", error=True)
            return False
        try:
            edge = PipelineEdge.model_validate({"from": source, "to": target})
        except ValueError as exc:
            self._set_status(f"связь: {exc}", error=True)
            return False
        reason = self._edge_blocker(pipeline, edge)
        if reason is not None:
            self._set_status(reason, error=True)
            return False
        edges = [*pipeline.edges, edge]
        if not self._write_pipeline(nodes=list(pipeline.nodes), edges=edges):
            return False
        self.rebuild(self._store.document)
        self._set_status(f"добавлена связь {source} → {target}")
        return True

    def _edge_blocker(self, pipeline: PipelineSection, edge: PipelineEdge) -> str | None:
        """Причина, по которой ребро нельзя добавить, или ``None``."""
        by_id = {node.id: node for node in pipeline.nodes}
        try:
            src = parse_port_ref(edge.src)
            dst = parse_port_ref(edge.dst)
        except ValueError as exc:
            return str(exc)
        if src.node not in by_id or dst.node not in by_id:
            return f"узел {src.node if src.node not in by_id else dst.node!r} не найден"
        src_node = by_id[src.node]
        dst_node = by_id[dst.node]
        if src_node.type not in NODE_TYPES or dst_node.type not in NODE_TYPES:
            return "тип узла отсутствует в реестре"
        src_kind = node_output_kind(src_node.type, src.port)
        if src_kind is None:
            return f"у узла {src.node!r} нет выхода {src.port!r}"
        dst_kind = node_input_kind(dst_node.type, dst.port)
        if dst_kind is None:
            return f"у узла {dst.node!r} нет входа {dst.port!r}"
        if not accepts(dst_kind, src_kind):
            return (
                f"порт {edge.src} ({port_title(src_kind)}) несовместим"
                f" с {edge.dst} ({port_title(dst_kind)})"
            )
        if any(item.src == edge.src and item.dst == edge.dst for item in pipeline.edges):
            return "такая связь уже существует"
        probe = pipeline.model_copy(
            update={"edges": [*pipeline.edges, edge]},
        )
        if build_pipeline_graph(probe).cycles:
            return "связь создаёт цикл"
        return None

    def remove_edge(self, index: int | None = None) -> bool:
        pipeline = self.pipeline
        if pipeline is None:
            self._set_status("в проекте нет конвейеров", error=True)
            return False
        row = self._edge_list.currentRow() if index is None else index
        if not 0 <= row < len(pipeline.edges):
            self._set_status("связь не выбрана", error=True)
            return False
        remaining = [edge for position, edge in enumerate(pipeline.edges) if position != row]
        if not self._write_pipeline(nodes=list(pipeline.nodes), edges=remaining):
            return False
        self.rebuild(self._store.document)
        self._set_status("связь удалена")
        return True

    def _write_pipeline(
        self,
        *,
        nodes: list[PipelineNode],
        edges: list[PipelineEdge],
    ) -> bool:
        """Пишет конвейер целиком: узлы и рёбра меняются одной командой undo."""
        pipeline = self.pipeline
        if pipeline is None:
            return False
        payload = pipeline.model_dump(mode="python")
        payload["nodes"] = [item.model_dump(mode="python") for item in nodes]
        payload["edges"] = [item.model_dump(mode="python") for item in edges]
        return self._write(f"pipelines.{self._pipeline_index}", payload)

    def _write(self, path: str, value: Any) -> bool:
        try:
            self._store.set_value(path, value)
        except ProjectValidationError as exc:
            detail = exc.issues[0].message if exc.issues else str(exc)
            self._set_status(f"{path}: {detail}", error=True)
            return False
        except ProjectError as exc:
            self._set_status(f"{path}: {exc}", error=True)
            return False
        return True

    # --- проверка и прогон ----------------------------------------------

    def validate_pipeline(self) -> list[Issue]:
        self._recalculate()
        errors = sum(1 for item in self._issues if item.severity is Severity.ERROR)
        self._set_status(
            f"проверка: замечаний {len(self._issues)}, ошибок {errors}",
            error=errors > 0,
        )
        self._update_summary()
        return list(self._issues)

    def run_pipeline(
        self,
        *,
        frames: int = 120,
        fps: float = 120.0,
        work_scale: float = 1.0,
        max_queue: int = 4,
        realtime_drop: bool = False,
    ) -> bool:
        pipeline = self.pipeline
        if pipeline is None:
            self._set_status("в проекте нет конвейеров", error=True)
            return False
        if not can_run(pipeline):
            self._set_status("конвейер не проходит валидацию: прогон отменён", error=True)
            return False
        config = PipelineConfig(
            frames=frames,
            fps=fps,
            work_scale=work_scale,
            max_queue=max_queue,
            realtime_drop=realtime_drop,
            backend=make_backend("stub"),
        )
        runner = PipelineRunner(pipeline, config=config)
        try:
            self._report = runner.run()
        except ProjectError as exc:
            self._set_status(str(exc), error=True)
            return False
        finally:
            runner.close()
        self._update_metrics()
        self._set_status(self._report.summary())
        return self._report.ok

    def _update_metrics(self) -> None:
        report = self._report
        if report is None or report.pipeline_id != (
            self.pipeline.pipeline_id if self.pipeline else ""
        ):
            self._node_table.setRowCount(0)
            self._events_list.clear()
            self._update_summary()
            return
        metrics = report.metrics
        self._summary_label.setText(
            f"{report.summary()}\n"
            f"факт по узлам: {metrics.total_work_ms:.1f} мс"
            f" · очередь до {metrics.queue_max_depth}"
            f" · ожидание {metrics.queue_wait_ms:.1f} мс"
            f" · метки {'монотонны' if metrics.monotonic else 'нарушены'}"
        )
        rows = list(metrics.nodes.items())
        self._node_table.setRowCount(len(rows))
        for row, (name, item) in enumerate(rows):
            for column, text in enumerate(
                (
                    name,
                    f"{item.plan_ms:.2f}",
                    f"{item.p50_ms:.2f}",
                    f"{item.p95_ms:.2f}",
                    f"{item.max_ms:.2f}",
                )
            ):
                self._node_table.setItem(row, column, QTableWidgetItem(text))
        self._events_list.clear()
        for event in report.events[-200:]:
            text = (
                f"{event.get('kind')}: кадр {event.get('frame_id')}"
                f" · {event.get('node', '—')}"
                f" · {event.get('latency_ms', '—')} мс"
            )
            self._events_list.addItem(QListWidgetItem(text))

    # --- исходники ------------------------------------------------------

    def choose_source_root(self) -> bool:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Каталог исходников",
            str(self._source_root or self._settings.last_dir or Path.cwd()),
        )
        if not chosen:
            return False
        return self.set_source_root(Path(chosen))

    def set_source_root(self, path: Path | str) -> bool:
        root = Path(path)
        try:
            tree = scan_sources(root)
        except ProjectError as exc:
            self._set_status(str(exc), error=True)
            return False
        self._source_root = tree.root
        self._source_tree = tree
        self._source_status = None
        self._source_root_label.setText(str(tree.root))
        self._fill_source_tree()
        self._source_status_label.setText(
            f"файлов {len(tree.files)} · хэш {tree.digest[:16]}… · снимок не проверен"
        )
        return True

    def _fill_source_tree(self) -> None:
        self._source_tree_widget.clear()
        tree = self._source_tree
        if tree is None:
            return
        directories: dict[str, QTreeWidgetItem] = {}
        for item in tree.files:
            parts = item.path.split("/")
            parent: QTreeWidgetItem | None = None
            prefix = ""
            for part in parts[:-1]:
                prefix = f"{prefix}/{part}" if prefix else part
                node = directories.get(prefix)
                if node is None:
                    node = QTreeWidgetItem([part, "", ""])
                    if parent is None:
                        self._source_tree_widget.addTopLevelItem(node)
                    else:
                        parent.addChild(node)
                    directories[prefix] = node
                parent = node
            child = QTreeWidgetItem([parts[-1], item.language, str(item.size)])
            child.setData(0, Qt.ItemDataRole.UserRole, item.path)
            if parent is None:
                self._source_tree_widget.addTopLevelItem(child)
            else:
                parent.addChild(child)
        self._source_tree_widget.expandAll()

    def _on_source_selected(
        self,
        current: QTreeWidgetItem | None,
        _previous: Any = None,
    ) -> None:
        if current is None:
            return
        relative = current.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(relative, str) or self._source_tree is None:
            return
        try:
            text = self._source_tree.read(relative)
        except ProjectError as exc:
            self._set_status(str(exc), error=True)
            return
        self._source_text.setPlainText(text)
        self._source_text.moveCursor(QTextCursor.MoveOperation.Start)
        self._highlighter.set_language(current.text(1))

    def snapshot_sources(self) -> bool:
        tree = self._source_tree
        if tree is None:
            self._set_status("каталог исходников не выбран", error=True)
            return False
        target = self._snapshot_file or (Path.cwd() / "project.json.sources.json")
        try:
            save_snapshot(tree, target)
        except OSError as exc:
            self._set_status(f"снимок не сохранён: {exc}", error=True)
            return False
        self._source_status = check_stale(tree, load_snapshot(target))
        self._source_status_label.setText(
            f"снимок: {target} · состояние {self._source_status.state}"
        )
        self._set_status(f"снимок исходников: {target}")
        return True

    def check_sources(self) -> SourceStatus | None:
        tree = self._source_tree
        if tree is None:
            self._set_status("каталог исходников не выбран", error=True)
            return None
        target = self._snapshot_file or (Path.cwd() / "project.json.sources.json")
        self._source_status = check_stale(tree, load_snapshot(target))
        status = self._source_status
        detail = ""
        if status.changed:
            detail += f" · изменены: {', '.join(status.changed)}"
        if status.added:
            detail += f" · добавлены: {', '.join(status.added)}"
        if status.removed:
            detail += f" · удалены: {', '.join(status.removed)}"
        self._source_status_label.setText(f"состояние: {status.state}{detail}")
        self._set_status(f"исходники: {status.state}{detail}", error=status.stale)
        return status

    def open_source(self, relative: str | None = None) -> bool:
        tree = self._source_tree
        if tree is None:
            self._set_status("каталог исходников не выбран", error=True)
            return False
        target = relative
        if target is None:
            current = self._source_tree_widget.currentItem()
            value = current.data(0, Qt.ItemDataRole.UserRole) if current is not None else None
            target = value if isinstance(value, str) else None
        if target is None:
            self._set_status("файл не выбран", error=True)
            return False
        path = tree.root / target
        if not path.is_file():
            self._set_status(f"файл {target!r} не найден", error=True)
            return False
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        self._set_status(
            f"открыт во внешнем редакторе: {target}" if opened else f"не удалось открыть {target}",
            error=not opened,
        )
        return bool(opened)

    # --- служебное ------------------------------------------------------

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self._status_label.setStyleSheet(_ERROR_STYLE if error else "")
        self._status_label.setText(text)

    def dispose(self) -> None:
        """Отключает сигналы: закрытая вкладка не получает правки."""
        self._disposed = True
        self._graph_tree.blockSignals(True)
        self._edge_list.blockSignals(True)
        self._source_tree_widget.blockSignals(True)


class _SourceHighlighter(QSyntaxHighlighter):
    """Простая подсветка Python, JSON и YAML без внешних зависимостей."""

    _PYTHON_KEYWORDS = (
        "def",
        "class",
        "return",
        "if",
        "elif",
        "else",
        "for",
        "while",
        "import",
        "from",
        "as",
        "with",
        "try",
        "except",
        "finally",
        "raise",
        "pass",
        "None",
        "True",
        "False",
        "and",
        "or",
        "not",
        "in",
        "is",
        "lambda",
        "yield",
    )

    def __init__(self, document: Any) -> None:
        super().__init__(document)
        self._language = "text"
        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = []

    def set_language(self, language: str) -> None:
        self._language = language
        self._rules = self._build_rules(language)
        self.rehighlight()

    def _format(self, color: str, *, bold: bool = False) -> QTextCharFormat:
        result = QTextCharFormat()
        result.setForeground(QColor(color))
        if bold:
            result.setFontWeight(QFont.Weight.Bold)
        return result

    def _build_rules(self, language: str) -> list[tuple[QRegularExpression, QTextCharFormat]]:
        rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        if language == "python":
            keyword = self._format("#c678dd", bold=True)
            for word in self._PYTHON_KEYWORDS:
                rules.append((QRegularExpression(rf"\b{word}\b"), keyword))
        if language in {"python", "yaml", "toml", "ini"}:
            rules.append((QRegularExpression(r"#.*$"), self._format("#6a737d")))
        if language in {"json", "python"}:
            rules.append((QRegularExpression(r'"[^"]*"'), self._format("#98c379")))
            rules.append((QRegularExpression(r"'[^']*'"), self._format("#98c379")))
        if language in {"python", "json", "yaml", "toml", "ini"}:
            rules.append((QRegularExpression(r"\b\d+(\.\d+)?\b"), self._format("#d19a66")))
        if language == "json":
            rules.append(
                (QRegularExpression(r'"[^"]*"(?=\s*:)'), self._format("#61afef", bold=True))
            )
        return rules

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt override
        for expression, fmt in self._rules:
            iterator = expression.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)


def _unique_id(kind: str, existing: set[str]) -> str:
    base = kind.replace(".", "_")
    index = 1
    while f"{base}-{index}" in existing:
        index += 1
    return f"{base}-{index}"


def _edge_node(ref: str) -> str:
    return ref.split(".", 1)[0]
