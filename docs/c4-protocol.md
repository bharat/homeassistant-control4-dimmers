# Control4 Zigbee Text Protocol Reference

This document captures the complete known Control4 proprietary text protocol as reverse-engineered from C4 Director (`zap.log`) captures and live device probing via Zigbee2MQTT.

---

## Transport Layer

All C4 text commands are sent as **raw ASCII bytes** in the APS payload — with **NO ZCL framing** (no frame control, sequence number, or command ID header).

| Field | Value | Notes |
|-------|-------|-------|
| Profile | 0xC25C (49756) | "C4_MIB_PROFILE_ID" |
| Cluster | 0x0001 | |
| Source Endpoint | 1 | Coordinator → device |
| Dest Endpoint | 1 | Coordinator → device |
| Response From EP | 197 (0xC5) | Device → coordinator |
| Response From Profile | 0xC25C | Same proprietary profile |

A second profile, **0xC25D** ("C4_NETWORK_PROFILE_ID"), is used for device self-identification broadcasts (see [Device Identification](#device-identification)).

---

## Packet Types

| Prefix | Name | Direction | Purpose |
|--------|------|-----------|---------|
| `0s` | SET | Coordinator → Device | Write a value |
| `0g` | GET | Coordinator → Device | Read a value (expects response) |
| `0r` | RESPONSE | Device → Coordinator | Reply to SET or GET |
| `0t` | TELL/EVENT | Device → Coordinator | Unsolicited status report |
| `0i` | INIT/SYSTEM | Coordinator → Device | System/init commands (e.g., reboot, ALS init) |

## Packet Format

```
<type><seq_hex4> <command_or_status> [params]\r\n
```

- **Sequence number:** 4-digit hex counter (e.g., `a9c8`), incremented per command. The device echoes it in the response for request-response correlation.
- **`\r\n`** terminates all packets.

### Response Status Codes

| Code | Meaning |
|------|---------|
| `000` | Success (may be followed by data) |
| `n<XX>` | Count response — "I have XX items" (e.g., `n01` = 1 item) |
| `v01` | Invalid value/parameter |
| `e00` | Parameter error (missing args) |

---

## Command Catalog

### `c4.dmx.*` — Device/DMX Commands (Shared Namespace)

Both dimmers and keypads use this namespace. All commands confirmed via Director log capture.

#### LED Control

```
SET: 0s<seq> c4.dmx.led <button_id> <mode> <RRGGBB>
GET: 0g<seq> c4.dmx.led <button_id> <mode>
 →   0r<seq> 000 c4.dmx.led <RRGGBB>
```

| Parameter | Values |
|-----------|--------|
| `button_id` | Dimmer: `01`=top, `04`=bottom. KD120/KC120277: `00`–`05` (6 slots) |
| `mode` | `03`=ON color (shown when active), `04`=OFF color (shown when inactive), `05`=current/override |
| `RRGGBB` | 6-digit hex RGB color |

**SET examples:**
```
0s<seq> c4.dmx.led 01 03 ffffff    — set top LED white when ON
0s<seq> c4.dmx.led 01 04 000000    — set top LED dark when OFF
0s<seq> c4.dmx.led 04 03 000000    — set bottom LED dark when ON
0s<seq> c4.dmx.led 04 04 0000ff    — set bottom LED blue when OFF
```

**GET examples (read stored colors from firmware):**
```
0g<seq> c4.dmx.led 01 03           — read top LED ON color
→ 0r<seq> 000 c4.dmx.led ffffff   — stored color is white

0g<seq> c4.dmx.led 02 03           — read slot 2 ON color (device probing)
→ 0r<seq> 000 c4.dmx.led 0000cc   — stored color is blue (KD120/KC120277)
→ (no response / timeout)          — button doesn't exist (APD120)
```

**Color persistence:** LED colors are stored in device firmware and survive power cycles and network migrations. When migrating from C4 to Z2M, stored colors can be read to auto-populate HA state without user reconfiguration.

**Device probing via GET:** Querying `c4.dmx.led 02 03` is the primary method for distinguishing APD120 (2 buttons) from KD120/KC120277 (6 buttons). Devices with button 02 respond with a color; devices without it do not respond.

#### Ambient LED

```
0g<seq> c4.dmx.amb 01              — GET ambient LED color
→ 0r<seq> 000 c4.dmx.amb 00       — Response: 00 = off
```

#### Load Control

```
0s<seq> c4.dmx.off 0000            — Turn off load 00
```

Note: Load on/dimming is handled via standard ZCL on/off + level control clusters.

#### Button/Scene Events (Unsolicited)

```
0t<seq> sa c4.dmx.bp <button>      — Button press
0t<seq> sa c4.dmx.sc <button>      — Scene change
0t<seq> sa c4.dmx.cc <btn> <count> — Click count (after all clicks done)
```

Button numbering: `00`, `01`, etc. (leading zero). The dimmer sends `bp 01` (bottom rocker = button 1), the keypad sends `bp 00` through `bp 05`.

#### Load Status Telemetry (Unsolicited — Dimmers Only)

```
0t<seq> sa c4.dmx.ls <load> <on/off> <level> <voltage> <current> <pf> <temp> <energy> <runtime> <flags>
```

**Example:** `c4.dmx.ls 00 00 64 007a 0151 0023 0029 035e 000b 0000`

| Field | Hex | Decimal | Meaning |
|-------|-----|---------|---------|
| load | 00 | 0 | Load index |
| on/off | 00 | 0 | 0=off, 1=on |
| level | 64 | 100 | Brightness % |
| voltage | 007a | 122 | Line voltage (V) |
| current | 0151 | 337 | Current (mA?) |
| field5 | 0023 | 35 | Power or power factor? |
| temp | 0029 | 41 | Temperature (°C) |
| energy | 035e | 862 | Cumulative energy (scaling TBD) |
| runtime | 000b | 11 | Runtime (scaling TBD) |
| flags | 0000 | 0 | Status flags |

Sent continuously by dimmers (every few seconds). Not sent by keypads. See [Appendix](#appendix-composer-pro-properties--protocol-mapping) for correlation with Composer Pro energy data.

#### Power Configuration (Dimmers Only)

```
0s<seq> c4.dmx.pwr b5              — Power configuration SET (meaning of b5 TBD)
0g<seq> c4.dmx.pwr                 — Power configuration GET (returns live power data)
→ 0r<seq> 000 c4.dmx.pwr 007b 00eb 0004 001d 0088 0004 0000
0s<seq> c4.dmx.plm 00              — Power line mode (00 = auto-detect)
0s<seq> c4.dmx.pmti 0007 0007      — Power measurement timer interval
```

**`c4.dmx.pwr` GET response fields** (observed from C4-KD120):

| Field | Hex | Decimal | Probable Meaning |
|-------|-----|---------|-----------------|
| 1 | 007b | 123 | Line voltage (V) |
| 2 | 00eb | 235 | Current (mA?) |
| 3 | 0004 | 4 | Power (W?) |
| 4 | 001d | 29 | Temperature (°C?) |
| 5 | 0088 | 136 | Unknown |
| 6 | 0004 | 4 | Unknown |
| 7 | 0000 | 0 | Flags |

**Correlation with Composer Pro:**
- `c4.dmx.plm 00` → Dimming Mode: "Auto-Detect" (Detected: Reverse Phase)
- `c4.dmx.pmti` → Configures the energy monitoring refresh interval shown in Composer Pro "Energy Information" panel

#### Dimmer Query (Dimmers Only)

```
0g<seq> c4.dmx.dim                 — Query dimmer type
→ 0r<seq> 000 c4.dmx.dim 02       — Type 02 (Reverse Phase)
```

Likely maps to detected dimming mode: `01` = Forward Phase, `02` = Reverse Phase.
Keypads do not respond to this query.

#### Device Crypto Key

```
0g<seq> c4.dmx.key
→ 0r<seq> 000 c4.dmx.key 00 64 382cadff b314ee3b 18089a81 00000000 00000000
```

Used by C4 Director for cloud authentication. Not needed for Z2M operation.

#### Button Panel Count

```
0g<seq> c4.dmx.bp
→ 0r<seq> n01                      — 1 button panel (dimmer)
```

Keypads would return `n06` or similar for 6-button panels.

---

### `c4.dm.*` — Dimming Table Commands (Dimmers Only)

#### Table Value Read/Write

```
0g<seq> c4.dm.tv <load> <var>              — GET table value
→ 0r<seq> 000 c4.dm.tv <value_hex8>       — Response

0s<seq> c4.dm.tv <load> <var> <value_hex8> — SET table value
→ 0r<seq> 000                              — ACK
```

**Known table variables** (set by Director during dimmer join, correlated with Composer Pro UI):

| Var | Hex Value | Decimal | Composer Pro Property |
|-----|-----------|---------|----------------------|
| 00 | (read only) → 0064 | 100 | Current brightness level (%) |
| 01 | 00000064 | 100 | **Default On Brightness** = 100% |
| 02 | 000000fa | 250 | **Click Rate Up** = 0.250s (250ms) |
| 03 | 000002ee | 750 | **Click Rate Down** = 0.750s (750ms) |
| 04 | 00001388 | 5000 | **Hold Ramp Rate Up** = 5.000s (5000ms) |
| 05 | 00001388 | 5000 | **Hold Ramp Rate Down** = 5.000s (5000ms) |
| 06 | 00000064 | 100 | **Max On** = 100% |
| 07 | (not set) | — | **Default Brightness Rate** = 0.250s? (may share var 02) |
| 08 | 00000001 | 1 | **Min On** = 1% |
| 09 | 00000000 | 0 | **Cold Start Time** = 0ms |
| 0a | 00000000 | 0 | **Cold Start Level** = 0% |

All values are in milliseconds (for rates/times) or percentage (for levels).
Var 07 was not written during the observed join — the device may already have the correct default.

---

### `c4.dm.sl` — Dimmer Slot/Slave Query (Dimmers Only)

```
0g<seq> c4.dm.sl
→ 0r<seq> 000 c4.dm.sl 00
```

Observed during C4-KD120 pre-join polling. Exact meaning TBD — possibly dimmer slot configuration or slave/master status. Returns `00`.

---

### `c4.sy.*` — System Commands

```
0s<seq> c4.sy.zpw 00               — ZigBee power mode
```

---

### `c4.als.*` — Ambient Light Sensor (Dimmers Only)

```
0i<seq> c4.als.sra                  — Init/start ambient light sensor reporting
→ 0r<seq> 000                       — ACK
```

Maps to the **Ambient Light Profiles** section in Composer Pro, which has:
- Backlight Color (set via `c4.dmx.amb`)
- Dark Room / Dim Room / Bright Room profiles for Status LEDs and Backlight brightness
- Sensing Mode: Independent

The `c4.dmx.amb 01` query returns `00` when the backlight color is black (off).

---

### `c4.kp.*` — Keypad Commands (Older C4-KP6-Z Model)

These commands were documented by @ArcadeMachinist for the **C4-KP6-Z** model (EM250-based, ZPro firmware). Newer keypads (C4-KC120277) use `c4.dmx.*` commands instead.

```
c4.kp.bb <btn>         — Button down
c4.kp.bc <btn>         — Button up (click)
c4.kp.bh <btn>         — Button held
c4.kp.be <btn>         — Button released after hold
c4.kp.cc <btn> <count> — Click count

c4.kp.lv <btn> RRGGBB  — Set momentary color (resets on click)
c4.kp.lf <btn> RRGGBB  — Set "off" color
c4.kp.lo <btn> RRGGBB  — Set "on" color
c4.kp.lo ff ff RRGGBB  — Set all 6 buttons at once

0i<seq> c4.kp.rb 00    — Reboot keypad
```

**Source:** [Z2M Issue #15361](https://github.com/Koenkk/zigbee2mqtt/issues/15361)

---

## Device Identification

### Self-Identification Broadcast

On join (and periodically), C4 devices broadcast an identification message on **profile 0xC25D** (C4_NETWORK_PROFILE_ID), cluster 0x0001. The payload is a ZCL attribute report containing the model string.

**Dimmer example (hex):**
```
...0700421b63343a636f6e74726f6c345f6c696768743a43342d415044313230...
```
Decodes to: `c4:control4_light:C4-APD120`

**Keypad example (hex):**
```
...0700421a63343a636f6e74726f6c345f6b703a43342d4b43313230323737...
```
Decodes to: `c4:control4_kp:C4-KC120277`

### Model String Format

```
c4:<device_type>:<model_number>
```

| Type Tag | Device |
|----------|--------|
| `control4_light` | Dimmer / switch (has load) |
| `control4_kp` | Keypad (no load, buttons + LEDs) |
| `control4_keypad` | Older keypad model (C4-KP6-Z, per GitHub issue) |

### Additional Identification Fields

The broadcast also contains firmware version and other attributes:

| Attribute | Dimmer (C4-APD120) | Keypad Dimmer (C4-KD120) | Keypad (C4-KC120277) |
|-----------|--------------------|-----------------------------|----------------------|
| Model | `c4:control4_light:C4-APD120` | `c4:control4_light:C4-KD120` | `c4:control4_kp:C4-KC120277` |
| Firmware | `5.1.1` | `5.1.1` | `4.4.16` |

### Runtime Identification Queries

If the broadcast isn't captured, these queries can differentiate at runtime:

| Query | Dimmer (APD120) | Keypad Dimmer (KD120) | Pure Keypad (KC120277) |
|-------|----------------|----------------------|----------------------|
| `c4.dmx.dim` | `000 c4.dmx.dim 02` | Responds (has load) | No response / error |
| `c4.dmx.ls` (telemetry) | Sent continuously | Sent continuously | Never sent |
| `c4.dmx.bp` (count) | `n01` (1 panel) | `n04`+ (multi-button) | `n06`+ (multi-button) |

---

## Factory Reset

**13-4-13 sequence:** Press top button 13 times, bottom button 4 times, top button 13 times. Device LEDs flash green to confirm. Device leaves current mesh and enters pairing mode.

**4-tap identify:** After reset, press top button 4 times. LEDs blink yellow, then turn blue when joined.

---

## Technical Notes

### Adapter Compatibility

- **EZSP (Silicon Labs):** Full support. Profile ID (0xC25C) can be set per-call in the adapter layer.
- **ZNP (Texas Instruments):** Receive-only for proprietary profile. Cannot send with custom profile ID per-call — it's set at endpoint registration time.

### ZCL Framing Bypass

The C4 protocol uses NO ZCL framing. In zigbee-herdsman, this is bypassed by calling `endpoint.sendRequest()` directly with a fake frame object whose `toBuffer()` returns raw ASCII bytes.

### Gamma Correction

C4 LEDs have a non-linear response. The C4 Director only sends pure channels (0x00 or 0xFF). For arbitrary colors via HA color picker, apply γ=2.0 gamma correction to all RGB channels before sending.

---

## Appendix: Composer Pro Properties → Protocol Mapping

Correlated from C4 Director join logs and Composer Pro UI for a C4-APD120 (MAC: 000fff0000cabd8d, firmware 5.1.1).

### Properties Tab

| Composer Pro Property | Protocol Command | Value |
|-----------------------|-----------------|-------|
| Default On Brightness: 100% | `c4.dm.tv 00 01 00000064` | Var 01 = 100 |
| Default Brightness Rate: 0.250s | `c4.dm.tv 00 02 000000fa` (or separate var) | 250ms |
| Click Rate Up: 0.250s | `c4.dm.tv 00 02 000000fa` | Var 02 = 250ms |
| Click Rate Down: 0.750s | `c4.dm.tv 00 03 000002ee` | Var 03 = 750ms |
| Hold Ramp Rate Up: 5.000s | `c4.dm.tv 00 04 00001388` | Var 04 = 5000ms |
| Hold Ramp Rate Down: 5.000s | `c4.dm.tv 00 05 00001388` | Var 05 = 5000ms |
| Min On: 1% | `c4.dm.tv 00 08 00000001` | Var 08 = 1 |
| Max On: 100% | `c4.dm.tv 00 06 00000064` | Var 06 = 100 |
| Cold Start Time: 0ms | `c4.dm.tv 00 09 00000000` | Var 09 = 0 |
| Cold Start Level: 0% | `c4.dm.tv 00 0a 00000000` | Var 0a = 0 |
| LED On Color (Top): white | `c4.dmx.led 01 03 ffffff` | |
| LED Off Color (Top): black | `c4.dmx.led 01 04 000000` | |
| LED On Color (Bottom): black | `c4.dmx.led 04 03 000000` | |
| LED Off Color (Bottom): blue | `c4.dmx.led 04 04 0000ff` | |

### Advanced Properties Tab

| Composer Pro Property | Protocol Command | Notes |
|-----------------------|-----------------|-------|
| Dimming Mode: Auto-Detect | `c4.dmx.plm 00` | `00` = auto-detect |
| Detected: Reverse Phase | `c4.dmx.dim` → `02` | `02` = reverse phase |
| Backlight Color: black | `c4.dmx.amb 01` → `00` | `00` = off/black |
| Ambient Light Profiles | `c4.als.sra` | Init ALS reporting |

### Button Settings Tab

| Composer Pro Property | Notes |
|-----------------------|-------|
| Buttons Control Load: checked | Default dimmer behavior |
| LEDs Follow Load: checked | LEDs change state based on load on/off |
| Use as 2 Button Keypad: **unchecked** | When checked, dimmer acts as keypad (no load control) |
| Button Style: Traditional | Cosmetic (C4 Director only) |
| Enable Auto Off: unchecked, 30s | Would use a timer command (TBD) |
| Turn on to previous level: unchecked | Affects power-on behavior |

The **"Use as 2 Button Keypad"** option is notable — it means the dimmer hardware can operate in keypad mode (buttons don't control load, just send events). This may change which `c4.dmx.*` events the device sends.

### Keypad Button Settings (C4-KC120277)

The keypad has a **modular button layout** — the 6-slot chassis is assembled from parts (1/2/3 Slot High, Rocker, Down/Up) via Composer Pro's drag-and-drop "Assembled Keypad" panel.

Each button has:
| Property | Notes |
|----------|-------|
| Name | Used for engraving text |
| LED Behavior | Dropdown (follow scene, always on, etc.) |
| On Color | LED color when button is active |
| Off Color | LED color when button is inactive |

Button IDs (`00`–`05`) correspond to physical slot positions, not logical buttons. A 2-slot button occupies two consecutive IDs. The Director sets LED colors per-slot using `c4.dmx.led <slot> <mode> <RRGGBB>`.

No dimming properties, no energy information, no load control — keypad is purely buttons + LEDs.

### Energy Information Tab (Dimmers Only)

| Composer Pro Property | Protocol Source | Notes |
|-----------------------|----------------|-------|
| Minutes Off: 1,070,901 | `c4.dmx.ls` telemetry | |
| Minutes On: 1,361,678 | `c4.dmx.ls` telemetry | |
| Minutes On Today: 802 | `c4.dmx.ls` telemetry | |
| Current Power: 36 watts | `c4.dmx.ls` telemetry | Field 5 (current) |
| Energy Used: 475.598 kWh | `c4.dmx.ls` telemetry | Field 8 (energy) |
| Energy Used Today: 198 Wh | `c4.dmx.ls` telemetry | |
| MAC: 000fff0000cabd8d | Zigbee IEEE address | |
| Version: 5.1.1 | Self-ID broadcast | Profile 0xC25D |
| Wiring Mode: Parallel | Physical wiring config | |

### Load Status Telemetry Decode (Revised)

Based on correlation with Composer Pro energy data:

```
c4.dmx.ls <load> <on/off> <level> <voltage> <current> <??> <temp> <energy> <runtime> <flags>
```

Example: `c4.dmx.ls 00 00 64 007a 0151 0023 0029 035e 000b 0000`

| Field | Hex | Decimal | Probable Meaning |
|-------|-----|---------|-----------------|
| load | 00 | 0 | Load index |
| on/off | 00 | 0 | 0=off, 1=on |
| level | 64 | 100 | Brightness % |
| voltage | 007a | 122 | Line voltage (V) |
| current | 0151 | 337 | Current draw (mA?) |
| field5 | 0023 | 35 | Power factor or watts? |
| temp | 0029 | 41 | Temperature (°C) |
| energy | 035e | 862 | Cumulative energy (units TBD) |
| runtime | 000b | 11 | Runtime (units TBD) |
| flags | 0000 | 0 | Status flags |

Note: Exact unit scaling for energy/runtime fields needs further correlation. The Composer Pro shows 475.598 kWh cumulative and 36W current power, but the raw telemetry values don't directly match — there may be a scaling factor or the values represent different time windows.
