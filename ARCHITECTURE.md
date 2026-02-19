# Architecture

This document describes the architecture of the Control4 Dimmers integration
for Home Assistant, covering the full data flow from Zigbee wire protocol
through to the Lovelace card.

## Design Principles

- **Hardware-agnostic naming**: Entity IDs contain no "Control4" branding.
  Users migrating off C4 hardware keep clean names.
- **1-based button numbering**: The wire protocol is 0-based hex (`00`–`05`).
  Everything above the Z2M converter boundary uses 1-based numbers (`1`–`6`).
- **Distributed attributes**: Per-button config (LED colors, behavior, LED mode)
  lives on each button's event entity, not dumped on a central sensor.
- **Sensor anchor**: A lightweight `sensor` entity is the device's primary
  representation in HA, exposing device-level metadata only.
- **Services over entities**: LED control, button simulation, and device type
  changes use HA service calls, not dedicated entities.
- **Slim Z2M**: The converter exposes only the standard dimmer light and
  an action enum. All LED/config entities are managed in HA.
- **Ambient light profiles**: Pushed to device firmware (C4's native approach).
  The device adjusts LED brightness autonomously—no HA polling loop.

## Data Flow

```
┌─────────────┐     raw ASCII      ┌──────────────┐     MQTT/JSON      ┌──────────────┐
│  C4 Device  │ ◄──────────────── │   Zigbee2MQTT │ ──────────────── │ Home Assistant │
│  (Zigbee)   │  profile 0xC25C   │  + converter  │  zigbee2mqtt/*   │  integration   │
└─────────────┘     cluster 1      └──────────────┘                   └──────────────┘
                                         │                                   │
                                   c4-protocol.mjs                     manager.py
                                   control4.mjs                        sensor.py
                                                                       light.py
                                                                       event.py
                                                                       store.py
                                                                       __init__.py
```

### Layer 1: Wire Protocol (C4 Text Protocol)

C4 devices communicate via raw ASCII over Zigbee (profile `0xC25C`, cluster 1):

| Verb | Format | Purpose |
|------|--------|---------|
| `0s` | `0s<seq> <cmd> <params>\r\n` | SET (write a value) |
| `0g` | `0g<seq> <cmd> <params>\r\n` | GET (query a value) |
| `0r` | `0r<seq> 000 <data>` | Response (success) |
| `0t` | `0t<seq> sa <cmd> <data>` | Telemetry (unsolicited event) |

Key commands:
- `c4.dmx.led <btn> <mode> [color]` — GET/SET LED color (mode `03`=on, `04`=off)
- `c4.dmx.bp <btn>` — Button press event
- `c4.dmx.br <btn>` — Button release event
- `c4.dmx.cc <btn> <count>` — Click count (tap detection)
- `c4.dmx.dim` — Query dimmer type (response identifies device model)
- `c4.dmx.amb <sensor>` — Query ambient light sensor

### Layer 2: Z2M Converter

The converter (`z2m/converters/control4.mjs`) translates between the C4 wire
protocol and Z2M's MQTT state model.

**Entities exposed by Z2M:**

| Entity | Purpose |
|--------|---------|
| Standard dimmer light | `genOnOff` + `genLevelCtrl` on EP1 (dimmers only) |
| `action` enum | Button events: `button_1_press`, `button_1_click_1`, etc. |

**State attributes** (published on the device's MQTT topic):
- `c4_device_type`: detected device type (`dimmer`, `keypaddim`, `keypad`)
- `c4_led_N_on`, `c4_led_N_off`: hex RGB color strings per button
- `c4_response`, `c4_response_ep`: raw protocol responses for debugging

**Button numbering boundary**: The converter maps wire hex `00`–`05` to
1-based indices `1`–`6`. This is the only place in the stack where 0-based
wire IDs appear.

### Layer 3: HA Integration (Python)

The integration (`custom_components/control4_dimmers/`) subscribes to Z2M
MQTT topics and manages HA entities.

**Key components:**

| Module | Responsibility |
|--------|---------------|
| `manager.py` | MQTT subscription, device discovery, state tracking, event dispatch |
| `store.py` | Persistent storage (`.storage/`) for device configs |
| `models.py` | Data models: `DeviceConfig`, `DeviceState`, `SlotConfig` |
| `sensor.py` | Sensor anchor entity (device representation) |
| `light.py` | Dimmer load entity |
| `event.py` | Button event entities (with LED config as attributes) |
| `__init__.py` | Platform setup, websocket API, service registration |

### Layer 4: Lovelace Card (JavaScript)

The card (`frontend/control4-dimmer-card.js`) binds to the sensor anchor entity,
discovers sibling entities via the HA device registry, and reads button
configuration from each event entity's attributes.

## Entity Model

### Naming Convention

Entity IDs are derived from the Z2M friendly name. Users should include the
device type in their Z2M friendly name (e.g., "Kitchen Dimmer", "Theater Keypad").
All entities use `has_entity_name = True`.

### Dimmer (e.g., Z2M name "Kitchen Dimmer")

```
HA Device: "Kitchen Dimmer"
│
├── sensor.kitchen_dimmer                 # anchor (primary entity)
│     state: "connected" | "disconnected"
│     attributes:
│       device_type: "dimmer"
│       detected_type: "dimmer"
│       model_id: "C4-APD120"
│       ieee_address: "0x000fff0000aaa001"
│
├── light.kitchen_dimmer_load             # physical dimmer load
│     state: on/off
│     brightness: 0–255
│
├── event.kitchen_dimmer_button_2         # button (Top, wire 01)
│     event_type: pressed | released | single_tap | double_tap | triple_tap
│     attributes:
│       on_color: "#ffffff"
│       off_color: "#000000"
│       behavior: "load_on"
│       led_mode: "follow_load"
│
├── event.kitchen_dimmer_button_5         # button (Bottom, wire 04)
│     attributes: (same structure)
│
└── sensor.kitchen_dimmer_ambient_light   # ambient light sensor
      state: lux value
```

### Keypad (e.g., Z2M name "Theater Keypad")

```
HA Device: "Theater Keypad"
│
├── sensor.theater_keypad                 # anchor
│     state: "connected"
│     attributes: device_type, model_id, ieee_address
│
├── event.theater_keypad_button_1         # buttons 1–6
├── event.theater_keypad_button_2
├── event.theater_keypad_button_3
├── event.theater_keypad_button_4
├── event.theater_keypad_button_5
├── event.theater_keypad_button_6
│     attributes: on_color, off_color, behavior, led_mode
│
└── sensor.theater_keypad_ambient_light
```

### Why This Structure

- **Each entity = one concern**: Button config lives on the button's event entity,
  not on a central dump.
- **Templates read naturally**:
  `{{ state_attr('event.kitchen_dimmer_button_1', 'on_color') }}`
- **Services target the right entity**:
  `service: control4_dimmers.set_led` with `entity_id: event.kitchen_dimmer_button_1`
- **No entity overload**: The sensor anchor carries only device-level metadata
  (4 attributes), not 30+ button attributes.

## Services

### `control4_dimmers.set_led`

Set an LED color on a specific button.

```yaml
service: control4_dimmers.set_led
data:
  entity_id: event.kitchen_dimmer_button_1
  mode: "on"       # "on" or "off"
  color: "#ff0000"  # hex RGB
```

Sends MQTT command, updates persistent store, updates entity attributes.

### `control4_dimmers.press_button`

Simulate a button press via MQTT.

```yaml
service: control4_dimmers.press_button
data:
  entity_id: event.kitchen_dimmer_button_1
```

### `control4_dimmers.set_device_type`

Override the auto-detected device type.

```yaml
service: control4_dimmers.set_device_type
data:
  entity_id: sensor.kitchen_dimmer
  device_type: "keypaddim"
```

## Button Numbering

| Wire (hex) | Z2M / HA | Physical Position |
|-----------|----------|-------------------|
| `00` | 1 | Top-left (keypads) |
| `01` | 2 | Top rocker (dimmers) / Second button |
| `02` | 3 | Third button |
| `03` | 4 | Fourth button |
| `04` | 5 | Bottom rocker (dimmers) / Fifth button |
| `05` | 6 | Sixth button |

The translation from 0-based wire IDs to 1-based happens once, in the Z2M
converter's `parseButtonEvent()` function: `wireId = parseInt(hex, 16) + 1`.

## Entity Count

| Device Type | Z2M Entities | HA Entities | Total |
|-------------|-------------|-------------|-------|
| 6-button keypad dimmer | 2 (light + action) | 9 (sensor + light + 6 events + ambient) | 11 |
| 6-button keypad | 1 (action) | 8 (sensor + 6 events + ambient) | 9 |

Down from 52 and 44 respectively in the pre-redesign architecture.

## Persistent Storage

Device configuration is stored in `.storage/control4_dimmers_devices.<entry_id>`:

```json
{
  "devices": {
    "0x000fff0000aaa001": {
      "ieee_address": "0x000fff0000aaa001",
      "friendly_name": "Kitchen Dimmer",
      "device_type": "dimmer",
      "device_type_override": null,
      "slots": [
        {
          "slot_id": 2,
          "name": "Top",
          "behavior": "load_on",
          "led_mode": "follow_load",
          "led_on_color": "ffffff",
          "led_off_color": "000000"
        }
      ]
    }
  }
}
```

## Ambient Light

C4 devices have built-in ambient light sensors. The Control4 Director pushes a
brightness profile to the device firmware, and the device then autonomously
adjusts LED brightness based on its own sensor reading—no polling required.

Sensing modes:
- **Independent**: Device uses its own ambient sensor
- **Source**: Device shares its sensor reading with followers
- **Follower**: Device uses another device's sensor reading

The `sensor.{name}_ambient_light` entity reads the current lux value on demand.
It is currently created for all devices unconditionally; devices without ambient
light hardware will show "unknown". A future improvement could defer creation
until an actual lux reading is received.

The `control4_dimmers.set_ambient_profile` service (future) will push brightness
profiles to the device firmware.
