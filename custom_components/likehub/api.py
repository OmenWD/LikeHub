"""HTTP-клиент облака: login, refresh, sync, SSE-поток.

Транспортный слой: знает про заголовки, таймауты и коды ответа, но ничего —
про сущности Home Assistant (правило слоёв, архитектура 3.1).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .const import (
    AGENT_VERSION,
    HTTP_TIMEOUT,
    SSE_SOCK_READ_TIMEOUT,
    TOKEN_REFRESH_MARGIN,
)

_LOGGER = logging.getLogger(__name__)


class LikeHubError(Exception):
    """Базовая ошибка обмена с облаком."""


class LikeHubConnectionError(LikeHubError):
    """Сеть недоступна или таймаут. Ретраится по бэкоффу."""


class LikeHubAuthError(LikeHubError):
    """401/403 по учётным данным: нужен refresh, затем reauth."""


class LikeHubRejectedError(LikeHubError):
    """400: тело отвергнуто навсегда. Ретрай запрещён (спецификация 6.5)."""


class LikeHubSiteBlockedError(LikeHubError):
    """403: объект заблокирован."""


class LikeHubSiteNotFoundError(LikeHubError):
    """404: site_id неизвестен сервису."""


class LikeHubDuplicateError(LikeHubError):
    """409: сервер уже видел этот батч — считаем доставленным."""


class LikeHubTooLargeError(LikeHubError):
    """413: батч нужно уменьшить вдвое."""


class LikeHubRateLimitError(LikeHubError):
    """429: ждём Retry-After."""

    def __init__(self, retry_after: int | None = None) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


class LikeHubServerError(LikeHubError):
    """5xx: ретраится по бэкоффу."""


@dataclass(slots=True)
class SiteInfo:
    site_id: str
    site_name: str


@dataclass(slots=True)
class LoginResult:
    site_id: str
    site_name: str
    sites: list[SiteInfo]
    access_token: str
    expires_in: int
    refresh_token: str
    site_secret: str


@dataclass(slots=True)
class SyncResult:
    server_time: str | None
    ack_seq: int | None
    next_poll_in: int | None
    want_full_snapshot: bool
    commands: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class StreamEvent:
    """Событие SSE. `name` — command / revoke / resync; пинги наружу не отдаются."""

    name: str
    data: dict[str, Any]


class LikeHubApi:
    """Клиент облака. Держит access-токен в памяти и обновляет его заранее."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        refresh_token: str,
        site_id: str | None = None,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._refresh_token = refresh_token
        self._site_id = site_id
        self._access_token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def refresh_token(self) -> str:
        return self._refresh_token

    @property
    def site_id(self) -> str | None:
        return self._site_id

    # --- Авторизация ---

    async def login(
        self, email: str, password: str, site_id: str | None = None
    ) -> LoginResult:
        """Вход по логину и паролю.

        Пароль используется только здесь и никуда не сохраняется (П-1).
        `site_id` передаётся, когда владелец выбрал объект из нескольких.
        """
        payload: dict[str, Any] = {
            "email": email,
            "password": password,
            "agent_version": AGENT_VERSION,
        }
        if site_id:
            payload["site_id"] = site_id

        data = await self._request("POST", "/v1/auth/login", json_body=payload, auth=False)
        result = LoginResult(
            site_id=data["site_id"],
            site_name=data.get("site_name", data["site_id"]),
            sites=[
                SiteInfo(site["site_id"], site.get("site_name", site["site_id"]))
                for site in data.get("sites", [])
            ],
            access_token=data["access_token"],
            expires_in=int(data.get("expires_in", 3600)),
            refresh_token=data["refresh_token"],
            site_secret=data["site_secret"],
        )
        self._apply_tokens(result.access_token, result.expires_in, result.refresh_token)
        self._site_id = result.site_id
        return result

    async def refresh(self) -> str:
        """Обновление access-токена. Возвращает актуальный refresh-токен."""
        data = await self._request(
            "POST",
            "/v1/auth/refresh",
            json_body={"refresh_token": self._refresh_token},
            auth=False,
        )
        self._apply_tokens(
            data["access_token"],
            int(data.get("expires_in", 3600)),
            data.get("refresh_token", self._refresh_token),
        )
        return self._refresh_token

    async def revoke(self) -> None:
        """Отзыв при удалении интеграции: best effort, ошибка не блокирует (ФТ-А-10)."""
        try:
            await self._request(
                "POST",
                "/v1/auth/revoke",
                json_body={"refresh_token": self._refresh_token},
                auth=False,
            )
        except LikeHubError as err:
            _LOGGER.debug("Отзыв токена не выполнен: %s", err)

    async def _ensure_token(self) -> None:
        """Обновляет токен заранее — за TOKEN_REFRESH_MARGIN до истечения."""
        async with self._lock:
            if self._access_token and time.monotonic() < self._expires_at:
                return
            await self.refresh()

    def _apply_tokens(self, access_token: str, expires_in: int, refresh_token: str) -> None:
        self._access_token = access_token
        self._expires_at = time.monotonic() + max(expires_in - TOKEN_REFRESH_MARGIN, 30)
        self._refresh_token = refresh_token

    # --- Синхронизация ---

    async def sync(self, payload: dict[str, Any], idempotency_key: str) -> SyncResult:
        data = await self._request(
            "POST",
            "/v1/sync",
            json_body=payload,
            headers={"X-Idempotency-Key": idempotency_key},
        )
        return SyncResult(
            server_time=data.get("server_time"),
            ack_seq=data.get("ack_seq"),
            next_poll_in=data.get("next_poll_in"),
            want_full_snapshot=bool(data.get("want_full_snapshot", False)),
            # Неизвестные поля ответа игнорируются: сервер может развиваться
            # быстрее, чем обновляются агенты (раздел 11 ТЗ, пункт 3).
            commands=list(data.get("commands", [])),
        )

    # --- Канал команд ---

    async def stream(self) -> AsyncIterator[StreamEvent]:
        """SSE-подписка. Общего таймаута нет, только sock_read (ФТ-Р-09)."""
        await self._ensure_token()
        timeout = aiohttp.ClientTimeout(total=None, sock_read=SSE_SOCK_READ_TIMEOUT)

        try:
            async with self._session.get(
                f"{self._base_url}/v1/commands/stream",
                headers={**self._headers(), "Accept": "text/event-stream"},
                timeout=timeout,
            ) as response:
                if response.status in (401, 403):
                    raise LikeHubAuthError(f"Канал команд отклонён: {response.status}")
                if response.status == 404:
                    # Сервер без канала команд — не ошибка: работаем по fallback (ТЗ 11.1).
                    raise LikeHubSiteNotFoundError("Канал команд не развёрнут")
                if response.status >= 400:
                    raise LikeHubServerError(f"Канал команд вернул {response.status}")

                event_name = "message"
                data_lines: list[str] = []

                async for raw in response.content:
                    line = raw.decode("utf-8").rstrip("\r\n")

                    if line.startswith(":"):
                        continue  # комментарий-пинг

                    if not line:
                        if data_lines:
                            payload = "\n".join(data_lines)
                            data_lines = []
                            name, event_name = event_name, "message"
                            try:
                                yield StreamEvent(name, json.loads(payload))
                            except json.JSONDecodeError:
                                _LOGGER.debug("Неразбираемое SSE-сообщение пропущено")
                        continue

                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].strip())

        except aiohttp.ClientError as err:
            raise LikeHubConnectionError(str(err)) from err
        except asyncio.TimeoutError as err:
            raise LikeHubConnectionError("Таймаут чтения канала команд") from err

    # --- Низкий уровень ---

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Agent-Version": AGENT_VERSION,
        }
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        if self._site_id:
            headers["X-Site-Id"] = self._site_id
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        if auth:
            await self._ensure_token()

        request_headers = {**self._headers(), **(headers or {})}

        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                json=json_body,
                headers=request_headers,
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
            ) as response:
                if response.status == 204:
                    return {}

                # Тело разбирается независимо от заголовка Content-Type: прокси
                # и обратные прокси его иногда не проставляют, а игнорировать
                # содержимое ответа из-за этого нельзя.
                body: dict[str, Any] = {}
                try:
                    parsed = await response.json(content_type=None)
                    if isinstance(parsed, dict):
                        body = parsed
                except (aiohttp.ContentTypeError, json.JSONDecodeError, ValueError):
                    body = {}

                self._raise_for_status(response.status, body, response.headers)
                return body

        except aiohttp.ClientError as err:
            raise LikeHubConnectionError(str(err)) from err
        except asyncio.TimeoutError as err:
            raise LikeHubConnectionError("Таймаут запроса") from err

    @staticmethod
    def _raise_for_status(
        status: int, body: dict[str, Any], headers: Any
    ) -> None:
        """Реакция на коды ответа — строго по таблице 6.5 спецификации."""
        if status < 400:
            return

        error_code = str(body.get("error", ""))

        if status == 400:
            raise LikeHubRejectedError(error_code or "validation_failed")
        if status == 401:
            raise LikeHubAuthError(error_code or "unauthorized")
        if status == 403:
            if error_code == "site_blocked":
                raise LikeHubSiteBlockedError(error_code)
            raise LikeHubAuthError(error_code or "forbidden")
        if status == 404:
            raise LikeHubSiteNotFoundError(error_code or "site_not_found")
        if status == 409:
            raise LikeHubDuplicateError(error_code or "batch_already_accepted")
        if status == 413:
            raise LikeHubTooLargeError(error_code or "payload_too_large")
        if status == 429:
            retry_after = headers.get("Retry-After") if headers else None
            raise LikeHubRateLimitError(int(retry_after) if retry_after else None)
        if status >= 500:
            raise LikeHubServerError(f"HTTP {status}")

        raise LikeHubError(f"HTTP {status}: {error_code}")
