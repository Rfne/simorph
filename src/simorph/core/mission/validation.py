"""Валидатор миссии и упражнения (T6-06, FR-MSN-06…09).

Проверяются пять классов ошибок:

1. **граф** — ровно один ``start``, цикл только через ``loop``, достижимость;
2. **обязательные входы** — поля, без которых элемент не имеет смысла,
   и непустой список переходов у ветвления;
3. **тайминги** — TTL не меньше длительности, бюджет миссии укладывается
   в TTL и в разумный предел;
4. **geofence и высота** — поза внутри участка и своей зоны, высота
   в допустимом диапазоне;
5. **совместимость сенсора** — элемент наблюдения требует камеру, а не
   дальномер, и сенсор должен быть у агента миссии.

Сообщения и пути совпадают с теми, что уже использует общий валидатор
проекта: панель замечаний открывает ровно тот элемент и то поле.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from simorph.core.issues import Issue, IssueCode, Severity, issue
from simorph.core.mission.elements import element_spec
from simorph.core.mission.graph import build_graph
from simorph.core.mission.timings import mission_budget
from simorph.core.models.agent import AgentSection
from simorph.core.models.exercise import ExerciseSection, Mission, MissionElement
from simorph.core.models.world import WorldSection

__all__ = [
    "MAX_ALTITUDE_M",
    "MIN_ALTITUDE_M",
    "MISSION_BUDGET_LIMIT_S",
    "can_run",
    "exercise_issues",
    "mission_issues",
]

#: Верхняя граница высоты полёта для проверки geofence, м.
MAX_ALTITUDE_M: float = 120.0

#: Нижняя граница высоты: ниже нуля элемент уходит под рельеф.
MIN_ALTITUDE_M: float = 0.0

#: Разумный предел бюджета миссии, с (час).
MISSION_BUDGET_LIMIT_S: float = 3600.0

#: Сенсоры, которые считаются камерой для наблюдения и съёмки.
_CAMERA_SENSORS = frozenset({"camera", "stereo_camera"})

#: Элементы, которым нужна камера.
_CAMERA_ELEMENTS = frozenset({"observe", "capture"})

#: Ссылочные поля, которые проверяются на существование цели.
_REFERENCE_FIELDS = ("next", "zone", "sensor")


def mission_issues(
    mission: Mission,
    *,
    world: WorldSection,
    agents: Sequence[AgentSection],
    prefix: str,
) -> list[Issue]:
    """Все замечания по одной миссии."""
    elements = mission.elements
    issues = _duplicates(
        [(f"{prefix}.elements.{i}.id", item.id) for i, item in enumerate(elements)]
    )
    graph = build_graph(elements)
    index_by_id = {item.id: index for index, item in enumerate(elements)}

    issues += _reference_issues(elements, world, agents, mission.agent_id, prefix)
    issues += _graph_issues(graph, elements, index_by_id, prefix)
    issues += _input_issues(elements, prefix)
    issues += _timing_issues(elements, prefix)
    issues += _geofence_issues(elements, world, prefix)
    issues += _sensor_issues(elements, agents, mission.agent_id, prefix)
    return issues


def exercise_issues(
    exercise: ExerciseSection,
    *,
    world: WorldSection,
    agents: Sequence[AgentSection],
    prefix: str,
) -> list[Issue]:
    """Замечания по упражнению: связи, состав и все миссии."""
    issues: list[Issue] = []
    agent_ids = {agent.agent_id for agent in agents}

    if exercise.world_id != world.world_id:
        issues.append(
            issue(
                IssueCode.DANGLING_REFERENCE,
                f"{prefix}.world_id",
                f"мир {exercise.world_id!r} не совпадает с world.world_id {world.world_id!r}",
            )
        )
    for agent_index, agent_id in enumerate(exercise.agents):
        if agent_id not in agent_ids:
            issues.append(
                issue(
                    IssueCode.DANGLING_REFERENCE,
                    f"{prefix}.agents.{agent_index}",
                    f"агент {agent_id!r} не найден в agents",
                )
            )

    issues += _duplicates(
        [
            (f"{prefix}.missions.{i}.mission_id", item.mission_id)
            for i, item in enumerate(exercise.missions)
        ]
    )

    for mission_index, mission in enumerate(exercise.missions):
        mission_prefix = f"{prefix}.missions.{mission_index}"
        if mission.agent_id not in agent_ids:
            issues.append(
                issue(
                    IssueCode.DANGLING_REFERENCE,
                    f"{mission_prefix}.agent_id",
                    f"агент {mission.agent_id!r} не найден в agents",
                )
            )
        elif mission.agent_id not in exercise.agents:
            issues.append(
                issue(
                    IssueCode.DANGLING_REFERENCE,
                    f"{mission_prefix}.agent_id",
                    f"агент {mission.agent_id!r} не указан в agents упражнения",
                )
            )
        issues += mission_issues(
            mission,
            world=world,
            agents=agents,
            prefix=mission_prefix,
        )
    return issues


def can_run(
    mission: Mission,
    *,
    world: WorldSection,
    agents: Sequence[AgentSection],
) -> bool:
    """Миссию можно запускать, если среди замечаний нет ошибок."""
    return not any(
        item.severity is Severity.ERROR
        for item in mission_issues(mission, world=world, agents=agents, prefix="mission")
    )


# --- классы проверок ------------------------------------------------------


def _reference_issues(
    elements: Sequence[MissionElement],
    world: WorldSection,
    agents: Sequence[AgentSection],
    agent_id: str,
    prefix: str,
) -> list[Issue]:
    """Битые ссылки: переход, зона и сенсор."""
    issues: list[Issue] = []
    element_ids = {item.id for item in elements}
    zone_ids = {zone.id for zone in world.zones}
    sensors = _agent_sensors(agents, agent_id)

    for index, element in enumerate(elements):
        base = f"{prefix}.elements.{index}"
        for target in element.next:
            if target not in element_ids:
                issues.append(
                    issue(
                        IssueCode.DANGLING_REFERENCE,
                        f"{base}.next",
                        f"элемент {target!r} не найден в миссии",
                    )
                )
        if element.zone is not None and element.zone not in zone_ids:
            issues.append(
                issue(
                    IssueCode.DANGLING_REFERENCE,
                    f"{base}.zone",
                    f"зона {element.zone!r} не найдена в world.zones",
                )
            )
        if element.sensor is not None and element.sensor not in sensors:
            issues.append(
                issue(
                    IssueCode.DANGLING_REFERENCE,
                    f"{base}.sensor",
                    f"сенсор {element.sensor!r} не найден у агента {agent_id!r}",
                )
            )
    return issues


def _graph_issues(
    graph: Any,
    elements: Sequence[MissionElement],
    index_by_id: dict[str, int],
    prefix: str,
) -> list[Issue]:
    """Старт, циклы и достижимость."""
    issues: list[Issue] = []
    if len(graph.starts) != 1:
        issues.append(
            issue(
                IssueCode.INVALID_GRAPH,
                f"{prefix}.elements",
                "в миссии должен быть ровно один элемент 'start'",
            )
        )

    offenders = sorted(
        (
            name
            for name in graph.illegal_cycles
            if graph.by_id.get(name) is not None and graph.by_id[name].type != "loop"
        ),
        key=lambda value: index_by_id[value],
    )
    for name in offenders:
        issues.append(
            issue(
                IssueCode.INVALID_GRAPH,
                f"{prefix}.elements.{index_by_id[name]}.next",
                "цикл допустим только через элемент 'loop'",
            )
        )

    if graph.start is not None:
        for index, element in enumerate(elements):
            if element.id not in graph.reachable:
                issues.append(
                    issue(
                        IssueCode.INVALID_GRAPH,
                        f"{prefix}.elements.{index}",
                        f"элемент {element.id!r} недостижим из 'start'",
                        severity=Severity.WARNING,
                    )
                )
    return issues


def _input_issues(elements: Sequence[MissionElement], prefix: str) -> list[Issue]:
    """Обязательные поля и непустые ветви."""
    issues: list[Issue] = []
    for index, element in enumerate(elements):
        base = f"{prefix}.elements.{index}"
        try:
            spec = element_spec(element.type)
        except KeyError:
            continue
        for name in spec.required:
            value = getattr(element, name, None)
            if value is None or (isinstance(value, str) and not value.strip()):
                issues.append(
                    issue(
                        IssueCode.INVALID_VALUE,
                        f"{base}.{name}",
                        f"элемент {element.type!r} требует поле {name!r}",
                    )
                )
        if element.type == "parallel" and len(element.next) < 2:
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"{base}.next",
                    "элемент 'parallel' требует минимум две ветви",
                )
            )
        if spec.terminal and element.next:
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"{base}.next",
                    f"терминальный элемент {element.type!r} не должен иметь переходов",
                )
            )
    return issues


def _timing_issues(elements: Sequence[MissionElement], prefix: str) -> list[Issue]:
    """TTL против длительности и бюджет миссии."""
    issues: list[Issue] = []
    for index, element in enumerate(elements):
        if element.ttl_s is None or element.duration_s is None:
            continue
        if element.ttl_s < element.duration_s:
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"{prefix}.elements.{index}.ttl_s",
                    f"TTL {element.ttl_s:g} с меньше длительности {element.duration_s:g} с",
                )
            )

    budget = mission_budget(list(elements))
    if budget.total_s > MISSION_BUDGET_LIMIT_S:
        issues.append(
            issue(
                IssueCode.INVALID_VALUE,
                f"{prefix}.elements",
                f"бюджет миссии {budget.total_s:.0f} с превышает предел"
                f" {MISSION_BUDGET_LIMIT_S:.0f} с",
                severity=Severity.WARNING,
            )
        )
    if budget.over_ttl:
        tightest = min(
            (index for index, element in enumerate(elements) if element.ttl_s is not None),
            key=lambda index: float(elements[index].ttl_s or 0.0),
        )
        issues.append(
            issue(
                IssueCode.INVALID_VALUE,
                f"{prefix}.elements.{tightest}.ttl_s",
                f"бюджет миссии {budget.total_s:.1f} с превышает TTL {budget.ttl_s:.1f} с",
            )
        )
    return issues


def _geofence_issues(
    elements: Sequence[MissionElement],
    world: WorldSection,
    prefix: str,
) -> list[Issue]:
    """Поза внутри участка и зоны, высота в допустимом диапазоне."""
    issues: list[Issue] = []
    width, depth = world.terrain.size_m
    zones = {zone.id: zone for zone in world.zones}

    for index, element in enumerate(elements):
        pose = element.pose
        if pose is None:
            continue
        base = f"{prefix}.elements.{index}.pose"
        x, y, z = pose
        if not 0.0 <= x <= width or not 0.0 <= y <= depth:
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    base,
                    f"поза ({x:g}, {y:g}, {z:g}) вне участка {width:g}×{depth:g} м",
                )
            )
        if not MIN_ALTITUDE_M <= z <= MAX_ALTITUDE_M:
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    base,
                    f"высота {z:g} м вне диапазона {MIN_ALTITUDE_M:g}…{MAX_ALTITUDE_M:g} м",
                )
            )
        zone = zones.get(element.zone) if element.zone is not None else None
        if zone is not None and not _inside(x, y, zone.polygon):
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    base,
                    f"поза ({x:g}, {y:g}) вне зоны {zone.id!r}",
                )
            )
    return issues


def _sensor_issues(
    elements: Sequence[MissionElement],
    agents: Sequence[AgentSection],
    agent_id: str,
    prefix: str,
) -> list[Issue]:
    """Совместимость типа сенсора с элементом."""
    issues: list[Issue] = []
    sensors = _agent_sensors(agents, agent_id)
    for index, element in enumerate(elements):
        if element.sensor is None or element.type not in _CAMERA_ELEMENTS:
            continue
        sensor = sensors.get(element.sensor)
        if sensor is None or sensor.type in _CAMERA_SENSORS:
            continue
        issues.append(
            issue(
                IssueCode.INVALID_VALUE,
                f"{prefix}.elements.{index}.sensor",
                f"элемент {element.type!r} требует камеру, сенсор {sensor.id!r} — {sensor.type!r}",
            )
        )
    return issues


# --- вспомогательные ------------------------------------------------------


def _duplicates(pairs: Sequence[tuple[str, str]]) -> list[Issue]:
    """Повторяющиеся идентификаторы: путь указывает на повтор."""
    seen: set[str] = set()
    issues: list[Issue] = []
    for path, value in pairs:
        if value in seen:
            issues.append(
                issue(IssueCode.DUPLICATE_ID, path, f"идентификатор {value!r} уже используется")
            )
        seen.add(value)
    return issues


def _agent_sensors(agents: Sequence[AgentSection], agent_id: str) -> dict[str, Any]:
    for agent in agents:
        if agent.agent_id == agent_id:
            return {sensor.id: sensor for sensor in agent.sensors}
    return {}


def _inside(x: float, y: float, polygon: Sequence[tuple[float, float]]) -> bool:
    """Точка внутри полигона: правило чётности пересечений."""
    result = False
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if (y1 > y) != (y2 > y):
            crossing = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if crossing > x:
                result = not result
    return result
