"""Тесты CLI ``simorph profile``: project, check, export (T3-05…T3-07, G3-05)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simorph.cli.exit_codes import ExitCode
from simorph.cli.main import main

ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = ROOT / "examples" / "profiles"
AGROFORCE = PROFILES_DIR / "agroforce.json"
TATNEFT = PROFILES_DIR / "nda" / "tatneft.json"


@pytest.mark.integration
def test_project_reports_kappa_and_vectors(capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["profile", "project", str(AGROFORCE), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["status"] == "ok"
    assert payload["parameters"] == 75
    assert payload["completeness"]["kappa"]["fraction"] == "42/75"
    assert payload["completeness"]["kappa_plus"]["fraction"] == "57/75"
    assert payload["projection"]["counts"] == {"P_gen": 17, "P_sim": 30, "P_hw": 5}


@pytest.mark.integration
def test_project_text_output_mentions_kappa(capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["profile", "project", str(AGROFORCE)])
    out, _ = capfd.readouterr()

    assert code == int(ExitCode.OK)
    assert "κ  42/75" in out
    assert "κ+ 57/75" in out


@pytest.mark.integration
def test_check_lists_unknown_fields(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "customer.json"
    source.write_text(
        json.dumps(
            {
                "customer": "Заказчик",
                "owner": "Иванов",
                "parameters": [{"id": "П-06", "value": 1.0, "class": "M"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    code = main(["profile", "check", str(source), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["unknown"][0]["name"] == "owner"


@pytest.mark.integration
def test_broken_json_exit_code_and_position(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "broken.json"
    source.write_text('{\n  "customer": ,\n}', encoding="utf-8")

    code = main(["profile", "project", str(source)])
    _, err = capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert "строка 2" in err


@pytest.mark.integration
def test_missing_file_is_data_error(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["profile", "check", str(tmp_path / "нет.json")])
    _, err = capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert "не найден" in err


@pytest.mark.integration
def test_export_writes_version_and_hash(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "agroforce.export.json"

    code = main(["profile", "export", str(AGROFORCE), "-o", str(output), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert output.exists()
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["export_version"] == "1.0"
    assert written["hash"] == payload["hash"]
    assert written["profile"]["customer"]


@pytest.mark.integration
def test_export_nda_profile_needs_protected_root(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    code = main(["profile", "export", str(TATNEFT), "-o", str(tmp_path / "out.json")])
    _, err = capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert "NDA" in err


@pytest.mark.integration
def test_export_nda_profile_to_protected_root(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    protected = tmp_path / "nda"
    protected.mkdir()
    output = protected / "tatneft.json"

    code = main(
        [
            "profile",
            "export",
            str(TATNEFT),
            "-o",
            str(output),
            "--nda-root",
            str(protected),
        ]
    )
    out, _ = capfd.readouterr()

    assert code == int(ExitCode.OK)
    assert output.exists()
    assert "hash sha256:" in out


@pytest.mark.integration
def test_blocked_projection_exit_code(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "unmarked.json"
    source.write_text(
        json.dumps({"parameters": [{"id": "П-06", "value": 1.0}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    code = main(["profile", "project", str(source)])
    out, err = capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert "проекция заблокирована" in err
    assert "42/75" not in out


@pytest.mark.integration
def test_unknown_action_is_data_error(capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["profile"])
    _, err = capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert "не указано действие" in err
