"""Приём и исполнение команд из облака.

Реализует правило П-3: из облака приходит только имя действия из закрытого словаря,
зашитого в агенте. `domain`, `service` и `entity_id` из тела команды не берутся
никогда — иначе скомпрометированный сервер исполнил бы в доме пользователя
произвольный сервис HA, включая `shell_command` (сценарий приёмки С-12).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    ACTIONS,
    COMMAND_HISTORY_MAX,
    COMMAND_HISTORY_TTL,
    COMMAND_MAX_TTL,
    DEFAULT_ALLOW_REMOTE_CONTROL,
    DOMAIN,
    EVENT_COMMAND,
    Intent,
    OPT_ALLOW_REMOTE_CONTROL,
    OPT_PERMISSION_PREFIX,
    OPT_ROLE_PREFIX,
    SERVICE_BY_DOMAIN_INTENT,
    STORAGE_KEY_COMMANDS,
    STORAGE_VERSION,
    AckReason,
    AckStatus,
)

_LOGGER = logging.getLogger(__name__)


class CommandHistory:
    """Реестр исполненных команд: 24 ч, ≤ 500 записей, переживает перезагрузку (СБ-08)."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY_COMMANDS}.{entry_id}"
        )
        self._seen: dict[str, float] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data:
            self._seen = {k: float(v) for k, v in data.get("seen", {}).items()}
        self._prune()

    def contains(self, command_id: str) -> bool:
        self._prune()
        return command_id in self._seen

    def remember(self, command_id: str) -> None:
        self._seen[command_id] = time.time()
        self._prune()
        self._store.async_delay_save(lambda: {"seen": self._seen}, 10)

    def _prune(self) -> None:
        deadline = time.time() - COMMAND_HISTORY_TTL
        self._seen = {k: v for k, v in self._seen.items() if v > deadline}
        if len(self._seen) > COMMAND_HISTORY_MAX:
            oldest = sorted(self._seen.items(), key=lambda item: item[1])
            for key, _ in oldest[: len(self._seen) - COMMAND_HISTORY_MAX]:
                self._seen.pop(key, None)

    async def async_remove_storage(self) -> None:
        await self._store.async_remove()


class CommandProcessor:
    """Проверяет и исполняет команды, формирует ACK."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: Any,
        site_secret: str,
        history: CommandHistory,
    ) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self._site_secret = site_secret
        self._history = history
        self.last_command: dict[str, Any] | None = None

    async def async_handle(self, command: dict[str, Any]) -> dict[str, Any]:
        """Обрабатывает команду и возвращает ACK.

        Порядок проверок из ФТ-К-04 соблюдается строго: подпись первой, до разбора
        содержимого, чтобы неподписанные данные не влияли ни на что — включая
        заполнение реестра исполненных команд.
        """
        command_id = str(command.get("command_id", ""))
        action_name = str(command.get("action", ""))

        # 1. Подпись — до любых действий, сравнение постоянного времени (СБ-06).
        if not self._verify_signature(command):
            _LOGGER.warning("Команда с некорректной подписью отвергнута")
            return self._ack(command_id, AckStatus.REJECTED, AckReason.BAD_SIGNATURE)

        # 2. Срок действия (СБ-07).
        if self._is_expired(command):
            return self._ack(command_id, AckStatus.REJECTED, AckReason.EXPIRED)

        # 3. Защита от повторов (ФТ-К-08).
        if self._history.contains(command_id):
            return self._ack(command_id, AckStatus.DUPLICATE)

        # 4. Общий рубильник (ФТ-Н-03).
        if not self._remote_control_enabled():
            return self._ack(
                command_id, AckStatus.REJECTED, AckReason.REMOTE_CONTROL_DISABLED
            )

        # 5. Действие есть в словаре агента (ФТ-К-10).
        action = ACTIONS.get(action_name)
        if action is None:
            _LOGGER.warning("Неизвестное действие %s отвергнуто", action_name)
            return self._ack(command_id, AckStatus.REJECTED, AckReason.UNKNOWN_ACTION)

        # 6. Персональное разрешение для действий «на снятие защиты» (ФТ-Н-04).
        if action.confirm and not self._action_permitted(action_name):
            return self._ack(
                command_id, AckStatus.REJECTED, AckReason.ACTION_NOT_PERMITTED
            )

        # Внутренние действия сервисов HA не вызывают.
        if action.intent is Intent.INTERNAL:
            return await self._handle_internal(command_id, action_name)

        # 7. Роль сопоставлена с существующей и доступной сущностью.
        entity_id = self._entity_for_role(action.role)
        if not entity_id:
            return self._ack(command_id, AckStatus.REJECTED, AckReason.ROLE_NOT_MAPPED)

        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return self._ack(
                command_id, AckStatus.REJECTED, AckReason.ENTITY_UNAVAILABLE, entity_id
            )

        service = SERVICE_BY_DOMAIN_INTENT.get((state.domain, action.intent))
        if service is None:
            # Тип сущности не поддерживается словарём (раздел 8, правило 1).
            return self._ack(
                command_id, AckStatus.REJECTED, AckReason.ROLE_NOT_MAPPED, entity_id
            )

        self._history.remember(command_id)

        try:
            await self.hass.services.async_call(
                state.domain,
                service,
                {"entity_id": entity_id},
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001 — любая ошибка сервиса это failed
            _LOGGER.error("Команда %s не исполнена: %s", action_name, err)
            return self._ack(
                command_id, AckStatus.FAILED, None, entity_id, error=str(err)[:500]
            )

        return self._ack(command_id, AckStatus.DONE, None, entity_id)

    async def _handle_internal(self, command_id: str, action_name: str) -> dict[str, Any]:
        self._history.remember(command_id)
        if action_name == "request_snapshot":
            self.coordinator.async_request_snapshot()
        return self._ack(command_id, AckStatus.DONE)

    # --- Проверки ---

    def _verify_signature(self, command: dict[str, Any]) -> bool:
        """HMAC-SHA256 по строке из 6.4 спецификации, сравнение `compare_digest`."""
        signature = str(command.get("signature", ""))
        if not signature:
            return False

        params = command.get("params") or {}
        canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
        message = ".".join(
            [
                str(command.get("command_id", "")),
                str(command.get("action", "")),
                str(command.get("issued_at", "")),
                str(command.get("expires_at", "")),
                canonical,
            ]
        )
        expected = hmac.new(
            self._site_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        provided = signature.split("=", 1)[1] if signature.startswith("sha256=") else signature
        return hmac.compare_digest(expected, provided)

    @staticmethod
    def _is_expired(command: dict[str, Any]) -> bool:
        raw = command.get("expires_at")
        if not raw:
            return True
        try:
            expires_at = datetime.fromisoformat(str(raw))
        except ValueError:
            return True
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        if expires_at < now:
            return True
        # TTL больше допустимого — команда тоже недействительна (СБ-07).
        return (expires_at - now).total_seconds() > COMMAND_MAX_TTL

    def _remote_control_enabled(self) -> bool:
        return bool(
            self.coordinator.entry.options.get(
                OPT_ALLOW_REMOTE_CONTROL, DEFAULT_ALLOW_REMOTE_CONTROL
            )
        )

    def _action_permitted(self, action_name: str) -> bool:
        return bool(
            self.coordinator.entry.options.get(f"{OPT_PERMISSION_PREFIX}{action_name}", False)
        )

    def _entity_for_role(self, role: str | None) -> str | None:
        if role is None:
            return None
        value = self.coordinator.entry.options.get(f"{OPT_ROLE_PREFIX}{role}")
        return str(value) if value else None

    # --- ACK и аудит ---

    def _ack(
        self,
        command_id: str,
        status: AckStatus,
        reason: AckReason | None = None,
        entity_id: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        ack: dict[str, Any] = {
            "command_id": command_id,
            "status": str(status),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        if reason is not None:
            ack["reason"] = str(reason)
        if error:
            ack["error"] = error

        self.last_command = {
            "command_id": command_id,
            "status": str(status),
            "reason": str(reason) if reason else None,
            "entity_id": entity_id,
            "at": ack["finished_at"],
        }
        return ack

    def fire_event(self, command: dict[str, Any], ack: dict[str, Any]) -> None:
        """Событие на шине HA: попадает в логбук с инициатором и статусом (ФТ-К-07)."""
        self.hass.bus.async_fire(
            EVENT_COMMAND,
            {
                "domain": DOMAIN,
                "command_id": ack.get("command_id"),
                "action": command.get("action"),
                "entity_id": (self.last_command or {}).get("entity_id"),
                "issued_by": command.get("issued_by"),
                "status": ack.get("status"),
                "reason": ack.get("reason"),
            },
        )
