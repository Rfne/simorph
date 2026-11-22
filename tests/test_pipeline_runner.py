"""Тесты исполнения конвейера: кадры, задержки, очередь и швы (T8-02…T8-07)."""

from __future__ import annotations

import pytest

from simorph.core.errors import ProjectError
from simorph.core.models.pipeline import PipelineEdge, PipelineNode, PipelineSection
from simorph.core.pipeline import (
    InferenceResult,
    PipelineConfig,
    PipelineRunner,
    RoiChannel,
    RoiMessage,
    StubInferenceBackend,
    make_backend,
)
from simorph.core.pipeline.frames import FramePayload


def _edge(src: str, dst: str) -> PipelineEdge:
    return PipelineEdge.model_validate({"from": src, "to": dst})


def computational() -> PipelineSection:
    """Разреженный контур: 4.8 мс плана на кадр (G8-02)."""
    return PipelineSection(
        schema_version="1.0",
        pipeline_id="PIPE-CMP-01",
        kind="computational",
        nodes=[
            PipelineNode(id="src", type="source.stereo", params={"camera": "cam_stereo"}),
            PipelineNode(id="rect", type="rectify"),
            PipelineNode(id="det", type="sparse.detect"),
            PipelineNode(id="trk", type="sparse.track"),
            PipelineNode(id="sp", type="sparse.stereo"),
            PipelineNode(id="sink", type="sink.events", params={"topic": "events"}),
        ],
        edges=[
            _edge("src.stereo", "rect.stereo"),
            _edge("rect.stereo", "det.stereo"),
            _edge("det.features", "trk.features"),
            _edge("trk.tracks", "sp.tracks"),
            _edge("sp.points", "sink.events"),
        ],
    )


def neural() -> PipelineSection:
    return PipelineSection(
        schema_version="1.0",
        pipeline_id="PIPE-NN-01",
        kind="neural",
        nodes=[
            PipelineNode(id="src", type="source.frame", params={"camera": "cam_stereo"}),
            PipelineNode(id="pre", type="preprocess"),
            PipelineNode(id="inf", type="inference"),
            PipelineNode(id="nms", type="postprocess.nms"),
            PipelineNode(id="sink", type="sink.events", params={"topic": "events"}),
        ],
        edges=[
            _edge("src.frame", "pre.frame"),
            _edge("pre.frame", "inf.frame"),
            _edge("inf.detections", "nms.detections"),
            _edge("nms.detections", "sink.events"),
        ],
    )


def roi_pipeline() -> PipelineSection:
    return PipelineSection(
        schema_version="1.0",
        pipeline_id="PIPE-ROI",
        kind="computational",
        nodes=[
            PipelineNode(id="src", type="source.frame", params={"camera": "cam_stereo"}),
            PipelineNode(id="roi", type="roi"),
            PipelineNode(id="sink", type="sink.events", params={"topic": "events"}),
        ],
        edges=[_edge("src.frame", "roi.frame"), _edge("roi.roi", "sink.events")],
    )


def test_default_run_completes_120_frames() -> None:
    runner = PipelineRunner(computational())

    report = runner.run()

    assert report.ok is True
    assert report.state == "finished"
    assert report.reason is None
    assert report.metrics.frames_attempted == 120
    assert report.metrics.frames_completed == 120
    assert report.metrics.frames_dropped == 0
    assert report.metrics.monotonic is True
    assert runner.state == "finished"
    assert runner.report is report
    assert report.events


def test_sparse_plan_fits_five_ms() -> None:
    runner = PipelineRunner(computational())
    report = runner.run()

    assert report.metrics.critical_path_ms == pytest.approx(4.8)
    assert report.metrics.critical_path_ms <= 5.0
    assert report.metrics.total_work_ms == pytest.approx(4.8)
    assert set(report.metrics.nodes) == {"src", "rect", "det", "trk", "sp", "sink"}
    assert all(item.count == 120 for item in report.metrics.nodes.values())


def test_inference_p95_fits_neural_budget() -> None:
    runner = PipelineRunner(neural())

    report = runner.run()

    inference = report.metrics.nodes["inf"]
    assert inference.p50_ms == pytest.approx(380.0)
    assert inference.p95_ms == pytest.approx(380.0)
    assert inference.p95_ms <= 400.0
    assert report.metrics.critical_path_ms == pytest.approx(387.3)


def test_custom_backend_latency_reaches_metrics() -> None:
    backend = StubInferenceBackend(latency_ms=25.0, detections=2, embeddings=3)
    runner = PipelineRunner(neural(), PipelineConfig(frames=10, backend=backend))

    report = runner.run()

    assert report.metrics.nodes["inf"].p95_ms == pytest.approx(25.0)
    assert report.config["backend"] == "stub"


def test_backend_swap_does_not_change_caller_code() -> None:
    class SlowBackend:
        name = "custom"

        def infer(self, frame: FramePayload) -> InferenceResult:
            return InferenceResult(
                detections=1, embeddings=0, latency_ms=50.0 + frame.frame_id, backend=self.name
            )

    runner = PipelineRunner(neural(), PipelineConfig(frames=5, backend=SlowBackend()))
    report = runner.run()

    assert report.ok is True
    assert report.metrics.nodes["inf"].max_ms == pytest.approx(55.0)
    assert make_backend("onnx").name == "onnx"
    assert make_backend("unknown").name == "unknown"


def test_run_is_deterministic() -> None:
    first_clock = iter(range(0, 10_000_000, 1_000))
    first = PipelineRunner(
        computational(), PipelineConfig(frames=3), now_ns=lambda: next(first_clock)
    )
    second_clock = iter(range(0, 10_000_000, 1_000))
    second = PipelineRunner(
        computational(), PipelineConfig(frames=3), now_ns=lambda: next(second_clock)
    )

    assert first.run().events == second.run().events


def test_queue_grows_and_waits_without_dropping_by_default() -> None:
    config = PipelineConfig(frames=20, fps=1000.0)
    runner = PipelineRunner(computational(), config)

    report = runner.run()

    assert report.metrics.frames_completed == 20
    assert report.metrics.frames_dropped == 0
    assert report.metrics.queue_max_depth >= 2
    assert report.metrics.queue_wait_ms > 0.0


def test_realtime_drop_discards_frames_only_when_allowed() -> None:
    config = PipelineConfig(frames=20, fps=1000.0, max_queue=1, realtime_drop=True)
    runner = PipelineRunner(computational(), config)

    report = runner.run()

    assert report.metrics.frames_dropped > 0
    assert report.metrics.frames_attempted == report.metrics.frames_completed
    assert report.metrics.frames_completed + report.metrics.frames_dropped == config.frames
    assert any(event["kind"] == "frame_dropped" for event in report.events)


def test_invalid_pipeline_refuses_to_run() -> None:
    broken = computational().model_copy(
        update={
            "edges": [_edge("src.stereo", "det.features")],
        }
    )
    runner = PipelineRunner(broken)

    with pytest.raises(ProjectError, match="не проходит валидацию"):
        runner.run()
    assert runner.state == "idle"


def test_runtime_failure_becomes_interrupted_report() -> None:
    class BrokenBackend:
        name = "broken"

        def infer(self, frame: FramePayload) -> InferenceResult:
            raise RuntimeError("модель упала")

    runner = PipelineRunner(neural(), PipelineConfig(frames=3, backend=BrokenBackend()))

    report = runner.run()

    assert report.state == "interrupted"
    assert report.ok is False
    assert report.reason is not None and "модель упала" in report.reason
    assert report.metrics.frames_completed == 0


def test_second_run_is_rejected() -> None:
    runner = PipelineRunner(computational(), PipelineConfig(frames=1))
    runner.run()

    with pytest.raises(RuntimeError, match="уже выполнялся"):
        runner.run()


def test_roi_channel_receives_messages_and_reports_drops() -> None:
    channel = RoiChannel(capacity=2)
    runner = PipelineRunner(roi_pipeline(), PipelineConfig(frames=10, roi=channel))

    report = runner.run()
    runner.close()

    assert report.metrics.roi["published"] == 10
    assert report.metrics.roi["dropped"] == 8
    assert report.metrics.roi["depth"] == 2
    drained = channel.drain()
    assert [item.frame_id for item in drained] == [9, 10]
    assert drained[0].rois == ((0.0, 0.0, 640.0, 480.0),)


def test_roi_channel_capacity_is_validated() -> None:
    with pytest.raises(ValueError):
        RoiChannel(capacity=0)


def test_roi_message_serialises() -> None:
    message = RoiMessage(frame_id=1, t_monotonic_ns=5, rois=((0.0, 1.0, 2.0, 3.0),))

    assert message.to_dict() == {
        "frame_id": 1,
        "t_monotonic_ns": 5,
        "rois": [[0.0, 1.0, 2.0, 3.0]],
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"frames": 0},
        {"fps": 0.0},
        {"max_queue": -1},
        {"work_scale": 0.0},
        {"frame_bytes": 0},
    ],
)
def test_config_rejects_invalid_values(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        PipelineConfig(**kwargs)  # type: ignore[arg-type]
