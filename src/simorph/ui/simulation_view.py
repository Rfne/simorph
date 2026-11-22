"""Вкладка «Симуляция»: прогон миссии, метрики и события (T7-05, G7-01).

Вкладка не считает физику сама: она запускает тот же ``SimulationRunner``,
что и ``simorph sim run``, в фоновом потоке. Поэтому оконный режим не
блокирует интерфейс и даёт те же метрики, что headless-прогон.

Пока прогон идёт, панель состояния обновляется по таймеру: состояние
супервизора, время сцены и PC, такты, кадры, потери. После завершения
появляются интервалы план/факт, вклад ступеней и последние события.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from simorph.core.models.agent import AgentSection
from simorph.core.models.exercise import ExerciseSection, Mission
from simorph.core.models.project import ProjectDocument
from simorph.core.simulation import (
    RunReport,
    RunState,
    SimulationConfig,
    SimulationRunner,
)
from simorph.settings import ApplicationSettings
from simorph.ui.commands import DocumentStore

__all__ = ["SimulationView"]

#: Сколько последних событий показывать в панели.
_EVENT_LIMIT = 200

#: Период обновления панели во время прогона, мс.
_POLL_INTERVAL_MS = 100

#: Красный текст для ошибок.
_ERROR_STYLE = "color: #e5534b;"


class SimulationView(QWidget):
    """Прогон миссии: состояние, план/факт и журнал событий."""

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
        self._disposed = False
        self._runner: SimulationRunner | None = None
        self._thread: threading.Thread | None = None
        self._report: RunReport | None = None
        self._seen_events = 0
        self._finished = False
        self._error: str | None = None
        self.setObjectName("editor_simulation")

        self._exercise_combo = QComboBox()
        self._exercise_combo.currentIndexChanged.connect(self._on_exercise_changed)
        self._mission_combo = QComboBox()
        self._mission_combo.currentIndexChanged.connect(self._on_mission_changed)

        self._scale_spin = QDoubleSpinBox()
        self._scale_spin.setRange(0.05, 8.0)
        self._scale_spin.setSingleStep(0.25)
        self._scale_spin.setValue(1.0)
        self._scale_spin.setDecimals(2)
        self._scale_spin.setToolTip("Целевой масштаб времени: 2x — вдвое быстрее плана")

        self._duration_spin = QDoubleSpinBox()
        self._duration_spin.setRange(0.0, 3600.0)
        self._duration_spin.setSingleStep(1.0)
        self._duration_spin.setValue(0.0)
        self._duration_spin.setDecimals(1)
        self._duration_spin.setToolTip("0 — длительность по бюджету миссии")

        self._pace_box = QCheckBox("Выдерживать план")
        self._pace_box.setChecked(True)
        self._pace_box.setToolTip("Снять — прогон на максимальной скорости")

        self._start_button = QPushButton("Запустить")
        self._start_button.clicked.connect(lambda: self.start_run())
        self._pause_button = QPushButton("Пауза")
        self._pause_button.clicked.connect(self.toggle_pause)
        self._step_button = QPushButton("Шаг")
        self._step_button.clicked.connect(self.step_once)
        self._stop_button = QPushButton("Стоп")
        self._stop_button.clicked.connect(self.stop_run)
        for button in (self._pause_button, self._step_button, self._stop_button):
            button.setEnabled(False)

        self._state_label = QLabel("прогон не запущен")
        self._state_label.setWordWrap(True)
        self._state_label.setStyleSheet("font-weight: bold;")
        self._clock_label = QLabel("—")
        self._clock_label.setWordWrap(True)
        self._frames_label = QLabel("—")
        self._frames_label.setWordWrap(True)
        self._supervisor_label = QLabel("—")
        self._supervisor_label.setWordWrap(True)

        self._stage_table = QTableWidget(0, 4)
        self._stage_table.setHorizontalHeaderLabels(
            ["Ступень", "План, мс", "Факт, мс", "Перерасход, мс"]
        )
        self._stage_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._stage_table.setColumnWidth(0, 140)

        self._interval_table = QTableWidget(0, 6)
        self._interval_table.setHorizontalHeaderLabels(
            ["#", "Сцена, с", "PC, с", "k", "План, мс", "Факт, мс"]
        )
        self._interval_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._interval_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        for column in range(6):
            self._interval_table.setColumnWidth(column, 90)

        self._events = QListWidget()

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)

        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(self._build_toolbar())
        layout.addWidget(self._build_splitter(), 1)
        layout.addWidget(self._status_label)

        self._sync_combos()

    # --- построение -----------------------------------------------------

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("Упражнение:"))
        row.addWidget(self._exercise_combo)
        row.addWidget(QLabel("Миссия:"))
        row.addWidget(self._mission_combo)
        row.addWidget(QLabel("Масштаб:"))
        row.addWidget(self._scale_spin)
        row.addWidget(QLabel("Длительность, с:"))
        row.addWidget(self._duration_spin)
        row.addWidget(self._pace_box)
        row.addWidget(self._start_button)
        row.addWidget(self._pause_button)
        row.addWidget(self._step_button)
        row.addWidget(self._stop_button)
        row.addStretch(1)
        return row

    def _build_splitter(self) -> QSplitter:
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self._state_label)
        left_layout.addWidget(self._clock_label)
        left_layout.addWidget(self._frames_label)
        left_layout.addWidget(self._supervisor_label)
        stage_title = QLabel("Вклад ступеней")
        stage_title.setStyleSheet("font-weight: bold;")
        left_layout.addWidget(stage_title)
        left_layout.addWidget(self._stage_table, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        interval_title = QLabel("Интервалы план/факт")
        interval_title.setStyleSheet("font-weight: bold;")
        right_layout.addWidget(interval_title)
        right_layout.addWidget(self._interval_table, 1)
        events_title = QLabel("События")
        events_title.setStyleSheet("font-weight: bold;")
        right_layout.addWidget(events_title)
        right_layout.addWidget(self._events, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        return splitter

    # --- состояние ------------------------------------------------------

    @property
    def document(self) -> ProjectDocument:
        return self._document

    @property
    def runner(self) -> SimulationRunner | None:
        return self._runner

    @property
    def report(self) -> RunReport | None:
        return self._report

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def state_text(self) -> str:
        return self._state_label.text()

    @property
    def clock_text(self) -> str:
        return self._clock_label.text()

    @property
    def frames_text(self) -> str:
        return self._frames_label.text()

    @property
    def status_text(self) -> str:
        return self._status_label.text()

    @property
    def exercise_index(self) -> int:
        return self._exercise_index

    @property
    def mission_index(self) -> int:
        return self._mission_index

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

    def interval_rows(self) -> list[tuple[str, str, str, str, str, str]]:
        """Строки таблицы интервалов план/факт."""
        rows: list[tuple[str, str, str, str, str, str]] = []
        for row in range(self._interval_table.rowCount()):
            items = [self._interval_table.item(row, column) for column in range(6)]
            if any(item is None for item in items):
                continue
            rows.append(
                (
                    _text(items[0]),
                    _text(items[1]),
                    _text(items[2]),
                    _text(items[3]),
                    _text(items[4]),
                    _text(items[5]),
                )
            )
        return rows

    def stage_rows(self) -> list[tuple[str, str, str, str]]:
        """Строки таблицы вклада ступеней."""
        rows: list[tuple[str, str, str, str]] = []
        for row in range(self._stage_table.rowCount()):
            items = [self._stage_table.item(row, column) for column in range(4)]
            if any(item is None for item in items):
                continue
            rows.append((_text(items[0]), _text(items[1]), _text(items[2]), _text(items[3])))
        return rows

    def event_rows(self) -> list[str]:
        """Тексты событий в журнале."""
        return [self._events.item(index).text() for index in range(self._events.count())]

    # --- документ -------------------------------------------------------

    def rebuild(self, document: ProjectDocument) -> None:
        """Подхватывает новый документ, не сбрасывая выбранные индексы."""
        self._document = document
        self._sync_combos()

    def set_selection(self, exercise: int, mission: int) -> bool:
        """Переключает упражнение и миссию по индексам."""
        if not 0 <= exercise < len(self._document.exercises):
            return False
        missions = self._document.exercises[exercise].missions
        if not 0 <= mission < len(missions):
            return False
        self._exercise_index = exercise
        self._mission_index = mission
        self._sync_combos()
        return True

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

    def _on_exercise_changed(self, index: int) -> None:
        if index < 0 or self.running:
            return
        self._exercise_index = index
        self._mission_index = 0
        self._sync_combos()

    def _on_mission_changed(self, index: int) -> None:
        if index < 0 or self.running:
            return
        self._mission_index = index

    # --- прогон ---------------------------------------------------------

    def start_run(self, duration_s: float | None = None) -> bool:
        """Запускает прогон в фоновом потоке."""
        if self.running:
            self._set_status("прогон уже идёт", error=True)
            return False
        mission = self.mission
        if mission is None:
            self._set_status("в упражнении нет миссий", error=True)
            return False
        duration = duration_s if duration_s is not None else self._duration_spin.value()
        config = SimulationConfig(
            scale=self._scale_spin.value(),
            duration_s=duration if duration > 0.0 else None,
            pace=self._pace_box.isChecked(),
        )
        try:
            runner = SimulationRunner(mission, agent=self._agent(mission), config=config)
        except ValueError as exc:
            self._set_status(f"параметры прогона: {exc}", error=True)
            return False
        self._runner = runner
        self._report = None
        self._seen_events = 0
        self._finished = False
        self._error = None
        self._events.clear()
        self._interval_table.setRowCount(0)
        self._stage_table.setRowCount(0)
        self._state_label.setText("подготовка")
        self._set_status("прогон запущен")
        self._start_button.setEnabled(False)
        self._pause_button.setEnabled(True)
        self._pause_button.setText("Пауза")
        self._step_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        self._thread = threading.Thread(target=self._execute, name="simulation", daemon=True)
        self._thread.start()
        self._timer.start()
        return True

    def _execute(self) -> None:
        runner = self._runner
        if runner is None:
            return
        try:
            self._report = runner.run()
        except Exception as exc:  # поток не должен умирать молча (FR-SIM-14)
            self._error = str(exc)
        finally:
            runner.close()

    def toggle_pause(self) -> bool:
        """Пауза или продолжение; в паузе доступен шаг вперёд."""
        runner = self._runner
        if runner is None:
            return False
        if runner.state is RunState.RUNNING:
            if not runner.pause():
                return False
            self._pause_button.setText("Продолжить")
            self._step_button.setEnabled(True)
            self._set_status("пауза")
            self._poll()
            return True
        if runner.state is RunState.PAUSED:
            if not runner.resume():
                return False
            self._pause_button.setText("Пауза")
            self._step_button.setEnabled(False)
            self._set_status("продолжение")
            return True
        return False

    def step_once(self) -> bool:
        """Один такт вручную при паузе."""
        runner = self._runner
        if runner is None or not runner.step_once():
            return False
        self._poll()
        return True

    def stop_run(self) -> None:
        """Останавливает прогон: супервизор переходит в ``interrupted``."""
        runner = self._runner
        if runner is not None:
            runner.request_stop()
        self._set_status("остановка…")

    def wait(self, timeout_s: float = 10.0) -> bool:
        """Ждёт завершения фонового потока (для тестов и закрытия окна)."""
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout_s)
        finished = not thread.is_alive()
        if finished:
            self._timer.stop()
            self._poll()
        return finished

    def _poll(self) -> None:
        runner = self._runner
        if runner is None or self._disposed:
            return
        state = runner.state
        clock = runner.clock
        metrics = runner.metrics
        self._state_label.setText(f"состояние: {state}")
        self._clock_label.setText(
            f"сцена {clock.sim_time_s:.2f} с · PC {clock.wall_time_s:.2f} с"
            f" · тактов {clock.ticks} · k {clock.scale_factor():.2f}"
        )
        self._frames_label.setText(
            f"кадры: отправлено {metrics.frames_pushed}"
            f" · принято {metrics.frames_consumed}"
            f" · потерь {metrics.frames_dropped}"
        )
        supervisor = runner.supervisor
        self._supervisor_label.setText(
            f"элементов пройдено {len(supervisor.entered)} из {len(supervisor.windows)}"
            f" · очередь {supervisor.queue.depth} (пик {supervisor.queue.peak})"
        )
        self._append_events()
        if not self.running and not self._finished:
            self._timer.stop()
            self._finished = True
            self._finish()

    def _append_events(self) -> None:
        runner = self._runner
        if runner is None:
            return
        events = runner.events.events
        for event in events[self._seen_events :]:
            item = QListWidgetItem(f"{event.t_monotonic_ns} · {event.kind} · {event.message}")
            self._events.addItem(item)
        self._seen_events = len(events)
        while self._events.count() > _EVENT_LIMIT:
            self._events.takeItem(0)
        if self._events.count():
            self._events.scrollToBottom()

    def _finish(self) -> None:
        self._start_button.setEnabled(True)
        self._pause_button.setEnabled(False)
        self._pause_button.setText("Пауза")
        self._step_button.setEnabled(False)
        self._stop_button.setEnabled(False)
        report = self._report
        if report is None:
            reason = self._error or "прогон завершился без отчёта"
            self._set_status(reason, error=True)
            return
        self._fill_intervals(report)
        self._fill_stages(report)
        self._state_label.setText(f"состояние: {report.state}")
        self._clock_label.setText(
            f"сцена {report.clock.get('sim_time_s', 0.0):.2f} с"
            f" · PC {report.clock.get('wall_time_s', 0.0):.2f} с"
            f" · тактов {report.frames.get('attempted', 0)}"
            f" · k p50 {report.metrics.get('k', {}).get('p50', 1.0):.2f}"
            f", p95 {report.metrics.get('k', {}).get('p95', 1.0):.2f}"
        )
        self._frames_label.setText(
            f"кадры: отправлено {report.frames.get('pushed', 0)}"
            f" · принято {report.frames.get('consumed', 0)}"
            f" · потерь {report.frames.get('dropped', 0)}"
            f" ({report.loss_ratio * 100:.2f}%)"
        )
        self._supervisor_label.setText(
            f"событий {len(report.events)}"
            f" · интервалов {report.metrics.get('intervals', 0)}"
            f" · причина: {report.reason or '—'}"
        )
        if report.ok:
            self._set_status(report.summary())
        else:
            self._set_status(f"{report.state}: {report.reason or 'прогон не завершён'}", error=True)

    def _fill_intervals(self, report: RunReport) -> None:
        samples = report.metrics.get("samples", [])
        self._interval_table.setRowCount(len(samples))
        for row, sample in enumerate(samples):
            stages = sample.get("stages", {})
            plan = sum(float(item.get("plan_ms", 0.0)) for item in stages.values())
            actual = sum(float(item.get("actual_ms", 0.0)) for item in stages.values())
            values = (
                str(sample.get("index", row)),
                f"{float(sample.get('scene_time_s', 0.0)):.2f}",
                f"{float(sample.get('wall_time_s', 0.0)):.2f}",
                f"{float(sample.get('k', 1.0)):.2f}",
                f"{plan:.2f}",
                f"{actual:.2f}",
            )
            for column, text in enumerate(values):
                self._interval_table.setItem(row, column, QTableWidgetItem(text))

    def _fill_stages(self, report: RunReport) -> None:
        stages = report.metrics.get("stages", {})
        self._stage_table.setRowCount(len(stages))
        for row, (name, item) in enumerate(stages.items()):
            values = (
                name,
                f"{float(item.get('plan_ms', 0.0)):.2f}",
                f"{float(item.get('actual_ms', 0.0)):.2f}",
                f"{float(item.get('overrun_ms', 0.0)):.2f}",
            )
            for column, text in enumerate(values):
                self._stage_table.setItem(row, column, QTableWidgetItem(text))

    def _agent(self, mission: Mission) -> AgentSection | None:
        for agent in self._document.agents:
            if agent.agent_id == mission.agent_id:
                return agent
        return None

    # --- служебное ------------------------------------------------------

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self._status_label.setStyleSheet(_ERROR_STYLE if error else "")
        self._status_label.setText(text)

    def dispose(self) -> None:
        """Останавливает прогон и отключает таймер у закрытой вкладки."""
        self._disposed = True
        self._timer.stop()
        runner = self._runner
        if runner is not None:
            runner.request_stop()
        thread = self._thread
        if thread is not None:
            thread.join(1.0)


def _text(item: QTableWidgetItem | None) -> str:
    return "" if item is None else item.text()
