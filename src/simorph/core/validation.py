"""Проверка проекта: схема, версия и связность разделов (T1-05, G1-05).

Валидатор возвращает список замечаний, а не падает на первом. Это нужно и
CLI (показать все ошибки сразу), и UI (перейти к каждому объекту).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from pydantic import ValidationError

from simorph.core.agent.validation import agent_issues
from simorph.core.errors import ProjectValidationError, UnsupportedVersionError
from simorph.core.issues import (
    Issue,
    IssueCode,
    Severity,
    has_errors,
    issue,
    issues_from_validation_error,
)
from simorph.core.models.project import ProjectDocument
from simorph.core.schema_version import CURRENT_VERSION, is_current, is_from_future, is_known


def _duplicates(pairs: Sequence[tuple[str, str]]) -> list[Issue]:
    """Находит повторяющиеся идентификаторы; путь указывает на повтор."""
    seen: set[str] = set()
    issues: list[Issue] = []
    for path, value in pairs:
        if value in seen:
            issues.append(
                issue(IssueCode.DUPLICATE_ID, path, f"идентификатор {value!r} уже используется")
            )
        seen.add(value)
    return issues


def _check_world(document: ProjectDocument) -> list[Issue]:
    world = document.world
    issues: list[Issue] = []
    issues += _duplicates([(f"world.zones.{i}.id", zone.id) for i, zone in enumerate(world.zones)])
    issues += _duplicates(
        [(f"world.buildings.{i}.id", item.id) for i, item in enumerate(world.buildings)]
    )
    issues += _duplicates(
        [(f"world.equipment.{i}.id", item.id) for i, item in enumerate(world.equipment)]
    )
    issues += _duplicates([(f"world.trees.{i}.id", item.id) for i, item in enumerate(world.trees)])
    issues += _duplicates([(f"world.water.{i}.id", item.id) for i, item in enumerate(world.water)])

    zone_ids = {zone.id for zone in world.zones}
    for index, building in enumerate(world.buildings):
        if building.zone not in zone_ids:
            issues.append(
                issue(
                    IssueCode.DANGLING_REFERENCE,
                    f"world.buildings.{index}.zone",
                    f"зона {building.zone!r} не найдена в world.zones",
                )
            )

    if world.terrain.size_m[0] <= 0 or world.terrain.size_m[1] <= 0:
        issues.append(
            issue(
                IssueCode.INVALID_RANGE,
                "world.terrain.size_m",
                "размер участка должен быть положительным",
            )
        )
    low, high = world.terrain.elevation_m
    if low > high:
        issues.append(
            issue(
                IssueCode.INVALID_RANGE,
                "world.terrain.elevation_m",
                "нижняя отметка рельефа выше верхней",
            )
        )
    return issues


def _check_agent(agent_index: int, document: ProjectDocument) -> list[Issue]:
    agent = document.agents[agent_index]
    prefix = f"agents.{agent_index}"
    issues: list[Issue] = []
    issues += _duplicates(
        [(f"{prefix}.links.{i}.id", item.id) for i, item in enumerate(agent.links)]
    )
    issues += _duplicates(
        [(f"{prefix}.joints.{i}.id", item.id) for i, item in enumerate(agent.joints)]
    )
    issues += _duplicates(
        [(f"{prefix}.sensors.{i}.id", item.id) for i, item in enumerate(agent.sensors)]
    )
    issues += _duplicates(
        [(f"{prefix}.actuators.{i}.id", item.id) for i, item in enumerate(agent.actuators)]
    )
    issues += _duplicates(
        [(f"{prefix}.links_comm.{i}.id", item.id) for i, item in enumerate(agent.links_comm)]
    )

    link_ids = {link.id for link in agent.links}
    joint_ids = {joint.id for joint in agent.joints}

    for index, joint in enumerate(agent.joints):
        for field_name, target in (("parent", joint.parent), ("child", joint.child)):
            if target not in link_ids:
                issues.append(
                    issue(
                        IssueCode.DANGLING_REFERENCE,
                        f"{prefix}.joints.{index}.{field_name}",
                        f"звено {target!r} не найдено в links",
                    )
                )
    for index, sensor in enumerate(agent.sensors):
        if sensor.link is not None and sensor.link not in link_ids:
            issues.append(
                issue(
                    IssueCode.DANGLING_REFERENCE,
                    f"{prefix}.sensors.{index}.link",
                    f"звено {sensor.link!r} не найдено в links",
                )
            )
    for index, actuator in enumerate(agent.actuators):
        if actuator.joint is not None and actuator.joint not in joint_ids:
            issues.append(
                issue(
                    IssueCode.DANGLING_REFERENCE,
                    f"{prefix}.actuators.{index}.joint",
                    f"сустав {actuator.joint!r} не найден в joints",
                )
            )
    # Физическая согласованность и целостность дерева: ссылки уже проверены
    # выше, поэтому из общего набора берутся только новые классы замечаний.
    for item in agent_issues(agent, index=agent_index):
        if item.code is IssueCode.DANGLING_REFERENCE:
            continue
        issues.append(item)
    return issues


def _check_mission(
    prefix: str,
    elements: Sequence[Any],
    agent_sensor_ids: dict[str, set[str]],
    zone_ids: set[str],
    mission_agent: str,
) -> list[Issue]:
    """Проверяет ссылки внутри миссии и структуру её графа."""
    issues = _duplicates(
        [(f"{prefix}.elements.{i}.id", item.id) for i, item in enumerate(elements)]
    )
    element_ids = {item.id for item in elements}

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
        if element.sensor is not None:
            sensors = agent_sensor_ids.get(mission_agent, set())
            if element.sensor not in sensors:
                issues.append(
                    issue(
                        IssueCode.DANGLING_REFERENCE,
                        f"{base}.sensor",
                        f"сенсор {element.sensor!r} не найден у агента {mission_agent!r}",
                    )
                )

    starts = [index for index, element in enumerate(elements) if element.type == "start"]
    if len(starts) != 1:
        issues.append(
            issue(
                IssueCode.INVALID_GRAPH,
                f"{prefix}.elements",
                "в миссии должен быть ровно один элемент 'start'",
            )
        )

    issues += _mission_cycle_issues(prefix, elements)
    issues += _mission_reachability_issues(prefix, elements)
    return issues


def _mission_cycle_issues(prefix: str, elements: Sequence[Any]) -> list[Issue]:
    """Цикл в миссии допустим только через элемент ``loop``."""
    by_id = {item.id: item for item in elements}
    state: dict[str, int] = {}
    offenders: set[str] = set()

    def visit(node_id: str, path: list[str]) -> None:
        state[node_id] = 1
        path.append(node_id)
        node = by_id.get(node_id)
        if node is not None:
            for target in node.next:
                if target not in by_id:
                    continue
                if state.get(target) == 1:
                    if node.type != "loop" and target in path:
                        offenders.update(path[path.index(target) :])
                elif state.get(target, 0) == 0:
                    visit(target, path)
        path.pop()
        state[node_id] = 2

    for item in elements:
        if state.get(item.id, 0) == 0:
            visit(item.id, [])

    index_by_id = {item.id: index for index, item in enumerate(elements)}
    return [
        issue(
            IssueCode.INVALID_GRAPH,
            f"{prefix}.elements.{index_by_id[name]}.next",
            "цикл допустим только через элемент 'loop'",
        )
        for name in sorted(offenders, key=lambda value: index_by_id[value])
    ]


def _mission_reachability_issues(prefix: str, elements: Sequence[Any]) -> list[Issue]:
    """Элемент, недостижимый из ``start``, — предупреждение, а не ошибка."""
    by_id = {item.id: item for item in elements}
    reached: set[str] = set()
    stack = [item.id for item in elements if item.type == "start"]
    while stack:
        node_id = stack.pop()
        if node_id in reached:
            continue
        reached.add(node_id)
        node = by_id.get(node_id)
        if node is not None:
            stack.extend(target for target in node.next if target in by_id)

    return [
        issue(
            IssueCode.INVALID_GRAPH,
            f"{prefix}.elements.{index}",
            f"элемент {element.id!r} недостижим из 'start'",
            severity=Severity.WARNING,
        )
        for index, element in enumerate(elements)
        if element.id not in reached
    ]


def _check_exercises(document: ProjectDocument) -> list[Issue]:
    issues: list[Issue] = []
    agent_ids = {agent.agent_id for agent in document.agents}
    agent_sensor_ids = {
        agent.agent_id: {sensor.id for sensor in agent.sensors} for agent in document.agents
    }
    zone_ids = {zone.id for zone in document.world.zones}

    for exercise_index, exercise in enumerate(document.exercises):
        prefix = f"exercises.{exercise_index}"
        if exercise.world_id != document.world.world_id:
            issues.append(
                issue(
                    IssueCode.DANGLING_REFERENCE,
                    f"{prefix}.world_id",
                    f"мир {exercise.world_id!r} не совпадает с world.world_id "
                    f"{document.world.world_id!r}",
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
            issues += _check_mission(
                mission_prefix,
                mission.elements,
                agent_sensor_ids,
                zone_ids,
                mission.agent_id,
            )
    return issues


def _check_pipelines(document: ProjectDocument) -> list[Issue]:
    issues: list[Issue] = []
    for pipeline_index, pipeline in enumerate(document.pipelines):
        prefix = f"pipelines.{pipeline_index}"
        issues += _duplicates(
            [(f"{prefix}.nodes.{i}.id", node.id) for i, node in enumerate(pipeline.nodes)]
        )
        node_ids = {node.id for node in pipeline.nodes}
        for edge_index, edge in enumerate(pipeline.edges):
            for field_name, ref in (("from", edge.src), ("to", edge.dst)):
                node_id = ref.split(".", 1)[0]
                if node_id not in node_ids:
                    issues.append(
                        issue(
                            IssueCode.DANGLING_REFERENCE,
                            f"{prefix}.edges.{edge_index}.{field_name}",
                            f"узел {node_id!r} не найден в nodes",
                        )
                    )
        issues += _duplicates(
            [
                (f"{prefix}.edges.{i}", f"{edge.src}->{edge.dst}")
                for i, edge in enumerate(pipeline.edges)
            ]
        )
        issues += _pipeline_cycle_issues(prefix, pipeline)
    return issues


def _pipeline_cycle_issues(prefix: str, pipeline: Any) -> list[Issue]:
    """Граф конвейера обязан быть ациклическим (TRD 5.8)."""
    graph: dict[str, set[str]] = {node.id: set() for node in pipeline.nodes}
    for edge in pipeline.edges:
        src = edge.src.split(".", 1)[0]
        dst = edge.dst.split(".", 1)[0]
        if src in graph and dst in graph:
            graph[src].add(dst)

    state: dict[str, int] = {}
    in_cycle: set[str] = set()

    def visit(node_id: str, path: list[str]) -> None:
        state[node_id] = 1
        path.append(node_id)
        for target in graph.get(node_id, ()):
            if state.get(target) == 1:
                in_cycle.update(path[path.index(target) :])
            elif state.get(target, 0) == 0:
                visit(target, path)
        path.pop()
        state[node_id] = 2

    for node_id in graph:
        if state.get(node_id, 0) == 0:
            visit(node_id, [])

    index_by_id = {node.id: index for index, node in enumerate(pipeline.nodes)}
    return [
        issue(
            IssueCode.INVALID_GRAPH,
            f"{prefix}.nodes.{index_by_id[name]}",
            "цикл в графе конвейера",
        )
        for name in sorted(in_cycle, key=lambda value: index_by_id[value])
    ]


def _check_reports(document: ProjectDocument) -> list[Issue]:
    issues: list[Issue] = []
    pipeline_ids = {pipeline.pipeline_id for pipeline in document.pipelines}
    for report_index, report in enumerate(document.reports):
        for link_index, pipeline_id in enumerate(report.pipeline_ids):
            if pipeline_id not in pipeline_ids:
                issues.append(
                    issue(
                        IssueCode.DANGLING_REFERENCE,
                        f"reports.{report_index}.pipeline_ids.{link_index}",
                        f"конвейер {pipeline_id!r} не найден в pipelines",
                    )
                )
    return issues


def check_document(document: ProjectDocument) -> list[Issue]:
    """Проверяет связность и уникальность внутри валидного документа."""
    issues: list[Issue] = []
    issues += _duplicates(
        [(f"agents.{i}.agent_id", agent.agent_id) for i, agent in enumerate(document.agents)]
    )
    issues += _duplicates(
        [
            (f"exercises.{i}.exercise_id", item.exercise_id)
            for i, item in enumerate(document.exercises)
        ]
    )
    issues += _duplicates(
        [
            (f"pipelines.{i}.pipeline_id", item.pipeline_id)
            for i, item in enumerate(document.pipelines)
        ]
    )
    issues += _duplicates(
        [(f"reports.{i}.report_id", item.report_id) for i, item in enumerate(document.reports)]
    )
    issues += _duplicates(
        [
            (f"profile.parameters.{i}.id", item.id)
            for i, item in enumerate(document.profile.parameters)
        ]
    )

    issues += _check_world(document)
    for agent_index in range(len(document.agents)):
        issues += _check_agent(agent_index, document)
    issues += _check_exercises(document)
    issues += _check_pipelines(document)
    issues += _check_reports(document)

    for index, parameter in enumerate(document.profile.parameters):
        issues += _check_profile_parameter(index, parameter)
    return issues


#: Допустимые целевые векторы по классу параметра (TRD 5.2, 6.1).
_TARGETS_BY_CLASS: dict[str, tuple[str, ...]] = {
    "C": ("P_gen",),
    "E": ("P_sim", "P_hw"),
    "M": ("P_sim",),
}


def _check_profile_parameter(index: int, parameter: Any) -> list[Issue]:
    """Согласованность класса, целевого вектора и ключей проекции.

    Незаполненный класс — предупреждение, а не ошибка: профиль можно
    сохранить и дополнить, но проекция такого профиля запрещена (T3-03).
    """
    prefix = f"profile.parameters.{index}"
    issues: list[Issue] = []
    if parameter.klass is None:
        issues.append(
            issue(
                IssueCode.INVALID_VALUE,
                f"{prefix}.class",
                "класс не указан: проекция профиля заблокирована",
                severity=Severity.WARNING,
            )
        )
        return issues
    if parameter.klass == "N":
        return issues

    allowed = _TARGETS_BY_CLASS.get(parameter.klass, ())
    if parameter.target is None:
        # Класс М может остаться без вектора: часть метаданных в прогон не идёт.
        if parameter.klass in {"C", "E"}:
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"{prefix}.target",
                    f"параметр класса {parameter.klass!r} не проецируется ни в один вектор",
                )
            )
    elif allowed and parameter.target not in allowed:
        issues.append(
            issue(
                IssueCode.INVALID_VALUE,
                f"{prefix}.target",
                f"параметр класса {parameter.klass!r} проецируется в {parameter.target!r},"
                f" допустимо: {', '.join(allowed)}",
            )
        )
    if parameter.target is not None and not parameter.projection:
        issues.append(
            issue(
                IssueCode.INVALID_VALUE,
                f"{prefix}.projection",
                "указан целевой вектор, но не перечислены ключи проекции",
                severity=Severity.WARNING,
            )
        )
    return issues


def parse_document(payload: dict[str, Any]) -> ProjectDocument:
    """Строит документ из словаря.

    Raises:
        ProjectValidationError: документ не соответствует схеме.
    """
    try:
        return ProjectDocument.model_validate(payload)
    except ValidationError as exc:
        issues = issues_from_validation_error(exc)
        raise ProjectValidationError(
            f"документ не прошёл проверку схемы: {len(issues)} замечаний",
            issues=issues,
        ) from exc


def _version_issues(payload: dict[str, Any]) -> list[Issue]:
    raw = payload.get("schema_version", payload.get("version"))
    if not isinstance(raw, str) or not raw.strip():
        return [
            issue(IssueCode.MISSING_FIELD, "schema_version", "поле 'schema_version' обязательно")
        ]
    version = raw.strip()
    if is_from_future(version):
        return [
            issue(
                IssueCode.UNKNOWN_VERSION,
                "schema_version",
                f"версия {version} новее поддерживаемой {CURRENT_VERSION}",
            )
        ]
    if not is_known(version):
        return [issue(IssueCode.UNKNOWN_VERSION, "schema_version", f"неизвестная версия {version}")]
    if not is_current(version):
        return [
            issue(
                IssueCode.UNKNOWN_VERSION,
                "schema_version",
                f"версия {version} требует миграции до {CURRENT_VERSION}",
            )
        ]
    return []


def validate_payload(payload: Any) -> list[Issue]:
    """Полная проверка сырого документа: версия, схема, связность."""
    if not isinstance(payload, dict):
        return [issue(IssueCode.INVALID_TYPE, "", "корень документа должен быть объектом")]

    version_issues = _version_issues(payload)
    if version_issues:
        return version_issues

    try:
        document = ProjectDocument.model_validate(payload)
    except ValidationError as exc:
        return issues_from_validation_error(exc)
    return check_document(document)


def validate_document(document: ProjectDocument) -> list[Issue]:
    """Проверяет только связность уже разобранного документа."""
    return check_document(document)


def require_valid(document: ProjectDocument) -> None:
    """Бросает :class:`ProjectValidationError`, если есть ошибки.

    Raises:
        ProjectValidationError: в документе есть ошибки уровня ``error``.
    """
    issues = check_document(document)
    if has_errors(issues):
        raise ProjectValidationError("документ содержит ошибки", issues=issues)


def describe(issues: Iterable[Issue]) -> str:
    """Короткое текстовое описание замечаний для stderr."""
    return "; ".join(f"{item.path}: {item.message}" for item in issues)


__all__ = [
    "UnsupportedVersionError",
    "check_document",
    "describe",
    "parse_document",
    "require_valid",
    "validate_document",
    "validate_payload",
]
