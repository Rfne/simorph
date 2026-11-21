"""Сцена мира: сборка, сброс, кадры и ground truth (T4-01…T4-09).

``Scene`` связывает раскладку, рендер и погоду. Правила этапа:

- сборка не бросает исключений наружу: сбой помечает сцену ``interrupted``,
  и приложение продолжает работать (G4-07);
- ``reset`` пересобирает раскладку из того же seed и профиля, поэтому
  повторный сброс даёт идентичную сцену (G4-06);
- фотореализм и колебания влияют только на предпросмотр, а ground truth
  снимается с сенсорной позы без вибрации (G4-04);
- лимит объектов проверяется в раскладке, сцена строится и при превышении.

Сцена — не глобальный объект: её создаёт интерфейс или CLI, а закрывает
владелец. Соединение PyBullet живёт внутри рендера и освобождается
в ``close``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from simorph.core.issues import IssueCode, Severity, issue
from simorph.core.models.profile import ProfileSection
from simorph.core.models.world import WorldSection
from simorph.core.world.camera import CameraPose, CameraSpec, OrbitCamera
from simorph.core.world.ground_truth import GroundTruth, bbox_corners
from simorph.core.world.layout import MAX_OBJECTS, SceneLayout, build_layout
from simorph.core.world.lighting import SunState, lighting_for_hour, sun_state
from simorph.core.world.renderer import BulletRenderer, Frame
from simorph.core.world.snapshot import save_snapshot
from simorph.core.world.weather import Precipitation, WeatherState, weather_state

__all__ = ["Scene", "SceneStats", "SceneStatus"]

#: Состояние сцены: готова или прервана сбоем.
SceneStatus = Literal["ready", "interrupted"]


@dataclass(slots=True)
class SceneStats:
    """Статистика сцены для интерфейса, CLI и журнала."""

    objects: int = 0
    bodies: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    limit: int = MAX_OBJECTS
    truncated: int = 0
    warnings: int = 0
    build_seconds: float = 0.0
    status: SceneStatus = "ready"
    error: str | None = None

    @property
    def over_limit(self) -> bool:
        return self.truncated > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "error": self.error,
            "objects": self.objects,
            "bodies": self.bodies,
            "by_category": dict(self.by_category),
            "limit": self.limit,
            "truncated": self.truncated,
            "warnings": self.warnings,
            "build_seconds": round(self.build_seconds, 4),
        }


class Scene:
    """Сцена мира: раскладка, тела PyBullet, свет и погода."""

    def __init__(
        self,
        world: WorldSection,
        profile: ProfileSection | None = None,
        *,
        limit: int = MAX_OBJECTS,
        renderer: BulletRenderer | None = None,
        layout: SceneLayout | None = None,
    ) -> None:
        self._world = world
        self._profile = profile
        self._limit = limit
        self._renderer = renderer if renderer is not None else BulletRenderer()
        self._owns_renderer = renderer is None
        self._layout = layout
        self._status: SceneStatus = "ready"
        self._error: str | None = None
        self._build_seconds = 0.0
        self._realism = True
        self._vibrations = True
        self._hour: float | None = None
        self._sun = sun_state(world.lighting)
        self._weather = weather_state(world, vibrations=True)
        self._time = 0.0

    # --- сборка ---------------------------------------------------------

    def build(self) -> SceneStats:
        """Строит тела сцены; сбой не выходит наружу."""
        started = time.perf_counter()
        self._status = "ready"
        self._error = None
        if self._layout is None:
            self._layout = build_layout(self._world, self._profile, limit=self._limit)
        try:
            self._renderer.clear()
            failures: list[str] = []
            for item in self._layout.objects:
                try:
                    self._renderer.add_object(item)
                except Exception as exc:  # один объект не должен срывать сцену
                    failures.append(f"{item.id}: {exc}")
            self._renderer.set_lighting(self._sun, realism=self._realism)
        except Exception as exc:  # сцена не должна ронять приложение (G4-07)
            self._status = "interrupted"
            self._error = str(exc)
        else:
            if failures and self._renderer.body_count == 0:
                self._status = "interrupted"
                self._error = failures[0]
            elif failures:
                for text in failures[:5]:
                    self._layout.warnings.append(
                        issue(
                            IssueCode.INVALID_VALUE,
                            "world",
                            f"объект не построен ({text})",
                            severity=Severity.WARNING,
                        )
                    )
        self._build_seconds = time.perf_counter() - started
        return self.stats

    def rebuild(self, world: WorldSection, profile: ProfileSection | None = None) -> SceneStats:
        """Пересобирает сцену под новый документ (правка проекта в UI)."""
        self._world = world
        self._profile = profile
        self._layout = None
        self._hour = None
        self._sun = sun_state(world.lighting)
        self._weather = weather_state(world, vibrations=self._vibrations)
        return self.build()

    def reset(self) -> SceneStats:
        """Сбрасывает сцену к seed: раскладка строится заново."""
        self._layout = None
        self._time = 0.0
        return self.build()

    # --- состояние ------------------------------------------------------

    @property
    def layout(self) -> SceneLayout:
        if self._layout is None:
            self._layout = build_layout(self._world, self._profile, limit=self._limit)
        return self._layout

    @property
    def stats(self) -> SceneStats:
        layout = self.layout
        return SceneStats(
            objects=len(layout.objects),
            bodies=self._renderer.body_count,
            by_category=layout.stats(),
            limit=layout.limit,
            truncated=layout.truncated,
            warnings=len(layout.warnings),
            build_seconds=self._build_seconds,
            status=self._status,
            error=self._error,
        )

    @property
    def status(self) -> SceneStatus:
        return self._status

    @property
    def interrupted(self) -> bool:
        return self._status == "interrupted"

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def sun(self) -> SunState:
        return self._sun

    @property
    def weather(self) -> WeatherState:
        return self._weather

    @property
    def realism(self) -> bool:
        return self._realism

    # --- переключатели --------------------------------------------------

    def set_realism(self, enabled: bool) -> None:
        """Фотореализм: яркость и оттенок материалов; GT не меняется."""
        self._realism = bool(enabled)
        if not self.interrupted:
            self._renderer.set_lighting(self._sun, realism=self._realism)

    def set_vibrations(self, enabled: bool) -> None:
        """Колебания камеры предпросмотра; сенсор и GT не затрагиваются."""
        self._vibrations = bool(enabled)
        self._weather = weather_state(
            self._world,
            precipitation=self._weather.precipitation,
            vibrations=self._vibrations,
        )

    def set_precipitation(self, kind: Precipitation) -> None:
        """Вид осадков: визуальный слой поверх кадра."""
        self._weather = weather_state(
            self._world,
            precipitation=kind,
            vibrations=self._vibrations,
        )

    def set_hour(self, hour: float | None) -> None:
        """Время суток для предпросмотра; ``None`` — вернуть освещение мира."""
        self._hour = hour
        if hour is None:
            self._sun = sun_state(self._world.lighting)
        else:
            self._sun = sun_state(
                lighting_for_hour(hour, cloudiness=self._world.lighting.cloudiness)
            )
        if not self.interrupted:
            self._renderer.set_lighting(self._sun, realism=self._realism)

    def advance(self, dt: float) -> None:
        """Продвигает время предпросмотра: от него зависят порывы и вибрация."""
        self._time += max(0.0, float(dt))

    @property
    def time(self) -> float:
        return self._time

    # --- кадры ----------------------------------------------------------

    def preview_pose(self, camera: OrbitCamera) -> CameraPose:
        """Поза предпросмотра с колебаниями среды."""
        pose = camera.pose()
        offset = self._weather.vibration(self._time)
        if offset == (0.0, 0.0, 0.0):
            return pose
        return pose.translated(offset)

    def render(
        self,
        camera: OrbitCamera,
        spec: CameraSpec | None = None,
        *,
        vibrations: bool = True,
    ) -> Frame:
        """Кадр предпросмотра."""
        target_spec = spec if spec is not None else camera.spec()
        pose = self.preview_pose(camera) if vibrations else camera.pose()
        return self._renderer.render(pose, target_spec)

    def render_pose(self, pose: CameraPose, spec: CameraSpec) -> Frame:
        """Кадр с сенсорной позы: без вибрации, для ground truth."""
        return self._renderer.render(pose, spec)

    def render_stereo(self, pose: CameraPose, spec: CameraSpec) -> tuple[Frame, Frame]:
        return self._renderer.render_stereo(pose, spec)

    def ground_truth(self, pose: CameraPose, spec: CameraSpec) -> GroundTruth:
        """Истина: глубина и маски из кадра, позы и точки — из раскладки."""
        frame = self._renderer.render(pose, spec)
        poses = {item.id: item.pose for item in self.layout.objects}
        keypoints = {
            item.id: bbox_corners(item)
            for item in self.layout.objects
            if item.category not in {"terrain", "zone"}
        }
        return GroundTruth(
            depth=frame.depth,
            mask=frame.mask,
            labels=dict(frame.body_labels),
            poses=poses,
            keypoints=keypoints,
            spec=spec,
        )

    # --- снимок ---------------------------------------------------------

    def snapshot(
        self,
        path: Path,
        camera: OrbitCamera,
        spec: CameraSpec | None = None,
    ) -> tuple[Path, Path]:
        """Сохраняет кадр и JSON с камерой и статистикой."""
        target_spec = spec if spec is not None else camera.spec()
        frame = self.render(camera, target_spec)
        meta = {
            "world_id": self._world.world_id,
            "seed": self._world.seed,
            "camera": camera.to_dict(),
            "pose": self.preview_pose(camera).to_dict(),
            "sensor": target_spec.to_dict(),
            "sun": {
                "azimuth_deg": self._sun.azimuth_deg,
                "elevation_deg": self._sun.elevation_deg,
                "phase": self._sun.phase,
            },
            "weather": self._weather.to_dict(),
            "scene": self.stats.to_dict(),
            "depth_min": round(frame.depth_range()[0], 3),
            "depth_max": round(frame.depth_range()[1], 3),
        }
        return save_snapshot(path, frame.rgb, meta)

    # --- жизненный цикл -------------------------------------------------

    def close(self) -> None:
        """Освобождает соединение PyBullet, если сцена его создала."""
        if self._owns_renderer:
            self._renderer.close()

    def __enter__(self) -> Scene:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
