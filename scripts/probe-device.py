#!/usr/bin/env python3
"""
Interactive probe for Control4 Zigbee devices via Zigbee2MQTT MQTT API.

Sends commands to a device and listens for results in the device state.
Useful for identifying device type, reading attributes, and experimenting
with C4 text protocol queries — without redeploying the converter.

Requires: paho-mqtt  (pip install paho-mqtt)

Usage:
  python3 probe-device.py <device_name> [--broker HOST] [--port PORT]

  # Run all standard probes
  python3 probe-device.py Kitchen

  # Interactive mode — send arbitrary commands
  python3 probe-device.py Kitchen --interactive

  # Just read genBasic attributes
  python3 probe-device.py Kitchen --zcl-read genBasic

  # Send a C4 GET query
  python3 probe-device.py Kitchen --c4-query "c4.dmx.amb 01"
"""

import argparse
import json
import sys
import time
import threading

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print('Error: paho-mqtt not installed. Run: pip install paho-mqtt', file=sys.stderr)
    sys.exit(1)


# ─── MQTT helpers ────────────────────────────────────────────────────

class DeviceProber:
    def __init__(self, device_name, broker='localhost', port=1883):
        self.device = device_name
        self.base_topic = f'zigbee2mqtt/{device_name}'
        self.set_topic = f'{self.base_topic}/set'
        self.state = {}
        self.last_response = None
        self.response_event = threading.Event()

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(broker, port, keepalive=60)
        self.client.loop_start()

        # Wait for connection
        time.sleep(0.5)

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        # Subscribe to device state topic
        client.subscribe(self.base_topic)
        # Also subscribe to bridge responses for errors
        client.subscribe('zigbee2mqtt/bridge/response/#')

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        if msg.topic == self.base_topic:
            self.state.update(payload)

            # Check for probe results
            if 'probe_result' in payload:
                self.last_response = payload['probe_result']
                self.response_event.set()
            elif 'c4_response' in payload:
                self.last_response = payload['c4_response']
                self.response_event.set()

        elif msg.topic.startswith('zigbee2mqtt/bridge/response/'):
            # Bridge error responses
            if payload.get('status') == 'error':
                print(f'  Bridge error: {payload.get("error", "unknown")}')

    def send(self, payload, wait=3.0):
        """Send a command and wait for a response."""
        self.response_event.clear()
        self.last_response = None

        self.client.publish(self.set_topic, json.dumps(payload))

        if self.response_event.wait(timeout=wait):
            return self.last_response
        return None

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()


# ─── Probe commands ──────────────────────────────────────────────────

def probe_full(prober):
    """Run comprehensive device probe."""
    print(f'\n{"="*60}')
    print(f'  PROBING: {prober.device}')
    print(f'{"="*60}\n')

    print('Sending c4_probe command...')
    result = prober.send({'c4_probe': True}, wait=15.0)

    if result is None:
        print('  No response (timeout). Is the device online?')
        return

    if isinstance(result, dict) and 'error' in result:
        print(f'  Error: {result["error"]}')
        return

    # Device info
    if 'device' in result:
        print('\n── Device Info ─────────────────────────────────────')
        dev = result['device']
        for k, v in dev.items():
            print(f'  {k:20s} = {v}')

    # Endpoints
    if 'endpoints' in result:
        print('\n── Endpoints ───────────────────────────────────────')
        for ep_id, ep in sorted(result['endpoints'].items(), key=lambda x: int(x[0])):
            print(f'\n  Endpoint {ep_id}:')
            print(f'    profileID:      {ep.get("profileID", "?")}')
            print(f'    deviceID:       {ep.get("deviceID", "?")}')
            in_cl = ep.get('inputClusters', [])
            out_cl = ep.get('outputClusters', [])
            print(f'    inputClusters:  {in_cl}')
            print(f'    outputClusters: {out_cl}')

    # genBasic
    if 'genBasic' in result:
        print('\n── genBasic Attributes ─────────────────────────────')
        gb = result['genBasic']
        if 'error' in gb:
            print(f'  Error: {gb["error"]}')
        else:
            for k, v in gb.items():
                print(f'  {k:20s} = {repr(v)}')

    print()


def zcl_read(prober, cluster, attributes=None, endpoint=1):
    """Read ZCL cluster attributes."""
    payload = {'zcl_read': {'cluster': cluster, 'endpoint': endpoint}}
    if attributes:
        payload['zcl_read']['attributes'] = attributes

    print(f'\nReading {cluster} from endpoint {endpoint}...')
    result = prober.send(payload, wait=10.0)

    if result is None:
        print('  No response (timeout)')
        return

    if isinstance(result, dict):
        if 'error' in result:
            print(f'  Error: {result["error"]}')
        elif 'attributes' in result:
            print(f'  Cluster: {result.get("cluster", "?")}')
            for k, v in result['attributes'].items():
                print(f'  {k:20s} = {repr(v)}')
    else:
        print(f'  Result: {result}')


def c4_query(prober, command):
    """Send a C4 GET query and wait for the response."""
    print(f'\nSending C4 query: {command}')
    prober.send({'c4_query': command}, wait=5.0)

    # The query response comes asynchronously via fzControl4Response
    # Wait a bit for the c4_response to arrive
    time.sleep(1.0)
    resp = prober.state.get('c4_response')
    if resp:
        print(f'  Response: {resp}')
    else:
        print('  No response captured (the fromZigbee handler may not have fired)')
        print('  Check Z2M logs for [C4 RECV] entries')


def interactive_mode(prober):
    """Interactive command loop."""
    print(f'\n{"="*60}')
    print(f'  INTERACTIVE MODE — device: {prober.device}')
    print(f'{"="*60}')
    print()
    print('Commands:')
    print('  probe                         — full device probe')
    print('  read [cluster] [attr ...]     — read ZCL attributes')
    print('  query <c4_command>            — send C4 GET query (0g prefix)')
    print('  cmd <c4_command>              — send C4 SET command (0s prefix)')
    print('  raw <json>                    — send arbitrary JSON to device/set')
    print('  state                         — show current device state')
    print('  quit                          — exit')
    print()

    while True:
        try:
            line = input('> ').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue

        parts = line.split(None, 1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ''

        try:
            if cmd == 'quit' or cmd == 'exit' or cmd == 'q':
                break
            elif cmd == 'probe':
                probe_full(prober)
            elif cmd == 'read':
                read_parts = args.split() if args else []
                cluster = read_parts[0] if read_parts else 'genBasic'
                attrs = read_parts[1:] if len(read_parts) > 1 else None
                # Try to parse as ints for numeric attribute IDs
                if attrs:
                    parsed = []
                    for a in attrs:
                        try:
                            parsed.append(int(a))
                        except ValueError:
                            parsed.append(a)
                    attrs = parsed
                zcl_read(prober, cluster, attrs)
            elif cmd == 'query':
                if not args:
                    print('  Usage: query <c4_command>')
                    continue
                c4_query(prober, args)
            elif cmd == 'cmd':
                if not args:
                    print('  Usage: cmd <c4_command>')
                    continue
                prober.send({'c4_cmd': args}, wait=3.0)
                print(f'  Sent: {args}')
            elif cmd == 'raw':
                if not args:
                    print('  Usage: raw <json>')
                    continue
                payload = json.loads(args)
                result = prober.send(payload, wait=5.0)
                print(f'  Result: {json.dumps(result, indent=2) if result else "no response"}')
            elif cmd == 'state':
                print(json.dumps(prober.state, indent=2))
            else:
                print(f'  Unknown command: {cmd}')
        except Exception as e:
            print(f'  Error: {e}')


# ─── Main ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Probe a Control4 device via Zigbee2MQTT',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument('device', help='Z2M device friendly name (e.g. "Kitchen")')
    parser.add_argument('--broker', default='localhost', help='MQTT broker host (default: localhost)')
    parser.add_argument('--port', type=int, default=1883, help='MQTT broker port (default: 1883)')
    parser.add_argument('--interactive', '-i', action='store_true', help='Interactive command mode')
    parser.add_argument('--zcl-read', metavar='CLUSTER', help='Read all attributes from a ZCL cluster')
    parser.add_argument('--c4-query', metavar='CMD', help='Send a C4 GET query')
    args = parser.parse_args()

    prober = DeviceProber(args.device, broker=args.broker, port=args.port)

    try:
        if args.interactive:
            interactive_mode(prober)
        elif args.zcl_read:
            zcl_read(prober, args.zcl_read)
        elif args.c4_query:
            c4_query(prober, args.c4_query)
        else:
            # Default: run full probe
            probe_full(prober)
    finally:
        prober.close()


if __name__ == '__main__':
    main()
