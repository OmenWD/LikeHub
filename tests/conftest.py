"""Общие фикстуры тестов интеграции."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.likehub.api import LoginResult, SiteInfo, SyncResult
from custom_components.likehub.const import (
    CONF_BASE_URL,
    CONF_EMAIL,
    CONF_REFRESH_TOKEN,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    CONF_SITE_SECRET,
    DOMAIN,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

SITE_ID = "site_7f3a19"
SITE_SECRET = "test-site-secret"
EMAIL = "owner@example.com"
PASSWORD = "Пароль-Тестовый-1"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Без этого HA не видит custom_components в тестах."""
    yield


@pytest.fixture
def login_result() -> LoginResult:
    return LoginResult(
        site_id=SITE_ID,
        site_name="Квартира на Ленина",
        sites=[SiteInfo(SITE_ID, "Квартира на Ленина")],
        access_token="access-1",
        expires_in=3600,
        refresh_token="refresh-1",
        site_secret=SITE_SECRET,
    )


@pytest.fixture
def login_result_multi() -> LoginResult:
    """Аккаунт с несколькими объектами — включает шаг выбора (ФТ-А-06)."""
    return LoginResult(
        site_id=SITE_ID,
        site_name="Квартира на Ленина",
        sites=[
            SiteInfo(SITE_ID, "Квартира на Ленина"),
            SiteInfo("site_222222222222", "Дача"),
        ],
        access_token="access-1",
        expires_in=3600,
        refresh_token="refresh-1",
        site_secret=SITE_SECRET,
    )


@pytest.fixture
def sync_result() -> SyncResult:
    return SyncResult(
        server_time="2026-08-17T14:30:01+03:00",
        ack_seq=1,
        next_poll_in=300,
        want_full_snapshot=False,
        commands=[],
    )


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=SITE_ID,
        title="Квартира на Ленина",
        data={
            CONF_SITE_ID: SITE_ID,
            CONF_SITE_NAME: "Квартира на Ленина",
            CONF_EMAIL: EMAIL,
            CONF_REFRESH_TOKEN: "refresh-1",
            CONF_SITE_SECRET: SITE_SECRET,
            CONF_BASE_URL: "https://api.example.test",
        },
        options={},
    )


@pytest.fixture
def mock_api(sync_result: SyncResult) -> Any:
    """Клиент облака целиком под моком: сеть в тестах не используется."""

    async def _empty_stream():
        if False:  # pragma: no cover — генератор, который ничего не отдаёт
            yield None

    with patch(
        "custom_components.likehub.LikeHubApi", autospec=True
    ) as mock_class:
        instance = mock_class.return_value
        instance.refresh = AsyncMock(return_value="refresh-1")
        instance.sync = AsyncMock(return_value=sync_result)
        instance.revoke = AsyncMock(return_value=None)
        instance.stream = _empty_stream
        instance.site_id = SITE_ID
        instance.refresh_token = "refresh-1"
        yield instance
