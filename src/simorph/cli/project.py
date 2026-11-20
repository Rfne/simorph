"""Команды ``simorph project``: new, validate, migrate, hash (T1-07)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from simorph.cli.exit_codes import ExitCode
from simorph.core.canonical import compute_hash, dump_document
from simorph.core.errors import ProjectError, ProjectValidationError, StaleLockError
from simorph.core.factory import new_project
from simorph.core.issues import has_errors
from simorph.core.locking import ProjectLock
from simorph.core.migrations import migrate_payload
from simorph.core.repository import read_json, save_project, write_text_atomic
from simorph.core.validation import parse_document, validate_payload

#: Имя файла проекта по умолчанию.
DEFAULT_PROJECT_NAME = "project.json"

#: Подсказка для флага снятия устаревшей блокировки.
TAKEOVER_HELP = "снять устаревшую блокировку, оставленную завершившимся процессом"


def add_common_options(parser: argparse.ArgumentParser) -> None:
    """Повторяет общие флаги для подкоманд проекта."""
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("-v", "--verbose", action="count", default=argparse.SUPPRESS)
    parser.add_argument("--log-file", type=Path, default=argparse.SUPPRESS)


def build_project_parser(subparsers: Any) -> None:
    """Регистрирует подкоманды ``project``."""
    project = subparsers.add_parser("project", help="операции с файлом проекта")
    add_common_options(project)
    actions = project.add_subparsers(dest="project_command", metavar="ДЕЙСТВИЕ")

    new_parser = actions.add_parser("new", help="создать новый проект")
    add_common_options(new_parser)
    new_parser.add_argument("path", type=Path, help=f"путь к файлу ({DEFAULT_PROJECT_NAME})")
    new_parser.add_argument("--name", default=None, help="имя проекта (по умолчанию — имя файла)")
    new_parser.add_argument("--seed", type=int, default=42, help="seed мира и упражнения")
    new_parser.add_argument("--force", action="store_true", help="перезаписать существующий файл")
    new_parser.add_argument("--takeover-lock", action="store_true", help=TAKEOVER_HELP)

    validate_parser = actions.add_parser("validate", help="проверить проект")
    add_common_options(validate_parser)
    validate_parser.add_argument("path", type=Path, help="путь к файлу проекта")

    migrate_parser = actions.add_parser("migrate", help="мигрировать проект к текущей версии")
    add_common_options(migrate_parser)
    migrate_parser.add_argument("path", type=Path, help="путь к файлу проекта")
    migrate_parser.add_argument(
        "--dry-run", action="store_true", help="показать шаги, не записывая файл"
    )
    migrate_parser.add_argument("--takeover-lock", action="store_true", help=TAKEOVER_HELP)

    hash_parser = actions.add_parser("hash", help="напечатать sha256 конфигурации")
    add_common_options(hash_parser)
    hash_parser.add_argument("path", type=Path, help="путь к файлу проекта")


def _write(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def _write_json(payload: object) -> None:
    _write(json.dumps(payload, ensure_ascii=False, indent=2))


def _resolve_target(path: Path) -> Path:
    """Если передан каталог, проект ищется внутри него."""
    return path / DEFAULT_PROJECT_NAME if path.is_dir() else path


def _acquire_lock(target: Path, *, takeover_stale: bool, logger: logging.Logger) -> ProjectLock:
    """Захватывает блокировку проекта перед записью (G1-04).

    Устаревшая блокировка не снимается молча: пользователь подтверждает
    это флагом ``--takeover-lock``, иначе можно потерять чужую работу.
    """
    lock = ProjectLock(target)
    try:
        lock.acquire(takeover_stale=takeover_stale)
    except StaleLockError as exc:
        raise StaleLockError(
            f"{exc}; снимите её флагом --takeover-lock", holder=exc.holder
        ) from None
    logger.debug("блокировка захвачена: %s", lock.path)
    return lock


def cmd_new(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    """Создаёт новый проект и сохраняет его на диск."""
    target = _resolve_target(Path(args.path))
    lock = _acquire_lock(target, takeover_stale=bool(args.takeover_lock), logger=logger)
    try:
        if target.exists() and not args.force:
            raise ProjectError(f"файл уже существует: {target} (используйте --force)")

        document = new_project(args.name or target.stem or "project", seed=int(args.seed))
        save_project(document, target, make_backup=target.exists())
    finally:
        lock.release()

    digest = compute_hash(document)
    logger.info("создан проект %s (%s)", target, digest)

    if as_json:
        _write_json(
            {
                "status": "ok",
                "path": str(target),
                "project_id": str(document.project_id),
                "schema_version": document.schema_version,
                "hash": digest,
            }
        )
    else:
        _write(f"создан проект: {target}")
        _write(f"project_id    {document.project_id}")
        _write(f"hash          {digest}")
    return int(ExitCode.OK)


def cmd_validate(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    """Проверяет проект и возвращает код 0 или 1."""
    target = _resolve_target(Path(args.path))
    payload = read_json(target)
    issues = validate_payload(payload)
    ok = not has_errors(issues)

    if not ok:
        logger.error("проект не прошёл проверку: %s замечаний", len(issues))

    if as_json:
        _write_json(
            {
                "status": "ok" if ok else "error",
                "path": str(target),
                "issues": [item.to_dict() for item in issues],
            }
        )
    else:
        _write(f"проект  {target}")
        _write(f"статус  {'ok' if ok else 'error'}")
        for item in issues:
            _write(f"  {item.severity}: {item.path}: {item.message}")
        if not issues:
            _write("замечаний нет")

    return int(ExitCode.OK) if ok else int(ExitCode.DATA_ERROR)


def cmd_migrate(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    """Применяет цепочку миграций, сохраняя резервную копию."""
    target = _resolve_target(Path(args.path))
    payload = read_json(target)
    result = migrate_payload(payload, section="project")

    if not result.applied:
        if as_json:
            _write_json({"status": "ok", "path": str(target), "applied": []})
        else:
            _write(f"миграция не требуется: {target}")
        return int(ExitCode.OK)

    if args.dry_run:
        if as_json:
            _write_json({"status": "ok", "path": str(target), "applied": result.applied})
        else:
            _write(f"будут применены шаги: {', '.join(result.applied)}")
        return int(ExitCode.OK)

    lock = _acquire_lock(target, takeover_stale=bool(args.takeover_lock), logger=logger)
    try:
        try:
            document = parse_document(result.payload)
        except ProjectValidationError as exc:
            raise ProjectError(
                "мигрированный проект не прошёл проверку схемы, файл не изменён"
            ) from exc

        backup = write_text_atomic(target, dump_document(document), make_backup=True)
    finally:
        lock.release()

    logger.info("миграция применена: %s", ", ".join(result.applied))

    if as_json:
        _write_json(
            {
                "status": "ok",
                "path": str(target),
                "applied": result.applied,
                "backup": str(backup) if backup else None,
                "hash": compute_hash(document),
            }
        )
    else:
        _write(f"применено: {', '.join(result.applied)}")
        if backup:
            _write(f"резервная копия: {backup}")
    return int(ExitCode.OK)


def cmd_hash(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    """Печатает sha256 канонического представления проекта."""
    target = _resolve_target(Path(args.path))
    payload = read_json(target)
    result = migrate_payload(payload, section="project")
    document = parse_document(result.payload)
    digest = compute_hash(document)
    logger.debug("хэш проекта %s: %s", target, digest)

    if as_json:
        _write_json({"status": "ok", "path": str(target), "hash": digest})
    else:
        _write(digest)
    return int(ExitCode.OK)


#: Таблица обработчиков подкоманд.
HANDLERS = {
    "new": cmd_new,
    "validate": cmd_validate,
    "migrate": cmd_migrate,
    "hash": cmd_hash,
}


def run_project_command(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    """Диспетчер подкоманд ``project``."""
    name = getattr(args, "project_command", None)
    handler = HANDLERS.get(name) if isinstance(name, str) else None
    if handler is None:
        raise ProjectError("не указано действие: new, validate, migrate или hash")
    return handler(args, as_json=as_json, logger=logger)
