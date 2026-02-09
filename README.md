# Control4 Zigbee Dimmer Migration to Home Assistant / Zigbee2MQTT

Migrate ~30 Control4 Zigbee dimmers to Zigbee2MQTT without replacing hardware.
Full on/off, dimming, and **LED color control** — matching the original Control4 behavior.

## TL;DR

Control4 dimmers are **standard Zigbee HA devices underneath the proprietary layer**.
Endpoint 1 speaks standard `genOnOff` (cluster 0x0006) and `genLevelCtrl`
(cluster 0x0008). On/off and dimming work with any Zigbee coordinator.

LED color control uses a **proprietary text-based protocol** (profile 0xC25C,
cluster 0x0001) that sends raw ASCII commands as the APS payload — no ZCL
framing. This project includes the reverse-engineered protocol and a working
converter.

This has been proven working on SmartThings and Hubitat with custom drivers.
This project provides the equivalent for Zigbee2MQTT, including LED control
that those platforms never fully implemented.

---

## Prerequisites

- Home Assistant with Zigbee2MQTT installed and running
- A supported Zigbee coordinator (recommended: SONOFF ZBDongle-E or -P,
  SLZB-06, or any EFR32/CC2652-based stick)
- Physical access to every Control4 dimmer (for button press reset)
- Your Control4 system still running (so dimmers have power)

## Architecture

```
                    ┌──────────────────────────────┐
                    │   Control4 Dimmer (in wall)   │
                    │                               │
                    │  EP 1  (0x0104 Zigbee HA)     │ ◄── Standard: On/Off, Dimming
                    │  EP 1  (0xC25C C4 MIB)        │ ◄── Proprietary: LED control
                    │  EP C5 (0xC25C Events)         │ ◄── Incoming: responses & status
                    └──────────┬────────────────────┘
                               │ Zigbee 2.4GHz
                               ▼
                    ┌──────────────────────────────┐
                    │   Zigbee Coordinator          │
                    │   (SONOFF ZBDongle-E, etc.)   │
                    └──────────┬────────────────────┘
                               │ Serial/USB
                               ▼
                    ┌──────────────────────────────┐
                    │   Zigbee2MQTT                 │
                    │   + External Converter         │
                    └──────────┬────────────────────┘
                               │ MQTT
                               ▼
                    ┌──────────────────────────────┐
                    │   Home Assistant              │
                    └──────────────────────────────┘
```

## Known Compatible Models

| Model | Type | On/Off + Dimming | LED Control | Notes |
|-------|------|:---:|:---:|-------|
| C4-APD120 | Adaptive phase dimmer 120V | **Confirmed** | **Confirmed** | Primary test device |
| C4-DIM / LDZ-102-x | In-wall dimmer | Confirmed (ST/HE) | Expected | Same protocol |
| C4-KD120 | Keypad dimmer 120V | Expected | Expected | Untested with Z2M |
| C4-KD277 | Keypad dimmer 277V | Expected | Expected | Untested with Z2M |
| C4-FPD120 | Forward phase dimmer 120V | Expected | Expected | |
| C4-SW | In-wall switch (on/off only) | Expected | Expected | Simpler (no dimming) |

> **Note**: All Control4 Zigbee devices share manufacturer ID 43981 (0xABCD).
> The converter matches on this ID, so it should work with any C4 dimmer model.

---

## What Works Today

- **On/Off** — standard Zigbee HA, rock-solid
- **Dimming** — standard Zigbee HA, smooth transitions
- **LED Colors** — proprietary C4 protocol, fully decoded:
  - Set top/bottom LED colors independently
  - Separate colors for ON state and OFF state
  - Batch mode to set all 4 LED states at once
  - Raw command interface for experimentation
- **LED Color Pickers in Home Assistant** — each LED state is exposed as a
  native HA light entity with a color wheel (see [HA Integration Architecture](#ha-integration-architecture))
- **Clean Z2M dashboard** — custom icon, no interview warnings

---

## Step-by-Step Migration

### Phase 1: Preparation

1. **Document your current setup**: Note which dimmer is in which room/location.
   Map IEEE addresses if possible (visible in Composer Pro).

2. **Set Zigbee2MQTT channel**: If possible, configure Zigbee2MQTT to use the
   same channel your Control4 system uses. This avoids interference during
   migration. Check your C4 channel in Composer Pro > Zigbee Configuration.

3. **Install the external converter**:
   Copy `external_converters/control4-dimmer.mjs` to your Zigbee2MQTT
   `external_converters` directory:

   ```bash
   # Docker example
   cp external_converters/control4-dimmer.mjs /opt/zigbee2mqtt/data/external_converters/

   # Home Assistant OS addon
   cp external_converters/control4-dimmer.mjs /config/zigbee2mqtt/external_converters/

   # Or use the Z2M web UI: Settings > Dev console > External converters
   ```

4. **Restart Zigbee2MQTT** to load the converter.

### Phase 2: Reset and Pair (Per Dimmer)

Do this ONE dimmer at a time until you've confirmed everything works,
then batch the rest.

#### Step 1: Factory Reset the Dimmer

Perform the **13-4-13 sequence** to leave the C4 mesh AND factory reset:

```
Press TOP button    13 times (pause briefly)
Press BOTTOM button  4 times (pause briefly)
Press TOP button    13 times
```

The dimmer LED will blink to confirm. The device has now left the Control4
network and is in factory default state, ready to join a new network.

> **Alternative**: The 9-9-9 sequence (9 top, 9 bottom, 9 top) resets settings
> but may not leave the mesh cleanly. Use 13-4-13 for a complete departure.
>
> **For keypads**: Use the top-left button instead of top, bottom-left instead
> of bottom. For 6-button keypads, same but with top-left/bottom-left buttons.

#### Step 2: Enable Zigbee2MQTT Pairing

In the Zigbee2MQTT web UI (or via MQTT), enable "Permit Join".

#### Step 3: Put Dimmer in Pairing Mode

After the factory reset, the dimmer should automatically be searching for
a network. If not, try:
- Press the top button 4 times quickly (identify/announce sequence)
- Or power cycle the dimmer at the breaker

#### Step 4: Wait for Interview

The dimmer will appear in Zigbee2MQTT. The interview may partially fail
(this is expected due to the empty modelId and endpoint 196/197 issues).

You should see log messages like:
```
Device '0x000fff00XXXXXXXX' joined
Starting interview of '0x000fff00XXXXXXXX'
...
Successfully interviewed '0x000fff00XXXXXXXX'  (or interview may fail)
Device with Zigbee model '' is NOT supported
```

**This is normal.** The external converter should still match the device
via its fingerprint (profile 0x0104, deviceId 0x0101, clusters).

#### Step 5: Verify and Fix Database (if needed)

If the device shows a red warning triangle or as unsupported, you may need
to manually patch the database. Stop Zigbee2MQTT and edit `database.db`:

Find the line with your device's IEEE address and ensure:
```json
{
  "ieeeAddr": "0x000fff00XXXXXXXX",
  "modelID": "",
  "manufacturerName": "Control4",
  "interviewCompleted": true,
  "type": "Router",
  "endpoints": {
    "1": {
      "profId": 260,
      "epId": 1,
      "devId": 257,
      "inClusterList": [0, 3, 4, 5, 6, 8, 10],
      "outClusterList": []
    }
  }
}
```

Restart Zigbee2MQTT. The device should now be recognized by the converter.

#### Step 6: Test Commands

In the Zigbee2MQTT web UI, select the device and try:

- **On**: Set state to ON
- **Off**: Set state to OFF
- **Brightness**: Set brightness slider to ~50%

#### Step 7: Set LED Colors

Set the standard C4 dimmer look (white top when on, blue bottom when off):

```bash
mosquitto_pub -t zigbee2mqtt/DEVICE_NAME/set -m \
  '{"c4_led": {"top_on": "ffffff", "top_off": "000000", "bottom_on": "000000", "bottom_off": "0000ff"}}'
```

### Phase 3: Batch Migration

Once one dimmer works:

1. Reset dimmers in groups of 3-5 (keep some lights working!)
2. Enable Permit Join on Zigbee2MQTT
3. Factory reset each dimmer (13-4-13)
4. Wait for all to pair
5. Patch database.db if needed
6. Set LED colors for each dimmer
7. Test each one
8. Rename devices in Z2M to match your room names

### Phase 4: Home Assistant Integration

With `homeassistant: true` in your Zigbee2MQTT config, each dimmer
automatically appears in Home Assistant as **5 entities**:

- `light.<name>` — main dimmer (on/off + brightness)
- `light.<name>_top_led_on` — top LED color when load is ON
- `light.<name>_top_led_off` — top LED color when load is OFF
- `light.<name>_bottom_led_on` — bottom LED color when load is ON
- `light.<name>_bottom_led_off` — bottom LED color when load is OFF

Each LED entity has a native HA color picker. Set LED colors using
the color wheel, or use automations. See [HA Integration Architecture](#ha-integration-architecture)
for details.

Migrate your automations from Control4 to HA.

### Phase 5: Decommission Control4

Once all dimmers are migrated and stable:

1. Remove the Control4 director from your network
2. Optionally remove any C4 Zigbee range extenders (your Z2M mesh will
   need its own routing - the C4 dimmers themselves act as Zigbee routers)
3. Monitor your Zigbee mesh map in Z2M for healthy routing

---

## LED Control Reference

### MQTT Commands

**Set all 4 LED states at once** (recommended — matches C4 Director behavior):
```json
{"c4_led": {"top_on": "ffffff", "top_off": "000000", "bottom_on": "000000", "bottom_off": "0000ff"}}
```

**Set a single LED:**
```json
{"c4_led": {"led": "top", "color": "ffffff", "mode": "on"}}
{"c4_led": {"led": "bottom", "color": "0000ff", "mode": "off"}}
```

**Raw C4 text command** (for experimentation):
```json
{"c4_cmd": "c4.dmx.led 01 03 ff0000"}
```

### LED Parameters

| Parameter | Values |
|-----------|--------|
| LED ID | `01` = top button, `04` = bottom button |
| Mode | `03` = ON color (load is on), `04` = OFF color (load is off) |
| Color | 6-digit hex RGB: `ffffff`=white, `000000`=off, `ff0000`=red, `0000ff`=blue |

### Common LED Configurations

| Style | top_on | top_off | bottom_on | bottom_off |
|-------|--------|---------|-----------|------------|
| Standard C4 | `ffffff` | `000000` | `000000` | `0000ff` |
| All white | `ffffff` | `ffffff` | `ffffff` | `ffffff` |
| Status indicator | `00ff00` | `ff0000` | `000000` | `000000` |

---

## HA Integration Architecture

Each Control4 dimmer exposes **5 light entities** in Home Assistant:

| HA Entity | Purpose | Protocol |
|-----------|---------|----------|
| `light.<name>` | Main dimmer (on/off + brightness) | Standard Zigbee HA |
| `light.<name>_top_led_on` | Top LED color when load is ON | C4 text protocol |
| `light.<name>_top_led_off` | Top LED color when load is OFF | C4 text protocol |
| `light.<name>_bottom_led_on` | Bottom LED color when dimmer is ON | C4 text protocol |
| `light.<name>_bottom_led_off` | Bottom LED color when dimmer is OFF | C4 text protocol |

The 4 LED entities each have a **color picker** (HS color wheel) and
**brightness slider** in the HA UI. The original MQTT interfaces (`c4_led`,
`c4_cmd`) remain available for scripting and automations.

### How It Works

The converter uses the Z2M `ModernExtend` extension system to register
the LED entities as virtual endpoints on the same physical device:

```
                        ┌─────────────────────────────────┐
                        │    Home Assistant                │
                        │                                  │
                        │  light.kitchen (main dimmer)     │
                        │  light.kitchen_top_led_on        │──┐
                        │  light.kitchen_top_led_off       │──┤
                        │  light.kitchen_bottom_led_on     │──┤ Color pickers
                        │  light.kitchen_bottom_led_off    │──┘
                        └──────────────┬──────────────────┘
                                       │ MQTT auto-discovery
                                       ▼
                        ┌─────────────────────────────────┐
                        │    Zigbee2MQTT                   │
                        │                                  │
                        │  extend: [                       │
                        │    c4LedLight('top_led_on')      │──┐
                        │    c4LedLight('top_led_off')     │  │ Endpoint-scoped
                        │    c4LedLight('bottom_led_on')   │  │ converters
                        │    c4LedLight('bottom_led_off')  │──┘
                        │    light()  ← main dimmer        │
                        │  ]                               │
                        │                                  │
                        │  toZigbee: [tzControl4Led,       │──── MQTT raw
                        │             tzControl4Cmd]       │     interface
                        └──────────────┬──────────────────┘
                                       │
                      ┌────────────────┼────────────────┐
                      │ Standard Zigbee│  C4 Text Proto  │
                      │  (genOnOff,    │  (raw ASCII,    │
                      │   genLevelCtrl)│   profile C25C) │
                      └────────────────┼────────────────┘
                                       ▼
                        ┌─────────────────────────────────┐
                        │    Control4 Dimmer (endpoint 1)  │
                        └─────────────────────────────────┘
```

### Converter Routing

The main challenge is that both the main dimmer and LED entities handle
`state`, `brightness`, and `color` keys. Z2M resolves this using the
`endpoints` property on toZigbee converters:

- Each `c4LedLight` converter has `endpoints: ['top_led_on']` (etc.) —
  it only matches commands targeting that specific virtual endpoint.
- The `light()` modernExtend converter has no endpoint restriction —
  it acts as a fallback for the default endpoint (main dimmer).
- **Order matters**: LED extends are listed BEFORE `light()` in the
  `extend` array. Endpoint-restricted converters are checked first;
  when they don't match the target endpoint, Z2M falls through to
  the next converter.

Example routing:

| MQTT topic | Target endpoint | Converter used |
|------------|----------------|----------------|
| `zigbee2mqtt/Kitchen/set {"state":"ON"}` | default | `light()` → standard Zigbee |
| `zigbee2mqtt/Kitchen/top_led_on/set {"color":{"hue":0,"saturation":100}}` | top_led_on | `c4LedLight` → C4 text |
| `zigbee2mqtt/Kitchen/set {"c4_led":{"top_on":"ff0000"}}` | default | `tzControl4Led` → C4 text |

### Brightness Semantics for LEDs

The C4 protocol only accepts a 6-digit hex color — there is no separate
LED intensity parameter. Brightness is implemented by scaling the HSV
value channel:

- Brightness 254 (max) + red (H:0, S:100) → `ff0000`
- Brightness 127 (half) + red → `800000`
- Brightness 0 or state OFF → `000000`

### Two Ways to Control LEDs

Both interfaces work simultaneously and are available at all times:

**1. HA Color Picker** (light entities):
- Tap the LED light entity in HA → use the color wheel
- Brightness slider dims the LED proportionally
- ON/OFF toggle enables/disables the LED
- State is persisted in Z2M across restarts

**2. MQTT Direct** (raw hex, for scripting):
```bash
# Batch mode — set all 4 LED states at once
mosquitto_pub -t zigbee2mqtt/Kitchen/set -m \
  '{"c4_led": {"top_on": "ffffff", "top_off": "000000", "bottom_on": "000000", "bottom_off": "0000ff"}}'

# Single LED
mosquitto_pub -t zigbee2mqtt/Kitchen/set -m \
  '{"c4_led": {"led": "top", "color": "ff0000", "mode": "on"}}'

# Raw C4 command
mosquitto_pub -t zigbee2mqtt/Kitchen/set -m \
  '{"c4_cmd": "c4.dmx.led 01 03 ff0000"}'
```

> **Note**: The two interfaces use separate state keys. Changing an LED
> via the color picker won't update the `c4_led_*` state, and vice versa.
> This is a known limitation — both are sending the same C4 commands to
> the device, just tracked independently.

---

## Troubleshooting

### Device won't pair

- Ensure 13-4-13 was performed correctly (watch for LED blink confirmation)
- Try power cycling the dimmer at the breaker after reset
- Move your coordinator closer temporarily
- Check that Permit Join is enabled and the channel is correct

### Interview fails completely

- This is expected for endpoints 196/197. Endpoint 1 should still work.
- If endpoint 1 also fails, the device may need a firmware update or may
  be a newer model with different behavior.
- Try pairing multiple times. Sometimes it takes 2-3 attempts.

### Commands timeout

- Verify `disableDefaultResponse: true` is set in the converter
- Check that commands are going to endpoint 1 (not 196/197)
- Try increasing the timeout in Z2M advanced settings

### Device pairs but shows as unsupported

- The fingerprint matching may not work for your specific model
- Manually set the modelId in database.db
- Check Z2M logs for the actual clusters reported by the device

### LED commands accepted but LEDs don't change

- Commands return `000` (success) but no visible change? Check you're
  looking at the right dimmer! (Learned this the hard way.)
- Try sending all 4 LED commands as a batch — the device may need the
  complete set.
- Toggle the light on/off after setting colors — some modes only apply
  on state change.

### Only some dimmers work

- Older vs. newer C4 dimmer models may behave differently
- The C4-APD (adaptive phase) series is newer and may need a different
  fingerprint or cluster set
- Sniff traffic (see sniff-c4-traffic.md) to see what your model exposes

---

## How It Works (Technical Deep Dive)

### The Two Faces of a Control4 Dimmer

Every C4 Zigbee dimmer presents itself as a multi-endpoint Zigbee device:

**Endpoint 1** (Profile 0x0104 — Zigbee Home Automation):
- This is a standard, compliant Zigbee HA dimmable light
- Supports clusters: Basic (0x0000), Identify (0x0003), Groups (0x0004),
  Scenes (0x0005), On/Off (0x0006), Level Control (0x0008), Time (0x000A)
- Commands work exactly like any other Zigbee dimmer

**Endpoint 1** also accepts (Profile 0xC25C — C4 "MIB" Protocol):
- Same physical endpoint, different Zigbee profile
- Raw ASCII text commands for LED control, power management, etc.
- Cluster 0x0001 (NOT genPowerCfg — proprietary C4 protocol)
- No ZCL framing — the APS payload IS the text command

**Endpoint 197 / 0xC5** (Profile 0xC25C — C4 Events):
- Sends responses and unsolicited status reports back to the coordinator
- Response format: `0r<seq> 000` (success) or `0r<seq> v01` (error)
- Telemetry format: `0t<seq> sa c4.dmx.ls <status_fields>`
- Refuses simple descriptor requests (causes interview failures)

**Endpoint 196 / 0xC4** (Profile 0xC25D — C4 Network):
- Used for device network management on the C4 mesh
- Different profile (0xC25D vs 0xC25C) from the text command protocol
- Refuses simple descriptor requests
- Not needed for Z2M operation

### C4 Text Protocol

The proprietary Control4 protocol sends raw ASCII as the APS payload.
Unlike standard Zigbee, there is **no ZCL framing** — no frame control
byte, no sequence number byte, no command ID byte. Just text.

```
┌─────────────────────────────────────────────────┐
│  APS Frame                                       │
│  Profile: 0xC25C   Cluster: 0x0001              │
│  Src EP: 1         Dst EP: 1                    │
│                                                  │
│  Payload (raw ASCII):                            │
│  "0s<seq> c4.dmx.led 01 03 ffffff\r\n"          │
└─────────────────────────────────────────────────┘
```

**Verb prefixes:**
| Prefix | Meaning | Direction |
|--------|---------|-----------|
| `0s` | SET — write a value | Coordinator → Device |
| `0g` | GET — read a value | Coordinator → Device |
| `0r` | Response — ACK/NACK | Device → Coordinator |
| `0t` | Telemetry — unsolicited report | Device → Coordinator |

**Response codes:**
| Code | Meaning |
|------|---------|
| `000` | Success |
| `v01` | Invalid value or parameter |
| `e00` | Parameter error (missing arguments) |

### Known Command Catalog

| Command | Description | Example |
|---------|-------------|---------|
| `c4.dmx.led` | Set LED color | `c4.dmx.led 01 03 ffffff` |
| `c4.dmx.pwr` | Set power level | `c4.dmx.pwr b5` |
| `c4.dmx.off` | Turn off | `c4.dmx.off 0000` |
| `c4.dmx.amb` | Ambient LED mode | `c4.dmx.amb 01` (query) |
| `c4.dmx.ls` | Light status report | (incoming telemetry) |
| `c4.dmx.key` | Key/button event | (incoming telemetry) |
| `c4.dmx.bp` | Button press event | (incoming telemetry) |
| `c4.dmx.cc` | Config change | (unknown params) |
| `c4.dmx.hc` | Unknown | |
| `c4.dmx.he` | Unknown | |
| `c4.dmx.plm` | Unknown | |
| `c4.dmx.pmti` | Unknown | |
| `c4.dmx.sc` | Scene? | |

### Why Control4 Locks You In

Control4 deliberately:
1. Returns empty `modelId` from `genBasic` cluster — breaks auto-discovery
2. Uses endpoints (196/197) that refuse standard Zigbee introspection
3. Doesn't send ZCL default responses — causes timeout errors
4. Wraps standard functionality in a proprietary profile layer

But they CAN'T hide the standard Zigbee HA endpoint (1) because the
Zigbee specification requires it for HA profile compliance. The standard
clusters respond to standard commands. They just made it hard to discover.

### The Network Key Problem (Non-Issue After Reset)

When devices are on a Control4 network, they use C4's network encryption
key. After factory reset (13-4-13), the device forgets the old key and
will accept a new one during pairing with your Zigbee2MQTT coordinator.
No key extraction is needed.

### Implementation Note: Bypassing ZCL Framing

The biggest implementation challenge was that zigbee-herdsman's
`endpoint.command()` always wraps payloads in ZCL headers. The C4 protocol
needs raw ASCII with NO framing. The solution: call the private
`endpoint.sendRequest()` directly with a fake frame object whose
`toBuffer()` returns just the raw bytes. See the converter source for
details.

### Implementation Note: LED Light Entities

The converter exposes LED colors as native HA light entities using the
Z2M `ModernExtend` extension pattern. A factory function `c4LedLight()`
creates one extension per LED state, each returning:

- An `exposes` entry: a `Light` with brightness + HS color, tagged with
  `.withEndpoint(name)` so HA creates a separate entity for it.
- A `toZigbee` converter scoped to that endpoint via `endpoints: [name]`.
  It converts HA color values (HS or XY) to 6-digit hex RGB and sends
  the appropriate `c4.dmx.led` command via the raw text protocol.
- Color conversion (HSV→RGB, XY→RGB) is done in the converter since
  the C4 protocol only accepts hex RGB colors.

See [HA Integration Architecture](#ha-integration-architecture) for the
full routing and state management details.

---

## Files in This Project

```
control4-zigbee-migration/
├── README.md                          # This file
├── PROGRESS.md                        # Session-by-session progress log
├── external_converters/
│   └── control4-dimmer.mjs            # Zigbee2MQTT external converter
├── device_icons/
│   └── C4-APD120.png                  # Custom Z2M dashboard icon
├── mosquitto_pub.sh                   # Helper script for MQTT commands
└── sniff-c4-traffic.md                # Guide to sniff C4 Zigbee traffic
```

## Credits

- **pstuart** — Original SmartThings C4 dimmer driver that proved standard
  Zigbee commands work on endpoint 1
- **iankberry** — Hubitat port confirming continued compatibility
- **samtherecordman** — Zigbee2MQTT issue #160 pioneer work with
  disableDefaultResponse discovery
- **Koenkk** — Zigbee2MQTT/zigbee-herdsman guidance on interview workarounds
