# Control4 Device Identification & Comparison

How to identify and differentiate Control4 Zigbee devices (dimmers vs. keypads) for correct converter routing in Zigbee2MQTT.

---

## Known Device Models

| Model | Type | Type Tag | Firmware | Endpoints | Load? | Buttons |
|-------|------|----------|----------|-----------|-------|---------|
| C4-APD120 | Adaptive Phase Dimmer | `control4_light` | 5.1.1 | 1, 196, 197 | Yes (1 load) | 2 (top/bottom rocker) |
| C4-KD120 | Keypad Dimmer | `control4_light` | 5.1.1 | 1, 196, 197 | Yes (1 load) | 6 (rocker + 4 keypad buttons) |
| C4-KC120277 | Configurable Keypad | `control4_kp` | 4.4.16 | 1, 196, 197 | No | 6 slots (configurable) |
| C4-KP6-Z | 6-Button Keypad (older) | `control4_keypad` | 3.22.41 | 1, 2 | No | 6 |

**IMPORTANT: All newer C4 devices (APD120, KD120, KC120277) share IDENTICAL endpoint structures (1, 196, 197).** Only the older C4-KP6-Z has a different structure (endpoints 1, 2). Endpoint-based Z2M fingerprinting CANNOT differentiate newer C4 device types. Runtime probing via C4 text protocol is required.

**Common to all:** Manufacturer ID 43981 (0xABCD), IEEE prefix `0x000fff`.

**Note:** The C4-APD120 dimmer has a "Use as 2 Button Keypad" option in Composer Pro. When enabled, the dimmer hardware acts as a keypad (buttons send events but don't control the load). This means a dimmer's behavior can change at the software level — the endpoint structure remains the same.

**Note:** The C4-KD120 (Keypad Dimmer) self-identifies as `control4_light` — the same type tag as the C4-APD120. The model string (`C4-KD120` vs `C4-APD120`) is the only differentiator in the self-ID broadcast. The KD120 uses the exact same C4 text protocol as the APD120, with the key difference being it has 6 LED button slots (00–05) instead of just 2 (01, 04). Its endpoint structure matches all newer C4 devices (1/196/197). Z2M fingerprinting alone cannot distinguish ANY newer C4 devices — runtime probing is required.

---

## Identification Methods

### 1. Endpoint Fingerprint — NOT USABLE for Newer Devices

**CORRECTED:** All newer C4 devices (APD120, KD120, KC120277) report identical endpoints:

| Device | Endpoints | EP1 deviceID |
|--------|-----------|--------------|
| Dimmer (C4-APD120) | 1, 196, 197 | 0x0101 (Dimmable Light) |
| Keypad Dimmer (C4-KD120) | 1, 196, 197 | 0x0101 (Dimmable Light) |
| Pure Keypad (C4-KC120277) | 1, 196, 197 | 0x0101 (Dimmable Light) |
| Older Keypad (C4-KP6-Z) | 1, **2** | 0x0000 |

**All newer devices are endpoint-identical.** The earlier assumption that KC120277 had endpoints 1, 2 was based on the C4-KP6-Z (older EM250-based model, from GitHub issue #15361). Live testing of the KC120277 confirmed it uses 1/196/197.

Z2M fingerprint: single catch-all for all newer C4 devices:
```javascript
fingerprint: [{manufacturerID: 43981}]
```

Device-type differentiation requires **runtime probing** (see section 3 below).

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

### 3. Runtime C4 Query (REQUIRED for Device-Type Detection)

Since endpoint fingerprinting cannot differentiate newer C4 devices, runtime probing via C4 text protocol is the **primary identification method**.

**Single-command detection: `0g c4.dmx.dim` (query dimmer type)**

This single command provides complete three-way device identification:

| Device | `c4.dmx.dim` Response | Dim Type |
|--------|----------------------|----------|
| APD120 (Adaptive Phase Dimmer) | `000 c4.dmx.dim 01` | `01` = Forward Phase |
| KD120 (Keypad Dimmer) | `000 c4.dmx.dim 02` | `02` = Reverse Phase |
| KC120277 (Pure Keypad) | `n01` (error — no load) | n/a |

**Detection matrix:**

| `c4.dmx.dim` | Device Type | Buttons | Load |
|--------------|-------------|---------|------|
| `01` | APD120 (dimmer) | 2 (top=01, bottom=04) | Yes |
| `02` | KD120 (keypad dimmer) | 6 (slots 00–05) | Yes |
| error/`n01` | KC120277 (pure keypad) | 6 (slots 00–05) | No |

**IMPORTANT (corrected):** The earlier approach of probing `c4.dmx.led 02 03` (button 02 existence) does NOT work. All C4 devices — including the 2-button APD120 — respond to LED queries for all 6 button slots (00–05). The APD120 simply stores `000000` for its unused slots. The `c4.dmx.dim` response code is the **only** reliable three-way differentiator discovered so far.

**Supplementary queries (confirmed via `survey` command):**

| Query | APD120 | KD120 | KC120277 |
|-------|--------|-------|----------|
| `c4.dm.sl` | `000 c4.dm.sl 00` | `000 c4.dm.sl 00` | `n01` (unsupported) |
| `c4.dm.tv` | `e00` | `e00` | `n01` |
| `c4.dmx.pwr` | `000 c4.dmx.pwr ...` | `000 c4.dmx.pwr ...` | `n01` |
| `c4.als.sra` | `e00` | `e00` | `n01` |
| `c4.dmx.bp` | `n01` | `n01` | `n01` |
| `c4.kp.*` | `n01` | `n01` | `n01` |

Note: `c4.dm.sl` distinguishes load/no-load but not APD120 from KD120 (both return `00`). Only `c4.dmx.dim` provides full three-way differentiation.

**LED color persistence:** All C4 devices store LED colors in firmware across power cycles and network migrations. All devices respond to `0g c4.dmx.led <btn> <mode>` for all 6 slots (00–05), even the 2-button APD120 (unused slots store `000000`). Use this to auto-populate HA state during migration.

**Tool:** Use `probe-device.py` with `--docker` to run these queries interactively:
```bash
python3 probe-device.py <device> --docker -i   # then type "detect" or "survey"
python3 probe-device.py <device> --docker --detect  # one-shot detection
```

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

### Keypad Dimmer Join (C4-KD120)

**Source:** `000fff0000c9be0c.log`

1. Device broadcasts self-identification: `c4:control4_light:C4-KD120` (profile 0xC25D)
2. Director turns off load: `c4.dmx.off 0000`
3. Device sends button events during 4-tap join:
   - `c4.dmx.bp 00` (button 00 press — top rocker)
   - `c4.dmx.sc 00` (scene change)
   - `c4.dmx.cc 00 04` (4 clicks detected)
4. Director sets ZigBee power mode: `c4.sy.zpw 00`
5. Director sets power line mode: `c4.dmx.plm 00`
6. Director sets LED colors (**4 buttons, all mode 05**):
   - `c4.dmx.led 01 05 0000cc` (button 1 = blue)
   - `c4.dmx.led 02 05 0000cc` (button 2 = blue)
   - `c4.dmx.led 03 05 0000cc` (button 3 = blue)
   - `c4.dmx.led 04 05 000000` (button 4 = off)
7. Director queries device key: `c4.dmx.key`
8. Director inits ALS: `c4.als.sra`
9. Director reads/writes dimming table: `c4.dm.tv 00 00..0a` (same values as APD120)
10. Device sends load status telemetry: `c4.dmx.ls 00 00 05 007c ...`
11. Director queries ambient LED: `c4.dmx.amb 01` → `00`

**Notable differences from C4-APD120:**
- **4 LED buttons (01–04)** instead of 2 (01, 04) — the KD120 has additional keypad buttons
- **All LEDs use mode 05** (coordinator-pushed override) instead of modes 03/04 (firmware-managed on/off)
- **4-tap join uses button 00** (not button 01) — button 00 is likely the top rocker
- **Same dimming table, load telemetry, power config** — identical load-control protocol
- **New pre-join commands**: `c4.dmx.pwr` as GET (returns power data `007b 00eb 0004 001d 0088 0004 0000`), `c4.dm.sl` (returns `00`, meaning TBD — possibly dimmer slot/slave config)

---

## LED Button Numbering

### Dimmer (C4-APD120)

| Button ID | Location | Default ON Color | Default OFF Color |
|-----------|----------|-----------------|-------------------|
| `01` | Top rocker | `ffffff` (white) | `000000` (black/off) |
| `04` | Bottom rocker | `000000` (black/off) | `0000ff` (blue) |

### Keypad Dimmer (C4-KD120)

| Button ID | Location | Default Color (Director) | Notes |
|-----------|----------|-------------------------|-------|
| `00` | Top rocker / slot 0 | (not set by Director) | 4-tap join button; likely load control |
| `01` | Slot 1 | `0000cc` (blue) | |
| `02` | Slot 2 | `0000cc` (blue) | |
| `03` | Slot 3 | `0000cc` (blue) | |
| `04` | Slot 4 | `000000` (off) | |
| `05` | Slot 5 (bottom) | (not set by Director in log) | May be unused in this config |

All LEDs use mode 05 (coordinator-pushed). The Director manages LED state centrally rather than letting firmware auto-switch. In Composer Pro, the KD120 appears as a dimmer with a "Keypad" child component — the dimmer controls the load, and the keypad child manages button behavior and LED colors. The KD120 has the same 6-slot chassis as the KC120277 keypad, plus a load.

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

## Z2M Converter Architecture

### Why One Definition, Not Three

All newer C4 devices share identical endpoint structures (1/196/197), identical manufacturer ID (43981), and the same `c4.dmx.*` text protocol namespace. Z2M fingerprinting cannot distinguish them. The older C4-KP6-Z (endpoints 1/2, `c4.kp.*` namespace) is a separate case.

**Architecture: Single catch-all definition with runtime detection.**

```
1. Z2M pairs device → catch-all fingerprint matches (manufID: 43981)
2. probe-device.py runs "detect" → single C4 query: c4.dmx.dim
   - "01" → APD120 (forward-phase dimmer, 2 buttons)
   - "02" → KD120 (reverse-phase keypad dimmer, 6 buttons)
   - error → KC120277 (pure keypad, 6 buttons, no load)
3. Reads all stored LED colors (6 slots × 2 modes = 12 queries)
4. Publishes device type + LED colors to Z2M state via MQTT
5. Exposes: all 6 button slots (users disable unused ones in HA)
   - APD120: main dimmer light + 2 relevant LED entities (top/bottom)
   - KD120: main dimmer light + 6 LED entities + button events
   - KC120277: 6 LED entities + button events (no main light)
```

### Runtime Detection Flow

Detection uses `probe-device.py --docker --detect` (Docker log capture), which is more
reliable than the in-converter `c4_detect` mechanism (which suffers from Z2M's single-threaded
message processing preventing fromZigbee responses during toZigbee execution).

```python
# Single command, three-way detection:
response = c4_query_sync('c4.dmx.dim')

if response contains "000 c4.dmx.dim 01" → deviceType = 'dimmer'     (APD120)
if response contains "000 c4.dmx.dim 02" → deviceType = 'keypaddim'  (KD120)
if response is error/n01/timeout         → deviceType = 'keypad'     (KC120277)
```

### LED Color Auto-Population

C4 devices persistently store LED colors in firmware. During `configure()`, the converter can **read** all stored colors using `0g c4.dmx.led <btn> <mode>` and auto-populate the HA state with the existing C4 configuration. This means migrated devices will show their current colors in HA immediately — no manual reconfiguration needed.

---

## Sources

- **Dimmer log:** `/private/tmp/000fff0000cabd8d.log` (C4-APD120 joining C4 network)
- **Keypad log:** `/private/tmp/0x000fff0000ce96a4.log` (C4-KC120277 joining C4 network)
- **Keypad Dimmer log:** `/private/tmp/000fff0000c9be0c.log` (C4-KD120 joining C4 network)
- **GitHub Issue:** [Z2M #15361 — Control4 6-button keypad](https://github.com/Koenkk/zigbee2mqtt/issues/15361) by @ArcadeMachinist
- **SmartThings Thread:** [Control4 Keypad Zigbee Driver](https://community.smartthings.com/t/control4-keypad-zigbee-driver/3563)
- **Live probing:** `scripts/probe-device.py` with `--docker` flag
  - Kitchen (0x000fff0000c55f83) — APD120 dimmer: `c4.dmx.dim` → `01`
  - Downstairs Hall (0x000fff0000c9be0c) — KD120 keypad dimmer: `c4.dmx.dim` → `02`
  - 0x000fff0000ce30c7 — KC120277 pure keypad: `c4.dmx.dim` → `n01`
- **Survey diffs:** `survey-kitchen.txt` vs `survey-0x000fff0000ce30c7.txt` vs `survey-0x000fff0000c9be0c.txt`
