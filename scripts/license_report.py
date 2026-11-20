"""Формирует ``THIRD_PARTY_LICENSES.md`` по установленным пакетам.

Запуск: ``python scripts/license_report.py``.
Скрипт опирается на ``pip-licenses`` (входит в extra ``dev``) и не требует сети.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

HEADER = """# Сторонние лицензии

Файл сгенерирован командой `python scripts/license_report.py`
на основе установленного окружения (`pip-licenses`).

Проект распространяется под MIT (см. `LICENSE`). Графическая библиотека —
PySide6, лицензия LGPLv3 (двойная: LGPL-3.0 / GPL-2.0 / GPL-3.0, проект
использует LGPL-3.0). Условия поставки зафиксированы в `NOTICE`
(NFR-LIC-02, ADR-0003).

| Пакет | Версия | Лицензия |
|---|---|---|
"""


def _collect() -> list[dict[str, Any]]:
    result = subprocess.run(
        [sys.executable, "-m", "piplicenses", "--format=json", "--with-urls"],
        check=True,
        capture_output=True,
        text=True,
    )
    packages: list[dict[str, Any]] = json.loads(result.stdout)
    return packages


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    packages = _collect()

    rows = [
        f"| {_escape(str(pkg['Name']))} | {_escape(str(pkg['Version']))} | "
        f"{_escape(str(pkg['License']))} |"
        for pkg in sorted(packages, key=lambda item: str(item["Name"]).lower())
    ]
    target = repo_root / "THIRD_PARTY_LICENSES.md"
    # newline="\n" обязателен: иначе на Windows файл получит CRLF и хуки
    # pre-commit будут править его при каждом прогоне.
    with target.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(HEADER + "\n".join(rows) + "\n")

    sys.stdout.write(f"записано {len(rows)} пакетов в {target.name}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
