"""Агент: кинематика, сенсоры, актуаторы, ``P_hw`` и предпросмотр (этап 5).

Пакет не зависит от Qt и от PyBullet на уровне импорта: кинематика,
валидация и экспорт работают и без графической библиотеки, а рендер
предпросмотра подключается лениво — так же, как в сцене мира.
"""

from __future__ import annotations

from simorph.core.agent.actuators import (
    ActuatorCommand,
    ActuatorController,
    ActuatorState,
)
from simorph.core.agent.exporting import export_manifest, to_urdf, write_urdf
from simorph.core.agent.hw import (
    HW_GROUPS,
    HW_SPECS,
    HwSpec,
    hw_groups,
    hw_payload,
    hw_value,
)
from simorph.core.agent.kinematics import (
    GRAVITY,
    KinematicTree,
    build_tree,
    link_inertia,
    link_volume,
)
from simorph.core.agent.preview import (
    AgentPreview,
    PreviewStats,
    collisions,
    initial_camera,
    preview_layout,
    service_zones,
)
from simorph.core.agent.reference import build_drone, build_rover
from simorph.core.agent.sensors import SensorSample, SensorSuite, sensor_types, supported_types
from simorph.core.agent.validation import (
    agent_issues,
    can_launch,
    check_agent,
    sensor_data_rate_mbps,
    thrust_margin,
)

__all__ = [
    "GRAVITY",
    "HW_GROUPS",
    "HW_SPECS",
    "ActuatorCommand",
    "ActuatorController",
    "ActuatorState",
    "AgentPreview",
    "HwSpec",
    "KinematicTree",
    "PreviewStats",
    "SensorSample",
    "SensorSuite",
    "agent_issues",
    "build_drone",
    "build_rover",
    "build_tree",
    "can_launch",
    "check_agent",
    "collisions",
    "export_manifest",
    "hw_groups",
    "hw_payload",
    "hw_value",
    "initial_camera",
    "link_inertia",
    "link_volume",
    "preview_layout",
    "sensor_data_rate_mbps",
    "sensor_types",
    "service_zones",
    "supported_types",
    "thrust_margin",
    "to_urdf",
    "write_urdf",
]
