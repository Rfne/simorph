"""Отчёт: схема, сбор данных, графики и пороговые выводы (T11-01, T11-02, G11-02)."""

from __future__ import annotations

from pathlib import Path

import pytest

from simorph.core.report import (
    DEFAULT_SECTIONS,
    MetricSeries,
    ReportConfig,
    ReportData,
    ReportResult,
    build_plots,
    collect_report_data,
    derive_conclusions,
    evaluate_thresholds,
)

#: Идентификатор прогона в журнале фикстуры.
REPORT_RUN_ID = "sim-report"


def _series(name: str, values: list[float], *, unit: str | None = None) -> MetricSeries:
    return MetricSeries(
        name=name,
        unit=unit,
        t_ns=[index * 1_000 for index in range(len(values))],
        values=list(values),
    )


# --- T11-01: структуры ---------------------------------------------------


@pytest.mark.unit
def test_metric_series_statistics() -> None:
    series = _series("k", [1.0, 2.0, 3.0, 4.0], unit="ratio")

    stats = series.statistics()

    assert stats["count"] == 4.0
    assert stats["mean"] == pytest.approx(2.5)
    assert stats["min"] == 1.0
    assert stats["max"] == 4.0
    assert stats["p50"] == 2.0
    assert stats["p95"] == 4.0
    assert stats["sum"] == pytest.approx(10.0)
    assert series.total == pytest.approx(10.0)


@pytest.mark.unit
def test_metric_series_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="разной длины"):
        MetricSeries(name="bad", unit=None, t_ns=[1, 2], values=[1.0])


@pytest.mark.unit
def test_report_config_defaults_and_validation() -> None:
    config = ReportConfig()

    assert config.sections == list(DEFAULT_SECTIONS)
    assert config.export == ["md", "html"]
    with pytest.raises(ValueError, match="неизвестные секции"):
        ReportConfig(sections=["metadata", "unknown"])
    with pytest.raises(ValueError, match="неизвестные форматы"):
        ReportConfig(export=["docx"])
    with pytest.raises(ValueError, match="хотя бы одна секция"):
        ReportConfig(sections=[])
    with pytest.raises(ValueError, match="хотя бы один формат"):
        ReportConfig(export=[])
    with pytest.raises(ValueError, match="заголовок"):
        ReportConfig(title="  ")


@pytest.mark.unit
def test_report_config_round_trip() -> None:
    config = ReportConfig(
        report_id="REP-02",
        title="Сравнение",
        sections=["metadata", "scale"],
        thresholds={"k.p95": 1.2},
        export=["html"],
        template="templates/custom.j2",
        pipeline_ids=["PIPE-1"],
    )

    restored = ReportConfig.from_dict(config.to_dict())

    assert restored.to_dict() == config.to_dict()


@pytest.mark.unit
def test_report_data_values_and_result() -> None:
    data = ReportData(
        run={"run_id": "r-1", "kind": "simulation"},
        series={"k": _series("k", [0.5, 0.75]), "k.p95": _series("k.p95", [0.75])},
    )

    assert data.run_id == "r-1"
    assert data.values("k.") == {"k.p95": 0.75}

    result = ReportResult(config=ReportConfig(), data=data, files={"md": "report.md"})
    assert result.ok is True
    assert "r-1" in result.summary()

    failed = ReportResult(
        config=ReportConfig(),
        data=data,
        conclusions=list(evaluate_thresholds(data, {"k.p95": 0.1})),
    )
    assert failed.ok is False
    assert failed.to_dict()["status"] == "warn"


# --- T11-01: сбор из журнала ---------------------------------------------


@pytest.mark.integration
def test_collect_report_data(report_database: Path) -> None:
    data = collect_report_data(report_database, REPORT_RUN_ID)

    assert data.run_id == REPORT_RUN_ID
    assert data.kind == "simulation"
    assert data.app_version == "0.13.0"
    assert data.events == 1
    assert len(data.artifacts) == 1
    assert "k.p95" in data.series
    assert data.series["k"].count == 2


@pytest.mark.integration
def test_collect_scale_section_and_stages(report_database: Path) -> None:
    data = collect_report_data(report_database)

    names = [item.name for item in data.scale]
    assert "Масштаб k, p95" in names
    assert any(item.note == "frames.dropped" for item in data.scale)

    stages = {item.stage: item for item in data.stages}
    assert set(stages) == {"sensor", "npu"}
    sensor = stages["sensor"]
    assert sensor.plan_ms == 10.0
    assert sensor.actual_p95_ms == 12.0
    assert sensor.overrun_ms == pytest.approx(2.0)
    assert sensor.ratio == pytest.approx(1.2)
    assert sensor.samples == 2
    assert stages["npu"].overrun_ms == 0.0


@pytest.mark.integration
def test_collect_unknown_run_is_data_error(report_database: Path) -> None:
    with pytest.raises(KeyError, match="не найден"):
        collect_report_data(report_database, "missing")


# --- T11-02: графики -----------------------------------------------------


@pytest.mark.unit
def test_build_plots_for_run(report_database: Path) -> None:
    data = collect_report_data(report_database, REPORT_RUN_ID)

    charts = build_plots(data)

    names = {chart.name for chart in charts}
    assert {"k", "interval", "stages_actual", "stages_plan_fact", "queue"} <= names
    for chart in charts:
        assert chart.png.startswith(b"\x89PNG")
        assert chart.width == 640
        assert chart.data_uri().startswith("data:image/png;base64,")


@pytest.mark.unit
def test_plots_without_data_are_skipped() -> None:
    data = ReportData(run={"run_id": "empty", "kind": "simulation"})

    assert build_plots(data) == []


# --- T11-02: выводы и G11-02 --------------------------------------------


@pytest.mark.unit
def test_threshold_text_matches_value(report_database: Path) -> None:
    data = collect_report_data(report_database, REPORT_RUN_ID)

    strict = {item.metric: item for item in evaluate_thresholds(data, {"k.p95": 1.0})}
    loose = {item.metric: item for item in evaluate_thresholds(data, {"k.p95": 2.0})}

    assert strict["k.p95"].level == "fail"
    assert strict["k.p95"].value == 1.4
    assert "1.4" in strict["k.p95"].text
    assert loose["k.p95"].level == "ok"
    assert loose["k.p95"].text != strict["k.p95"].text

    border = evaluate_thresholds(data, {"k.p95": 1.5})
    assert border[0].level == "warn"


@pytest.mark.unit
def test_threshold_prefix_uses_worst_value() -> None:
    data = ReportData(
        run={"run_id": "r", "kind": "simulation"},
        series={
            "stage.a.overrun_ms": _series("stage.a.overrun_ms", [0.1, 0.2]),
            "stage.b.overrun_ms": _series("stage.b.overrun_ms", [3.0]),
        },
    )

    result = evaluate_thresholds(data, {"stage.*.overrun_ms": 1.0})

    assert len(result) == 1
    assert result[0].metric == "stage.b.overrun_ms"
    assert result[0].level == "fail"


@pytest.mark.unit
def test_missing_metric_is_informational() -> None:
    data = ReportData(run={"run_id": "r", "kind": "simulation"})

    result = evaluate_thresholds(data, {"eval.map50": 0.6})

    assert result[0].level == "info"
    assert "отсутствует" in result[0].text


@pytest.mark.unit
def test_derive_conclusions_follow_values(report_database: Path) -> None:
    data = collect_report_data(report_database, REPORT_RUN_ID)

    conclusions = {item.metric: item for item in derive_conclusions(data)}

    assert conclusions["k.p50"].level == "fail"
    assert "1.20" in conclusions["k.p50"].text
    assert conclusions["frames.dropped"].level == "ok"
    assert conclusions["stage.sensor.overrun_ms"].level == "fail"
    assert "12.00" in conclusions["stage.sensor.overrun_ms"].text
    assert conclusions["eval.map50"].value == pytest.approx(0.64)


@pytest.mark.unit
def test_conclusions_without_metrics_are_informational() -> None:
    data = ReportData(run={"run_id": "empty", "kind": "simulation"})

    result = derive_conclusions(data)

    assert len(result) == 1
    assert result[0].level == "info"
    assert "нет метрик" in result[0].text
