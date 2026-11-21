"""Тесты импорта и экспорта профиля (T3-01, T3-06, T3-07, G3-01, G3-06, G3-07)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simorph.core.errors import ProjectValidationError
from simorph.core.models.profile import ProfileSection
from simorph.core.profile import (
    JsonPositionError,
    NdaViolation,
    build_reference_profile,
    check_nda_export,
    export_profile_payload,
    import_profile_payload,
    import_profile_text,
    is_nda_path,
    nda_roots,
    parse_profile_json,
    profile_hash,
    profile_payload,
    sanitize_paths,
)
from simorph.core.profile.exporting import dump_profile_export
from simorph.core.profile.spec import SPEC_BY_ID
from simorph.core.schema_version import CURRENT_VERSION


def test_unknown_fields_are_preserved_and_reported() -> None:
    payload = {
        "customer": "АО «Тест»",
        "owner": "Иванов",
        "parameters": [{"id": "П-06", "value": 1.0, "class": "M", "legacy": "да"}],
    }

    imported = import_profile_payload(payload)

    assert [item.name for item in imported.unknown] == ["owner", "legacy"]
    assert imported.section.extra == {"owner": "Иванов"}
    assert imported.section.parameters[0].extra == {"legacy": "да"}

    exported = profile_payload(imported.section)
    assert exported["owner"] == "Иванов"
    assert exported["parameters"][0]["legacy"] == "да"
    assert "extra" not in exported


def test_export_envelope_is_accepted_on_import() -> None:
    section = build_reference_profile("agroforce")
    envelope = export_profile_payload(section, roots=[])

    imported = import_profile_payload(envelope)

    assert imported.parameter_count == 75
    assert imported.section.customer == section.customer


def test_broken_json_reports_line_and_column() -> None:
    with pytest.raises(JsonPositionError) as error:
        parse_profile_json('{\n  "customer": ,\n}')

    assert error.value.line == 2
    assert error.value.column > 1
    assert "строка 2" in str(error.value)


def test_non_object_json_is_rejected() -> None:
    with pytest.raises(JsonPositionError):
        parse_profile_json("[1, 2, 3]")


def test_schema_error_reports_parameter_path() -> None:
    with pytest.raises(ProjectValidationError) as error:
        import_profile_payload({"parameters": [{"id": "П-06"}]})

    assert any(item.path == "parameters.0" for item in error.value.issues)


def test_parameters_must_be_a_list() -> None:
    with pytest.raises(ProjectValidationError) as error:
        import_profile_payload({"parameters": {"id": "П-06"}})

    assert error.value.issues[0].path == "parameters"


def test_unit_mismatch_is_a_warning() -> None:
    spec = SPEC_BY_ID["П-06"]
    assert spec.unit is not None
    imported = import_profile_payload(
        {
            "parameters": [
                {
                    "id": "П-06",
                    "value": 1.0,
                    "class": spec.klass,
                    "target": spec.target,
                    "unit": "не та единица",
                }
            ]
        }
    )

    assert imported.issues[0].severity == "warning"
    assert imported.report()["issues"][0]["path"] == "parameters.0.unit"


def test_import_text_reads_utf8_file(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps({"customer": "Заказчик", "parameters": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    imported = import_profile_text(path.read_text(encoding="utf-8"))

    assert imported.section.customer == "Заказчик"


def test_hash_is_stable_and_sensitive_to_values() -> None:
    section = build_reference_profile("agroforce")
    changed = section.model_copy(
        update={
            "parameters": [
                section.parameters[0].model_copy(update={"value": "другое значение"}),
                *section.parameters[1:],
            ]
        }
    )

    assert profile_hash(section) == profile_hash(section)
    assert profile_hash(section).startswith("sha256:")
    assert profile_hash(section) != profile_hash(changed)


def test_export_contains_version_and_hash() -> None:
    section = build_reference_profile("volga-kama")
    payload = export_profile_payload(section, roots=[])

    assert payload["schema_version"] == CURRENT_VERSION
    assert payload["export_version"] == "1.0"
    assert payload["hash"] == profile_hash(section)
    assert payload["profile"]["customer"] == section.customer


def test_export_strips_absolute_paths() -> None:
    section = ProfileSection(
        schema_version=CURRENT_VERSION,
        parameters=[
            {
                "id": "П-06",
                "value": 1.0,
                "class": "M",
                "source": r"D:\Заказчик\ТЗ\план.json",
            }
        ],
    )

    exported = profile_payload(section)

    assert exported["parameters"][0]["source"] == "план.json"
    assert sanitize_paths("/etc/secret.txt") == "secret.txt"


def test_nda_profile_is_refused_outside_protected_root(tmp_path: Path) -> None:
    section = build_reference_profile("tatneft")
    protected = tmp_path / "nda"
    protected.mkdir()

    with pytest.raises(NdaViolation):
        export_profile_payload(section, destination=tmp_path / "out.json", roots=[protected])

    payload = export_profile_payload(section, destination=protected / "out.json", roots=[protected])
    assert payload["profile"]["nda"] is True


def test_nda_profile_without_roots_is_refused(tmp_path: Path) -> None:
    section = build_reference_profile("tatneft")

    issues = check_nda_export(section, tmp_path / "out.json", roots=[])

    assert issues
    assert "SIMORPH_NDA_ROOTS" in issues[0].message


def test_nda_roots_are_read_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIMORPH_NDA_ROOTS", str(tmp_path))

    assert nda_roots() == (tmp_path,)
    assert is_nda_path(tmp_path / "profile.json")
    assert not is_nda_path(tmp_path.parent / "other" / "profile.json")


def test_non_nda_profile_with_nda_source_is_refused(tmp_path: Path) -> None:
    section = ProfileSection(
        schema_version=CURRENT_VERSION,
        parameters=[
            {
                "id": "П-06",
                "value": 1.0,
                "class": "M",
                "source": str(tmp_path / "plan.json"),
            }
        ],
    )

    issues = check_nda_export(section, tmp_path / "out.json", roots=[tmp_path])

    assert issues
    assert issues[0].path == "profile.parameters.0.source"


def test_dump_is_readable_and_newline_terminated() -> None:
    payload = export_profile_payload(build_reference_profile("agroforce"), roots=[])

    text = dump_profile_export(payload)

    assert text.endswith("\n")
    assert '"export_version": "1.0"' in text
    assert json.loads(text)["hash"] == payload["hash"]
