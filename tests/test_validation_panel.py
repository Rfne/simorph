"""Тесты панели валидации (T2-07, G2-05)."""

from __future__ import annotations

import pytest

from simorph.core.issues import Issue, IssueCode, Severity
from simorph.ui.validation_panel import ValidationPanel


@pytest.fixture
def panel(qtbot: object) -> ValidationPanel:
    created = ValidationPanel()
    qtbot.addWidget(created)  # type: ignore[attr-defined]
    return created


def _issues() -> list[Issue]:
    return [
        Issue(
            code=IssueCode.DANGLING_REFERENCE,
            path="agents.0.sensors.0.link",
            message="нет звена",
        ),
        Issue(
            code=IssueCode.INVALID_VALUE,
            path="world.seed",
            message="мягкое замечание",
            severity=Severity.WARNING,
        ),
    ]


@pytest.mark.ui
def test_issues_are_grouped_by_severity(panel: ValidationPanel) -> None:
    panel.set_issues(_issues())

    tree = panel._tree
    assert tree.topLevelItemCount() == 2
    errors = tree.topLevelItem(0)
    warnings = tree.topLevelItem(1)
    assert errors.text(0).startswith("Ошибки")
    assert errors.childCount() == 1
    assert warnings.text(0).startswith("Предупреждения")
    assert warnings.childCount() == 1
    assert errors.child(0).text(1) == "agents.0.sensors.0.link"
    assert "Ошибок: 1, предупреждений: 1" in panel._summary.text()


@pytest.mark.ui
def test_activation_emits_path(panel: ValidationPanel) -> None:
    panel.set_issues(_issues())
    received: list[str] = []
    panel.navigate_requested.connect(received.append)

    child = panel._tree.topLevelItem(0).child(0)
    panel._tree.itemDoubleClicked.emit(child, 0)

    assert received == ["agents.0.sensors.0.link"]


@pytest.mark.ui
def test_empty_issue_list_shows_placeholder(panel: ValidationPanel) -> None:
    panel.set_issues([])

    assert panel.issues() == []
    assert panel._tree.topLevelItemCount() == 0
    assert panel._summary.text() == "Замечаний нет"
