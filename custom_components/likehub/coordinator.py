"""Координатор: единственная точка отправки данных в облако.

Тик, внеочередные отправки, дельта/снимок, очередь и бэкофф. Все отправки идут
через один `asyncio.Lock`: параллельных POST /v1/sync не бывает никогда, поэтому
`seq` монотонен без гонок, а сервер не получает пересекающиеся батчи (архитектура 3.1).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event

from .api import (
    LikeHubApi,
    LikeHubAuthError,
    LikeHubConnectionError,
    LikeHubDuplicateError,
    LikeHubRateLimitError,
    LikeHubRejectedError,
    LikeHubServerError,
    LikeHubSiteBlockedError,
    LikeHubSiteNotFoundError,
    LikeHubTooLargeError,
)
from .const import (
    AGENT_VERSION,
    ATTR_WHITELIST,
    BACKOFF_JITTER,
    BACKOFF_MAX,
    BACKOFF_START,
    BATCH_MAX_EVENTS,
    BATTERY_LOW_COOLDOWN,
    BATTERY_LOW_THRESHOLD,
    BURST_WINDOW,
    DEFAULT_MIN_INTERVAL,
    DEFAULT_SEND_TELEMETRY,
    DEFAULT_TICK_INTERVAL,
    DOMAIN,
    ISSUE_SITE_BLOCKED,
    ISSUE_SITE_NOT_FOUND,
    KIND_BY_DEVICE_CLASS,
    KIND_MEASUREMENT,
    KIND_UNAVAILABLE,
    MAX_TICK_INTERVAL,
    MIN_TICK_INTERVAL,
    OPT_DOMAINS,
    OPT_ENTITIES,
    OPT_MIN_INTERVAL,
    OPT_SEND_TELEMETRY,
    Severity,
    SyncReason,
)
from .queue import EventQueue

_LOGGER = logging.getLogger(__name__)

UNAVAILABLE_STATES = {"unavailable", "unknown"}


class LikeHubCoordinator:
    """Управляет отправкой событий и состояний в облако."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: LikeHubApi,
        queue: EventQueue,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.api = api
        self.queue = queue

        self.seq = 0
        self.last_sync: datetime | None = None
        self.last_error: str | None = None
        self.sent_count = 0
        self.failed_count = 0
        self.rejected_count = 0

        self._lock = asyncio.Lock()
        self._interval = DEFAULT_TICK_INTERVAL
        self._backoff = 0.0
        self._need_snapshot = True
        self._pending_states: dict[str, dict[str, Any]] = {}
        self._pending_acks: list[dict[str, Any]] = []
        self._burst_task: asyncio.Task[None] | None = None
        self._unsub_state: Any = None
        self._tracked: set[str] = set()
        self._battery_notified: dict[str, float] = {}
        self._batch_limit = BATCH_MAX_EVENTS
        self._listeners: list[Any] = []
        self._stopped = False

    # --- Жизненный цикл ---

    async def async_start(self) -> None:
        """Первая синхронизация и подписка на изменения выбранных сущностей."""
        self._resubscribe()
        self.async_add_event(
            entity_id=f"{DOMAIN}.agent",
            kind="agent_started",
            severity=Severity.INFO,
            name="Агент запущен",
            state=AGENT_VERSION,
        )
        await self.async_sync(SyncReason.BOOT)

    async def async_stop(self) -> None:
        """Снятие подписок и сохранение очереди (ФТ-Р-10)."""
        self._stopped = True
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if self._burst_task and not self._burst_task.done():
            self._burst_task.cancel()
        await self.queue.async_save_now()

    @callback
    def add_listener(self, listener: Any) -> Any:
        """Подписка сущностей на обновления состояния координатора."""
        self._listeners.append(listener)

        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    @callback
    def _notify_listeners(self) -> None:
        for listener in list(self._listeners):
            listener()

    # --- Выбор отправляемых сущностей ---

    @property
    def selected_entities(self) -> set[str]:
        """Сущности, выбранные владельцем. Пусто — наружу не уходит ничего (СБ-09)."""
        options = self.entry.options
        selected: set[str] = set(options.get(OPT_ENTITIES, []) or [])

        domains = options.get(OPT_DOMAINS, []) or []
        if domains:
            for state in self.hass.states.async_all():
                if state.domain in domains:
                    selected.add(state.entity_id)
        return selected

    @property
    def send_telemetry(self) -> bool:
        return bool(self.entry.options.get(OPT_SEND_TELEMETRY, DEFAULT_SEND_TELEMETRY))

    @property
    def min_interval(self) -> int:
        return int(self.entry.options.get(OPT_MIN_INTERVAL, DEFAULT_MIN_INTERVAL))

    @property
    def interval(self) -> int:
        return self._interval

    @callback
    def _resubscribe(self) -> None:
        """Переподписка при изменении списка сущностей.

        Смена списка требует снимка: сервер должен узнать о новом наборе целиком
        (ФТ-О-07).
        """
        entities = self.selected_entities
        if entities == self._tracked and self._unsub_state:
            return

        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None

        if self._tracked and entities != self._tracked:
            self._need_snapshot = True

        self._tracked = entities
        if entities:
            self._unsub_state = async_track_state_change_event(
                self.hass, list(entities), self._handle_state_change
            )

    async def async_options_updated(self) -> None:
        self._resubscribe()

    # --- Обработка изменений состояний ---

    @callback
    def _handle_state_change(self, event: Event) -> None:
        new_state: State | None = event.data.get("new_state")
        old_state: State | None = event.data.get("old_state")
        if new_state is None:
            return
        # Отсутствие изменения значения событием не считается (ФТ-О-05).
        if old_state is not None and old_state.state == new_state.state:
            return

        kind, severity = self._classify(new_state)

        if severity == Severity.INFO and not self.send_telemetry:
            # Телеметрия выключена, но аварии по выбранным сущностям идут всегда (ФТ-Н-05).
            self._pending_states[new_state.entity_id] = self._state_item(new_state)
            return

        if kind == "battery_low" and not self._battery_crossed(new_state):
            self._pending_states[new_state.entity_id] = self._state_item(new_state)
            return

        self.async_add_event(
            entity_id=new_state.entity_id,
            kind=kind,
            severity=severity,
            name=new_state.attributes.get("friendly_name", new_state.entity_id),
            state=new_state.state,
            previous=old_state.state if old_state else None,
            unit=new_state.attributes.get("unit_of_measurement"),
            area=self._area_of(new_state.entity_id),
            attrs=self._filter_attrs(new_state.attributes),
            occurred_at=new_state.last_changed,
        )
        self._pending_states[new_state.entity_id] = self._state_item(new_state)

    def _classify(self, state: State) -> tuple[str, Severity]:
        """Тип события по device_class и домену.

        Сырой `device_class` наружу не отдаётся: сервер не должен переписываться
        под каждую новую железку (спецификация 7.2).
        """
        if state.state in UNAVAILABLE_STATES:
            return KIND_UNAVAILABLE, Severity.WARNING

        device_class = state.attributes.get("device_class")
        if device_class and device_class in KIND_BY_DEVICE_CLASS:
            kind, severity = KIND_BY_DEVICE_CLASS[device_class]
            # У бинарных датчиков тревога — только переход в `on`.
            if state.domain == "binary_sensor" and state.state != "on":
                return kind, Severity.INFO
            return kind, severity

        return KIND_MEASUREMENT, Severity.INFO

    def _battery_crossed(self, state: State) -> bool:
        """`battery_low` — только при переходе через порог сверху вниз, не чаще раза в сутки."""
        try:
            level = float(state.state)
        except (TypeError, ValueError):
            return False
        if level > BATTERY_LOW_THRESHOLD:
            self._battery_notified.pop(state.entity_id, None)
            return False

        now = time.monotonic()
        last = self._battery_notified.get(state.entity_id)
        if last is not None and now - last < BATTERY_LOW_COOLDOWN:
            return False
        self._battery_notified[state.entity_id] = now
        return True

    def _area_of(self, entity_id: str) -> str | None:
        try:
            from homeassistant.helpers import area_registry as ar
            from homeassistant.helpers import device_registry as dr
            from homeassistant.helpers import entity_registry as er

            entity_registry = er.async_get(self.hass)
            entry = entity_registry.async_get(entity_id)
            if entry is None:
                return None

            area_id = entry.area_id
            if area_id is None and entry.device_id:
                device = dr.async_get(self.hass).async_get(entry.device_id)
                area_id = device.area_id if device else None
            if area_id is None:
                return None

            area = ar.async_get(self.hass).async_get_area(area_id)
            return area.name if area else None
        except Exception:  # noqa: BLE001 — зона необязательна, ошибка реестра не важна
            return None

    @staticmethod
    def _filter_attrs(attributes: Any) -> dict[str, Any] | None:
        """Белый список атрибутов: не более 10 полей (решение В-4)."""
        filtered = {
            key: value
            for key, value in attributes.items()
            if key in ATTR_WHITELIST and isinstance(value, (str, int, float, bool))
        }
        return filtered or None

    @staticmethod
    def _state_item(state: State) -> dict[str, Any]:
        return {
            "entity_id": state.entity_id,
            "state": state.state,
            "unit": state.attributes.get("unit_of_measurement"),
            "changed_at": state.last_changed.isoformat(),
        }

    # --- Постановка событий и ACK ---

    @callback
    def async_add_event(
        self,
        *,
        entity_id: str,
        kind: str,
        severity: Severity,
        name: str,
        state: str,
        previous: str | None = None,
        unit: str | None = None,
        area: str | None = None,
        attrs: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        """Кладёт событие в очередь; аварии уходят внеочередной отправкой."""
        moment = occurred_at or datetime.now(timezone.utc)
        event = {
            "event_id": f"{entity_id}:{int(moment.timestamp())}",
            "kind": kind,
            "severity": str(severity),
            "entity_id": entity_id,
            "name": name,
            "state": str(state)[:255],
            "occurred_at": moment.isoformat(),
        }
        if previous is not None:
            event["previous"] = str(previous)[:255]
        if unit:
            event["unit"] = unit
        if area:
            event["area"] = area
        if attrs:
            event["attrs"] = attrs

        self.queue.append(event)
        self._notify_listeners()

        if severity in (Severity.CRITICAL, Severity.WARNING):
            self._schedule_burst()

    @callback
    def async_add_ack(self, ack: dict[str, Any]) -> None:
        """ACK доставляется внеочередной синхронизацией не позднее 2 с (ФТ-К-06)."""
        self._pending_acks.append(ack)
        self._schedule_burst()

    @callback
    def _schedule_burst(self) -> None:
        """Склейка внеочередных отправок окном 2 с (ФТ-О-04)."""
        if self._stopped:
            return
        if self._burst_task and not self._burst_task.done():
            return

        async def _burst() -> None:
            await asyncio.sleep(BURST_WINDOW)
            await self.async_sync(SyncReason.EVENT)

        self._burst_task = self.entry.async_create_background_task(
            self.hass, _burst(), name=f"{DOMAIN}_burst"
        )

    @callback
    def async_request_snapshot(self) -> None:
        self._need_snapshot = True

    # --- Синхронизация ---

    async def async_sync(self, reason: SyncReason) -> bool:
        """Одна синхронизация. Возвращает True при успешной доставке."""
        if self._stopped:
            return False

        async with self._lock:
            if self._backoff and time.monotonic() < self._backoff:
                return False

            self.seq += 1
            events = self.queue.head(self._batch_limit)
            payload = self._build_payload(reason, events)
            idempotency_key = (
                events[0]["event_id"] if events else f"seq:{self.entry.entry_id}:{self.seq}"
            )

            try:
                result = await self.api.sync(payload, idempotency_key)

            except LikeHubDuplicateError:
                # Сервер уже видел этот батч — считаем доставленным (спецификация 6.5).
                self._on_delivered(events)
                return True

            except LikeHubRejectedError as err:
                # 400: тело отвергнуто навсегда, ретрай запрещён.
                self.rejected_count += 1
                self.last_error = f"400 {err}"
                _LOGGER.warning("Сервер отверг батч навсегда: %s", err)
                self.queue.remove({e["event_id"] for e in events})
                return False

            except LikeHubTooLargeError:
                # 413: уменьшаем батч вдвое (ФТ-О-11).
                self._halve_batch(events)
                return False

            except LikeHubAuthError as err:
                self.last_error = "auth"
                raise ConfigEntryAuthFailed("Требуется повторная авторизация") from err

            except LikeHubSiteBlockedError:
                self._raise_issue(ISSUE_SITE_BLOCKED)
                self._apply_backoff(3600)
                return False

            except LikeHubSiteNotFoundError:
                self._raise_issue(ISSUE_SITE_NOT_FOUND)
                self._apply_backoff(3600)
                return False

            except LikeHubRateLimitError as err:
                self._apply_backoff(err.retry_after or BACKOFF_START)
                return False

            except (LikeHubConnectionError, LikeHubServerError) as err:
                self.failed_count += 1
                self.last_error = str(err)
                # Ошибки сети — debug: при длительном офлайне лог не должен спамиться (ФТ-Р-07).
                _LOGGER.debug("Синхронизация не удалась: %s", err)
                self._apply_backoff()
                return False

            self._on_success(result, events, payload)
            return True

    def _build_payload(
        self, reason: SyncReason, events: list[dict[str, Any]]
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "seq": self.seq,
            "reason": str(reason),
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "queue_size": self.queue.size,
            "dropped_count": self.queue.dropped,
            "agent": {
                "version": AGENT_VERSION,
                "ha_version": getattr(self.hass.config, "version", None),
                "install_type": self._install_type(),
            },
            "events": events,
        }

        states = self._build_states(reason)
        if states:
            payload["states"] = states

        if self._pending_acks:
            payload["acks"] = list(self._pending_acks)

        return payload

    def _build_states(self, reason: SyncReason) -> dict[str, Any] | None:
        """Снимок при первом запуске, reconnect, запросе сервера и смене набора (ФТ-О-07)."""
        if self._need_snapshot or reason in (SyncReason.BOOT, SyncReason.RECONNECT):
            items = [
                self._state_item(state)
                for entity_id in sorted(self.selected_entities)
                if (state := self.hass.states.get(entity_id)) is not None
            ]
            return {"kind": "snapshot", "items": items}

        if not self._pending_states:
            return None
        return {"kind": "delta", "items": list(self._pending_states.values())}

    def _install_type(self) -> str:
        """Тип установки HA — для диагностики на сервере (ФТ-Д-02)."""
        try:
            from homeassistant.helpers.system_info import async_get_system_info  # noqa: F401
        except ImportError:
            return "unknown"
        if hasattr(self.hass, "components") and hasattr(self.hass.components, "hassio"):
            return "os"
        return "container"

    def _on_success(
        self, result: Any, events: list[dict[str, Any]], payload: dict[str, Any]
    ) -> None:
        self._backoff = 0.0
        self._batch_limit = BATCH_MAX_EVENTS
        self.last_error = None
        self.last_sync = datetime.now(timezone.utc)
        self.sent_count += len(events)

        self._on_delivered(events)
        self.queue.confirm_dropped()

        if payload.get("states", {}).get("kind") == "snapshot":
            self._need_snapshot = False
        self._pending_states.clear()
        self._pending_acks.clear()

        if result.next_poll_in:
            lower = max(MIN_TICK_INTERVAL, self.min_interval)
            self._interval = max(lower, min(int(result.next_poll_in), MAX_TICK_INTERVAL))

        if result.want_full_snapshot:
            self._need_snapshot = True

        self._notify_listeners()

    def _on_delivered(self, events: list[dict[str, Any]]) -> None:
        self.queue.remove({e["event_id"] for e in events})
        self._notify_listeners()

    def _halve_batch(self, events: list[dict[str, Any]]) -> None:
        """413: уменьшаем батч вдвое, минимум одно событие (ФТ-О-11).

        Лимит живёт в объекте, а не в модуле: у нескольких объектов в одном HA
        не должно быть общего состояния.
        """
        self._batch_limit = max(1, len(events) // 2)
        _LOGGER.debug("Батч уменьшен до %d событий", self._batch_limit)

    def _apply_backoff(self, seconds: float | None = None) -> None:
        """Бэкофф 5 с → ×2 → 300 с с джиттером ±20 % (ФТ-Р-05)."""
        if seconds is None:
            current = max(BACKOFF_START, min(self._current_backoff() * 2, BACKOFF_MAX))
        else:
            current = min(float(seconds), BACKOFF_MAX)

        jitter = current * BACKOFF_JITTER
        delay = current + random.uniform(-jitter, jitter)
        self._backoff = time.monotonic() + max(delay, 1.0)
        self._last_backoff = current

    def _current_backoff(self) -> float:
        return getattr(self, "_last_backoff", BACKOFF_START / 2)

    def _raise_issue(self, issue_id: str) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{issue_id}_{self.entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=issue_id,
        )

    # --- Диагностика ---

    def diagnostics(self) -> dict[str, Any]:
        """Счётчики и состояние без секретов (ФТ-Д-03)."""
        return {
            "seq": self.seq,
            "queue_size": self.queue.size,
            "dropped": self.queue.dropped,
            "interval": self._interval,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "last_error": self.last_error,
            "sent_count": self.sent_count,
            "failed_count": self.failed_count,
            "rejected_count": self.rejected_count,
            "tracked_entities": len(self._tracked),
            "need_snapshot": self._need_snapshot,
        }
