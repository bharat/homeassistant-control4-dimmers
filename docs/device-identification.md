# Control4 Device Identification & Comparison

How to identify and differentiate Control4 Zigbee devices (dimmers vs. keypads) for correct converter routing in Zigbee2MQTT.

---

## Known Device Models

| Model | Type | Type Tag | Firmware | Endpoints | Load? | Buttons |
|-------|------|----------|----------|-----------|-------|---------|
| C4-APD120 | Adaptive Phase Dimmer | `control4_light` | 5.1.1 | 1, 196, 197 | Yes (1 load) | 1 rocker (top/bottom) |
| C4-KC120277 | Configurable Keypad | `control4_kp` | 4.4.16 | 1, 2 | No | Multiple (6+?) |
| C4-KP6-Z | 6-Button Keypad (older) | `control4_keypad` | 3.22.41 | 1, 2 | No | 6 |

**Common to all:** Manufacturer ID 43981 (0xABCD), IEEE prefix `0x000fff`.

**Note:** The C4-APD120 dimmer has a "Use as 2 Button Keypad" option in Composer Pro. When enabled, the dimmer hardware acts as a keypad (buttons send events but don't control the load). This means a dimmer's behavior can change at the software level — the endpoint structure remains the same.

---

## Identification Methods

### 1. Endpoint Fingerprint (Best for Z2M Converter Routing)

The most reliable method — available immediately from Z2M's `database.db` after pairing, no probing required.

| Device | Endpoints | EP1 deviceID |
|--------|-----------|--------------|
| Dimmer | 1, **196**, **197** | 0x0101 (Dimmable Light) |
| Keypad | 1, **2** | 0x0000 |

**Key differentiator:** Presence of endpoint 196/197 = dimmer. Presence of endpoint 2 (without 196/197) = keypad.

Z2M fingerprint example:
```javascript
// Dimmer: match on endpoints 1/196/197
fingerprint: [{
    manufacturerID: 43981,
    endpoints: [
        {ID: 1, profileID: 0x0104, deviceID: 0x0101},
        {ID: 196},
        {ID: 197},
    ],
}]

// Keypad: match on endpoints 1/2 (no 196/197)
fingerprint: [{
    manufacturerID: 43981,
    endpoints: [
        {ID: 1},
        {ID: 2},
    ],
}]
```

### 2. Self-Identification Broadcast (Most Definitive)

Devices broadcast their model on profile 0xC25D after joining. The payload contains a string like:

```
c4:control4_light:C4-APD120      → dimmer
c4:control4_kp:C4-KC120277       → keypad
```

**Format:** `c4:<type_tag>:<model_number>`

**Type tags:**
- `control4_light` — dimmer/switch with load
- `control4_kp` — keypad (newer models)
- `control4_keypad` — keypad (older C4-KP6-Z)

The older C4-KP6-Z model also reports this via `genPowerCfg` attribute 7 on endpoint 2.

### 3. Runtime C4 Query (For Probing)

If the broadcast isn't captured, send C4 text protocol queries:

| Query | Dimmer | Keypad |
|-------|--------|--------|
| `c4.dmx.dim` | `000 c4.dmx.dim 02` | No response / not recognized |
| `c4.dmx.ls` telemetry | Sent continuously | Never sent |
| `c4.dmx.bp` (count query) | `n01` (1 rocker) | `n06` or higher |

Use `probe-device.py` with `--docker` to run these queries interactively.

---

## Director Join Sequences

### Dimmer Join (C4-APD120)

**Source:** `000fff0000cabd8d.log`

1. Device broadcasts self-identification: `c4:control4_light:C4-APD120` (profile 0xC25D)
2. Device spontaneously sends load status telemetry: `c4.dmx.ls 00 00 64 007a ...`
3. Director sends power config: `c4.dmx.pwr b5`
4. Director turns off load: `c4.dmx.off 0000`
5. Director sets ZigBee power mode: `c4.sy.zpw 00`
6. Director sets power line mode: `c4.dmx.plm 00`
7. Director queries device key: `c4.dmx.key`
8. Director inits ALS: `c4.als.sra`
9. Director reads/writes dimming table: `c4.dm.tv 00 00..0a` (10 variables for ramp rates, min/max)
10. Director sets LED colors (4 commands):
    - `c4.dmx.led 01 03 ffffff` (top ON = white)
    - `c4.dmx.led 01 04 000000` (top OFF = black)
    - `c4.dmx.led 04 03 000000` (bottom ON = black)
    - `c4.dmx.led 04 04 0000ff` (bottom OFF = blue)
11. Director sets power measurement timer: `c4.dmx.pmti 0007 0007`
12. Director queries dimmer type: `c4.dmx.dim` → `02`
13. Director queries ambient LED: `c4.dmx.amb 01` → `00`
14. Director repeats power config: `c4.dmx.pwr b5`
15. Director writes dimming table again (repeat for reliability)

**Notable:** The dimmer sends `c4.dmx.ls` telemetry continuously throughout (power monitoring data).

### Keypad Join (C4-KC120277)

**Source:** `0x000fff0000ce96a4.log`

1. Device broadcasts self-identification: `c4:control4_kp:C4-KC120277` (profile 0xC25D)
2. Director sets initial LED: `c4.dmx.led 00 05 0000cc` (button 0 = blue)
3. Device sends button events during 4-tap join:
   - `c4.dmx.bp 00` (button press)
   - `c4.dmx.sc 00` (scene change)
   - `c4.dmx.cc 00 04` (4 clicks detected)
4. Director sets remaining LEDs:
   - `c4.dmx.led 02 05 000000` (button 2 = off)
   - `c4.dmx.led 03 05 000000` (button 3 = off)
   - `c4.dmx.led 04 05 0000ff` (button 4 = blue)
   - `c4.dmx.led 05 05 0000ff` (button 5 = blue)
5. Director queries device key: `c4.dmx.key` → key material
6. Director sets button 0 LED: `c4.dmx.led 00 05 cccccc` (grey)
7. Director queries ambient LED: `c4.dmx.amb 01` → `00`

**Notable:** No `c4.dmx.ls`, no `c4.dm.tv`, no `c4.dmx.dim`, no `c4.dmx.pwr` — keypad has no load.

---

## LED Button Numbering

### Dimmer (C4-APD120)

| Button ID | Location | Default ON Color | Default OFF Color |
|-----------|----------|-----------------|-------------------|
| `01` | Top rocker | `ffffff` (white) | `000000` (black/off) |
| `04` | Bottom rocker | `000000` (black/off) | `0000ff` (blue) |

### Keypad (C4-KC120277)

The keypad chassis has **6 physical slots** (IDs `00`–`05`), but the actual button layout is **configurable** via Composer Pro. Buttons are assembled from modular parts:

| Keypad Part | Slots Used |
|-------------|-----------|
| 1 Slot High | 1 slot |
| 2 Slots High | 2 slots |
| 3 Slots High | 3 slots |
| Rocker | 2 slots (up/down) |
| Down / Up | 2 slots |

**Example configuration** (from the paired C4-KC120277):

| Slot | Button Name | Note |
|------|-------------|------|
| 00 | "Cabinets" | 1 slot |
| 01 | (part of "Cabinets"?) | Skipped in Director log |
| 02 | "Early Bird" | 1 slot |
| 03 | "Unused" | 1 slot |
| 04 | "Kitchen" | 1 slot |
| 05 | (unused) | |

Each button has per-button settings in Composer Pro:
- **Name** (for engraving)
- **LED Behavior** (dropdown — follow load, always on, etc.)
- **On Color** (LED color when active)
- **Off Color** (LED color when inactive)

Button IDs correspond to **physical slot positions** (0–5 top to bottom), not the logical button count. A 2-slot-high button occupies two consecutive IDs.

The C4-KP6-Z (older model) uses buttons `00`–`05` for a fixed 6-button layout.

---

## Sources

- **Dimmer log:** `/private/tmp/000fff0000cabd8d.log` (C4-APD120 joining C4 network)
- **Keypad log:** `/private/tmp/0x000fff0000ce96a4.log` (C4-KC120277 joining C4 network)
- **GitHub Issue:** [Z2M #15361 — Control4 6-button keypad](https://github.com/Koenkk/zigbee2mqtt/issues/15361) by @ArcadeMachinist
- **SmartThings Thread:** [Control4 Keypad Zigbee Driver](https://community.smartthings.com/t/control4-keypad-zigbee-driver/3563)
- **Live probing:** `scripts/probe-device.py` with `--docker` flag against Kitchen dimmer (0x000fff0000c55f83)
