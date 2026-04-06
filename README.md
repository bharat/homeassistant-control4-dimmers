# Control4 Dimmers for Home Assistant

[![Tests](https://github.com/bharat/homeassistant-control4-dimmers/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/bharat/homeassistant-control4-dimmers/actions/workflows/test.yml)
[![Validate](https://github.com/bharat/homeassistant-control4-dimmers/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/bharat/homeassistant-control4-dimmers/actions/workflows/validate.yml)
[![Lint](https://github.com/bharat/homeassistant-control4-dimmers/actions/workflows/lint.yml/badge.svg?branch=main)](https://github.com/bharat/homeassistant-control4-dimmers/actions/workflows/lint.yml)
[![Z2M Tests](https://github.com/bharat/homeassistant-control4-dimmers/actions/workflows/z2m-test.yml/badge.svg?branch=main)](https://github.com/bharat/homeassistant-control4-dimmers/actions/workflows/z2m-test.yml)

> **Background:** Control4 makes beautifully engineered Zigbee dimmers and
> keypads. This project bridges them into the Home Assistant ecosystem so
> they can participate alongside other Zigbee devices — preserving the
> hardware you already have and the features that make it great. For the
> full story of how this integration was researched and built using publicly
> available information, see **[RESEARCH.md](RESEARCH.md)**.

A Zigbee2MQTT converter that brings Control4 Zigbee dimmers and keypads into
Home Assistant with full on/off, dimming, LED color control, and keypad
button events — preserving the original Control4 experience.

## What you can do

- Control C4 dimmers and keypads through Home Assistant using standard Zigbee.
- Configure buttons with a visual chassis editor in a custom Lovelace card.
- Set per-button LED colors with a native HA color picker (on-state and off-state independently).
- Trigger automations from physical button events (`pressed`, `released`, `single_tap`, `double_tap`, `triple_tap`) exposed as HA event entities.
- Auto-detect device type (dimmer, keypad dimmer, or pure keypad) at pairing time.
- Read stored LED colors from device firmware so dimmers retain their existing C4 colors.
- Send raw C4 text protocol commands for experimentation and debugging.
- Build and deploy a custom Z2M Docker image that bundles everything you need.

## Supported Devices

| Model | Type | Dimming | LED Control | Buttons | Status |
|-------|------|:---:|:---:|:---:|--------|
| C4-APD120 | Adaptive Phase Dimmer | Yes | Yes | 2 (rocker) | **Confirmed** |
| C4-KD120 | Keypad Dimmer | Yes | Yes | 6 (rocker + keypad) | **Confirmed** |
| C4-KC120277 | Configurable Keypad | N/A | Yes | 6 (configurable slots) | **Confirmed** |

All newer Control4 Zigbee devices with manufacturer ID `43981` (`0xABCD`) are
expected to work. To move a device to a new Zigbee mesh, use the **13-4-13**
factory reset sequence: press top 13x, bottom 4x, top 13x.

## Prerequisites

- Home Assistant with [Zigbee2MQTT](https://www.zigbee2mqtt.io/) installed
- A supported Zigbee coordinator (SONOFF ZBDongle-E, SLZB-06, or any
  EFR32/CC2652-based stick)
- Physical access to each Control4 device for the factory reset sequence

## Installation

### Docker (recommended)

The easiest path is a custom Z2M Docker image that bundles the converter and
the required zigbee-herdsman patch.

```bash
cd z2m
cp .env.example .env     # edit with your Z2M data dir, coordinator, etc.
docker build -t z2m-control4 .
docker compose up -d
```

The resulting image is a drop-in replacement for the stock
`koenkk/zigbee2mqtt` image.

### Manual (without Docker)

1. Copy `z2m/converters/control4.mjs` into your Zigbee2MQTT
   `external_converters/` directory. This is a single self-contained file
   (the protocol logic is bundled in). Do **not** copy any other `.mjs`
   files — Z2M auto-loads every `.mjs` in that directory as a converter
   and will reject helper modules.
2. Patch zigbee-herdsman to accept the C4 profile:
   apply `z2m/herdsman-c4-profile.patch` to the source.
3. Restart Zigbee2MQTT.

### HA Custom Component

Install the `custom_components/control4_dimmers` directory into your Home
Assistant `custom_components/` folder and restart. The integration auto-discovers
Control4 devices from Zigbee2MQTT and provides:

- **Lovelace card** with an entities-card-style header, per-button LED
  indicators, and click-to-open light control.
- **Visual chassis editor** for naming buttons, choosing LED modes/colors,
  and configuring button sizes.
- **Event entities** for each button slot, with automation links shown
  directly in the editor.
- **Light entities** for dimmers with brightness control via the native HA
  more-info dialog.

## Migrating a Device from Control4

This guide walks through moving a C4 device from an existing Control4
system onto your Zigbee2MQTT mesh. The device retains its LED colors
from the C4 Director — no manual reconfiguration needed.

### Step 1: Factory reset the device

Press **top 4x** on the device. (The 13-4-13 sequence is an alternative
full reset.) The LEDs will flash to confirm.

### Step 2: Pair with Zigbee2MQTT

1. Open the Z2M web UI and enable **Permit Join** (Settings → Permit Join).
2. The device should appear within 30 seconds.
3. **The interview will fail** — this is normal and expected. C4 devices
   don't support standard Zigbee genBasic reads, so Z2M can't read
   model/manufacturer via the usual interview. The device will show
   **Interview state: failed** with a warning icon — this does not
   affect functionality. The converter handles everything the interview
   can't.
4. Despite the failed interview, Z2M will show the device as
   **Supported: external** with model **C4-Zigbee**.

### Step 3: Auto-detection (automatic)

Device type detection happens automatically — both at pairing time
(via Z2M's `configure` step) and when the HA integration starts (for
any device without a detected type). No manual action is needed.

You can verify detection in the Z2M logs:

```
[C4 DETECT] Device type: dimmer        (C4-APD120, 2 buttons + load)
[C4 DETECT] Device type: keypaddim     (C4-KD120, 6 buttons + load)
[C4 DETECT] Device type: keypad        (C4-KC120277, 6 buttons, no load)
```

If detection didn't work (rare — e.g., EP 197 wasn't registered on
the very first pairing), you can trigger it manually:

```bash
mosquitto_pub -t 'zigbee2mqtt/DEVICE_NAME/set' -m '{"c4_detect": true}'
```

Detection also reads stored LED colors from the device firmware.

### Step 4: Rename in Z2M (do this BEFORE reloading HA)

Give the device a friendly name in Z2M (About tab → click the edit
icon next to the IEEE address). For example: "Kitchen", "Kitchen
Keypad". **Do this before reloading the HA integration** — HA entity
IDs are based on the name at creation time, so naming first avoids
entities with hex addresses.

The integration uses Z2M's light entity (MQTT+ approach), so the
device's Z2M name becomes the light entity name (e.g., `light.kitchen`).

### Step 5: Reload the HA integration

Go to **Settings → Devices & Services → Control4 Dimmers → ⋮ → Reload**.
This ensures the integration picks up the new device with its friendly
name. The device should now appear in the Control4 Dimmers card editor
dropdown.

> **Note:** Pairing the first device may trigger discovery, but
> subsequent devices may require an explicit reload. When in doubt,
> reload after renaming each new device.

### Step 6: Configure in the card editor

There are two card types:

**Control4 Dimmers** — a single-device card for individual control.
Add it to your dashboard, select the device from the dropdown, and
configure each button.

**Control4 Dimmer Grid** — an all-in-one card that auto-discovers
every C4 device and shows them in a grid. Add the card with
`type: custom:control4-dimmer-grid`. The editor has numbered tabs
to flip through each device. Configure grid-level title, column
count, and faceplate color, or override colors per device.

#### Button configuration

Each button has a **Mode** (for dimmers/keypad-dimmers):

- **Load Control** — firmware handles the physical button press
  directly (fast, no software round-trip). Choose Turn On, Turn Off,
  or Toggle. LED automatically follows the load state.
- **Programmable** — button fires an HA event entity. You can
  configure actions for Tap, Double Tap, and Hold using HA's
  `ha-service-picker` (any HA service + target entity).

#### LED modes (for Programmable buttons)

- **Fixed** — static LED color (single color picker).
- **Programmed** — LED tracks an entity's on/off state. Requires
  a tracking entity. Shows On and Off color pickers.
- **Push/Release** — LED lights while button is pressed. Shows
  Pushed and Released color pickers.

#### Faceplate color

Choose from official Control4 faceplate colors (Aluminum, Biscuit,
Black, Brown, Ivory, Light Almond, Midnight Black, White). The card
renders the chassis and buttons in the selected color with
appropriate contrast.

### What to expect per device type

| Type | Buttons | Load | Default config |
|------|---------|------|-------|
| **Dimmer** (C4-APD120) | 2 (rocker) | Yes | Top = Load On, Bottom = Load Off |
| **Keypad Dimmer** (C4-KD120) | 6 (rocker + 4 keypad) | Yes | Button 1 = Toggle Load, 2-6 = Programmable |
| **Keypad** (C4-KC120277) | 6 (configurable) | No | All Programmable |

### Troubleshooting

- **Device shows as "Unsupported"**: The external converter isn't loaded.
  Verify `control4.mjs` is in Z2M's `external_converters/` directory and
  restart Z2M. Only copy `control4.mjs` — do not copy other `.mjs` files.

- **Device not appearing in HA card**: The HA integration discovers
  devices from `zigbee2mqtt/bridge/devices`. Verify the device shows
  as "Supported: external" in Z2M, then reload the integration.

- **Detection shows "unknown"**: Run `{"c4_detect": true}` manually.
  On first pairing, the coordinator's EP 197 may not be registered yet —
  restart Z2M once and try again.

- **LED colors not changing**: Make sure you clicked **Save** in the card
  editor. Colors are pushed to the device firmware via `c4.dmx.led`
  commands on save — they persist across power cycles.

- **Buttons don't control the light**: Verify the button's mode is
  set to **Load Control** (not Programmable). The integration uses
  the Z2M light entity for load control.

## How it works

Control4 dimmers have a thoughtful two-layer architecture. Endpoint 1 speaks
standard Zigbee HA — `genOnOff` (cluster `0x0006`) and `genLevelCtrl`
(cluster `0x0008`) — for basic on/off and dimming. The advanced features that
make C4 hardware special (LED colors, button events, device identification)
use a text-based protocol on Zigbee profile `0xC25C` with raw ASCII payloads.

This project has three layers:

1. **Z2M External Converter** -- translates between the C4 text protocol and
   Zigbee2MQTT entities (lights, actions, selects).
2. **zigbee-herdsman Patch** -- adds profile `0xC25C` to the EZSP adapter's
   incoming message whitelist so C4 responses and button events aren't silently
   dropped.
3. **HA Custom Component** -- Lovelace card with a visual chassis editor,
   event entities for button automations, and light entities for dimmers.

### Runtime device detection

All newer C4 devices share identical endpoint structures (1, 196, 197) and
the same manufacturer ID. The converter uses a single
`c4.dmx.dim` query to differentiate:

| Response | Device | Type |
|----------|--------|------|
| `01` | C4-APD120 | Forward-phase dimmer (2 buttons) |
| `02` | C4-KD120 | Reverse-phase keypad dimmer (6 buttons + load) |
| error/timeout | C4-KC120277 | Pure keypad (6 buttons, no load) |

## Development

```bash
# Run the HA integration dev environment (MQTT broker + simulator + HA)
scripts/develop

# Run Python tests for the custom component
python -m pytest tests/

# Run the Z2M converter test suite (104 tests)
cd z2m && npm test

# Build the Docker image
cd z2m && make build

# Deploy to production server via SSH
cd z2m && make deploy DEPLOY_HOST=your-server

# Push image to GitHub Container Registry
cd z2m && make push
```

## Roadmap

See [PLAN.md](PLAN.md) for the full project arc.

- [x] Clean converter with test framework (104 tests)
- [x] Herdsman C4 profile patch
- [x] Docker build pipeline + GitHub Actions CI/CD
- [x] HA custom component with event entities and light entities
- [x] Keypad configuration frontend (visual 6-slot chassis editor)
- [ ] Complete device support (telemetry sensors, dimming tables)

## Credits

- **pstuart** -- Original SmartThings C4 driver that proved standard
  Zigbee commands work (2014)
- **ArcadeMachinist** -- Decoded the C4 keypad protocol and identified the
  EZSP adapter requirement (2022)
- **iankberry** -- Hubitat port confirming continued compatibility
- **samtherecordman** -- Z2M issue #160 pioneer work with
  `disableDefaultResponse` discovery
- **Koenkk** -- Zigbee2MQTT / zigbee-herdsman

## License

MIT
