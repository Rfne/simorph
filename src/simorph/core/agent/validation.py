"""Физическая валидация агента (T5-05, FR-AGT-08, FR-AGT-14).

Проверки отвечают на вопрос «можно ли запускать этого агента», а не
«правильно ли заполнена схема»: схема уже не даст нулевую массу или
отрицательный бюджет. Здесь ловятся несогласованности между разделами:
тяга против веса, инерция, бюджеты CPU/GPU, энергия и канал связи.

Каждая проверка возвращает замечания с путём к полю, поэтому интерфейс
открывает ровно тот параметр, который надо исправить. Ошибка блокирует
запуск, предупреждение — нет.
"""

from __future__ import annotations

import math

from simorph.core.agent.kinematics import KinematicTree, build_tree
from simorph.core.issues import Issue, IssueCode, Severity, issue
from simorph.core.models.agent import Actuator, AgentSection, Sensor

__all__ = ["agent_issues", "can_launch", "check_agent", "sensor_data_rate_mbps"]

#: Запас по тяге: тяга должна превышать вес хотя бы на 10 %.
THRUST_MARGIN: float = 1.1

#: Допустимое расхождение массы звеньев и TC-DYN-01.
MASS_TOLERANCE: float = 0.25

#: Минимальное время работы от батареи, мин.
MIN_RUNTIME_MIN: float = 5.0

#: Сенсоры, которые обязаны быть закреплены на звене.
LINKED_SENSORS = frozenset(
    {"imu9", "gps", "stereo_camera", "camera", "distance", "angle", "angular_rate"}
)

#: Сжатие видеопотока: сырой кадр в канал не уходит (TRD 8.2).
COMPRESSION_FACTOR: float = 20.0


def sensor_data_rate_mbps(sensor: Sensor) -> float:
    """Оценка сжатого потока данных сенсора, Мбит/с."""
    if sensor.type not in {"camera", "stereo_camera"} or sensor.resolution is None:
        return 0.0
    width, height = sensor.resolution
    depth = int(sensor.bit_depth or 8)
    frames = 2 if sensor.type == "stereo_camera" else 1
    raw = width * height * depth * frames * float(sensor.rate_hz) / 1e6
    return raw / COMPRESSION_FACTOR


def check_agent(agent: AgentSection, *, index: int = 0) -> list[Issue]:
    """Полный набор физических замечаний по агенту."""
    prefix = f"agents.{index}"
    tree = build_tree(agent)
    issues: list[Issue] = []

    for message in tree.errors:
        issues.append(issue(IssueCode.INVALID_GRAPH, f"{prefix}.joints", message))

    issues += _mass_issues(agent, tree, prefix)
    issues += _sensor_issues(agent, prefix)
    issues += _actuator_issues(agent, prefix)
    issues += _thrust_issues(agent, tree, prefix)
    issues += _budget_issues(agent, prefix)
    issues += _energy_issues(agent, prefix)
    issues += _comm_issues(agent, prefix)
    return issues


def agent_issues(agent: AgentSection, *, index: int = 0) -> list[Issue]:
    """Синоним :func:`check_agent` для вызова из общего валидатора."""
    return check_agent(agent, index=index)


def can_launch(agent: AgentSection, *, index: int = 0) -> bool:
    """Запуск разрешён, если среди замечаний нет ошибок."""
    return not any(item.severity is Severity.ERROR for item in check_agent(agent, index=index))


def _mass_issues(agent: AgentSection, tree: KinematicTree, prefix: str) -> list[Issue]:
    issues: list[Issue] = []
    if not agent.links:
        issues.append(
            issue(IssueCode.INVALID_VALUE, f"{prefix}.links", "у агента нет звеньев: масса нулевая")
        )
        return issues
    if tree.total_mass_kg <= 0.0:
        issues.append(
            issue(
                IssueCode.INVALID_VALUE, f"{prefix}.links", "суммарная масса агента должна быть > 0"
            )
        )
    inertia = tree.inertia_kg_m2
    for axis, value in zip(("Ixx", "Iyy", "Izz"), inertia, strict=True):
        if value <= 0.0:
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"{prefix}.links",
                    f"момент инерции {axis} не положителен: {value:.4f}",
                )
            )
    declared = float(agent.hw.tc_dyn.mass_kg)
    if tree.total_mass_kg > 0.0:
        mismatch = abs(declared - tree.total_mass_kg) / tree.total_mass_kg
        if mismatch > MASS_TOLERANCE:
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"{prefix}.hw.tc_dyn.mass_kg",
                    f"TC-DYN-01 ({declared:.2f} кг) расходится с массой звеньев"
                    f" ({tree.total_mass_kg:.2f} кг) более чем на {MASS_TOLERANCE:.0%}",
                    severity=Severity.WARNING,
                )
            )
    declared_inertia = agent.hw.tc_dyn.inertia_kg_m2
    if any(value <= 0.0 for value in declared_inertia):
        issues.append(
            issue(
                IssueCode.INVALID_VALUE,
                f"{prefix}.hw.tc_dyn.inertia_kg_m2",
                "TC-DYN-02 содержит неположительный момент инерции",
            )
        )
    return issues


def _sensor_issues(agent: AgentSection, prefix: str) -> list[Issue]:
    issues: list[Issue] = []
    link_ids = {link.id for link in agent.links}
    for position, sensor in enumerate(agent.sensors):
        base = f"{prefix}.sensors.{position}"
        if sensor.link is None:
            if sensor.type in LINKED_SENSORS:
                issues.append(
                    issue(
                        IssueCode.INVALID_VALUE,
                        f"{base}.link",
                        f"сенсор {sensor.id!r} не закреплён на звене",
                        severity=Severity.WARNING,
                    )
                )
        elif sensor.link not in link_ids:
            issues.append(
                issue(
                    IssueCode.DANGLING_REFERENCE,
                    f"{base}.link",
                    f"звено {sensor.link!r} не найдено в links",
                )
            )
        if (
            sensor.type in {"camera", "stereo_camera"}
            and sensor.rate_hz > agent.hw.tc_cmp.stabilization_rate_hz
        ):
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"{base}.rate_hz",
                    f"частота сенсора {sensor.rate_hz:.0f} Гц выше частоты контура"
                    f" стабилизации {agent.hw.tc_cmp.stabilization_rate_hz:.0f} Гц",
                    severity=Severity.WARNING,
                )
            )
    return issues


def _actuator_issues(agent: AgentSection, prefix: str) -> list[Issue]:
    issues: list[Issue] = []
    joint_ids = {joint.id for joint in agent.joints}
    for position, actuator in enumerate(agent.actuators):
        base = f"{prefix}.actuators.{position}"
        if actuator.joint is not None and actuator.joint not in joint_ids:
            issues.append(
                issue(
                    IssueCode.DANGLING_REFERENCE,
                    f"{base}.joint",
                    f"сустав {actuator.joint!r} не найден в joints",
                )
            )
        if actuator.joints is not None:
            for name in actuator.joints:
                if name not in joint_ids:
                    issues.append(
                        issue(
                            IssueCode.DANGLING_REFERENCE,
                            f"{base}.joints",
                            f"сустав {name!r} не найден в joints",
                        )
                    )
        if actuator.type == "thrust_vector" and (actuator.thrust_max_n or 0.0) <= 0.0:
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"{base}.thrust_max_n",
                    "тяга реактивного вектора должна быть положительной",
                )
            )
    if agent.role == "rover" and not _of_type(agent.actuators, "drive_axis"):
        issues.append(
            issue(
                IssueCode.INVALID_VALUE,
                f"{prefix}.actuators",
                "у ровера нет ведущей оси: движение невозможно",
                severity=Severity.WARNING,
            )
        )
    return issues


def _thrust_issues(agent: AgentSection, tree: KinematicTree, prefix: str) -> list[Issue]:
    issues: list[Issue] = []
    motors = _of_type(agent.actuators, "thrust_vector")
    if agent.role == "drone":
        if not motors:
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"{prefix}.actuators",
                    "у БВС нет реактивных актуаторов: тяга отсутствует",
                )
            )
            return issues
        per_motor = [float(item.thrust_max_n or agent.hw.tc_dyn.max_thrust_n) for item in motors]
        total_thrust = sum(per_motor)
        required = tree.weight_n * THRUST_MARGIN
        if total_thrust < required:
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"{prefix}.hw.tc_dyn.max_thrust_n",
                    f"суммарная тяга {total_thrust:.2f} Н не превышает вес"
                    f" {tree.weight_n:.2f} Н с запасом {THRUST_MARGIN:.0%}",
                )
            )
        if tree.total_mass_kg > 0.0:
            declared = float(agent.hw.tc_dyn.max_thrust_n) * len(motors)
            if declared < required:
                issues.append(
                    issue(
                        IssueCode.INVALID_VALUE,
                        f"{prefix}.hw.tc_dyn.max_thrust_n",
                        f"TC-DYN-03 на {len(motors)} моторов даёт {declared:.2f} Н —"
                        f" меньше веса {tree.weight_n:.2f} Н с запасом",
                    )
                )
    return issues


def _budget_issues(agent: AgentSection, prefix: str) -> list[Issue]:
    issues: list[Issue] = []
    hw = agent.hw
    cycle_ms = 1000.0 / hw.tc_cmp.stabilization_rate_hz
    if hw.tc_cmp.cpu_budget_ms > cycle_ms:
        issues.append(
            issue(
                IssueCode.INVALID_VALUE,
                f"{prefix}.hw.tc_cmp.cpu_budget_ms",
                f"TC-CMP-01 ({hw.tc_cmp.cpu_budget_ms:.2f} мс) больше цикла управления"
                f" ({cycle_ms:.2f} мс при TC-CMP-04)",
            )
        )
    frame_ms = 1000.0 / hw.tc_sen.camera_fps
    if hw.tc_cmp.gpu_budget_ms > frame_ms:
        issues.append(
            issue(
                IssueCode.INVALID_VALUE,
                f"{prefix}.hw.tc_cmp.gpu_budget_ms",
                f"TC-CMP-02 ({hw.tc_cmp.gpu_budget_ms:.2f} мс) больше периода кадра"
                f" ({frame_ms:.2f} мс при TC-SEN-02)",
                severity=Severity.WARNING,
            )
        )
    if hw.tc_cmp.heavy_light_threshold_ms > hw.tc_cmp.gpu_budget_ms:
        issues.append(
            issue(
                IssueCode.INVALID_VALUE,
                f"{prefix}.hw.tc_cmp.heavy_light_threshold_ms",
                "TC-CMP-06 больше бюджета GPU: переключение heavy/light не сработает",
                severity=Severity.WARNING,
            )
        )
    cameras = [item for item in agent.sensors if item.type in {"camera", "stereo_camera"}]
    for position, sensor in enumerate(agent.sensors):
        if sensor.type not in {"camera", "stereo_camera"}:
            continue
        rate = float(sensor.rate_hz)
        if rate > hw.tc_cmp.planning_rate_hz * 10.0:
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"{prefix}.sensors.{position}.rate_hz",
                    f"сенсор {sensor.id!r} даёт {rate:.0f} Гц при контуре планирования"
                    f" {hw.tc_cmp.planning_rate_hz:.0f} Гц: очередь команд переполнится",
                    severity=Severity.WARNING,
                )
            )
    if cameras:
        declared = tuple(hw.tc_sen.camera_resolution)
        actual = cameras[0].resolution
        if actual is not None and tuple(actual) != declared:
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"{prefix}.hw.tc_sen.camera_resolution",
                    f"TC-SEN-01 {declared[0]}×{declared[1]} не совпадает"
                    f" с камерой {actual[0]}×{actual[1]}",
                    severity=Severity.WARNING,
                )
            )
    return issues


def _energy_issues(agent: AgentSection, prefix: str) -> list[Issue]:
    issues: list[Issue] = []
    hw = agent.hw
    total_power = hw.tc_ene.hover_power_w + hw.tc_ene.onboard_computer_power_w
    runtime_min = hw.tc_ene.battery_capacity_wh / total_power * 60.0
    if runtime_min < MIN_RUNTIME_MIN:
        issues.append(
            issue(
                IssueCode.INVALID_VALUE,
                f"{prefix}.hw.tc_ene.battery_capacity_wh",
                f"батареи хватает на {runtime_min:.1f} мин — меньше {MIN_RUNTIME_MIN:.0f} мин",
                severity=Severity.WARNING,
            )
        )
    if hw.tc_ene.max_thrust_power_w < hw.tc_ene.hover_power_w:
        issues.append(
            issue(
                IssueCode.INVALID_VALUE,
                f"{prefix}.hw.tc_ene.max_thrust_power_w",
                "TC-ENE-03 меньше TC-ENE-02: потребление при максимальной тяге ниже hover",
                severity=Severity.WARNING,
            )
        )
    return issues


def _comm_issues(agent: AgentSection, prefix: str) -> list[Issue]:
    issues: list[Issue] = []
    bandwidth = float(agent.hw.tc_com.bandwidth_mbps)
    for link in agent.links_comm:
        if link.bandwidth_mbps > bandwidth:
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"{prefix}.links_comm",
                    f"канал {link.id!r} шире TC-COM-01: {link.bandwidth_mbps:.1f}"
                    f" против {bandwidth:.1f} Мбит/с",
                    severity=Severity.WARNING,
                )
            )
    demand = sum(sensor_data_rate_mbps(item) for item in agent.sensors)
    if demand > bandwidth:
        issues.append(
            issue(
                IssueCode.INVALID_VALUE,
                f"{prefix}.hw.tc_com.bandwidth_mbps",
                f"сенсоры требуют {demand:.1f} Мбит/с при TC-COM-01 {bandwidth:.1f} Мбит/с",
                severity=Severity.WARNING,
            )
        )
    return issues


def _of_type(actuators: list[Actuator], kind: str) -> list[Actuator]:
    return [item for item in actuators if item.type == kind]


def thrust_margin(agent: AgentSection) -> float:
    """Отношение суммарной тяги к весу: 1.0 — ровно вес."""
    tree = build_tree(agent)
    if tree.weight_n <= 0.0:
        return math.inf
    motors = _of_type(agent.actuators, "thrust_vector")
    total = sum(float(item.thrust_max_n or 0.0) for item in motors)
    return total / tree.weight_n
