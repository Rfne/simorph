"""Пороговые выводы отчёта: соответствие текста значениям метрик (T11-02, G11-02).

Вывод — это не мнение, а функция от метрик: порог сравнивается с числом,
и текст собирается из тех же чисел. Поэтому «вывод противоречит метрике»
невозможно по построению, а тест проверяет именно это — меняет порог
и смотрит, что уровень и текст меняются вместе с ним.

Пороги задаются по имени метрики (``k.p95``) или по префиксу с ``*``
(``stage.*.overrun_ms``). Для префикса берётся максимум по совпавшим
метрикам: одна ступень с перерасходом уже нарушение.
"""

from __future__ import annotations

import re
from typing import Final

from simorph.core.report.schema import Conclusion, ConclusionLevel, ReportConfig, ReportData

__all__ = [
    "DEFAULT_THRESHOLDS",
    "build_conclusions",
    "derive_conclusions",
    "evaluate_thresholds",
]

#: Пороги по умолчанию: те, что заданы TRD для отчёта и дашборда.
DEFAULT_THRESHOLDS: Final[dict[str, float]] = {
    "k.p95": 1.5,
    "frames.dropped": 0.02,
    "eval.map50": 0.60,
    "eval.latency_p95_ms": 400.0,
}

#: Доля порога, после которой вывод становится «на границе».
WARN_SHARE: Final = 0.9

#: Предел перерасхода ступени, после которого это уже нарушение.
STAGE_OVERRUN_MS: Final = 1.0


def _match_series(data: ReportData, pattern: str) -> list[tuple[str, float]]:
    """Метрики, подходящие под имя или шаблон с ``*``; для каждой — худшее значение."""
    if "*" in pattern:
        regex = re.compile("^" + re.escape(pattern).replace(r"\*", ".*") + "$")
        return [
            (name, max(series.values))
            for name, series in data.series.items()
            if regex.match(name) and series.values
        ]
    series = data.series.get(pattern)
    if series is None or not series.values:
        return []
    return [(pattern, series.values[-1])]


def evaluate_thresholds(
    data: ReportData,
    thresholds: dict[str, float],
) -> list[Conclusion]:
    """Сравнивает метрики с порогами и возвращает выводы по каждому."""
    result: list[Conclusion] = []
    for pattern, limit in sorted(thresholds.items()):
        matched = _match_series(data, pattern)
        if not matched:
            result.append(
                Conclusion(
                    metric=pattern,
                    level="info",
                    text=f"метрика {pattern} в прогоне отсутствует: порог {limit:g} не проверен",
                    threshold=limit,
                )
            )
            continue
        name, value = max(matched, key=lambda item: item[1])
        unit = data.series[name].unit if name in data.series else None
        level: ConclusionLevel
        if value > limit:
            level = "fail"
            text = f"{name} = {value:.4g} превышает порог {limit:g}"
        elif value >= limit * WARN_SHARE:
            level = "warn"
            text = f"{name} = {value:.4g} близко к порогу {limit:g}"
        else:
            level = "ok"
            text = f"{name} = {value:.4g} в пределах порога {limit:g}"
        if unit:
            text = f"{text} ({unit})"
        result.append(
            Conclusion(
                metric=name,
                level=level,
                text=text,
                value=value,
                threshold=limit,
                unit=unit,
            )
        )
    return result


def _value(data: ReportData, name: str) -> float | None:
    series = data.series.get(name)
    if series is None or not series.values:
        return None
    return series.values[-1]


def derive_conclusions(data: ReportData) -> list[Conclusion]:
    """Выводы из самих значений: масштаб, потери, очередь, ступени, обучение."""
    result: list[Conclusion] = []

    k_p50 = _value(data, "k.p50")
    k_p95 = _value(data, "k.p95")
    if k_p50 is not None and k_p95 is not None:
        if k_p95 <= 1.0:
            result.append(
                Conclusion(
                    metric="k.p95",
                    level="ok",
                    text=f"мягкий масштаб не потребовался: k p50 {k_p50:.2f}, p95 {k_p95:.2f}",
                    value=k_p95,
                    unit="доля",
                )
            )
        elif k_p50 <= 1.0:
            result.append(
                Conclusion(
                    metric="k.p95",
                    level="warn",
                    text=(
                        f"масштаб растягивал время на части интервалов:"
                        f" k p50 {k_p50:.2f}, p95 {k_p95:.2f}"
                    ),
                    value=k_p95,
                    unit="доля",
                )
            )
        else:
            result.append(
                Conclusion(
                    metric="k.p50",
                    level="fail",
                    text=(
                        f"прогон медленнее плана на всех интервалах:"
                        f" k p50 {k_p50:.2f}, p95 {k_p95:.2f}"
                    ),
                    value=k_p50,
                    unit="доля",
                )
            )

    dropped = _value(data, "frames.dropped")
    consumed = _value(data, "frames.consumed")
    if dropped is not None and consumed is not None:
        total = dropped + consumed
        share = dropped / total if total > 0.0 else 0.0
        level: ConclusionLevel = "ok" if dropped == 0.0 else ("warn" if share <= 0.02 else "fail")
        result.append(
            Conclusion(
                metric="frames.dropped",
                level=level,
                text=(
                    f"потерь кадров нет: обработано {consumed:.0f}"
                    if dropped == 0.0
                    else f"потеряно {dropped:.0f} кадров из {total:.0f} ({share * 100:.2f} %)"
                ),
                value=dropped,
                unit="кадров",
            )
        )

    wait = _value(data, "queue.wait_ms")
    if wait is not None and wait > 0.0:
        result.append(
            Conclusion(
                metric="queue.wait_ms",
                level="info",
                text=f"ожидание в очереди: {wait:.3f} мс",
                value=wait,
                unit="мс",
            )
        )

    if data.stages:
        worst = max(data.stages, key=lambda item: item.overrun_ms)
        if worst.overrun_ms > STAGE_OVERRUN_MS:
            result.append(
                Conclusion(
                    metric=f"stage.{worst.stage}.overrun_ms",
                    level="fail",
                    text=(
                        f"ступень {worst.stage} выходит за план:"
                        f" p95 {worst.actual_p95_ms:.2f} мс против {worst.plan_ms:.2f} мс"
                    ),
                    value=worst.overrun_ms,
                    unit="мс",
                )
            )
        else:
            result.append(
                Conclusion(
                    metric=f"stage.{worst.stage}.overrun_ms",
                    level="ok",
                    text=(
                        f"все ступени в плане; самая нагруженная — {worst.stage}:"
                        f" p95 {worst.actual_p95_ms:.2f} мс при плане {worst.plan_ms:.2f} мс"
                    ),
                    value=worst.overrun_ms,
                    unit="мс",
                )
            )

    map50 = _value(data, "eval.map50")
    if map50 is not None:
        result.append(
            Conclusion(
                metric="eval.map50",
                level="info",
                text=f"качество головы: mAP@0.5 {map50:.3f}",
                value=map50,
            )
        )

    latency = _value(data, "eval.latency_p95_ms")
    if latency is not None:
        result.append(
            Conclusion(
                metric="eval.latency_p95_ms",
                level="info",
                text=f"задержка инференса p95 {latency:.1f} мс",
                value=latency,
                unit="мс",
            )
        )

    loss = _value(data, "train.loss")
    if loss is not None:
        result.append(
            Conclusion(
                metric="train.loss",
                level="info",
                text=f"итоговая потеря обучения {loss:.4f}",
                value=loss,
            )
        )

    if not result:
        result.append(
            Conclusion(
                metric="—",
                level="info",
                text=f"в прогоне {data.run_id} нет метрик, пригодных для выводов",
            )
        )
    return result


def build_conclusions(data: ReportData, config: ReportConfig) -> list[Conclusion]:
    """Пороги из конфигурации, затем автоматические выводы по значениям."""
    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds.update(config.thresholds)
    return evaluate_thresholds(data, thresholds) + derive_conclusions(data)
