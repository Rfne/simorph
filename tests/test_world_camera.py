"""Тесты камеры сцены: сенсор, орбита, стереопара, проекция (T4-05, T4-07)."""

from __future__ import annotations

import math

import pytest

from simorph.core.world import (
    CameraPose,
    CameraSpec,
    OrbitCamera,
    bbox_corners,
    project_point,
    stereo_offset,
    surface_depth,
)
from simorph.core.world.layout import SceneObject


def _pose() -> CameraPose:
    return CameraPose(eye=(0.0, -100.0, 5.0), target=(0.0, 0.0, 5.0), up=(0.0, 0.0, 1.0))


def _box(size: tuple[float, float, float] = (10.0, 10.0, 10.0)) -> SceneObject:
    return SceneObject("BOX-1", "building", "box", (0.0, 0.0, 5.0), size, (0.8, 0.2, 0.2, 1.0))


def test_spec_aspect_and_scaled() -> None:
    spec = CameraSpec(640, 480, fov_deg=45.0, mode="stereo", baseline_m=0.2)

    assert spec.aspect == pytest.approx(4.0 / 3.0)
    small = spec.scaled(240, 180)
    assert (small.width, small.height) == (240, 180)
    assert small.fov_deg == 45.0
    assert small.mode == "stereo"
    assert small.baseline_m == 0.2
    assert spec.scaled(4, 4).width == 16


def test_orbit_pose_keeps_distance_and_looks_at_target() -> None:
    camera = OrbitCamera(target=(10.0, 20.0, 5.0), distance=100.0, yaw_deg=30.0, pitch_deg=-20.0)
    pose = camera.pose()
    target = camera.target
    to_target = tuple(target[index] - pose.eye[index] for index in range(3))
    length = math.dist(pose.eye, target)

    assert length == pytest.approx(100.0)
    assert tuple(pose.forward[index] for index in range(3)) == pytest.approx(
        tuple(value / length for value in to_target)
    )
    assert pose.forward[2] < 0.0


def test_orbit_wraps_yaw_and_clamps_pitch() -> None:
    camera = OrbitCamera(yaw_deg=350.0, pitch_deg=0.0)

    camera.orbit(20.0, 100.0)
    assert camera.yaw_deg == pytest.approx(10.0)
    assert camera.pitch_deg == pytest.approx(85.0)

    camera.orbit(0.0, -400.0)
    assert camera.pitch_deg == pytest.approx(-85.0)


def test_zoom_clamps_distance() -> None:
    camera = OrbitCamera(distance=100.0)

    camera.zoom(0.5)
    assert camera.distance == pytest.approx(50.0)

    camera.zoom(1e-9)
    assert camera.distance == pytest.approx(OrbitCamera.MIN_DISTANCE)

    camera.zoom(1e9)
    assert camera.distance == pytest.approx(OrbitCamera.MAX_DISTANCE)


def test_zoom_ignores_non_positive_factor() -> None:
    camera = OrbitCamera(distance=100.0)

    camera.zoom(0.0)
    camera.zoom(-2.0)

    assert camera.distance == pytest.approx(100.0)


def test_pan_moves_target_along_right_vector() -> None:
    camera = OrbitCamera(target=(0.0, 0.0, 0.0), distance=100.0, yaw_deg=-90.0, pitch_deg=0.0)
    right = camera.pose().right

    camera.pan(10.0, 0.0)

    assert camera.target[0] == pytest.approx(right[0] * 2.0)
    assert camera.target[2] == pytest.approx(0.0)


def test_stereo_offset_is_symmetric() -> None:
    pose = _pose()

    left, right = stereo_offset(pose, 0.5)

    assert math.dist(left.eye, right.eye) == pytest.approx(0.5)
    midpoint = tuple((left.eye[index] + right.eye[index]) / 2.0 for index in range(3))
    assert midpoint == pytest.approx(pose.eye)
    assert left.target[0] < pose.target[0] < right.target[0]


def test_project_point_center_and_side() -> None:
    spec = CameraSpec(160, 120)

    center = project_point((0.0, 0.0, 5.0), _pose(), spec)
    right = project_point((10.0, 0.0, 5.0), _pose(), spec)

    assert center is not None and right is not None
    assert center[0] == pytest.approx(80.0, abs=0.5)
    assert center[1] == pytest.approx(60.0, abs=0.5)
    assert center[2] == pytest.approx(100.0)
    assert right[0] > center[0]


def test_project_point_behind_camera_is_none() -> None:
    spec = CameraSpec(160, 120)

    assert project_point((0.0, -200.0, 5.0), _pose(), spec) is None


def test_bbox_corners_span_size() -> None:
    corners = bbox_corners(_box())

    assert len(corners) == 8
    assert min(point[0] for point in corners) == pytest.approx(-5.0)
    assert max(point[2] for point in corners) == pytest.approx(10.0)


def test_surface_depth_is_closer_than_center() -> None:
    spec = CameraSpec(160, 120)
    box = _box()
    center = project_point(box.pose, _pose(), spec)

    assert center is not None
    assert surface_depth(box, _pose(), spec) == pytest.approx(center[2] - 5.0)
