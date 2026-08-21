"""Форма настроек: меню, цикл «устройство → параметры», группы, прочее (ФТ-Н)."""

from __future__ import annotations

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.likehub.const import (
    CONF_DEVICE,
    DOMAIN,
    OPT_ADD_ANOTHER,
    OPT_ALLOW_REMOTE_CONTROL,
    OPT_DOMAINS,
    OPT_ENTITIES,
    OPT_KEEP,
    OPT_MIN_INTERVAL,
    OPT_OPEN_GROUPS,
    OPT_SEND_TELEMETRY,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

LEAK = "binary_sensor.leak_kitchen"
BATTERY = "sensor.leak_battery"
SIGNAL = "sensor.leak_signal"
FOREIGN = "sensor.other_device_temp"


@pytest.fixture
def leak_device(hass: HomeAssistant, config_entry: MockConfigEntry) -> str:
    """Датчик протечки с тремя параметрами плюс сущность чужого устройства."""
    config_entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)

    device = devices.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "leak-kitchen")},
        name="Датчик протечки (кухня)",
    )
    other = devices.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "thermostat")},
        name="Термостат (гостиная)",
    )

    for entity_id, device_id in (
        (LEAK, device.id),
        (BATTERY, device.id),
        (SIGNAL, device.id),
        (FOREIGN, other.id),
    ):
        domain, object_id = entity_id.split(".")
        entities.async_get_or_create(
            domain, "test", object_id, device_id=device_id, suggested_object_id=object_id
        )

    hass.states.async_set(LEAK, "off", {"friendly_name": "Протечка"})
    hass.states.async_set(
        BATTERY,
        "87",
        {"friendly_name": "Заряд батареи", "unit_of_measurement": "%"},
    )
    hass.states.async_set(SIGNAL, "-64", {"friendly_name": "Уровень сигнала"})
    hass.states.async_set(FOREIGN, "22.4", {"friendly_name": "Температура"})
    return device.id


async def _open_menu(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    return result


async def test_init_shows_menu_with_summary(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Первый экран — меню из трёх разделов, а не форма на десять полей."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={OPT_ENTITIES: [LEAK, BATTERY], OPT_DOMAINS: ["sensor"]}
    )

    result = await _open_menu(hass, config_entry)

    assert result["menu_options"] == ["devices", "advanced", "remote"]
    # Сводка состояния — в описании шага: подписи пунктов меню HA не подставляет.
    assert result["description_placeholders"] == {"entities": "2", "groups": "1"}


async def test_device_cycle_saves_selected_readings(
    hass: HomeAssistant, config_entry: MockConfigEntry, leak_device: str
) -> None:
    """Выбор устройства → отметка его параметров → сохранение в entities."""
    result = await _open_menu(hass, config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_ADD_ANOTHER: True}
    )
    assert result["step_id"] == "add_device"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_DEVICE: leak_device}
    )
    assert result["step_id"] == "device_entities"
    assert result["description_placeholders"] == {"device": "Датчик протечки (кухня)"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_ENTITIES: [LEAK, BATTERY], OPT_ADD_ANOTHER: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[OPT_ENTITIES] == [LEAK, BATTERY]


async def test_readings_are_labelled_with_current_value(
    hass: HomeAssistant, config_entry: MockConfigEntry, leak_device: str
) -> None:
    """Рядом с параметром — имя и текущее значение, а не entity_id (ФТ-Н-02)."""
    result = await _open_menu(hass, config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_ADD_ANOTHER: True}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_DEVICE: leak_device}
    )

    labels = result["data_schema"].schema[OPT_ENTITIES].options
    assert labels[BATTERY] == "Заряд батареи · 87 %"
    assert labels[LEAK] == "Протечка · off"
    # Сущность соседнего устройства в список не попадает.
    assert FOREIGN not in labels


async def test_add_another_returns_to_device_step(
    hass: HomeAssistant, config_entry: MockConfigEntry, leak_device: str
) -> None:
    """Чекбокс «добавить ещё одно» замыкает цикл и не теряет отмеченное."""
    result = await _open_menu(hass, config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_ADD_ANOTHER: True}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_DEVICE: leak_device}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_ENTITIES: [LEAK], OPT_ADD_ANOTHER: True}
    )

    assert result["step_id"] == "add_device"

    # Второй заход по тому же устройству: ранее отмеченное показано как выбранное.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_DEVICE: leak_device}
    )
    assert result["data_schema"]({})[OPT_ENTITIES] == [LEAK]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_ENTITIES: [LEAK, SIGNAL], OPT_ADD_ANOTHER: False}
    )
    assert config_entry.options[OPT_ENTITIES] == [LEAK, SIGNAL]


async def test_unticking_removes_only_this_device(
    hass: HomeAssistant, config_entry: MockConfigEntry, leak_device: str
) -> None:
    """Снятая отметка убирает параметр, но выбор по другим устройствам цел."""
    hass.config_entries.async_update_entry(
        config_entry, options={OPT_ENTITIES: [LEAK, BATTERY, FOREIGN]}
    )

    result = await _open_menu(hass, config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_ADD_ANOTHER: True}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_DEVICE: leak_device}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_ENTITIES: [BATTERY], OPT_ADD_ANOTHER: False}
    )

    assert config_entry.options[OPT_ENTITIES] == [FOREIGN, BATTERY]


async def test_device_without_readings_shows_error(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Устройство без пригодных сущностей объясняет себя, а не даёт пустой список."""
    config_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "empty")},
        name="Пустышка",
    )

    result = await _open_menu(hass, config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_ADD_ANOTHER: True}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_DEVICE: device.id}
    )

    assert result["step_id"] == "add_device"
    assert result["errors"] == {"base": "device_without_entities"}


async def test_groups_save_domains(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Группы датчиков — отдельные переключатели, складываются в domains."""
    config_entry.add_to_hass(hass)

    result = await _open_menu(hass, config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_OPEN_GROUPS: True}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"group_sensor": True, "group_binary_sensor": True}
    )

    assert config_entry.options[OPT_DOMAINS] == ["sensor", "binary_sensor"]


async def test_advanced_saves_telemetry_and_floor(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)

    result = await _open_menu(hass, config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "advanced"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_SEND_TELEMETRY: False, OPT_MIN_INTERVAL: 120}
    )

    assert config_entry.options[OPT_SEND_TELEMETRY] is False
    assert config_entry.options[OPT_MIN_INTERVAL] == 120


async def test_remote_step_has_only_the_master_switch(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Роли и разрешения из формы убраны до выдачи команд сервером (О-21)."""
    config_entry.add_to_hass(hass)

    result = await _open_menu(hass, config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "remote"}
    )

    assert set(result["data_schema"].schema) == {OPT_ALLOW_REMOTE_CONTROL}


async def test_saving_keeps_hidden_roles_and_permissions(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Скрытые ключи переживают сохранение любого раздела: форма их не затирает."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            "role_water_valve": "valve.main",
            "permit_open_water": True,
            OPT_ENTITIES: [LEAK],
        },
    )

    result = await _open_menu(hass, config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "remote"}
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_ALLOW_REMOTE_CONTROL: True}
    )

    assert config_entry.options["role_water_valve"] == "valve.main"
    assert config_entry.options["permit_open_water"] is True
    assert config_entry.options[OPT_ENTITIES] == [LEAK]
    assert config_entry.options[OPT_ALLOW_REMOTE_CONTROL] is True


async def test_unticking_in_the_list_removes_a_device(
    hass: HomeAssistant, config_entry: MockConfigEntry, leak_device: str
) -> None:
    """Список передаваемого — он же управление: снятая отметка убирает устройство."""
    hass.config_entries.async_update_entry(
        config_entry, options={OPT_ENTITIES: [LEAK, BATTERY, FOREIGN]}
    )

    result = await _open_menu(hass, config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    assert result["step_id"] == "devices"

    options = result["data_schema"].schema[OPT_KEEP].config["options"]
    keys = [option["value"] for option in options]
    assert keys[0] == f"device:{leak_device}"
    assert options[0]["label"] == "Датчик протечки (кухня) · 2"
    # По умолчанию отмечено всё, что передаётся сейчас.
    assert result["data_schema"]({})[OPT_KEEP] == keys

    # Снимаем отметку с датчика протечки, оставляем термостат.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_KEEP: [keys[1]]}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[OPT_ENTITIES] == [FOREIGN]


async def test_list_shows_entities_without_device(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Помощник не привязан к устройству — в списке он отдельной строкой."""
    config_entry.add_to_hass(hass)
    hass.states.async_set(
        "input_number.test_temp", "21", {"friendly_name": "Тестовая температура"}
    )
    hass.config_entries.async_update_entry(
        config_entry, options={OPT_ENTITIES: ["input_number.test_temp"]}
    )

    result = await _open_menu(hass, config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )

    options = result["data_schema"].schema[OPT_KEEP].config["options"]
    assert options[0] == {
        "value": "entity:input_number.test_temp",
        "label": "Тестовая температура",
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_KEEP: []}
    )
    assert config_entry.options[OPT_ENTITIES] == []


async def test_empty_list_offers_only_the_ways_in(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Пока ничего не передаётся, списка нет — только переходы к добавлению."""
    config_entry.add_to_hass(hass)

    result = await _open_menu(hass, config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )

    assert set(result["data_schema"].schema) == {OPT_ADD_ANOTHER, OPT_OPEN_GROUPS}
    assert result["description_placeholders"]["devices"] == "—"


async def test_summary_is_a_markdown_table(
    hass: HomeAssistant, config_entry: MockConfigEntry, leak_device: str
) -> None:
    """Сводка — таблица Markdown: имя, число параметров и что именно уходит."""
    hass.config_entries.async_update_entry(
        config_entry, options={OPT_ENTITIES: [LEAK, BATTERY, FOREIGN]}
    )

    result = await _open_menu(hass, config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )

    assert result["description_placeholders"]["devices"].splitlines() == [
        "| Устройство | Параметров | Что передаётся |",
        "|---|---:|---|",
        "| Датчик протечки (кухня) | 2 | Протечка, Заряд батареи |",
        "| Термостат (гостиная) | 1 | Температура |",
    ]


async def test_own_entities_are_never_offered(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Свои сенсоры выбрать нельзя: очередь растёт от отправки и зацикливается (BUG-008)."""
    config_entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)

    own = devices.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "site_agent")},
        name="LikeHub (Квартира на Ленина)",
    )
    entities.async_get_or_create(
        "sensor", DOMAIN, "queue", device_id=own.id, suggested_object_id="likehub_queue"
    )
    hass.states.async_set("sensor.likehub_queue", "0", {"friendly_name": "Очередь событий"})

    result = await _open_menu(hass, config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_ADD_ANOTHER: True}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_DEVICE: own.id}
    )

    # Пригодных параметров у собственного устройства нет — выбирать нечего.
    assert result["step_id"] == "add_device"
    assert result["errors"] == {"base": "device_without_entities"}
