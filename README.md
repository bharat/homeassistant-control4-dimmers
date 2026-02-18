# Control4 Dimmers for Home Assistant

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
- Set per-button LED colors with a native HA color picker (on-state and off-state independently).
- Receive keypad button press, click count, and scene change events as HA actions.
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

1. Copy `z2m/converters/control4.mjs` and `z2m/converters/c4-protocol.mjs`
   into your Zigbee2MQTT `external_converters/` directory.
2. Patch zigbee-herdsman to accept the C4 profile. Either:
   - Apply `z2m/herdsman-c4-profile.patch` to the source, or
   - Run `exploration/scripts/patch-herdsman-c4-profile.sh` against a running
     Z2M Docker container.
3. Restart Zigbee2MQTT.

### HA Custom Component (future)

The Home Assistant custom component (`custom_components/control4_dimmers`) is
scaffolded but not yet functional. It will provide a keypad button configuration
UI once device support is complete. For now, all device control flows through
Zigbee2MQTT.

## Adding a Device

1. Factory reset the device: press **top 13x, bottom 4x, top 13x**.
2. Enable Permit Join in the Zigbee2MQTT web UI.
3. Wait for the device to appear (interview may partially fail -- this is
   normal for C4 devices).
4. Detect the device type and read stored LED colors:
   ```
   mosquitto_pub -t zigbee2mqtt/DEVICE_NAME/set -m '{"c4_detect": true}'
   ```
5. Optionally fix the interview state:
   ```
   python3 exploration/scripts/fix-c4-database.py /path/to/database.db --apply
   ```

See [exploration/README.md](exploration/README.md) for a detailed guide
covering batch setup, LED color configuration, and troubleshooting.

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
3. **HA Custom Component** *(future)* -- keypad configuration UI for the
   6-slot C4 chassis.

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
# Run the converter test suite (104 tests)
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

- [x] Import exploration repo with full research and development history
- [x] Clean converter with test framework (104 tests)
- [x] Herdsman C4 profile patch
- [x] Docker build pipeline + GitHub Actions CI/CD
- [ ] Complete device support (telemetry sensors, dimming tables)
- [ ] HA custom component for keypad configuration
- [ ] Keypad configuration frontend (visual 6-slot chassis editor)

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
