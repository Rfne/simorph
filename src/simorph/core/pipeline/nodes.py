"""Реестр типов узлов конвейера (T8-02, T8-03).

Узел описывается контрактом: какие входы и выходы он объявляет, сколько
миллисекунд занимает по плану и какие параметры обязательны. Редактор,
валидатор и исполнитель берут типы из одного реестра, поэтому не
расходятся в названиях портов и бюджетов.

Вычислительный конвейер — разреженный контур стерео (ректификация,
детекция и трекинг признаков, разреженная стерео, плотная стерео, ROI,
слияние) плюс приёмник событий. Нейросетевой — препроцессинг, инференс,
NMS и головы детекции и эмбеддинга. Источники и приёмники общие: их
используют оба конвейера.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Literal

from simorph.core.pipeline.ports import PortKind

__all__ = [
    "NODE_SPECS",
    "NODE_TYPES",
    "NODE_TYPES_BY_KIND",
    "NodePipeline",
    "NodeSpec",
    "default_params",
    "node_budget_ms",
    "node_input_kind",
    "node_output_kind",
    "node_spec",
    "node_specs_for",
]

#: К какому конвейеру относится узел; ``common`` — к обоим.
NodePipeline = Literal["computational", "neural", "common"]


@dataclass(frozen=True, slots=True)
class NodeSpec:
    """Контракт одного типа узла."""

    type: str
    title: str
    kind: NodePipeline
    category: str
    description: str
    inputs: dict[str, PortKind] = field(default_factory=dict)
    outputs: dict[str, PortKind] = field(default_factory=dict)
    budget_ms: float = 1.0
    required_params: tuple[str, ...] = ()
    defaults: dict[str, Any] = field(default_factory=dict)
    #: Приёмник событий принимает результат любого типа.
    universal_input: bool = False

    @property
    def is_source(self) -> bool:
        """У узла нет входов: он порождает поток."""
        return not self.inputs

    @property
    def is_sink(self) -> bool:
        """У узла нет выходов: он завершает ветвь."""
        return not self.outputs


#: Полный перечень типов узлов MVP.
NODE_SPECS: Final[tuple[NodeSpec, ...]] = (
    # --- источники и приёмники -------------------------------------------
    NodeSpec(
        type="source.frame",
        title="Источник кадра",
        kind="common",
        category="source",
        description="Цветной кадр с камеры агента",
        outputs={"frame": PortKind.FRAME_COLOR},
        budget_ms=0.2,
        required_params=("camera",),
        defaults={"camera": "cam_stereo", "fps": 120},
    ),
    NodeSpec(
        type="source.stereo",
        title="Источник стереопары",
        kind="computational",
        category="source",
        description="Стереопара с камеры агента",
        outputs={"stereo": PortKind.FRAME_STEREO},
        budget_ms=0.2,
        required_params=("camera",),
        defaults={"camera": "cam_stereo", "fps": 120},
    ),
    NodeSpec(
        type="sink.events",
        title="Приёмник событий",
        kind="common",
        category="sink",
        description="Завершает ветвь и публикует результат в журнал событий",
        inputs={"events": PortKind.EVENTS},
        budget_ms=0.1,
        required_params=("topic",),
        defaults={"topic": "events"},
        universal_input=True,
    ),
    # --- вычислительный конвейер -----------------------------------------
    NodeSpec(
        type="rectify",
        title="Ректификация",
        kind="computational",
        category="stereo",
        description="Выравнивание стереопары по калибровке",
        inputs={"stereo": PortKind.FRAME_STEREO},
        outputs={"stereo": PortKind.FRAME_STEREO},
        budget_ms=0.8,
        defaults={"calibration": "default"},
    ),
    NodeSpec(
        type="sparse.detect",
        title="Детекция признаков",
        kind="computational",
        category="sparse",
        description="Поиск ключевых точек на стереопаре",
        inputs={"stereo": PortKind.FRAME_STEREO},
        outputs={"features": PortKind.FEATURES},
        budget_ms=1.2,
        defaults={"max_features": 200},
    ),
    NodeSpec(
        type="sparse.track",
        title="Трекинг признаков",
        kind="computational",
        category="sparse",
        description="Сопровождение признаков между кадрами",
        inputs={"features": PortKind.FEATURES},
        outputs={"tracks": PortKind.TRACKS},
        budget_ms=1.0,
        defaults={"window": 5},
    ),
    NodeSpec(
        type="sparse.stereo",
        title="Разреженная стерео",
        kind="computational",
        category="sparse",
        description="Триангуляция треков в облако точек",
        inputs={"tracks": PortKind.TRACKS},
        outputs={"points": PortKind.POINTS},
        budget_ms=1.5,
        defaults={"min_depth_m": 0.5},
    ),
    NodeSpec(
        type="dense.stereo",
        title="Плотная стерео",
        kind="computational",
        category="stereo",
        description="Карта диспаратности и глубины по стереопаре",
        inputs={"stereo": PortKind.FRAME_STEREO},
        outputs={
            "disparity": PortKind.DISPARITY,
            "depth": PortKind.DEPTH,
        },
        budget_ms=2.5,
        defaults={"block_size": 5},
    ),
    NodeSpec(
        type="roi",
        title="Области интереса",
        kind="computational",
        category="roi",
        description="Выделение ROI по кадру или детекциям",
        inputs={"frame": PortKind.FRAME_COLOR},
        outputs={"roi": PortKind.ROI},
        budget_ms=0.6,
        defaults={"margin": 0.1},
    ),
    NodeSpec(
        type="fusion",
        title="Слияние",
        kind="computational",
        category="fusion",
        description="Слияние разреженных точек с картой глубины",
        inputs={"points": PortKind.POINTS, "depth": PortKind.DEPTH},
        outputs={"points": PortKind.POINTS},
        budget_ms=0.5,
        defaults={"voxel_m": 0.1},
    ),
    NodeSpec(
        type="event",
        title="Событие",
        kind="computational",
        category="fusion",
        description="Формирование события по трекам или детекциям",
        inputs={"tracks": PortKind.TRACKS, "detections": PortKind.DETECTIONS},
        outputs={"events": PortKind.EVENTS},
        budget_ms=0.3,
        defaults={"topic": "events"},
    ),
    # --- нейросетевой конвейер -------------------------------------------
    NodeSpec(
        type="preprocess",
        title="Препроцессинг",
        kind="neural",
        category="neural",
        description="Приведение кадра к входу модели",
        inputs={"frame": PortKind.FRAME_COLOR},
        outputs={"frame": PortKind.FRAME_COLOR},
        budget_ms=2.0,
        defaults={"width": 640, "height": 480},
    ),
    NodeSpec(
        type="inference",
        title="Инференс",
        kind="neural",
        category="neural",
        description="Вызов InferenceBackend.infer()",
        inputs={"frame": PortKind.FRAME_COLOR},
        outputs={
            "detections": PortKind.DETECTIONS,
            "embeddings": PortKind.EMBEDDINGS,
        },
        budget_ms=380.0,
        defaults={"model": "stub", "backend": "stub"},
    ),
    NodeSpec(
        type="postprocess.nms",
        title="NMS",
        kind="neural",
        category="neural",
        description="Подавление пересекающихся детекций",
        inputs={"detections": PortKind.DETECTIONS},
        outputs={"detections": PortKind.DETECTIONS},
        budget_ms=5.0,
        defaults={"iou": 0.5},
    ),
    NodeSpec(
        type="head.detection",
        title="Голова детекции",
        kind="neural",
        category="neural",
        description="Детекции по признакам",
        inputs={"features": PortKind.FEATURES},
        outputs={"detections": PortKind.DETECTIONS},
        budget_ms=10.0,
        defaults={"classes": 4},
    ),
    NodeSpec(
        type="head.embedding",
        title="Голова эмбеддинга",
        kind="neural",
        category="neural",
        description="Эмбеддинг по признакам",
        inputs={"features": PortKind.FEATURES},
        outputs={"embeddings": PortKind.EMBEDDINGS},
        budget_ms=3.0,
        defaults={"dim": 128},
    ),
)

#: Типы узлов в порядке реестра.
NODE_TYPES: Final[tuple[str, ...]] = tuple(spec.type for spec in NODE_SPECS)

_SPECS_BY_TYPE: Final[dict[str, NodeSpec]] = {spec.type: spec for spec in NODE_SPECS}

#: Узлы по виду конвейера; общие входят в оба набора.
NODE_TYPES_BY_KIND: Final[dict[str, tuple[str, ...]]] = {
    kind: tuple(spec.type for spec in NODE_SPECS if spec.kind in (kind, "common"))
    for kind in ("computational", "neural")
}


def node_spec(kind: str) -> NodeSpec:
    """Контракт типа узла.

    Raises:
        KeyError: типа нет в реестре.
    """
    return _SPECS_BY_TYPE[kind]


def node_specs_for(kind: str) -> tuple[NodeSpec, ...]:
    """Узлы, доступные для конвейера указанного вида."""
    allowed = NODE_TYPES_BY_KIND.get(kind, NODE_TYPES)
    return tuple(_SPECS_BY_TYPE[name] for name in allowed)


def default_params(kind: str) -> dict[str, Any]:
    """Параметры нового узла: копия умолчаний типа."""
    spec = _SPECS_BY_TYPE.get(kind)
    return dict(spec.defaults) if spec is not None else {}


def node_budget_ms(node: Any) -> float:
    """Бюджет узла: явный из проекта или умолчание типа."""
    explicit = getattr(node, "budget_ms", None)
    if explicit is not None:
        return float(explicit)
    spec = _SPECS_BY_TYPE.get(str(getattr(node, "type", "")))
    return spec.budget_ms if spec is not None else 1.0


def node_input_kind(kind: str, port: str) -> PortKind | None:
    """Тип входного порта узла или ``None``, если порта нет."""
    spec = _SPECS_BY_TYPE.get(kind)
    return spec.inputs.get(port) if spec is not None else None


def node_output_kind(kind: str, port: str) -> PortKind | None:
    """Тип выходного порта узла или ``None``, если порта нет."""
    spec = _SPECS_BY_TYPE.get(kind)
    return spec.outputs.get(port) if spec is not None else None
