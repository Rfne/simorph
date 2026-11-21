"""Тесты освещения и времени суток (T4-03, FR-WLD-07)."""

from __future__ import annotations

import pytest

from simorph.core.world import SunState, lighting_for_hour, phase_title, sun_state


def test_noon_has_highest_sun() -> None:
    noon = lighting_for_hour(12.0)

    assert noon.sun_elevation_deg == pytest.approx(60.0)
    assert noon.sun_azimuth_deg == pytest.approx(180.0)


def test_dawn_and_sunset_are_at_horizon() -> None:
    dawn = lighting_for_hour(6.0)
    sunset = lighting_for_hour(18.0)

    assert dawn.sun_elevation_deg == pytest.approx(0.0, abs=1e-9)
    assert dawn.sun_azimuth_deg == pytest.approx(90.0)
    assert sunset.sun_elevation_deg == pytest.approx(0.0, abs=1e-9)
    assert sunset.sun_azimuth_deg == pytest.approx(270.0)


def test_midnight_is_clamped_below_horizon() -> None:
    midnight = lighting_for_hour(0.0)

    assert midnight.sun_elevation_deg == pytest.approx(-30.0)
    assert midnight.sun_elevation_deg < 0.0


def test_hour_wraps_and_cloudiness_is_clamped() -> None:
    assert lighting_for_hour(24.0).sun_elevation_deg == pytest.approx(
        lighting_for_hour(0.0).sun_elevation_deg
    )
    assert lighting_for_hour(12.0, cloudiness=2.0).cloudiness == pytest.approx(1.0)
    assert lighting_for_hour(12.0, cloudiness=-1.0).cloudiness == pytest.approx(0.0)


def test_sun_state_maps_world_lighting(project_document) -> None:
    state = sun_state(project_document.world.lighting)

    assert state.azimuth_deg == pytest.approx(project_document.world.lighting.sun_azimuth_deg)
    assert state.elevation_deg == pytest.approx(project_document.world.lighting.sun_elevation_deg)


def test_intensity_depends_on_elevation_and_clouds() -> None:
    clear = SunState(180.0, 60.0, 0.0)
    cloudy = SunState(180.0, 60.0, 0.5)
    night = SunState(0.0, -10.0, 0.0)

    assert clear.intensity == pytest.approx(1.0)
    assert cloudy.intensity == pytest.approx(0.675)
    assert night.intensity == 0.0
    assert cloudy.intensity < clear.intensity


def test_phase_and_title() -> None:
    assert SunState(0.0, 90.0, 0.0).phase == "day"
    assert SunState(0.0, 0.0, 0.0).phase == "twilight"
    assert SunState(0.0, -10.0, 0.0).phase == "night"
    assert phase_title(SunState(0.0, 90.0, 0.0)) == "день"


def test_color_depends_on_phase() -> None:
    day = SunState(0.0, 90.0, 0.0).color
    twilight = SunState(0.0, 0.0, 0.0).color
    night = SunState(0.0, -10.0, 0.0).color

    assert day == pytest.approx((1.0, 0.97, 0.92))
    assert twilight[0] > twilight[2]
    assert night[2] > night[0]


def test_shade_keeps_base_without_realism() -> None:
    base = (0.8, 0.2, 0.2, 1.0)

    assert SunState(0.0, 90.0, 0.0).shade(base, realism=False) == base


def test_shade_preserves_alpha_and_darkens_clouds() -> None:
    base = (0.8, 0.2, 0.2, 1.0)
    clear = SunState(0.0, 60.0, 0.0).shade(base)
    cloudy = SunState(0.0, 60.0, 0.8).shade(base)

    assert clear[3] == pytest.approx(1.0)
    assert cloudy[3] == pytest.approx(1.0)
    assert cloudy[0] < clear[0]
    assert cloudy[1] < clear[1]
