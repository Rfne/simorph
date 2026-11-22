"""Вкладка «Журналы»: прогоны, ряды, фильтры и экспорт (T9-03, G9-03).

Журнал — отдельный файл ``run.sqlite``. Вкладка открывает его, показывает
списки прогонов и ряды метрик или событий, фильтрует по виду, статусу,
имени метрики и интервалу времени и выгружает выборку в CSV или Parquet.

Большие выборки читаются в фоновом потоке: интерфейс не замирает на
10^5 строках, а результат приходит сигналом. Синхронные методы
(``refresh_runs``, ``refresh_rows``) оставлены для тестов и для малых
журналов — они делают ту же работу без потока.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from simorph.core.errors import ProjectError
from simorph.core.logs import (
    ExportFormat,
    ExportResult,
    RunStore,
    default_log_path,
    export_table,
    open_store,
)
from simorph.settings import ApplicationSettings

__all__ = ["LogsView"]

#: Стиль подсказок.
_MUTED_STYLE = "color: palette(mid);"
#: Стиль ошибки.
_ERROR_STYLE = "color: #c0392b;"

#: Колонки таблицы прогонов.
RUN_COLUMNS: tuple[str, ...] = ("Прогон", "Вид", "Статус", "Начат", "Завершён", "Объект")

#: Предел строк выборки в таблице.
DEFAULT_LIMIT = 5000


class _JobSignals(QObject):
    """Сигналы фоновой задачи: результат или ошибка."""

    done = Signal(object)
    failed = Signal(str)


class _Job(QRunnable):
    """Читает журнал в фоновом потоке и отдаёт результат сигналом.

    Сигналы живут отдельно от задачи: Qt удаляет ``QRunnable`` сразу после
    ``run``, а доставка результата в поток интерфейса происходит позже.
    """

    def __init__(self, function: Any, signals: _JobSignals) -> None:
        super().__init__()
        self.function = function
        self.signals = signals

    def run(self) -> None:  # pragma: no cover - выполняется в пуле потоков
        try:
            result = self.function()
        except Exception as exc:  # сбой чтения не должен ронять окно
            self.signals.failed.emit(str(exc))
            return
        self.signals.done.emit(result)


class LogsView(QWidget):
    """Табличный просмотр журнала прогонов."""

    def __init__(
        self,
        settings: ApplicationSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._store: RunStore | None = None
        self._path: Path | None = None
        self._runs: list[dict[str, Any]] = []
        self._rows: list[dict[str, Any]] = []
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._jobs: list[_JobSignals] = []
        self._disposed = False
        self._last_query_ms = 0.0
        self.setObjectName("editor_logs")

        self._path_label = QLabel("журнал не открыт")
        self._path_label.setStyleSheet(_MUTED_STYLE)
        self._open_button = QPushButton("Открыть…")
        self._open_button.clicked.connect(lambda: self.choose_database())
        self._refresh_button = QPushButton("Обновить")
        self._refresh_button.clicked.connect(lambda: self.refresh_all())

        self._kind_combo = QComboBox()
        self._kind_combo.addItem("все виды", None)
        self._kind_combo.addItem("симуляция", "simulation")
        self._kind_combo.addItem("конвейер", "pipeline")
        self._kind_combo.currentIndexChanged.connect(lambda _index: self.refresh_runs())
        self._status_combo = QComboBox()
        self._status_combo.addItem("все статусы", None)
        for status in ("running", "finished", "interrupted", "failed"):
            self._status_combo.addItem(status, status)
        self._status_combo.currentIndexChanged.connect(lambda _index: self.refresh_runs())
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("фильтр по run_id, объекту или хэшу")
        self._search_edit.textChanged.connect(lambda _text: self._apply_run_filter())

        self._runs_table = QTableWidget(0, len(RUN_COLUMNS))
        self._runs_table.setHorizontalHeaderLabels(list(RUN_COLUMNS))
        self._runs_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._runs_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._runs_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._runs_table.setSortingEnabled(True)
        self._runs_table.setColumnWidth(0, 180)
        self._runs_table.setColumnWidth(3, 170)
        self._runs_table.itemSelectionChanged.connect(self._on_run_selected)

        self._table_combo = QComboBox()
        self._table_combo.addItem("метрики", "metrics")
        self._table_combo.addItem("события", "events")
        self._table_combo.currentIndexChanged.connect(lambda _index: self._on_table_changed())
        self._metric_combo = QComboBox()
        self._metric_combo.setEditable(True)
        self._metric_combo.setMinimumWidth(220)
        metric_line = self._metric_combo.lineEdit()
        if metric_line is not None:
            metric_line.setPlaceholderText("имя метрики или префикс")
        self._since_edit = QLineEdit()
        self._since_edit.setPlaceholderText("с, нс")
        self._until_edit = QLineEdit()
        self._until_edit.setPlaceholderText("по, нс")
        self._apply_button = QPushButton("Показать")
        self._apply_button.clicked.connect(lambda: self.refresh_rows())
        self._csv_button = QPushButton("CSV…")
        self._csv_button.clicked.connect(lambda: self.export_rows("csv"))
        self._parquet_button = QPushButton("Parquet…")
        self._parquet_button.clicked.connect(lambda: self.export_rows("parquet"))

        self._rows_table = QTableWidget(0, 5)
        self._rows_table.setHorizontalHeaderLabels(["t, нс", "Имя", "Значение", "Ед.", "Контекст"])
        self._rows_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._rows_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._rows_table.setSortingEnabled(False)
        self._rows_table.setColumnWidth(0, 150)
        self._rows_table.setColumnWidth(1, 220)

        self._summary_label = QLabel("журнал не открыт")
        self._summary_label.setWordWrap(True)
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        filters = QHBoxLayout()
        filters.addWidget(self._kind_combo)
        filters.addWidget(self._status_combo)
        filters.addWidget(self._search_edit, 1)
        left_layout.addLayout(filters)
        left_layout.addWidget(self._runs_table, 1)
        left_layout.addWidget(self._summary_label)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        row_filters = QHBoxLayout()
        row_filters.addWidget(self._table_combo)
        row_filters.addWidget(self._metric_combo, 1)
        row_filters.addWidget(self._since_edit)
        row_filters.addWidget(self._until_edit)
        row_filters.addWidget(self._apply_button)
        right_layout.addLayout(row_filters)
        right_layout.addWidget(self._rows_table, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self._csv_button)
        actions.addWidget(self._parquet_button)
        right_layout.addLayout(actions)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(left)
        self._splitter.addWidget(right)
        self._splitter.setSizes([560, 640])

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Журнал:"))
        toolbar.addWidget(self._path_label, 1)
        toolbar.addWidget(self._open_button)
        toolbar.addWidget(self._refresh_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(toolbar)
        layout.addWidget(self._splitter, 1)
        layout.addWidget(self._status_label)

        self._update_actions()

    # --- свойства для тестов ---------------------------------------------

    @property
    def store(self) -> RunStore | None:
        return self._store

    @property
    def database(self) -> Path | None:
        return self._path

    @property
    def runs(self) -> list[dict[str, Any]]:
        return list(self._runs)

    @property
    def rows(self) -> list[dict[str, Any]]:
        return list(self._rows)

    @property
    def selected_run_id(self) -> str | None:
        item: QTableWidgetItem | None = self._runs_table.currentItem()
        if item is None:
            return None
        row = item.row()
        return self._runs[row]["run_id"] if 0 <= row < len(self._runs) else None

    @property
    def status_text(self) -> str:
        return self._status_label.text()

    @property
    def summary_text(self) -> str:
        return self._summary_label.text()

    @property
    def last_query_ms(self) -> float:
        return self._last_query_ms

    def run_ids(self) -> list[str]:
        return [str(item["run_id"]) for item in self._runs]

    def row_values(self, row: int) -> tuple[str, ...]:
        values: list[str] = []
        for column in range(self._rows_table.columnCount()):
            cell: QTableWidgetItem | None = self._rows_table.item(row, column)
            values.append("" if cell is None else cell.text())
        return tuple(values)

    def row_count(self) -> int:
        return self._rows_table.rowCount()

    def table_name(self) -> str:
        data = self._table_combo.currentData()
        return str(data) if isinstance(data, str) else "metrics"

    # --- управление фильтрами -------------------------------------------

    def set_kind_filter(self, kind: str | None) -> int:
        """Выбирает вид прогона и перечитывает список."""
        index = self._kind_combo.findData(kind)
        self._kind_combo.setCurrentIndex(max(0, index))
        return self.refresh_runs()

    def set_status_filter(self, status: str | None) -> int:
        """Выбирает статус прогона и перечитывает список."""
        index = self._status_combo.findData(status)
        self._status_combo.setCurrentIndex(max(0, index))
        return self.refresh_runs()

    def set_search(self, text: str) -> None:
        """Текстовый фильтр по ``run_id``, объекту и хэшу проекта."""
        self._search_edit.setText(text)

    def set_metric(self, text: str) -> None:
        """Имя метрики (точное) или префикс с точкой на конце."""
        self._metric_combo.setEditText(text)

    def set_table(self, table: str) -> int:
        """Переключает ряды: ``metrics`` или ``events``."""
        index = max(0, self._table_combo.findData(table))
        if index == self._table_combo.currentIndex():
            self._on_table_changed()
        else:
            self._table_combo.setCurrentIndex(index)
        return len(self._rows)

    def set_interval(self, since_ns: int | None, until_ns: int | None) -> int:
        """Интервал времени сцены; ``None`` снимает границу."""
        self._since_edit.setText("" if since_ns is None else str(since_ns))
        self._until_edit.setText("" if until_ns is None else str(until_ns))
        return self.refresh_rows()

    def select_run(self, run_id: str) -> bool:
        """Выбирает прогон в списке и читает его ряды."""
        for row, item in enumerate(self._runs):
            if item["run_id"] == run_id:
                self._runs_table.selectRow(row)
                return True
        return False

    # --- журнал ----------------------------------------------------------

    def set_database(self, path: Path | str | None) -> bool:
        """Открывает журнал; ``None`` закрывает текущий."""
        if path is None:
            self._close_store()
            self._path = None
            self._path_label.setText("журнал не открыт")
            self._update_actions()
            return False
        target = Path(path)
        if self._store is not None and self._path == target:
            self.refresh_all()
            return True
        self._close_store()
        if not target.exists():
            self._path = None
            self._path_label.setText(f"журнал не найден: {target}")
            self._set_status("журнал появится после первого прогона", error=True)
            self._update_actions()
            return False
        try:
            self._store = open_store(target)
        except (ProjectError, OSError) as exc:
            self._set_status(f"журнал не открыт: {exc}", error=True)
            return False
        self._path = target
        self._path_label.setText(str(target))
        stats = self._store.stats()
        self._set_status(
            f"схема {stats['schema_version']} · {stats['journal_mode']}"
            f" · прогонов {stats['counts']['runs']}"
        )
        self.refresh_all()
        return True

    def set_project_path(self, path: Path | None) -> None:
        """Журнал рядом с проектом: ``run.sqlite``."""
        if path is None:
            if self._store is not None:
                self.set_database(None)
            return
        self.set_database(default_log_path(path))

    def choose_database(self) -> bool:
        chosen = QFileDialog.getOpenFileName(
            self,
            "Журнал прогонов",
            str(self._path or self._settings.last_dir or Path.cwd()),
            "Журнал simorph (run.sqlite *.sqlite);;SQLite (*.sqlite *.db)",
        )
        if not chosen or not chosen[0]:
            return False
        return self.set_database(Path(chosen[0]))

    def refresh_all(self) -> None:
        self.refresh_runs()
        self.refresh_rows()

    def refresh_runs(self) -> int:
        """Читает список прогонов с фильтрами."""
        store = self._store
        if store is None:
            self._runs = []
            self._fill_runs()
            return 0
        started = time.perf_counter()
        self._runs = store.list_runs(
            kind=self._kind_combo.currentData(),
            status=self._status_combo.currentData(),
            limit=500,
        )
        self._last_query_ms = (time.perf_counter() - started) * 1000.0
        self._fill_runs()
        self._apply_run_filter()
        self._fill_metric_names()
        self._set_status(f"прогонов {len(self._runs)} · выборка {self._last_query_ms:.1f} мс")
        return len(self._runs)

    def refresh_rows(self) -> int:
        """Читает ряды выбранного прогона с фильтрами."""
        store = self._store
        if store is None:
            self._rows = []
            self._fill_rows()
            return 0
        run_id = self.selected_run_id
        if run_id is None:
            self._rows = []
            self._fill_rows()
            return 0
        started = time.perf_counter()
        if self.table_name() == "events":
            self._rows = store.events(
                run_id,
                event_type=self._filter_text() or None,
                since_ns=self._since_ns(),
                until_ns=self._until_ns(),
                limit=DEFAULT_LIMIT,
            )
        else:
            name, prefix = self._metric_query()
            self._rows = store.metrics(
                run_id,
                name=name,
                prefix=prefix,
                since_ns=self._since_ns(),
                until_ns=self._until_ns(),
                limit=DEFAULT_LIMIT,
            )
        self._last_query_ms = (time.perf_counter() - started) * 1000.0
        self._fill_rows()
        self._set_status(f"строк {len(self._rows)} · выборка {self._last_query_ms:.1f} мс")
        return len(self._rows)

    def refresh_rows_async(self) -> None:
        """Читает ряды в фоновом потоке: интерфейс не блокируется."""
        store = self._store
        run_id = self.selected_run_id
        if store is None or run_id is None:
            return
        table = self.table_name()
        name, prefix = self._metric_query()
        since_ns = self._since_ns()
        until_ns = self._until_ns()

        def query() -> list[dict[str, Any]]:
            if table == "events":
                return store.events(
                    run_id,
                    event_type=name,
                    since_ns=since_ns,
                    until_ns=until_ns,
                    limit=DEFAULT_LIMIT,
                )
            return store.metrics(
                run_id,
                name=name,
                prefix=prefix,
                since_ns=since_ns,
                until_ns=until_ns,
                limit=DEFAULT_LIMIT,
            )

        signals = _JobSignals()
        signals.done.connect(self._on_rows_ready)
        signals.failed.connect(lambda message: self._set_status(message, error=True))
        # Сигналы держим до доставки: QRunnable Qt удаляет сразу после run.
        self._jobs.append(signals)
        if len(self._jobs) > 8:
            del self._jobs[: len(self._jobs) - 8]
        self._pool.start(_Job(query, signals))

    def _on_rows_ready(self, rows: object) -> None:
        if self._disposed or not isinstance(rows, list):
            return
        self._rows = rows
        self._fill_rows()
        self._set_status(f"строк {len(self._rows)}")

    def wait_for_reads(self, timeout_ms: int = 5000) -> bool:
        """Ждёт фоновых чтений и доставляет результаты в интерфейс."""
        finished = self._pool.waitForDone(timeout_ms)
        QApplication.processEvents()
        return finished

    def export_rows(self, fmt: ExportFormat) -> bool:
        """Выгружает текущую выборку в файл; ``fmt`` — ``csv`` или ``parquet``."""
        store = self._store
        run_id = self.selected_run_id
        if store is None or run_id is None:
            self._set_status("прогон не выбран", error=True)
            return False
        suffix = "parquet" if fmt == "parquet" else "csv"
        chosen = QFileDialog.getSaveFileName(
            self,
            "Экспорт рядов",
            str(self._settings.last_dir or Path.cwd()) + f"/{self.table_name()}.{suffix}",
            f"{suffix.upper()} (*.{suffix})",
        )
        if not chosen or not chosen[0]:
            return False
        return self.export_to(Path(chosen[0]), fmt)

    def export_to(self, path: Path | str, fmt: ExportFormat | None = None) -> bool:
        """Выгружает выборку в указанный файл (без диалога)."""
        store = self._store
        run_id = self.selected_run_id
        if store is None or run_id is None:
            self._set_status("прогон не выбран", error=True)
            return False
        try:
            name, prefix = self._metric_query()
            result: ExportResult = export_table(
                store,
                self.table_name(),
                path,
                fmt=fmt,
                run_id=run_id,
                name=name,
                prefix=prefix,
                since_ns=self._since_ns(),
                until_ns=self._until_ns(),
            )
        except (ProjectError, OSError, ValueError) as exc:
            self._set_status(f"экспорт не выполнен: {exc}", error=True)
            return False
        self._set_status(f"выгружено {result.rows} строк в {result.path} · {result.bytes} Б")
        return True

    # --- наполнение ------------------------------------------------------

    def _fill_runs(self) -> None:
        self._runs_table.blockSignals(True)
        self._runs_table.setSortingEnabled(False)
        self._runs_table.setRowCount(len(self._runs))
        for row, run in enumerate(self._runs):
            values = (
                str(run.get("run_id", "")),
                str(run.get("kind", "")),
                str(run.get("status", "")),
                str(run.get("started_at", "")),
                str(run.get("finished_at") or "—"),
                str(run.get("pipeline_id") or run.get("mission_id") or "—"),
            )
            for column, text in enumerate(values):
                self._runs_table.setItem(row, column, QTableWidgetItem(text))
        self._runs_table.setSortingEnabled(True)
        self._runs_table.blockSignals(False)
        if self._runs:
            self._runs_table.selectRow(0)

    def _fill_rows(self) -> None:
        self._rows_table.setRowCount(len(self._rows))
        events = self.table_name() == "events"
        for row, item in enumerate(self._rows):
            if events:
                values = (
                    str(item.get("t_ns", "")),
                    str(item.get("type", "")),
                    str(item.get("source", "")),
                    "",
                    _context_text(item.get("payload")),
                )
            else:
                values = (
                    str(item.get("t_ns", "")),
                    str(item.get("name", "")),
                    _number_text(item.get("value")),
                    str(item.get("unit") or ""),
                    _context_text(item.get("tags")),
                )
            for column, text in enumerate(values):
                self._rows_table.setItem(row, column, QTableWidgetItem(text))

    def _fill_metric_names(self) -> None:
        store = self._store
        run_id = self.selected_run_id
        current = self._metric_combo.currentText()
        self._metric_combo.blockSignals(True)
        self._metric_combo.clear()
        if store is not None and run_id is not None and self.table_name() == "metrics":
            self._metric_combo.addItems(store.metric_names(run_id))
        self._metric_combo.setEditText(current)
        self._metric_combo.blockSignals(False)

    def _apply_run_filter(self) -> None:
        """Скрывает строки, не совпадающие с текстовым фильтром."""
        needle = self._search_edit.text().strip().lower()
        for row, run in enumerate(self._runs):
            haystack = " ".join(
                str(run.get(key) or "")
                for key in ("run_id", "pipeline_id", "mission_id", "project_hash")
            ).lower()
            self._runs_table.setRowHidden(row, bool(needle) and needle not in haystack)
        if needle:
            self._summary_label.setText(f"фильтр: {needle} · прогонов {len(self._runs)}")
        else:
            self._summary_label.setText(f"прогонов {len(self._runs)}")

    # --- служебное -------------------------------------------------------

    def _on_run_selected(self) -> None:
        self._fill_metric_names()
        self.refresh_rows()

    def _on_table_changed(self) -> None:
        events = self.table_name() == "events"
        self._metric_combo.setEnabled(not events)
        # У метрик и событий разные пространства имён: фильтр не переносится.
        self._metric_combo.setEditText("")
        metric_line = self._metric_combo.lineEdit()
        if metric_line is not None:
            metric_line.setPlaceholderText("тип события" if events else "имя метрики или префикс")
        self.refresh_rows()

    def _filter_text(self) -> str:
        return self._metric_combo.currentText().strip()

    def _metric_query(self) -> tuple[str | None, str | None]:
        """Имя метрики или префикс: ``stage.`` — префикс, иначе точное имя."""
        text = self._filter_text()
        if not text:
            return None, None
        if text.endswith("."):
            return None, text
        return text, None

    def _since_ns(self) -> int | None:
        return _parse_ns(self._since_edit.text())

    def _until_ns(self) -> int | None:
        return _parse_ns(self._until_edit.text())

    def _update_actions(self) -> None:
        has_store = self._store is not None
        for widget in (
            self._refresh_button,
            self._apply_button,
            self._csv_button,
            self._parquet_button,
        ):
            widget.setEnabled(has_store)

    def _close_store(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self._status_label.setStyleSheet(_ERROR_STYLE if error else "")
        self._status_label.setText(text)

    def dispose(self) -> None:
        """Закрывает журнал: закрытая вкладка не держит соединение."""
        self._disposed = True
        self._runs_table.blockSignals(True)
        self._rows_table.blockSignals(True)
        # Дожидаемся чтения: фоновый поток не должен работать с закрытой базой.
        self._pool.clear()
        self._pool.waitForDone(2000)
        self._jobs.clear()
        self._close_store()


def _parse_ns(text: str) -> int | None:
    """Разбирает необязательное целое: пустая строка — нет фильтра."""
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return int(stripped)
    except ValueError:
        return None


def _number_text(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _context_text(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, dict):
        return ", ".join(f"{key}={item}" for key, item in list(value.items())[:3])
    return str(value)
