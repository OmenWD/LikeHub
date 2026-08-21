"""HTTP-клиент: заголовки, разбор кодов ответа, SSE (ФТ-Р-06, таблица 6.5)."""

from __future__ import annotations

import pytest

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.likehub.api import (
    LikeHubApi,
    LikeHubAuthError,
    LikeHubDuplicateError,
    LikeHubRateLimitError,
    LikeHubRejectedError,
    LikeHubServerError,
    LikeHubSiteBlockedError,
    LikeHubSiteNotFoundError,
    LikeHubTooLargeError,
)
from custom_components.likehub.const import AGENT_VERSION

BASE = "https://api.example.test"


@pytest.fixture
async def api(hass, aioclient_mock) -> LikeHubApi:
    # Асинхронная и зависит от aioclient_mock: сессия создаётся внутри событийного
    # цикла и уже после подмены транспорта, иначе клиент уйдёт в реальную сеть.
    return LikeHubApi(
        async_get_clientsession(hass), BASE, refresh_token="refresh-1", site_id="site_1"
    )


async def test_login_parses_response(hass, api, aioclient_mock) -> None:
    aioclient_mock.post(
        f"{BASE}/v1/auth/login",
        json={
            "site_id": "site_7f3a19",
            "site_name": "Квартира",
            "sites": [{"site_id": "site_7f3a19", "site_name": "Квартира"}],
            "access_token": "access-1",
            "expires_in": 3600,
            "refresh_token": "refresh-2",
            "site_secret": "secret-1",
        },
    )

    result = await api.login("owner@example.com", "пароль")

    assert result.site_id == "site_7f3a19"
    assert result.refresh_token == "refresh-2"
    assert len(result.sites) == 1
    # Пароль уходит только в теле запроса и нигде не запоминается.
    assert aioclient_mock.mock_calls[0][2]["password"] == "пароль"


async def test_login_sends_site_id_when_chosen(hass, api, aioclient_mock) -> None:
    """Расширение протокола: выбор объекта из нескольких (раздел 11 ТЗ)."""
    aioclient_mock.post(
        f"{BASE}/v1/auth/login",
        json={
            "site_id": "site_2",
            "site_name": "Дача",
            "sites": [],
            "access_token": "a",
            "expires_in": 3600,
            "refresh_token": "r",
            "site_secret": "s",
        },
    )

    await api.login("owner@example.com", "пароль", site_id="site_2")

    assert aioclient_mock.mock_calls[0][2]["site_id"] == "site_2"


async def test_invalid_credentials(hass, api, aioclient_mock) -> None:
    aioclient_mock.post(
        f"{BASE}/v1/auth/login", status=401, json={"error": "invalid_credentials"}
    )

    with pytest.raises(LikeHubAuthError):
        await api.login("owner@example.com", "неверный")


async def test_refresh_rotates_token(hass, api, aioclient_mock) -> None:
    aioclient_mock.post(
        f"{BASE}/v1/auth/refresh",
        json={"access_token": "a2", "expires_in": 3600, "refresh_token": "r2"},
    )

    token = await api.refresh()

    assert token == "r2"
    assert api.refresh_token == "r2"


async def test_refresh_revoked_raises_auth(hass, api, aioclient_mock) -> None:
    aioclient_mock.post(
        f"{BASE}/v1/auth/refresh", status=401, json={"error": "refresh_revoked"}
    )

    with pytest.raises(LikeHubAuthError):
        await api.refresh()


async def test_revoke_ignores_errors(hass, api, aioclient_mock) -> None:
    """ФТ-А-10: отзыв best effort, ошибка не блокирует удаление."""
    aioclient_mock.post(f"{BASE}/v1/auth/revoke", status=500)

    await api.revoke()  # исключение не поднимается


async def test_sync_sends_required_headers(hass, api, aioclient_mock) -> None:
    aioclient_mock.post(
        f"{BASE}/v1/auth/refresh",
        json={"access_token": "a2", "expires_in": 3600, "refresh_token": "r2"},
    )
    aioclient_mock.post(
        f"{BASE}/v1/sync",
        json={
            "server_time": "2026-08-17T14:30:01+03:00",
            "ack_seq": 7,
            "next_poll_in": 300,
            "want_full_snapshot": False,
            "commands": [],
        },
    )

    result = await api.sync({"seq": 7}, idempotency_key="key-1")

    assert result.ack_seq == 7
    assert result.next_poll_in == 300

    headers = aioclient_mock.mock_calls[-1][3]
    assert headers["X-Site-Id"] == "site_1"
    assert headers["X-Idempotency-Key"] == "key-1"
    assert headers["X-Agent-Version"] == AGENT_VERSION
    assert headers["Authorization"] == "Bearer a2"


async def test_sync_ignores_unknown_fields(hass, api, aioclient_mock) -> None:
    """Раздел 11 ТЗ, пункт 3: неизвестные поля ответа игнорируются."""
    aioclient_mock.post(
        f"{BASE}/v1/auth/refresh",
        json={"access_token": "a", "expires_in": 3600, "refresh_token": "r"},
    )
    aioclient_mock.post(
        f"{BASE}/v1/sync",
        json={"ack_seq": 1, "next_poll_in": 300, "новое_поле": {"x": 1}},
    )

    result = await api.sync({"seq": 1}, idempotency_key="k")
    assert result.ack_seq == 1


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (400, {"error": "validation_failed"}, LikeHubRejectedError),
        (401, {"error": "token_expired"}, LikeHubAuthError),
        (403, {"error": "site_blocked"}, LikeHubSiteBlockedError),
        (403, {"error": "site_mismatch"}, LikeHubAuthError),
        (404, {"error": "site_not_found"}, LikeHubSiteNotFoundError),
        (409, {"error": "batch_already_accepted"}, LikeHubDuplicateError),
        (413, {"error": "payload_too_large"}, LikeHubTooLargeError),
        (429, {"error": "too_many_requests"}, LikeHubRateLimitError),
        (500, {}, LikeHubServerError),
        (503, {}, LikeHubServerError),
    ],
)
async def test_response_codes_map_to_exceptions(
    hass, api, aioclient_mock, status, payload, expected
) -> None:
    """Каждый код из таблицы 6.5 превращается в своё исключение."""
    aioclient_mock.post(
        f"{BASE}/v1/auth/refresh",
        json={"access_token": "a", "expires_in": 3600, "refresh_token": "r"},
    )
    aioclient_mock.post(f"{BASE}/v1/sync", status=status, json=payload)

    with pytest.raises(expected):
        await api.sync({"seq": 1}, idempotency_key="k")


async def test_rate_limit_carries_retry_after(hass, api, aioclient_mock) -> None:
    aioclient_mock.post(
        f"{BASE}/v1/auth/refresh",
        json={"access_token": "a", "expires_in": 3600, "refresh_token": "r"},
    )
    aioclient_mock.post(
        f"{BASE}/v1/sync",
        status=429,
        json={"error": "too_many_requests"},
        headers={"Retry-After": "42"},
    )

    with pytest.raises(LikeHubRateLimitError) as err:
        await api.sync({"seq": 1}, idempotency_key="k")

    assert err.value.retry_after == 42


async def test_stream_parses_events_and_skips_pings(hass, api, aioclient_mock) -> None:
    """SSE: пинги пропускаются, события отдаются разобранными."""
    aioclient_mock.post(
        f"{BASE}/v1/auth/refresh",
        json={"access_token": "a", "expires_in": 3600, "refresh_token": "r"},
    )
    aioclient_mock.get(
        f"{BASE}/v1/commands/stream",
        text=(
            ": ping\n\n"
            'event: command\ndata: {"command_id":"cmd_1","action":"close_water"}\n\n'
            ": ping\n\n"
            'event: resync\ndata: {"want_full_snapshot":true}\n\n'
        ),
    )

    events = [event async for event in api.stream()]

    assert [e.name for e in events] == ["command", "resync"]
    assert events[0].data["command_id"] == "cmd_1"
    assert events[1].data["want_full_snapshot"] is True


async def test_stream_missing_endpoint(hass, api, aioclient_mock) -> None:
    """404 канала: сервер без команд — не ошибка, работаем по тику (ТЗ 11.1)."""
    aioclient_mock.post(
        f"{BASE}/v1/auth/refresh",
        json={"access_token": "a", "expires_in": 3600, "refresh_token": "r"},
    )
    aioclient_mock.get(f"{BASE}/v1/commands/stream", status=404)

    with pytest.raises(LikeHubSiteNotFoundError):
        [event async for event in api.stream()]


async def test_stream_unauthorized(hass, api, aioclient_mock) -> None:
    aioclient_mock.post(
        f"{BASE}/v1/auth/refresh",
        json={"access_token": "a", "expires_in": 3600, "refresh_token": "r"},
    )
    aioclient_mock.get(f"{BASE}/v1/commands/stream", status=401)

    with pytest.raises(LikeHubAuthError):
        [event async for event in api.stream()]
