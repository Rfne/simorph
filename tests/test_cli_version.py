"""Тесты команды ``version``: состав версий и чистота JSON-вывода."""

from __future__ import annotations

import json
import sys

import pytest

from simorph.cli.exit_codes import ExitCode
from simorph.cli.main import main


@pytest.mark.unit
def test_version_json_is_parseable(capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["version", "--json"])
    out, _ = capfd.readouterr()

    assert code == int(ExitCode.OK)
    payload = json.loads(out)
    assert payload["python"] == sys.version.split()[0]
    assert payload["simorph"]


@pytest.mark.unit
def test_version_json_reports_gate_keys(capfd: pytest.CaptureFixture[str]) -> None:
    main(["version", "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    for key in ("qt", "pybullet", "opencv", "torch", "sqlite"):
        assert payload[key], f"версия {key} не определена"


@pytest.mark.unit
def test_version_json_is_not_polluted_by_banner(capfd: pytest.CaptureFixture[str]) -> None:
    """PyBullet печатает баннер сборки из C++; он не должен попасть в stdout."""
    main(["version", "--json"])
    out, _ = capfd.readouterr()

    assert "build time" not in out
    json.loads(out)


@pytest.mark.unit
def test_json_flag_before_command(capfd: pytest.CaptureFixture[str]) -> None:
    assert main(["--json", "version"]) == int(ExitCode.OK)
    out, _ = capfd.readouterr()
    json.loads(out)


@pytest.mark.unit
def test_version_human_lists_keys(capfd: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == int(ExitCode.OK)
    out, _ = capfd.readouterr()

    assert "python" in out
    assert "sqlite" in out
    assert "{" not in out
