# LikeHub — Home Assistant integration

*[Русская версия](README.ru.md)*

Cloud agent for Home Assistant: it reports alarms, telemetry and heartbeats to the LikeHub service and executes the commands the service sends back. Outbound HTTPS only — no ports to open, no inbound connections, no HTTP handlers registered in Home Assistant.

## Requirements

- Home Assistant 2025.1 or newer
- Any installation type: HA OS, Supervised, Container, Core
- Outbound access to `https://api.likehub.me` on port 443

No external dependencies: `requirements` in `manifest.json` is empty.

## Installation

1. Create an account at [likehub.me/crm/](https://likehub.me/crm/) — the “Registration” tab on the sign-in screen. It creates your first site along with the account.
2. HACS → ⋮ → **Custom repositories** → add `https://github.com/OmenWD/LikeHub`, category `Integration`.
3. Find **LikeHub** in HACS, download the latest release, then **restart Home Assistant**.
4. Settings → Devices & services → **Add integration** → **LikeHub**.
5. Enter the e-mail and password of your LikeHub account. The server address is prefilled with `https://api.likehub.me`.

The password is used once to obtain tokens and is **never stored**: only the refresh token goes into the config entry. When you change the password, Home Assistant shows “Reauthentication required” — enter the new one and every setting of the site is preserved.

If your account holds several sites, a “Select site” step follows: pick the one this Home Assistant installation serves.

Manual installation without HACS: copy `custom_components/likehub/` from the release into your `config/custom_components/`, restart, and continue from step 4.

## Configuration

Settings → Devices & services → **Integrations** tab → the **LikeHub** card → **Configure** on the site entry. Direct link: `/config/integrations/integration/likehub`.

A menu of three sections opens.

### Devices being sent

Nothing leaves your home until you pick something here — this is the one section that has to be filled in.

The section opens with a table of what is being sent — device, how many readings, and which ones — and below it the same list as ticks. **Unticking a row is how you remove it**: the device stops being sent, while it and its entities stay untouched in Home Assistant. Entities without a device (helpers, template sensors) appear as their own rows.

Two ticks lead further:

- **Add a device** — choose a device, then tick which of its readings to send. The current value is shown next to each one, so it is clear what exactly will leave the house. “Add another device” loops back to the picker.
- **Configure whole sensor groups** — switches for *All measurements*, *All alarm sensors*, *Sockets and relays*, *Lighting*, *Sirens*, *Valves*. A group covers every matching entity, including ones you add later, and is the only way to include entities that have no device.

The integration's own diagnostic entities are never offered and are ignored even if a whole domain is selected: `sensor.*_event_queue` grows from the very act of sending, so subscribing to it would feed itself.

### Advanced

| Option | Default | Meaning |
|---|---|---|
| Send telemetry | on | Numeric readings. Alarms for the selected entities are sent regardless |
| No more than one sync per | 60 s | A floor, not a period: the server assigns the interval (300 s by default) and cannot go below this value |

### Remote control

The master switch for command execution, off by default. Device mapping for actions will appear here together with command delivery in the second version of the service; until then the server returns an empty command list.

Settings apply immediately — the entry reloads itself, no Home Assistant restart needed.

## Entities

| Entity | What it shows |
|---|---|
| `binary_sensor.*_command_channel` | whether the command channel (SSE) is established |
| `sensor.*_last_sync` | time of the last acknowledged synchronization |
| `sensor.*_event_queue` | how many events wait to be sent; the attributes carry how many were dropped |
| `sensor.*_last_command` | status of the last command and its attributes |
| `switch.*_remote_control` | the master switch, usable from automations |
| `button.*_test_connection` | sends a test event immediately |

## Action dictionary

The cloud sends only an action name from a closed list. The `domain` and `service` are derived by the agent itself from the type of the entity you mapped — they are never taken from the command. A command carrying an arbitrary service (`shell_command`, say) is rejected with `unknown_action`, and no Home Assistant service is called at all.

| Action | Role | What it does | Needs a separate permission |
|---|---|---|---|
| `close_water` | water valve | closes the valve | no |
| `open_water` | water valve | opens the valve | **yes** |
| `siren_on` | siren | turns on | no |
| `siren_off` | siren | turns off | **yes** |
| `light_on` / `light_off` | alert light | turns on / off | no |
| `request_snapshot` | — | sends a full snapshot out of turn | no |
| `ping` | — | channel check | no |

Every executed command lands in the logbook with the action, the entity, the initiator and the result.

Role mapping and the two permissions are not shown in the settings form yet: the server issues no commands, so there is nothing to configure. The checks themselves are implemented in the agent and covered by tests; the screen returns together with command delivery.

## Remote control arrives in version 2

Command execution is fully implemented in the agent, but the server side of command delivery ships in the second stage. Until then the server answers with an empty command list and `switch.*_remote_control` stays off. This is deliberate: changing the command protocol after users have installed the integration costs more than implementing it up front.

## When the connection drops

Events pile up in a queue (up to 5000) that survives a Home Assistant restart. Once the link is back, everything is delivered without duplicates. On overflow only telemetry is discarded — alarms are never dropped, and the number of lost messages is reported to the server.

The queue is written to disk lazily (every 10 seconds, and only when it changed): Home Assistant often runs from an SD card.

## Privacy

- The password is neither stored nor logged
- Nothing is sent to the cloud until you explicitly select entities
- Tokens, the signing key and the e-mail address are stripped from diagnostics
- Connections are outbound only: the integration opens no ports and registers no HTTP handlers

## Brand icons

Icons live inside the integration — `custom_components/likehub/brand/` (the HA 2026.3+ mechanism, nothing to declare in the manifest). For 2025.1–2026.2 the same files are submitted through a pull request to [home-assistant/brands](https://github.com/home-assistant/brands); the source and generator are in `brand/`.

Redraw: `python brand/generate_icons.py --variant a --out brand`

## Changelog

Every release carries its notes from [CHANGELOG.md](CHANGELOG.md), written in English. A tag without a matching section fails the release workflow, so no version ships undocumented.

## Development

```bash
python3.13 -m venv .venv
.venv/bin/pip install pytest-homeassistant-custom-component
.venv/bin/python -m pytest          # 89 tests, 89 % coverage
```

CI runs the suite on both ends of the supported range: Python 3.13 with HA 2025.1 (the manifest minimum) and Python 3.14 with the current core, plus hassfest and the HACS action.

## License

[Apache License 2.0](LICENSE). The LikeHub name and logo are trademarks of the rights holder; the code license grants no rights to them (§6).
