"""Мастера установки и настройки.

Пароль вводится один раз и обменивается на токены; в ConfigEntry.data пишется
только refresh-токен (правило П-1). Пароль не сохраняется ни в записи, ни в опциях,
ни в логах — проверяется сценарием приёмки С-1 грепом по .storage/.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    LikeHubApi,
    LikeHubAuthError,
    LikeHubConnectionError,
    LikeHubError,
    LoginResult,
)
from .const import (
    ACTIONS,
    CONF_BASE_URL,
    CONF_EMAIL,
    CONF_REFRESH_TOKEN,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    CONF_SITE_SECRET,
    DEFAULT_ALLOW_REMOTE_CONTROL,
    DEFAULT_BASE_URL,
    DEFAULT_MIN_INTERVAL,
    DEFAULT_SEND_TELEMETRY,
    DOMAIN,
    MAX_TICK_INTERVAL,
    MIN_TICK_INTERVAL,
    OPT_ALLOW_REMOTE_CONTROL,
    OPT_DOMAINS,
    OPT_ENTITIES,
    OPT_MIN_INTERVAL,
    OPT_PERMISSION_PREFIX,
    OPT_ROLE_PREFIX,
    OPT_SEND_TELEMETRY,
    ROLES,
    SIGNUP_URL,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL)
        ),
        vol.Required("password"): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
    }
)


class LikeHubConfigFlow(ConfigFlow, domain=DOMAIN):
    """Установка интеграции через UI (ФТ-А-01)."""

    VERSION = 1

    def __init__(self) -> None:
        self._email: str | None = None
        self._password: str | None = None
        self._base_url: str = DEFAULT_BASE_URL
        self._login: LoginResult | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL]
            self._password = user_input["password"]
            self._base_url = user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL)

            try:
                self._login = await self._async_login(self._email, self._password)
            except LikeHubAuthError:
                errors["base"] = "invalid_auth"
            except LikeHubConnectionError:
                errors["base"] = "cannot_connect"
            except LikeHubError:
                errors["base"] = "unknown"
            else:
                if len(self._login.sites) > 1:
                    return await self.async_step_site()
                return await self._async_create(self._login)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
            # Учётная запись заводится на сайте сервиса: интеграция регистрацию
            # не выполняет и без готового аккаунта войти не может (КП-09).
            description_placeholders={"signup_url": SIGNUP_URL},
        )

    async def async_step_site(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Выбор объекта, если у аккаунта их несколько (ФТ-А-06)."""
        assert self._login is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            site_id = user_input[CONF_SITE_ID]
            try:
                # Повторный вход с выбранным объектом: токены выдаются на объект.
                login = await self._async_login(
                    self._email or "", self._password or "", site_id=site_id
                )
            except LikeHubError:
                errors["base"] = "cannot_connect"
            else:
                return await self._async_create(login)

        options = {site.site_id: site.site_name for site in self._login.sites}
        return self.async_show_form(
            step_id="site",
            data_schema=vol.Schema({vol.Required(CONF_SITE_ID): vol.In(options)}),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Повторная авторизация запрашивает только пароль (ФТ-А-08)."""
        self._email = entry_data.get(CONF_EMAIL)
        self._base_url = entry_data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            try:
                login = await self._async_login(
                    entry.data[CONF_EMAIL],
                    user_input["password"],
                    site_id=entry.data[CONF_SITE_ID],
                )
            except LikeHubAuthError:
                errors["base"] = "invalid_auth"
            except LikeHubConnectionError:
                errors["base"] = "cannot_connect"
            except LikeHubError:
                errors["base"] = "unknown"
            else:
                # Обновляем только токены: выбор сущностей и ролей сохраняется (С-13).
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_REFRESH_TOKEN: login.refresh_token,
                        CONF_SITE_SECRET: login.site_secret,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required("password"): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
            description_placeholders={"email": entry.data.get(CONF_EMAIL, "")},
        )

    async def _async_login(
        self, email: str, password: str, site_id: str | None = None
    ) -> LoginResult:
        api = LikeHubApi(
            async_get_clientsession(self.hass), self._base_url, refresh_token=""
        )
        return await api.login(email, password, site_id=site_id)

    async def _async_create(self, login: LoginResult) -> ConfigFlowResult:
        """Создание записи. Один config entry = один объект (спецификация, раздел 3)."""
        await self.async_set_unique_id(login.site_id)
        self._abort_if_unique_id_configured(error="site_already_configured")

        return self.async_create_entry(
            title=login.site_name,
            data={
                CONF_SITE_ID: login.site_id,
                CONF_SITE_NAME: login.site_name,
                CONF_EMAIL: self._email,
                CONF_REFRESH_TOKEN: login.refresh_token,
                CONF_SITE_SECRET: login.site_secret,
                CONF_BASE_URL: self._base_url,
                # Пароля здесь нет и быть не может (П-1).
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> LikeHubOptionsFlow:
        return LikeHubOptionsFlow()


class LikeHubOptionsFlow(OptionsFlow):
    """Настройка после установки (ФТ-Н-01…07)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema: dict[Any, Any] = {
            # По умолчанию пусто: без явного выбора наружу не уходит ничего (СБ-09).
            vol.Optional(OPT_ENTITIES, default=list(options.get(OPT_ENTITIES, []))): (
                EntitySelector(EntitySelectorConfig(multiple=True))
            ),
            vol.Optional(OPT_DOMAINS, default=list(options.get(OPT_DOMAINS, []))): (
                vol.All(
                    cv_multi_select(
                        {
                            "binary_sensor": "binary_sensor",
                            "sensor": "sensor",
                            "switch": "switch",
                            "valve": "valve",
                            "light": "light",
                            "siren": "siren",
                        }
                    )
                )
            ),
            vol.Optional(
                OPT_SEND_TELEMETRY,
                default=options.get(OPT_SEND_TELEMETRY, DEFAULT_SEND_TELEMETRY),
            ): BooleanSelector(),
            vol.Optional(
                OPT_MIN_INTERVAL,
                default=options.get(OPT_MIN_INTERVAL, DEFAULT_MIN_INTERVAL),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_TICK_INTERVAL,
                    max=MAX_TICK_INTERVAL,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="с",
                )
            ),
            vol.Optional(
                OPT_ALLOW_REMOTE_CONTROL,
                default=options.get(
                    OPT_ALLOW_REMOTE_CONTROL, DEFAULT_ALLOW_REMOTE_CONTROL
                ),
            ): BooleanSelector(),
        }

        # Сопоставление ролей: незаполненная роль → действие отвергается role_not_mapped.
        for role in ROLES:
            key = f"{OPT_ROLE_PREFIX}{role}"
            schema[vol.Optional(key, description={"suggested_value": options.get(key)})] = (
                EntitySelector(EntitySelectorConfig(multiple=False))
            )

        # Отдельные разрешения для действий «на снятие защиты», по умолчанию выключены.
        for action_name, action in ACTIONS.items():
            if not action.confirm:
                continue
            key = f"{OPT_PERMISSION_PREFIX}{action_name}"
            schema[vol.Optional(key, default=options.get(key, False))] = BooleanSelector()

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))


def cv_multi_select(options: dict[str, str]) -> Any:
    """Мультивыбор доменов без зависимости от приватных хелперов HA."""
    from homeassistant.helpers import config_validation as cv

    return cv.multi_select(options)
