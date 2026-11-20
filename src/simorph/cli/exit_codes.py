"""Коды возврата командной строки.

0 — успех, 1 — ошибка данных или конфигурации, 2 — внутренняя ошибка.
"""

from enum import IntEnum


class ExitCode(IntEnum):
    """Коды возврата, на которые опирается CI и внешние скрипты."""

    OK = 0
    DATA_ERROR = 1
    INTERNAL_ERROR = 2
