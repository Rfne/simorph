"""Экспорт агента в URDF-подобный файл (T5-07, FR-AGT-15).

URDF не умеет ни сенсоров, ни бортовых констант, поэтому файл —
«URDF-подобный»: стандартные ``link``/``joint`` с габаритами, массой
и инерцией, плюс расширения ``simorph:sensor``, ``simorph:actuator``
и ``simorph:hw`` в отдельном пространстве имён.

Единицы записываются явно в атрибуте ``unit``: файл читают и парсер
PyBullet, и человек, а «просто число» без единицы — источник ошибок
на целевом борту.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from xml.dom import minidom

from simorph.core.agent.hw import HW_SPECS, hw_value
from simorph.core.agent.kinematics import build_tree, link_inertia
from simorph.core.models.agent import AgentSection, Link

__all__ = ["export_manifest", "to_urdf", "write_urdf"]

#: Пространство имён расширений simorph.
SIMORPH_NS = "https://simorph.local/urdf/agent"

#: Отображение типа сустава схемы в URDF.
_JOINT_TYPES = {
    "fixed": "fixed",
    "revolute": "revolute",
    "continuous": "continuous",
    "prismatic": "prismatic",
}


def _fmt(value: float) -> str:
    """Число без хвостовых нулей: стабильный вид файла."""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _geometry(link: Link) -> ET.Element:
    geometry = ET.Element("geometry")
    if link.type == "sphere":
        ET.SubElement(geometry, "sphere", {"radius": _fmt(link.size_m[0] / 2.0)})
    elif link.type == "cylinder":
        ET.SubElement(
            geometry,
            "cylinder",
            {"radius": _fmt(link.size_m[0] / 2.0), "length": _fmt(link.size_m[2])},
        )
    else:
        ET.SubElement(
            geometry,
            "box",
            {"size": " ".join(_fmt(value) for value in link.size_m)},
        )
    return geometry


def _link_element(link: Link) -> ET.Element:
    element = ET.Element("link", {"name": link.id})
    inertial = ET.SubElement(element, "inertial")
    ET.SubElement(inertial, "mass", {"value": _fmt(link.mass_kg), "unit": "kg"})
    inertia = link_inertia(link)
    ET.SubElement(
        inertial,
        "inertia",
        {
            "ixx": _fmt(inertia[0]),
            "iyy": _fmt(inertia[1]),
            "izz": _fmt(inertia[2]),
            "unit": "kg*m^2",
        },
    )
    ET.SubElement(
        inertial,
        "origin",
        {"xyz": " ".join(_fmt(value) for value in link.pose), "unit": "m"},
    )
    visual = ET.SubElement(element, "visual")
    visual.append(_geometry(link))
    ET.SubElement(visual, "material", {"name": f"mat_{link.id}"})
    return element


def _joint_element(joint: Any) -> ET.Element:
    element = ET.Element(
        "joint",
        {"name": joint.id, "type": _JOINT_TYPES.get(joint.type, "fixed")},
    )
    ET.SubElement(element, "parent", {"link": joint.parent})
    ET.SubElement(element, "child", {"link": joint.child})
    ET.SubElement(element, "axis", {"xyz": " ".join(_fmt(value) for value in joint.axis)})
    return element


def _sensor_element(sensor: Any) -> ET.Element:
    attributes = {
        "id": sensor.id,
        "type": sensor.type,
        "rate_hz": _fmt(sensor.rate_hz),
        "unit": "Hz",
    }
    if sensor.link is not None:
        attributes["link"] = sensor.link
    if sensor.resolution is not None:
        attributes["resolution"] = f"{sensor.resolution[0]}x{sensor.resolution[1]}"
    for name in (
        "bit_depth",
        "mode",
        "baseline_m",
        "fov_deg",
        "range_m",
        "noise",
        "latency_ms",
        "accuracy_m",
        "channels",
        "levels",
        "active_level",
        "sync",
    ):
        value = getattr(sensor, name, None)
        if value is not None:
            attributes[name] = _fmt(value) if isinstance(value, float) else str(value)
    if sensor.range_deg is not None:
        attributes["range_deg"] = " ".join(_fmt(value) for value in sensor.range_deg)
    return ET.Element(f"{{{SIMORPH_NS}}}sensor", attributes)


def _actuator_element(actuator: Any) -> ET.Element:
    attributes = {"id": actuator.id, "type": actuator.type}
    for name in (
        "joint",
        "max_speed_rad_s",
        "max_speed_mps",
        "max_torque_nm",
        "max_force_n",
        "response_ms",
        "channels",
        "max_speed_dps",
        "thrust_max_n",
        "gimbal_deg",
        "levels",
    ):
        value = getattr(actuator, name, None)
        if value is not None:
            attributes[name] = _fmt(value) if isinstance(value, float) else str(value)
    if actuator.range_deg is not None:
        attributes["range_deg"] = " ".join(_fmt(value) for value in actuator.range_deg)
    if actuator.range_m is not None:
        attributes["range_m"] = " ".join(_fmt(value) for value in actuator.range_m)
    if actuator.joints:
        attributes["joints"] = " ".join(actuator.joints)
    if actuator.lengths_m:
        attributes["lengths_m"] = " ".join(_fmt(value) for value in actuator.lengths_m)
    return ET.Element(f"{{{SIMORPH_NS}}}actuator", attributes)


def _hw_element(agent: AgentSection) -> list[ET.Element]:
    elements: list[ET.Element] = []
    for spec in HW_SPECS:
        value = hw_value(agent.hw, spec)
        text = " ".join(_fmt(item) for item in value) if isinstance(value, tuple) else _fmt(value)
        elements.append(
            ET.Element(
                f"{{{SIMORPH_NS}}}hw",
                {
                    "id": spec.id,
                    "group": spec.group,
                    "title": spec.title,
                    "unit": spec.unit,
                    "adjustable": "true" if spec.adjustable else "false",
                    "value": text,
                },
            )
        )
    return elements


def to_urdf(agent: AgentSection) -> str:
    """URDF-подобный XML агента."""
    tree = build_tree(agent)
    root = ET.Element(
        "robot",
        {
            "name": agent.agent_id,
            "role": agent.role,
            "xmlns:simorph": SIMORPH_NS,
        },
    )
    for link_id in tree.order or sorted(tree.links):
        root.append(_link_element(tree.links[link_id]))
    for joint in agent.joints:
        root.append(_joint_element(joint))
    for sensor in agent.sensors:
        root.append(_sensor_element(sensor))
    for actuator in agent.actuators:
        root.append(_actuator_element(actuator))
    for element in _hw_element(agent):
        root.append(element)
    raw = ET.tostring(root, encoding="utf-8")
    pretty = minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8")
    return pretty.decode("utf-8")


def write_urdf(agent: AgentSection, path: Path) -> Path:
    """Пишет файл и создаёт каталог назначения."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_urdf(agent), encoding="utf-8", newline="\n")
    return path


def export_manifest(agent: AgentSection) -> dict[str, Any]:
    """Сводка экспорта: что и в каком количестве попало в файл."""
    tree = build_tree(agent)
    return {
        "agent_id": agent.agent_id,
        "role": agent.role,
        "links": len(agent.links),
        "joints": len(agent.joints),
        "sensors": len(agent.sensors),
        "actuators": len(agent.actuators),
        "hw_constants": len(HW_SPECS),
        "mass_kg": round(tree.total_mass_kg, 4),
        "root": tree.root,
        "valid": tree.valid,
    }
