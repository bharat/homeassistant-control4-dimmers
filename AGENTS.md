# AGENTS.md — Control4 Dimmers HA Integration

This is the canonical agent guide for `bharat/homeassistant-control4-dimmers`. New Claude/Codex/Cursor sessions should read this before making changes. Pair it with `ARCHITECTURE.md`, `PLAN.md`, and `RESEARCH.md` for deeper context.

## What this is

A Home Assistant custom integration for **Control4 Zigbee dimmers / keypads** (C4-APD120 phase dimmer, C4-KD120 keypad-dimmer, C4-KC120277 configurable keypad). It exists because Control4's hardware speaks two Zigbee profiles on endpoint 1 — the standard HA profile (`0x0104`, clusters `genOnOff` / `genLevelCtrl`) for on/off + dimming, and a proprietary text profile (`0xC25C`, manufacturer ID `0xABCD`) for LED color, button events, and device identification. To handle both, the project bundles three independent layers:

1. **Z2M external converter** (`z2m/converters/control4.mjs`) — parses the C4 text protocol, exposes color/event entities to MQTT.
2. **`zigbee-herdsman` patch** (`z2m/herdsman-c4-profile.patch`, applied at Docker build time) — whitelists profile `0xC25C` in the EZSP adapter so button events aren't silently dropped by EFR32/CC2652 coordinators.
3. **HA custom component** (`custom_components/control4_dimmers/`) — discovers devices from `zigbee2mqtt/bridge/devices`, creates sensor / event / light entities, and ships a custom Lovelace card (`control4-dimmer-card`) for the visual 6-slot configuration UI.

## Layout

```
.
├── ARCHITECTURE.md             # System-level data flow + protocol reference (read first)
├── PLAN.md                     # Roadmap (7 phases) + completion status
├── RESEARCH.md                 # How the C4 protocol was reverse-engineered (narrative)
├── CONTRIBUTING.md             # Standard fork/PR flow
├── README.md                   # User-facing install + setup
│
├── custom_components/control4_dimmers/
│   ├── __init__.py             # async_setup_entry, services, WebSocket API
│   ├── manifest.json           # version is "0.0.0" sentinel — see Releases section
│   ├── config_flow.py          # Single-step user flow (asks for MQTT base topic)
│   ├── const.py                # DOMAIN, DEVICE_TYPES, DEVICE_TYPE_SLOTS, BUTTON_EVENT_TYPES
│   ├── manager.py              # Brain: MQTT subs, device discovery, state, service routing
│   ├── models.py               # SlotConfig / DeviceConfig / DeviceState dataclasses
│   ├── store.py                # Persistent JSON in .storage/control4_dimmers_devices.<entry_id>
│   ├── sensor.py               # Device anchor + ambient light (lux) sensors
│   ├── light.py                # Brightness 0-255 (proxies Z2M's standard light entity)
│   ├── event.py                # One Control4ButtonEvent entity per button slot
│   ├── frontend/
│   │   ├── __init__.py         # Serves the Lovelace card as a JS module
│   │   └── control4-dimmer-card.js  # LitElement; dashboard + chassis editor (~1500 LOC)
│   └── services.yaml           # set_led, press_button, set_device_type
│
├── z2m/
│   ├── converters/control4.mjs # Self-contained converter: protocol + Z2M glue
│   ├── tests/                  # 104 vitest tests of the pure-protocol functions
│   ├── Dockerfile              # FROM koenkk/zigbee2mqtt:latest + converter + herdsman patch
│   ├── docker-compose.yml      # Drop-in replacement for stock Z2M
│   ├── Makefile                # build / build-amd64 / push (multi-arch) / deploy / test
│   ├── herdsman-c4-profile.patch  # Source patch (applied via sed at image build time)
│   └── package.json            # vitest scripts
│
├── tests/                      # HA component tests (pytest + pytest-homeassistant-custom-component)
│   ├── conftest.py             # mock_hass / mock_entry / mock_store / mock_manager fixtures
│   └── test_*.py               # 7 test modules
│
├── scripts/
│   ├── setup                   # Container post-create: pip + pre-commit + claude CLI + act
│   ├── develop                 # Mosquitto + HA + (optional) device simulator via concurrently
│   ├── lint                    # ruff check --fix && ruff format --check
│   ├── simulate_devices.py     # Fake Z2M devices for ./scripts/develop --sim
│   ├── *.json                  # 14 LED probe payloads (manual debugging — NOT used at runtime)
│   └── c4mqtt                  # Stray binary, excluded from ruff (purpose unknown — investigate before touching)
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
cd z2m && npm test                                  # Z2M converter tests (104 vitest)
cd z2m && npm run test:watch

# Lint
./scripts/lint                                      # ruff check --fix + format --check
pre-commit run --all-files                          # Same hooks CI runs

# Z2M Docker image (when changing converters/ or herdsman patch)
cd z2m && make build                                # Local build
cd z2m && make deploy DEPLOY_HOST=<server>          # SSH-based prod deploy
cd z2m && make push                                 # Push multi-arch to ghcr.io
```

## Conventions and gotchas

- **Three-layer architecture is non-negotiable.** Protocol logic stays in `z2m/converters/control4.mjs`; nothing in `custom_components/` should know what hex bytes a C4 command uses. Likewise, HA-specific entity logic stays in the component; the converter must remain testable in isolation (the 104 vitest tests rely on this).
- **Manifest version is `"0.0.0"` on purpose.** HACS reads the version from git tags, not `manifest.json`. Don't bump it manually — Releases section explains.
- **`PLATFORMS = [Platform.EVENT, Platform.SENSOR]` only** — the integration deliberately doesn't register a `light` platform; it reuses Z2M's native light entity via MQTT. Don't add `light.py` to `PLATFORMS`.
- **LED color uses gamma-corrected RGB.** C4 LEDs have a non-linear response curve; pure colors only use `0x00` / `0xFF` byte values. `C4_LED_GAMMA = 2.0` in the converter — don't strip it "to simplify."
- **Profile `0xC25C` is whitelisted via `sed` against compiled JS** in the Z2M Dockerfile. When upstream `zigbee-herdsman` adds proper extensibility (or merges the patch), swap the `sed` for a normal `npm install`. Don't `npm install` the patched fork at build time today — the Docker layer cache won't catch it.
- **`scripts/c4mqtt` is a stray binary** (excluded from ruff). Purpose is unclear — don't delete or invoke it without investigating. Ask bharat first.
- **Per-device persistent state lives in `.storage/control4_dimmers_devices.<entry_id>`.** Schema changes require bumping `STORAGE_VERSION` in `const.py` and writing a migration in `store.py`.
- **Pre-commit runs pytest with a 15s/test timeout.** Long-running tests should be marked `@pytest.mark.slow` or split.

## Existing docs

- `ARCHITECTURE.md` — system data flow, protocol layer split, entity model, button-numbering scheme. Read this before changing the manager or any entity platform.
- `PLAN.md` — roadmap divided into 7 phases (converter / herdsman patch / Docker / device support / HA component / frontend / upstream). Update when shipping a phase.
- `RESEARCH.md` — narrative of how the protocol was decoded from the 2014 SmartThings thread, 2013 HC-800 debug log, and FCC filings. Useful for understanding why constants are what they are.
- `CONTRIBUTING.md` — standard fork/PR contribution flow.
- `README.md` — user-facing install (HACS + manual), setup walkthrough, troubleshooting.

## Releases

Tags use SemVer: `v<MAJOR>.<MINOR>.<PATCH>` (no releases cut yet — first one establishes the convention). Release titles use `Control4 Dimmers v<version>` to match the convention used by sister fleet projects (`Lockly v…`, `Triad AMS v…`).

The release workflow (`.github/workflows/release.yml`) auto-creates the GitHub release on `v*` tag push. HACS reads the version from the git tag, not `manifest.json` — so do not bump `manifest.json`'s `"0.0.0"`.

Build the GitHub release body in three parts:

1. **Lead paragraph** (no header): 1–3 sentences of plain-English summary of what this release means for users.
2. **`## What's Changed`**: bullet list of non-dependabot merged PRs since the previous tag, one per line: `* <commit subject> by @<author> in <PR url>`. Skip dependabot PRs.
3. **`N dependabot updates:`** (rollup at the bottom): one line per dependency: `* <package>: <oldest version in window> → <newest version>`. Collapse all bumps for the same dep into one line.

End with `**Full Changelog**: <compare link>` (GitHub auto-generates).

Reference example (sister project): https://github.com/bharat/homeassistant-lockly/releases/tag/v1.0.4

## What NOT to touch

- `z2m/herdsman-c4-profile.patch` — the canonical source for the profile whitelist. Edits here change the Docker build behavior.
- `manifest.json`'s `"version"` field — sentinel. HACS uses git tags.
- `scripts/c4mqtt` — stray binary, purpose unknown. Don't run, delete, or move without context.
- `scripts/*.json` — LED probe payloads. They're hand-crafted manual-debugging artifacts, not test data.
- `.cursor/rules/` — IDE rules; harmless but not part of the runtime.
