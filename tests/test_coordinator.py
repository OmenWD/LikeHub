"""Координатор: классификация событий, очередь, дельта/снимок, бэкофф."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.likehub.api import (
    LikeHubConnectionError,
    LikeHubDuplicateError,
    LikeHubRejectedError,
    SyncResult,
)
from custom_components.likehub.const import (
    AGENT_VERSION,
    OPT_ENTITIES,
    OPT_SEND_TELEMETRY,
    Severity,
    SyncReason,
)
from custom_components.likehub.coordinator import LikeHubCoordinator
from custom_components.likehub.queue import EventQueue


@pytest.fixture
async def coordinator(hass, config_entry) -> LikeHubCoordinator:
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry,
        options={OPT_ENTITIES: ["binary_sensor.leak_kitchen", "sensor.water_meter"]},
    )

    api = MagicMock()
    api.sync = AsyncMock(
        return_value=SyncResult(
            server_time=None,
            ack_seq=1,
            next_poll_in=300,
            want_full_snapshot=False,
            commands=[],
        )
    )

    queue = EventQueue(hass, config_entry.entry_id)
    await queue.async_load()

    return LikeHubCoordinator(hass, config_entry, api, queue)


async def test_leak_becomes_critical_event(hass, coordinator) -> None:
    """Датчик протечки → water_leak / critical (спецификация 7.2)."""
    coordinator._resubscribe()
    hass.states.async_set(
        "binary_sensor.leak_kitchen",
        "off",
        {"device_class": "moisture", "friendly_name": "Протечка под мойкой"},
    )
    await hass.async_block_till_done()
    hass.states.async_set(
        "binary_sensor.leak_kitchen",
        "on",
        {"device_class": "moisture", "friendly_name": "Протечка под мойкой"},
    )
    await hass.async_block_till_done()

    events = coordinator.queue.head(10)
    leak = [e for e in events if e["kind"] == "water_leak"]
    assert leak
    assert leak[-1]["severity"] == Severity.CRITICAL
    assert leak[-1]["name"] == "Протечка под мойкой"
    assert leak[-1]["previous"] == "off"
    # event_id = {entity_id}:{unix_ts} (ФТ-О-09)
    assert leak[-1]["event_id"].startswith("binary_sensor.leak_kitchen:")


async def test_same_state_is_not_an_event(hass, coordinator) -> None:
    """Отсутствие изменения значения событием не считается (ФТ-О-05)."""
    coordinator._resubscribe()
    hass.states.async_set("sensor.water_meter", "10", {"device_class": "water"})
    await hass.async_block_till_done()
    before = coordinator.queue.size

    hass.states.async_set("sensor.water_meter", "10", {"device_class": "water"})
    await hass.async_block_till_done()

    assert coordinator.queue.size == before


async def test_unavailable_is_a_warning(hass, coordinator) -> None:
    """`unavailable` — диагностический сигнал, а не «нет данных» (ФТ-О-06)."""
    coordinator._resubscribe()
    hass.states.async_set("sensor.water_meter", "10")
    await hass.async_block_till_done()
    hass.states.async_set("sensor.water_meter", "unavailable")
    await hass.async_block_till_done()

    kinds = [e["kind"] for e in coordinator.queue.head(10)]
    assert "unavailable" in kinds


async def test_telemetry_off_keeps_alarms(hass, coordinator, config_entry) -> None:
    """ФТ-Н-05: телеметрия выключена, аварии идут всегда."""
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            OPT_ENTITIES: ["binary_sensor.leak_kitchen", "sensor.water_meter"],
            OPT_SEND_TELEMETRY: False,
        },
    )
    coordinator._resubscribe()

    hass.states.async_set("sensor.water_meter", "1")
    await hass.async_block_till_done()
    hass.states.async_set("sensor.water_meter", "2")
    await hass.async_block_till_done()
    assert coordinator.queue.size == 0

    hass.states.async_set("binary_sensor.leak_kitchen", "on", {"device_class": "moisture"})
    await hass.async_block_till_done()
    assert coordinator.queue.size == 1


async def test_snapshot_on_boot_then_delta(hass, coordinator) -> None:
    """ФТ-О-07: снимок при первом запуске, дальше дельта."""
    coordinator._resubscribe()
    hass.states.async_set("sensor.water_meter", "1284.7", {"unit_of_measurement": "м³"})
    await hass.async_block_till_done()

    await coordinator.async_sync(SyncReason.BOOT)
    payload = coordinator.api.sync.call_args[0][0]
    assert payload["states"]["kind"] == "snapshot"

    hass.states.async_set("sensor.water_meter", "1290.1", {"unit_of_measurement": "м³"})
    await hass.async_block_till_done()

    await coordinator.async_sync(SyncReason.TICK)
    payload = coordinator.api.sync.call_args[0][0]
    assert payload["states"]["kind"] == "delta"


async def test_server_can_request_snapshot(hass, coordinator) -> None:
    """want_full_snapshot от сервера — снимок в ближайшую синхронизацию (С-15)."""
    coordinator._resubscribe()
    await coordinator.async_sync(SyncReason.BOOT)

    coordinator.api.sync.return_value = SyncResult(
        server_time=None, ack_seq=2, next_poll_in=300, want_full_snapshot=True, commands=[]
    )
    await coordinator.async_sync(SyncReason.TICK)

    coordinator.api.sync.return_value = SyncResult(
        server_time=None, ack_seq=3, next_poll_in=300, want_full_snapshot=False, commands=[]
    )
    await coordinator.async_sync(SyncReason.TICK)

    payload = coordinator.api.sync.call_args[0][0]
    assert payload["states"]["kind"] == "snapshot"


async def test_queue_cleared_only_after_ack(hass, coordinator) -> None:
    """ФТ-О-08: из очереди удаляется только подтверждённое."""
    coordinator.async_add_event(
        entity_id="binary_sensor.leak_kitchen",
        kind="water_leak",
        severity=Severity.CRITICAL,
        name="Протечка",
        state="on",
    )
    assert coordinator.queue.size == 1

    coordinator.api.sync.side_effect = LikeHubConnectionError("нет сети")
    await coordinator.async_sync(SyncReason.EVENT)
    assert coordinator.queue.size == 1

    coordinator.api.sync.side_effect = None
    coordinator._backoff = 0
    await coordinator.async_sync(SyncReason.EVENT)
    assert coordinator.queue.size == 0


async def test_duplicate_counts_as_delivered(hass, coordinator) -> None:
    """409: сервер уже видел батч — чистим очередь (спецификация 6.5)."""
    coordinator.async_add_event(
        entity_id="sensor.x",
        kind="measurement",
        severity=Severity.INFO,
        name="X",
        state="1",
    )
    coordinator.api.sync.side_effect = LikeHubDuplicateError("batch_already_accepted")

    assert await coordinator.async_sync(SyncReason.EVENT) is True
    assert coordinator.queue.size == 0


async def test_rejected_batch_is_dropped_without_retry(hass, coordinator) -> None:
    """400: тело отвергнуто навсегда, ретрай запрещён."""
    coordinator.async_add_event(
        entity_id="sensor.x",
        kind="measurement",
        severity=Severity.INFO,
        name="X",
        state="1",
    )
    coordinator.api.sync.side_effect = LikeHubRejectedError("validation_failed")

    assert await coordinator.async_sync(SyncReason.EVENT) is False
    assert coordinator.queue.size == 0
    assert coordinator.rejected_count == 1


async def test_next_poll_in_respects_minimum(hass, coordinator, config_entry) -> None:
    """С-14: сервер просит 10 с, агент не опускается ниже минимума настроек."""
    hass.config_entries.async_update_entry(
        config_entry,
        options={**config_entry.options, "min_interval": 120},
    )
    coordinator.api.sync.return_value = SyncResult(
        server_time=None, ack_seq=1, next_poll_in=10, want_full_snapshot=False, commands=[]
    )

    await coordinator.async_sync(SyncReason.TICK)
    assert coordinator.interval == 120


async def test_payload_contains_agent_block(hass, coordinator) -> None:
    """ФТ-Д-02: версия агента, версия HA, тип установки, размер очереди."""
    await coordinator.async_sync(SyncReason.TICK)
    payload = coordinator.api.sync.call_args[0][0]

    assert payload["agent"]["version"] == AGENT_VERSION
    assert "ha_version" in payload["agent"]
    assert "install_type" in payload["agent"]
    assert payload["queue_size"] == 0
    assert payload["reason"] == "tick"


async def test_nothing_leaves_without_selection(hass, config_entry) -> None:
    """СБ-09: без явного выбора сущностей наружу не уходит ничего."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options={})

    api = MagicMock()
    api.sync = AsyncMock(
        return_value=SyncResult(
            server_time=None, ack_seq=1, next_poll_in=300, want_full_snapshot=True, commands=[]
        )
    )
    queue = EventQueue(hass, config_entry.entry_id)
    await queue.async_load()
    coordinator = LikeHubCoordinator(hass, config_entry, api, queue)
    coordinator._resubscribe()

    hass.states.async_set("binary_sensor.leak_kitchen", "on", {"device_class": "moisture"})
    await hass.async_block_till_done()

    assert coordinator.queue.size == 0
    await coordinator.async_sync(SyncReason.BOOT)
    payload = api.sync.call_args[0][0]
    assert payload["events"] == []
    assert payload["states"]["items"] == []
