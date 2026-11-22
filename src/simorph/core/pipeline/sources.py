"""Read-only исходники конвейера и обнаружение устаревания (T8-05, G8-05).

Окно исходников показывает дерево каталога и текст файла, но никогда не
пишет в него: изменение кода — задача внешнего редактора. Чтобы понять,
что вариант конвейера устарел, рядом с проектом хранится снимок хэшей
файлов (``project.json.sources.json``). Сравнение текущего дерева со
снимком даёт статус ``fresh`` или ``stale`` и списки изменённых,
добавленных и удалённых файлов.

Снимок не входит в ``project.json``: это артефакт рабочего места, а не
часть переносимого документа. Поэтому схема проекта и его хэш не меняются.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from simorph.core.errors import ProjectError

__all__ = [
    "LANGUAGE_BY_SUFFIX",
    "SOURCE_SNAPSHOT_VERSION",
    "SUPPORTED_SUFFIXES",
    "SourceFile",
    "SourceStatus",
    "SourceTree",
    "check_stale",
    "external_open",
    "language_for",
    "load_snapshot",
    "save_snapshot",
    "scan_sources",
    "snapshot_path",
    "snapshot_payload",
]

#: Версия формата снимка исходников.
SOURCE_SNAPSHOT_VERSION = "1.0"

#: Расширения, которые показываются в дереве исходников.
SUPPORTED_SUFFIXES: tuple[str, ...] = (
    ".py",
    ".json",
    ".yaml",
    ".yml",
    ".txt",
    ".md",
    ".toml",
    ".cfg",
    ".ini",
    ".j2",
    ".urdf",
)

#: Язык подсветки по расширению.
LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": "python",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".txt": "text",
    ".md": "markdown",
    ".toml": "toml",
    ".cfg": "ini",
    ".ini": "ini",
    ".j2": "jinja",
    ".urdf": "xml",
}

#: Каталоги, которые не сканируются.
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        "build",
        "dist",
    }
)

#: Предел чтения файла в окне исходников, байт.
MAX_READ_BYTES = 512 * 1024

#: Предел числа файлов в дереве.
MAX_FILES = 2000


@dataclass(frozen=True, slots=True)
class SourceFile:
    """Файл исходников: относительный путь, размер и хэш."""

    path: str
    size: int
    sha256: str
    language: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size": self.size,
            "sha256": self.sha256,
            "language": self.language,
        }


@dataclass(slots=True)
class SourceTree:
    """Снимок дерева исходников."""

    root: Path
    files: list[SourceFile] = field(default_factory=list)
    truncated: bool = False

    @property
    def digest(self) -> str:
        """Хэш дерева: зависит от путей и содержимого, не от порядка обхода."""
        hasher = hashlib.sha256()
        for item in sorted(self.files, key=lambda value: value.path):
            hasher.update(f"{item.path}\0{item.sha256}\n".encode())
        return hasher.hexdigest()

    def file(self, relative: str) -> SourceFile | None:
        target = relative.replace("\\", "/")
        for item in self.files:
            if item.path == target:
                return item
        return None

    def read(self, relative: str) -> str:
        """Читает файл только для чтения.

        Raises:
            ProjectError: файл вне корня, отсутствует или не читается.
        """
        root = self.root.resolve()
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            raise ProjectError(f"{relative!r}: путь выходит за корень исходников")
        if not target.is_file():
            raise ProjectError(f"{relative!r}: файл не найден")
        try:
            data = target.read_bytes()[:MAX_READ_BYTES]
        except OSError as exc:
            raise ProjectError(f"{relative!r}: не удалось прочитать файл: {exc}") from exc
        text = data.decode("utf-8", errors="replace")
        if target.stat().st_size > MAX_READ_BYTES:
            text += "\n… файл показан не полностью\n"
        return text

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "digest": self.digest,
            "files": [item.to_dict() for item in self.files],
            "truncated": self.truncated,
        }


@dataclass(slots=True)
class SourceStatus:
    """Сравнение текущего дерева со снимком."""

    state: str
    digest: str
    expected: str | None = None
    changed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def stale(self) -> bool:
        return self.state == "stale"

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "digest": self.digest,
            "expected": self.expected,
            "changed": list(self.changed),
            "added": list(self.added),
            "removed": list(self.removed),
        }


def language_for(path: str | Path) -> str:
    """Язык подсветки по расширению файла."""
    return LANGUAGE_BY_SUFFIX.get(Path(path).suffix.lower(), "text")


def scan_sources(root: Path | str, *, max_files: int = MAX_FILES) -> SourceTree:
    """Сканирует каталог и считает хэши поддерживаемых файлов.

    Raises:
        ProjectError: каталог не существует или не является каталогом.
    """
    base = Path(root).resolve()
    if not base.exists():
        raise ProjectError(f"каталог исходников не найден: {base}")
    if not base.is_dir():
        raise ProjectError(f"путь исходников не является каталогом: {base}")

    files: list[SourceFile] = []
    truncated = False
    for directory, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if name not in _SKIP_DIRS)
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            relative = path.relative_to(base).as_posix()
            files.append(
                SourceFile(
                    path=relative,
                    size=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                    language=language_for(path),
                )
            )
            if len(files) >= max_files:
                truncated = True
                break
        if truncated:
            break
    files.sort(key=lambda item: item.path)
    return SourceTree(root=base, files=files, truncated=truncated)


def snapshot_path(project_path: Path | str) -> Path:
    """Путь к снимку исходников рядом с проектом."""
    target = Path(project_path)
    return target.with_name(f"{target.name}.sources.json")


def snapshot_payload(tree: SourceTree) -> dict[str, Any]:
    """Содержимое снимка: версия, хэш дерева и список файлов."""
    return {
        "schema_version": SOURCE_SNAPSHOT_VERSION,
        "digest": tree.digest,
        "root": str(tree.root),
        "truncated": tree.truncated,
        "files": [item.to_dict() for item in tree.files],
    }


def save_snapshot(tree: SourceTree, path: Path | str) -> Path:
    """Пишет снимок атомарно: недописанный файл не станет готовым."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.tmp")
    temporary.write_text(
        json.dumps(snapshot_payload(tree), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def load_snapshot(path: Path | str) -> dict[str, Any]:
    """Читает снимок; отсутствие или порча файла — пустой словарь."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def check_stale(tree: SourceTree, snapshot: dict[str, Any]) -> SourceStatus:
    """Сравнивает дерево со снимком и возвращает статус устаревания."""
    expected = snapshot.get("digest")
    if not isinstance(expected, str) or not expected:
        return SourceStatus(state="missing", digest=tree.digest)
    old_files = {
        str(item.get("path")): str(item.get("sha256"))
        for item in snapshot.get("files", [])
        if isinstance(item, dict) and item.get("path") and item.get("sha256")
    }
    new_files = {item.path: item.sha256 for item in tree.files}
    changed = sorted(
        path
        for path, digest in new_files.items()
        if path in old_files and old_files[path] != digest
    )
    added = sorted(path for path in new_files if path not in old_files)
    removed = sorted(path for path in old_files if path not in new_files)
    state = "stale" if changed or added or removed else "fresh"
    return SourceStatus(
        state=state,
        digest=tree.digest,
        expected=expected,
        changed=changed,
        added=added,
        removed=removed,
    )


def external_open(path: Path | str) -> bool:
    """Открывает файл ассоциированным приложением.

    В тестах не вызывается: это действие пользователя, а не частичный
    сценарий. Возвращает ``True``, если процесс запуска удалось создать.
    """
    target = str(Path(path))
    starter = getattr(os, "startfile", None)
    if starter is not None:
        starter(target)
        return True
    command = ["open", target] if sys.platform == "darwin" else ["xdg-open", target]
    try:
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return True
