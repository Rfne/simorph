"""Контрольные суммы пакета: ``checksums.sha256`` и его проверка (T11-06, FR-DEP-07).

Формат — как у ``sha256sum``: хэш, два пробела, путь относительно корня
пакета. Так файл читается стандартными утилитами на борту и остаётся
диффуемым: порядок строк стабилен (сортировка по пути), переводы строк —
``\\n`` на любой ОС.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from simorph.core.package.manifest import CHECKSUMS_NAME

__all__ = [
    "ChecksumCheck",
    "ChecksumEntry",
    "read_checksums",
    "verify_checksums",
    "write_checksums",
]

#: Файлы, которые не попадают в собственные контрольные суммы.
SELF_EXCLUDED: Final[frozenset[str]] = frozenset({CHECKSUMS_NAME})


@dataclass(frozen=True, slots=True)
class ChecksumEntry:
    """Строка контрольных сумм: путь относительно корня и хэш."""

    path: str
    sha256: str

    def line(self) -> str:
        return f"{self.sha256}  {self.path}"


@dataclass(slots=True)
class ChecksumCheck:
    """Результат сверки контрольных сумм с файлами."""

    entries: int = 0
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues and self.entries > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.ok else "error",
            "entries": self.entries,
            "issues": list(self.issues),
        }

    def summary(self) -> str:
        state = "сходятся" if self.ok else "не сходятся"
        detail = "" if self.ok else " · " + "; ".join(self.issues)
        return f"контрольные суммы {state} · файлов {self.entries}{detail}"


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def collect_entries(
    root: Path | str, *, exclude: frozenset[str] = SELF_EXCLUDED
) -> list[ChecksumEntry]:
    """Все файлы пакета, кроме собственных контрольных сумм."""
    base = Path(root)
    entries: list[ChecksumEntry] = []
    for path in sorted(item for item in base.rglob("*") if item.is_file()):
        relative = path.relative_to(base).as_posix()
        if relative in exclude:
            continue
        entries.append(ChecksumEntry(path=relative, sha256=_digest(path)))
    return entries


def write_checksums(
    root: Path | str,
    *,
    name: str = CHECKSUMS_NAME,
    exclude: frozenset[str] = SELF_EXCLUDED,
) -> list[ChecksumEntry]:
    """Пишет ``checksums.sha256`` в корень пакета и возвращает строки."""
    base = Path(root)
    base.mkdir(parents=True, exist_ok=True)
    entries = collect_entries(base, exclude=exclude | {name})
    text = "".join(f"{entry.line()}\n" for entry in entries)
    (base / name).write_text(text, encoding="utf-8", newline="\n")
    return entries


def read_checksums(path: Path | str) -> list[ChecksumEntry]:
    """Читает файл контрольных сумм, пропуская пустые строки."""
    entries: list[ChecksumEntry] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        entries.append(ChecksumEntry(path=parts[1].lstrip("*").strip(), sha256=parts[0]))
    return entries


def verify_checksums(
    root: Path | str,
    *,
    name: str = CHECKSUMS_NAME,
) -> ChecksumCheck:
    """Сверяет файл сумм с содержимым пакета: лишние и недостающие тоже ошибка."""
    base = Path(root)
    check = ChecksumCheck()
    target = base / name
    if not target.is_file():
        check.issues.append(f"файл сумм не найден: {name}")
        return check
    entries = read_checksums(target)
    check.entries = len(entries)
    if not entries:
        check.issues.append("файл сумм пуст")
        return check
    listed: set[str] = set()
    for entry in entries:
        listed.add(entry.path)
        candidate = base / entry.path
        if not candidate.is_file():
            check.issues.append(f"файл отсутствует: {entry.path}")
            continue
        actual = _digest(candidate)
        if actual != entry.sha256:
            check.issues.append(f"хэш не совпадает: {entry.path}")
    actual_files = {
        path.relative_to(base).as_posix()
        for path in base.rglob("*")
        if path.is_file() and path.relative_to(base).as_posix() not in SELF_EXCLUDED | {name}
    }
    for missing in sorted(actual_files - listed):
        check.issues.append(f"файл не учтён в суммах: {missing}")
    return check
