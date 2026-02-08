# Migration Progress & Findings

## Session: 2026-02-07

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
1. **Interview:** Failed repeatedly with "Delivery failed" routing errors. Even when the device was reachable for single commands, the multi-step interview process failed.
2. **External converter matching:** The converter loaded successfully but NEVER matched the device, despite:
   - Setting `modelID: "C4-APD120"` in database.db
   - Setting `manufacturerName: "Control4"` in database.db
   - Setting `interviewCompleted: true` in database.db
   - Using both `zigbeeModel` and `fingerprint` matching
   - Testing with a minimal zero-import converter
   - Saving converter via Z2M web UI (confirmed loaded at runtime)
3. **Z2M overwrites database:** Z2M writes its in-memory device state to database.db on shutdown, reverting our manual patches (modelID, manufacturerName go back to null)

### Key Discovery: Converter Matching Issue
The core unsolved problem is that Z2M 2.7.0 (zigbee-herdsman-converters v25.80.0) does not match external converters to devices that originally had empty/failed modelID, even after manually patching the database. The device always shows as "(Automatically generated definition)".

**Evidence that the converter DID partially match on one attempt:**
- Z2M logged `Configuring '0x000fff0000cabd9b'` during one restart
- The configure failed because it tried `genPowerCfg.configReport` on endpoint 196 (the proprietary C4 endpoint had cluster 1 which Z2M interpreted as genPowerCfg)

### Open Questions for Next Session
1. **Why doesn't zigbeeModel matching work?** The database has `modelID: "C4-APD120"` and the converter has `zigbeeModel: ['C4-APD120']`. Need to check what `device.modelID` actually returns in Z2M's runtime (via MQTT bridge/devices topic).
2. **Does Z2M cache device definitions?** Maybe once a device is assigned the auto-generated definition, Z2M never re-evaluates it even when external converters are loaded/updated.
3. **Would deleting and re-adding work?** Remove the device from Z2M, restart with converter loaded, then either re-pair or manually add the database entry.
4. **Alternative: Use zigbee-herdsman-converters PR?** Submit the converter as a built-in (not external) so it loads before device enumeration.

### Database Patch Reference

Correct database entry for a C4-APD120 (stop Z2M before editing):
```json
{
  "modelID": "C4-APD120",
  "manufacturerName": "Control4",
  "type": "Router",
  "interviewCompleted": true,
  "endpoints": {
    "1": {
      "profId": 260,
      "epId": 1,
      "devId": 257,
      "inClusterList": [0, 3, 4, 5, 6, 8, 10],
      "outClusterList": []
    },
    "196": {
      "profId": 49757,
      "epId": 196,
      "devId": 1,
      "inClusterList": [],
      "outClusterList": []
    },
    "197": {
      "epId": 197,
      "inClusterList": [],
      "outClusterList": []
    }
  }
}
```

**Important:** Do NOT put cluster 1 in endpoint 196's inClusterList — Z2M interprets it as genPowerCfg and tries battery reporting on the proprietary endpoint.

### Environment
- Z2M version: 2.7.0
- zigbee-herdsman: 7.0.1
- zigbee-herdsman-converters: 25.80.0
- Coordinator: EmberZNet 8.0.2 (EZSP protocol v14)
- Coordinator IEEE: 0x7cc6b6fffe9b1368
- Setup: Docker Compose on host "generosity"
- MQTT broker: mosquitto at 192.168.1.54:1883
