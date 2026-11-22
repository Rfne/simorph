"""Тесты вкладки «Конвейеры»: граф, правки, прогон и исходники (G8-04…G8-06)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from simorph.core.factory import new_project
from simorph.core.models.pipeline import PipelineEdge, PipelineNode, PipelineSection
from simorph.core.models.project import ProjectDocument
from simorph.core.repository import load_project, save_project
from simorph.settings import ApplicationSettings
from simorph.ui.main_window import PIPELINE_TITLE, MainWindow
from simorph.ui.pipeline_view import PipelineView


def _document() -> ProjectDocument:
    return new_project("pipeline", seed=1)


def _two_preprocess() -> ProjectDocument:
    """Конвейер из двух препроцессоров: удобно проверять циклы."""
    pipeline = PipelineSection(
        schema_version="1.0",
        pipeline_id="PIPE-NN-01",
        kind="neural",
        nodes=[
            PipelineNode(id="a", type="preprocess"),
            PipelineNode(id="b", type="preprocess"),
            PipelineNode(id="sink", type="sink.events", params={"topic": "events"}),
        ],
        edges=[
            PipelineEdge.model_validate({"from": "a.frame", "to": "b.frame"}),
            PipelineEdge.model_validate({"from": "b.frame", "to": "sink.events"}),
        ],
    )
    return _document().model_copy(update={"pipelines": [pipeline]})


@pytest.fixture
def window(qtbot: object, isolated_settings: ApplicationSettings) -> MainWindow:
    created = MainWindow(isolated_settings)
    created.confirmations_enabled = False
    created.store.apply(_document())
    qtbot.addWidget(created)
    return created


@pytest.fixture
def view(window: MainWindow) -> Iterator[PipelineView]:
    window.open_pipeline()
    created = window.pipeline_view
    assert created is not None
    yield created
    created.dispose()


@pytest.mark.ui
def test_tab_opens_from_action(window: MainWindow) -> None:
    assert window.action_pipeline.shortcut().toString() == "F10"

    index = window.open_pipeline()
    created = window.pipeline_view

    assert created is not None
    assert window.tabs.widget(index) is created
    assert window.tabs.tabText(index) == PIPELINE_TITLE
    assert created.pipeline is not None
    assert created.pipeline.pipeline_id == "PIPE-CMP-01"


@pytest.mark.ui
def test_graph_and_edges_are_shown(view: PipelineView) -> None:
    assert view.node_ids() == ["src", "sink"]
    assert [name for _layer, name in view.graph_rows()] == ["src", "sink"]
    assert view.edge_rows() == [("src.frame", "sink.events")]
    assert view.graph.valid is True
    assert view.issues == []
    assert "план" in view.summary_text


@pytest.mark.ui
def test_add_and_remove_node_are_single_undo_steps(view: PipelineView) -> None:
    assert view.add_node("rectify") is True
    assert len(view.pipeline.nodes) == 3  # type: ignore[union-attr]
    assert view.pipeline.nodes[-1].id == "rectify-1"  # type: ignore[union-attr]

    view._store.undo()
    assert view.node_ids() == ["src", "sink"]

    assert view.remove_node("sink") is True
    assert view.node_ids() == ["src"]
    assert view.edge_rows() == []


@pytest.mark.ui
def test_add_edge_rejects_incompatible_ports(view: PipelineView) -> None:
    assert view.add_node("rectify") is True

    assert view.add_edge("src.frame", "rectify-1.stereo") is False
    assert "несовместим" in view.status_text


@pytest.mark.ui
def test_add_edge_rejects_duplicates(view: PipelineView) -> None:
    assert view.add_edge("src.frame", "sink.events") is False
    assert "уже существует" in view.status_text


@pytest.mark.ui
def test_add_edge_rejects_cycle(window: MainWindow) -> None:
    window.store.apply(_two_preprocess())
    window.open_pipeline()
    created = window.pipeline_view
    assert created is not None

    assert created.add_edge("b.frame", "a.frame") is False
    assert "цикл" in created.status_text
    created.dispose()


@pytest.mark.ui
def test_apply_node_updates_params_and_budget(view: PipelineView) -> None:
    assert view.select_node("src") is True
    assert view.submit_field("params", '{"camera": "cam_wide", "fps": 60}') is True

    node = view.pipeline.nodes[0]  # type: ignore[union-attr]
    assert node.params["camera"] == "cam_wide"
    assert node.params["fps"] == 60

    assert view.submit_field("budget_ms", "0,5") is True
    assert view.pipeline.nodes[0].budget_ms == pytest.approx(0.5)  # type: ignore[union-attr]

    view._store.undo()
    assert view.pipeline.nodes[0].budget_ms == pytest.approx(0.2)  # type: ignore[union-attr]


@pytest.mark.ui
def test_apply_node_rejects_broken_json(view: PipelineView) -> None:
    assert view.select_node("src") is True

    assert view.submit_field("params", "{не json") is False
    assert "узел:" in view.status_text


@pytest.mark.ui
def test_run_fills_metrics_and_events(view: PipelineView) -> None:
    assert view.run_pipeline(frames=6) is True

    report = view.report
    assert report is not None
    assert report.ok is True
    assert report.metrics.frames_completed == 6
    assert "finished" in view.summary_text
    assert "кадров 6/6" in view.summary_text
    assert view.status_text


@pytest.mark.ui
def test_sources_snapshot_and_stale_status(view: PipelineView, tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    module = root / "demo.py"
    module.write_text("value = 1\n", encoding="utf-8")

    view.set_project_path(tmp_path / "project.json")
    assert view.set_source_root(root) is True
    assert view.source_tree is not None
    assert [item.path for item in view.source_tree.files] == ["demo.py"]

    assert view.snapshot_sources() is True
    assert (tmp_path / "project.json.sources.json").is_file()
    assert view.source_status is not None
    assert view.source_status.state == "fresh"

    module.write_text("value = 2\n", encoding="utf-8")
    assert view.set_source_root(root) is True
    status = view.check_sources()

    assert status is not None
    assert status.state == "stale"
    assert status.changed == ["demo.py"]
    assert "изменены: demo.py" in view.status_text


@pytest.mark.ui
def test_sources_require_selected_root(view: PipelineView) -> None:
    assert view.snapshot_sources() is False
    assert "каталог исходников не выбран" in view.status_text
    assert view.check_sources() is None


@pytest.mark.ui
def test_rebuild_keeps_pipeline_index(view: PipelineView) -> None:
    assert view.select_node("sink") is True
    view.rebuild(_document())

    assert view.pipeline_index == 0
    assert view.selected_id == "sink"
    assert view.select_node("ghost") is False


@pytest.mark.ui
def test_edited_graph_survives_save_and_reopen(
    view: PipelineView, window: MainWindow, tmp_path: Path
) -> None:
    assert view.add_node("roi") is True
    assert view.add_edge("src.frame", "roi-1.frame") is True
    assert view.select_node("roi-1") is True
    assert view.submit_field("budget_ms", "1.5") is True
    assert view.submit_field("params", '{"margin": 0.25}') is True

    target = tmp_path / "project.json"
    save_project(window.store.document, target, make_backup=False)
    reopened = load_project(target).document

    assert reopened.pipelines[0].nodes[2].type == "roi"
    assert reopened.pipelines[0].nodes[2].budget_ms == pytest.approx(1.5)
    assert reopened.pipelines[0].nodes[2].params == {"margin": 0.25}
    assert ("src.frame", "roi-1.frame") in [
        (edge.src, edge.dst) for edge in reopened.pipelines[0].edges
    ]
