"""Тесты CLI ``simorph sim``: headless-прогон и replay (T7-05, T7-07)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simorph.cli.exit_codes import ExitCode
from simorph.cli.main import main


@pytest.mark.integration
def test_run_reports_finished(project_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["sim", "run", str(project_path), "--duration", "0.2", "--no-pace", "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["status"] == "ok"
    assert payload["state"] == "finished"
    assert payload["frames"]["attempted"] == 24
    assert payload["frames"]["pushed"] == 24
    assert payload["frames"]["consumed"] == 24
    assert payload["frames"]["loss_ratio"] == 0.0
    assert payload["metrics"]["k"]["p50"] is not None
    assert payload["events"][0]["kind"] == "run_started"


@pytest.mark.integration
def test_run_text_output(project_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["sim", "run", str(project_path), "--duration", "0.1"])
    out, _ = capfd.readouterr()

    assert code == int(ExitCode.OK)
    assert "прогон finished" in out
    assert "потерь 0" in out
    assert "масштаб 1" in out


@pytest.mark.integration
def test_run_records_stream_and_replay_matches(
    project_path: Path, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    stream = tmp_path / "stream.jsonl"
    code = main(
        [
            "sim",
            "run",
            str(project_path),
            "--duration",
            "0.2",
            "--no-pace",
            "--record",
            str(stream),
        ]
    )
    assert code == int(ExitCode.OK)
    assert stream.exists()
    capfd.readouterr()

    code = main(["sim", "replay", str(stream), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["ok"] is True
    assert payload["frames"] == 24
    assert payload["matched"] == 24


@pytest.mark.integration
def test_run_with_slowdown_shows_overrun(
    project_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "sim",
            "run",
            str(project_path),
            "--duration",
            "0.2",
            "--no-pace",
            "--work-ms",
            "8.3333",
            "--slowdown",
            "3",
            "--json",
        ]
    )
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["frames"]["attempted"] == payload["frames"]["pushed"] == 24
    assert payload["metrics"]["k"]["p50"] > 2.5
    assert payload["metrics"]["stages"]["sensor"]["overrun_ms"] > 0.0


@pytest.mark.integration
def test_run_with_degradation_reports_loss(
    project_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "sim",
            "run",
            str(project_path),
            "--duration",
            "0.5",
            "--no-pace",
            "--loss",
            "0.5",
            "--latency-ms",
            "2",
            "--json",
        ]
    )
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["frames"]["dropped"] > 0
    assert payload["metrics"]["degradation"]["lost"] == payload["frames"]["dropped"]


@pytest.mark.integration
def test_run_rejects_invalid_degradation(
    project_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    code = main(["sim", "run", str(project_path), "--duration", "0.1", "--loss", "1.5", "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.DATA_ERROR)
    assert payload["status"] == "error"


@pytest.mark.integration
def test_run_rejects_invalid_scale(project_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["sim", "run", str(project_path), "--duration", "0.1", "--scale", "0", "--json"])
    out, _ = capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert json.loads(out)["status"] == "error"


@pytest.mark.integration
def test_replay_missing_file_is_data_error(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    code = main(["sim", "replay", str(tmp_path / "absent.jsonl"), "--json"])
    out, _ = capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert json.loads(out)["status"] == "error"


@pytest.mark.integration
def test_sim_without_action_is_data_error(capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["sim"])
    _out, err = capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert "run" in err
