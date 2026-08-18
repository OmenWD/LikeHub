"""Кнопка проверки связи (ФТ-С-06, ФТ-Д-01)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, KIND_TEST, Severity, SyncReason
from .entity import LikeHubEntity

if TYPE_CHECKING:
    from . import LikeHubConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LikeHubConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([TestButton(entry)])


class TestButton(LikeHubEntity, ButtonEntity):
    """Отправляет тестовое событие и показывает результат."""

    _attr_translation_key = "test"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: LikeHubConfigEntry) -> None:
        super().__init__(entry, "test")

    async def async_press(self) -> None:
        coordinator = self.coordinator
        coordinator.async_add_event(
            entity_id=f"{DOMAIN}.test",
            kind=KIND_TEST,
            severity=Severity.INFO,
            name="Проверка связи",
            state="ok",
        )
        await coordinator.async_sync(SyncReason.MANUAL)
