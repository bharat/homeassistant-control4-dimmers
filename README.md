# Control4 Zigbee Dimmer Migration to Home Assistant / Zigbee2MQTT

Migrate ~30 Control4 Zigbee dimmers to Zigbee2MQTT without replacing hardware.

## TL;DR

Control4 dimmers are **standard Zigbee HA devices underneath the proprietary layer**.
Endpoint 1 speaks standard `genOnOff` (cluster 0x0006) and `genLevelCtrl`
(cluster 0x0008). On/off and dimming work with any Zigbee coordinator.
The proprietary C4 stuff lives on endpoint 0xC4 and is only needed for
button events (double/triple tap, etc.).

This has been proven working on SmartThings and Hubitat with custom drivers.
This project provides the equivalent for Zigbee2MQTT.

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
                    │  EP C4 (0xC25D Proprietary)   │ ◄── Button events (optional)
                    │  EP C5 (Proprietary, silent)  │
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

| Model | Type | Status |
|-------|------|--------|
| C4-DIM / LDZ-102-x | In-wall dimmer | Confirmed working (SmartThings/Hubitat) |
| C4-KD120 | Keypad dimmer 120V | Expected to work |
| C4-KD277 | Keypad dimmer 277V | Expected to work |
| C4-APD120 | Adaptive phase dimmer 120V | May need different handling (newer model) |
| C4-SW | In-wall switch (on/off only) | Expected to work (simpler) |

> **Note**: Newer "adaptive phase" dimmers (C4-APD series) may use an updated
> Zigbee stack. The process is the same but the fingerprint may differ. Test
> one unit first.

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

If the device shows as unsupported, you may need to manually patch the
database. Stop Zigbee2MQTT and edit `database.db`:

Find the line with your device's IEEE address and ensure:
```json
{
  "ieeeAddr": "0x000fff00XXXXXXXX",
  "modelID": "",
  "manufacturerName": "Control4",
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

If you get timeout errors, the `disableDefaultResponse: true` in the
converter should handle this. If not, verify the converter is loaded.

### Phase 3: Batch Migration

Once one dimmer works:

1. Reset dimmers in groups of 3-5 (keep some lights working!)
2. Enable Permit Join on Zigbee2MQTT
3. Factory reset each dimmer (13-4-13)
4. Wait for all to pair
5. Patch database.db if needed
6. Test each one
7. Rename devices in Z2M to match your room names

### Phase 4: Home Assistant Integration

With `homeassistant: true` in your Zigbee2MQTT config, devices
automatically appear in Home Assistant as `light.*` entities.

Migrate your automations from Control4 to HA. The C4 button events
(double/triple tap) are exposed as `action` attributes if the proprietary
endpoint works.

### Phase 5: Decommission Control4

Once all dimmers are migrated and stable:

1. Remove the Control4 director from your network
2. Optionally remove any C4 Zigbee range extenders (your Z2M mesh will
   need its own routing - the C4 dimmers themselves act as Zigbee routers)
3. Monitor your Zigbee mesh map in Z2M for healthy routing

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

### Only some dimmers work

- Older vs. newer C4 dimmer models may behave differently
- The C4-APD (adaptive phase) series is newer and may need a different
  fingerprint or cluster set
- Sniff traffic (see sniff-c4-traffic.md) to see what your model exposes

### Dimmer LED stays a certain color

- C4 dimmers use LED colors to indicate Zigbee network status
- After pairing with a new network, the LED behavior may change
- This is cosmetic and doesn't affect functionality

---

## How It Works (Technical Deep Dive)

### The Two Faces of a Control4 Dimmer

Every C4 Zigbee dimmer presents itself as a multi-endpoint Zigbee device:

**Endpoint 1** (Profile 0x0104 - Zigbee Home Automation):
- This is a standard, compliant Zigbee HA dimmable light
- Supports clusters: Basic (0x0000), Identify (0x0003), Groups (0x0004),
  Scenes (0x0005), On/Off (0x0006), Level Control (0x0008), Time (0x000A)
- Commands work exactly like any other Zigbee dimmer

**Endpoint 196 / 0xC4** (Profile 0xC25D - Control4 Proprietary):
- Custom C4 protocol using text-based commands
- Carries button events: `sa c4.dm.cc <button> <action>`
- Carries dimming events: `sa c4.dm.t0c <level>` (top) / `sa c4.dm.b0c <level>` (bottom)
- Only cluster: 0x0001

**Endpoint 197 / 0xC5** (Proprietary):
- Refuses to respond to simple descriptor requests
- Purpose unknown (possibly firmware update or internal C4 config)

### Why Control4 Locks You In

Control4 deliberately:
1. Returns empty `modelId` from `genBasic` cluster - breaks auto-discovery
2. Uses endpoints (196/197) that refuse standard Zigbee introspection
3. Doesn't send ZCL default responses - causes timeout errors
4. Wraps standard functionality in a proprietary profile layer

But they CAN'T hide the standard Zigbee HA endpoint (1) because the
Zigbee specification requires it for HA profile compliance. The standard
clusters respond to standard commands. They just made it hard to discover.

### The Network Key Problem (Non-Issue After Reset)

When devices are on a Control4 network, they use C4's network encryption
key. After factory reset (13-4-13), the device forgets the old key and
will accept a new one during pairing with your Zigbee2MQTT coordinator.
No key extraction is needed.

---

## Files in This Project

```
control4-zigbee-migration/
├── README.md                          # This file
├── external_converters/
│   └── control4-dimmer.mjs            # Zigbee2MQTT external converter
├── patch-database.sh                  # Helper to fix database.db entries
└── sniff-c4-traffic.md                # Guide to sniff C4 Zigbee traffic
```

## Credits

- **pstuart** - Original SmartThings C4 dimmer driver that proved standard
  Zigbee commands work on endpoint 1
- **iankberry** - Hubitat port confirming continued compatibility
- **samtherecordman** - Zigbee2MQTT issue #160 pioneer work with
  disableDefaultResponse discovery
- **Koenkk** - Zigbee2MQTT/zigbee-herdsman guidance on interview workarounds
