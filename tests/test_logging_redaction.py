"""Тесты логирования: JSON-lines, корреляция ``run_id`` и маскирование NDA-путей."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simorph.logging_setup import (
    NDA_ROOTS_ENV,
    REDACTED,
    Redactor,
    configure_logging,
)


@pytest.mark.unit
def test_log_file_is_json_lines_with_run_id(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "run.log"
    logger = configure_logging(log_file=log_file, verbose=1, run_id="run-42")
    logger.info("старт прогона")

    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    payload = json.loads(lines[0])
    assert payload["run_id"] == "run-42"
    assert payload["level"] == "INFO"
    assert payload["message"] == "старт прогона"
    assert payload["logger"] == "simorph"


@pytest.mark.unit
def test_nda_path_is_redacted_in_file(tmp_path: Path) -> None:
    secret_root = tmp_path / "secret_zone"
    log_file = tmp_path / "run.log"
    logger = configure_logging(log_file=log_file, verbose=1, nda_roots=[secret_root])

    logger.warning("кадр %s", secret_root / "frames" / "0001.png")

    content = log_file.read_text(encoding="utf-8")
    assert "secret_zone" not in content
    assert REDACTED in content
    assert "0001.png" in content


@pytest.mark.unit
def test_nda_path_is_redacted_with_windows_separators(tmp_path: Path) -> None:
    secret_root = tmp_path / "vault"
    logger = configure_logging(log_file=tmp_path / "run.log", verbose=1, nda_roots=[secret_root])

    logger.warning("файл %s", str(secret_root).replace("/", "\\") + "\\a.png")

    content = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "vault" not in content
    assert REDACTED in content


@pytest.mark.unit
def test_redactor_reads_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    secret_root = tmp_path / "nda_store"
    monkeypatch.setenv(NDA_ROOTS_ENV, str(secret_root))

    redactor = Redactor.from_environment()
    assert redactor.roots
    assert "nda_store" not in redactor.redact(str(secret_root / "a.png"))


@pytest.mark.unit
def test_repeated_configuration_replaces_handlers(tmp_path: Path) -> None:
    first = configure_logging(log_file=tmp_path / "one.log", run_id="first")
    second = configure_logging(log_file=tmp_path / "two.log", run_id="second")

    second.info("после переконфигурации")

    assert "после переконфигурации" in (tmp_path / "two.log").read_text(encoding="utf-8")
    assert first.handlers
    assert len(second.handlers) == 2
