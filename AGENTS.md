# AGENTS.md -- Control4 Dimmers HA Integration

This is the canonical agent guide for `bharat/homeassistant-control4-dimmers`. New Claude/Codex/Cursor sessions should read this before making changes. Pair it with `ARCHITECTURE.md`, `PLAN.md`, and `RESEARCH.md` for deeper context.

## What this is

A Home Assistant custom integration for **Control4 Zigbee dimmers / keypads** (C4-APD120 phase dimmer, C4-KD120 keypad-dimmer, C4-KC120277 configurable keypad). The hardware speaks two Zigbee profiles: the standard HA profile (`0x0104`, clusters `genOnOff` / `genLevelCtrl`) for on/off + dimming, and a proprietary text profile (`0xC25C`, manufacturer ID `0xABCD`) for LED color, button events, and device identification.

The stack has two halves:

1. **The Zigbee side** (separate repos): the [zigbee2mqtt-control4](https://github.com/bharat/zigbee2mqtt-control4) Docker image, built from a `zigbee-herdsman` fork (profile whitelist, `Endpoint.sendRaw`, C4 interview quirk) and a `zigbee-herdsman-converters` fork (`src/devices/control4.ts`, the in-tree device definition with runtime type detection and self-heal). C4 devices are natively supported there; there is no external converter and no herdsman patch anymore.
2. **This repo** -- the HA custom component (`custom_components/control4_dimmers/`): discovers devices from `zigbee2mqtt/bridge/devices`, creates sensor / event / light entities, and ships a custom Lovelace card (`control4-dimmer-card`) for the visual 6-slot configuration UI.

The two halves communicate ONLY over MQTT, via a frozen contract (see below). Nothing in this repo knows what hex bytes a C4 command uses beyond composing `c4.dmx.*` command strings for the `/set` topic.

## Layout

```
.
├── ARCHITECTURE.md             # System-level data flow + protocol reference (read first)
├── PLAN.md                     # Roadmap + completion status
├── RESEARCH.md                 # How the C4 protocol was reverse-engineered (narrative)
├── CONTRIBUTING.md             # Standard fork/PR flow
├── README.md                   # User-facing install + setup
│
├── custom_components/control4_dimmers/
│   ├── __init__.py             # async_setup_entry, services, WebSocket API
│   ├── manifest.json           # version is "0.0.0" sentinel -- see Releases section
│   ├── config_flow.py          # Single-step user flow (asks for MQTT base topic)
│   ├── const.py                # DOMAIN, DEVICE_TYPES, DEVICE_TYPE_SLOTS, BUTTON_EVENT_TYPES
│   ├── manager.py              # Brain: MQTT subs, device discovery, state, service routing
│   ├── models.py               # SlotConfig / DeviceConfig / DeviceState dataclasses
│   ├── store.py                # Persistent JSON in .storage/control4_dimmers_devices.<entry_id>
│   ├── sensor.py               # Device anchor + ambient light (lux) sensors
│   ├── light.py                # Brightness 0-255 (proxies Z2M's standard light entity)
│   ├── event.py                # One Control4ButtonEvent entity per button slot
│   ├── brand/                  # HACS brand icons (ship in-repo since HA 2026.3.0)
│   ├── frontend/
│   │   ├── __init__.py         # Serves the Lovelace card as a JS module
│   │   └── control4-dimmer-card.js  # LitElement; dashboard + chassis editor (~1500 LOC)
│   └── services.yaml           # set_device_config, set_slot, push_config, set_led, set_slot_led, press_button, send_raw_command, set_device_type, snapshot/restore
│
├── tests/                      # HA component tests (pytest + pytest-homeassistant-custom-component)
│   ├── conftest.py             # mock_hass / mock_entry / mock_store / mock_manager fixtures
│   └── test_*.py               # test modules
│
├── scripts/
│   ├── setup                   # Container post-create: pip + pre-commit + claude CLI + act
│   ├── develop                 # Mosquitto + HA + (optional) device simulator via concurrently
│   ├── lint                    # ruff check --fix && ruff format --check
│   ├── simulate_devices.py     # Fake Z2M devices for ./scripts/develop --sim (the in-repo stand-in for the Zigbee side)
│   └── led-*.json              # 14 LED probe payloads (protocol reverse-engineering archive -- NOT used at runtime)
│
├── .ruff.toml                  # `select = ["ALL"]` with ~10 disabled; max-complexity 25
├── .pre-commit-config.yaml     # ruff + EOF/whitespace + check-yaml + local pytest hook (15s timeout/test)
└── pyproject.toml              # Pytest config only (asyncio_mode = "auto", testpaths = ["tests"])
```

## Dev workflow

```bash
# First time inside the devcontainer (auto-runs scripts/setup on create):
pre-commit install                                  # If not already done

# Run the full local stack: Mosquitto broker + HA in debug + fake devices
./scripts/develop                                   # All three
./scripts/develop --no-sim                          # Skip simulator (use real Z2M)
./scripts/develop --fresh                           # Start with no detected devices

# HA dashboard: http://localhost:8123
# Add MQTT integration → broker localhost:1883
# Add Control4 Dimmers integration → MQTT base topic "zigbee2mqtt"

# Tests
python -m pytest tests/                             # HA component tests
python -m pytest -k test_device_discovery -v        # Pattern match

# Lint
./scripts/lint                                      # ruff check --fix + format --check
pre-commit run --all-files                          # Same hooks CI runs
```

Zigbee-side changes (device definition, protocol library, Docker image) are made in the fork repos and delivered by [zigbee2mqtt-control4](https://github.com/bharat/zigbee2mqtt-control4)'s CI, not from here.

## The frozen MQTT contract

This integration and the Zigbee-side definition are separate codebases coupled only by these MQTT names. Do NOT rename them on either side (the ZHC fork carries a contract test asserting the same list):

- **Inbound (device state)**: `c4_device_type` (`dimmer`/`keypaddim`/`keypad`), `c4_led_{1..6}_{on,off}` (flat hex), `action` with the 48-value grammar `button_{1..6}_{press,scene,click_{1..4}}` + `paddle_{up,down}_{press,scene,click_{1..4}}`, `state`/`brightness`, `c4_detect_result`, `c4_response`/`c4_response_ep`, `button_N_behavior`/`button_N_led_mode`.
- **Outbound (`<topic>/<name>/set`)**: `c4_cmd`, `c4_detect`, `c4_led`, `c4_query`, `state`/`brightness`, plus `button_N_behavior`/`button_N_led_mode` state writes.

## Conventions and gotchas

- **The MQTT boundary is non-negotiable.** Protocol logic lives on the Zigbee side; nothing in `custom_components/` should know what hex bytes a C4 command uses (composing `c4.dmx.*` ASCII command strings is the ceiling).
- **Manifest version is `"0.0.0"` on purpose.** HACS reads the version from git tags, not `manifest.json`. Don't bump it manually -- Releases section explains.
- **`PLATFORMS = [Platform.EVENT, Platform.SENSOR]` only** -- the integration deliberately doesn't register a `light` platform; it reuses Z2M's native light entity via MQTT. Don't add `light.py` to `PLATFORMS`.
- **Per-device persistent state lives in `.storage/control4_dimmers_devices.<entry_id>`.** Schema changes require bumping `STORAGE_VERSION` in `const.py` and writing a migration in `store.py`.
- **Pre-commit runs pytest with a 15s/test timeout.** Long-running tests should be marked `@pytest.mark.slow` or split.
- **`scripts/led-*.json`** are hand-crafted protocol reverse-engineering artifacts, kept as the lab notebook behind RESEARCH.md. Not test data; don't run in CI.

## Existing docs

- `ARCHITECTURE.md` -- system data flow, protocol reference, entity model, button-numbering scheme. Read this before changing the manager or any entity platform.
- `PLAN.md` -- roadmap + completion status. Update when shipping a phase.
- `RESEARCH.md` -- narrative of how the protocol was decoded from the 2014 SmartThings thread, 2013 HC-800 debug log, and FCC filings. Useful for understanding why constants are what they are.
- `CONTRIBUTING.md` -- standard fork/PR contribution flow.
- `README.md` -- user-facing install (HACS + manual), setup walkthrough, troubleshooting.

## Releases

Tags use **CalVer**: `v<YYYY>.<M>.<DD>` (e.g. `v2026.5.13`). Release titles use `Control4 Dimmers v<YYYY>.<M>.<DD>`. Matches the fleet-wide HA-integration convention (triad-ams set the canonical shape).

The release workflow (`.github/workflows/release.yml`) auto-creates the GitHub release on `v*` tag push. HACS reads the version from the git tag, not `manifest.json` -- so do not bump `manifest.json`'s `"0.0.0"`.

Build the GitHub release body in three parts:

1. **Lead paragraph** (no header): 1–3 sentences of plain-English summary of what this release means for users.
2. **`## What's Changed`**: bullet list of non-dependabot merged PRs since the previous tag, one per line: `* <commit subject> by @<author> in <PR url>`. Skip dependabot PRs.
3. **`N dependabot updates:`** (rollup at the bottom): one line per dependency: `* <package>: <oldest version in window> → <newest version>`. Collapse all bumps for the same dep into one line.

End with `**Full Changelog**: <compare link>` (GitHub auto-generates).

Reference example (sister project): https://github.com/bharat/homeassistant-lockly/releases/tag/v1.0.4

## What NOT to touch

- `manifest.json`'s `"version"` field -- sentinel. HACS uses git tags.
- `scripts/led-*.json` -- LED probe payloads. Hand-crafted manual-debugging artifacts, not test data.
- The frozen MQTT contract names (above) -- the Zigbee-side fork and every deployed automation depend on them.
- `.cursor/rules/` -- IDE rules; harmless but not part of the runtime.
