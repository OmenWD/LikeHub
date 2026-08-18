"""Выгрузка диагностики без секретов (ФТ-С-08, С-17).

Токены, `site_secret` и e-mail вырезаются обязательно: файл диагностики пользователь
прикладывает к обращению в поддержку, и он не должен давать доступ к объекту.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_EMAIL,
    CONF_REFRESH_TOKEN,
    CONF_SITE_SECRET,
    OPT_ALLOW_REMOTE_CONTROL,
    OPT_DOMAINS,
    OPT_ENTITIES,
    OPT_MIN_INTERVAL,
    OPT_SEND_TELEMETRY,
)

if TYPE_CHECKING:
    from . import LikeHubConfigEntry

TO_REDACT = {CONF_REFRESH_TOKEN, CONF_SITE_SECRET, CONF_EMAIL, "password", "access_token"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LikeHubConfigEntry
) -> dict[str, Any]:
    data = entry.runtime_data
    options = entry.options

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": {
            "entities_count": len(options.get(OPT_ENTITIES, []) or []),
            "domains": options.get(OPT_DOMAINS, []),
            "send_telemetry": options.get(OPT_SEND_TELEMETRY),
            "min_interval": options.get(OPT_MIN_INTERVAL),
            "allow_remote_control": options.get(OPT_ALLOW_REMOTE_CONTROL),
            "roles_mapped": [
                key for key, value in options.items() if key.startswith("role_") and value
            ],
        },
        "coordinator": data.coordinator.diagnostics(),
        "channel": {"connected": data.stream_connected},
        "last_command": data.commands.last_command,
    }
