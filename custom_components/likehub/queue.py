"""Персистентная очередь недоставленных событий.

Очередь переживает перезагрузку HA (ФТ-Р-01) и пишется на диск отложенно:
HA часто работает с SD-карты, частая запись её убивает (ФТ-Р-02).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    QUEUE_MAX_EVENTS,
    QUEUE_SAVE_DELAY,
    STORAGE_KEY_QUEUE,
    STORAGE_VERSION,
    Severity,
)

_LOGGER = logging.getLogger(__name__)


class EventQueue:
    """Очередь событий с вытеснением и отложенным сохранением."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY_QUEUE}.{entry_id}"
        )
        self._events: list[dict[str, Any]] = []
        self._dropped = 0
        self._loaded = False

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data:
            self._events = list(data.get("events", []))
            self._dropped = int(data.get("dropped", 0))
        self._loaded = True
        _LOGGER.debug("Очередь восстановлена: %d событий", len(self._events))

    @property
    def size(self) -> int:
        return len(self._events)

    @property
    def dropped(self) -> int:
        """Число отброшенных событий: сервер обязан знать о потере данных (ФТ-Р-04)."""
        return self._dropped

    def append(self, event: dict[str, Any]) -> None:
        """Добавляет событие, при переполнении вытесняя старые `info`."""
        self._events.append(event)
        self._evict_if_needed()
        self._schedule_save()

    def extend(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        self._events.extend(events)
        self._evict_if_needed()
        self._schedule_save()

    def head(self, limit: int) -> list[dict[str, Any]]:
        """Первые события в порядке возникновения — для формирования батча."""
        return self._events[:limit]

    def remove(self, event_ids: set[str]) -> None:
        """Удаляет подтверждённые события: только то, что сервер подтвердил (ФТ-О-08)."""
        if not event_ids:
            return
        self._events = [e for e in self._events if e.get("event_id") not in event_ids]
        self._schedule_save()

    def confirm_dropped(self) -> None:
        """Сбрасывает счётчик после того, как сервер его принял."""
        if self._dropped:
            self._dropped = 0
            self._schedule_save()

    def clear(self) -> None:
        self._events.clear()
        self._schedule_save()

    def _evict_if_needed(self) -> None:
        """При переполнении отбрасываются самые старые `info`.

        `critical` и `warning` не отбрасываются никогда (ФТ-Р-03): потерять аварию
        хуже, чем потерять телеметрию, ради которой очередь и переполнилась.
        """
        overflow = len(self._events) - QUEUE_MAX_EVENTS
        if overflow <= 0:
            return

        kept: list[dict[str, Any]] = []
        to_drop = overflow
        for event in self._events:
            if to_drop > 0 and event.get("severity") == Severity.INFO:
                to_drop -= 1
                self._dropped += 1
                continue
            kept.append(event)
        self._events = kept

        if to_drop > 0:
            _LOGGER.warning(
                "Очередь переполнена, но все события важные: %d сверх лимита сохранено",
                to_drop,
            )

    def _schedule_save(self) -> None:
        if self._loaded:
            self._store.async_delay_save(self._data, QUEUE_SAVE_DELAY)

    def _data(self) -> dict[str, Any]:
        return {"events": self._events, "dropped": self._dropped}

    async def async_save_now(self) -> None:
        """Немедленная запись — при выгрузке записи (ФТ-Р-10)."""
        await self._store.async_save(self._data())

    async def async_remove_storage(self) -> None:
        """Удаление данных при удалении интеграции (ФТ-А-10)."""
        await self._store.async_remove()
