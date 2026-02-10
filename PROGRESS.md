# Migration Progress & Findings

---

## Roadmap

### Phase 1: Core Dimmer Support ✅ COMPLETE
- [x] Reverse-engineer the proprietary commands that Control4 sends to set LED colors
- [x] Decode protocol: raw ASCII over Profile 0xC25C, Cluster 1, Endpoint 1 (no ZCL framing)
- [x] Add `toZigbee` converter to set LED color via C4 text protocol
- [x] Expose LED control as Z2M MQTT commands (`c4_led` and `c4_cmd`)
- [x] LED behavior: top LED white when light is on, bottom LED blue when off (matches prior C4 behavior)

### Phase 2: Home Assistant Integration ✅ COMPLETE
- [x] Expose LED colors as native HA light entities with color pickers (4 entities per dimmer)
- [x] Multi-endpoint architecture: 5 entities per dimmer (main + 4 LED states)
- [x] Endpoint-scoped converter routing (LED converters before `light()` for correct dispatch)
- [x] Gamma correction (γ=2.0) for accurate LED color reproduction
- [x] Race condition fix: prioritize `meta.message` over stale `meta.state` for color presets
- [x] Factory default colors pre-populated (top=white on, bottom=blue off)
- [x] MQTT raw interface (`c4_led`, `c4_cmd`) retained alongside HA color pickers
- [ ] LED color restore on Z2M restart — Z2M persists state, but doesn't re-send to device on startup. Need to verify if C4 dimmer firmware remembers LED colors across power cycles.

### Phase 3: Streamlined Onboarding ✅ COMPLETE
- [x] Idempotent Python script (`scripts/fix-c4-database.py`) to fix interview state (legacy fallback)
  - Matches on `manufId: 43981` (not model ID or device type)
  - Dry-run by default, `--apply` to write
  - Creates timestamped backup before writing
- [x] **Live device metadata via MQTT** — `c4_identify` converter sets manufacturerName, powerSource, and interviewCompleted via `device.save()` while Z2M is running (no database patching needed)
- [x] **Auto-configure on pair** — `configure()` sets manufacturerName and powerSource automatically on first pair
- [x] Document end-to-end onboarding flow in README
- [x] Custom device icon (hosted on postimg.cc)
- [x] Root cause of interview failure identified: endpoints 196/197 refuse `simpleDescriptor` — cannot be fixed in converter, must patch database post-pairing
- [x] Old manual `jq`/`grep` patching replaced by automated script, then largely superseded by `c4_identify`

### Phase 4: Batch Migration 🔲 NOT STARTED
- [ ] Pair remaining ~29 dimmers (only Kitchen dimmer migrated so far)
- [ ] Verify `fix-c4-database.py` works at scale (batch of 5+ dimmers)
- [ ] Test LED color restore after Z2M restart and after power cycle
- [ ] Set LED colors for all dimmers (via HA automation or MQTT batch script)
- [ ] Rename all HA entities for clean display names
- [ ] Decommission Control4 Director once all dimmers are stable

### Phase 5: Keypad Support 🟡 IN PROGRESS
- [x] Protocol fully documented for all three device types (APD120, KD120, KC120277)
- [x] Decoded C4-KD120 Director join log — confirmed identical protocol to APD120 with 4 LED buttons
- [x] Built initial two-definition converter (dimmer + keypad)
- [x] Added `c4ButtonConfig()` factory for per-button behavior/LED-mode select entities
- [x] Enhanced `fzControl4Response` with button event parsing (bp/sc/cc) and smart load control
- [x] **Paired C4-KD120 — confirmed endpoints 1/196/197 (same as APD120)**
- [x] **Paired C4-KC120277 — confirmed endpoints 1/196/197 (NOT 1/2 as initially assumed)**
- [x] **CRITICAL FINDING: All newer C4 devices are endpoint-identical — fingerprinting cannot differentiate them**
- [x] **Unified single-definition architecture with runtime detection via C4 text protocol**
- [x] Response queue (`queryC4WithResponse`) for synchronous query/response during detection
- [x] `c4_detect` toZigbee converter: probes device type + reads stored LED colors
- [ ] Test unified converter with APD120 dimmer
- [ ] Test unified converter with KD120 keypad dimmer
- [ ] Test unified converter with KC120277 pure keypad
- [ ] Test button events (bp, sc, cc) reaching HA as action events
- [ ] Test LED color control per-button (modes 03/04/05)
- [ ] Test smart behavior: button press → genOnOff toggle on EP1
- [ ] Test `c4_detect` auto-population of stored LED colors
- [ ] Test with C4-KP6-Z (older model, `c4.kp.*` namespace) if available

### Phase 6: Upstream Contribution 🔲 NOT STARTED
- [ ] Clean up converter for general use (remove debug `console.error` calls)
- [ ] Submit PR to [zigbee-herdsman-converters](https://github.com/Koenkk/zigbee-herdsman-converters)
- [ ] Include `fingerprint: [{manufacturerID: 43981}]` matching strategy
- [ ] Document `disableDefaultResponse` requirement and interview quirks
- [ ] Reference existing community work (SmartThings/pstuart, Hubitat/iankberry, Z2M issue #160)
- [ ] The `sendRequest()` hack to bypass ZCL framing may need a cleaner upstream approach

---

## Session 7: 2026-02-10 — Unified Converter Architecture

### Goal
After discovering that all newer C4 devices have identical endpoint structures (1/196/197), redesign the converter from two separate definitions into one unified definition with runtime device-type detection.

### Critical Discovery: All Newer C4 Devices Are Endpoint-Identical

| Device | Endpoints (Actual) | Previously Assumed |
|--------|-------------------|-------------------|
| C4-APD120 (dimmer) | 1, 196, 197 | 1, 196, 197 ✓ |
| C4-KD120 (keypad dimmer) | 1, 196, 197 | 1, 196, 197 (TBC) ✓ |
| C4-KC120277 (pure keypad) | **1, 196, 197** | **1, 2** ✗ |

The assumption that the KC120277 had endpoints 1, 2 came from the older C4-KP6-Z (GitHub issue #15361, EM250-based). Live testing of the KC120277 confirmed it uses 1/196/197 — identical to dimmers. **Endpoint fingerprinting cannot differentiate ANY newer C4 devices.**

### New Architecture: Single Definition + Runtime Detection

**Before:** Two definitions (dimmer + keypad) matched by endpoint fingerprint.
**After:** One catch-all definition matched by `manufacturerID: 43981`, with runtime detection via C4 text protocol.

Detection matrix:
| `0g c4.dmx.led 02 03` | `0g c4.dmx.dim` | Device Type |
|------------------------|-----------------|-------------|
| No response | Responds | APD120 (dimmer, 2 buttons) |
| Responds with color | Responds | KD120 (keypad dimmer, 6 buttons) |
| Responds with color | No response | KC120277 (pure keypad, 6 buttons) |

### What Was Built

1. **Response queue mechanism** (`pendingQueries` Map + `queryC4WithResponse()`):
   - Enables synchronous query/response pattern within Z2M converters
   - Registers query by seq number, response handler resolves the Promise
   - `fzControl4Response` checks pending queries before regular parsing
   - Configurable timeout (default 3s) — resolves `null` on timeout

2. **`c4_detect` toZigbee converter**:
   - Probes device type using the detection matrix above
   - Reads all stored LED colors from firmware (`0g c4.dmx.led <btn> <mode>`)
   - Populates HA state with actual colors (no manual reconfiguration needed)
   - Stores `c4_device_type` in state (visible in HA)
   - One-time setup: `{"c4_detect": true}` after pairing

3. **Unified entity set** (superset of all device types):
   - 12 LED light entities (6 buttons × on/off)
   - 12 config select entities (6 buttons × behavior + LED mode)
   - 1 action entity (button press events)
   - 1 main dimmer light (for load control — harmless on keypads)
   - Users disable unused entities in HA based on detected device type

4. **Updated docs** — corrected endpoint data, added detection matrix, architecture section

### Breaking Change: Entity Naming
Dimmer LED entities renamed from `top_led_on/off`, `bottom_led_on/off` to `button_1_on/off`, `button_4_on/off` for consistency with the 6-slot numbering scheme. Existing HA automations referencing old entity names will need updating.

### Next Steps
- Test with APD120, KD120, KC120277
- Test `c4_detect` response queue mechanism
- Test LED color auto-population from firmware

---

## Session 6: 2026-02-09 — Keypad Converter & KD120 Protocol Decode

### Goal
Build the Z2M keypad converter definition with per-button LED entities, configuration selects, and button event handling. Decode the C4-KD120 (Keypad Dimmer) Director join log to understand the three-device-type landscape.

### Three Device Types Identified

| Model | Type Tag | Has Load | Buttons | Z2M Fingerprint |
|-------|----------|----------|---------|-----------------|
| C4-APD120 | `control4_light` | Yes | 2 (rocker) | `{manufID: 43981}` (catch-all) |
| C4-KD120 | `control4_light` | Yes | 6 (rocker + keypad) | Same as APD120 |
| C4-KC120277 | `control4_kp` | No | 6 slots | Same as APD120 (corrected in Session 7) |

### KD120 Protocol Findings (from Director Log)
- **Self-ID**: `c4:control4_light:C4-KD120`, firmware `5.1.1` — same type tag as APD120
- **Join sequence**: Nearly identical to APD120 — same commands, same order
- **LED buttons**: 01, 02, 03, 04 (4 buttons vs APD120's 2). Button 00 is the top rocker.
- **LED mode**: Director uses mode 05 (override) for ALL KD120 LEDs, not 03/04 like APD120
- **New commands**: `c4.dm.sl` (returns `00`, meaning TBD), `c4.dmx.pwr` as GET (returns power data)
- **Load**: Has load — sends `c4.dmx.ls` telemetry and has full dimming table

### What Was Built (Later Superseded by Session 7)

1. **Two-definition converter** (dimmer + keypad) — later merged into unified definition
2. **`c4ButtonConfig()` factory**: Creates per-button select entities
3. **Enhanced `fzControl4Response`**: Button event parsing + smart load control
4. **Updated documentation**: Protocol docs updated with KD120 data

---

## Session 5: 2026-02-09 — Device Probing & Protocol Documentation

### Goal
Build tooling to probe Control4 devices for identification (dimmer vs. keypad), reverse-engineer the full join protocol from C4 Director logs, and document all findings.

### What Was Built

#### 1. Device Probing Toolkit

Added three new `toZigbee` converters to `control4-dimmer.mjs`:
- **`tzControl4Query`** — Send C4 GET queries via MQTT (`c4_query` key)
- **`tzControl4ZclRead`** — Read arbitrary ZCL cluster attributes (`zcl_read` key), with one-by-one fallback when batch read fails due to `UNSUPPORTED_ATTRIBUTE`
- **`tzControl4Probe`** — Comprehensive probe: enumerates endpoints, clusters, and attempts genBasic attribute reads

Added `fzControl4Response` fromZigbee converter to capture raw C4 text protocol responses.

#### 2. Interactive Probe Script (`scripts/probe-device.py`)

Python script for interactive device interrogation via Z2M's MQTT API:
- Commands: `probe`, `read`, `query`, `cmd`, `raw`, `state`, `debug`, `docker-test`
- MQTT authentication support (`-u` / `-P`)
- Command history via `readline`
- **Docker log capture** (`--docker` flag): Tails Z2M Docker logs in background, filters for C4 profile (0xC25C) frames, decodes hex payloads to ASCII, and displays responses inline — no manual log forensics needed

#### 3. Protocol Documentation

Created comprehensive documentation in `docs/`:
- **`docs/c4-protocol.md`** — Complete C4 text protocol reference (all known commands, packet types, response codes, transport layer details)
- **`docs/device-identification.md`** — Device comparison (dimmer vs. keypad), endpoint fingerprints, Director join sequences, LED button numbering

### Key Discoveries

#### Device Self-Identification
C4 devices broadcast their model on profile 0xC25D after joining:
- Dimmer: `c4:control4_light:C4-APD120` (firmware 5.1.1)
- Keypad: `c4:control4_kp:C4-KC120277` (firmware 4.4.16)

#### Endpoint Fingerprints (CORRECTED in Session 7)
- **All newer C4 devices (APD120, KD120, KC120277):** Endpoints 1, 196, 197
- **Older keypad (C4-KP6-Z):** Endpoints 1, 2
- ~~Keypad: Endpoints 1, 2~~ — this was based on C4-KP6-Z data; live KC120277 has 1/196/197

#### Shared Command Namespace
Both dimmers and keypads (at least the C4-KC120277) use `c4.dmx.*` commands. The older C4-KP6-Z uses `c4.kp.*` (per GitHub issue #15361).

#### New Commands Discovered from Director Logs
| Command | Purpose |
|---------|---------|
| `c4.dmx.off <load>` | Turn off load |
| `c4.dmx.pwr <val>` | Power configuration |
| `c4.dmx.plm <val>` | Power line mode |
| `c4.dmx.pmti <a> <b>` | Power measurement timer |
| `c4.dmx.dim` | Dimmer type query (dimmer-only) |
| `c4.dm.tv <load> <var> [val]` | Dimming table values (ramp rates, min/max) |
| `c4.sy.zpw <val>` | ZigBee power mode |
| `c4.als.sra` | Ambient light sensor init |
| `c4.dmx.ls` | Load status telemetry (voltage, current, temp, energy — dimmer-only) |

#### genBasic Attributes — Fully Locked Down
All `genBasic` attributes return `UNSUPPORTED_ATTRIBUTE` on C4 devices. Device identification must use the proprietary C4 protocol, not standard ZCL.

#### Live Device Metadata (`c4_identify`)
Created `tzControl4Identify` toZigbee converter that sets device metadata via `device.save()` while Z2M is running:
- `device.manufacturerName = 'Control4'`
- `device.powerSource = 'Mains (single phase)'`
- `device.interviewCompleted = true`

Triggered via MQTT: `{"c4_identify": true}` on `zigbee2mqtt/<device>/set`. Values persist to `database.db` and show on the About page after one Z2M restart (frontend caches the devices list on startup).

Also added auto-setup in `configure()` — sets manufacturerName and powerSource on first pair without any manual intervention.

**Firmware ID** remains "Unknown" — the firmware version (e.g., "5.1.1") is only available from the 0xC25D self-ID broadcast, which herdsman doesn't route to converters. No known C4 text protocol command returns it. Accepted as cosmetic-only limitation.

#### Composer Pro Property Correlation
Decoded the full `c4.dm.tv` dimming table from Director logs and correlated with Composer Pro UI:
- Var 01 = Default On Brightness, Var 02 = Click Rate Up, Var 03 = Click Rate Down
- Var 04/05 = Hold Ramp Rate Up/Down, Var 06 = Max On, Var 08 = Min On
- Var 09/0a = Cold Start Time/Level

Also documented keypad modular button layout (6-slot chassis with configurable parts) and the "Use as 2 Button Keypad" mode for dimmers.

### Errors Encountered & Resolved

| Error | Cause | Fix |
|-------|-------|-----|
| `probe-device.py` timeout on all commands | MQTT broker requires authentication | Added `--username` and `--password` arguments |
| `zcl_read genBasic` batch read fails | C4 returns `UNSUPPORTED_ATTRIBUTE` for all genBasic attributes | Implemented one-by-one fallback in `tzControl4ZclRead` |
| `c4_query` shows "No response captured" | `fzControl4Response` not firing for profile 0xC25C frames | Added `DockerResponseCapture` class to tail Docker logs and extract responses directly |
| Docker capture regex not matching | Pattern used `INCOMING_MESSAGE_HANDLER` (all caps) but actual log line is `ezspIncomingMessageHandler` (camelCase) | Fixed regex to match `profileId` + `messageContents` loosely |
| Docker capture getting stale history | `docker logs --since 1s` replayed old lines | Changed to `--tail 0` (stream new only) |
| Power shows `?` on About page after `c4_identify` | Z2M caches `bridge/devices` on startup; `device.save()` persists but frontend doesn't refresh | One Z2M restart after running `c4_identify` picks up the persisted values |

---

## Session 4: 2026-02-08 — Home Assistant Integration & Polish

### Goal
Expose LED colors as native Home Assistant light entities with color pickers, fix the Z2M interview warning, and polish the overall experience.

### What Was Built

#### 1. LED Light Entities for Home Assistant

Each dimmer now exposes **5 entities** in HA via Z2M auto-discovery:

| Entity | Endpoint | Purpose |
|--------|----------|---------|
| `light.<name>` | default | Main dimmer (on/off + brightness) |
| `light.<name>_top_led_on` | top_led_on | Top LED color when load is ON |
| `light.<name>_top_led_off` | top_led_off | Top LED color when load is OFF |
| `light.<name>_bottom_led_on` | bottom_led_on | Bottom LED color when load is ON |
| `light.<name>_bottom_led_off` | bottom_led_off | Bottom LED color when load is OFF |

Architecture: a `c4LedLight()` factory function creates a `ModernExtend`-compatible object per LED state, each with its own `Light` expose (brightness + HS color) and endpoint-scoped `toZigbee` converter. These are listed before `light()` in the `extend` array so endpoint-restricted converters are checked first.

#### 2. Gamma Correction

C4 LEDs have a non-linear response — low RGB channel values produce disproportionate light, washing out saturated colors (e.g. blue appeared grayish-white). The C4 Director only ever sends pure channels (0x00 or 0xFF).

Applied γ=2.0 gamma correction to all RGB channel values before sending to device. This compresses low values, making the full color range usable from HA's color picker.

#### 3. Race Condition Fix

HA color presets send `state`, `brightness`, and `color` in the same MQTT message. Z2M calls `convertSet` for each key separately, but the color handler hasn't updated `meta.state` by the time the state/brightness handler runs. Fix: check `meta.message` (the full incoming MQTT payload) for color values, prioritizing it over potentially stale `meta.state`.

#### 4. Interview State Fix Script

Created `scripts/fix-c4-database.py`:
- Matches C4 devices by `manufId: 43981` (not model ID or device type)
- Sets `interviewState: "SUCCESSFUL"` and `interviewCompleted: true`
- Dry-run by default (`--apply` to actually write)
- Creates timestamped backup before writing
- Idempotent — safe to run repeatedly after each batch of pairings

This replaced a broken bash script that was matching all 27 devices instead of just C4 dimmers (it matched on `type == 'Router'` OR empty `modelID`).

#### 5. Entity Naming

Z2M 2.7 derives HA entity names for `light` entities from the endpoint name (e.g. `top_led_on` → "Top_led_on"). The `withLabel()` API does **not** control light entity names — only generic exposes (binary/numeric/enum). Manual rename in HA is required for clean display names.

Naming convention settled on: endpoint IDs use `top_led_on` / `bottom_led_off` pattern, which puts the meaningful part (position + state) first.

#### 6. Device Icon

The converter includes an `icon` field pointing to an externally hosted image (postimg.cc) since the GitHub repo is private and raw URLs contain expiring tokens.

### Errors Encountered & Resolved

| Error | Cause | Fix |
|-------|-------|-----|
| Double-suffixed state keys (`brightness_led_top_on_led_top_on`) | `convertSet` returning suffixed keys, Z2M adding suffix again | Return base keys (`state`, `brightness`, `color`); let Z2M suffix once |
| HA presets turn LED white, color wheel works | Race condition: `meta.state` has old color when state/brightness handler runs | Read color from `meta.message` (full MQTT payload) instead of `meta.state` |
| Blue appears grayish-white | C4 LED non-linear response; low channel values (0x18) too bright | Gamma correction γ=2.0 on all RGB channels |
| `withLabel()` has no effect on light entities | Z2M 2.7 only uses `withLabel()` for generic exposes, not lights | Removed; documented as manual rename in HA |
| Database fix script matched all 27 devices | Bash script matched `type==Router` OR empty `modelID` | Rewrote in Python, matching on `manufId == 43981` only |
| Interview still shows "failed" after converter fix attempts | `device.interviewCompleted = true` in `configure()` doesn't persist | External Python script patches `database.db` directly |
| GitHub raw URL for icon expires | Private repo, token in URL | Hosted on postimg.cc |

---

## Session 3: 2026-02-08 — LED Control Breakthrough

### Goal
Reverse-engineer the Control4 proprietary LED control protocol and implement it in the Z2M converter.

### Test Setup
- **Z2M device:** Kitchen dimmer (C4-APD120, IEEE 0x000fff0000c55f83)
- **Reference device:** Island dimmer (still on C4 network, used for Composer Pro log capture)
- **Log source:** Composer Pro `zap.log` on the C4 Director (mounted via SMB)

### Key Discoveries

#### 1. C4 Text Protocol (Complete Decode)

Control4 dimmers use a **text-based command protocol** sent as raw ASCII bytes in the APS payload — with **NO ZCL framing** (no frame control, sequence number, or command ID header). This is unique among Zigbee devices.

**Transport:**
| Field | Value |
|-------|-------|
| Profile | 0xC25C (49756) — "C4 MIB" |
| Cluster | 0x0001 |
| Source Endpoint | 1 (coordinator) |
| Destination Endpoint | 1 (device) |
| Responses from | Endpoint 197 (0xC5) → Endpoint 197 |

**Command format:**
```
0s<seq_hex4> <command> <params>\r\n    — SET (write a value)
0g<seq_hex4> <command> <params>\r\n    — GET (read a value)
0r<seq_hex4> 000 [data]\r\n            — Response: success
0r<seq_hex4> v01\r\n                   — Response: invalid value/parameter
0r<seq_hex4> e00\r\n                   — Response: parameter error (missing args)
0t<seq_hex4> sa <command> <data>\r\n   — Telemetry: unsolicited status report
```

**Sequence number:** 4-digit hex counter (e.g., `a9c8`), incremented per command. The device echoes it in the response for request-response correlation.

#### 2. LED Command: `c4.dmx.led`

```
c4.dmx.led <led_id> <mode> <rrggbb>
```

| Parameter | Values |
|-----------|--------|
| `led_id` | `01` = top button LED, `04` = bottom button LED |
| `mode` | `03` = ON color (shown when load is on), `04` = OFF color (shown when load is off) |
| `rrggbb` | 6-digit hex RGB color (`ffffff`=white, `000000`=off/dark, `0000ff`=blue) |

**C4 Director always sends all 4 combinations as a group:**
```
c4.dmx.led 01 03 ffffff    — top LED white when ON
c4.dmx.led 01 04 000000    — top LED dark when OFF
c4.dmx.led 04 03 000000    — bottom LED dark when ON
c4.dmx.led 04 04 0000ff    — bottom LED blue when OFF
```

#### 3. Full Command Catalog (from zap.log)

| Command | Direction | Description |
|---------|-----------|-------------|
| `c4.dmx.led` | Outgoing (0s) | Set LED color (per button, per on/off state) |
| `c4.dmx.pwr` | Outgoing (0s) | Set power/dim level (e.g., `b5` ≈ 71%) |
| `c4.dmx.off` | Outgoing (0s) | Turn off (e.g., `0000`) |
| `c4.dmx.amb` | Both (0g/0s) | Query/set ambient LED mode (`0g` to query, value `00`=off) |
| `c4.dmx.ls` | Incoming (0t) | Light status telemetry (periodic, 10+ fields) |
| `c4.dmx.key` | Incoming (0t) | Button/key press events |
| `c4.dmx.bp` | Incoming (0t) | Button press events |
| `c4.dmx.cc` | ? | Config change? |
| `c4.dmx.hc` | ? | Unknown |
| `c4.dmx.he` | ? | Unknown |
| `c4.dmx.plm` | ? | Unknown |
| `c4.dmx.pmti` | ? | Unknown |
| `c4.dmx.sc` | ? | Scene? |

#### 4. Implementation: Bypassing ZCL Framing

The biggest technical challenge: zigbee-herdsman's `endpoint.command()` always wraps payloads in a ZCL header (frame control + sequence + command ID). The C4 protocol uses NO ZCL framing — just raw ASCII.

**Solution:** Call the private `endpoint.sendRequest()` method directly (TypeScript's `private` is not enforced at runtime) with a fake frame object whose `toBuffer()` returns just the raw ASCII bytes:

```javascript
const frame = {
    cluster: {ID: 1, name: 'c4Mib'},
    command: {ID: 0x35, name: 'c4TextCmd'},
    header: { transactionSequenceNumber: seq & 0xFF, ... },
    toBuffer: () => Buffer.from('0s0001 c4.dmx.led 01 03 ffffff\r\n', 'ascii'),
};
await ep.sendRequest(frame, {profileId: 0xC25C, disableResponse: true, ...});
```

This sends raw bytes as the APS payload while still using herdsman's routing, retries, and coordinator management.

### Errors Encountered & Resolved

| Error | Cause | Fix |
|-------|-------|-----|
| `Cluster 'c4Mib' does not exist` | `definition.customClusters` not auto-applied to device | Mutated `device.customClusters` directly |
| `Cannot set property customClusters which has only a getter` | `device.customClusters` is read-only | Used `device.customClusters.c4Mib = ...` to mutate the returned object |
| LED command accepted (000) but ZCL-framed — no visual change | `endpoint.command()` adds 3-byte ZCL header that device can't parse | Bypassed with `sendRequest()` + fake frame (no ZCL framing) |
| Modes 00/01/02 rejected with `v01` | Invalid mode values | Only modes `03` (ON color) and `04` (OFF color) are valid |
| LED command sent but "nothing happened" | Was looking at the wrong dimmer (Island vs Kitchen!) | Commands were working all along — verified with 4-command batch |

---

## Session 2: 2026-02-08 — Successful Prototype

### Test Device
- **Model:** C4-APD120 (Adaptive Phase Dimmer 120V)
- **IEEE:** 0x000fff0000c55f83
- **Network address:** 11023 (0x2B0F)
- **Manufacturer ID:** 43981 (0xABCD)
- **Friendly name:** Kitchen

### What Worked
1. **Factory reset (13-4-13):** Successful — device left C4 mesh (double green LEDs confirmed)
2. **Pairing:** Device joined Z2M network immediately after permit join enabled
3. **Converter matching via `manufacturerID` fingerprint:** The key breakthrough from Session 1. Using `fingerprint: [{manufacturerID: 43981}]` reliably matches the device even with a failed interview and absent `modelID`
4. **On/Off control:** Works via Z2M dashboard and MQTT
5. **Dimming:** Works via Z2M dashboard and MQTT
6. **`modernExtend` with `light()`:** Provides clean exposes (state, brightness, color_temp) with minimal custom code
7. **`disableDefaultResponse: true`:** Eliminates timeout errors — C4 devices don't send ZCL default responses
8. **Custom `configure` function:** Only binds endpoint 1 clusters, avoids touching proprietary endpoints 196/197

### Key Lessons Learned (across both sessions)
1. **`modelID` is absent after failed interview** — not empty string, completely missing from database. `zigbeeModel` matching cannot work. Use `manufacturerID` fingerprint instead.
2. **Z2M does not re-evaluate converters for existing devices.** After changing/fixing the converter, you must force-remove the device and re-pair (13-4-13 again) for the new converter to be applied.
3. **Z2M overwrites `database.db` on shutdown.** Always stop Z2M before patching, then start it. Never patch while running.
4. **Failed interview corrupts cluster lists** — `inClusterList` gets filled with `65535` garbage values. Must be manually corrected.
5. **Endpoint 196 cluster 1 is NOT `genPowerCfg`** — it's a proprietary C4 cluster. If Z2M sees it, it tries battery reporting on a mains-powered dimmer and fails. Clear it from `inClusterList`.
6. **`light({configureReporting: false})` is essential** — prevents Z2M from trying to configure reporting on clusters/endpoints that don't support it.

### What Didn't Work / Still Open
1. ~~**LEDs:** Top and bottom LEDs are dim blue instead of white. LED color control requires proprietary C4 commands~~ → **Resolved in Session 3**
2. ~~**Interview still fails** — endpoints 196/197 refuse simpleDescriptor requests~~ → **Resolved in Session 4** with `fix-c4-database.py`
3. **Firmware ID:** Shows "Unknown" in Z2M dashboard (cosmetic, non-blocking)

---

## Session 1: 2026-02-07 — Initial Exploration

### Test Device
- **Model:** C4-APD120 (Adaptive Phase Dimmer 120V)
- **IEEE:** 0x000fff0000cabd9b
- **Network address:** 63093 (0xF675)
- **Manufacturer ID:** 43981 (0xABCD)

### What Worked
1. **Factory reset (13-4-13):** Successful — device left C4 mesh
2. **Pairing:** Device joined Z2M network and got a network address
3. **Basic communication:** Device responded to ZCL commands (genIdentify returned UNSUPPORTED_ATTRIBUTE — proving reachability)
4. **On/Off control:** Light physically toggled on and off via Z2M dev console (using auto-generated definition)
5. **Routing:** Initially flaky (ROUTE_ERROR_MANY_TO_ONE_ROUTE_FAILURE), but resolved on its own after a few minutes

### What Didn't Work
1. **Interview:** Failed repeatedly with "Delivery failed" routing errors
2. **External converter matching:** The converter loaded but never matched — `modelID` was absent from DB, `zigbeeModel` matching couldn't work
3. **Z2M overwrites database:** Manual patches reverted on shutdown

### Key Discovery
The `manufacturerID` (43981 / 0xABCD) is ALWAYS available in `database.db` even after a failed interview. This became the reliable matching strategy used in Session 2.

---

## Environment
- Z2M version: 2.7.0
- zigbee-herdsman: 7.0.1
- zigbee-herdsman-converters: 25.80.0
- Coordinator: EmberZNet 8.0.2 (EZSP protocol v14)
- Coordinator IEEE: 0x7cc6b6fffe9b1368
- Setup: Docker Compose on host "generosity"
- Z2M data directory: `/data/misc/menalto-services/zigbee/zigbee2mqtt/data`
- MQTT broker: mosquitto at 192.168.1.54:1883
