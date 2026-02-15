# Control4 Zigbee Integration for Home Assistant

Migrate Control4 Zigbee dimmers and keypads to Home Assistant via Zigbee2MQTT — without replacing hardware.

Full on/off, dimming, LED color control, and keypad button events — matching original Control4 behavior.

## How It Works

Control4 dimmers are **standard Zigbee HA devices underneath the proprietary layer**. Endpoint 1 speaks standard `genOnOff` and `genLevelCtrl` for dimming. LED control and button events use a proprietary text-based protocol on profile `0xC25C`.

This project provides:

1. **Z2M External Converter** (`z2m/converters/control4.mjs`) — Device definitions for all newer C4 in-wall devices
2. **Patched zigbee-herdsman** — Whitelists C4 profile `0xC25C` in the EZSP adapter so button events and LED responses aren't dropped
3. **Custom Docker Image** — Drop-in replacement for stock Z2M, bundling the converter and herdsman patch
4. **Test Suite** — 104 tests covering color math, protocol formatting, response parsing, and device detection

## Supported Devices

| Model | Type | On/Off + Dimming | LED Control | Button Events |
|-------|------|:---:|:---:|:---:|
| C4-APD120 | Adaptive Phase Dimmer | **Confirmed** | **Confirmed** | **Confirmed** |
| C4-KD120 | Keypad Dimmer | **Confirmed** | **Confirmed** | **Confirmed** |
| C4-KC120277 | Configurable Keypad | N/A (no load) | **Confirmed** | **Confirmed** |

All newer Control4 Zigbee devices with manufacturer ID `43981` (`0xABCD`) are expected to work.

## Quick Start

### Option 1: Docker (Recommended)

```bash
# Build the custom Z2M image
cd z2m
docker build -t z2m-control4 .

# Copy and edit environment config
cp .env.example .env
# Edit .env with your Z2M data dir, coordinator device, etc.

# Run with docker compose
docker compose up -d
```

### Option 2: Manual Installation

1. Copy `z2m/converters/control4.mjs` and `z2m/converters/c4-protocol.mjs` to your Z2M `external_converters/` directory
2. Apply the herdsman profile patch (see `z2m/herdsman-c4-profile.patch`) or run `exploration/scripts/patch-herdsman-c4-profile.sh`
3. Restart Zigbee2MQTT

### Pair a Dimmer

1. Factory reset: Press **top 13x, bottom 4x, top 13x** (13-4-13 sequence)
2. Enable Permit Join in Z2M
3. Wait for the device to pair
4. Run device detection: `mosquitto_pub -t zigbee2mqtt/DEVICE_NAME/set -m '{"c4_detect": true}'`

See `exploration/README.md` for the full step-by-step migration guide.

## Project Structure

```
├── z2m/                          # Z2M converter, Docker, and tests
│   ├── converters/
│   │   ├── control4.mjs          # Main Z2M converter (the core artifact)
│   │   └── c4-protocol.mjs       # Pure protocol logic (testable, no Z2M deps)
│   ├── tests/
│   │   └── c4-protocol.test.mjs  # 104 tests for the protocol module
│   ├── Dockerfile                # Custom Z2M image with C4 support
│   ├── docker-compose.yml        # Docker Compose for local dev
│   ├── Makefile                  # build/test/deploy/push targets
│   └── herdsman-c4-profile.patch # Source-level patch for zigbee-herdsman
├── exploration/                  # Original reverse-engineering work (imported with history)
│   ├── README.md                 # Full migration guide
│   ├── PROGRESS.md               # Session-by-session progress log
│   ├── docs/                     # Protocol reference and device identification
│   └── scripts/                  # Utility scripts (probe, database fix, etc.)
├── custom_components/            # HA custom component (future: keypad config UI)
│   └── control4_dimmers/
└── PLAN.md                       # Project roadmap
```

## Development

```bash
# Run the converter test suite
cd z2m && npm test

# Build the Docker image
cd z2m && make build

# Deploy to production server
cd z2m && make deploy DEPLOY_HOST=your-server
```

## Roadmap

See [PLAN.md](PLAN.md) for the full project arc. Current status:

- [x] **Phase 0**: Import exploration repo with full history
- [x] **Phase 1**: Clean converter + test framework (104 tests)
- [x] **Phase 2**: Herdsman C4 profile patch
- [x] **Phase 3**: Docker build pipeline + CI/CD
- [ ] **Phase 4**: Complete device support (all 3 types, telemetry, dimming tables)
- [ ] **Phase 5**: HA custom component for keypad configuration
- [ ] **Phase 6**: Keypad configuration frontend (visual editor)
- [ ] **Phase 7**: Upstream contributions (deferred)

## Credits

- **pstuart** — Original SmartThings C4 dimmer driver
- **iankberry** — Hubitat port
- **samtherecordman** — Z2M issue #160 pioneer work
- **Koenkk** — Zigbee2MQTT / zigbee-herdsman

## License

MIT
