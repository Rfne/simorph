"""Вкладка «3D-сцена»: обзор мира, переключатели и снимок (T4-06…T4-08).

Вид рисует кадры, которые снимает ``Scene``: PyBullet в режиме ``DIRECT``
и программный рендерер. Qt-виджет показывает кадр, а не открывает второе
окно PyBullet, поэтому сцена работает и на машине без дисплея, и в CI.

Управление обзором: левая кнопка — орбита, средняя или правая — сдвиг цели,
колесо — зум. Пока пользователь двигает камеру, кадр считается в меньшем
разрешении: так обзор остаётся отзывчивым на большой сцене, а в покое
картинка рисуется в полном размере.

Фотореализм меняет только цвет материалов, колебания — только позу
предпросмотра. Ground truth снимается с сенсорной позы и от этих
переключателей не зависит (G4-04).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPaintEvent, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from simorph.core.models.project import ProjectDocument
from simorph.core.world import CameraSpec, OrbitCamera, Scene
from simorph.core.world.lighting import phase_title
from simorph.settings import ApplicationSettings

__all__ = ["SceneView"]

#: Интервал обновления вида, мс.
FRAME_INTERVAL_MS = 33

#: Разрешение кадра в покое и во время движения камеры.
#: Во время движения кадр меньше: программный TinyRenderer упирается
#: в заполнение пикселей, и 240×180 даёт 30 FPS при 1000 объектах (G4-05).
IDLE_SIZE = (640, 480)
INTERACTIVE_SIZE = (240, 180)

#: Задержка пересборки сцены после правки документа, мс.
REBUILD_DELAY_MS = 400

#: Сколько миллисекунд после движения камеры считать вид «в движении».
INTERACTION_HOLD_MS = 250

_ERROR_STYLE = "color: #e5534b;"


class SceneView(QWidget):
    """3D-вид сцены мира."""

    def __init__(
        self,
        document: ProjectDocument,
        settings: ApplicationSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._document = document
        self._scene = Scene(document.world, document.profile)
        self._camera = _initial_camera(document)
        self._interacting = False
        self._frame_ms = 0.0
        self._fps = 0.0
        self._last_mouse: QPoint | None = None
        self._mode = "orbit"
        self._disposed = False
        self.setObjectName("editor_scene")

        self._canvas = _Canvas(self)
        self._fps_label = QLabel("—")
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(self._build_toolbar())
        layout.addWidget(self._canvas, 1)
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

        stats = self._scene.build()
        self._update_status(stats)
        self._timer.start()

    # --- построение -----------------------------------------------------

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        reset_button = QPushButton("Сброс к seed")
        reset_button.setToolTip("Пересобрать сцену из того же seed")
        reset_button.clicked.connect(self.reset_scene)

        snapshot_button = QPushButton("Снимок…")
        snapshot_button.clicked.connect(lambda: self.save_snapshot())

        self._realism_box = QCheckBox("Фотореализм")
        self._realism_box.setChecked(True)
        self._realism_box.toggled.connect(self.set_realism)

        self._vibration_box = QCheckBox("Колебания")
        self._vibration_box.setChecked(True)
        self._vibration_box.toggled.connect(self.set_vibrations)

        self._precipitation_box = QComboBox()
        for code, title in (("none", "без осадков"), ("rain", "дождь"), ("snow", "снег")):
            self._precipitation_box.addItem(title, code)
        self._precipitation_box.currentIndexChanged.connect(self._on_precipitation)

        self._hour_box = QComboBox()
        for hour in (6, 9, 12, 15, 18, 21, 0):
            self._hour_box.addItem(f"{hour:02d}:00", hour)
        self._hour_box.setCurrentIndex(2)
        self._hour_box.currentIndexChanged.connect(self._on_hour)

        for widget in (
            reset_button,
            snapshot_button,
            self._realism_box,
            self._vibration_box,
            self._precipitation_box,
            self._hour_box,
        ):
            row.addWidget(widget)
        row.addStretch(1)
        return row

    # --- состояние ------------------------------------------------------

    @property
    def scene(self) -> Scene:
        return self._scene

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
    def fps_text(self) -> str:
        return self._fps_label.text()

    def tick(self) -> None:
        """Один шаг вида: время, кадр, статистика."""
        if self._disposed or not self.isVisible():
            return
        self._scene.advance(FRAME_INTERVAL_MS / 1000.0)
        started = time.perf_counter()
        self._render()
        elapsed = (time.perf_counter() - started) * 1000.0
        self._frame_ms = elapsed if self._frame_ms <= 0.0 else 0.8 * self._frame_ms + 0.2 * elapsed
        self._fps = 1000.0 / self._frame_ms if self._frame_ms > 0.0 else 0.0
        self._fps_label.setText(
            f"FPS {self._fps:.0f} · кадр {self._frame_ms:.0f} мс"
            f" · объектов {self._scene.stats.objects}"
            f" · {phase_title(self._scene.sun)}"
        )

    def render_once(self, width: int | None = None, height: int | None = None) -> QImage:
        """Снимает и возвращает кадр (для тестов и снимка)."""
        target = (width, height) if width and height else IDLE_SIZE
        spec = CameraSpec(width=target[0], height=target[1])
        frame = self._scene.render(self._camera, spec)
        return _to_image(frame.rgb, spec.width, spec.height)

    def _render(self) -> None:
        if self._scene.interrupted:
            self._canvas.set_message(f"сцена прервана: {self._scene.error or 'неизвестная ошибка'}")
            return
        size = INTERACTIVE_SIZE if self._interacting else IDLE_SIZE
        image = self.render_once(*size)
        image = self._with_precipitation(image)
        self._canvas.set_image(image)

    def _with_precipitation(self, image: QImage) -> QImage:
        """Рисует осадки поверх кадра: это слой вида, а не геометрия."""
        weather = self._scene.weather
        count = weather.particles_per_frame
        if count <= 0:
            return image
        painted = image.copy()
        painter = QPainter(painted)
        try:
            color = (
                QColor(180, 200, 235) if weather.precipitation == "rain" else QColor(240, 245, 255)
            )
            painter.setPen(QPen(color, 1))
            width, height = painted.width(), painted.height()
            time_s = self._scene.time
            for index in range(count):
                x = int(
                    (index * 97 + time_s * (220 if weather.precipitation == "rain" else 60)) % width
                )
                y = int(
                    (index * 53 + time_s * (620 if weather.precipitation == "rain" else 140))
                    % height
                )
                length = 10 if weather.precipitation == "rain" else 3
                painter.drawLine(x, y, x, min(height - 1, y + length))
        finally:
            painter.end()
        return painted

    def _update_status(self, stats: Any) -> None:
        parts = [
            f"мир {self._document.world.world_id}",
            f"seed {self._document.world.seed}",
            f"объектов {stats.objects}",
            f"сборка {stats.build_seconds * 1000:.0f} мс",
        ]
        if stats.truncated:
            parts.append(f"лимит {stats.limit}: пропущено {stats.truncated}")
        if stats.status != "ready":
            parts.append(f"сцена прервана: {stats.error}")
            self._status_label.setStyleSheet(_ERROR_STYLE)
        else:
            self._status_label.setStyleSheet("")
        self._status_label.setText(" · ".join(parts))

    # --- документ -------------------------------------------------------

    def rebuild(self, document: ProjectDocument) -> None:
        """Откладывает пересборку: правка в инспекторе не должна тормозить."""
        self._document = document
        self._rebuild_timer.start()

    def _rebuild_now(self) -> None:
        if self._disposed:
            return
        stats = self._scene.rebuild(self._document.world, self._document.profile)
        self._camera = _initial_camera(self._document)
        self._update_status(stats)
        self._render()

    def reset_scene(self) -> None:
        """Сброс к seed без перезапуска приложения."""
        self._camera = _initial_camera(self._document)
        stats = self._scene.reset()
        self._update_status(stats)
        self._render()

    # --- переключатели --------------------------------------------------

    def set_realism(self, enabled: bool) -> None:
        self._realism_box.setChecked(bool(enabled))
        self._scene.set_realism(bool(enabled))

    def set_vibrations(self, enabled: bool) -> None:
        self._vibration_box.setChecked(bool(enabled))
        self._scene.set_vibrations(bool(enabled))

    def set_precipitation(self, kind: str) -> None:
        index = self._precipitation_box.findData(kind)
        if index >= 0:
            self._precipitation_box.setCurrentIndex(index)
        self._scene.set_precipitation(kind)  # type: ignore[arg-type]

    def set_hour(self, hour: int) -> None:
        index = self._hour_box.findData(hour)
        if index >= 0:
            self._hour_box.setCurrentIndex(index)
        self._scene.set_hour(float(hour))

    def _on_precipitation(self, _index: int) -> None:
        kind = self._precipitation_box.currentData()
        if isinstance(kind, str):
            self._scene.set_precipitation(kind)  # type: ignore[arg-type]

    def _on_hour(self, _index: int) -> None:
        hour = self._hour_box.currentData()
        if isinstance(hour, int):
            self._scene.set_hour(float(hour))

    # --- снимок ---------------------------------------------------------

    def save_snapshot(self, path: Path | None = None) -> bool:
        """Сохраняет кадр и JSON с камерой."""
        target = path
        if target is None:
            suggested = (self._settings.last_dir or Path.cwd()) / "scene.png"
            chosen = QFileDialog.getSaveFileName(
                self,
                "Снимок сцены",
                str(suggested),
                "Изображение (PNG);;PNG (*.png)",
            )
            if not chosen or not chosen[0]:
                return False
            target = Path(chosen[0])
        try:
            image, meta = self._scene.snapshot(target, self._camera)
        except Exception as exc:  # снимок не должен ронять окно
            QMessageBox.warning(self, "Снимок не сохранён", str(exc))
            self._status_label.setStyleSheet(_ERROR_STYLE)
            self._status_label.setText(f"снимок не сохранён: {exc}")
            return False
        self._settings.last_dir = target.parent
        self._status_label.setStyleSheet("")
        self._status_label.setText(f"снимок: {image.name} · описание: {meta.name}")
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
        self._scene.close()

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - переопределение Qt
        self.dispose()
        super().closeEvent(event)


class _Canvas(QWidget):
    """Область кадра: масштабирует изображение и показывает сообщения."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self._message = ""
        self.setMinimumSize(320, 240)
        self.setAutoFillBackground(True)

    def set_image(self, image: QImage) -> None:
        self._image = image
        self._message = ""
        self.update()

    def set_message(self, message: str) -> None:
        self._image = None
        self._message = message
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - переопределение Qt
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), self.palette().window())
            if self._image is not None:
                scaled = self._image.scaled(
                    self.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                left = (self.width() - scaled.width()) // 2
                top = (self.height() - scaled.height()) // 2
                painter.drawImage(left, top, scaled)
            elif self._message:
                painter.setPen(QColor("#e5534b"))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
        finally:
            painter.end()


def _to_image(rgb: Any, width: int, height: int) -> QImage:
    """QImage из RGB-буфера кадра."""
    data = rgb.tobytes() if hasattr(rgb, "tobytes") else bytes(rgb)
    image = QImage(data, width, height, width * 3, QImage.Format.Format_RGB888)
    return image.copy()


def _initial_camera(document: ProjectDocument) -> OrbitCamera:
    """Камера по умолчанию: обзор всего участка под углом."""
    size_x, size_y = document.world.terrain.size_m
    elevation_low, elevation_high = document.world.terrain.elevation_m
    distance = max(60.0, max(float(size_x), float(size_y)) * 0.35)
    return OrbitCamera(
        target=(0.0, 0.0, (float(elevation_low) + float(elevation_high)) / 2.0),
        distance=distance,
        yaw_deg=45.0,
        pitch_deg=-38.0,
    )
