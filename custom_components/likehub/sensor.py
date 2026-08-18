"""Диагностические сенсоры: последняя синхронизация, очередь, последняя команда."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import LikeHubEntity

if TYPE_CHECKING:
    from . import LikeHubConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LikeHubConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        [LastSyncSensor(entry), QueueSensor(entry), LastCommandSensor(entry)]
    )


class LastSyncSensor(LikeHubEntity, SensorEntity):
    """Время последней подтверждённой синхронизации (ФТ-С-02)."""

    _attr_translation_key = "last_sync"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: LikeHubConfigEntry) -> None:
        super().__init__(entry, "last_sync")

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_sync


class QueueSensor(LikeHubEntity, SensorEntity):
    """Число недоставленных событий (ФТ-С-03)."""

    _attr_translation_key = "queue"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "событий"

    def __init__(self, entry: LikeHubConfigEntry) -> None:
        super().__init__(entry, "queue")

    @property
    def native_value(self) -> int:
        return self.coordinator.queue.size

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"dropped": self.coordinator.queue.dropped}


class LastCommandSensor(LikeHubEntity, SensorEntity):
    """Последняя обработанная команда с атрибутами (ФТ-С-04)."""

    _attr_translation_key = "last_command"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: LikeHubConfigEntry) -> None:
        super().__init__(entry, "last_command")

    @property
    def native_value(self) -> str | None:
        last = self._entry.runtime_data.commands.last_command
        return last.get("status") if last else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._entry.runtime_data.commands.last_command or {}
