"""Запуск ``python -m simorph``."""

from __future__ import annotations

import sys

from simorph.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
