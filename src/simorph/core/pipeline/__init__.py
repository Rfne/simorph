"""Конвейеры: порты, узлы, граф, исполнение и исходники (этап 8).

Пакет не зависит от Qt: те же функции используют CLI ``simorph pipeline``,
вкладка «Конвейеры» и тесты. Реестр типов узлов и портов — единственный
источник контрактов, поэтому валидатор, редактор и исполнитель не
расходятся в названиях.

Швы переноса (``InferenceBackend`` и ``RoiChannel``) отделены от
исполнителя: замена заглушки на ONNX/OpenVINO/NPU-бэкенд не меняет
вызывающий код.
"""

from __future__ import annotations

from simorph.core.pipeline.backends import (
    DEFAULT_INFERENCE_MS,
    InferenceBackend,
    InferenceResult,
    RoiChannel,
    RoiMessage,
    StubInferenceBackend,
    make_backend,
)
from simorph.core.pipeline.execution import PipelineConfig, PipelineRunner
from simorph.core.pipeline.frames import FramePayload
from simorph.core.pipeline.graph import PipelineGraph, build_pipeline_graph
from simorph.core.pipeline.metrics import (
    NodeLatency,
    PipelineMetrics,
    PipelineRunReport,
    percentile,
)
from simorph.core.pipeline.nodes import (
    NODE_SPECS,
    NODE_TYPES,
    NODE_TYPES_BY_KIND,
    NodeSpec,
    default_params,
    node_budget_ms,
    node_input_kind,
    node_output_kind,
    node_spec,
    node_specs_for,
)
from simorph.core.pipeline.ports import (
    PORT_KINDS,
    PORT_TITLES,
    PortKind,
    accepts,
    format_port_ref,
    parse_port_ref,
    port_title,
)
from simorph.core.pipeline.sources import (
    LANGUAGE_BY_SUFFIX,
    SOURCE_SNAPSHOT_VERSION,
    SUPPORTED_SUFFIXES,
    SourceFile,
    SourceStatus,
    SourceTree,
    check_stale,
    external_open,
    language_for,
    load_snapshot,
    save_snapshot,
    scan_sources,
    snapshot_path,
    snapshot_payload,
)
from simorph.core.pipeline.validation import (
    NEURAL_BUDGET_MS,
    SPARSE_BUDGET_MS,
    can_run,
    pipeline_issues,
)

__all__ = [
    "DEFAULT_INFERENCE_MS",
    "LANGUAGE_BY_SUFFIX",
    "NEURAL_BUDGET_MS",
    "NODE_SPECS",
    "NODE_TYPES",
    "NODE_TYPES_BY_KIND",
    "PORT_KINDS",
    "PORT_TITLES",
    "SOURCE_SNAPSHOT_VERSION",
    "SPARSE_BUDGET_MS",
    "SUPPORTED_SUFFIXES",
    "FramePayload",
    "InferenceBackend",
    "InferenceResult",
    "NodeLatency",
    "NodeSpec",
    "PipelineConfig",
    "PipelineGraph",
    "PipelineMetrics",
    "PipelineRunReport",
    "PipelineRunner",
    "PortKind",
    "RoiChannel",
    "RoiMessage",
    "SourceFile",
    "SourceStatus",
    "SourceTree",
    "StubInferenceBackend",
    "accepts",
    "build_pipeline_graph",
    "can_run",
    "check_stale",
    "default_params",
    "external_open",
    "format_port_ref",
    "language_for",
    "load_snapshot",
    "make_backend",
    "node_budget_ms",
    "node_input_kind",
    "node_output_kind",
    "node_spec",
    "node_specs_for",
    "parse_port_ref",
    "percentile",
    "pipeline_issues",
    "port_title",
    "save_snapshot",
    "scan_sources",
    "snapshot_path",
    "snapshot_payload",
]
