"""Тесты CLI ``simorph pipeline``: build, validate, run и sources (T8-08)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simorph.cli.exit_codes import ExitCode
from simorph.cli.main import main
from simorph.core.factory import new_project
from simorph.core.models.pipeline import PipelineEdge, PipelineNode, PipelineSection
from simorph.core.models.project import ProjectDocument
from simorph.core.repository import save_project


def _project(tmp_path: Path, *, broken: bool = False) -> Path:
    document = new_project("pipeline", seed=1)
    pipeline = document.pipelines[0]
    if broken:
        pipeline = PipelineSection(
            schema_version="1.0",
            pipeline_id=pipeline.pipeline_id,
            kind="computational",
            nodes=[
                PipelineNode(id="src", type="source.frame", params={"camera": "cam_stereo"}),
                PipelineNode(id="det", type="sparse.detect"),
            ],
            edges=[PipelineEdge.model_validate({"from": "src.frame", "to": "det.stereo"})],
        )
    project: ProjectDocument = document.model_copy(update={"pipelines": [pipeline]})
    path = tmp_path / "project.json"
    save_project(project, path, make_backup=False)
    return path


@pytest.mark.integration
def test_build_reports_graph_and_plan(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    path = _project(tmp_path)

    code = main(["pipeline", "build", str(path), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["status"] == "ok"
    assert payload["pipeline_id"] == "PIPE-CMP-01"
    assert payload["nodes"] == 2
    assert payload["edges"] == 1
    assert payload["graph"]["layer_count"] == 2
    assert payload["graph"]["valid"] is True
    assert payload["graph"]["critical_path"] == ["src", "sink"]
    assert payload["graph"]["critical_path_ms"] == pytest.approx(0.3)
    assert payload["errors"] == 0


@pytest.mark.integration
def test_build_text_output(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    path = _project(tmp_path)

    code = main(["pipeline", "build", str(path)])
    out, _ = capfd.readouterr()

    assert code == int(ExitCode.OK)
    assert "конвейер PIPE-CMP-01" in out
    assert "слоёв 2" in out
    assert "критический путь: src → sink" in out
    assert "типы: sink.events, source.frame" in out


@pytest.mark.integration
def test_validate_accepts_demo_pipeline(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    path = _project(tmp_path)

    code = main(["pipeline", "validate", str(path), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["status"] == "ok"
    assert payload["can_run"] is True
    assert payload["issues"] == []


@pytest.mark.integration
def test_validate_rejects_incompatible_ports(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    path = _project(tmp_path, broken=True)

    code = main(["pipeline", "validate", str(path), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.DATA_ERROR)
    assert payload["status"] == "error"
    assert payload["can_run"] is False
    assert any("несовместим" in item["message"] for item in payload["issues"])


@pytest.mark.integration
def test_validate_text_lists_issue_paths(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    path = _project(tmp_path, broken=True)

    code = main(["pipeline", "validate", str(path)])
    out, _ = capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert "ошибок" in out
    assert "pipelines.0.edges.0.to" in out


@pytest.mark.integration
def test_run_reports_frames_and_latencies(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    path = _project(tmp_path)

    code = main(["pipeline", "run", str(path), "--frames", "12", "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["status"] == "ok"
    assert payload["state"] == "finished"
    assert payload["metrics"]["frames"]["completed"] == 12
    assert payload["metrics"]["frames"]["dropped"] == 0
    assert set(payload["metrics"]["nodes"]) == {"src", "sink"}
    assert payload["events"]


@pytest.mark.integration
def test_run_text_and_realtime_drop(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    path = _project(tmp_path)

    code = main(
        [
            "pipeline",
            "run",
            str(path),
            "--frames",
            "6",
            "--fps",
            "1000",
            "--max-queue",
            "1",
            "--realtime-drop",
            "--work-scale",
            "4",
        ]
    )
    out, _ = capfd.readouterr()

    assert code == int(ExitCode.OK)
    assert "конвейер PIPE-CMP-01" in out
    assert "отброшено" in out
    assert "план" in out


@pytest.mark.integration
def test_run_of_broken_pipeline_fails(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    path = _project(tmp_path, broken=True)

    code = main(["pipeline", "run", str(path), "--frames", "2", "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.DATA_ERROR)
    assert payload["status"] == "error"
    assert "не проходит валидацию" in payload["error"]


@pytest.mark.integration
def test_sources_snapshot_and_stale_check(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    path = _project(tmp_path)
    root = tmp_path / "src"
    root.mkdir()
    module = root / "demo.py"
    module.write_text("value = 1\n", encoding="utf-8")

    code = main(["pipeline", "sources", str(path), "--snapshot", "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["status"] == "ok"
    assert [item["path"] for item in payload["files"]] == ["demo.py"]
    snapshot = tmp_path / "project.json.sources.json"
    assert snapshot.is_file()

    code = main(["pipeline", "sources", str(path), "--check", "--json"])
    out, _ = capfd.readouterr()
    assert code == int(ExitCode.OK)
    assert json.loads(out)["check"]["state"] == "fresh"

    module.write_text("value = 2\n", encoding="utf-8")
    code = main(["pipeline", "sources", str(path), "--check", "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.DATA_ERROR)
    assert payload["status"] == "stale"
    assert payload["check"]["changed"] == ["demo.py"]


@pytest.mark.integration
def test_sources_without_snapshot_is_missing(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    path = _project(tmp_path)
    (tmp_path / "src").mkdir()

    code = main(["pipeline", "sources", str(path), "--check", "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["check"]["state"] == "missing"
    assert payload["files"] == []


@pytest.mark.integration
def test_unknown_pipeline_index_fails(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    path = _project(tmp_path)

    code = main(["pipeline", "build", str(path), "--index", "5", "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.DATA_ERROR)
    assert "конвейер с номером 5 не найден" in payload["error"]
