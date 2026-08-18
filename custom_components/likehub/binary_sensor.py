"""Индикатор канала команд (ФТ-С-01)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    async_add_entities([ConnectedBinarySensor(entry)])


class ConnectedBinarySensor(LikeHubEntity, BinarySensorEntity):
    """Канал команд установлен: SSE активен."""

    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: LikeHubConfigEntry) -> None:
        super().__init__(entry, "connected")

    @property
    def is_on(self) -> bool:
        return self._entry.runtime_data.stream_connected
