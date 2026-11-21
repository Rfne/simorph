"""Вкладка «Агент»: дерево конфигурации и 3D-предпросмотр (T5-04, T5-06).

Слева — состав агента: звенья, сенсоры, актуаторы и бортовые константы
``P_hw`` по группам ``TC-*``. Справа — предпросмотр без фотореализма:
орбита, панорамирование, зум, зоны обслуживания и подсветка коллизий.

Предпросмотр пересобирается после правки документа с задержкой: набор
текста в инспекторе не должен перестраивать сцену на каждый символ.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QImage, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from simorph.core.agent import (
    AgentPreview,
    build_tree,
    check_agent,
    hw_payload,
    initial_camera,
)
from simorph.core.models.agent import AgentSection
from simorph.core.models.project import ProjectDocument
from simorph.core.world import CameraSpec, OrbitCamera
from simorph.settings import ApplicationSettings
from simorph.ui.scene_view import _Canvas, _to_image

__all__ = ["AgentView"]

#: Интервал обновления вида, мс.
FRAME_INTERVAL_MS = 33

#: Разрешение кадра в покое и во время движения камеры.
IDLE_SIZE = (640, 480)
INTERACTIVE_SIZE = (320, 240)

#: Задержка пересборки после правки документа, мс.
REBUILD_DELAY_MS = 300

#: Сколько миллисекунд после движения камеры считать вид «в движении».
INTERACTION_HOLD_MS = 250

_ERROR_STYLE = "color: #e5534b;"


class AgentView(QWidget):
    """3D-предпросмотр агента и его состав."""

    def __init__(
        self,
        document: ProjectDocument,
        settings: ApplicationSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._document = document
        self._agent = _first_agent(document)
        self._preview = AgentPreview(self._agent) if self._agent is not None else None
        self._camera = initial_camera(self._agent) if self._agent is not None else OrbitCamera()
        self._interacting = False
        self._frame_ms = 0.0
        self._fps = 0.0
        self._last_mouse: QPoint | None = None
        self._mode = "orbit"
        self._disposed = False
        self.setObjectName("editor_agent")

        self._canvas = _Canvas(self)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Параметр", "Значение"])
        self._tree.setColumnWidth(0, 220)
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._fps_label = QLabel("—")

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._tree)
        splitter.addWidget(self._canvas)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 640])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(self._build_toolbar())
        layout.addWidget(splitter, 1)
        layout.addWidget(self._status_label)
        layout.addWidget(self._fps_label)

        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_INTERVAL_MS)
        self._timer.timeout.connect(self.tick)

        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(REBUILD_DELAY_MS)
        self._rebuild_timer.timeout.connect(self._rebuild_now)

        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(INTERACTION_HOLD_MS)
        self._hold_timer.timeout.connect(self._stop_interaction)

        self._rebuild_now()
        self._timer.start()

    # --- построение -----------------------------------------------------

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        reset_button = QPushButton("Сброс вида")
        reset_button.clicked.connect(self.reset_view)
        export_button = QPushButton("Экспорт URDF…")
        export_button.clicked.connect(lambda: self.save_export())
        self._zones_box = QCheckBox("Зоны")
        self._zones_box.setChecked(True)
        self._zones_box.toggled.connect(self.set_zones)
        for widget in (reset_button, export_button, self._zones_box):
            row.addWidget(widget)
        row.addStretch(1)
        return row

    # --- состояние ------------------------------------------------------

    @property
    def preview(self) -> AgentPreview | None:
        return self._preview

    @property
    def camera(self) -> OrbitCamera:
        return self._camera

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def frame_ms(self) -> float:
        return self._frame_ms

    @property
    def status_text(self) -> str:
        return self._status_label.text()

    @property
    def agent(self) -> AgentSection | None:
        return self._agent

    def tick(self) -> None:
        """Один шаг вида: кадр и статистика."""
        if self._disposed or not self.isVisible():
            return
        started = time.perf_counter()
        self._render()
        elapsed = (time.perf_counter() - started) * 1000.0
        self._frame_ms = elapsed if self._frame_ms <= 0.0 else 0.8 * self._frame_ms + 0.2 * elapsed
        self._fps = 1000.0 / self._frame_ms if self._frame_ms > 0.0 else 0.0
        if self._preview is not None:
            self._fps_label.setText(
                f"FPS {self._fps:.0f} · кадр {self._frame_ms:.0f} мс"
                f" · звеньев {self._preview.stats.links}"
                f" · коллизий {len(self._preview.stats.collisions)}"
            )

    def render_once(self, width: int | None = None, height: int | None = None) -> QImage:
        """Снимает и возвращает кадр (для тестов и снимка)."""
        if self._preview is None:
            return QImage()
        target = (width, height) if width and height else IDLE_SIZE
        spec = CameraSpec(width=target[0], height=target[1])
        frame = self._preview.render(self._camera, spec)
        return _to_image(frame.rgb, spec.width, spec.height)

    def _render(self) -> None:
        if self._preview is None:
            self._canvas.set_message("в проекте нет агентов")
            return
        if self._preview.stats.status != "ready":
            self._canvas.set_message(
                f"предпросмотр прерван: {self._preview.stats.error or 'неизвестная ошибка'}"
            )
            return
        size = INTERACTIVE_SIZE if self._interacting else IDLE_SIZE
        self._canvas.set_image(self.render_once(*size))

    # --- документ -------------------------------------------------------

    def rebuild(self, document: ProjectDocument) -> None:
        """Откладывает пересборку после правки документа."""
        self._document = document
        self._rebuild_timer.start()

    def _rebuild_now(self) -> None:
        if self._disposed:
            return
        agent = _first_agent(self._document)
        self._agent = agent
        if agent is None:
            if self._preview is not None:
                self._preview.close()
                self._preview = None
            self._tree.clear()
            self._update_status(None)
            self._render()
            return
        if self._preview is None:
            self._preview = AgentPreview(agent)
        self._camera = initial_camera(agent)
        stats = self._preview.rebuild(agent, zones=self._zones_box.isChecked())
        self._fill_tree(agent)
        self._update_status(stats)
        self._render()

    def reset_view(self) -> None:
        """Возвращает камеру к обзору агента целиком."""
        if self._agent is not None:
            self._camera = initial_camera(self._agent)
        self._render()

    def set_zones(self, enabled: bool) -> None:
        self._zones_box.setChecked(bool(enabled))
        if self._preview is not None:
            self._preview.set_zones(bool(enabled))
            self._update_status(self._preview.stats)

    def _fill_tree(self, agent: AgentSection) -> None:
        """Состав агента: звенья, сенсоры, актуаторы и константы."""
        self._tree.clear()
        tree = build_tree(agent)
        links = QTreeWidgetItem([f"Звенья ({len(agent.links)})", f"корень {tree.root or '—'}"])
        for link in agent.links:
            links.addChild(
                QTreeWidgetItem(
                    [link.id, f"{link.type}, {link.mass_kg:g} кг, {_size_text(link.size_m)}"]
                )
            )
        self._tree.addTopLevelItem(links)

        sensors = QTreeWidgetItem([f"Сенсоры ({len(agent.sensors)})", ""])
        for sensor in agent.sensors:
            sensors.addChild(
                QTreeWidgetItem(
                    [sensor.id, f"{sensor.type}, {sensor.rate_hz:g} Гц, звено {sensor.link or '—'}"]
                )
            )
        self._tree.addTopLevelItem(sensors)

        actuators = QTreeWidgetItem([f"Актуаторы ({len(agent.actuators)})", ""])
        for actuator in agent.actuators:
            actuators.addChild(QTreeWidgetItem([actuator.id, actuator.type]))
        self._tree.addTopLevelItem(actuators)

        payload = hw_payload(agent)
        constants = QTreeWidgetItem([f"P_hw ({len(payload)})", "TC-DYN/SEN/CMP/ENE/COM"])
        groups: dict[str, QTreeWidgetItem] = {}
        for item in payload:
            group = str(item["group"])
            node = groups.get(group)
            if node is None:
                node = QTreeWidgetItem([group, ""])
                groups[group] = node
                constants.addChild(node)
            value = item["value"]
            text = (
                " ".join(f"{part:g}" for part in value) if isinstance(value, list) else f"{value:g}"
            )
            node.addChild(
                QTreeWidgetItem([f"{item['id']} {item['title']}", f"{text} {item['unit']}"])
            )
        self._tree.addTopLevelItem(constants)
        self._tree.expandToDepth(0)

    def _update_status(self, stats: Any) -> None:
        if self._agent is None:
            self._status_label.setStyleSheet(_ERROR_STYLE)
            self._status_label.setText("в проекте нет агентов")
            return
        issues = check_agent(self._agent)
        errors = sum(1 for item in issues if item.severity == "error")
        parts = [
            f"агент {self._agent.agent_id} ({self._agent.role})",
            f"звеньев {len(self._agent.links)}",
            f"сенсоров {len(self._agent.sensors)}",
            f"актуаторов {len(self._agent.actuators)}",
        ]
        if stats is not None:
            parts.append(f"сборка {stats.build_ms:.0f} мс")
            if stats.collisions:
                parts.append(f"коллизий {len(stats.collisions)}")
        parts.append(f"ошибок {errors}")
        self._status_label.setStyleSheet(_ERROR_STYLE if errors else "")
        self._status_label.setText(" · ".join(parts))

    # --- экспорт --------------------------------------------------------

    def save_export(self, path: Path | None = None) -> bool:
        """Сохраняет URDF-подобный файл агента."""
        if self._agent is None:
            return False
        target = path
        if target is None:
            suggested = (self._settings.last_dir or Path.cwd()) / f"{self._agent.agent_id}.urdf"
            chosen = QFileDialog.getSaveFileName(
                self,
                "Экспорт агента",
                str(suggested),
                "URDF-подобный файл (*.urdf *.xml);;Все файлы (*)",
            )
            if not chosen or not chosen[0]:
                return False
            target = Path(chosen[0])
        from simorph.core.agent import write_urdf

        try:
            written = write_urdf(self._agent, target)
        except OSError as exc:  # экспорт не должен ронять окно
            QMessageBox.warning(self, "Экспорт не выполнен", str(exc))
            return False
        self._settings.last_dir = target.parent
        self._status_label.setStyleSheet("")
        self._status_label.setText(f"экспорт: {written.name}")
        return True

    # --- мышь -----------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - переопределение Qt
        self._last_mouse = event.position().toPoint()
        self._mode = "orbit" if event.button() == Qt.MouseButton.LeftButton else "pan"
        self._interacting = True
        self._hold_timer.start()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - переопределение Qt
        if self._last_mouse is None:
            return
        point = event.position().toPoint()
        delta = point - self._last_mouse
        self._last_mouse = point
        if self._mode == "orbit":
            self._camera.orbit(delta.x() * 0.4, delta.y() * 0.4)
        else:
            self._camera.pan(delta.x(), delta.y())
        self._interacting = True
        self._hold_timer.start()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - переопределение Qt
        self._last_mouse = None
        self._hold_timer.start()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - переопределение Qt
        steps = event.angleDelta().y() / 120.0
        self._camera.zoom(0.85**steps)
        self._interacting = True
        self._hold_timer.start()

    def _stop_interaction(self) -> None:
        self._interacting = False

    # --- жизненный цикл -------------------------------------------------

    def dispose(self) -> None:
        """Останавливает таймеры и освобождает соединение PyBullet."""
        self._disposed = True
        self._timer.stop()
        self._rebuild_timer.stop()
        self._hold_timer.stop()
        if self._preview is not None:
            self._preview.close()

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - переопределение Qt
        self.dispose()
        super().closeEvent(event)


def _first_agent(document: ProjectDocument) -> AgentSection | None:
    return document.agents[0] if document.agents else None


def _size_text(size: tuple[float, float, float]) -> str:
    return "×".join(f"{value:g}" for value in size) + " м"
