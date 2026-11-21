"""Тесты спецификации и эталонных профилей (T3-01, T3-08, G3-05)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from simorph.core.profile import (
    CUSTOMER_CODES,
    PROFILE_SPEC,
    SPEC_BY_ID,
    build_reference_profile,
    completeness,
    profile_hash,
    project_profile,
    spec_by_block,
)

ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = ROOT / "examples" / "profiles"
NDA_DIR = PROFILES_DIR / "nda"


def test_spec_has_75_unique_parameters() -> None:
    assert len(PROFILE_SPEC) == 75
    assert len(SPEC_BY_ID) == 75
    assert [spec.id for spec in PROFILE_SPEC] == [f"П-{number:02d}" for number in range(1, 76)]


def test_spec_classes_match_source_table() -> None:
    assert Counter(spec.klass for spec in PROFILE_SPEC) == {
        "C": 17,
        "E": 25,
        "M": 15,
        "N": 18,
    }


def test_spec_blocks_cover_a_to_j() -> None:
    assert Counter(spec.block for spec in PROFILE_SPEC) == {
        "A": 5,
        "B": 9,
        "C": 8,
        "D": 9,
        "E": 9,
        "F": 8,
        "G": 8,
        "H": 7,
        "I": 7,
        "J": 5,
    }


def test_spec_by_block_returns_only_that_block() -> None:
    block = spec_by_block("G")
    assert block
    assert {spec.block for spec in block} == {"G"}
    assert len(block) == 8


def test_projected_classes_have_target_and_keys() -> None:
    for spec in PROFILE_SPEC:
        if spec.klass in {"C", "E"}:
            assert spec.target in {"P_gen", "P_sim", "P_hw"}, spec.id
            assert spec.projection, spec.id


def test_class_n_is_never_projected() -> None:
    for spec in PROFILE_SPEC:
        if spec.klass == "N":
            assert spec.target is None, spec.id
            assert spec.projection == (), spec.id


@pytest.mark.parametrize("code", CUSTOMER_CODES)
def test_reference_profile_is_complete(code: str) -> None:
    section = build_reference_profile(code)
    metrics = completeness(section)

    assert len(section.parameters) == 75
    assert metrics.unmarked == 0
    assert metrics.kappa_text == "42/75"
    assert metrics.kappa_plus_text == "57/75"
    assert metrics.kappa_percent == pytest.approx(56.0)
    assert metrics.kappa_plus_percent == pytest.approx(76.0)


@pytest.mark.parametrize("code", CUSTOMER_CODES)
def test_reference_profile_projects_into_three_vectors(code: str) -> None:
    projection = project_profile(build_reference_profile(code))

    assert projection.ready
    assert projection.counts() == {"P_gen": 17, "P_sim": 30, "P_hw": 5}
    assert len(projection.uncovered) == 5


@pytest.mark.parametrize("code", CUSTOMER_CODES)
def test_example_file_matches_generator(code: str) -> None:
    section = build_reference_profile(code)
    path = (NDA_DIR if section.nda else PROFILES_DIR) / f"{code}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["export_version"] == "1.0"
    assert payload["hash"] == profile_hash(section)
    assert payload["profile"]["customer"] == section.customer
    assert payload["profile"]["nda"] is section.nda
    assert len(payload["profile"]["parameters"]) == 75


def test_nda_profile_lives_in_protected_directory() -> None:
    assert build_reference_profile("tatneft").nda
    assert not build_reference_profile("agroforce").nda
    assert (NDA_DIR / "tatneft.json").exists()
    assert not (PROFILES_DIR / "tatneft.json").exists()
