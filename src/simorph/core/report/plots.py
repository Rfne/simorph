"""Графики отчёта без внешних зависимостей (T11-02, T11-03).

Графики рисуются в массив пикселей и кодируются своим PNG-энкодером: ни
matplotlib, ни браузера, ни сети. Поэтому отчёт собирается в headless-CI
и остаётся автономным: в Markdown картинки лежат файлами рядом, в HTML —
встроены как base64.

Шрифт — растровый 5×7 для цифр и латиницы: подписи ступеней в конвейере
латинские, а русские заголовки и пояснения живут в разметке, а не в
пикселях. Так график не зависит от системных шрифтов и одинаков на всех
машинах.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Final

import numpy as np

from simorph.core.report.schema import MetricSeries, StageFact
from simorph.core.world.snapshot import png_bytes

__all__ = [
    "Chart",
    "bar_chart",
    "histogram_chart",
    "line_chart",
    "stage_chart",
]

#: Размер графика по умолчанию.
WIDTH: Final = 640
HEIGHT: Final = 200

#: Поля вокруг области данных.
PADDING_LEFT: Final = 44
PADDING_RIGHT: Final = 12
PADDING_TOP: Final = 12
PADDING_BOTTOM: Final = 24

#: Цвета: фон, сетка, оси, палитра линий.
BACKGROUND: Final[tuple[int, int, int]] = (255, 255, 255)
GRID: Final[tuple[int, int, int]] = (226, 232, 240)
AXIS: Final[tuple[int, int, int]] = (100, 116, 139)
TEXT: Final[tuple[int, int, int]] = (51, 65, 85)
PALETTE: Final[tuple[tuple[int, int, int], ...]] = (
    (37, 99, 235),
    (220, 38, 38),
    (22, 163, 74),
    (202, 138, 4),
    (147, 51, 234),
    (8, 145, 178),
)

#: Растровый шрифт 5×7: цифры, латиница и служебные знаки.
_FONT: Final[dict[str, tuple[str, ...]]] = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    ",": ("00000", "00000", "00000", "00000", "01100", "01100", "01000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    "%": ("11001", "11010", "00100", "00100", "01000", "01011", "10011"),
    ":": ("00000", "01100", "01100", "00000", "01100", "01100", "00000"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "_": ("00000", "00000", "00000", "00000", "00000", "00000", "11111"),
}


@dataclass(frozen=True, slots=True)
class Chart:
    """Готовый график: PNG в памяти и подпись для разметки."""

    name: str
    title: str
    png: bytes
    width: int
    height: int

    def data_uri(self) -> str:
        """PNG как data-URI: так HTML остаётся автономным."""
        return "data:image/png;base64," + base64.b64encode(self.png).decode("ascii")


def _canvas(width: int, height: int) -> np.ndarray:
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    canvas[:, :] = BACKGROUND
    return canvas


def _put(canvas: np.ndarray, x: int, y: int, color: tuple[int, int, int]) -> None:
    if 0 <= y < canvas.shape[0] and 0 <= x < canvas.shape[1]:
        canvas[y, x] = color


def _line(
    canvas: np.ndarray,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    color: tuple[int, int, int],
) -> None:
    """Отрезок по Брезенхему: без сглаживания, зато без зависимостей."""
    steps = int(max(abs(x1 - x0), abs(y1 - y0)))
    if steps == 0:
        _put(canvas, round(x0), round(y0), color)
        return
    for step in range(steps + 1):
        ratio = step / steps
        _put(
            canvas,
            round(x0 + (x1 - x0) * ratio),
            round(y0 + (y1 - y0) * ratio),
            color,
        )


def _rect(
    canvas: np.ndarray,
    left: int,
    top: int,
    right: int,
    bottom: int,
    color: tuple[int, int, int],
) -> None:
    canvas[max(0, top) : max(0, bottom), max(0, left) : max(0, right)] = color


def _text(
    canvas: np.ndarray,
    x: int,
    y: int,
    text: str,
    color: tuple[int, int, int] = TEXT,
    *,
    scale: int = 1,
) -> None:
    """Пишет строку растровым шрифтом; неизвестные знаки пропускаются."""
    cursor = x
    for symbol in text.upper():
        glyph = _FONT.get(symbol)
        if glyph is None:
            cursor += 6 * scale
            continue
        for row, bits in enumerate(glyph):
            for column, bit in enumerate(bits):
                if bit != "1":
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        _put(
                            canvas,
                            cursor + column * scale + dx,
                            y + row * scale + dy,
                            color,
                        )
        cursor += 6 * scale


def _text_width(text: str, *, scale: int = 1) -> int:
    return len(text) * 6 * scale


def _frame(
    canvas: np.ndarray,
    *,
    left: int,
    top: int,
    right: int,
    bottom: int,
    y_min: float,
    y_max: float,
) -> None:
    """Сетка, оси и подписи шкалы по вертикали."""
    for step in range(5):
        ratio = step / 4.0
        y = round(top + (bottom - top) * ratio)
        color = AXIS if step in (0, 4) else GRID
        _line(canvas, left, y, right, y, color)
        value = y_max - (y_max - y_min) * ratio
        label = _format_axis(value)
        _text(canvas, max(1, left - 6 - _text_width(label)), y - 3, label)


def _format_axis(value: float) -> str:
    magnitude = abs(value)
    if magnitude >= 1000.0:
        return f"{value / 1000.0:.1f}K"
    if magnitude >= 10.0:
        return f"{value:.0f}"
    if magnitude >= 1.0:
        return f"{value:.1f}"
    if magnitude == 0.0:
        return "0"
    return f"{value:.2f}"


def _bounds(values: list[float], *, include_zero: bool = True) -> tuple[float, float]:
    if not values:
        return (0.0, 1.0)
    low = min(values)
    high = max(values)
    if include_zero:
        low = min(low, 0.0)
        high = max(high, 0.0)
    if high - low < 1e-9:
        high = low + 1.0
    margin = (high - low) * 0.08
    return (low - margin, high + margin)


def line_chart(
    series: list[MetricSeries],
    *,
    title: str,
    name: str = "series",
    width: int = WIDTH,
    height: int = HEIGHT,
) -> Chart:
    """Линейный график одного или нескольких рядов по времени сцены."""
    canvas = _canvas(width, height)
    left, right = PADDING_LEFT, width - PADDING_RIGHT
    top, bottom = PADDING_TOP, height - PADDING_BOTTOM
    values = [value for item in series for value in item.values]
    y_min, y_max = _bounds(values)
    _frame(canvas, left=left, top=top, right=right, bottom=bottom, y_min=y_min, y_max=y_max)

    for index, item in enumerate(series):
        if item.count < 1:
            continue
        color = PALETTE[index % len(PALETTE)]
        times = item.t_ns if item.count > 1 else [0, 1]
        points = item.values if item.count > 1 else [item.values[0], item.values[0]]
        span = max(times[-1] - times[0], 1)
        previous: tuple[float, float] | None = None
        for moment, value in zip(times, points, strict=True):
            x = left + (moment - times[0]) / span * (right - left)
            y = bottom - (value - y_min) / (y_max - y_min) * (bottom - top)
            if previous is not None:
                _line(canvas, previous[0], previous[1], x, y, color)
            previous = (x, y)
        if item.count == 1 and previous is not None:
            _rect(
                canvas,
                int(previous[0]) - 2,
                int(previous[1]) - 2,
                int(previous[0]) + 3,
                int(previous[1]) + 3,
                color,
            )

    legend = "  ".join(item.name for item in series[:4])
    if legend:
        _text(canvas, left, bottom + 8, legend[: max(1, (width - left - 8) // 6)])
    return Chart(
        name=name,
        title=title,
        png=png_bytes(canvas),
        width=width,
        height=height,
    )


def histogram_chart(
    values: list[float],
    *,
    title: str,
    name: str = "histogram",
    bins: int = 16,
    width: int = WIDTH,
    height: int = HEIGHT,
) -> Chart:
    """Распределение значений: столбики по интервалам."""
    canvas = _canvas(width, height)
    left, right = PADDING_LEFT, width - PADDING_RIGHT
    top, bottom = PADDING_TOP, height - PADDING_BOTTOM
    if not values:
        _text(canvas, left, top + 8, "NO DATA")
        return Chart(name=name, title=title, png=png_bytes(canvas), width=width, height=height)

    counts, edges = np.histogram(np.asarray(values, dtype=np.float64), bins=bins)
    y_min, y_max = 0.0, float(counts.max()) if counts.size else 1.0
    _frame(canvas, left=left, top=top, right=right, bottom=bottom, y_min=y_min, y_max=y_max)
    slot = (right - left) / max(len(counts), 1)
    for index, count in enumerate(counts.tolist()):
        bar_left = round(left + index * slot + 1)
        bar_right = round(left + (index + 1) * slot - 1)
        bar_top = round(bottom - (count / max(y_max, 1.0)) * (bottom - top))
        _rect(canvas, bar_left, bar_top, bar_right, bottom, PALETTE[0])
    low, high = _format_axis(float(edges[0])), _format_axis(float(edges[-1]))
    _text(canvas, left, bottom + 8, f"{low} .. {high}")
    return Chart(name=name, title=title, png=png_bytes(canvas), width=width, height=height)


def bar_chart(
    rows: list[tuple[str, float]],
    *,
    title: str,
    name: str = "bars",
    width: int = WIDTH,
    height: int = HEIGHT,
) -> Chart:
    """Столбики «значение по категории»: перерасход или вклад ступеней."""
    canvas = _canvas(width, height)
    left, right = PADDING_LEFT + 24, width - PADDING_RIGHT
    top, bottom = PADDING_TOP, height - PADDING_BOTTOM
    if not rows:
        _text(canvas, left, top + 8, "NO DATA")
        return Chart(name=name, title=title, png=png_bytes(canvas), width=width, height=height)

    values = [value for _, value in rows]
    y_min, y_max = _bounds(values)
    _frame(canvas, left=left, top=top, right=right, bottom=bottom, y_min=y_min, y_max=y_max)
    slot = (right - left) / len(rows)
    for index, (label, value) in enumerate(rows):
        bar_left = round(left + index * slot + 2)
        bar_right = round(left + (index + 1) * slot - 2)
        y_value = bottom - (value - y_min) / (y_max - y_min) * (bottom - top)
        y_zero = bottom - (0.0 - y_min) / (y_max - y_min) * (bottom - top)
        top_edge = round(min(y_value, y_zero))
        bottom_edge = round(max(y_value, y_zero))
        _rect(canvas, bar_left, top_edge, bar_right, bottom_edge, PALETTE[index % len(PALETTE)])
        _text(canvas, bar_left, bottom + 8, label[:8].upper())
    return Chart(name=name, title=title, png=png_bytes(canvas), width=width, height=height)


def stage_chart(
    stages: list[StageFact],
    *,
    title: str = "План и факт по ступеням",
    name: str = "stages",
    width: int = WIDTH,
    height: int = HEIGHT,
) -> Chart:
    """План и факт ступеней парами: видно, где перерасход."""
    canvas = _canvas(width, height)
    left, right = PADDING_LEFT + 24, width - PADDING_RIGHT
    top, bottom = PADDING_TOP, height - PADDING_BOTTOM
    if not stages:
        _text(canvas, left, top + 8, "NO DATA")
        return Chart(name=name, title=title, png=png_bytes(canvas), width=width, height=height)

    values = [item.plan_ms for item in stages] + [item.actual_p95_ms for item in stages]
    y_min, y_max = _bounds(values)
    _frame(canvas, left=left, top=top, right=right, bottom=bottom, y_min=y_min, y_max=y_max)
    slot = (right - left) / len(stages)
    bar_width = max(3, int(slot / 3))
    for index, item in enumerate(stages):
        base = left + index * slot + slot / 2
        plan_top = bottom - (item.plan_ms - y_min) / (y_max - y_min) * (bottom - top)
        actual_top = bottom - (item.actual_p95_ms - y_min) / (y_max - y_min) * (bottom - top)
        _rect(canvas, int(base - bar_width) - 1, int(plan_top), int(base) - 1, bottom, PALETTE[0])
        _rect(canvas, int(base) + 1, int(actual_top), int(base + bar_width) + 1, bottom, PALETTE[1])
        _text(canvas, int(base - _text_width(item.stage[:6]) / 2), bottom + 8, item.stage[:6])
    _text(canvas, right - 60, top + 2, "PLAN/ACT")
    return Chart(name=name, title=title, png=png_bytes(canvas), width=width, height=height)
