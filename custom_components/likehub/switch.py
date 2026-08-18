"""Локальный рубильник удалённого управления (ФТ-С-05).

Синхронизирован с опцией `allow_remote_control` в обе стороны и доступен
из автоматизаций: владелец может выключить управление сценарием, не заходя в настройки.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEFAULT_ALLOW_REMOTE_CONTROL, OPT_ALLOW_REMOTE_CONTROL
from .entity import LikeHubEntity

if TYPE_CHECKING:
    from . import LikeHubConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LikeHubConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([RemoteControlSwitch(hass, entry)])


class RemoteControlSwitch(LikeHubEntity, SwitchEntity):
    _attr_translation_key = "remote_control"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hass: HomeAssistant, entry: LikeHubConfigEntry) -> None:
        super().__init__(entry, "remote_control")
        self._hass = hass

    @property
    def is_on(self) -> bool:
        return bool(
            self._entry.options.get(
                OPT_ALLOW_REMOTE_CONTROL, DEFAULT_ALLOW_REMOTE_CONTROL
            )
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, value: bool) -> None:
        options = {**self._entry.options, OPT_ALLOW_REMOTE_CONTROL: value}
        self._hass.config_entries.async_update_entry(self._entry, options=options)
