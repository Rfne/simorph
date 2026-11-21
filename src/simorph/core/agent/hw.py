"""Бортовые константы ``P_hw``: реестр групп ``TC-*`` (T5-04, FR-AGT-12).

Значения живут в схеме агента (``AgentSection.hw``) и сохраняются вместе
с проектом. Здесь — только реестр: идентификатор ``TC-*`` из TRD 5.4.1,
единица измерения, подстраиваемость и путь к полю модели. Реестр нужен,
чтобы отчёт, экспорт и CLI говорили теми же идентификаторами, что и
требования, а не именами полей Python.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from simorph.core.models.agent import AgentSection, HardwareConstants

__all__ = [
    "HW_GROUPS",
    "HW_SPECS",
    "HwSpec",
    "hw_groups",
    "hw_payload",
    "hw_value",
]


@dataclass(frozen=True, slots=True)
class HwSpec:
    """Одна бортовая константа."""

    id: str
    group: str
    title: str
    unit: str
    adjustable: bool
    path: str


#: Группы констант в порядке TRD.
HW_GROUPS: Final[tuple[str, ...]] = ("TC-DYN", "TC-SEN", "TC-CMP", "TC-ENE", "TC-COM")

#: Полный перечень констант TRD 5.4.1: 35 записей.
HW_SPECS: Final[tuple[HwSpec, ...]] = (
    HwSpec("TC-DYN-01", "TC-DYN", "Масса дрона с нагрузкой", "кг", True, "tc_dyn.mass_kg"),
    HwSpec(
        "TC-DYN-02",
        "TC-DYN",
        "Моменты инерции Ixx, Iyy, Izz",
        "кг·м²",
        True,
        "tc_dyn.inertia_kg_m2",
    ),
    HwSpec("TC-DYN-03", "TC-DYN", "Максимальная тяга мотора", "Н", True, "tc_dyn.max_thrust_n"),
    HwSpec(
        "TC-DYN-04",
        "TC-DYN",
        "Передаточная функция мотора",
        "с",
        True,
        "tc_dyn.motor_time_constant_s",
    ),
    HwSpec(
        "TC-DYN-05",
        "TC-DYN",
        "Максимальная линейная скорость",
        "м/с",
        True,
        "tc_dyn.max_linear_speed_mps",
    ),
    HwSpec(
        "TC-DYN-06",
        "TC-DYN",
        "Максимальное линейное ускорение",
        "м/с²",
        True,
        "tc_dyn.max_linear_accel_mps2",
    ),
    HwSpec(
        "TC-DYN-07",
        "TC-DYN",
        "Максимальная угловая скорость",
        "рад/с",
        True,
        "tc_dyn.max_angular_rate_rad_s",
    ),
    HwSpec(
        "TC-DYN-08",
        "TC-DYN",
        "Коэффициент аэродинамического сопротивления",
        "—",
        True,
        "tc_dyn.drag_coefficient",
    ),
    HwSpec("TC-DYN-09", "TC-DYN", "Плечо мотора", "м", False, "tc_dyn.motor_arm_m"),
    HwSpec("TC-SEN-01", "TC-SEN", "Разрешение камеры", "px", True, "tc_sen.camera_resolution"),
    HwSpec("TC-SEN-02", "TC-SEN", "Частота кадров камеры", "Гц", True, "tc_sen.camera_fps"),
    HwSpec("TC-SEN-03", "TC-SEN", "Угол обзора (FOV)", "град", True, "tc_sen.camera_fov_deg"),
    HwSpec("TC-SEN-04", "TC-SEN", "Шум гироскопа", "рад/с", True, "tc_sen.gyro_noise_rad_s"),
    HwSpec("TC-SEN-05", "TC-SEN", "Шум акселерометра", "м/с²", True, "tc_sen.accel_noise_mps2"),
    HwSpec("TC-SEN-06", "TC-SEN", "Задержка сенсоров", "мс", True, "tc_sen.sensor_latency_ms"),
    HwSpec(
        "TC-SEN-07",
        "TC-SEN",
        "Максимальная дальность детекции",
        "м",
        True,
        "tc_sen.detection_range_m",
    ),
    HwSpec(
        "TC-CMP-01", "TC-CMP", "Бюджет CPU на цикл управления", "мс", True, "tc_cmp.cpu_budget_ms"
    ),
    HwSpec(
        "TC-CMP-02", "TC-CMP", "Бюджет GPU на цикл восприятия", "мс", True, "tc_cmp.gpu_budget_ms"
    ),
    HwSpec(
        "TC-CMP-03", "TC-CMP", "Размер буфера кадров", "кадры", True, "tc_cmp.frame_buffer_frames"
    ),
    HwSpec(
        "TC-CMP-04",
        "TC-CMP",
        "Частота контроллера стабилизации",
        "Гц",
        True,
        "tc_cmp.stabilization_rate_hz",
    ),
    HwSpec(
        "TC-CMP-05", "TC-CMP", "Частота контура планирования", "Гц", True, "tc_cmp.planning_rate_hz"
    ),
    HwSpec(
        "TC-CMP-06",
        "TC-CMP",
        "Порог переключения heavy/light",
        "мс",
        True,
        "tc_cmp.heavy_light_threshold_ms",
    ),
    HwSpec("TC-CMP-07", "TC-CMP", "PID roll/pitch", "vec3", True, "tc_cmp.pid_roll_pitch"),
    HwSpec("TC-CMP-08", "TC-CMP", "PID yaw", "vec3", True, "tc_cmp.pid_yaw"),
    HwSpec(
        "TC-CMP-09",
        "TC-CMP",
        "Максимальный размер очереди команд",
        "шт.",
        True,
        "tc_cmp.command_queue_limit",
    ),
    HwSpec("TC-ENE-01", "TC-ENE", "Ёмкость батареи", "Вт·ч", True, "tc_ene.battery_capacity_wh"),
    HwSpec("TC-ENE-02", "TC-ENE", "Потребление в режиме hover", "Вт", True, "tc_ene.hover_power_w"),
    HwSpec(
        "TC-ENE-03",
        "TC-ENE",
        "Потребление при максимальной тяге",
        "Вт",
        True,
        "tc_ene.max_thrust_power_w",
    ),
    HwSpec(
        "TC-ENE-04",
        "TC-ENE",
        "Потребление бортовой ЭВМ",
        "Вт",
        True,
        "tc_ene.onboard_computer_power_w",
    ),
    HwSpec(
        "TC-ENE-05", "TC-ENE", "Порог критического заряда", "%", True, "tc_ene.critical_charge_pct"
    ),
    HwSpec(
        "TC-COM-01",
        "TC-COM",
        "Пропускная способность канала",
        "Мбит/с",
        True,
        "tc_com.bandwidth_mbps",
    ),
    HwSpec("TC-COM-02", "TC-COM", "Базовая задержка канала", "мс", True, "tc_com.base_latency_ms"),
    HwSpec("TC-COM-03", "TC-COM", "MTU пакета", "байт", False, "tc_com.mtu_bytes"),
    HwSpec(
        "TC-COM-04",
        "TC-COM",
        "Размер буфера повторной передачи",
        "шт.",
        True,
        "tc_com.retransmit_buffer",
    ),
    HwSpec(
        "TC-COM-05", "TC-COM", "Порог потери связи (timeout)", "мс", True, "tc_com.link_timeout_ms"
    ),
)


def hw_value(hw: HardwareConstants, spec: HwSpec) -> Any:
    """Значение константы по её пути в модели."""
    group, field = spec.path.split(".", 1)
    return getattr(getattr(hw, group), field)


def hw_payload(agent: AgentSection) -> list[dict[str, Any]]:
    """Константы агента списком: идентификатор, группа, единица и значение."""
    payload: list[dict[str, Any]] = []
    for spec in HW_SPECS:
        value = hw_value(agent.hw, spec)
        payload.append(
            {
                "id": spec.id,
                "group": spec.group,
                "title": spec.title,
                "unit": spec.unit,
                "adjustable": spec.adjustable,
                "value": list(value) if isinstance(value, tuple) else value,
            }
        )
    return payload


def hw_groups(agent: AgentSection) -> dict[str, dict[str, Any]]:
    """Константы по группам: удобно для инспектора и отчёта."""
    groups: dict[str, dict[str, Any]] = {name: {} for name in HW_GROUPS}
    for item in hw_payload(agent):
        groups[str(item["group"])][str(item["id"])] = item["value"]
    return groups
