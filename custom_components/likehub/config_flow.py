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
    DeviceSelector,
    DeviceSelectorConfig,
    EntityFilterSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
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
    CONF_BASE_URL,
    CONF_DEVICE,
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
    DOMAIN_GROUPS,
    MAX_TICK_INTERVAL,
    MIN_TICK_INTERVAL,
    OPT_ADD_ANOTHER,
    OPT_ALLOW_REMOTE_CONTROL,
    OPT_DOMAINS,
    OPT_ENTITIES,
    OPT_MIN_INTERVAL,
    OPT_SEND_TELEMETRY,
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
    """Настройка после установки (ФТ-Н-01…07).

    Меню вместо одной формы: единственное, без чего интеграция не работает, — выбор
    передаваемых данных, и он не должен тонуть среди прочих полей. Роли и разрешения
    удалённого управления из формы убраны до появления выдачи команд на сервере (О-21):
    настраивать то, чего сервер не делает, вводит в заблуждение. Их значения в записи
    остаются нетронутыми — форма их не показывает и не затирает.
    """

    def __init__(self) -> None:
        self._device_id: str | None = None
        # Накопитель для цикла «устройство → параметры»: между шагами запись ещё
        # не сохранена, а выбор по предыдущим устройствам терять нельзя.
        self._entities: list[str] | None = None

    # --- Меню ---

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        options = self.config_entry.options
        return self.async_show_menu(
            step_id="init",
            menu_options=["devices", "advanced", "remote"],
            description_placeholders={
                "entities": str(len(options.get(OPT_ENTITIES, []) or [])),
                "groups": str(len(options.get(OPT_DOMAINS, []) or [])),
            },
        )

    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        menu = ["add_device"]
        # Пункт удаления появляется, только когда есть что удалять: пустая форма
        # выбора — тупик, из которого человек выходит кнопкой «назад».
        if self._current_entities():
            menu.append("remove_device")
        menu.append("groups")

        return self.async_show_menu(
            step_id="devices",
            menu_options=menu,
            description_placeholders={"devices": self._devices_summary()},
        )

    # --- Цикл «устройство → его параметры» ---

    async def async_step_add_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self._device_id = user_input[CONF_DEVICE]
            if self._device_entity_choices(self._device_id):
                return await self.async_step_device_entities()
            # Устройство без единой пригодной сущности выбрать можно, но настраивать
            # в нём нечего — честнее сказать сразу, чем показать пустой список.
            errors["base"] = "device_without_entities"

        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE): DeviceSelector(
                    DeviceSelectorConfig(
                        entity=[
                            EntityFilterSelectorConfig(
                                domain=list(DOMAIN_GROUPS.values())
                            )
                        ]
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="add_device", data_schema=schema, errors=errors
        )

    async def async_step_device_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        device_id = self._device_id
        assert device_id is not None
        choices = self._device_entity_choices(device_id)

        if user_input is not None:
            chosen = list(user_input.get(OPT_ENTITIES, []))
            # Снятые галочки означают отказ от сущности: пересобираем список так,
            # чтобы от этого устройства остались ровно отмеченные.
            kept = [e for e in self._current_entities() if e not in choices]
            self._entities = kept + chosen

            if user_input.get(OPT_ADD_ANOTHER):
                self._device_id = None
                return await self.async_step_add_device()
            return self._save({OPT_ENTITIES: self._entities})

        selected = [e for e in self._current_entities() if e in choices]
        schema = vol.Schema(
            {
                vol.Optional(OPT_ENTITIES, default=selected): cv_multi_select(choices),
                vol.Optional(OPT_ADD_ANOTHER, default=False): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="device_entities",
            data_schema=schema,
            description_placeholders={"device": self._device_name(device_id)},
        )

    async def async_step_remove_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Убрать устройство целиком.

        Снять галочки в шаге параметров тоже можно, но догадаться до этого нельзя:
        удаление должно называться удалением и лежать на виду.
        """
        grouped = self._entities_by_device()

        if user_input is not None:
            dropped = set(user_input.get(CONF_DEVICE, []))
            kept = [
                entity_id
                for key, entities in grouped.items()
                for entity_id in entities
                if key not in dropped
            ]
            self._entities = kept
            return self._save({OPT_ENTITIES: kept})

        choices = [
            SelectOptionDict(value=key, label=self._group_label(key, entities))
            for key, entities in grouped.items()
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE): SelectSelector(
                    SelectSelectorConfig(
                        options=choices,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                )
            }
        )
        return self.async_show_form(step_id="remove_device", data_schema=schema)

    # --- Группы датчиков целиком ---

    async def async_step_groups(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            domains = [
                domain
                for key, domain in DOMAIN_GROUPS.items()
                if user_input.get(key, False)
            ]
            return self._save({OPT_DOMAINS: domains})

        current = self.config_entry.options.get(OPT_DOMAINS, []) or []
        schema = vol.Schema(
            {
                vol.Optional(key, default=domain in current): BooleanSelector()
                for key, domain in DOMAIN_GROUPS.items()
            }
        )
        return self.async_show_form(step_id="groups", data_schema=schema)

    # --- Прочее ---

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(
                {
                    OPT_SEND_TELEMETRY: user_input[OPT_SEND_TELEMETRY],
                    OPT_MIN_INTERVAL: int(user_input[OPT_MIN_INTERVAL]),
                }
            )

        options = self.config_entry.options
        schema = vol.Schema(
            {
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
            }
        )
        return self.async_show_form(step_id="advanced", data_schema=schema)

    async def async_step_remote(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(
                {OPT_ALLOW_REMOTE_CONTROL: user_input[OPT_ALLOW_REMOTE_CONTROL]}
            )

        schema = vol.Schema(
            {
                vol.Optional(
                    OPT_ALLOW_REMOTE_CONTROL,
                    default=self.config_entry.options.get(
                        OPT_ALLOW_REMOTE_CONTROL, DEFAULT_ALLOW_REMOTE_CONTROL
                    ),
                ): BooleanSelector()
            }
        )
        return self.async_show_form(step_id="remote", data_schema=schema)

    # --- Вспомогательное ---

    def _save(self, changes: dict[str, Any]) -> ConfigFlowResult:
        """Слияние с прежними опциями.

        `async_create_entry` заменяет опции целиком, поэтому раздел, не показывающий
        роли и разрешения, стёр бы их. Сохраняем поверх копии прежних значений.
        """
        data = dict(self.config_entry.options)
        data.update(changes)
        return self.async_create_entry(data=data)

    def _current_entities(self) -> list[str]:
        if self._entities is not None:
            return self._entities
        return list(self.config_entry.options.get(OPT_ENTITIES, []) or [])

    def _device_entity_choices(self, device_id: str) -> dict[str, str]:
        """Сущности устройства с текущим значением в подписи.

        Человек выбирает «Заряд батареи · 87 %», а не `sensor.leak_battery`: видно,
        какие именно данные покинут дом.
        """
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(self.hass)
        choices: dict[str, str] = {}
        for entry in er.async_entries_for_device(registry, device_id):
            if entry.domain not in DOMAIN_GROUPS.values():
                continue
            state = self.hass.states.get(entry.entity_id)
            name = (
                state.attributes.get("friendly_name")
                if state is not None
                else entry.name or entry.original_name
            ) or entry.entity_id
            value = state.state if state is not None else "—"
            unit = (
                state.attributes.get("unit_of_measurement", "")
                if state is not None
                else ""
            )
            choices[entry.entity_id] = f"{name} · {value}{f' {unit}' if unit else ''}"
        return choices

    def _device_name(self, device_id: str) -> str:
        from homeassistant.helpers import device_registry as dr

        device = dr.async_get(self.hass).async_get(device_id)
        if device is None:
            return device_id
        return device.name_by_user or device.name or device_id

    def _entities_by_device(self) -> dict[str, list[str]]:
        """Выбранные сущности по устройствам.

        Ключ — `device:<id>` либо `entity:<entity_id>` для сущностей без устройства
        (помощники, шаблонные датчики): они удаляются поштучно и в список выбора
        попадают отдельными строками.
        """
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(self.hass)
        grouped: dict[str, list[str]] = {}
        for entity_id in self._current_entities():
            entry = registry.async_get(entity_id)
            device_id = entry.device_id if entry is not None else None
            key = f"device:{device_id}" if device_id else f"entity:{entity_id}"
            grouped.setdefault(key, []).append(entity_id)
        return grouped

    def _group_label(self, key: str, entities: list[str]) -> str:
        if key.startswith("device:"):
            return f"{self._device_name(key.removeprefix('device:'))} · {len(entities)}"

        entity_id = key.removeprefix("entity:")
        state = self.hass.states.get(entity_id)
        if state is None:
            return entity_id
        return state.attributes.get("friendly_name") or entity_id

    def _devices_summary(self) -> str:
        """Список настроенного для описания шага.

        Меню Home Assistant не умеет подставлять состояние в подписи пунктов, поэтому
        сводка выводится текстом над ними.
        """
        grouped = self._entities_by_device()
        if not grouped:
            return "—"
        # Пункты маркированного списка Markdown: описание шага рендерится как разметка,
        # и строки, начатые не с «-», склеились бы в один абзац.
        return "\n".join(
            f"- {self._group_label(key, entities)}"
            for key, entities in grouped.items()
        )


def cv_multi_select(options: dict[str, str]) -> Any:
    """Мультивыбор без зависимости от приватных хелперов HA."""
    from homeassistant.helpers import config_validation as cv

    return cv.multi_select(options)
