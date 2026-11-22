"""Тесты CLI ``simorph runs`` и записи прогонов в журнал (T9-06, G9-06)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simorph.cli.exit_codes import ExitCode
from simorph.cli.main import main
from simorph.core.logs import (
    MetricRow,
    RunRecord,
    default_log_path,
    open_store,
    record_to_file,
)
from simorph.core.pipeline.metrics import NodeLatency, PipelineMetrics, PipelineRunReport

pytestmark = pytest.mark.integration


def _seed_database(project_path: Path, *, runs: int = 2) -> Path:
    """Журнал с прогонами: свежий последним по времени старта."""
    database = default_log_path(project_path)
    with open_store(database) as store:
        for index in range(runs):
            run_id = f"run-{index}"
            store.start_run(
                RunRecord(
                    run_id=run_id,
                    kind="simulation",
                    status="finished",
                    started_at=f"2026-11-0{index + 1}T10:00:00Z",
                    finished_at=f"2026-11-0{index + 1}T10:01:00Z",
                    mission_id="M-01",
                    seed=index,
                )
            )
            store.write_metrics(run_id, [MetricRow("k", 0.5 + index, index, "ratio")])
    return database


def test_runs_list_json(project_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    _seed_database(project_path)

    code = main(["runs", "list", str(project_path), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["status"] == "ok"
    assert payload["count"] == 2
    assert [item["run_id"] for item in payload["runs"]] == ["run-1", "run-0"]


def test_runs_list_text_and_filters(project_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    _seed_database(project_path)

    code = main(["runs", "list", str(project_path), "--kind", "simulation", "--limit", "1"])
    out, _ = capfd.readouterr()

    assert code == int(ExitCode.OK)
    assert "прогонов 1" in out
    assert "run-1" in out
    assert "run-0" not in out


def test_runs_list_requires_existing_database(
    project_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    code = main(["runs", "list", str(project_path)])
    _, err = capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert "журнал не найден" in err


def test_runs_show_last_with_metric_and_event(
    project_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    _seed_database(project_path)

    code = main(["runs", "show", str(project_path), "--last", "--metric", "k", "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["run"]["run_id"] == "run-1"
    assert payload["counts"]["metrics"] == 1
    assert payload["metric_names"] == ["k"]
    assert payload["metrics"][0]["value"] == pytest.approx(1.5)


def test_runs_show_unknown_run(project_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    _seed_database(project_path)

    code = main(["runs", "show", str(project_path), "absent"])
    _, err = capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert "не найден" in err


def test_runs_export_csv_and_parquet(
    project_path: Path, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    _seed_database(project_path)
    csv_path = tmp_path / "metrics.csv"
    parquet_path = tmp_path / "metrics.parquet"

    code = main(["runs", "export", str(project_path), "-o", str(csv_path), "--table", "metrics"])
    assert code == int(ExitCode.OK)
    first, _ = capfd.readouterr()

    code = main(
        [
            "runs",
            "export",
            str(project_path),
            "-o",
            str(parquet_path),
            "--table",
            "metrics",
            "--run",
            "run-1",
            "--format",
            "parquet",
            "--json",
        ]
    )
    second, _ = capfd.readouterr()
    payload = json.loads(second)

    assert code == int(ExitCode.OK)
    assert "выгружено 2 строк" in first
    assert payload["rows"] == 1
    assert payload["format"] == "parquet"
    assert csv_path.exists()
    assert parquet_path.exists()


def test_runs_annotate_adds_row(project_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    _seed_database(project_path)

    code = main(
        [
            "runs",
            "annotate",
            str(project_path),
            "run-1",
            "--frame",
            "3",
            "--class-id",
            "person",
            "--bbox",
            "0,0,10,10",
            "--json",
        ]
    )
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["annotations"] == 1
    assert payload["counts"]["annotations"] == 1

    with open_store(default_log_path(project_path)) as store:
        annotations = store.annotations("run-1")

    assert annotations[0]["frame_id"] == 3
    assert annotations[0]["bbox"] == [0.0, 0.0, 10.0, 10.0]


def test_runs_prune_dry_run_then_remove(
    project_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    _seed_database(project_path, runs=3)

    code = main(["runs", "prune", str(project_path), "--keep-last", "1", "--dry-run", "--json"])
    out, _ = capfd.readouterr()
    planned = json.loads(out)

    assert code == int(ExitCode.OK)
    assert planned["removed"] == 0
    assert set(planned["candidates"]) == {"run-0", "run-1"}

    code = main(["runs", "prune", str(project_path), "--keep-last", "1"])
    out, _ = capfd.readouterr()

    assert code == int(ExitCode.OK)
    assert "удалено прогонов: 2" in out
    with open_store(default_log_path(project_path)) as store:
        assert [item["run_id"] for item in store.list_runs()] == ["run-2"]


def test_runs_prune_requires_condition(
    project_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    _seed_database(project_path)

    code = main(["runs", "prune", str(project_path)])
    _, err = capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert "keep-last" in err


def test_sim_run_writes_log(project_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    database = default_log_path(project_path)

    code = main(
        [
            "sim",
            "run",
            str(project_path),
            "--duration",
            "0.02",
            "--no-pace",
            "--log",
            str(database),
            "--json",
        ]
    )
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["log"]["path"] == str(database)
    assert database.exists()

    with open_store(database) as store:
        runs = store.list_runs()

    assert len(runs) == 1
    assert runs[0]["kind"] == "simulation"
    assert runs[0]["status"] == "finished"


def test_pipeline_run_writes_log(project_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    database = default_log_path(project_path)

    code = main(
        [
            "pipeline",
            "run",
            str(project_path),
            "--frames",
            "3",
            "--log",
            str(database),
        ]
    )
    out, _ = capfd.readouterr()

    assert code == int(ExitCode.OK)
    assert "журнал:" in out

    with open_store(database) as store:
        runs = store.list_runs()

    assert len(runs) == 1
    assert runs[0]["kind"] == "pipeline"
    assert runs[0]["pipeline_id"] == "PIPE-CMP-01"


def test_record_to_file_summary_is_json_ready(tmp_path: Path) -> None:
    report = PipelineRunReport(
        pipeline_id="P-1",
        kind="computational",
        state="finished",
        reason=None,
        config={},
        metrics=PipelineMetrics(
            pipeline_id="P-1",
            frames_attempted=1,
            frames_completed=1,
            nodes={"src": NodeLatency("src", "source.frame", 0.1, [0.1])},
        ),
    )

    summary = record_to_file(tmp_path / "run.sqlite", report, kind="pipeline")

    assert json.dumps(summary, default=str)
    assert summary["counts"]["runs"] == 1
