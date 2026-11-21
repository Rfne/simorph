"""Тесты погоды: ветер, порывы, осадки и колебания (T4-04, FR-WLD-04, FR-WLD-08)."""

from __future__ import annotations

import pytest

from simorph.core.world import weather_state


def _world(project_document, *, speed=5.0, cloudiness=0.0, gust="dryden"):
    world = project_document.world
    return world.model_copy(
        update={
            "wind": world.wind.model_copy(update={"speed_mps": speed, "gust_model": gust}),
            "lighting": world.lighting.model_copy(update={"cloudiness": cloudiness}),
        }
    )


def test_amplitude_grows_with_wind(project_document) -> None:
    calm = weather_state(_world(project_document, speed=0.0))
    windy = weather_state(_world(project_document, speed=5.0))

    assert calm.vibration_amplitude_m == 0.0
    assert windy.vibration_amplitude_m == pytest.approx(0.032)


def test_vibrations_off_zeroes_amplitude(project_document) -> None:
    state = weather_state(_world(project_document, speed=10.0), vibrations=False)

    assert state.vibration_amplitude_m == 0.0
    assert state.vibration(1.0) == (0.0, 0.0, 0.0)


def test_precipitation_follows_cloudiness(project_document) -> None:
    assert weather_state(_world(project_document, cloudiness=0.1)).precipitation == "none"
    assert weather_state(_world(project_document, cloudiness=0.8)).precipitation == "rain"


def test_explicit_precipitation_wins(project_document) -> None:
    state = weather_state(_world(project_document, cloudiness=0.9), precipitation="snow")

    assert state.precipitation == "snow"


def test_gust_is_deterministic_and_bounded(project_document) -> None:
    state = weather_state(_world(project_document, speed=5.0))

    first = state.gust(1.3)
    assert first == state.gust(1.3)
    for moment in (0.0, 0.4, 1.0, 2.5, 7.0):
        assert 0.45 < state.gust(moment) < 1.6


def test_gust_is_flat_for_other_models(project_document) -> None:
    state = weather_state(_world(project_document, speed=5.0, gust="constant"))

    assert state.gust(3.0) == 1.0


def test_vibration_is_bounded_by_amplitude(project_document) -> None:
    state = weather_state(_world(project_document, speed=8.0))

    for moment in (0.1, 0.7, 2.0, 5.5):
        offset = state.vibration(moment)
        assert len(offset) == 3
        assert abs(offset[0]) <= state.vibration_amplitude_m * 1.6
        assert abs(offset[2]) <= state.vibration_amplitude_m * 0.8 + 1e-9


def test_particles_count_follows_kind_and_clouds(project_document) -> None:
    none = weather_state(_world(project_document, cloudiness=0.0))
    rain = weather_state(_world(project_document, cloudiness=0.5), precipitation="rain")
    snow = weather_state(_world(project_document, cloudiness=0.5), precipitation="snow")

    assert none.particles_per_frame == 0
    assert rain.particles_per_frame == 42
    assert snow.particles_per_frame == 28


def test_wind_vector_uses_direction(project_document) -> None:
    world = _world(project_document, speed=4.0)
    world = world.model_copy(update={"wind": world.wind.model_copy(update={"direction_deg": 90.0})})

    vector = weather_state(world).wind_vector

    assert vector[0] == pytest.approx(0.0, abs=1e-9)
    assert vector[1] == pytest.approx(4.0)
    assert vector[2] == 0.0
