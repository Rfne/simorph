"""Структурированное логирование с корреляцией ``run_id`` и маскированием путей.

Правила:

* в stderr — читаемый текст, в файл — JSON-lines (по одной записи на строку);
* каждая запись содержит ``run_id``: он приходит из аргумента, переменной
  окружения ``SIMORPH_RUN_ID`` или генерируется;
* пути из защищённого (NDA) каталога заменяются на ``<redacted>`` до записи
  в любой обработчик, поэтому попасть в лог они не могут.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUN_ID_ENV = "SIMORPH_RUN_ID"
NDA_ROOTS_ENV = "SIMORPH_NDA_ROOTS"
REDACTED = "<redacted>"
LOGGER_NAME = "simorph"


def new_run_id() -> str:
    """Короткий идентификатор прогона для корреляции записей."""
    return uuid.uuid4().hex[:12]


class Redactor:
    """Заменяет пути защищённых каталогов на маркер.

    Сравнение идёт по нормализованной форме (прямые слэши), но в тексте
    заменяются оба варианта разделителей, чтобы запись не зависела от ОС.
    """

    def __init__(self, roots: Iterable[str | Path] = ()) -> None:
        normalized: list[str] = []
        for root in roots:
            text = str(root).strip()
            if not text:
                continue
            candidate = text.replace("\\", "/").rstrip("/")
            if candidate and candidate not in normalized:
                normalized.append(candidate)
        self._roots = tuple(sorted(normalized, key=len, reverse=True))

    @property
    def roots(self) -> tuple[str, ...]:
        return self._roots

    def redact(self, text: str) -> str:
        result = text
        for root in self._roots:
            for variant in (root, root.replace("/", "\\")):
                if variant in result:
                    result = result.replace(variant, REDACTED)
        return result

    @classmethod
    def from_environment(cls) -> Redactor:
        """Читает список каталогов из ``SIMORPH_NDA_ROOTS`` (через ``os.pathsep``)."""
        raw = os.environ.get(NDA_ROOTS_ENV, "")
        return cls(part for part in raw.split(os.pathsep) if part.strip())


class ContextFilter(logging.Filter):
    """Добавляет ``run_id`` и маскирует сообщение до форматирования."""

    def __init__(self, redactor: Redactor, run_id: str) -> None:
        super().__init__()
        self._redactor = redactor
        self._run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redactor.redact(record.getMessage())
        record.args = ()
        record.run_id = self._run_id
        return True


class JsonFormatter(logging.Formatter):
    """Формат JSON-lines: одна запись — одна строка."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "run_id": getattr(record, "run_id", None),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Читаемый формат для stderr."""

    def format(self, record: logging.LogRecord) -> str:
        run_id = getattr(record, "run_id", "-")
        moment = datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")
        line = f"{moment} {record.levelname:<7} [{run_id}] {record.name}: {record.getMessage()}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


def _level_for(verbose: int) -> int:
    if verbose >= 2:
        return logging.DEBUG
    if verbose == 1:
        return logging.INFO
    return logging.WARNING


def configure_logging(
    *,
    log_file: Path | None = None,
    verbose: int = 0,
    run_id: str | None = None,
    nda_roots: Iterable[str | Path] | None = None,
) -> logging.Logger:
    """Настраивает логгер приложения и возвращает его.

    Повторный вызов безопасен: прежние обработчики закрываются и заменяются.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    resolved_run_id = run_id or os.environ.get(RUN_ID_ENV) or new_run_id()
    redactor = Redactor.from_environment() if nda_roots is None else Redactor(nda_roots)
    context = ContextFilter(redactor, resolved_run_id)

    stream_handler = logging.StreamHandler(stream=sys.stderr)
    stream_handler.setLevel(_level_for(verbose))
    stream_handler.setFormatter(TextFormatter())
    stream_handler.addFilter(context)
    logger.addHandler(stream_handler)

    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(JsonFormatter())
        file_handler.addFilter(context)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Возвращает дочерний логгер приложения."""
    return logging.getLogger(LOGGER_NAME if name is None else f"{LOGGER_NAME}.{name}")
