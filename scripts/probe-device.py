#!/usr/bin/env python3
"""
Interactive probe for Control4 Zigbee devices via Zigbee2MQTT MQTT API.

Sends commands to a device and listens for results in the device state.
For C4 text protocol queries, captures responses by tailing Z2M Docker
logs and decoding the raw hex frames — no manual log parsing needed.

Requires: paho-mqtt  (pip install paho-mqtt)

Usage:
  python3 probe-device.py <device_name> [--broker HOST] [--port PORT]

  # Interactive mode (recommended)
  python3 probe-device.py Kitchen --broker 192.168.1.54 -u ha -P pass -i

  # With Docker response capture (auto-decodes C4 query responses)
  python3 probe-device.py Kitchen --broker 192.168.1.54 -u ha -P pass -i --docker zigbee2mqtt

Commands in interactive mode:
  probe                         — full device probe (endpoints + genBasic)
  read [cluster] [attr ...]     — read ZCL attributes
  query <c4_command>            — send C4 GET query, capture response
  cmd <c4_command>              — send C4 SET command (0s prefix)
  raw <json>                    — send arbitrary JSON to device/set
  state                         — show current device state
  debug on/off                  — toggle Z2M debug logging
  quit                          — exit
"""

import argparse
import json
import re
import readline  # noqa: F401 — enables arrow-key history in input()
import subprocess
import sys
import time
import threading

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print('Error: paho-mqtt not installed. Run: pip install paho-mqtt', file=sys.stderr)
    sys.exit(1)


# ─── Docker Log Response Capture ─────────────────────────────────────

class DockerResponseCapture:
    """Capture C4 text protocol responses from Z2M Docker logs.

    C4 responses arrive on EP 197 with profile 0xC25C. They don't flow
    through Z2M's converter pipeline, but herdsman logs the raw frame
    in debug mode. This class tails Docker logs, filters for C4 frames,
    and decodes the hex payload to ASCII.
    """

    C4_PROFILE_ID = 49756  # 0xC25C

    def __init__(self, container='zigbee2mqtt'):
        self.container = container
        self._process = None
        self._buffer = []
        self._lock = threading.Lock()
        self._reader_thread = None
        self._running = False
        self._lines_read = 0
        self._start_error = None

    def start(self):
        """Start tailing Docker logs in background."""
        self._running = True
        # --tail 0: skip history, only stream new lines
        # stderr=STDOUT: Docker may write logs to stderr
        try:
            self._process = subprocess.Popen(
                ['docker', 'logs', '--follow', '--tail', '0', self.container],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            self._start_error = 'docker command not found'
            print(f'  [docker-capture] ERROR: docker command not found')
            return
        except Exception as e:
            self._start_error = str(e)
            print(f'  [docker-capture] ERROR: {e}')
            return

        self._reader_thread = threading.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()

        # Give it a moment to confirm the process is alive
        time.sleep(0.5)
        if self._process.poll() is not None:
            # Process exited immediately — read any error output
            err = self._process.stdout.read(500) if self._process.stdout else ''
            self._start_error = err.strip() or f'exited with code {self._process.returncode}'
            print(f'  [docker-capture] ERROR: docker logs exited immediately: {self._start_error}')
        else:
            print(f'  [docker-capture] Tailing logs from container "{self.container}"')

    def _reader(self):
        """Background thread: read Docker log lines, extract C4 responses."""
        # Match the line that has BOTH profileId:49756 AND messageContents on it.
        # The actual herdsman log line looks like:
        #   ezspIncomingMessageHandler: type=UNICAST apsFrame={"profileId":49756,...} ... messageContents=<hex>
        # The pattern is intentionally loose to handle formatting variations.
        pattern = re.compile(
            r'profileId["\s:]+' + str(self.C4_PROFILE_ID) +
            r'.*messageContents=([0-9a-fA-F]+)'
        )
        try:
            for line in self._process.stdout:
                if not self._running:
                    break
                self._lines_read += 1
                m = pattern.search(line)
                if m:
                    hex_data = m.group(1)
                    try:
                        text = bytes.fromhex(hex_data).decode('ascii').strip()
                    except (ValueError, UnicodeDecodeError):
                        text = f'<hex: {hex_data}>'
                    with self._lock:
                        self._buffer.append(text)
        except Exception:
            pass

    def collect(self, timeout=3.0):
        """Wait up to `timeout` seconds and return all captured responses."""
        deadline = time.time() + timeout
        # Poll until we get at least one response or timeout
        while time.time() < deadline:
            with self._lock:
                if self._buffer:
                    break
            time.sleep(0.1)

        with self._lock:
            responses = list(self._buffer)
            self._buffer.clear()
        return responses

    def flush(self):
        """Discard any buffered responses."""
        with self._lock:
            self._buffer.clear()

    def stop(self):
        """Stop tailing Docker logs."""
        self._running = False
        if self._process:
            self._process.kill()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            self._process = None

    @property
    def available(self):
        return self._process is not None and self._process.poll() is None


# ─── MQTT helpers ────────────────────────────────────────────────────

class DeviceProber:
    def __init__(self, device_name, broker='localhost', port=1883,
                 username=None, password=None, docker_container=None):
        self.device = device_name
        self.base_topic = f'zigbee2mqtt/{device_name}'
        self.set_topic = f'{self.base_topic}/set'
        self.bridge_topic = 'zigbee2mqtt/bridge/request/options'
        self.state = {}
        self.last_response = None
        self.response_event = threading.Event()
        self._username = username
        self._password = password

        # Docker response capture
        self.docker = None
        if docker_container:
            self.docker = DockerResponseCapture(docker_container)
            self.docker.start()
            time.sleep(0.3)
            if not self.docker.available:
                print(f'  Warning: could not start Docker log capture for "{docker_container}"')
                self.docker = None

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if username:
            self.client.username_pw_set(username, password)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(broker, port, keepalive=60)
        self.client.loop_start()

        # Wait for connection
        time.sleep(0.5)

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        client.subscribe(self.base_topic)
        client.subscribe('zigbee2mqtt/bridge/response/#')

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        if msg.topic == self.base_topic:
            self.state.update(payload)
            if 'probe_result' in payload:
                self.last_response = payload['probe_result']
                self.response_event.set()
            elif 'c4_response' in payload:
                self.last_response = payload['c4_response']
                self.response_event.set()

        elif msg.topic.startswith('zigbee2mqtt/bridge/response/'):
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

    def set_debug(self, enabled):
        """Toggle Z2M debug logging."""
        level = 'debug' if enabled else 'info'
        self.client.publish(self.bridge_topic,
                            json.dumps({'options': {'advanced': {'log_level': level}}}))
        time.sleep(0.3)

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()
        if self.docker:
            self.docker.stop()


# ─── C4 query with response capture ─────────────────────────────────

def c4_query(prober, command):
    """Send a C4 GET query and capture the response."""
    if prober.docker and prober.docker.available:
        # Docker capture mode: enable debug, send, capture, disable debug
        prober.docker.flush()
        prober.set_debug(True)
        time.sleep(0.2)

        print(f'  Sending: 0g<seq> {command}')
        prober.send({'c4_query': command}, wait=2.0)

        responses = prober.docker.collect(timeout=3.0)
        prober.set_debug(False)

        if responses:
            for r in responses:
                print(f'  Response: {r}')
        else:
            print('  No C4 response captured (device may not support this query)')
    else:
        # Fallback: send and hope fromZigbee handler works
        print(f'  Sending: 0g<seq> {command}')
        prober.send({'c4_query': command}, wait=3.0)
        time.sleep(1.0)
        resp = prober.state.get('c4_response')
        if resp:
            print(f'  Response: {resp}')
        else:
            print('  No response captured.')
            print('  Tip: use --docker to auto-capture responses from Z2M logs')


# ─── Probe commands ──────────────────────────────────────────────────

def probe_full(prober):
    """Run comprehensive device probe."""
    print(f'\n{"="*60}')
    print(f'  PROBING: {prober.device}')
    print(f'{"="*60}\n')

    print('Sending c4_probe command (this may take ~30s if genBasic is slow)...')
    result = prober.send({'c4_probe': True}, wait=120.0)

    if result is None:
        print('  No response (timeout). Is the device online?')
        return

    if isinstance(result, dict) and 'error' in result and 'device' not in result:
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
        if isinstance(gb, dict) and 'error' in gb and len(gb) == 1:
            print(f'  Error: {gb["error"]}')
        else:
            for k, v in gb.items():
                if isinstance(v, str) and v.startswith('<'):
                    print(f'  {k:20s}   {v}')
                else:
                    print(f'  {k:20s} = {repr(v)}')

    print()


def zcl_read(prober, cluster, attributes=None, endpoint=1):
    """Read ZCL cluster attributes."""
    payload = {'zcl_read': {'cluster': cluster, 'endpoint': endpoint}}
    if attributes:
        payload['zcl_read']['attributes'] = attributes

    print(f'\nReading {cluster} from endpoint {endpoint}...')
    result = prober.send(payload, wait=120.0)

    if result is None:
        print('  No response (timeout)')
        return

    if isinstance(result, dict):
        if 'error' in result and 'attributes' not in result:
            print(f'  Error: {result["error"]}')
        elif 'attributes' in result:
            print(f'  Cluster: {result.get("cluster", "?")}')
            if result.get('note'):
                print(f'  Note: {result["note"]}')
            for k, v in result['attributes'].items():
                if isinstance(v, str) and v.startswith('<error'):
                    print(f'  {k:20s}   <unsupported>')
                else:
                    print(f'  {k:20s} = {repr(v)}')
    else:
        print(f'  Result: {result}')


def interactive_mode(prober):
    """Interactive command loop."""
    print(f'\n{"="*60}')
    print(f'  INTERACTIVE MODE — device: {prober.device}')
    if prober.docker and prober.docker.available:
        print(f'  Docker response capture: ACTIVE')
    else:
        print(f'  Docker response capture: OFF (use --docker to enable)')
    print(f'{"="*60}')
    print()
    print('Commands:')
    print('  probe                         — full device probe')
    print('  read [cluster] [attr ...]     — read ZCL attributes')
    print('  query <c4_command>            — send C4 GET query, capture response')
    print('  cmd <c4_command>              — send C4 SET command (0s prefix)')
    print('  raw <json>                    — send arbitrary JSON to device/set')
    print('  state                         — show current device state')
    print('  debug on|off                  — toggle Z2M debug logging')
    print('  docker-test                   — verify Docker log capture is working')
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
            if cmd in ('quit', 'exit', 'q'):
                break
            elif cmd == 'probe':
                probe_full(prober)
            elif cmd == 'read':
                read_parts = args.split() if args else []
                cluster = read_parts[0] if read_parts else 'genBasic'
                attrs = read_parts[1:] if len(read_parts) > 1 else None
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
            elif cmd == 'debug':
                if args.lower() in ('on', '1', 'true'):
                    prober.set_debug(True)
                    print('  Debug logging enabled')
                elif args.lower() in ('off', '0', 'false'):
                    prober.set_debug(False)
                    print('  Debug logging disabled')
                else:
                    print('  Usage: debug on|off')
            elif cmd in ('docker-test', 'dt'):
                if not prober.docker:
                    print('  Docker capture not enabled (use --docker)')
                    continue
                d = prober.docker
                print(f'  Docker process alive: {d.available}')
                if d._start_error:
                    print(f'  Start error: {d._start_error}')
                    continue
                print(f'  Docker log lines read so far: {d._lines_read}')
                print(f'  Enabling debug, sending a known C4 command...')
                d.flush()
                pre_lines = d._lines_read
                prober.set_debug(True)
                time.sleep(0.5)
                # Send a query we know works (LED ambient color)
                prober.send({'c4_query': 'c4.dmx.amb 01'}, wait=2.0)
                time.sleep(2.0)
                post_lines = d._lines_read
                print(f'  Docker log lines read during test: {post_lines - pre_lines}')
                with d._lock:
                    n = len(d._buffer)
                print(f'  Buffered C4 responses: {n}')
                responses = d.collect(timeout=3.0)
                prober.set_debug(False)
                if responses:
                    print(f'  SUCCESS! Captured {len(responses)} response(s):')
                    for r in responses:
                        print(f'    {r}')
                else:
                    print('  No C4 responses captured.')
                    if post_lines == pre_lines:
                        print('  -> No Docker log lines received at all!')
                        print(f'     Is the container "{d.container}" on this machine?')
                        print(f'     Try: docker logs --tail 5 {d.container}')
                    else:
                        print(f'  -> Docker logs flowing ({post_lines - pre_lines} lines) but no C4 frames matched.')
                        print('     The device may not have responded, or the response format differs.')
                        print('     Try: docker logs --tail 20 ' + d.container + ' 2>&1 | grep -i messageContents')
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
    parser.add_argument('--username', '-u', help='MQTT username')
    parser.add_argument('--password', '-P', help='MQTT password')
    parser.add_argument('--docker', metavar='CONTAINER', nargs='?', const='zigbee2mqtt',
                        help='Docker container name for response capture (default: zigbee2mqtt)')
    parser.add_argument('--interactive', '-i', action='store_true', help='Interactive command mode')
    parser.add_argument('--zcl-read', metavar='CLUSTER', help='Read all attributes from a ZCL cluster')
    parser.add_argument('--c4-query', metavar='CMD', help='Send a C4 GET query')
    args = parser.parse_args()

    prober = DeviceProber(args.device, broker=args.broker, port=args.port,
                          username=args.username, password=args.password,
                          docker_container=args.docker)

    try:
        if args.interactive:
            interactive_mode(prober)
        elif args.zcl_read:
            zcl_read(prober, args.zcl_read)
        elif args.c4_query:
            c4_query(prober, args.c4_query)
        else:
            probe_full(prober)
    finally:
        prober.close()


if __name__ == '__main__':
    main()
