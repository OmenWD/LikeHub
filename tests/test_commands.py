"""Проверки команд — все ветви ФТ-К-04 и правило П-3.

Это самый важный набор в интеграции: ошибка здесь означает исполнение чужой
команды в доме пользователя.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.likehub.commands import CommandHistory, CommandProcessor
from custom_components.likehub.const import (
    OPT_ALLOW_REMOTE_CONTROL,
    OPT_PERMISSION_PREFIX,
    OPT_ROLE_PREFIX,
    AckReason,
    AckStatus,
)

from .conftest import SITE_SECRET


def sign(command: dict[str, Any], secret: str = SITE_SECRET) -> dict[str, Any]:
    """Подписывает команду по строке из 6.4 спецификации."""
    canonical = json.dumps(command.get("params") or {}, sort_keys=True, separators=(",", ":"))
    message = ".".join(
        [
            command["command_id"],
            command["action"],
            command["issued_at"],
            command["expires_at"],
            canonical,
        ]
    )
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return {**command, "signature": f"sha256={digest}"}


def make_command(action: str = "close_water", ttl: int = 60, **overrides: Any) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    command = {
        "command_id": overrides.pop("command_id", "cmd_1"),
        "action": action,
        "params": {},
        "issued_by": "user_18",
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
    }
    command.update(overrides)
    return sign(command)


@pytest.fixture
async def processor(hass) -> CommandProcessor:
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {
        OPT_ALLOW_REMOTE_CONTROL: True,
        f"{OPT_ROLE_PREFIX}water_valve": "valve.main",
        f"{OPT_ROLE_PREFIX}siren": "siren.hall",
    }

    coordinator = MagicMock()
    coordinator.entry = entry

    history = CommandHistory(hass, "entry-1")
    await history.async_load()

    hass.states.async_set("valve.main", "open")
    hass.states.async_set("siren.hall", "off")

    return CommandProcessor(hass, coordinator, SITE_SECRET, history)


async def test_valid_command_calls_service(hass, processor: CommandProcessor) -> None:
    """С-7: команда исполняется, ACK done."""
    calls: list[Any] = []

    async def _record(call):
        calls.append(call)

    hass.services.async_register("valve", "close_valve", _record)

    ack = await processor.async_handle(make_command())

    assert ack["status"] == AckStatus.DONE
    assert len(calls) == 1
    assert calls[0].data["entity_id"] == "valve.main"


async def test_bad_signature_rejected(hass, processor: CommandProcessor) -> None:
    """С-10: испорченная подпись — отказ до любых действий."""
    command = make_command()
    command["signature"] = "sha256=deadbeef"

    called = AsyncMock()
    hass.services.async_register("valve", "close_valve", called)

    ack = await processor.async_handle(command)

    assert ack["status"] == AckStatus.REJECTED
    assert ack["reason"] == AckReason.BAD_SIGNATURE
    called.assert_not_called()


async def test_signature_from_other_secret_rejected(processor: CommandProcessor) -> None:
    """Подпись чужим ключом не проходит."""
    command = sign(
        {
            "command_id": "cmd_2",
            "action": "close_water",
            "params": {},
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(seconds=60)
            ).isoformat(),
        },
        secret="чужой-ключ",
    )

    ack = await processor.async_handle(command)
    assert ack["reason"] == AckReason.BAD_SIGNATURE


async def test_expired_command_rejected(processor: CommandProcessor) -> None:
    """С-9: истёкший expires_at."""
    ack = await processor.async_handle(make_command(ttl=-10))

    assert ack["status"] == AckStatus.REJECTED
    assert ack["reason"] == AckReason.EXPIRED


async def test_ttl_above_limit_rejected(processor: CommandProcessor) -> None:
    """TTL больше 120 с недействителен (СБ-07)."""
    ack = await processor.async_handle(make_command(ttl=600))
    assert ack["reason"] == AckReason.EXPIRED


async def test_duplicate_command_executed_once(hass, processor: CommandProcessor) -> None:
    """С-11: повторная доставка исполняется один раз."""
    calls: list[Any] = []
    hass.services.async_register("valve", "close_valve", lambda call: calls.append(call))

    command = make_command()
    first = await processor.async_handle(command)
    second = await processor.async_handle(command)

    assert first["status"] == AckStatus.DONE
    assert second["status"] == AckStatus.DUPLICATE
    assert len(calls) == 1


async def test_remote_control_disabled(hass, processor: CommandProcessor) -> None:
    """С-8: рубильник выключен."""
    processor.coordinator.entry.options = {OPT_ALLOW_REMOTE_CONTROL: False}

    ack = await processor.async_handle(make_command())

    assert ack["reason"] == AckReason.REMOTE_CONTROL_DISABLED


async def test_unknown_action_rejected(hass, processor: CommandProcessor) -> None:
    """С-12: действия нет в словаре агента — ни один сервис не вызывается."""
    called = AsyncMock()
    hass.services.async_register("shell_command", "run", called)

    ack = await processor.async_handle(make_command(action="run_shell"))

    assert ack["reason"] == AckReason.UNKNOWN_ACTION
    called.assert_not_called()


async def test_domain_service_from_command_ignored(hass, processor: CommandProcessor) -> None:
    """П-3: поля domain/service/entity_id из тела команды не используются."""
    called = AsyncMock()
    hass.services.async_register("shell_command", "run", called)

    command = make_command()
    command["domain"] = "shell_command"
    command["service"] = "run"
    command["entity_id"] = "shell_command.run"

    calls: list[Any] = []
    hass.services.async_register("valve", "close_valve", lambda call: calls.append(call))

    ack = await processor.async_handle(command)

    # Команда исполнена штатно — по словарю агента, а не по полям из тела.
    assert ack["status"] == AckStatus.DONE
    assert calls[0].data["entity_id"] == "valve.main"
    called.assert_not_called()


async def test_confirm_action_requires_permission(hass, processor: CommandProcessor) -> None:
    """Действие «на снятие защиты» без персонального разрешения отвергается."""
    ack = await processor.async_handle(make_command(action="open_water"))
    assert ack["reason"] == AckReason.ACTION_NOT_PERMITTED

    processor.coordinator.entry.options[f"{OPT_PERMISSION_PREFIX}open_water"] = True
    hass.services.async_register("valve", "open_valve", AsyncMock())

    ack = await processor.async_handle(make_command(action="open_water", command_id="cmd_9"))
    assert ack["status"] == AckStatus.DONE


async def test_role_not_mapped(hass, processor: CommandProcessor) -> None:
    """Незаполненная роль означает отказ."""
    processor.coordinator.entry.options = {OPT_ALLOW_REMOTE_CONTROL: True}

    ack = await processor.async_handle(make_command())
    assert ack["reason"] == AckReason.ROLE_NOT_MAPPED


async def test_entity_unavailable(hass, processor: CommandProcessor) -> None:
    """Сопоставленная сущность недоступна."""
    hass.states.async_set("valve.main", "unavailable")

    ack = await processor.async_handle(make_command())
    assert ack["reason"] == AckReason.ENTITY_UNAVAILABLE


async def test_unsupported_entity_domain(hass, processor: CommandProcessor) -> None:
    """Тип сущности не поддерживается словарём (раздел 8, правило 1)."""
    processor.coordinator.entry.options[f"{OPT_ROLE_PREFIX}water_valve"] = "climate.boiler"
    hass.states.async_set("climate.boiler", "heat")

    ack = await processor.async_handle(make_command())
    assert ack["reason"] == AckReason.ROLE_NOT_MAPPED


async def test_service_failure_gives_failed(hass, processor: CommandProcessor) -> None:
    """Сбой сервиса HA — статус failed с текстом ошибки."""

    async def _boom(call):
        raise RuntimeError("клапан не отвечает")

    hass.services.async_register("valve", "close_valve", _boom)

    ack = await processor.async_handle(make_command())

    assert ack["status"] == AckStatus.FAILED
    assert "клапан" in ack["error"]


async def test_internal_actions_do_not_call_services(
    hass, processor: CommandProcessor
) -> None:
    """`ping` и `request_snapshot` обрабатываются без вызова сервисов."""
    ack = await processor.async_handle(make_command(action="ping"))
    assert ack["status"] == AckStatus.DONE

    ack = await processor.async_handle(
        make_command(action="request_snapshot", command_id="cmd_snap")
    )
    assert ack["status"] == AckStatus.DONE
    processor.coordinator.async_request_snapshot.assert_called_once()


async def test_history_survives_reload(hass) -> None:
    """Реестр исполненных команд переживает перезагрузку (СБ-08)."""
    history = CommandHistory(hass, "entry-1")
    await history.async_load()
    history.remember("cmd_persist")
    await history._store.async_save({"seen": {"cmd_persist": history._seen["cmd_persist"]}})

    restored = CommandHistory(hass, "entry-1")
    await restored.async_load()

    assert restored.contains("cmd_persist")
