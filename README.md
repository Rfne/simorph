# simorph

Настольная среда калибровки бортовых конвейеров патрульного робота (НРТК/БВС).
Проект учебный: магистерская диссертация ИТМО, направление 27.04.03.

`simorph` собирает, проверяет и калибрует бортовой «разум» на PC-прототипе
до переноса на целевые платы RK3588 (RKNN) и A733 (ACUITY/NBG). Командный
контур, ГИС, BIM и ЭДО в границы проекта не входят.

Документация:

- `docs/TRD-v1.0.md` — технические требования;
- `docs/WP-v.1.0 (release-0, MVP).md` — рабочий план Release-0;
- `docs/adr/` — архитектурные решения;
- `CHANGELOG.md` — история изменений.

## Требования

- Python **3.13.7** (ровно эта версия; фиксируется `.python-version` и CI);
- Windows 10/11 — основная платформа, Ubuntu 24.04 — вторичная;
- для сборки PyBullet из исходников на Windows нужны MSVC Build Tools,
  на Linux — `build-essential` (готовых wheel для Python 3.13 нет).

## Установка

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux:   source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements-dev.lock
pip install -e . --no-deps
```

`requirements.lock` — только runtime, `requirements-dev.lock` — runtime и
инструменты разработки. Оба файла генерируются `pip-compile` из `pyproject.toml`.

## Проверка окружения

```bash
simorph version            # человекочитаемый список версий
simorph version --json     # машиночитаемый вывод
simorph selftest           # проверка обязательных зависимостей
simorph selftest --ui --offscreen   # Qt без дисплея (как в CI)
```

Коды возврата: `0` — успех, `1` — ошибка данных или конфигурации,
`2` — внутренняя ошибка. Лог пишется в stderr, JSON-lines — в `--log-file`.

## Работа с проектом

```bash
simorph project new examples/demo/project.json --name demo   # создать
simorph project validate examples/demo/project.json          # проверить
simorph project migrate examples/demo/project.json           # мигрировать
simorph project hash examples/demo/project.json              # sha256
```

`project.json` — один переносимый документ из семи разделов: `project`,
`profile`, `world`, `agent`, `exercise`, `pipeline`, `report`. Неизвестные
поля запрещены, у каждого раздела свой `schema_version`. Запись атомарная:
рядом появляются `.bak` (предыдущая версия) и `.lock` (на время работы).
Решение и последствия — `docs/adr/0002-project-json-document.md`.

## Графическая оболочка

```bash
simorph app                          # пустой проект в памяти
simorph app examples/demo/project.json
simorph app examples/demo/project.json --offscreen   # без дисплея
```

Рабочее место: дерево проекта слева, инспектор свойств справа, вкладки
разделов в центре, панели валидации и поиска — док-панели. Правки идут
через `QUndoStack` (Ctrl+Z / Ctrl+Y, лимит 100 шагов), проект
автосохраняется в `project.json.autosave` и восстанавливается после
аварийного завершения. Если проект уже открыт другим экземпляром, окно
работает в режиме «только чтение»: инспектор и сохранение блокируются.
Тема переключается в меню «Вид → Тема» без перезапуска.

## Разработка

```bash
ruff check .
ruff format --check .
mypy
pytest -m unit
pytest --cov=simorph --cov-report=term-missing
pre-commit install
```

Маркеры тестов: `unit`, `integration`, `ui`, `perf`, `regression`.

## Структура

```
src/simorph/
  cli/          командная строка, коды возврата, команды project и app
  core/         схемы разделов, канонизация, миграции, репозиторий, валидатор,
                доступ к значениям по пути
  ui/           главное окно, дерево, инспектор, вкладки, undo/redo, сессия,
                панели валидации и поиска
  selftest/     самопроверка окружения и Qt
  logging_setup.py  структурированное логирование, redaction
  settings.py   настройки приложения в QSettings
  version.py    сбор версий из метаданных дистрибутивов
tests/          unit, integration, ui, regression, golden
scripts/        служебные скрипты (отчёт по лицензиям)
examples/       демонстрационный проект
docs/           TRD, WP, ADR
```

## Лицензии

Код проекта — MIT (`LICENSE`). Графическая библиотека — PySide6 под
LGPLv3, поэтому MIT-код поставляется вместе с ней без перевода всего
произведения под GPLv3; условия поставки — в `NOTICE`, решение —
`docs/adr/0003-pyside6-instead-of-pyqt6.md`. Перечень сторонних лицензий —
`THIRD_PARTY_LICENSES.md`.
