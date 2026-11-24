"""CLI этапа 11: report, export-package, verify-package без GUI (T11-07, G11-06)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simorph.cli.exit_codes import ExitCode
from simorph.cli.main import main

#: Идентификатор прогона в журнале фикстуры.
REPORT_RUN_ID = "sim-report"


@pytest.mark.integration
def test_report_command_writes_files(report_database: Path, tmp_path: Path, capsys) -> None:
    output = tmp_path / "report"

    code = main(["report", str(report_database), "-o", str(output), "--format", "md,html"])

    captured = capsys.readouterr()
    assert code == int(ExitCode.OK)
    assert "отчёт REP-01" in captured.out
    assert (output / "report.md").is_file()
    assert (output / "report.html").is_file()
    assert (output / "plots" / "k.png").is_file()


@pytest.mark.integration
def test_report_command_json(report_database: Path, tmp_path: Path, capsys) -> None:
    code = main(
        [
            "report",
            str(report_database),
            REPORT_RUN_ID,
            "-o",
            str(tmp_path / "out"),
            "--json",
            "--threshold",
            "k.p95=1.0",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == int(ExitCode.OK)
    assert payload["status"] == "warn"
    assert payload["run_id"] == REPORT_RUN_ID
    assert any(item["level"] == "fail" for item in payload["conclusions"])


@pytest.mark.integration
def test_report_command_sections(report_database: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    code = main(
        [
            "report",
            str(report_database),
            "-o",
            str(output),
            "--format",
            "md",
            "--sections",
            "metadata",
        ]
    )

    markdown = (output / "report.md").read_text(encoding="utf-8")
    assert code == int(ExitCode.OK)
    assert "## 1. Метаданные" in markdown
    assert "## 3. Метрики" not in markdown


@pytest.mark.integration
def test_report_command_errors(tmp_path: Path, capsys) -> None:
    code = main(["report", str(tmp_path / "absent.sqlite")])

    assert code == int(ExitCode.DATA_ERROR)
    assert "не найден" in capsys.readouterr().err


@pytest.mark.integration
def test_report_command_rejects_bad_threshold(report_database: Path, capsys) -> None:
    code = main(["report", str(report_database), "--threshold", "k.p95"])

    assert code == int(ExitCode.DATA_ERROR)
    assert "ИМЯ=ЗНАЧЕНИЕ" in capsys.readouterr().err


@pytest.fixture
def model_file(tmp_path: Path) -> Path:
    model = tmp_path / "det_kp.onnx"
    model.write_bytes(b"onnx-stub")
    return model


@pytest.mark.integration
def test_export_package_two_targets(model_file: Path, tmp_path: Path, capsys) -> None:
    output = tmp_path / "packages"

    code = main(
        [
            "export-package",
            "--model",
            str(model_file),
            "--target",
            "rk3588",
            "--target",
            "a733",
            "-o",
            str(output),
            "--run",
            "9c5960cbbdd74dbe9a45aa0d5d5cb2fa",
        ]
    )

    captured = capsys.readouterr()
    assert code == int(ExitCode.OK), captured.err
    assert (output / "rk3588" / "manifest.yaml").is_file()
    assert (output / "a733" / "manifest.yaml").is_file()
    assert (output / "rk3588" / "scripts" / "convert_rknn.py").is_file()
    assert (output / "a733" / "scripts" / "convert_nbg.py").is_file()
    assert "пакет PKG-9c5960cb-rk3588" in captured.out


@pytest.mark.integration
def test_export_package_json_and_verify(model_file: Path, tmp_path: Path, capsys) -> None:
    output = tmp_path / "pkg"
    code = main(["export-package", "--model", str(model_file), "-o", str(output), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == int(ExitCode.OK)
    assert payload["status"] == "ok"
    assert payload["packages"][0]["checksums"]["status"] == "ok"

    verify_code = main(["verify-package", str(output / "rk3588"), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert verify_code == int(ExitCode.OK)
    assert report["status"] == "ok"
    assert report["checksums"]["entries"] >= 5


@pytest.mark.integration
def test_export_package_requires_model(tmp_path: Path, capsys) -> None:
    code = main(["export-package", "-o", str(tmp_path / "pkg")])

    assert code == int(ExitCode.DATA_ERROR)
    assert "модель" in capsys.readouterr().err


@pytest.mark.integration
def test_verify_package_detects_tampering(model_file: Path, tmp_path: Path, capsys) -> None:
    output = tmp_path / "pkg"
    main(["export-package", "--model", str(model_file), "-o", str(output)])
    (output / "rk3588" / "models" / "det_kp.onnx").write_bytes(b"tampered")

    code = main(["verify-package", str(output / "rk3588")])

    assert code == int(ExitCode.DATA_ERROR)
    assert "не сходятся" in capsys.readouterr().out


@pytest.mark.integration
def test_verify_package_absent(tmp_path: Path, capsys) -> None:
    code = main(["verify-package", str(tmp_path / "absent")])

    assert code == int(ExitCode.DATA_ERROR)
    assert "не найден" in capsys.readouterr().err


@pytest.mark.integration
def test_cli_help_lists_new_commands(capsys) -> None:
    code = main([])

    assert code == int(ExitCode.DATA_ERROR)
    text = capsys.readouterr().err
    for command in ("report", "export-package", "verify-package", "dashboard"):
        assert command in text
