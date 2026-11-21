"""Приём команд актуаторами семи типов (T5-03, FR-AGT-04, FR-AGT-07).

Модель актуатора — это не физика, а контракт: команда ограничивается
диапазоном, скоростью и инерцией привода, а состояние показывает, что
именно применилось. Седьмой этап подставит сюда реальные тела PyBullet;
интерфейс команды от этого не изменится.

Все семь типов принимают команду. Для каждого типа своя величина:
угол, позиция, набор суставных углов, тяга, скорость, фокус, канал.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from simorph.core.models.agent import Actuator, AgentSection

__all__ = ["ActuatorCommand", "ActuatorController", "ActuatorState", "ActuatorType"]


ActuatorType = str


@dataclass(frozen=True, slots=True)
class ActuatorCommand:
    """Команда актуатору: величина и момент времени."""

    actuator_id: str
    value: Any
    t_s: float = 0.0


@dataclass(slots=True)
class ActuatorState:
    """Состояние привода после применения команды."""

    actuator_id: str
    type: str
    target: Any = None
    position: Any = None
    velocity: Any = None
    saturated: bool = False
    accepted: bool = True
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "actuator_id": self.actuator_id,
            "type": self.type,
            "target": self.target,
            "position": self.position,
            "velocity": self.velocity,
            "saturated": self.saturated,
            "accepted": self.accepted,
            "message": self.message,
        }


class ActuatorController:
    """Контроллер команд: ограничения диапазона, скорости и задержки."""

    def __init__(self, agent: AgentSection) -> None:
        self._agent = agent
        self._actuators = {item.id: item for item in agent.actuators}
        self._states: dict[str, ActuatorState] = {
            item.id: ActuatorState(actuator_id=item.id, type=item.type, position=_initial(item))
            for item in agent.actuators
        }

    @property
    def actuators(self) -> dict[str, Actuator]:
        return dict(self._actuators)

    @property
    def states(self) -> dict[str, ActuatorState]:
        return dict(self._states)

    def state(self, actuator_id: str) -> ActuatorState:
        return self._states[actuator_id]

    def command(
        self,
        actuator_id: str,
        value: Any,
        *,
        dt_s: float = 0.01,
    ) -> ActuatorState:
        """Применяет команду и возвращает новое состояние привода.

        Raises:
            KeyError: актуатора с таким идентификатором нет у агента.
        """
        actuator = self._actuators[actuator_id]
        state = self._states[actuator_id]
        target, saturated = self._clamp(actuator, value)
        position = self._approach(actuator, state.position, target, max(1e-6, float(dt_s)))
        velocity = _difference(position, state.position, dt_s)
        state.target = target
        state.position = position
        state.velocity = velocity
        state.saturated = saturated or not _equal(target, position)
        state.accepted = True
        state.message = "ограничено диапазоном" if saturated else ""
        return state

    def command_many(
        self, commands: list[ActuatorCommand], *, dt_s: float = 0.01
    ) -> list[ActuatorState]:
        """Применяет пачку команд: порядок сохраняется."""
        return [self.command(item.actuator_id, item.value, dt_s=dt_s) for item in commands]

    # --- ограничения ------------------------------------------------------

    def _clamp(self, actuator: Actuator, value: Any) -> tuple[Any, bool]:
        """Приводит команду к допустимому диапазону."""
        if actuator.type == "rotary_axis":
            low, high = actuator.range_deg or (-180.0, 180.0)
            target = _as_float(value)
            return min(high, max(low, target)), not (low <= target <= high)
        if actuator.type == "prismatic":
            low, high = actuator.range_m or (0.0, 1.0)
            target = _as_float(value)
            return min(high, max(low, target)), not (low <= target <= high)
        if actuator.type == "linkage":
            values = value if isinstance(value, (list, tuple)) else [value]
            joints = list(actuator.joints or [])
            targets = [_as_float(item) for item in values]
            if joints and len(targets) != len(joints):
                targets = (targets + [0.0] * len(joints))[: len(joints)]
            return targets, False
        if actuator.type == "thrust_vector":
            limit = float(actuator.thrust_max_n or 0.0)
            target = _as_float(value)
            return min(limit, max(0.0, target)), not (0.0 <= target <= limit)
        if actuator.type == "drive_axis":
            limit = float(actuator.max_speed_rad_s or 0.0)
            target = _as_float(value)
            return min(limit, max(-limit, target)), abs(target) > limit
        if actuator.type == "optical_focus":
            low, high = actuator.range_m or (0.0, 10.0)
            target = _as_float(value)
            return min(high, max(low, target)), not (low <= target <= high)
        # external_io
        analog = actuator.levels == "analog"
        target = _as_float(value)
        if analog:
            return min(1.0, max(0.0, target)), not (0.0 <= target <= 1.0)
        discrete = 1.0 if target >= 0.5 else 0.0
        return discrete, discrete != target

    def _approach(self, actuator: Actuator, current: Any, target: Any, dt_s: float) -> Any:
        """Один шаг привода: задержка первого порядка и ограничение скорости."""
        if isinstance(target, list):
            current_list = current if isinstance(current, list) else [0.0] * len(target)
            return [
                _approach_scalar(float(current_list[index]), float(value), actuator, dt_s)
                for index, value in enumerate(target)
            ]
        return _approach_scalar(_as_float(current), float(target), actuator, dt_s)


def _approach_scalar(current: float, target: float, actuator: Actuator, dt_s: float) -> float:
    """Инерция привода: доля шага и предел скорости."""
    response = max(0.0, float(actuator.response_ms or 0.0)) / 1000.0
    alpha = 1.0 if response <= 0.0 else min(1.0, dt_s / (response + dt_s))
    step = (target - current) * alpha
    limit = _speed_limit(actuator)
    if limit is not None:
        max_step = limit * dt_s
        step = min(max_step, max(-max_step, step))
    return current + step


def _speed_limit(actuator: Actuator) -> float | None:
    """Предел изменения величины за секунду в единицах команды."""
    if actuator.type == "rotary_axis":
        if actuator.max_speed_dps is not None:
            return float(actuator.max_speed_dps)
        if actuator.max_speed_rad_s is not None:
            return math.degrees(float(actuator.max_speed_rad_s))
    if actuator.type in {"prismatic", "optical_focus"}:
        return None if actuator.max_speed_mps is None else float(actuator.max_speed_mps)
    if actuator.type == "drive_axis":
        return None if actuator.max_speed_rad_s is None else float(actuator.max_speed_rad_s)
    if actuator.type == "thrust_vector":
        return None if actuator.max_force_n is None else float(actuator.max_force_n)
    return None


def _initial(actuator: Actuator) -> Any:
    if actuator.type == "linkage":
        return [0.0 for _ in (actuator.joints or [])]
    if actuator.type == "thrust_vector":
        return 0.0
    return 0.0


def _difference(current: Any, previous: Any, dt_s: float) -> Any:
    if isinstance(current, list) and isinstance(previous, list):
        return [
            round((float(value) - float(previous[index])) / dt_s, 6)
            for index, value in enumerate(current)
        ]
    return round((_as_float(current) - _as_float(previous)) / dt_s, 6)


def _equal(left: Any, right: Any) -> bool:
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            abs(float(a) - float(b)) < 1e-9 for a, b in zip(left, right, strict=False)
        )
    return abs(_as_float(left) - _as_float(right)) < 1e-9


def _as_float(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
