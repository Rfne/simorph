"""Изоляция NDA-профилей (T3-06, FR-PRF-08).

Профиль под NDA экспортируется только в защищённый каталог: список таких
каталогов задаётся переменной ``SIMORPH_NDA_ROOTS`` (та же, что использует
маскирование путей в логах). Если профиль не помечен как NDA, но ссылается
источником на защищённый каталог, экспорт тоже запрещён — иначе путь утечёт
в общий JSON.

Проверка возвращает замечания, а не бросает исключение: интерфейс
показывает их рядом с кнопкой экспорта, CLI печатает и завершается кодом 1.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from simorph.core.issues import Issue, IssueCode, issue
from simorph.core.models.profile import ProfileSection
from simorph.logging_setup import NDA_ROOTS_ENV

__all__ = [
    "NdaViolation",
    "check_nda_export",
    "is_nda_path",
    "nda_roots",
]


class NdaViolation(Exception):  # noqa: N818 - это факт нарушения режима, а не сбой
    """Экспорт NDA-профиля за пределы защищённого каталога."""


def nda_roots(explicit: Iterable[str | Path] | None = None) -> tuple[Path, ...]:
    """Защищённые каталоги: явный список или ``SIMORPH_NDA_ROOTS``."""
    if explicit is not None:
        candidates = [Path(str(item)) for item in explicit]
    else:
        raw = os.environ.get(NDA_ROOTS_ENV, "")
        candidates = [Path(part) for part in raw.split(os.pathsep) if part.strip()]
    return tuple(item.expanduser() for item in candidates)


def _resolved(path: Path) -> Path:
    try:
        return path.expanduser().resolve(strict=False)
    except OSError:  # pragma: no cover - защита от недопустимых путей
        return path.expanduser()


def is_nda_path(
    path: str | Path,
    roots: Iterable[str | Path] | None = None,
) -> bool:
    """Лежит ли путь внутри защищённого каталога."""
    resolved_roots = nda_roots(roots)
    if not resolved_roots:
        return False
    target = _resolved(Path(str(path)))
    for root in resolved_roots:
        try:
            if target == _resolved(root) or target.is_relative_to(_resolved(root)):
                return True
        except ValueError:  # pragma: no cover - разные диски на Windows
            continue
    return False


def check_nda_export(
    section: ProfileSection,
    destination: str | Path | None = None,
    *,
    roots: Iterable[str | Path] | None = None,
) -> list[Issue]:
    """Проверяет, можно ли экспортировать профиль в указанное место."""
    resolved_roots = nda_roots(roots)
    issues: list[Issue] = []

    if section.nda:
        if not resolved_roots:
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    "profile.nda",
                    f"профиль помечен как NDA, но каталог не задан ({NDA_ROOTS_ENV})",
                )
            )
        if destination is None:
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    "profile.nda",
                    "NDA-профиль экспортируется только в защищённый каталог:"
                    " укажите путь назначения",
                )
            )
        elif not is_nda_path(destination, resolved_roots):
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    "profile.nda",
                    f"экспорт NDA-профиля за пределы защищённого каталога запрещён: {destination}",
                )
            )
        return issues

    for index, parameter in enumerate(section.parameters):
        source = parameter.source
        if source and is_nda_path(source, resolved_roots):
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"profile.parameters.{index}.source",
                    "источник параметра лежит в NDA-каталоге: профиль должен быть помечен как NDA",
                )
            )
    return issues
