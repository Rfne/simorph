"""Тесты дашборда Panel: конфигурация, LaTeX, виджеты и offline (T11-08, T11-09)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from simorph.cli.exit_codes import ExitCode
from simorph.cli.main import main
from simorph.core.dashboard import (
    DashboardConfig,
    build_dashboard,
    dashboard_summary,
    latex_kappa,
    load_dashboard_config,
    load_dashboard_data,
)
from simorph.core.errors import ProjectError


@pytest.fixture
def dashboard_data(report_database: Path):
    return load_dashboard_data(report_database)


def _widgets(app: object) -> list[object]:
    import panel as pn

    found: list[object] = []
    stack: list[object] = [app]
    while stack:
        item = stack.pop()
        if isinstance(item, pn.widgets.Widget):
            found.append(item)
        children = getattr(item, "objects", None)
        if isinstance(children, list):
            stack.extend(children)
    return found


@pytest.mark.unit
def test_dashboard_data_reads_runs(dashboard_data) -> None:
    assert dashboard_data.run_ids == ["sim-report"]
    run = dashboard_data.by_id("sim-report")

    assert run is not None
    assert run.kind == "simulation"
    assert "k.p95" in run.data.series
    assert dashboard_data.to_dict()["status"] == "ok"


@pytest.mark.unit
def test_dashboard_data_missing_run(report_database: Path) -> None:
    with pytest.raises(ProjectError, match="не найден"):
        load_dashboard_data(report_database, run_ids=["absent"])


@pytest.mark.unit
def test_latex_kappa_has_plan_fact(dashboard_data) -> None:
    formula = latex_kappa(dashboard_data.runs[0])

    assert formula.startswith("$") and formula.endswith("$")
    assert r"\kappa = \frac{t_{\mathrm{actual}}}{t_{\mathrm{plan}}}" in formula
    assert r"\kappa_{p95} = 1.40" in formula


@pytest.mark.unit
def test_dashboard_summary_has_latex_and_tables(dashboard_data) -> None:
    import panel as pn

    blocks = dashboard_summary(
        dashboard_data.runs[0],
        DashboardConfig(),
        threshold_ms=400.0,
        degraded_only=False,
    )
    latex = [item for item in blocks if isinstance(item, pn.pane.LaTeX)]

    assert len(latex) == 1
    text = " ".join(
        payload for item in blocks if isinstance(payload := getattr(item, "object", ""), str)
    )
    assert "k.p95" in text
    assert "План и факт по ступеням" in text


@pytest.mark.perf
def test_dashboard_builds_under_three_seconds(dashboard_data) -> None:
    started = time.perf_counter()
    app = build_dashboard(dashboard_data, DashboardConfig())
    seconds = time.perf_counter() - started

    assert seconds < 3.0
    widgets = _widgets(app)
    labels = {widget.label for widget in widgets}
    assert {"Прогон", "Порог задержки p95, мс", "Только деградация"} <= labels


@pytest.mark.unit
def test_dashboard_is_offline(dashboard_data) -> None:
    import panel as pn

    build_dashboard(dashboard_data, DashboardConfig())

    assert pn.config.inline is True
    blocks = dashboard_summary(
        dashboard_data.runs[0],
        DashboardConfig(),
        threshold_ms=400.0,
        degraded_only=False,
    )
    for block in blocks:
        payload = getattr(block, "object", b"")
        text = payload if isinstance(payload, str) else ""
        assert "http://" not in text and "https://" not in text


@pytest.mark.unit
def test_dashboard_config_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "dash_run.yaml"
    path.write_text(
        "title: Демо\n"
        "runs: [sim-report]\n"
        "sections: [metrics, scale]\n"
        "thresholds: {inference_p95_ms: 300}\n"
        "port: 5010\n",
        encoding="utf-8",
    )

    config = load_dashboard_config(path)

    assert config.title == "Демо"
    assert config.runs == ["sim-report"]
    assert config.sections == ["metrics", "scale"]
    assert config.thresholds["inference_p95_ms"] == 300.0
    assert config.port == 5010


@pytest.mark.unit
def test_dashboard_config_rejects_remote_host(tmp_path: Path) -> None:
    path = tmp_path / "dash_run.yaml"
    path.write_text("host: 0.0.0.0\n", encoding="utf-8")

    with pytest.raises(ProjectError, match="локальном адресе"):
        load_dashboard_config(path)


@pytest.mark.unit
def test_dashboard_config_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="не найдена"):
        load_dashboard_config(tmp_path / "absent.yaml")


@pytest.mark.integration
def test_dashboard_check_cli(report_database: Path, capsys) -> None:
    code = main(["dashboard", str(report_database), "--check", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == int(ExitCode.OK)
    assert payload["status"] == "ok"
    assert payload["widgets"] == 3
    assert payload["latex"] == 1
    assert payload["seconds"] < 3.0


@pytest.mark.integration
def test_dashboard_cli_missing_run(report_database: Path, capsys) -> None:
    code = main(["dashboard", str(report_database), "--run", "absent", "--check"])

    assert code == int(ExitCode.DATA_ERROR)
    assert "не найден" in capsys.readouterr().err
