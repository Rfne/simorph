"""Вкладка «Отчёт»: конструктор из двух панелей и предпросмотр (T11-04, G11-03).

Слева — что показать: прогон, секции, форматы, порог. Справа — готовый
документ. Любое изменение пересобирает отчёт целиком, поэтому предпросмотр
всегда соответствует настройкам, а не является отдельной копией разметки.

Предпросмотр — тот же HTML, что уходит в файл: Qt загружает его как
локальный документ и подтягивает PNG рядом. Поэтому «в предпросмотре одно,
в экспорте другое» невозможно по построению.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from simorph.core.logs import RunStore, default_log_path, open_store
from simorph.core.report import (
    DEFAULT_SECTIONS,
    ReportConfig,
    render_report,
    report_data_from_run,
)
from simorph.settings import ApplicationSettings

__all__ = ["ReportView"]

#: Стиль подсказок.
_MUTED_STYLE = "color: palette(mid);"
#: Стиль ошибки.
_ERROR_STYLE = "color: #c0392b;"

#: Подписи секций конструктора.
SECTION_LABELS: dict[str, str] = {
    "metadata": "Метаданные",
    "config": "Конфигурация",
    "metrics": "Метрики",
    "plots": "Графики",
    "conclusions": "Выводы",
    "scale": "Масштаб",
}

#: Форматы экспорта и их подписи.
FORMAT_LABELS: dict[str, str] = {"md": "Markdown", "html": "HTML", "pdf": "PDF"}


class ReportView(QWidget):
    """Две панели: настройки отчёта и предпросмотр."""

    def __init__(
        self,
        settings: ApplicationSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._project: Path | None = None
        self._store: RunStore | None = None
        self._temp: Path | None = None
        self._last_seconds = 0.0
        self._last_html: str | None = None
        self.setObjectName("editor_report")

        self._run_combo = QComboBox()
        self._run_combo.setMinimumWidth(260)
        self._run_combo.currentIndexChanged.connect(lambda _index: self.refresh())

        self._section_boxes: dict[str, QCheckBox] = {}
        sections_box = QGroupBox("Секции")
        sections_layout = QVBoxLayout(sections_box)
        for name in DEFAULT_SECTIONS:
            box = QCheckBox(SECTION_LABELS.get(name, name))
            box.setChecked(True)
            box.toggled.connect(lambda _checked: self.refresh())
            sections_layout.addWidget(box)
            self._section_boxes[name] = box

        self._format_boxes: dict[str, QCheckBox] = {}
        formats_box = QGroupBox("Экспорт")
        formats_layout = QHBoxLayout(formats_box)
        for name, label in FORMAT_LABELS.items():
            box = QCheckBox(label)
            box.setChecked(name in ("md", "html"))
            formats_layout.addWidget(box)
            self._format_boxes[name] = box

        self._threshold = QDoubleSpinBox()
        self._threshold.setRange(0.1, 10.0)
        self._threshold.setSingleStep(0.1)
        self._threshold.setDecimals(2)
        self._threshold.setValue(1.5)
        self._threshold.setKeyboardTracking(False)
        self._threshold.valueChanged.connect(lambda _value: self.refresh())

        threshold_row = QHBoxLayout()
        threshold_row.addWidget(QLabel("Порог k.p95:"))
        threshold_row.addWidget(self._threshold)
        threshold_row.addStretch(1)

        self._build_button = QPushButton("Обновить")
        self._build_button.clicked.connect(lambda: self.refresh())
        self._save_button = QPushButton("Сохранить…")
        self._save_button.clicked.connect(lambda: self.export_report())
        actions = QHBoxLayout()
        actions.addWidget(self._build_button)
        actions.addWidget(self._save_button)
        actions.addStretch(1)

        self._status_label = QLabel("журнал не открыт")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(_MUTED_STYLE)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Прогон:"))
        left_layout.addWidget(self._run_combo)
        left_layout.addWidget(sections_box)
        left_layout.addWidget(formats_box)
        left_layout.addLayout(threshold_row)
        left_layout.addLayout(actions)
        left_layout.addWidget(self._status_label)
        left_layout.addStretch(1)

        self._preview = QTextBrowser()
        self._preview.setOpenExternalLinks(False)
        self._preview.setObjectName("report_preview")

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(left)
        self._splitter.addWidget(self._preview)
        self._splitter.setSizes([320, 760])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._splitter)

    # --- данные ----------------------------------------------------------

    def set_project_path(self, path: Path | str | None) -> None:
        """Открывает журнал проекта и заполняет список прогонов."""
        self._project = None if path is None else Path(path)
        self._close_store()
        if self._project is None:
            self._set_status("журнал не открыт")
            return
        database = default_log_path(self._project)
        if not database.exists():
            self._set_status(f"журнал не найден: {database}", error=True)
            return
        self._store = open_store(database)
        runs = self._store.list_runs(limit=50)
        self._run_combo.blockSignals(True)
        self._run_combo.clear()
        for run in runs:
            object_id = run.get("pipeline_id") or run.get("mission_id") or "—"
            label = f"{str(run['run_id'])[:8]} · {run['kind']} · {run['started_at']} · {object_id}"
            self._run_combo.addItem(label, str(run["run_id"]))
        self._run_combo.blockSignals(False)
        if not runs:
            self._set_status("журнал пуст: прогонов нет", error=True)
            return
        self.refresh()

    def _close_store(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None

    # --- настройки -------------------------------------------------------

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self._status_label.setText(text)
        self._status_label.setStyleSheet(_ERROR_STYLE if error else _MUTED_STYLE)

    def sections(self) -> list[str]:
        return [name for name, box in self._section_boxes.items() if box.isChecked()]

    def formats(self) -> list[str]:
        return [name for name, box in self._format_boxes.items() if box.isChecked()]

    def config(self) -> ReportConfig:
        """Конфигурация из виджетов: то же, что уходит в рендер."""
        return ReportConfig(
            sections=self.sections(),
            export=self.formats() or ["md"],
            thresholds={"k.p95": float(self._threshold.value())},
        )

    @property
    def last_seconds(self) -> float:
        """Время последней сборки предпросмотра, с."""
        return self._last_seconds

    @property
    def last_html(self) -> str | None:
        return self._last_html

    # --- рендер ----------------------------------------------------------

    def refresh(self) -> None:
        """Пересобирает предпросмотр по текущим настройкам."""
        if self._store is None:
            self._set_status("журнал не открыт", error=True)
            return
        run_id = self._run_combo.currentData()
        if not isinstance(run_id, str):
            self._set_status("прогон не выбран", error=True)
            return
        try:
            config = self.config()
            started = time.perf_counter()
            data = report_data_from_run(self._store, run_id)
            target = self._preview_dir()
            result = render_report(data, config, output_dir=target)
            self._last_seconds = time.perf_counter() - started
            self._last_html = result.html
            html_path = result.files.get("html")
            if html_path is not None:
                self._preview.setSource(QUrl.fromLocalFile(str(Path(html_path))))
            else:
                self._preview.setHtml(result.html)
            self._set_status(
                f"собрано за {self._last_seconds:.2f} с"
                f" · выводов {len(result.conclusions)}"
                f" · файлов {len(result.files)}"
            )
        except Exception as exc:  # ошибка данных не должна ронять окно
            self._set_status(f"отчёт не собрался: {exc}", error=True)

    def _preview_dir(self) -> Path:
        """Каталог предпросмотра: новый на сборку, чтобы Qt не показал кэш."""
        if self._temp is not None:
            shutil.rmtree(self._temp, ignore_errors=True)
        self._temp = Path(tempfile.mkdtemp(prefix="simorph-preview-"))
        return self._temp

    def export_report(self) -> Path | None:
        """Сохраняет отчёт в выбранный каталог."""
        if self._store is None:
            self._set_status("журнал не открыт", error=True)
            return None
        run_id = self._run_combo.currentData()
        if not isinstance(run_id, str):
            return None
        directory = QFileDialog.getExistingDirectory(self, "Каталог отчёта")
        if not directory:
            return None
        target = Path(directory)
        data = report_data_from_run(self._store, run_id)
        result = render_report(data, self.config(), output_dir=target)
        self._set_status(f"отчёт сохранён: {target} · файлов {len(result.files)}")
        return target

    # --- жизненный цикл --------------------------------------------------

    def dispose(self) -> None:
        """Закрывает журнал и удаляет каталог предпросмотра."""
        self._close_store()
        if self._temp is not None:
            shutil.rmtree(self._temp, ignore_errors=True)
            self._temp = None
        self._preview.clear()
        self._last_html = None
