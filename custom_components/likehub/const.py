"""Константы интеграции: лимиты, словари событий и действий.

Модуль не импортирует другие модули интеграции (правило слоёв из архитектуры,
раздел 3.1). Значения обязаны совпадать с серверными в `server/src/domain/constants.ts`
(TASK-007): расхождение словарей даёт необъяснимые 400 на стыковке.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

DOMAIN: Final = "likehub"
AGENT_VERSION: Final = "1.3.0"
"""Уходит на сервер в теле синхронизации и заголовке; держится вровень с `manifest.json`."""
# Единственная точка правки при смене домена: от неё считаются адрес API
# и ссылка на регистрацию, которую видит пользователь в форме входа.
BRAND_DOMAIN: Final = "likehub.me"
DEFAULT_BASE_URL: Final = f"https://api.{BRAND_DOMAIN}"
# Регистрация — во вкладке «Регистрация» на экране входа веб-кабинета
# (likehub-web). Отдельной страницы /signup нет.
SIGNUP_URL: Final = f"https://{BRAND_DOMAIN}/crm/"

# --- Ключи ConfigEntry.data ---
CONF_SITE_ID: Final = "site_id"
CONF_SITE_NAME: Final = "site_name"
CONF_EMAIL: Final = "email"
CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_SITE_SECRET: Final = "site_secret"
CONF_BASE_URL: Final = "base_url"
# Пароль в ConfigEntry не сохраняется никогда (П-1) — константы для него нет намеренно.

# --- Ключи опций ---
OPT_ENTITIES: Final = "entities"
OPT_DOMAINS: Final = "domains"
OPT_SEND_TELEMETRY: Final = "send_telemetry"
OPT_MIN_INTERVAL: Final = "min_interval"
OPT_ALLOW_REMOTE_CONTROL: Final = "allow_remote_control"
OPT_ROLE_PREFIX: Final = "role_"
OPT_PERMISSION_PREFIX: Final = "permit_"

CONF_DEVICE: Final = "device"
"""Ключ шага выбора устройства в форме настроек; в опциях не хранится."""
OPT_ADD_ANOTHER: Final = "add_another"
"""Чекбокс «добавить ещё одно устройство»: замыкает цикл шагов, в опциях не хранится."""
OPT_KEEP: Final = "keep"
"""Отметки в списке передаваемого: снятая означает «больше не передавать»."""
OPT_OPEN_GROUPS: Final = "open_groups"
"""Переход к группам датчиков: кнопок в форме HA нет, переход — тоже отметка."""

DOMAIN_GROUPS: Final = {
    "group_sensor": "sensor",
    "group_binary_sensor": "binary_sensor",
    "group_switch": "switch",
    "group_light": "light",
    "group_siren": "siren",
    "group_valve": "valve",
}
"""Группы датчиков в форме настроек: ключ поля → домен HA.

Отдельное булево поле на группу, а не `multi_select` по доменам: подписи и пояснения
берутся из `strings.json` и переводятся, тогда как метки `multi_select` задаются
в коде и остались бы на одном языке."""

DEFAULT_MIN_INTERVAL: Final = 60
DEFAULT_SEND_TELEMETRY: Final = True
DEFAULT_ALLOW_REMOTE_CONTROL: Final = False

# --- Лимиты (спецификация, раздел 10) ---
DEFAULT_TICK_INTERVAL: Final = 300
MIN_TICK_INTERVAL: Final = 60
MAX_TICK_INTERVAL: Final = 3600
BURST_WINDOW: Final = 2.0
"""Окно склейки внеочередных отправок, секунды (ФТ-О-04)."""

QUEUE_MAX_EVENTS: Final = 5000
BATCH_MAX_EVENTS: Final = 500
BATCH_MAX_BYTES: Final = 256 * 1024
QUEUE_SAVE_DELAY: Final = 10
"""Отложенная запись очереди: HA часто работает с SD-карты (ФТ-Р-02)."""

HTTP_TIMEOUT: Final = 15
SSE_SOCK_READ_TIMEOUT: Final = 90
BACKOFF_START: Final = 5
BACKOFF_MAX: Final = 300
BACKOFF_JITTER: Final = 0.2
TOKEN_REFRESH_MARGIN: Final = 300
"""Токен обновляется заранее, а не по факту 401 (спецификация 6.1)."""

COMMAND_HISTORY_TTL: Final = 24 * 3600
COMMAND_HISTORY_MAX: Final = 500
COMMAND_MAX_TTL: Final = 120
"""TTL команды не более 120 с (СБ-07)."""

ACK_MAX_DELAY: Final = 2

BATTERY_LOW_THRESHOLD: Final = 20
"""Порог battery_low, проценты (решение В-4)."""
BATTERY_LOW_COOLDOWN: Final = 24 * 3600

ATTR_WHITELIST: Final = frozenset(
    {
        "device_class",
        "unit_of_measurement",
        "battery_level",
        "friendly_name",
        "area_id",
        "state_class",
        "last_changed",
    }
)
"""Белый список атрибутов события: не более 10 полей (решение В-4)."""

STORAGE_VERSION: Final = 1
STORAGE_KEY_QUEUE: Final = f"{DOMAIN}.queue"
STORAGE_KEY_COMMANDS: Final = f"{DOMAIN}.commands"

EVENT_COMMAND: Final = f"{DOMAIN}_command"
"""Событие на шине HA по каждой обработанной команде (ФТ-К-07)."""

ISSUE_ACCESS_REVOKED: Final = "access_revoked"
ISSUE_SITE_BLOCKED: Final = "site_blocked"
ISSUE_SITE_NOT_FOUND: Final = "site_not_found"
ISSUE_ROLES_NOT_MAPPED: Final = "roles_not_mapped"


class Severity(StrEnum):
    """Важность события (спецификация 7.1)."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class SyncReason(StrEnum):
    """Причина синхронизации (спецификация 6.2)."""

    TICK = "tick"
    EVENT = "event"
    ACK = "ack"
    RECONNECT = "reconnect"
    BOOT = "boot"
    MANUAL = "manual"


class AckStatus(StrEnum):
    DONE = "done"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    FAILED = "failed"


class AckReason(StrEnum):
    """Причины отказа (спецификация 6.4). Список закрытый."""

    BAD_SIGNATURE = "bad_signature"
    EXPIRED = "expired"
    UNKNOWN_ACTION = "unknown_action"
    REMOTE_CONTROL_DISABLED = "remote_control_disabled"
    ACTION_NOT_PERMITTED = "action_not_permitted"
    ROLE_NOT_MAPPED = "role_not_mapped"
    ENTITY_UNAVAILABLE = "entity_unavailable"


# --- Словарь типов событий (спецификация 7.2) ---
# Тип определяет агент по device_class и домену. Сырой device_class наружу не отдаётся:
# сервер не должен переписываться под каждую новую железку.

KIND_BY_DEVICE_CLASS: Final[dict[str, tuple[str, Severity]]] = {
    "moisture": ("water_leak", Severity.CRITICAL),
    "smoke": ("smoke", Severity.CRITICAL),
    "gas": ("gas", Severity.CRITICAL),
    "carbon_monoxide": ("gas", Severity.CRITICAL),
    "power": ("power_lost", Severity.CRITICAL),
    "door": ("door_open", Severity.WARNING),
    "window": ("door_open", Severity.WARNING),
    "opening": ("door_open", Severity.WARNING),
    "garage_door": ("door_open", Severity.WARNING),
    "motion": ("motion", Severity.INFO),
    "occupancy": ("motion", Severity.INFO),
    "temperature": ("temperature", Severity.INFO),
    "humidity": ("humidity", Severity.INFO),
    "battery": ("battery_low", Severity.WARNING),
}

KIND_MEASUREMENT: Final = "measurement"
KIND_UNAVAILABLE: Final = "unavailable"
KIND_TEST: Final = "test"
KIND_AGENT_STARTED: Final = "agent_started"


# --- Словарь действий (спецификация, раздел 8; правило П-3) ---


class Intent(StrEnum):
    """Намерение действия. domain/service выбирает агент по типу сущности."""

    OPEN = "open"
    CLOSE = "close"
    ON = "on"
    OFF = "off"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class Action:
    """Действие из закрытого словаря.

    Сервер присылает только имя действия. `domain` и `service` вычисляются агентом
    по домену сопоставленной владельцем сущности — из команды они не берутся никогда (П-3).
    """

    role: str | None
    intent: Intent
    confirm: bool = False
    """Требует отдельного разрешения: действия «на снятие защиты» (раздел 8, правило 2)."""


ROLE_WATER_VALVE: Final = "water_valve"
ROLE_SIREN: Final = "siren"
ROLE_ALERT_LIGHT: Final = "alert_light"

ROLES: Final = (ROLE_WATER_VALVE, ROLE_SIREN, ROLE_ALERT_LIGHT)

ACTIONS: Final[dict[str, Action]] = {
    "close_water": Action(ROLE_WATER_VALVE, Intent.CLOSE),
    "open_water": Action(ROLE_WATER_VALVE, Intent.OPEN, confirm=True),
    "siren_on": Action(ROLE_SIREN, Intent.ON),
    "siren_off": Action(ROLE_SIREN, Intent.OFF, confirm=True),
    "light_on": Action(ROLE_ALERT_LIGHT, Intent.ON),
    "light_off": Action(ROLE_ALERT_LIGHT, Intent.OFF),
    "request_snapshot": Action(None, Intent.INTERNAL),
    "ping": Action(None, Intent.INTERNAL),
}

SERVICE_BY_DOMAIN_INTENT: Final[dict[tuple[str, Intent], str]] = {
    ("valve", Intent.CLOSE): "close_valve",
    ("valve", Intent.OPEN): "open_valve",
    ("switch", Intent.CLOSE): "turn_off",
    ("switch", Intent.OPEN): "turn_on",
    ("switch", Intent.OFF): "turn_off",
    ("switch", Intent.ON): "turn_on",
    ("siren", Intent.ON): "turn_on",
    ("siren", Intent.OFF): "turn_off",
    ("light", Intent.ON): "turn_on",
    ("light", Intent.OFF): "turn_off",
    ("input_boolean", Intent.ON): "turn_on",
    ("input_boolean", Intent.OFF): "turn_off",
    ("input_boolean", Intent.OPEN): "turn_on",
    ("input_boolean", Intent.CLOSE): "turn_off",
}
"""Домен сущности + намерение → сервис HA. Домена нет в таблице — действие отвергается."""
