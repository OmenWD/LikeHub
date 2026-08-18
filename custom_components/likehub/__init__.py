"""Интеграция LikeHub: агент облачного сервиса внутри Home Assistant.

Только исходящие HTTPS-соединения: интеграция не открывает портов и не регистрирует
HTTP-обработчиков (СБ-01).
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .api import (
    LikeHubApi,
    LikeHubAuthError,
    LikeHubConnectionError,
    LikeHubError,
    LikeHubSiteNotFoundError,
)
from .commands import CommandHistory, CommandProcessor
from .const import (
    BACKOFF_JITTER,
    BACKOFF_MAX,
    BACKOFF_START,
    CONF_BASE_URL,
    CONF_REFRESH_TOKEN,
    CONF_SITE_ID,
    CONF_SITE_SECRET,
    DEFAULT_BASE_URL,
    DOMAIN,
    ISSUE_ACCESS_REVOKED,
    SyncReason,
)
from .coordinator import LikeHubCoordinator
from .queue import EventQueue

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.SWITCH,
]


@dataclass
class LikeHubData:
    """Рантайм записи. Токены живут только здесь, на диск попадает лишь refresh."""

    api: LikeHubApi
    coordinator: LikeHubCoordinator
    commands: CommandProcessor
    history: CommandHistory
    queue: EventQueue
    unsub: list[Any] = field(default_factory=list)
    stream_connected: bool = False


# ConfigEntry[LikeHubData] через присваивание, а не `type X = ...`:
# синтаксис PEP 695 требует Python 3.12, а тестовый стенд может быть на 3.11.
LikeHubConfigEntry = ConfigEntry[LikeHubData]


async def async_setup_entry(hass: HomeAssistant, entry: LikeHubConfigEntry) -> bool:
    """Загрузка записи: очередь, координатор, тик и канал команд."""
    session = async_get_clientsession(hass)
    api = LikeHubApi(
        session,
        entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
        refresh_token=entry.data[CONF_REFRESH_TOKEN],
        site_id=entry.data[CONF_SITE_ID],
    )

    queue = EventQueue(hass, entry.entry_id)
    await queue.async_load()

    history = CommandHistory(hass, entry.entry_id)
    await history.async_load()

    coordinator = LikeHubCoordinator(hass, entry, api, queue)
    commands = CommandProcessor(
        hass, coordinator, entry.data[CONF_SITE_SECRET], history
    )

    try:
        refresh_token = await api.refresh()
    except LikeHubAuthError as err:
        raise ConfigEntryAuthFailed("Токен обновления отозван") from err
    except LikeHubConnectionError as err:
        raise ConfigEntryNotReady("Облако недоступно") from err

    if refresh_token != entry.data[CONF_REFRESH_TOKEN]:
        # Ротация: новый токен сохраняем сразу, иначе после перезагрузки объект
        # останется со старым и уйдёт в reauth (ADR-011 на стороне сервера).
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_REFRESH_TOKEN: refresh_token}
        )

    entry.runtime_data = LikeHubData(
        api=api, coordinator=coordinator, commands=commands, history=history, queue=queue
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.async_start()

    # Тик и канал команд — фоновые задачи записи: снимаются при выгрузке (ФТ-К-01).
    entry.runtime_data.unsub.append(
        async_track_time_interval(
            hass,
            _make_tick(coordinator),
            timedelta(seconds=coordinator.interval),
        )
    )
    entry.async_create_background_task(
        hass, _stream_worker(hass, entry), name=f"{DOMAIN}_stream"
    )

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


def _make_tick(coordinator: LikeHubCoordinator) -> Any:
    async def _tick(_now: Any) -> None:
        # Плановая синхронизация выполняется всегда, даже без событий: это
        # heartbeat объекта (ФТ-О-02).
        await coordinator.async_sync(SyncReason.TICK)

    return _tick


async def _stream_worker(hass: HomeAssistant, entry: LikeHubConfigEntry) -> None:
    """Канал команд: SSE с переподключением по бэкоффу (ФТ-К-02)."""
    data = entry.runtime_data
    backoff = BACKOFF_START

    while True:
        try:
            async for event in data.api.stream():
                data.stream_connected = True
                backoff = BACKOFF_START
                await _handle_stream_event(hass, entry, event)

        except LikeHubAuthError:
            data.stream_connected = False
            try:
                await data.api.refresh()
                continue
            except LikeHubError:
                ir.async_create_issue(
                    hass,
                    DOMAIN,
                    f"{ISSUE_ACCESS_REVOKED}_{entry.entry_id}",
                    is_fixable=False,
                    severity=ir.IssueSeverity.ERROR,
                    translation_key=ISSUE_ACCESS_REVOKED,
                )
                entry.async_start_reauth(hass)
                return

        except LikeHubSiteNotFoundError:
            # Сервер без канала команд: работаем по fallback, лог не спамим (ТЗ 11.1).
            data.stream_connected = False
            _LOGGER.debug("Канал команд недоступен, работаем по периодическому тику")

        except asyncio.CancelledError:
            data.stream_connected = False
            raise

        except LikeHubError as err:
            data.stream_connected = False
            _LOGGER.debug("Канал команд разорван: %s", err)

        data.stream_connected = False
        jitter = backoff * BACKOFF_JITTER
        await asyncio.sleep(max(1.0, backoff + random.uniform(-jitter, jitter)))
        backoff = min(backoff * 2, BACKOFF_MAX)


async def _handle_stream_event(
    hass: HomeAssistant, entry: LikeHubConfigEntry, event: Any
) -> None:
    data = entry.runtime_data

    if event.name == "command":
        ack = await data.commands.async_handle(event.data)
        data.commands.fire_event(event.data, ack)
        data.coordinator.async_add_ack(ack)

    elif event.name == "revoke":
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{ISSUE_ACCESS_REVOKED}_{entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_ACCESS_REVOKED,
        )
        entry.async_start_reauth(hass)

    elif event.name == "resync":
        data.coordinator.async_request_snapshot()
        await data.coordinator.async_sync(SyncReason.RECONNECT)


async def _async_options_updated(
    hass: HomeAssistant, entry: LikeHubConfigEntry
) -> None:
    """Изменение опций перезагружает запись (ФТ-Н-07)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: LikeHubConfigEntry) -> bool:
    """Выгрузка: снимаем подписки и задачи, сохраняем очередь (ФТ-Р-10)."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unloaded:
        return False

    data = entry.runtime_data
    for unsub in data.unsub:
        unsub()
    data.unsub.clear()
    await data.coordinator.async_stop()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: LikeHubConfigEntry) -> None:
    """Удаление интеграции: отзыв токена best effort и очистка .storage/ (ФТ-А-10)."""
    api = LikeHubApi(
        async_get_clientsession(hass),
        entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN, ""),
        site_id=entry.data.get(CONF_SITE_ID),
    )
    await api.revoke()

    await EventQueue(hass, entry.entry_id).async_remove_storage()
    await CommandHistory(hass, entry.entry_id).async_remove_storage()
