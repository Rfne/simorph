"""Тесты бюджета миссии: критический путь, параллелизм, TTL (T6-05, FR-MSN-07)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from simorph.core.mission.timings import element_duration, mission_budget
from simorph.core.models.exercise import MissionElement


def _sequential() -> list[MissionElement]:
    """Два зависания подряд: бюджет равен сумме длительностей."""
    return [
        MissionElement(id="start", type="start", next=["d1"]),
        MissionElement(id="d1", type="dwell", duration_s=10.0, next=["d2"]),
        MissionElement(id="d2", type="dwell", duration_s=5.0, next=["ret"]),
        MissionElement(id="ret", type="return_to_base"),
    ]


def _parallel() -> list[MissionElement]:
    """Параллельные ветви: миссия ждёт самую долгую."""
    return [
        MissionElement(id="start", type="start", next=["split"]),
        MissionElement(id="split", type="parallel", next=["long", "short"]),
        MissionElement(id="long", type="dwell", duration_s=10.0, next=["ret"]),
        MissionElement(id="short", type="dwell", duration_s=4.0, next=["ret"]),
        MissionElement(id="ret", type="return_to_base", duration_s=1.0),
    ]


@pytest.mark.unit
def test_element_duration_defaults_to_zero() -> None:
    assert element_duration(MissionElement(id="s", type="start")) == 0.0
    assert element_duration(MissionElement(id="d", type="dwell", duration_s=2.5)) == 2.5


@pytest.mark.unit
def test_sequential_budget_is_sum() -> None:
    budget = mission_budget(_sequential())

    assert budget.total_s == pytest.approx(15.0)
    assert budget.sequential_s == pytest.approx(15.0)
    assert budget.parallel_width == 1
    assert budget.critical_path == ["start", "d1", "d2", "ret"]


@pytest.mark.unit
def test_parallel_budget_follows_longest_branch() -> None:
    budget = mission_budget(_parallel())

    assert budget.total_s == pytest.approx(11.0)
    assert budget.sequential_s == pytest.approx(15.0)
    assert budget.parallel_width == 2
    assert budget.critical_path == ["start", "split", "long", "ret"]


@pytest.mark.unit
def test_parallel_branches_start_together() -> None:
    budget = mission_budget(_parallel())

    assert budget.element_start("long") == pytest.approx(0.0)
    assert budget.element_start("short") == pytest.approx(0.0)
    assert budget.element_end("long") == pytest.approx(10.0)
    assert budget.element_end("short") == pytest.approx(4.0)
    assert budget.element_start("ret") == pytest.approx(10.0)
    assert budget.element_end("ret") == pytest.approx(11.0)


@pytest.mark.unit
def test_budget_of_single_element() -> None:
    budget = mission_budget([MissionElement(id="start", type="start")])

    assert budget.total_s == 0.0
    assert budget.parallel_width == 1
    assert budget.critical_path == ["start"]
    assert budget.element_start("start") == 0.0


@pytest.mark.unit
def test_empty_mission_has_zero_budget() -> None:
    budget = mission_budget([])

    assert budget.total_s == 0.0
    assert budget.critical_path == []
    assert budget.parallel_width == 0
    assert budget.ttl_s is None
    assert budget.over_ttl is False


@pytest.mark.unit
def test_ttl_is_the_tightest_limit() -> None:
    elements = _sequential()
    elements[1] = elements[1].model_copy(update={"ttl_s": 40.0})
    elements[2] = elements[2].model_copy(update={"ttl_s": 20.0})

    budget = mission_budget(elements)

    assert budget.ttl_s == pytest.approx(20.0)
    assert budget.over_ttl is False


@pytest.mark.unit
def test_budget_over_ttl_is_reported() -> None:
    elements = _sequential()
    elements[2] = elements[2].model_copy(update={"ttl_s": 8.0})

    budget = mission_budget(elements)

    assert budget.ttl_s == pytest.approx(8.0)
    assert budget.over_ttl is True


@pytest.mark.unit
def test_budget_is_reproducible() -> None:
    first = mission_budget(_parallel()).to_dict()
    second = mission_budget(_parallel()).to_dict()

    assert first == second
    assert first["total_s"] == pytest.approx(11.0)
    assert first["parallel_width"] == 2
    assert first["timeline"][0] == {"id": "start", "start_s": 0.0, "end_s": 0.0}


@pytest.mark.unit
def test_negative_duration_is_rejected() -> None:
    with pytest.raises(ValidationError):
        MissionElement(id="d", type="dwell", duration_s=-1.0)


@pytest.mark.unit
def test_negative_ttl_is_rejected() -> None:
    with pytest.raises(ValidationError):
        MissionElement(id="d", type="dwell", ttl_s=-5.0)
