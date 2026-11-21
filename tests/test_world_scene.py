"""Тесты сцены: сборка, детерминизм, GT, переключатели, снимок (G4-01…G4-07)."""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

import numpy as np
import pytest

from simorph.core.world import (
    DEPTH_TOLERANCE,
    CameraPose,
    CameraSpec,
    OrbitCamera,
    Scene,
    SceneLayout,
    SceneObject,
    surface_depth,
)


def _box_scene(world, *, size=(10.0, 10.0, 10.0)) -> Scene:
    layout = SceneLayout(
        seed=world.seed,
        objects=[
            SceneObject(
                "terrain",
                "terrain",
                "box",
                (0.0, 0.0, -0.5),
                (400.0, 400.0, 1.0),
                (0.3, 0.4, 0.3, 1.0),
            ),
            SceneObject("BOX-1", "building", "box", (0.0, 0.0, 5.0), size, (0.8, 0.2, 0.2, 1.0)),
        ],
    )
    scene = Scene(world, layout=layout)
    scene.build()
    return scene


def _pose() -> CameraPose:
    return CameraPose(eye=(0.0, -100.0, 5.0), target=(0.0, 0.0, 5.0), up=(0.0, 0.0, 1.0))


def test_scene_builds_ready(project_document) -> None:
    with Scene(project_document.world, project_document.profile) as scene:
        stats = scene.build()

    assert stats.status == "ready"
    assert stats.error is None
    assert stats.objects == stats.bodies
    assert stats.objects > 0
    assert stats.build_seconds > 0.0


def test_scene_reset_is_identical(project_document) -> None:
    from simorph.core.profile import build_reference_profile

    with Scene(project_document.world, build_reference_profile("agroforce")) as scene:
        scene.build()
        first = scene.layout.signature()
        scene.reset()
        second = scene.layout.signature()

    assert first == second


def test_scene_rebuild_uses_new_world(project_document) -> None:
    with Scene(project_document.world) as scene:
        scene.build()
        changed = project_document.world.model_copy(
            update={"seed": project_document.world.seed + 5}
        )
        stats = scene.rebuild(changed, project_document.profile)

    assert stats.status == "ready"
    assert scene.layout.seed == changed.seed


def test_ground_truth_depth_matches_geometry(project_document) -> None:
    spec = CameraSpec(320, 240)
    with _box_scene(project_document.world) as scene:
        truth = scene.ground_truth(_pose(), spec)
        box = scene.layout.objects[1]
        expected = surface_depth(box, _pose(), spec)
        u, v, _depth = _project(box.pose, _pose(), spec)
        measured = truth.depth_at(u, v)

    assert expected is not None
    assert measured == pytest.approx(expected, rel=DEPTH_TOLERANCE)
    assert truth.label_at(u, v) == "BOX-1"


def test_ground_truth_mask_covers_object(project_document) -> None:
    spec = CameraSpec(160, 120)
    with _box_scene(project_document.world) as scene:
        truth = scene.ground_truth(_pose(), spec)
        mask = truth.mask_of("BOX-1")

    assert mask.any()
    assert mask.sum() > 50
    assert truth.mask_of("нет-такого").sum() == 0


def test_ground_truth_has_poses_and_keypoints(project_document) -> None:
    spec = CameraSpec(160, 120)
    with _box_scene(project_document.world) as scene:
        truth = scene.ground_truth(_pose(), spec)
        payload = truth.to_dict()

    assert "BOX-1" in truth.poses
    assert len(truth.keypoints["BOX-1"]) == 8
    assert payload["visible"] and "BOX-1" in payload["visible"]
    assert payload["width"] == 160


def test_realism_toggle_keeps_ground_truth(project_document) -> None:
    spec = CameraSpec(160, 120)
    with _box_scene(project_document.world) as scene:
        first = scene.ground_truth(_pose(), spec)
        scene.set_realism(False)
        second = scene.ground_truth(_pose(), spec)
        preview = scene.render(_pose_camera(), spec)

    assert np.array_equal(first.depth, second.depth)
    assert np.array_equal(first.mask, second.mask)
    assert preview.rgb.shape == (120, 160, 3)


def test_vibration_does_not_change_sensor_truth(project_document) -> None:
    world = project_document.world.model_copy(
        update={"wind": project_document.world.wind.model_copy(update={"speed_mps": 6.0})}
    )
    spec = CameraSpec(160, 120)
    with _box_scene(world) as scene:
        scene.advance(0.7)
        first = scene.ground_truth(_pose(), spec)
        scene.set_vibrations(True)
        second = scene.ground_truth(_pose(), spec)
        camera = OrbitCamera(target=(0.0, 0.0, 5.0), distance=100.0, yaw_deg=-90.0, pitch_deg=0.0)
        shifted = scene.preview_pose(camera)

    assert np.array_equal(first.depth, second.depth)
    assert np.array_equal(first.mask, second.mask)
    assert shifted.eye != camera.pose().eye


def test_vibration_off_gives_clean_pose(project_document) -> None:
    world = project_document.world.model_copy(
        update={"wind": project_document.world.wind.model_copy(update={"speed_mps": 8.0})}
    )
    camera = OrbitCamera(target=(0.0, 0.0, 5.0), distance=100.0)
    with Scene(world) as scene:
        scene.set_vibrations(False)
        assert scene.preview_pose(camera).eye == camera.pose().eye
        scene.set_vibrations(True)
        scene.advance(1.0)
        assert scene.preview_pose(camera).eye != camera.pose().eye


def test_scene_survives_renderer_failure(project_document) -> None:
    class BrokenRenderer:
        body_count = 0

        def clear(self) -> None:
            pass

        def add_object(self, _item: object) -> int:
            raise RuntimeError("нет соединения")

        def set_lighting(self, _sun: object, *, realism: bool = True) -> None:
            pass

        def close(self) -> None:
            pass

    scene = Scene(project_document.world, renderer=BrokenRenderer())  # type: ignore[arg-type]
    stats = scene.build()

    assert stats.status == "interrupted"
    assert scene.interrupted
    assert stats.error and "нет соединения" in stats.error
    assert stats.objects > 0


def test_object_limit_warning_reaches_scene(project_document) -> None:
    with Scene(project_document.world, project_document.profile, limit=5) as scene:
        stats = scene.build()

    assert stats.truncated > 0
    assert stats.over_limit
    assert stats.warnings >= 1
    assert stats.objects == 5


def test_stereo_pair_is_consistent(project_document) -> None:
    spec = CameraSpec(160, 120, mode="stereo", baseline_m=0.12)
    with _box_scene(project_document.world) as scene:
        left, right = scene.render_stereo(_pose(), spec)
        u, v, _depth = _project((0.0, 0.0, 5.0), _pose(), spec)

    assert left.rgb.shape == right.rgb.shape
    assert left.label_at(u, v) == "BOX-1"
    assert right.label_at(u, v) == "BOX-1"
    assert float(left.depth[int(v), int(u)]) == pytest.approx(
        float(right.depth[int(v), int(u)]), abs=1.0
    )


def test_snapshot_writes_png_and_meta(project_document, tmp_path: Path) -> None:
    camera = OrbitCamera(target=(0.0, 0.0, 5.0), distance=120.0, yaw_deg=-90.0, pitch_deg=0.0)
    target = tmp_path / "frames" / "scene.png"
    with _box_scene(project_document.world) as scene:
        image, meta = scene.snapshot(target, camera, CameraSpec(160, 120))

    assert image.exists()
    assert image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["camera"]["distance"] == pytest.approx(120.0)
    assert payload["scene"]["objects"] == 2
    assert payload["depth_max"] >= payload["depth_min"]


@pytest.mark.perf
def test_thousand_objects_render_above_30_fps(project_document) -> None:
    objects = [
        SceneObject(
            "terrain", "terrain", "box", (0.0, 0.0, -0.5), (400.0, 400.0, 1.0), (0.3, 0.4, 0.3, 1.0)
        )
    ]
    for index in range(999):
        column = index % 32
        row = index // 32
        objects.append(
            SceneObject(
                id=f"O-{index:03d}",
                category="equipment",
                shape="box",
                pose=(-60.0 + column * 4.0, -60.0 + row * 4.0, 1.0),
                size=(2.0, 2.0, 2.0),
                color=(0.6, 0.6, 0.6, 1.0),
            )
        )
    layout = SceneLayout(seed=1, objects=objects, limit=1000)
    camera = OrbitCamera(target=(0.0, 0.0, 0.0), distance=220.0, yaw_deg=45.0, pitch_deg=-40.0)
    # Интерактивное разрешение вида: на нём проверяется G4-05.
    spec = CameraSpec(240, 180)

    with Scene(project_document.world, layout=layout) as scene:
        stats = scene.build()
        for _ in range(2):
            scene.render(camera, spec)
        timings = []
        for _ in range(7):
            started = time.perf_counter()
            scene.render(camera, spec)
            timings.append(time.perf_counter() - started)

    median_ms = statistics.median(timings) * 1000.0
    fps = 1000.0 / median_ms
    assert stats.objects == 1000
    assert fps >= 30.0, f"{fps:.1f} FPS при 1000 объектах (медиана {median_ms:.1f} мс)"


def test_lighting_and_weather_are_deterministic(project_document) -> None:
    from simorph.core.world import lighting_for_hour, weather_state

    noon = lighting_for_hour(12.0)
    midnight = lighting_for_hour(0.0)
    weather = weather_state(project_document.world)

    assert noon.sun_elevation_deg > midnight.sun_elevation_deg
    assert weather.gust(1.0) == weather.gust(1.0)
    assert weather.vibration(1.0) == weather.vibration(1.0)


def _project(point, pose, spec):
    from simorph.core.world import project_point

    projected = project_point(point, pose, spec)
    assert projected is not None
    return projected


def _pose_camera() -> OrbitCamera:
    return OrbitCamera(target=(0.0, 0.0, 5.0), distance=100.0, yaw_deg=-90.0, pitch_deg=0.0)
