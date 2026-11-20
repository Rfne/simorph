"""Тесты самопроверки Qt: окно открывается и закрывается без дисплея."""

from __future__ import annotations

import pytest

from simorph.cli.exit_codes import ExitCode
from simorph.selftest.ui import run_ui_selftest


@pytest.mark.ui
def test_ui_selftest_offscreen() -> None:
    assert run_ui_selftest(offscreen=True) == int(ExitCode.OK)
