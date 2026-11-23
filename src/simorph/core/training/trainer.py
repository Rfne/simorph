"""Тренер головы детекции: конфигурация, чекпойнты и отмена (T10-04, G10-03).

Обучение — это цикл по эпохам, внутри которого батчи собираются из кадров
датасета с аугментацией. Тренер не знает, откуда взялись кадры: ему дают
список ``DatasetFrame``, поэтому один и тот же код работает и на полном
датасете, и на мини-наборе в тесте.

Отмена не теряет прогресс: после каждого батча проверяется флаг, и при
отмене сохраняется чекпойнт с номером эпохи и историей. Продолжение
обучения — это загрузка чекпойнта и вызов ``fit`` с оставшимися эпохами;
``resume_epoch`` в отчёте говорит, с чего начинать.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any

import numpy as np
import torch
from torch import Tensor

from simorph.core.dataset.augment import AugmentConfig, augment_frame
from simorph.core.dataset.schema import DatasetFrame
from simorph.core.training.model import DetectionHead, ModelSpec
from simorph.core.training.targets import encode_targets, focal_loss

__all__ = [
    "CHECKPOINT_NAME",
    "EpochRecord",
    "TrainConfig",
    "TrainReport",
    "Trainer",
]

#: Имя чекпойнта по умолчанию.
CHECKPOINT_NAME = "checkpoint.pt"

#: Вес регрессии размера и смещения относительно карты классов.
REGRESSION_WEIGHT = 3.0


@dataclass(slots=True)
class TrainConfig:
    """Параметры обучения: эпохи, батч, скорость, seed и потоки."""

    epochs: int = 120
    batch_size: int = 16
    lr: float = 2e-3
    seed: int = 20261122
    threads: int = 2
    checkpoint_every: int = 5
    augment: AugmentConfig | None = None

    def __post_init__(self) -> None:
        if self.epochs < 1:
            raise ValueError("число эпох должно быть положительным")
        if self.batch_size < 1:
            raise ValueError("размер батча должен быть положительным")
        if self.lr <= 0.0:
            raise ValueError("скорость обучения должна быть положительной")
        if self.threads < 1:
            raise ValueError("число потоков должно быть положительным")
        if self.checkpoint_every < 1:
            raise ValueError("шаг чекпойнтов должен быть положительным")

    def to_dict(self) -> dict[str, Any]:
        return {
            "epochs": self.epochs,
            "batch_size": self.batch_size,
            "lr": self.lr,
            "seed": self.seed,
            "threads": self.threads,
            "checkpoint_every": self.checkpoint_every,
            "augment": None if self.augment is None else self.augment.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class EpochRecord:
    """Итог эпохи: номер и средняя потеря."""

    epoch: int
    loss: float

    def to_dict(self) -> dict[str, Any]:
        return {"epoch": self.epoch, "loss": round(self.loss, 6)}


@dataclass(slots=True)
class TrainReport:
    """Итог обучения: сколько эпох, где чекпойнт и чем закончилось."""

    epochs_done: int
    planned: int
    cancelled: bool
    loss: float
    checkpoint: str | None
    resume_epoch: int
    history: list[EpochRecord] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def finished(self) -> bool:
        return not self.cancelled and self.epochs_done >= self.planned

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "epochs_done": self.epochs_done,
            "planned": self.planned,
            "cancelled": self.cancelled,
            "finished": self.finished,
            "loss": round(self.loss, 6),
            "checkpoint": self.checkpoint,
            "resume_epoch": self.resume_epoch,
            "history": [item.to_dict() for item in self.history],
            "seconds": round(self.seconds, 3),
        }

    def summary(self) -> str:
        state = "прервано" if self.cancelled else "готово"
        return (
            f"обучение {state}: эпох {self.epochs_done}/{self.planned}"
            f" · потеря {self.loss:.4f}"
            f" · чекпойнт {self.checkpoint or '—'}"
            f" · {self.seconds:.1f} с"
        )


def _to_tensor(image: np.ndarray) -> Tensor:
    array = np.ascontiguousarray(image, dtype=np.float32) / 255.0
    return torch.from_numpy(array).permute(2, 0, 1)


class Trainer:
    """Цикл обучения с чекпойнтами и отменой."""

    def __init__(
        self,
        model: DetectionHead,
        config: TrainConfig,
        *,
        workdir: Path | str | None = None,
    ) -> None:
        self.model = model
        self.config = config
        self.workdir = Path(workdir) if workdir is not None else Path.cwd()
        self.optimizer = torch.optim.Adam(model.parameters(), lr=config.lr)
        self.scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            self.optimizer, T_max=config.epochs
        )
        self.start_epoch = 0
        self.history: list[EpochRecord] = []

    @property
    def spec(self) -> ModelSpec:
        return self.model.spec

    def fit(
        self,
        frames: Sequence[DatasetFrame],
        *,
        cancel: Event | None = None,
        on_epoch: Callable[[int, float], None] | None = None,
    ) -> TrainReport:
        """Обучает модель; при отмене сохраняет прогресс."""
        if not frames:
            raise ValueError("пустой набор кадров: обучать не на чем")
        started = time.perf_counter()
        torch.set_num_threads(self.config.threads)
        torch.manual_seed(self.config.seed)
        self.model.train()
        last_loss = 0.0
        epochs_done = 0
        cancelled = False
        checkpoint: Path | None = None

        for epoch in range(self.start_epoch, self.config.epochs):
            if cancel is not None and cancel.is_set():
                cancelled = True
                break
            last_loss, stopped = self._run_epoch(frames, epoch, cancel)
            epochs_done = epoch + 1 - self.start_epoch
            self.history.append(EpochRecord(epoch=epoch + 1, loss=last_loss))
            self.scheduler.step()
            if on_epoch is not None:
                on_epoch(epoch + 1, last_loss)
            checkpoint = self.save_checkpoint(epoch=epoch + 1, loss=last_loss)
            if stopped:
                cancelled = True
                break

        if checkpoint is None:
            checkpoint = self.save_checkpoint(epoch=self.start_epoch, loss=last_loss)
        self.model.eval()
        total_epochs = epochs_done
        return TrainReport(
            epochs_done=total_epochs,
            planned=self.config.epochs - self.start_epoch,
            cancelled=cancelled,
            loss=last_loss,
            checkpoint=str(checkpoint),
            resume_epoch=self.start_epoch + total_epochs,
            history=list(self.history),
            seconds=time.perf_counter() - started,
        )

    def _run_epoch(
        self,
        frames: Sequence[DatasetFrame],
        epoch: int,
        cancel: Event | None,
    ) -> tuple[float, bool]:
        """Одна эпоха: батчи, потеря и проверка отмены после каждого батча."""
        generator = torch.Generator().manual_seed(self.config.seed + epoch)
        order = torch.randperm(len(frames), generator=generator).tolist()
        total = 0.0
        steps = 0
        for start in range(0, len(order), self.config.batch_size):
            if cancel is not None and cancel.is_set():
                return (total / max(steps, 1), True)
            batch = [frames[index] for index in order[start : start + self.config.batch_size]]
            loss = self._batch_loss(batch)
            self.optimizer.zero_grad()
            loss.backward()  # type: ignore[no-untyped-call]
            self.optimizer.step()
            total += float(loss.detach())
            steps += 1
        return (total / max(steps, 1), False)

    def _batch_loss(self, batch: Sequence[DatasetFrame]) -> Tensor:
        """Потеря батча: карта классов плюс регрессия размера и смещения."""
        images: list[Tensor] = []
        prepared: list[DatasetFrame] = []
        for frame in batch:
            current = frame
            if self.config.augment is not None:
                augmented, _ = augment_frame(frame, self.config.augment)
                if augmented is None:
                    continue
                current = augmented
            prepared.append(current)
            images.append(_to_tensor(current.image))
        if not images:
            return torch.zeros((), requires_grad=True)

        heatmap, size, offset = self.model(torch.stack(images))
        heatmap_loss = torch.zeros(())
        size_loss = torch.zeros(())
        offset_loss = torch.zeros(())
        for index, frame in enumerate(prepared):
            targets = encode_targets(frame.boxes, self.spec)
            heatmap_loss = heatmap_loss + focal_loss(
                heatmap[index], torch.from_numpy(targets.heatmap)
            )
            mask = torch.from_numpy(targets.mask).bool()
            if bool(mask.any()):
                size_loss = size_loss + torch.nn.functional.l1_loss(
                    size[index][:, mask], torch.from_numpy(targets.size)[:, mask]
                )
                offset_loss = offset_loss + torch.nn.functional.smooth_l1_loss(
                    offset[index][:, mask], torch.from_numpy(targets.offset)[:, mask]
                )
        count = len(prepared)
        regression = (size_loss + offset_loss) / count
        return (heatmap_loss + REGRESSION_WEIGHT * regression) / count

    def save_checkpoint(self, *, epoch: int, loss: float) -> Path:
        """Сохраняет чекпойнт: веса, конфигурацию, эпоху и историю."""
        target = self.workdir / CHECKPOINT_NAME
        self.model.save(
            target,
            epoch=epoch,
            loss=loss,
            config=self.config.to_dict(),
            history=[item.to_dict() for item in self.history],
        )
        return target

    def resume(self, path: Path | str) -> int:
        """Продолжает обучение с чекпойнта; возвращает номер следующей эпохи."""
        payload = torch.load(Path(path), map_location="cpu", weights_only=False)
        self.model.load_state_dict(payload["state"])
        self.start_epoch = int(payload.get("epoch", 0))
        self.history = [
            EpochRecord(epoch=int(item["epoch"]), loss=float(item["loss"]))
            for item in payload.get("history", [])
        ]
        return self.start_epoch
