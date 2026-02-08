# Sniffing Control4 Zigbee Traffic (Optional Deep Dive)

If you want to fully reverse-engineer the proprietary C4 protocol on
endpoint 0xC4 (e.g., to capture all button events, LED control, etc.),
you can sniff the Zigbee traffic.

## Hardware Needed

- A spare Zigbee adapter for sniffing (CC2531, SONOFF Zigbee 3.0 dongle, etc.)
- Cannot be the same adapter running your coordinator

## Steps

### 1. Flash Sniffer Firmware

For CC2531: Flash the `CC2531ZNP-Prod.hex` sniffer firmware.
For EFR32-based dongles: Use `ember-zli` with the sniff command.

### 2. Determine the Zigbee Channel

Your Zigbee2MQTT coordinator operates on a specific channel (check your
`configuration.yaml` under `advanced.channel`, default is 11).

Control4 systems typically auto-select channels. If you're sniffing your
C4 network BEFORE migration, you'll need to find its channel:
- Check Composer Pro > Zigbee Configuration
- Or scan all channels 11-26 with your sniffer

### 3. Capture with Wireshark

```bash
# Using ember-zli (for EFR32 dongles)
ember-zli sniff --port /dev/ttyUSB1 --channel 11

# Using whsniff (for CC2531)
whsniff -c 11 | wireshark -k -i -
```

### 4. Decrypt Traffic

In Wireshark: Edit > Preferences > Protocols > ZigBee

**Trust Center Link Key** (well-known, used during pairing):
```
5A:69:67:42:65:65:41:6C:6C:69:61:6E:63:65:30:39
```

**Network Key**: You need this to decrypt ongoing traffic.
- For Zigbee2MQTT: Found in `configuration.yaml` under `advanced.network_key`
- For Control4: Extracted from the director (harder, may need Composer Pro access)

Add both keys in Wireshark's ZigBee protocol preferences.

### 5. Capture During Pairing

The most useful capture is during device pairing, when the network key
is transported encrypted with the link key. Wireshark can automatically
decrypt this if you have the Trust Center link key configured.

### 6. Analyze C4 Proprietary Frames

Filter for the C4 profile:
```
zbee_aps.profile == 0xc25d
```

The proprietary payload on endpoint 0xC4 uses a text-based protocol.
Look for strings like:
- `sa c4.dm.cc` - button/click commands
- `sa c4.dm.t0c` - top dimming events
- `sa c4.dm.b0c` - bottom dimming events

## What This Gets You

- Full understanding of every C4-proprietary message
- Ability to capture LED color/behavior commands
- Button press event details beyond single/double/triple tap
- Any C4-specific configuration parameters

For basic dimmer control (on/off/brightness), this sniffing is NOT
required - standard Zigbee HA clusters on endpoint 1 handle that.
