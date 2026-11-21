"""Проекция профиля в ``P_gen``, ``P_sim``, ``P_hw`` (T3-04, FR-PRF-03).

Проекция — это отображение «параметр профиля → ключ вектора». Параметр
класса ``C`` синтезирует геометрию мира, ``E`` — эмулируемое поведение,
``M`` — метаданные прогона, ``N`` не проецируется вовсе.

Проекция блокируется, если параметр класса ``C``/``E`` не имеет целевого
вектора или у параметра не указан класс: в этом случае векторы не
строятся, а замечания объясняют причину (G3-03, G3-04). Класс ``M``
с пустым ``target`` — не ошибка: часть метаданных в прогон не попадает.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from simorph.core.issues import Issue, IssueCode, Severity, issue
from simorph.core.models.profile import ProfileSection

__all__ = [
    "TARGETS",
    "ProjectionEntry",
    "ProjectionResult",
    "UncoveredParameter",
    "project_profile",
]

#: Порядок векторов в отчётах и интерфейсе.
TARGETS: Final[tuple[str, ...]] = ("P_gen", "P_sim", "P_hw")


@dataclass(frozen=True, slots=True)
class ProjectionEntry:
    """Одно попадание параметра в вектор."""

    parameter_id: str
    block: str | None
    klass: str
    target: str
    keys: tuple[str, ...]
    value: Any
    unit: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "parameter_id": self.parameter_id,
            "block": self.block,
            "class": self.klass,
            "target": self.target,
            "keys": list(self.keys),
            "value": self.value,
            "unit": self.unit,
        }


@dataclass(frozen=True, slots=True)
class UncoveredParameter:
    """Параметр, который не попал в целевой вектор."""

    parameter_id: str
    klass: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "parameter_id": self.parameter_id,
            "class": self.klass,
            "reason": self.reason,
        }


@dataclass(slots=True)
class ProjectionResult:
    """Результат проекции: векторы, непокрытые параметры и замечания."""

    vectors: dict[str, list[ProjectionEntry]] = field(default_factory=dict)
    uncovered: list[UncoveredParameter] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    blocked: bool = False

    @property
    def ready(self) -> bool:
        """Проекция построена и готова к использованию миром и агентом."""
        return not self.blocked

    def counts(self) -> dict[str, int]:
        return {target: len(self.vectors.get(target, [])) for target in TARGETS}

    def key_map(self, target: str) -> dict[str, list[str]]:
        """Ключ вектора → параметры, которые его заполняют."""
        mapping: dict[str, list[str]] = {}
        for entry in self.vectors.get(target, []):
            for key in entry.keys:
                mapping.setdefault(key, []).append(entry.parameter_id)
        return mapping

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "blocked": self.blocked,
            "counts": self.counts(),
            "vectors": {
                target: [entry.to_dict() for entry in self.vectors.get(target, [])]
                for target in TARGETS
            },
            "uncovered": [item.to_dict() for item in self.uncovered],
            "issues": [item.to_dict() for item in self.issues],
        }


def project_profile(section: ProfileSection) -> ProjectionResult:
    """Строит векторы ``P_gen``/``P_sim``/``P_hw`` из профиля."""
    result = ProjectionResult(vectors={target: [] for target in TARGETS})
    blocking: list[Issue] = []

    for index, parameter in enumerate(section.parameters):
        prefix = f"profile.parameters.{index}"
        if parameter.klass is None:
            blocking.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"{prefix}.class",
                    "класс не указан: проекция заблокирована",
                )
            )
            result.uncovered.append(UncoveredParameter(parameter.id, None, "класс не указан"))
            continue

        if parameter.klass == "N":
            continue

        if parameter.target is None:
            # Класс М без вектора — норма: часть метаданных в прогон не идёт.
            if parameter.klass in {"C", "E"}:
                blocking.append(
                    issue(
                        IssueCode.INVALID_VALUE,
                        f"{prefix}.target",
                        f"параметр класса {parameter.klass!r} не проецируется ни в один вектор",
                    )
                )
            result.uncovered.append(
                UncoveredParameter(parameter.id, parameter.klass, "нет целевого вектора")
            )
            continue

        if not parameter.projection:
            result.issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"{prefix}.projection",
                    "не перечислены ключи проекции",
                    severity=Severity.WARNING,
                )
            )

        value = parameter.value if parameter.value is not None else list(parameter.range or ())
        result.vectors[parameter.target].append(
            ProjectionEntry(
                parameter_id=parameter.id,
                block=parameter.block,
                klass=parameter.klass,
                target=parameter.target,
                keys=tuple(parameter.projection),
                value=value,
                unit=parameter.unit,
            )
        )

    if blocking:
        result.blocked = True
        result.issues = blocking + result.issues
        result.vectors = {target: [] for target in TARGETS}
    return result
