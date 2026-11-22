"""Тесты потока событий: монотонные метки и порядок (T7-04, G7-03)."""

from __future__ import annotations

from simorph.core.simulation.events import EventKind, EventStream


class _Clock:
    """Часы с заданной последовательностью значений."""

    def __init__(self, values: list[int]) -> None:
        self._values = values

    def __call__(self) -> int:
        return self._values.pop(0) if self._values else 0


def test_events_keep_monotonic_labels() -> None:
    stream = EventStream(clock_ns=_Clock([10, 5, 20]))
    first = stream.emit(EventKind.RUN_STARTED, "старт")
    second = stream.emit(EventKind.STATE_CHANGED, "пауза", t_scene_ns=5)
    third = stream.emit(EventKind.RUN_FINISHED, "финиш", t_scene_ns=10)

    assert (first.seq, second.seq, third.seq) == (0, 1, 2)
    assert second.t_monotonic_ns == 10
    assert third.t_monotonic_ns == 20
    assert stream.ordered() is True


def test_events_are_filtered_by_kind() -> None:
    stream = EventStream(clock_ns=_Clock([1, 2, 3]))
    stream.emit(EventKind.RUN_STARTED)
    stream.emit(EventKind.FRAME_PUSHED, "кадр", frame_id=1)
    stream.emit(EventKind.FRAME_PUSHED, "кадр", frame_id=2)

    assert len(stream) == 3
    assert [item.data["frame_id"] for item in stream.by_kind(EventKind.FRAME_PUSHED)] == [1, 2]
    assert EventKind.RUN_STARTED in stream.kinds()
    assert stream.last is not None
    assert stream.last.kind is EventKind.FRAME_PUSHED


def test_payload_and_clear() -> None:
    stream = EventStream(clock_ns=_Clock([1, 2]))
    stream.emit(EventKind.ERROR, "сбой", code="E1")
    payload = stream.to_payload()

    assert payload[0]["kind"] == "error"
    assert payload[0]["data"] == {"code": "E1"}

    stream.clear()
    assert len(stream) == 0
    assert stream.last is None
