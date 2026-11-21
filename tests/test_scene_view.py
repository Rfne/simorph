"""Тесты 3D-вида сцены в интерфейсе (T4-06, T4-08, G4-05…G4-07)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from simorph.settings import ApplicationSettings
from simorph.ui.main_window import SCENE_TITLE, MainWindow
from simorph.ui.scene_view import SceneView


@pytest.fixture
def window(qtbot: object, isolated_settings: ApplicationSettings) -> MainWindow:
    created = MainWindow(isolated_settings)
    created.confirmations_enabled = False
    qtbot.addWidget(created)
    return created


@pytest.fixture
def scene_view(window: MainWindow) -> Iterator[SceneView]:
    window.open_scene()
    view = window.scene_view
    assert view is not None
    yield view
    view.dispose()


@pytest.mark.ui
def test_scene_tab_opens_and_builds(window: MainWindow) -> None:
    index = window.open_scene()
    view = window.scene_view

    assert index >= 0
    assert view is not None
    assert window.tabs.tabText(index) == SCENE_TITLE
    assert view.scene.status == "ready"
    assert "объектов" in view.status_text
    assert view.scene.stats.objects > 0


@pytest.mark.ui
def test_scene_renders_frame(scene_view: SceneView) -> None:
    image = scene_view.render_once(64, 48)

    assert image.width() == 64
    assert image.height() == 48
    assert not image.isNull()


@pytest.mark.ui
def test_toggles_change_preview_only(scene_view: SceneView) -> None:
    scene = scene_view.scene

    scene_view.set_realism(False)
    assert scene.realism is False

    scene_view.set_vibrations(False)
    assert scene.weather.vibration_amplitude_m == 0.0

    scene_view.set_precipitation("snow")
    assert scene.weather.precipitation == "snow"

    scene_view.set_hour(0)
    assert scene.sun.elevation_deg < 0.0

    assert scene.status == "ready"
    assert scene.stats.bodies == scene.stats.objects


@pytest.mark.ui
def test_snapshot_saves_image_and_meta(scene_view: SceneView, tmp_path: Path) -> None:
    target = tmp_path / "scene.png"

    assert scene_view.save_snapshot(target) is True

    assert target.exists()
    meta = json.loads(target.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["camera"]["distance"] > 0.0
    assert meta["scene"]["objects"] > 0


@pytest.mark.ui
def test_reset_restores_layout(scene_view: SceneView) -> None:
    scene = scene_view.scene
    before = scene.layout.signature()

    scene_view.reset_scene()

    assert scene.layout.signature() == before
    assert scene.status == "ready"


@pytest.mark.ui
def test_dispose_stops_timers(scene_view: SceneView) -> None:
    scene_view.dispose()
    scene_view.tick()

    assert scene_view.frame_ms == 0.0
