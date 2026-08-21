# Changelog

All notable changes to this integration are documented here. Each release on GitHub carries the section for its version, so this file is the single source for release notes.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.2] - 2026-08-21

### Changed

- Release notes are now written by hand in English in `CHANGELOG.md` instead of being generated from commit messages. The release workflow refuses to publish a tag that has no matching section, and it back-fills the notes of earlier releases from the same file.

## [1.3.1] - 2026-08-21

### Fixed

- The device picker no longer offers LikeHub's own device. Home Assistant's built-in device selector cannot exclude an integration, so the list is now built by hand and contains only devices that actually have something to send. Picking the agent's own diagnostic sensors is what caused the event loop fixed in 1.3.0.

### Changed

- Device rows show the area next to the name, matching the standard picker.
- When no suitable device exists, the step explains that instead of opening an empty dropdown.

## [1.3.0] - 2026-08-21

### Fixed

- **Event feedback loop.** Selecting the integration's own entities made every send grow the event queue, which changed the queue sensor, which produced another event. On a live installation the queue hit its 5000-event ceiling and real telemetry was being dropped. The agent now ignores its own entities whatever the options say — including when a whole domain is selected — and the settings form no longer offers them.

### Changed

- The list of what is being sent is now the control itself: a table of devices, readings and current values, with the same rows as ticks. Unticking a row stops that device from being sent, so the separate “Remove devices” step from 1.2.0 is gone.
- Home Assistant forms have no buttons, so the ways onward — adding a device, configuring sensor groups — are ticks as well.

## [1.2.1] - 2026-08-21

### Changed

- The English README ships in the release, so HACS shows it regardless of where it reads the file from.

## [1.2.0] - 2026-08-21

### Added

- A “Remove devices” step: previously the only way to stop sending a device was to reopen it and untick every reading, which nobody could be expected to guess. Entities without a device, such as helpers and template sensors, are listed there as their own rows.

### Fixed

- The summary of configured devices is a proper Markdown list. Single newlines are not line breaks in Markdown, so the entries used to run together into one paragraph.

## [1.1.0] - 2026-08-21

### Changed

- **The settings form was rebuilt.** One screen with ten fields became a menu of three sections: devices being sent, advanced, remote control.
- Data is chosen per device: pick a device, then tick its readings, with the current value shown next to each one. A tick loops back to add the next device.
- Domains became sensor groups with plain-language names instead of raw slugs.
- Every field carries a description underneath. “Minimum sync interval” now says what it is — a floor, not a period.

### Removed

- Role mapping and the two remote-control permissions are no longer shown. They configure command delivery, which the server will only ship in version 2, and the entity pickers behind them had no domain filter, so a water valve field would offer a weather forecast. Stored values are kept untouched and the checks remain implemented in the agent.

## [1.0.0] - 2026-08-19

First public release.

### Added

- Cloud agent for Home Assistant: alarms, telemetry and heartbeats over outbound HTTPS only, with a queue of up to 5000 events that survives a restart and delivers without duplicates once the link returns.
- Setup through the UI: e-mail and password are exchanged for tokens once, and only the refresh token is stored.
- Diagnostic entities: command channel, last sync, event queue, last command, the remote-control switch and a test-connection button.
- Command execution built to a closed action dictionary — the cloud never supplies a `domain` or `service`.
- Packaging for HACS, brand icons inside the integration, and a release workflow that publishes a tag and checks it against the manifest version.

[1.3.2]: https://github.com/OmenWD/LikeHub/releases/tag/v1.3.2
[1.3.1]: https://github.com/OmenWD/LikeHub/releases/tag/v1.3.1
[1.3.0]: https://github.com/OmenWD/LikeHub/releases/tag/v1.3.0
[1.2.1]: https://github.com/OmenWD/LikeHub/releases/tag/v1.2.1
[1.2.0]: https://github.com/OmenWD/LikeHub/releases/tag/v1.2.0
[1.1.0]: https://github.com/OmenWD/LikeHub/releases/tag/v1.1.0
[1.0.0]: https://github.com/OmenWD/LikeHub/releases/tag/v1.0.0
