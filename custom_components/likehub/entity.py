"""Общая база сущностей интеграции.

Все сущности принадлежат одному устройству «LikeHub (имя объекта)» (ФТ-С).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import CONF_SITE_ID, CONF_SITE_NAME, DOMAIN

if TYPE_CHECKING:
    from . import LikeHubConfigEntry


class LikeHubEntity(Entity):
    """База: устройство, уникальный идентификатор, подписка на координатор."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: LikeHubConfigEntry, key: str) -> None:
        self._entry = entry
        self._key = key
        site_id = entry.data[CONF_SITE_ID]
        site_name = entry.data.get(CONF_SITE_NAME, site_id)

        self._attr_unique_id = f"{site_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, site_id)},
            name=f"LikeHub ({site_name})",
            manufacturer="LikeHub",
            model="Облачный агент",
        )

    @property
    def coordinator(self):  # noqa: ANN201 — тип задан в runtime_data
        return self._entry.runtime_data.coordinator

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.coordinator.add_listener(self.async_write_ha_state))
