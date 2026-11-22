"""Типы портов конвейера и правила совместимости (T8-01).

Порт — это типизированный вход или выход узла. Тип определяет, что по
ребру передаётся: кадр, карта глубины, облако точек, детекции и т. д.
Ребро допустимо, только если выход источника совместим со входом приёмника.

Двенадцать типов MVP покрывают оба типовых конвейера: вычислительный
(стерео, разреженные признаки, плотная глубина, ROI, события) и
нейросетевой (препроцессинг, инференс, NMS, головы). Порт ``events``
широкий: журнал событий принимает любой результат, поэтому приёмник
событий подключается к любому узлу без преобразований.

Ссылка на порт хранится строкой ``<узел>.<порт>`` — так же, как в схеме
проекта (``PipelineEdge``), поэтому ребро и диагностика говорят на одном
языке.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = [
    "PORT_KINDS",
    "PORT_TITLES",
    "PortKind",
    "PortRefParts",
    "accepts",
    "format_port_ref",
    "parse_port_ref",
    "port_title",
]


class PortKind(StrEnum):
    """Тип данных на порту конвейера."""

    FRAME_MONO = "frame.mono"
    FRAME_COLOR = "frame.color"
    FRAME_STEREO = "frame.stereo"
    DISPARITY = "disparity"
    DEPTH = "depth"
    POINTS = "points"
    FEATURES = "features"
    TRACKS = "tracks"
    ROI = "roi"
    DETECTIONS = "detections"
    EMBEDDINGS = "embeddings"
    EVENTS = "events"


#: Все типы портов в порядке TRD 5.8.
PORT_KINDS: Final[tuple[PortKind, ...]] = tuple(PortKind)

#: Русские подписи типов для редактора и отчёта.
PORT_TITLES: Final[dict[PortKind, str]] = {
    PortKind.FRAME_MONO: "моно-кадр",
    PortKind.FRAME_COLOR: "цветной кадр",
    PortKind.FRAME_STEREO: "стереопара",
    PortKind.DISPARITY: "диспаратность",
    PortKind.DEPTH: "глубина",
    PortKind.POINTS: "облако точек",
    PortKind.FEATURES: "признаки",
    PortKind.TRACKS: "треки",
    PortKind.ROI: "области интереса",
    PortKind.DETECTIONS: "детекции",
    PortKind.EMBEDDINGS: "эмбеддинги",
    PortKind.EVENTS: "события",
}

#: Какие типы принимает порт. ``events`` принимает всё.
_ACCEPTS: Final[dict[PortKind, frozenset[PortKind]]] = {
    PortKind.FRAME_MONO: frozenset({PortKind.FRAME_MONO}),
    PortKind.FRAME_COLOR: frozenset({PortKind.FRAME_COLOR, PortKind.FRAME_MONO}),
    PortKind.FRAME_STEREO: frozenset({PortKind.FRAME_STEREO}),
    PortKind.DISPARITY: frozenset({PortKind.DISPARITY, PortKind.FRAME_STEREO}),
    PortKind.DEPTH: frozenset({PortKind.DEPTH, PortKind.DISPARITY}),
    PortKind.POINTS: frozenset({PortKind.POINTS, PortKind.DEPTH}),
    PortKind.FEATURES: frozenset({PortKind.FEATURES, PortKind.FRAME_MONO, PortKind.FRAME_COLOR}),
    PortKind.TRACKS: frozenset({PortKind.TRACKS, PortKind.FEATURES}),
    PortKind.ROI: frozenset(
        {
            PortKind.ROI,
            PortKind.DETECTIONS,
            PortKind.FRAME_COLOR,
            PortKind.FRAME_MONO,
        }
    ),
    PortKind.DETECTIONS: frozenset({PortKind.DETECTIONS}),
    PortKind.EMBEDDINGS: frozenset({PortKind.EMBEDDINGS, PortKind.FEATURES}),
    PortKind.EVENTS: frozenset(PORT_KINDS),
}


def accepts(target: PortKind, source: PortKind) -> bool:
    """Совместим ли выход ``source`` со входом ``target``."""
    return source in _ACCEPTS[target]


def port_title(kind: PortKind) -> str:
    """Подпись типа порта; неизвестный тип возвращается как есть."""
    return PORT_TITLES.get(kind, str(kind))


@dataclass(frozen=True, slots=True)
class PortRefParts:
    """Разобранная ссылка на порт."""

    node: str
    port: str

    @property
    def text(self) -> str:
        return format_port_ref(self.node, self.port)


def parse_port_ref(ref: str) -> PortRefParts:
    """Делит ссылку ``<узел>.<порт>`` на части.

    Raises:
        ValueError: в ссылке нет разделителя или одна из частей пуста.
    """
    node, separator, port = ref.partition(".")
    if not separator or not node or not port:
        raise ValueError(f"ссылка на порт {ref!r} должна иметь вид '<узел>.<порт>'")
    return PortRefParts(node=node, port=port)


def format_port_ref(node: str, port: str) -> str:
    """Собирает ссылку на порт."""
    return f"{node}.{port}"
