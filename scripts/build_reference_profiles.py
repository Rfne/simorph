"""Сборка эталонных профилей в ``examples/profiles`` (T3-08).

Профили не пишутся руками: они собираются из спецификации ``core/profile/spec``
и значений заказчика, поэтому любое изменение маппинга сразу видно в диффе
и в golden-тестах. Профиль «Татнефть» помечен как NDA, поэтому лежит
в отдельном каталоге ``examples/profiles/nda``.

Запуск: ``python scripts/build_reference_profiles.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:  # pragma: no cover - запуск без установки
    sys.path.insert(0, str(ROOT / "src"))

from simorph.core.profile import (  # noqa: E402 - путь добавляется выше
    CUSTOMER_CODES,
    build_reference_profile,
    export_profile_payload,
)
from simorph.core.profile.exporting import dump_profile_export  # noqa: E402

#: Каталог эталонных профилей.
PROFILES_DIR = ROOT / "examples" / "profiles"

#: Каталог NDA-профилей: только он считается защищённым.
NDA_DIR = PROFILES_DIR / "nda"


def target_for(code: str) -> Path:
    """Путь файла профиля: NDA-профили уходят в отдельный каталог."""
    section = build_reference_profile(code)
    directory = NDA_DIR if section.nda else PROFILES_DIR
    return directory / f"{code}.json"


def main() -> int:
    written: list[Path] = []
    for code in CUSTOMER_CODES:
        section = build_reference_profile(code)
        target = target_for(code)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = export_profile_payload(section, destination=target, roots=[NDA_DIR])
        target.write_text(dump_profile_export(payload), encoding="utf-8", newline="\n")
        written.append(target)
        print(f"{code}: {len(section.parameters)} параметров → {target.relative_to(ROOT)}")
    return 0 if written else 1


if __name__ == "__main__":  # pragma: no cover - точка входа скрипта
    raise SystemExit(main())
