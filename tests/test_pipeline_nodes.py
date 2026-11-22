"""Тесты реестра узлов конвейера (T8-02, T8-03)."""

from __future__ import annotations

import pytest

from simorph.core.models.pipeline import PipelineNode
from simorph.core.pipeline import (
    NODE_SPECS,
    NODE_TYPES,
    NODE_TYPES_BY_KIND,
    default_params,
    node_budget_ms,
    node_input_kind,
    node_output_kind,
    node_spec,
    node_specs_for,
)


def test_registry_types_are_unique_and_ordered() -> None:
    assert len(NODE_TYPES) == len(set(NODE_TYPES))
    assert tuple(spec.type for spec in NODE_SPECS) == NODE_TYPES
    assert len(NODE_TYPES) == 16


def test_every_spec_declares_ports() -> None:
    for spec in NODE_SPECS:
        assert spec.inputs or spec.outputs, spec.type
        assert spec.budget_ms > 0.0, spec.type
        assert spec.title.strip(), spec.type


def test_sources_and_sinks_flags() -> None:
    source = node_spec("source.frame")
    sink = node_spec("sink.events")
    middle = node_spec("rectify")

    assert source.is_source is True
    assert source.is_sink is False
    assert sink.is_source is False
    assert sink.is_sink is True
    assert middle.is_source is False
    assert middle.is_sink is False


def test_common_nodes_are_available_in_both_kinds() -> None:
    for kind in ("computational", "neural"):
        assert "source.frame" in NODE_TYPES_BY_KIND[kind]
        assert "sink.events" in NODE_TYPES_BY_KIND[kind]

    assert "dense.stereo" in NODE_TYPES_BY_KIND["computational"]
    assert "dense.stereo" not in NODE_TYPES_BY_KIND["neural"]
    assert "inference" in NODE_TYPES_BY_KIND["neural"]
    assert "inference" not in NODE_TYPES_BY_KIND["computational"]


def test_node_specs_for_unknown_kind_returns_all() -> None:
    assert node_specs_for("ros2") == NODE_SPECS


def test_unknown_type_raises_key_error() -> None:
    with pytest.raises(KeyError):
        node_spec("missing.type")


def test_default_params_are_independent_copies() -> None:
    first = default_params("source.frame")
    second = default_params("source.frame")
    first["camera"] = "other"

    assert second["camera"] == "cam_stereo"
    assert default_params("missing.type") == {}


def test_inference_budget_fits_neural_target() -> None:
    assert node_spec("inference").budget_ms == pytest.approx(380.0)
    assert node_spec("inference").budget_ms <= 400.0


def test_explicit_budget_overrides_default() -> None:
    node = PipelineNode(id="inf", type="inference", budget_ms=12.5)
    assert node_budget_ms(node) == pytest.approx(12.5)

    plain = PipelineNode(id="inf", type="inference")
    assert node_budget_ms(plain) == pytest.approx(380.0)
    assert node_budget_ms("не узел") == pytest.approx(1.0)


def test_port_lookup_reports_unknown_ports() -> None:
    assert node_output_kind("source.frame", "frame").value == "frame.color"
    assert node_input_kind("rectify", "stereo").value == "frame.stereo"
    assert node_input_kind("rectify", "missing") is None
    assert node_output_kind("missing.type", "frame") is None


def test_required_params_have_defaults() -> None:
    for spec in NODE_SPECS:
        for name in spec.required_params:
            assert name in spec.defaults, f"{spec.type}: нет умолчания для {name}"
