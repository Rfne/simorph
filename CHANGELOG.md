# Журнал изменений

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версионирование — [Semantic Versioning](https://semver.org/lang/ru/).

## [Unreleased]

## [0.1.0] — 2026-11-20

Этап 0 «Каркас и окружение».

### Добавлено

- Каркас пакета `simorph` (setuptools, `src`-layout), editable-установка,
  `.python-version` = 3.13.7, `requires-python = ">=3.13,<3.14"`.
- Lock-файлы `requirements.lock` (runtime) и `requirements-dev.lock`
  (runtime и инструменты разработки).
- CLI: `simorph version`, `simorph selftest [--ui] [--offscreen]`, общие флаги
  `--json`, `-v/--verbose`, `--log-file`; коды возврата 0/1/2.
- Структурированное логирование: `run_id` в каждой записи, JSON-lines в файл,
  текст в stderr, маскирование путей NDA-каталогов.
- Самопроверка Qt с offscreen-платформой для CI без дисплея.
- Тестовый каркас: маркеры `unit`, `integration`, `ui`, `perf`, `regression`,
  `pytest-qt`, coverage-отчёт.
- CI GitHub Actions: Windows и Ubuntu, Python ровно 3.13.7.
- Отчёт по сторонним лицензиям `THIRD_PARTY_LICENSES.md`, файл `NOTICE`
  с разбором GPLv3 у PyQt6, шаблон ADR и ADR-0001 о выборе Python 3.13.7.

### Изменено

- `README.md` описывает требования, установку, проверки и структуру проекта.
