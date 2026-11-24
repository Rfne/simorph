"""Скрипты конвертации ONNX → RKNN и ONNX → NBG, кладущиеся в пакет (T11-06).

Скрипты не зависят от simorph: на бортовой машине может не быть ни Python-
пакета, ни его зависимостей, а вендорский тулчейн ставится отдельно. Поэтому
в пакет кладутся самодостаточные файлы, которые читают ``manifest.yaml``
рядом и запускаются руками:

    python scripts/convert_rknn.py --check
    python scripts/convert_rknn.py

Ключевое свойство — отсутствие тулчейна не является падением: скрипт
сообщает, чего не хватает, и возвращает код 2. Так экспорт пакета проходит
на машине без RKNN/ACUITY (FR-DEP-09).
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "INSTALL_DOC",
    "NBG_SCRIPT",
    "RKNN_SCRIPT",
    "SCRIPT_NAMES",
    "script_for",
]

#: Имя скрипта конвертации по цели.
SCRIPT_NAMES: Final[dict[str, str]] = {
    "rk3588": "convert_rknn.py",
    "a733": "convert_nbg.py",
}


RKNN_SCRIPT: Final = '''#!/usr/bin/env python3
"""ONNX -> RKNN для Rockchip RK3588.

Запуск на машине с RKNN-Toolkit2:

    python convert_rknn.py --check
    python convert_rknn.py --source ../models/det_kp.onnx --output ../models/det_kp.rknn

Без тулчейна скрипт сообщает об этом и завершается с кодом 2: пакет
собирается и без RKNN, конвертация — отдельный шаг.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_SOURCE = Path(__file__).resolve().parent.parent / "models" / "det_kp.onnx"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "models" / "det_kp.rknn"
DEFAULT_DATASET = Path(__file__).resolve().parent.parent / "calib" / "dataset.txt"


def toolchain_available() -> bool:
    try:
        import rknn.api  # noqa: F401
    except ImportError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ONNX -> RKNN (RK3588)")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--check", action="store_true", help="только проверить тулчейн")
    args = parser.parse_args(argv)

    if not toolchain_available():
        print("RKNN-Toolkit2 не установлен: pip install rknn-toolkit2", file=sys.stderr)
        return 2
    if args.check:
        print("RKNN-Toolkit2 доступен")
        return 0
    if not args.source.is_file():
        print(f"нет модели: {args.source}", file=sys.stderr)
        return 1

    from rknn.api import RKNN

    rknn = RKNN(verbose=True)
    rknn.config(target_platform="rk3588", mean_values=[[0, 0, 0]], std_values=[[255, 255, 255]])
    if rknn.load_onnx(model=str(args.source), inputs=["image"]) != 0:
        print("не удалось загрузить ONNX", file=sys.stderr)
        return 1
    quantize = args.dataset.is_file()
    if not quantize:
        print("калибровочный набор не найден: сборка без квантизации", file=sys.stderr)
    if rknn.build(do_quantization=quantize, dataset=str(args.dataset) if quantize else None) != 0:
        print("сборка RKNN не удалась: проверьте поддержку операторов", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if rknn.export_rknn(str(args.output)) != 0:
        print("экспорт RKNN не удался", file=sys.stderr)
        return 1
    rknn.release()
    print(f"готово: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


NBG_SCRIPT: Final = '''#!/usr/bin/env python3
"""ONNX -> NBG для Allwinner A733 через ACUITY Toolkit.

ACUITY — набор консольных утилит `pegasus`; запуск на машине с тулчейном:

    python convert_nbg.py --check
    python convert_nbg.py --source ../models/det_kp.onnx --output ../models/det_kp.nbg

Без `pegasus` скрипт сообщает об этом и завершается с кодом 2.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_SOURCE = Path(__file__).resolve().parent.parent / "models" / "det_kp.onnx"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "models" / "det_kp.nbg"


def pegasus() -> str | None:
    return shutil.which("pegasus")


def run(*args: str) -> int:
    print("pegasus", " ".join(args))
    return subprocess.call(["pegasus", *args])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ONNX -> NBG (A733)")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="только проверить тулчейн")
    args = parser.parse_args(argv)

    if pegasus() is None:
        print("ACUITY Toolkit не найден: нужен pegasus в PATH", file=sys.stderr)
        return 2
    if args.check:
        print("ACUITY Toolkit доступен")
        return 0
    if not args.source.is_file():
        print(f"нет модели: {args.source}", file=sys.stderr)
        return 1

    work = args.output.parent / "acuity"
    work.mkdir(parents=True, exist_ok=True)
    code = run("import", "onnx", "--model", str(args.source), "--output-dir", str(work))
    if code != 0:
        print("импорт ONNX не удался: проверьте поддержку операторов", file=sys.stderr)
        return 1
    code = run("quantize", "--model", str(work), "--output-dir", str(work))
    if code != 0:
        print("квантизация не удалась: нужен калибровочный набор", file=sys.stderr)
        return 1
    code = run("export", "ovxlib", "--model", str(work), "--output-dir", str(args.output.parent))
    if code != 0:
        print("экспорт NBG не удался", file=sys.stderr)
        return 1
    print(f"готово: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


INSTALL_DOC: Final = """# Установка пакета на борт

Пакет собран simorph и не содержит вендорских SDK: RKNN-Toolkit2 и ACUITY
ставятся отдельно на машине конвертации.

## 1. Проверить пакет

```
sha256sum -c checksums.sha256
```

Все строки должны быть `OK`. Манифест `manifest.yaml` описывает цель, SoC,
модели и бюджеты; `manifest_hash` — подпись содержимого.

## 2. Сконвертировать модель (если в пакете только ONNX)

```
python scripts/convert_rknn.py --check
python scripts/convert_rknn.py
```

Для A733 — `scripts/convert_nbg.py` (нужен `pegasus` из ACUITY Toolkit).
Отсутствие тулчейна не ошибка пакета: конвертация выполняется отдельно.

## 3. Скопировать на борт

```
scp -r <пакет> user@board:/opt/simorph/
```

Каталог `models/` — веса, `pipelines/` — конфигурация конвейера,
`calib/` — калибровка квантизации.

## 4. Проверить бюджет

`budgets.inference_p95_ms` и `budgets.memory_mb` из манифеста — целевые
значения профиля; замер на борту сравнивается с ними.
"""


def script_for(target: str) -> str:
    """Текст скрипта конвертации для цели."""
    if target == "rk3588":
        return RKNN_SCRIPT
    if target == "a733":
        return NBG_SCRIPT
    raise KeyError(target)
