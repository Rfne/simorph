"""Мир и 3D-сцена (этап 4).

Пакет не зависит от Qt: раскладку, свет, погоду и рендер использует и
интерфейс, и CLI. PyBullet импортируется лениво, только в рендере, поэтому
чтение проекта и проверка раскладки работают и без графической библиотеки.
"""

from __future__ import annotations

from simorph.core.world.camera import (
    CameraPose,
    CameraSpec,
    OrbitCamera,
    stereo_offset,
)
from simorph.core.world.ground_truth import (
    DEPTH_TOLERANCE,
    GroundTruth,
    bbox_corners,
    project_point,
    surface_depth,
)
from simorph.core.world.layout import (
    MAX_OBJECTS,
    SceneLayout,
    SceneObject,
    build_layout,
    generation_values,
)
from simorph.core.world.lighting import (
    SunState,
    lighting_for_hour,
    phase_title,
    sun_state,
)
from simorph.core.world.primitives import (
    ASSET_IDS,
    BUILTIN_ASSETS,
    AssetSpec,
    resolve_asset,
)
from simorph.core.world.renderer import BulletRenderer, Frame, RenderError
from simorph.core.world.scene import Scene, SceneStats, SceneStatus
from simorph.core.world.snapshot import png_bytes, save_snapshot, write_png
from simorph.core.world.weather import (
    Precipitation,
    WeatherState,
    weather_state,
)

__all__ = [
    "ASSET_IDS",
    "BUILTIN_ASSETS",
    "DEPTH_TOLERANCE",
    "MAX_OBJECTS",
    "AssetSpec",
    "BulletRenderer",
    "CameraPose",
    "CameraSpec",
    "Frame",
    "GroundTruth",
    "OrbitCamera",
    "Precipitation",
    "RenderError",
    "Scene",
    "SceneLayout",
    "SceneObject",
    "SceneStats",
    "SceneStatus",
    "SunState",
    "WeatherState",
    "bbox_corners",
    "build_layout",
    "generation_values",
    "lighting_for_hour",
    "phase_title",
    "png_bytes",
    "project_point",
    "resolve_asset",
    "save_snapshot",
    "stereo_offset",
    "sun_state",
    "surface_depth",
    "weather_state",
    "write_png",
]
