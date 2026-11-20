"""Интеграционные тесты команды ``selftest``."""

from __future__ import annotations

import json

import pytest

from simorph.cli.exit_codes import ExitCode
from simorph.cli.main import main


@pytest.mark.integration
def test_selftest_json_reports_no_missing(capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["selftest", "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["status"] == "ok"
    assert payload["missing"] == []
    assert payload["ui"] == "not-requested"
    assert payload["versions"]["python"]


@pytest.mark.integration
@pytest.mark.ui
def test_selftest_with_ui_offscreen(capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["selftest", "--ui", "--offscreen", "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["ui"] == "ok"
