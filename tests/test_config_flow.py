"""Config flow: установка, выбор объекта, дубль, ошибки, reauth (ФТ-А)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.likehub.api import (
    LikeHubAuthError,
    LikeHubConnectionError,
    LikeHubError,
)
from custom_components.likehub.const import (
    CONF_BASE_URL,
    CONF_EMAIL,
    CONF_REFRESH_TOKEN,
    CONF_SITE_ID,
    CONF_SITE_SECRET,
    DOMAIN,
)

from .conftest import EMAIL, PASSWORD, SITE_ID

USER_INPUT = {
    CONF_EMAIL: EMAIL,
    "password": PASSWORD,
    CONF_BASE_URL: "https://api.example.test",
}


def _patch_login(result_or_error):
    mock = (
        AsyncMock(side_effect=result_or_error)
        if isinstance(result_or_error, Exception)
        else AsyncMock(return_value=result_or_error)
    )
    return patch(
        "custom_components.likehub.config_flow.LikeHubApi.login", mock
    ), mock


async def test_user_flow_creates_entry(hass, login_result) -> None:
    """Успешная установка создаёт запись без пароля (ФТ-А-04, П-1)."""
    patcher, _ = _patch_login(login_result)
    with patcher, patch(
        "custom_components.likehub.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SITE_ID] == SITE_ID
    assert result["data"][CONF_REFRESH_TOKEN] == "refresh-1"
    assert result["data"][CONF_SITE_SECRET]
    # Пароля в записи нет и быть не может.
    assert "password" not in result["data"]
    assert PASSWORD not in str(result["data"])


async def test_multiple_sites_shows_picker(hass, login_result_multi, login_result) -> None:
    """ФТ-А-06: несколько объектов — шаг выбора."""
    with patch(
        "custom_components.likehub.config_flow.LikeHubApi.login",
        AsyncMock(side_effect=[login_result_multi, login_result]),
    ), patch("custom_components.likehub.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "site"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_SITE_ID: SITE_ID}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_invalid_auth(hass) -> None:
    """ФТ-А-07: неверный пароль."""
    patcher, _ = _patch_login(LikeHubAuthError("invalid_credentials"))
    with patcher:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_cannot_connect(hass) -> None:
    patcher, _ = _patch_login(LikeHubConnectionError("timeout"))
    with patcher:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["errors"] == {"base": "cannot_connect"}


async def test_unknown_error(hass) -> None:
    patcher, _ = _patch_login(LikeHubError("boom"))
    with patcher:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["errors"] == {"base": "unknown"}


async def test_duplicate_site_aborts(hass, config_entry, login_result) -> None:
    """Повторная установка того же объекта запрещена (ФТ-А-07)."""
    config_entry.add_to_hass(hass)

    patcher, _ = _patch_login(login_result)
    with patcher:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.ABORT


async def test_reauth_asks_only_password(hass, config_entry, login_result) -> None:
    """ФТ-А-08, С-13: reauth запрашивает только пароль и сохраняет настройки."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={"entities": ["binary_sensor.leak"]}
    )

    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    # В форме только пароль: e-mail берётся из записи.
    assert set(result["data_schema"].schema) == {"password"}

    login_result.refresh_token = "refresh-2"
    with patch(
        "custom_components.likehub.config_flow.LikeHubApi.login",
        AsyncMock(return_value=login_result),
    ), patch("custom_components.likehub.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "новый-пароль"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_REFRESH_TOKEN] == "refresh-2"
    # Настройки объекта пережили reauth.
    assert config_entry.options["entities"] == ["binary_sensor.leak"]


async def test_reauth_wrong_password(hass, config_entry) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)

    with patch(
        "custom_components.likehub.config_flow.LikeHubApi.login",
        AsyncMock(side_effect=LikeHubAuthError("invalid")),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "неверный"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
