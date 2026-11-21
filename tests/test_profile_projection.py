"""Тесты проекции профиля и полноты κ/κ+ (T3-03…T3-05, G3-03…G3-05)."""

from __future__ import annotations

import pytest

from simorph.core.factory import new_project
from simorph.core.models.profile import ProfileParameter, ProfileSection
from simorph.core.models.project import ProjectDocument
from simorph.core.profile import completeness, project_profile
from simorph.core.schema_version import CURRENT_VERSION
from simorph.core.validation import validate_document


def _section(*parameters: ProfileParameter) -> ProfileSection:
    return ProfileSection(schema_version=CURRENT_VERSION, parameters=list(parameters))


def _parameter(**fields: object) -> ProfileParameter:
    base: dict[str, object] = {"id": "П-06", "value": 120.0, "block": "B"}
    base.update(fields)
    return ProfileParameter.model_validate(base)


def _document(section: ProfileSection, project_document: ProjectDocument) -> ProjectDocument:
    return project_document.model_copy(update={"profile": section})


def test_empty_profile_is_not_blocked() -> None:
    projection = project_profile(_section())

    assert projection.ready
    assert projection.counts() == {"P_gen": 0, "P_sim": 0, "P_hw": 0}
    assert completeness(_section()).total == 0


def test_unmarked_class_blocks_projection() -> None:
    projection = project_profile(_section(_parameter()))

    assert not projection.ready
    assert projection.vectors["P_gen"] == []
    assert projection.issues[0].path == "profile.parameters.0.class"
    assert projection.uncovered[0].reason == "класс не указан"


def test_class_c_without_target_blocks_projection() -> None:
    projection = project_profile(_section(_parameter(klass="C")))

    assert not projection.ready
    assert projection.issues[0].path == "profile.parameters.0.target"


def test_class_e_without_target_blocks_projection() -> None:
    projection = project_profile(_section(_parameter(klass="E")))

    assert not projection.ready
    assert "не проецируется" in projection.issues[0].message


def test_class_m_without_target_is_allowed() -> None:
    projection = project_profile(_section(_parameter(klass="M")))

    assert projection.ready
    assert projection.counts() == {"P_gen": 0, "P_sim": 0, "P_hw": 0}
    assert [item.parameter_id for item in projection.uncovered] == ["П-06"]


def test_class_n_is_kept_but_not_projected() -> None:
    projection = project_profile(_section(_parameter(klass="N")))

    assert projection.ready
    assert projection.counts() == {"P_gen": 0, "P_sim": 0, "P_hw": 0}
    assert projection.uncovered == []


def test_parameter_without_keys_is_a_warning() -> None:
    projection = project_profile(_section(_parameter(klass="C", target="P_gen", projection=[])))

    assert projection.ready
    assert projection.counts()["P_gen"] == 1
    assert projection.issues[0].severity == "warning"
    assert projection.issues[0].path == "profile.parameters.0.projection"


def test_range_parameter_projects_as_pair() -> None:
    parameter = _parameter(value=None, range=(1.0, 18.0), klass="E", target="P_sim")

    projection = project_profile(_section(parameter))
    entry = projection.vectors["P_sim"][0]

    assert entry.value == [1.0, 18.0]
    assert entry.klass == "E"


def test_key_map_groups_parameters_by_vector_key() -> None:
    first = _parameter(id="П-06", klass="C", target="P_gen", projection=["world.size.x"])
    second = _parameter(id="П-12", klass="C", target="P_gen", projection=["world.size.x"])

    key_map = project_profile(_section(first, second)).key_map("P_gen")

    assert key_map == {"world.size.x": ["П-06", "П-12"]}


def test_completeness_ignores_unmarked_and_n() -> None:
    section = _section(
        _parameter(id="П-01", klass="C", target="P_gen", projection=["a"]),
        _parameter(id="П-02", klass="E", target="P_sim", projection=["b"]),
        _parameter(id="П-03", klass="M"),
        _parameter(id="П-04", klass="N"),
        _parameter(id="П-05"),
    )
    metrics = completeness(section)

    assert metrics.total == 5
    assert metrics.unmarked == 1
    assert metrics.kappa_text == "2/5"
    assert metrics.kappa_plus_text == "3/5"
    assert not metrics.ready


def test_validation_warns_about_unmarked_class(project_document: ProjectDocument) -> None:
    document = _document(_section(_parameter()), project_document)

    issues = [item for item in validate_document(document) if item.path.startswith("profile.")]

    assert len(issues) == 1
    assert issues[0].path == "profile.parameters.0.class"
    assert issues[0].severity == "warning"


@pytest.mark.parametrize(
    ("klass", "target"),
    [("C", "P_gen"), ("E", "P_sim"), ("E", "P_hw"), ("M", "P_sim")],
)
def test_validation_accepts_allowed_targets(
    project_document: ProjectDocument, klass: str, target: str
) -> None:
    document = _document(
        _section(_parameter(klass=klass, target=target, projection=["key"])),
        project_document,
    )

    assert validate_document(document) == []


def test_new_project_profile_is_valid(project_document: ProjectDocument) -> None:
    section = new_project("test", seed=1).profile

    assert section.parameters
    assert {parameter.klass for parameter in section.parameters} == {"N"}
    assert completeness(section).ready
    assert project_profile(section).ready
