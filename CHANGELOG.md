# Журнал изменений

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версионирование — [Semantic Versioning](https://semver.org/lang/ru/).

## [Unreleased]

## [0.2.0] — 2026-11-20

Этап 1 «Ядро проекта».

### Добавлено

- Схемы семи разделов проекта на pydantic: `project`, `profile`, `world`,
  `agent`, `exercise`, `pipeline`, `report`; неизвестные поля запрещены,
  у каждого раздела свой `schema_version`.
- Канонизация JSON и sha256 конфигурации: сортировка ключей, отсутствие
  пробелов, время в UTC до секунды.
- Миграции `vN → vN+1` с идемпотентностью и отказом на версии из будущего.
- Репозиторий проекта: UTF-8, переводы строк `\n`, атомарная замена через
  `.tmp`, резервная копия `.bak`, блокировка `.lock` с обнаружением
  устаревших блокировок.
- Валидатор: шесть классов ошибок (отсутствующее поле, неверный тип,
  висячая ссылка, дубль id, невалидный диапазон, неизвестная версия),
  проверка графов миссии и конвейера.
- Настройки приложения в `QSettings`: тема, язык, недавние проекты, путь
  к внешнему редактору; в проект не попадают.
- CLI: `simorph project new | validate | migrate | hash` с кодами 0/1/2
  и JSON-выводом.
- Демонстрационный проект `examples/demo/project.json`.
- Golden-эталон `tests/golden/demo_project_v1.json` и ADR-0002 о формате
  проекта.

### Изменено

- CI закреплён на `ubuntu-24.04` (в `ubuntu-latest` переехал на 26.04, где нет
  сборки Python 3.13.7), типы проверяются под Windows и Linux на обоих
  раннерах.
- Добавлен `.gitattributes` с `* text=auto eol=lf`: рабочая копия на любой ОС
  получает LF, побайтовые проверки не зависят от `core.autocrlf`.
- Платформенные ветки в `core/locking.py` разведены по `sys.platform`, чтобы
  `mypy --strict` сходился и на Windows, и на Linux.

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
