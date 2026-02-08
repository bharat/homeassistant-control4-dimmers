# Migration Progress & Findings

---

## Roadmap

### Phase 1: LED Control ✅ COMPLETE
- [x] Reverse-engineer the proprietary commands that Control4 sends to set LED colors
- [x] LED behavior: top LED white when light is on, bottom LED blue when off (matches prior C4 behavior)
- [x] Add `toZigbee` converter to set LED color via C4 text protocol
- [x] Expose LED control as Z2M MQTT commands (`c4_led` and `c4_cmd`)
- [x] Decode protocol: raw ASCII over Profile 0xC25C, Cluster 1, Endpoint 1 (no ZCL framing)
- [ ] Expose LED colors as proper Z2M `exposes` entries for Home Assistant UI integration
- [ ] Handle LED color restore on Z2M startup (persist and re-apply after reboot)

### Phase 2: Keypad Support
- [ ] Test pairing a C4 keypad (C4-KD120 or similar) with Z2M using the existing converter
- [ ] Map keypad button presses to Z2M actions (incoming `c4.dmx.key` / `c4.dmx.bp` events)
- [ ] LED control per-button on keypads (different colors per button for room/scene identification)
- [ ] Expose keypad LED colors to Home Assistant for dynamic control (e.g. set button 3 to green when scene is active)
- [ ] May need a separate converter definition for keypads vs. plain dimmers

### Phase 3: Streamlined Onboarding
- [ ] Eliminate or minimize database patching — investigate why Z2M interview fails and whether we can make it succeed (or at least not corrupt clusters)
- [ ] If patching remains necessary: create a single idempotent `patch-all.sh` script that:
  - Finds all Control4 devices in `database.db` (by `manufId: 43981`)
  - Patches each one with correct `inClusterList`, `outClusterList`, `modelID`, `manufacturerName`, `interviewCompleted`
  - Is safe to re-run at any time (idempotent)
  - Can be run after onboarding a batch of new dimmers
- [ ] Document the end-to-end onboarding flow: disconnect from C4 → 13-4-13 → pair → (patch if needed) → set LEDs → verify
- [ ] Update `README.md` with battle-tested instructions from real onboarding experience

### Phase 4: Upstream Contribution
- [ ] Clean up converter for general use (remove any hardcoded paths, personal device references)
- [ ] Submit PR to [zigbee-herdsman-converters](https://github.com/Koenkk/zigbee-herdsman-converters) to add built-in Control4 dimmer support
- [ ] Include `fingerprint: [{manufacturerID: 43981}]` matching strategy (proven to work)
- [ ] Document the `disableDefaultResponse` requirement and interview quirks
- [ ] Reference existing community work (SmartThings driver by pstuart, Hubitat port, Z2M issue #160)
- [ ] Include LED control in the PR (the `sendRequest` hack may need a cleaner upstream approach)

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

### What's Working Now
1. **On/Off control** via standard Zigbee HA (endpoint 1)
2. **Dimming** via standard Zigbee HA (endpoint 1)
3. **LED color control** via C4 text protocol:
   - Set top/bottom LED colors for on/off states
   - Batch mode (all 4 at once) and single LED mode
   - Raw command interface for experimentation (`c4_cmd`)
4. **Clean Z2M dashboard** (no warnings, custom icon)

### MQTT Interface

**Set all 4 LEDs at once (recommended):**
```json
{"c4_led": {"top_on": "ffffff", "top_off": "000000", "bottom_on": "000000", "bottom_off": "0000ff"}}
```

**Set a single LED:**
```json
{"c4_led": {"led": "top", "color": "ffffff", "mode": "on"}}
{"c4_led": {"led": "bottom", "color": "0000ff", "mode": "off"}}
```

**Raw C4 text command (for experimentation):**
```json
{"c4_cmd": "c4.dmx.led 01 03 ff0000"}
{"c4_cmd": "c4.dmx.pwr b5"}
```

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
9. **Database patching for clean dashboard:**
   - `interviewCompleted: true` + `interviewState: null` → green checkmark (no red warning triangle)
   - Corrected `inClusterList: [0,3,4,5,6,8,10]` and `outClusterList: []` for endpoint 1
   - Set `modelID: "C4-APD120"` and `manufacturerName: "Control4"` for display
10. **Custom device icon:** Placed PNG in `device_icons/C4-APD120.png`, set in Z2M config

### Key Lessons Learned (across both sessions)
1. **`modelID` is absent after failed interview** — not empty string, completely missing from database. `zigbeeModel` matching cannot work. Use `manufacturerID` fingerprint instead.
2. **Z2M does not re-evaluate converters for existing devices.** After changing/fixing the converter, you must force-remove the device and re-pair (13-4-13 again) for the new converter to be applied.
3. **Z2M overwrites `database.db` on shutdown.** Always stop Z2M before patching, then start it. Never patch while running.
4. **Failed interview corrupts cluster lists** — `inClusterList` gets filled with `65535` garbage values. Must be manually corrected.
5. **Endpoint 196 cluster 1 is NOT `genPowerCfg`** — it's a proprietary C4 cluster. If Z2M sees it, it tries battery reporting on a mains-powered dimmer and fails. Clear it from `inClusterList`.
6. **`light({configureReporting: false})` is essential** — prevents Z2M from trying to configure reporting on clusters/endpoints that don't support it.

### What Didn't Work / Still Open
1. ~~**LEDs:** Top and bottom LEDs are dim blue instead of white. LED color control requires proprietary C4 commands~~ → **Resolved in Session 3**
2. **Interview still fails** — endpoints 196/197 refuse simpleDescriptor requests, which aborts the interview. Database patching is still required.
3. **Firmware ID:** Shows "Unknown" in Z2M dashboard (cosmetic, non-blocking)

### Current Converter (working)
See `external_converters/control4-dimmer.mjs` — uses `modernExtend` `light()`, fingerprint on `manufacturerID: 43981`, custom configure for endpoint 1 only, plus C4 text protocol for LED control.

### Database Patch Required After Pairing
Stop Z2M, then patch the device entry (replace `XXXXXX` with device IEEE suffix):
```bash
grep 'XXXXXX' database.db | jq -c '
  .interviewCompleted = true |
  .interviewState = null |
  .modelID = "C4-APD120" |
  .manufacturerName = "Control4" |
  .endpoints."1".inClusterList = [0,3,4,5,6,8,10] |
  .endpoints."1".outClusterList = []
' > /tmp/patched_line.json

python3 -c "
old = open('database.db').readlines()
patch = open('/tmp/patched_line.json').read().strip()
out = [patch + '\n' if 'XXXXXX' in l else l for l in old]
open('database.db', 'w').writelines(out)
"
```

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
