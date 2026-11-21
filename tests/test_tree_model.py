"""Тесты дерева проекта: группы, бейджи, фильтр, масштаб (T2-02, G2-01, G2-07)."""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import Qt

from simorph.core.issues import Issue, IssueCode, Severity
from simorph.core.models.project import ProjectDocument
from simorph.ui.tree_model import (
    PATH_ROLE,
    SECTION_FIELDS,
    NodeKind,
    ProjectTreeModel,
    build_tree,
)


@pytest.mark.ui
def test_tree_has_six_groups(project_document: ProjectDocument) -> None:
    root = build_tree(project_document)

    assert len(root.children) == len(SECTION_FIELDS) == 6
    assert [child.path for child in root.children] == [
        "profile",
        "world",
        "agents",
        "exercises",
        "pipelines",
        "reports",
    ]
    assert all(child.kind is NodeKind.GROUP for child in root.children)


@pytest.mark.ui
def test_model_exposes_groups_with_icons(project_document: ProjectDocument) -> None:
    model = ProjectTreeModel()
    model.set_document(project_document)

    assert model.rowCount() == 6
    index = model.index(1, 0)
    assert index.data(PATH_ROLE) == "world"
    assert index.data(int(Qt.ItemDataRole.DecorationRole)) is not None


@pytest.mark.ui
def test_nested_paths_are_reachable(project_document: ProjectDocument) -> None:
    model = ProjectTreeModel()
    model.set_document(project_document)

    index = model.index_for_path("agents.0.sensors.0.id")
    assert index.isValid()
    assert index.data(PATH_ROLE) == "agents.0.sensors.0.id"


@pytest.mark.ui
def test_issues_raise_badges_to_groups(project_document: ProjectDocument) -> None:
    model = ProjectTreeModel()
    model.set_document(project_document)
    model.set_issues(
        [Issue(code=IssueCode.DANGLING_REFERENCE, path="agents.0.sensors.0.link", message="нет")]
    )

    group = model.index(2, 0)
    assert "⚠1" in str(group.data(int(Qt.ItemDataRole.DisplayRole)))
    assert model.node_for_path("agents.0.sensors.0.link").errors == 1  # type: ignore[union-attr]


@pytest.mark.ui
def test_warnings_are_counted_separately(project_document: ProjectDocument) -> None:
    model = ProjectTreeModel()
    model.set_document(project_document)
    model.set_issues(
        [
            Issue(
                code=IssueCode.INVALID_VALUE,
                path="world.seed",
                message="мягкое замечание",
                severity=Severity.WARNING,
            )
        ]
    )

    assert model.node_for_path("world.seed").warnings == 1  # type: ignore[union-attr]
    assert model.node_for_path("world").errors == 0  # type: ignore[union-attr]


@pytest.mark.ui
def test_filter_keeps_ancestors(project_document: ProjectDocument) -> None:
    model = ProjectTreeModel()
    model.set_document(project_document)
    model.set_filter("cam_stereo")

    groups = [model.index(row, 0).data(PATH_ROLE) for row in range(model.rowCount())]
    assert groups == ["agents", "pipelines"]

    agents = model.index(groups.index("agents"), 0)
    assert model.rowCount(agents) == 1
    assert model.index(0, 0, agents).data(PATH_ROLE) == "agents.0"


@pytest.mark.ui
def test_filter_finds_nested_field(project_document: ProjectDocument) -> None:
    model = ProjectTreeModel()
    model.set_document(project_document)
    model.set_filter("wheel_fl_joint")

    agents = model.index(0, 0)
    agent = model.index(0, 0, agents)
    joints = [model.index(row, 0, agent).data(PATH_ROLE) for row in range(model.rowCount(agent))]
    assert "agents.0.joints" in joints


@pytest.mark.ui
def test_filter_reset_restores_tree(project_document: ProjectDocument) -> None:
    model = ProjectTreeModel()
    model.set_document(project_document)
    model.set_filter("cam_stereo")
    model.set_filter("")

    assert model.rowCount() == 6


@pytest.mark.ui
def test_unknown_path_falls_back_to_ancestor(project_document: ProjectDocument) -> None:
    model = ProjectTreeModel()
    model.set_document(project_document)

    node = model.node_for_path("agents.0.absent.field")
    assert node is not None
    assert node.path == "agents.0"


@pytest.mark.ui
def test_thousand_nodes_render_fast() -> None:
    from simorph.core.factory import build_agent, new_project

    document = new_project("scale")
    agent = build_agent()
    document = document.model_copy(
        update={
            "agents": [agent.model_copy(update={"agent_id": f"A-{index}"}) for index in range(120)]
        }
    )

    model = ProjectTreeModel()
    started = time.perf_counter()
    model.set_document(document)
    elapsed = time.perf_counter() - started

    assert len(model.walk()) > 1000
    assert elapsed < 0.5
