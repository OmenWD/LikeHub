"""Очередь: вытеснение, восстановление, счётчик потерь (ФТ-Р-01…04)."""

from __future__ import annotations

from typing import Any

from custom_components.likehub.const import QUEUE_MAX_EVENTS, Severity
from custom_components.likehub.queue import EventQueue


def event(index: int, severity: Severity = Severity.INFO) -> dict[str, Any]:
    return {
        "event_id": f"sensor.x:{index}",
        "kind": "measurement",
        "severity": str(severity),
        "entity_id": "sensor.x",
        "name": "X",
        "state": str(index),
        "occurred_at": "2026-08-17T14:00:00+03:00",
    }


async def test_overflow_drops_only_info(hass) -> None:
    """ФТ-Р-03: вытесняются самые старые info, critical и warning — никогда."""
    queue = EventQueue(hass, "entry-1")
    await queue.async_load()

    queue.append(event(0, Severity.CRITICAL))
    queue.append(event(1, Severity.WARNING))
    for i in range(2, QUEUE_MAX_EVENTS + 100):
        queue.append(event(i))

    assert queue.size == QUEUE_MAX_EVENTS
    severities = {e["severity"] for e in queue.head(QUEUE_MAX_EVENTS)}
    assert str(Severity.CRITICAL) in severities
    assert str(Severity.WARNING) in severities
    # Положили 5100, оставили 5000: ровно 100 отброшенных info.
    # Число отброшенных передаётся серверу (ФТ-Р-04).
    assert queue.dropped == 100


async def test_alarms_are_never_dropped(hass) -> None:
    """Очередь из одних аварий не теряет ни одной, даже сверх лимита."""
    queue = EventQueue(hass, "entry-2")
    await queue.async_load()

    for i in range(QUEUE_MAX_EVENTS + 10):
        queue.append(event(i, Severity.CRITICAL))

    assert queue.size == QUEUE_MAX_EVENTS + 10
    assert queue.dropped == 0


async def test_remove_only_confirmed(hass) -> None:
    """ФТ-О-08: удаляется только подтверждённое сервером."""
    queue = EventQueue(hass, "entry-3")
    await queue.async_load()
    queue.extend([event(0), event(1), event(2)])

    queue.remove({"sensor.x:0", "sensor.x:2"})

    assert [e["event_id"] for e in queue.head(10)] == ["sensor.x:1"]


async def test_queue_survives_restart(hass) -> None:
    """ФТ-Р-01, С-5: очередь восстанавливается после перезагрузки HA."""
    queue = EventQueue(hass, "entry-4")
    await queue.async_load()
    queue.extend([event(0, Severity.CRITICAL), event(1)])
    await queue.async_save_now()

    restored = EventQueue(hass, "entry-4")
    await restored.async_load()

    assert restored.size == 2
    assert restored.head(1)[0]["event_id"] == "sensor.x:0"


async def test_dropped_counter_resets_after_confirmation(hass) -> None:
    queue = EventQueue(hass, "entry-5")
    await queue.async_load()
    for i in range(QUEUE_MAX_EVENTS + 5):
        queue.append(event(i))

    assert queue.dropped == 5
    queue.confirm_dropped()
    assert queue.dropped == 0
