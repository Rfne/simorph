"""Тесты URDF-подобного экспорта агента (T5-07, FR-AGT-15)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from simorph.core.agent import (
    HW_SPECS,
    build_drone,
    build_rover,
    export_manifest,
    to_urdf,
    write_urdf,
)
from simorph.core.agent.exporting import SIMORPH_NS


def _parse(text: str) -> ET.Element:
    return ET.fromstring(text)


@pytest.mark.unit
def test_urdf_is_well_formed_xml() -> None:
    root = _parse(to_urdf(build_rover()))

    assert root.tag == "robot"
    assert root.get("name") == "NRTC-01"
    assert root.get("role") == "rover"


@pytest.mark.unit
def test_urdf_contains_all_parts() -> None:
    rover = build_rover()
    root = _parse(to_urdf(rover))

    assert len(root.findall("link")) == len(rover.links) == 7
    assert len(root.findall("joint")) == len(rover.joints) == 6
    assert len(root.findall(f"{{{SIMORPH_NS}}}sensor")) == len(rover.sensors) == 9
    assert len(root.findall(f"{{{SIMORPH_NS}}}actuator")) == len(rover.actuators) == 6
    assert len(root.findall(f"{{{SIMORPH_NS}}}hw")) == len(HW_SPECS) == 35


@pytest.mark.unit
def test_link_geometry_and_inertia() -> None:
    root = _parse(to_urdf(build_rover()))
    links = {item.get("name"): item for item in root.findall("link")}

    base = links["base"]
    mass = base.find("inertial/mass")
    assert mass is not None
    assert float(mass.get("value", "0")) == pytest.approx(45.0)
    assert mass.get("unit") == "kg"

    box = base.find("visual/geometry/box")
    assert box is not None
    assert box.get("size") == "1.2 0.8 0.4"

    wheel = links["wheel_fl"].find("visual/geometry/cylinder")
    assert wheel is not None
    assert float(wheel.get("radius", "0")) == pytest.approx(0.1)
    assert float(wheel.get("length", "0")) == pytest.approx(0.1)


@pytest.mark.unit
def test_joint_links_and_types() -> None:
    root = _parse(to_urdf(build_rover()))
    joints = {item.get("name"): item for item in root.findall("joint")}

    pan = joints["cam_pan_joint"]
    assert pan.get("type") == "revolute"
    assert pan.find("parent").get("link") == "base"  # type: ignore[union-attr]
    assert pan.find("child").get("link") == "cam_pan"  # type: ignore[union-attr]
    assert joints["mast_joint"].get("type") == "prismatic"


@pytest.mark.unit
def test_sensor_and_actuator_extensions() -> None:
    root = _parse(to_urdf(build_rover()))
    sensors = {item.get("id"): item for item in root.findall(f"{{{SIMORPH_NS}}}sensor")}
    actuators = {item.get("id"): item for item in root.findall(f"{{{SIMORPH_NS}}}actuator")}

    stereo = sensors["cam_stereo"]
    assert stereo.get("type") == "stereo_camera"
    assert stereo.get("resolution") == "1280x800"
    assert stereo.get("baseline_m") == "0.12"
    assert stereo.get("link") == "cam_pan"

    drive = actuators["drive_fl"]
    assert drive.get("type") == "drive_axis"
    assert drive.get("joint") == "wheel_fl_joint"
    assert drive.get("max_speed_rad_s") == "20"


@pytest.mark.unit
def test_hw_constants_are_exported_with_units() -> None:
    root = _parse(to_urdf(build_drone()))
    entries = {item.get("id"): item for item in root.findall(f"{{{SIMORPH_NS}}}hw")}

    assert set(entries) == {spec.id for spec in HW_SPECS}
    mass = entries["TC-DYN-01"]
    assert mass.get("group") == "TC-DYN"
    assert mass.get("unit") == "кг"
    assert mass.get("adjustable") == "true"
    assert float(mass.get("value", "0")) == pytest.approx(2.4)
    assert entries["TC-DYN-02"].get("value") == "0.02 0.02 0.035"


@pytest.mark.unit
def test_write_urdf_creates_directories(tmp_path: Path) -> None:
    target = tmp_path / "export" / "nested" / "rover.urdf"

    written = write_urdf(build_rover(), target)

    assert written == target
    assert target.exists()
    assert _parse(target.read_text(encoding="utf-8")).get("name") == "NRTC-01"
    assert target.read_text(encoding="utf-8") == to_urdf(build_rover())


@pytest.mark.unit
def test_manifest_matches_agent() -> None:
    manifest = export_manifest(build_drone())

    assert manifest["agent_id"] == "BVS-01"
    assert manifest["role"] == "drone"
    assert manifest["links"] == 7
    assert manifest["joints"] == 6
    assert manifest["sensors"] == 6
    assert manifest["actuators"] == 7
    assert manifest["hw_constants"] == 35
    assert manifest["root"] == "body"
    assert manifest["valid"] is True
    assert manifest["mass_kg"] == pytest.approx(2.6)
