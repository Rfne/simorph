"""Тесты кодов возврата CLI."""

from __future__ import annotations

from typing import NoReturn

import pytest

from simorph.cli.exit_codes import ExitCode
from simorph.cli.main import main


@pytest.mark.unit
def test_unknown_command_is_data_error(capfd: pytest.CaptureFixture[str]) -> None:
    assert main(["nope"]) == int(ExitCode.DATA_ERROR)
    _, err = capfd.readouterr()
    assert "nope" in err or "invalid choice" in err


@pytest.mark.unit
def test_unknown_option_is_data_error(capfd: pytest.CaptureFixture[str]) -> None:
    assert main(["version", "--nope"]) == int(ExitCode.DATA_ERROR)
    _, err = capfd.readouterr()
    assert "unrecognized" in err or "--nope" in err


@pytest.mark.unit
def test_no_command_shows_help(capfd: pytest.CaptureFixture[str]) -> None:
    assert main([]) == int(ExitCode.DATA_ERROR)
    _, err = capfd.readouterr()
    assert "version" in err


@pytest.mark.unit
def test_internal_error_returns_two(
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> NoReturn:
        raise RuntimeError("boom")

    monkeypatch.setattr("simorph.cli.main.collect_versions", boom)

    assert main(["version"]) == int(ExitCode.INTERNAL_ERROR)
    _, err = capfd.readouterr()
    assert "внутренняя ошибка" in err


@pytest.mark.unit
def test_exit_codes_are_distinct() -> None:
    assert len({int(ExitCode.OK), int(ExitCode.DATA_ERROR), int(ExitCode.INTERNAL_ERROR)}) == 3
