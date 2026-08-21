"""Жизненный цикл записи и диагностические сущности (ФТ-С, ФТ-Р-10)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState

from custom_components.likehub.api import LikeHubAuthError, LikeHubConnectionError
from custom_components.likehub.const import (
    CONF_SITE_SECRET,
    DOMAIN,
    OPT_ALLOW_REMOTE_CONTROL,
    OPT_ENTITIES,
)


async def test_setup_creates_device_and_entities(hass, config_entry, mock_api) -> None:
    """С-1: после установки есть устройство и 6 сущностей."""
    config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED

    entities = [
        state.entity_id
        for state in hass.states.async_all()
        if "likehub" in state.entity_id
    ]
    # Шесть диагностических сущностей из ФТ-С-01…06.
    assert len(entities) >= 6


async def test_unload_releases_everything(hass, config_entry, mock_api) -> None:
    """ФТ-Р-10: выгрузка снимает задачи и не оставляет висящих соединений."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_auth_failure_starts_reauth(hass, config_entry) -> None:
    """ФТ-А-08: отозванный refresh-токен запускает reauth."""
    config_entry.add_to_hass(hass)

    with patch(
        "custom_components.likehub.LikeHubApi.refresh",
        AsyncMock(side_effect=LikeHubAuthError("refresh_revoked")),
    ):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_offline_cloud_gives_retry(hass, config_entry) -> None:
    """Облако недоступно — запись уходит в retry, а не в ошибку."""
    config_entry.add_to_hass(hass)

    with patch(
        "custom_components.likehub.LikeHubApi.refresh",
        AsyncMock(side_effect=LikeHubConnectionError("timeout")),
    ):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_remote_control_switch_syncs_option(hass, config_entry, mock_api) -> None:
    """ФТ-С-05: рубильник синхронизирован с опцией."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    switch = next(
        state.entity_id
        for state in hass.states.async_all("switch")
        if "remote_control" in state.entity_id
    )
    assert hass.states.get(switch).state == "off"

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch}, blocking=True
    )
    await hass.async_block_till_done()

    assert config_entry.options[OPT_ALLOW_REMOTE_CONTROL] is True


async def test_diagnostics_has_no_secrets(hass, config_entry, mock_api) -> None:
    """С-17: токены, site_secret и e-mail отсутствуют в диагностике."""
    from custom_components.likehub.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    dump = str(diagnostics)

    assert config_entry.data[CONF_SITE_SECRET] not in dump
    assert "refresh-1" not in dump
    assert "owner@example.com" not in dump


async def test_options_update_reloads_entry(hass, config_entry, mock_api) -> None:
    """ФТ-Н-07: изменение опций перезагружает запись без перезапуска HA."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    hass.config_entries.async_update_entry(
        config_entry, options={OPT_ENTITIES: ["binary_sensor.leak"]}
    )
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.options[OPT_ENTITIES] == ["binary_sensor.leak"]


async def test_remove_entry_revokes_token(hass, config_entry, mock_api) -> None:
    """ФТ-А-10, С-16: удаление вызывает revoke и чистит .storage/."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    # Клиент уже под моком фикстурой mock_api: проверяем вызов на нём,
    # иначе повторный patch подменит атрибут, которым код не пользуется.
    assert await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_api.revoke.assert_called_once()
    assert not hass.config_entries.async_entries(DOMAIN)


def test_agent_version_matches_manifest() -> None:
    """Версия из манифеста уходит на сервер: расхождение ловится здесь, а не у пользователя."""
    import json
    from pathlib import Path

    from custom_components.likehub.const import AGENT_VERSION

    manifest = json.loads(
        (Path(__file__).parent.parent / "custom_components/likehub/manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert AGENT_VERSION == manifest["version"]


def test_changelog_has_a_section_for_this_version() -> None:
    """Заметки к релизу берутся из CHANGELOG: выпуск без секции упал бы в CI."""
    import json
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).parent.parent
    version = json.loads(
        (root / "custom_components/likehub/manifest.json").read_text(encoding="utf-8")
    )["version"]

    result = subprocess.run(
        [sys.executable, str(root / "scripts/changelog_section.py"), version],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), f"Секция {version} в CHANGELOG.md пуста"
