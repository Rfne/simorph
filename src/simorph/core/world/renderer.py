"""Рендер сцены через PyBullet в режиме ``DIRECT`` (T4-01, T4-05).

Сцена не открывает окно PyBullet: соединение ``DIRECT`` работает без
дисплея и в CI, а кадр снимается программным рендерером TinyRenderer.
Отсюда три следствия:

- один и тот же код даёт кадр и на машине разработчика, и в headless-прогоне;
- глубина и маска объекта берутся из одного буфера, поэтому маска всегда
  согласована с геометрией;
- приложение не зависит от драйвера OpenGL и не падает без GPU.

Реализм — это цвет материала и яркость света; выключение реализма не меняет
ни одного тела, поэтому ground truth остаётся прежним (G4-04).
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np

from simorph.core.errors import SimorphError
from simorph.core.world.camera import CameraPose, CameraSpec
from simorph.core.world.layout import SceneObject
from simorph.core.world.lighting import SunState

__all__ = ["BulletRenderer", "Frame", "RenderError"]

#: Флаг «сегментация по объекту», а не по звену.
_SEGMENTATION_FLAGS: Final = 0

#: Загруженный модуль PyBullet: импорт печатает баннер в stdout, поэтому он
#: выполняется один раз и с подавлением нативного вывода.
_bullet_module: Any = None


@contextlib.contextmanager
def _silent_native_stdout() -> Any:
    """Прячет баннер PyBullet: он пишется в файловые дескрипторы, не в ``sys.stdout``.

    Баннер уходит то в stdout, то в stderr в зависимости от сборки PyBullet,
    поэтому глушатся оба потока — иначе он попадает в лог и ломает разбор
    вывода CLI.
    """
    saved: list[tuple[int, int]] = []
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        for descriptor in (1, 2):
            try:
                saved.append((descriptor, os.dup(descriptor)))
                os.dup2(devnull, descriptor)
            except OSError:  # pragma: no cover - платформа без файловых дескрипторов
                continue
        yield
    finally:
        for descriptor, duplicate in saved:
            os.dup2(duplicate, descriptor)
            os.close(duplicate)
        os.close(devnull)


def _bullet() -> Any:
    """Ленивый импорт PyBullet без баннера сборки в stdout."""
    global _bullet_module
    if _bullet_module is None:
        with _silent_native_stdout():
            import pybullet as module

            _bullet_module = module
    return _bullet_module


class RenderError(SimorphError):
    """Сцена не может быть отрисована."""


@dataclass(slots=True)
class Frame:
    """Кадр: цвет, глубина и маска."""

    rgb: np.ndarray
    depth: np.ndarray
    mask: np.ndarray
    spec: CameraSpec
    body_labels: dict[int, str] = field(default_factory=dict)

    @property
    def width(self) -> int:
        return int(self.rgb.shape[1])

    @property
    def height(self) -> int:
        return int(self.rgb.shape[0])

    def image_bytes(self) -> bytes:
        """RGB-буфер для ``QImage`` и PNG."""
        return self.rgb.tobytes()

    def depth_range(self) -> tuple[float, float]:
        return float(np.min(self.depth)), float(np.max(self.depth))

    def label_at(self, u: int, v: int) -> str | None:
        return self.body_labels.get(int(self.mask[int(v), int(u)]))


class BulletRenderer:
    """Обёртка над PyBullet: тела сцены, свет и кадры."""

    def __init__(self, client_id: int | None = None) -> None:
        self._client = client_id
        self._owned = client_id is None
        self._bodies: dict[int, SceneObject] = {}
        self._colors: dict[int, tuple[float, float, float, float]] = {}
        self._sun = SunState(135.0, 40.0, 0.0)
        self._realism = True

    # --- жизненный цикл -------------------------------------------------

    @property
    def client(self) -> int:
        if self._client is None:
            self._client = self._connect()
        return self._client

    def _connect(self) -> int:
        try:
            bullet = _bullet()
        except ImportError as exc:  # pragma: no cover - окружение без PyBullet
            raise RenderError("PyBullet не установлен: сцена недоступна") from exc
        return int(bullet.connect(bullet.DIRECT))

    def close(self) -> None:
        """Отключается, если соединение создано этим объектом."""
        if self._client is None or not self._owned:
            return
        try:
            bullet = _bullet()

            bullet.disconnect(self._client)
        except Exception:  # pragma: no cover - соединение уже закрыто
            pass
        finally:
            self._client = None
            self._bodies.clear()
            self._colors.clear()

    # --- тела -----------------------------------------------------------

    def clear(self) -> None:
        """Удаляет все тела сцены."""
        bullet = _bullet()

        for body in list(self._bodies):
            with contextlib.suppress(Exception):
                bullet.removeBody(body, physicsClientId=self.client)
        self._bodies.clear()
        self._colors.clear()

    @property
    def body_count(self) -> int:
        return len(self._bodies)

    def add_object(
        self, item: SceneObject, color: tuple[float, float, float, float] | None = None
    ) -> int:
        """Добавляет примитив и возвращает идентификатор тела."""
        bullet = _bullet()

        rgba = list(color if color is not None else item.color)
        try:
            shape = self._create_shape(item, rgba)
            body = int(
                bullet.createMultiBody(
                    baseMass=0.0,
                    baseCollisionShapeIndex=-1,
                    baseVisualShapeIndex=shape,
                    basePosition=list(item.pose),
                    physicsClientId=self.client,
                )
            )
        except Exception as exc:
            raise RenderError(f"объект {item.id!r} не построен: {exc}") from exc
        self._bodies[body] = item
        self._colors[body] = (rgba[0], rgba[1], rgba[2], rgba[3])
        return body

    def _create_shape(self, item: SceneObject, rgba: list[float]) -> int:
        bullet = _bullet()

        if item.shape == "box":
            return int(
                bullet.createVisualShape(
                    bullet.GEOM_BOX,
                    halfExtents=[
                        max(item.size[0], 0.01) / 2.0,
                        max(item.size[1], 0.01) / 2.0,
                        max(item.size[2], 0.01) / 2.0,
                    ],
                    rgbaColor=rgba,
                    physicsClientId=self.client,
                )
            )
        if item.shape == "cylinder":
            return int(
                bullet.createVisualShape(
                    bullet.GEOM_CYLINDER,
                    radius=max(item.size[0], 0.01) / 2.0,
                    length=max(item.size[2], 0.01),
                    rgbaColor=rgba,
                    physicsClientId=self.client,
                )
            )
        return int(
            bullet.createVisualShape(
                bullet.GEOM_SPHERE,
                radius=max(item.size[0], max(item.size[1], 0.01)) / 2.0,
                rgbaColor=rgba,
                physicsClientId=self.client,
            )
        )

    # --- свет и реализм -------------------------------------------------

    def set_lighting(self, sun: SunState, *, realism: bool = True) -> None:
        """Перекрашивает тела под текущее солнце.

        Тени в TinyRenderer не считаются, поэтому «реализм» — это яркость
        и оттенок материала. Геометрия не меняется, значит GT стабилен.
        """
        bullet = _bullet()

        self._sun = sun
        self._realism = realism
        for body, item in self._bodies.items():
            base = self._colors.get(body, item.color)
            rgba = sun.shade(base, realism=realism)
            try:
                bullet.changeVisualShape(
                    body,
                    -1,
                    rgbaColor=list(rgba),
                    physicsClientId=self.client,
                )
            except Exception:  # pragma: no cover - тело могло быть удалено
                continue

    @property
    def realism(self) -> bool:
        return self._realism

    @property
    def sun(self) -> SunState:
        return self._sun

    # --- кадры ----------------------------------------------------------

    def render(self, pose: CameraPose, spec: CameraSpec) -> Frame:
        """Снимает кадр: цвет, глубина в метрах и маска тел."""
        bullet = _bullet()

        view = bullet.computeViewMatrix(
            cameraEyePosition=list(pose.eye),
            cameraTargetPosition=list(pose.target),
            cameraUpVector=list(pose.up),
        )
        projection = bullet.computeProjectionMatrixFOV(
            fov=spec.fov_deg,
            aspect=spec.aspect,
            nearVal=spec.near,
            farVal=spec.far,
        )
        try:
            _w, _h, rgb, depth, mask = bullet.getCameraImage(
                width=spec.width,
                height=spec.height,
                viewMatrix=view,
                projectionMatrix=projection,
                renderer=bullet.ER_TINY_RENDERER,
                flags=_SEGMENTATION_FLAGS,
                physicsClientId=self.client,
            )
        except Exception as exc:
            raise RenderError(f"кадр не отрисован: {exc}") from exc

        colors = np.asarray(rgb, dtype=np.uint8).reshape(spec.height, spec.width, 4)[:, :, :3]
        buffer = np.asarray(depth, dtype=np.float64).reshape(spec.height, spec.width)
        labels = np.asarray(mask, dtype=np.int32).reshape(spec.height, spec.width)
        meters = spec.far * spec.near / (spec.far - (spec.far - spec.near) * buffer)
        return Frame(
            rgb=np.ascontiguousarray(colors),
            depth=np.ascontiguousarray(meters),
            mask=np.ascontiguousarray(labels),
            spec=spec,
            body_labels={body: item.id for body, item in self._bodies.items()},
        )

    def render_stereo(self, pose: CameraPose, spec: CameraSpec) -> tuple[Frame, Frame]:
        """Пара кадров стереопары: слева и справа."""
        from simorph.core.world.camera import stereo_offset

        left, right = stereo_offset(pose, spec.baseline_m)
        return self.render(left, spec), self.render(right, spec)

    # --- служебное ------------------------------------------------------

    def body_of(self, label: str) -> int | None:
        """Идентификатор тела по идентификатору объекта."""
        for body, item in self._bodies.items():
            if item.id == label:
                return body
        return None

    def stats(self) -> dict[str, Any]:
        return {
            "bodies": len(self._bodies),
            "realism": self._realism,
            "sun": {
                "azimuth_deg": self._sun.azimuth_deg,
                "elevation_deg": self._sun.elevation_deg,
                "phase": self._sun.phase,
            },
        }

    def __enter__(self) -> BulletRenderer:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
