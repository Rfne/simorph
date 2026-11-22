"""Часы симуляции: фиксированный шаг и мягкий масштаб времени (T7-01, T7-09).

Шаг физики фиксирован: один такт всегда сдвигает время сцены на ``step_s``,
поэтому метки кадров и состояние физики не расходятся. Масштаб времени —
это отношение «сколько времени PC нужно на такт» к плановому интервалу:

    плановый интервал = step_s / scale

При нехватке производительности такт выполняется дольше планового
интервала, но не пропускается: часы просто накапливают фактическое время
PC. Коэффициент ``k = T_факт / T_план`` считается по накопленным величинам
и всегда показывает, насколько прогон отстал от плана.

Отбрасывание тактов — отдельный явный режим ``realtime_drop``: только он
разрешает пропускать кадры, и по умолчанию он выключен (FR-SIM-16,
FR-SIM-19).
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["DEFAULT_STEP_S", "MAX_SCALE", "MIN_SCALE", "SimClock"]

#: Шаг физики по умолчанию, с (100 Гц).
DEFAULT_STEP_S: float = 0.01

#: Допустимый масштаб времени.
MIN_SCALE: float = 0.05
MAX_SCALE: float = 8.0

#: Допуск сравнения моментов времени, с.
_EPS = 1e-12


@dataclass(slots=True)
class SimClock:
    """Часы с фиксированным шагом: такты, время сцены и время PC."""

    step_s: float = DEFAULT_STEP_S
    scale: float = 1.0
    realtime_drop: bool = False
    _ticks: int = field(init=False, default=0, repr=False)
    _sim_time_s: float = field(init=False, default=0.0, repr=False)
    _wall_time_s: float = field(init=False, default=0.0, repr=False)
    _started: bool = field(init=False, default=False, repr=False)
    _paused: bool = field(init=False, default=False, repr=False)
    _stopped: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        if self.step_s <= 0.0:
            raise ValueError("шаг физики должен быть положительным")
        self._check_scale(self.scale)

    # --- состояние ------------------------------------------------------

    @property
    def ticks(self) -> int:
        """Сколько тактов выполнено."""
        return self._ticks

    @property
    def sim_time_s(self) -> float:
        """Время сцены, с."""
        return self._sim_time_s

    @property
    def wall_time_s(self) -> float:
        """Фактическое время PC, потраченное на такты, с."""
        return self._wall_time_s

    @property
    def started(self) -> bool:
        return self._started

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def running(self) -> bool:
        return self._started and not self._paused and not self._stopped

    @property
    def target_interval_s(self) -> float:
        """Плановый интервал одного такта на PC, с."""
        return self.step_s / self.scale

    @property
    def planned_time_s(self) -> float:
        """Сколько времени PC план отводит на выполненные такты, с."""
        return self._ticks * self.target_interval_s

    def scale_factor(self) -> float:
        """``k = T_факт / T_план`` по накопленным величинам."""
        planned = self.planned_time_s
        if planned <= _EPS:
            return 1.0
        return self._wall_time_s / planned

    # --- управление -----------------------------------------------------

    def set_scale(self, scale: float) -> None:
        """Меняет целевой масштаб времени; уже накопленное время не трогается."""
        self._check_scale(scale)
        self.scale = float(scale)

    def start(self) -> None:
        """Запускает часы; повторный запуск после остановки начинается заново."""
        if self._started and not self._stopped:
            self._paused = False
            return
        self._ticks = 0
        self._sim_time_s = 0.0
        self._wall_time_s = 0.0
        self._started = True
        self._paused = False
        self._stopped = False

    def pause(self) -> bool:
        """Ставит на паузу; ``False``, если часы не идут."""
        if not self.running:
            return False
        self._paused = True
        return True

    def resume(self) -> bool:
        """Снимает паузу; ``False``, если часы не на паузе."""
        if not self._started or self._stopped or not self._paused:
            return False
        self._paused = False
        return True

    def stop(self) -> None:
        """Останавливает часы: дальнейшие такты не выполняются."""
        self._stopped = True
        self._paused = False

    # --- такты ----------------------------------------------------------

    def tick(self, wall_s: float | None = None) -> bool:
        """Один такт: время сцены растёт ровно на шаг.

        Args:
            wall_s: фактическое время PC на такт; ``None`` — плановый интервал.

        Returns:
            ``True``, если такт выполнен.
        """
        if not self.running:
            return False
        elapsed = self.target_interval_s if wall_s is None else max(0.0, float(wall_s))
        self._ticks += 1
        self._sim_time_s += self.step_s
        self._wall_time_s += elapsed
        return True

    def ticks_for_wall(self, wall_elapsed_s: float) -> int:
        """Сколько тактов соответствует прошедшему времени PC.

        В мягком режиме это подсказка планировщику: такты всё равно
        выполняются по одному и не пропускаются.
        """
        interval = self.target_interval_s
        if interval <= _EPS or wall_elapsed_s <= 0.0:
            return 0
        return max(0, int(wall_elapsed_s / interval))

    # --- служебное ------------------------------------------------------

    @staticmethod
    def _check_scale(scale: float) -> None:
        if not MIN_SCALE <= float(scale) <= MAX_SCALE:
            raise ValueError(f"масштаб времени {scale!r} вне диапазона {MIN_SCALE:g}…{MAX_SCALE:g}")

    def to_dict(self) -> dict[str, object]:
        return {
            "step_s": self.step_s,
            "scale": self.scale,
            "realtime_drop": self.realtime_drop,
            "ticks": self._ticks,
            "sim_time_s": round(self._sim_time_s, 6),
            "wall_time_s": round(self._wall_time_s, 6),
            "target_interval_s": round(self.target_interval_s, 6),
            "k": round(self.scale_factor(), 4),
            "paused": self._paused,
            "stopped": self._stopped,
        }
