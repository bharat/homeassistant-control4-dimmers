/**
 * Zigbee2MQTT External Converter for Control4 Zigbee Dimmers
 *
 * Standard Zigbee HA control (endpoint 1, profile 0x0104):
 *   - Cluster 0x0006 (genOnOff) for on/off
 *   - Cluster 0x0008 (genLevelCtrl) for dimming
 *
 * Proprietary C4 text protocol (endpoint 1, profile 0xC25C "MIB"):
 *   - Cluster 0x0001, raw ASCII payload (NO ZCL framing)
 *   - Command format: "0s<seq_hex> <command> <params>\r\n"
 *   - Response format: "0r<seq_hex> 000 [data]\r\n"  (000 = success)
 *   - Query format:   "0g<seq_hex> <command> <params>\r\n"
 *   - Telemetry:      "0t<seq_hex> sa <command> <data>\r\n"
 *   - Responses/events arrive on endpoint 197 (0xC5), profile 0xC25C
 *
 * Known C4 text commands:
 *   c4.dmx.led <led_id> <mode> <rrggbb>  — Set LED color
 *     led_id: 01=top, 04=bottom
 *     mode:   03=ON color (shown when load is on)
 *             04=OFF color (shown when load is off)
 *     rrggbb: hex RGB (ff0000=red, 00ff00=green, ffffff=white, 000000=off)
 *
 *   c4.dmx.pwr <hex_level>   — Set power level (e.g. "b5" ≈ 71%)
 *   c4.dmx.off <param>       — Turn off (e.g. "0000")
 *   c4.dmx.amb <led_id>      — Query/set ambient LED (0g to query, 0s to set)
 *   c4.dmx.ls                — Light status telemetry (incoming only, 0t prefix)
 *   c4.dmx.key               — Button/key events (incoming only)
 *   c4.dmx.bp                — Button press events
 *   c4.dmx.cc                — Config change?
 *   c4.dmx.hc / c4.dmx.he   — Unknown
 *   c4.dmx.plm / c4.dmx.pmti / c4.dmx.sc — Unknown
 *
 * Known quirks:
 *   - modelId returned as empty string from genBasic (breaks auto-discovery)
 *   - Endpoints 196/197 refuse simpleDescriptor requests (interview failures)
 *   - Device does NOT send ZCL default responses (must use disableDefaultResponse)
 *   - manufId (43981 / 0xABCD) IS available even after failed interview
 *   - Text commands use profile 0xC25C, NOT 0x0104 or 0xC25D
 *   - Responses arrive on endpoint 0xC5 (197), not the sending endpoint
 *   - C4 Director always sends all 4 LED commands as a group (top+bottom × on+off)
 *
 * Matching: fingerprint on manufacturerID since modelID is often absent.
 * Factory reset: Press top 13x, bottom 4x, top 13x (13-4-13)
 */

import {light} from 'zigbee-herdsman-converters/lib/modernExtend';
import {Light, access} from 'zigbee-herdsman-converters/lib/exposes';

// ─── Protocol Constants ──────────────────────────────────────────────

const C4_MIB_PROFILE = 0xC25C; // 49756 — C4 "MIB" profile for text commands
const C4_CLUSTER     = 1;      // Proprietary cluster (NOT genPowerCfg)

// ─── LED Mapping ─────────────────────────────────────────────────────

const LED_IDS = {
    top:    '01',
    bottom: '04',
};

const LED_MODES = {
    on:  '03', // Color shown when the dimmer load is ON
    off: '04', // Color shown when the dimmer load is OFF
};

// ─── Color Conversion Utilities ─────────────────────────────────────
//
// HA sends colors as HS (hue/saturation) or XY (CIE 1931). We need to
// convert to 6-digit hex RGB for the C4 text protocol.
//
// C4 LEDs have a non-linear response — low channel values (like 0x18)
// produce disproportionately visible light, washing out saturated colors.
// The C4 Director only sends pure colors (channels at 0x00 or 0xFF).
// We apply gamma correction (γ=2.0) to compress low values, making
// e.g. HSV(241°, 92%, 100%) → 0000ff instead of 1814ff.

const C4_LED_GAMMA = 2.0;

function applyGamma(value01) {
    // Apply gamma to a 0–1 channel value, return 0–255 integer
    return Math.round(255 * Math.pow(Math.max(0, Math.min(1, value01)), C4_LED_GAMMA));
}

function hsvToRgbHex(h, s, v = 1) {
    // h: 0–360, s: 0–100, v: 0–1
    s /= 100;
    const c = v * s;
    const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
    const m = v - c;
    let r, g, b;
    if      (h < 60)  { r = c; g = x; b = 0; }
    else if (h < 120) { r = x; g = c; b = 0; }
    else if (h < 180) { r = 0; g = c; b = x; }
    else if (h < 240) { r = 0; g = x; b = c; }
    else if (h < 300) { r = x; g = 0; b = c; }
    else              { r = c; g = 0; b = x; }
    return [r + m, g + m, b + m]
        .map(ch => applyGamma(ch).toString(16).padStart(2, '0'))
        .join('');
}

function xyToRgbHex(x, y) {
    // CIE 1931 XY → sRGB (D65 illuminant), assuming Y brightness = 1
    if (y === 0) return '000000';
    const Y = 1, X = (Y / y) * x, Z = (Y / y) * (1 - x - y);
    let r = X *  3.2406 + Y * -1.5372 + Z * -0.4986;
    let g = X * -0.9689 + Y *  1.8758 + Z *  0.0415;
    let b = X *  0.0557 + Y * -0.2040 + Z *  1.0570;
    return [r, g, b]
        .map(ch => applyGamma(ch).toString(16).padStart(2, '0'))
        .join('');
}

function rgbHexToHs(hex) {
    const r = parseInt(hex.substring(0, 2), 16) / 255;
    const g = parseInt(hex.substring(2, 4), 16) / 255;
    const b = parseInt(hex.substring(4, 6), 16) / 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    let h = 0;
    if (d !== 0) {
        if (max === r)      h = ((g - b) / d) % 6;
        else if (max === g) h = (b - r) / d + 2;
        else                h = (r - g) / d + 4;
        h = Math.round(h * 60);
        if (h < 0) h += 360;
    }
    const s = max === 0 ? 0 : Math.round((d / max) * 100);
    return {hue: h, saturation: s};
}

// ─── Sequence Counter ────────────────────────────────────────────────

let seqCounter = Math.floor(Math.random() * 0xFFFF);

function nextSeq() {
    seqCounter = (seqCounter + 1) & 0xFFFF;
    return seqCounter.toString(16).padStart(4, '0');
}

// ─── Core: Send C4 Text Command ─────────────────────────────────────
//
// The C4 text protocol sends raw ASCII as the APS payload with NO ZCL
// framing. We bypass endpoint.command() and call sendRequest() directly
// with a fake frame whose toBuffer() returns just our raw text bytes.

async function sendC4(device, cmdBody) {
    const ep = device.getEndpoint(1);
    if (!ep) throw new Error('Endpoint 1 not found on device');

    const seq = nextSeq();
    const text = `0s${seq} ${cmdBody}\r\n`;
    const rawBytes = Buffer.from(text, 'ascii');

    const frame = {
        cluster: {ID: C4_CLUSTER, name: 'c4Mib'},
        command: {ID: 0x35, name: 'c4TextCmd'},
        header: {
            transactionSequenceNumber: seqCounter & 0xFF,
            frameControl: {frameType: 1, direction: 0, disableDefaultResponse: true},
        },
        toBuffer: () => rawBytes,
    };

    const options = {
        profileId: C4_MIB_PROFILE,
        disableDefaultResponse: true,
        disableResponse: true,
        timeout: 10000,
        direction: 0,
        reservedBits: 0,
        disableRecovery: false,
        writeUndiv: false,
        sendPolicy: 'immediate',
    };

    await ep.sendRequest(frame, options);
    return text.trim();
}

// ─── ModernExtend: C4 LED Light Entity ──────────────────────────────
//
// Creates a Home Assistant light entity with color picker for one LED
// state (e.g. "top LED when load is ON"). Each instance returns a
// ModernExtend-compatible object with its own expose and toZigbee
// converter, scoped to a specific endpoint name.
//
// The converter ordering matters: these must come BEFORE light() in the
// extend array so the endpoint-restricted converters are checked first.
// Commands to the default endpoint skip these (wrong endpoint) and fall
// through to the unrestricted light() converter for the main dimmer.

function c4LedLight({endpointName, ledId, modeCode, description}) {
    const expose = new Light()
        .withBrightness()
        .withColor(['hs'])
        .withEndpoint(endpointName)
        .withDescription(description);

    const toZigbee = [{
        key: ['state', 'brightness', 'color', 'color_hs', 'color_xy', 'color_temp', 'color_mode'],
        endpoints: [endpointName],
        convertSet: async (entity, key, value, meta) => {
            // DEBUG: trace every call so we can see what Z2M sends us
            const msg = meta.message || {};
            console.error(`[C4 LED ${endpointName}] convertSet key=${key} value=${JSON.stringify(value)} msg_keys=${Object.keys(msg)}`);

            // Z2M auto-suffixes state keys with the endpoint name, so we
            // READ from meta.state using suffixed keys but RETURN base keys.
            const stKey  = `state_${endpointName}`;
            const brKey  = `brightness_${endpointName}`;
            const colKey = `color_${endpointName}`;

            const cur = meta.state || {};
            let state      = cur[stKey]  ?? 'ON';
            let brightness = cur[brKey]  ?? 254;
            let color      = cur[colKey] ?? {hue: 0, saturation: 0}; // default white

            // When the same MQTT message contains both a color change AND
            // a state/brightness change, Z2M calls convertSet for each key
            // separately. The color handler hasn't updated meta.state yet,
            // so the state/brightness handler would read the OLD color and
            // send it to the device — overriding the correct color.
            //
            // Fix: if a color key is also present in the same message,
            // pull the color from the message directly instead of meta.state.
            const msgColor = msg.color || msg.color_hs;
            if (msgColor && (key === 'state' || key === 'brightness')) {
                const c = msgColor;
                if (c.h !== undefined) color = {...color, hue: c.h};
                else if (c.hue !== undefined) color = {...color, hue: c.hue};
                if (c.s !== undefined) color = {...color, saturation: c.s};
                else if (c.saturation !== undefined) color = {...color, saturation: c.saturation};
                if (c.x !== undefined && c.y !== undefined) {
                    color = rgbHexToHs(xyToRgbHex(c.x, c.y));
                }
            }

            // Also pull brightness from the same message if present
            if (msg.brightness !== undefined && key !== 'brightness') {
                brightness = typeof msg.brightness === 'number' ? msg.brightness : parseInt(msg.brightness, 10);
            }

            const result = {};

            if (key === 'state') {
                if (value === 'TOGGLE') state = (state === 'ON') ? 'OFF' : 'ON';
                else state = (value === 'ON' || value === true) ? 'ON' : 'OFF';
                result.state = state;
            } else if (key === 'brightness') {
                brightness = typeof value === 'number' ? value : parseInt(value, 10);
                result.brightness = brightness;
                // Turning brightness up implies ON
                if (brightness > 0 && state !== 'ON') {
                    state = 'ON';
                    result.state = 'ON';
                }
            } else if (key === 'color' || key === 'color_hs') {
                const c = value || {};
                // Z2M may send {hue, saturation} or {h, s} depending on path
                if (c.hue !== undefined) color = {...color, hue: c.hue};
                else if (c.h !== undefined) color = {...color, hue: c.h};
                if (c.saturation !== undefined) color = {...color, saturation: c.saturation};
                else if (c.s !== undefined) color = {...color, saturation: c.s};
                // HA sometimes sends XY inside a 'color' key
                if (c.x !== undefined && c.y !== undefined) {
                    color = rgbHexToHs(xyToRgbHex(c.x, c.y));
                }
                result.color = color;
                if (state !== 'ON') { state = 'ON'; result.state = 'ON'; }
            } else if (key === 'color_xy') {
                const c = value || {};
                if (c.x !== undefined && c.y !== undefined) {
                    color = rgbHexToHs(xyToRgbHex(c.x, c.y));
                    result.color = color;
                }
                if (state !== 'ON') { state = 'ON'; result.state = 'ON'; }
            } else if (key === 'color_temp' || key === 'color_mode') {
                // Ignore color_temp and color_mode — we only support RGB LEDs
                console.error(`[C4 LED ${endpointName}] ignoring key=${key}`);
                return {};
            }

            // Compute final hex — brightness scales the HSV value channel
            let hexColor;
            if (state === 'OFF' || brightness === 0) {
                hexColor = '000000';
            } else {
                hexColor = hsvToRgbHex(color.hue, color.saturation, brightness / 254);
            }

            console.error(`[C4 LED ${endpointName}] sending c4.dmx.led ${ledId} ${modeCode} ${hexColor}`);
            await sendC4(meta.device, `c4.dmx.led ${ledId} ${modeCode} ${hexColor}`);

            return {state: result};
        },
    }];

    return {exposes: [expose], fromZigbee: [], toZigbee, isModernExtend: true};
}

// ─── toZigbee: Set LED Colors ────────────────────────────────────────
//
// Sets LED colors for a single LED+mode, or all 4 at once.
//
// Single LED:
//   {"c4_led": {"led": "top", "color": "ff0000"}}
//   {"c4_led": {"led": "top", "color": "ff0000", "mode": "on"}}
//   {"c4_led": {"led": "bottom", "color": "0000ff", "mode": "off"}}
//
// All 4 at once (matches C4 Director behavior):
//   {"c4_led": {"top_on": "ffffff", "top_off": "000000",
//               "bottom_on": "000000", "bottom_off": "0000ff"}}

const tzControl4Led = {
    key: ['c4_led'],
    convertSet: async (entity, key, value, meta) => {
        const state = {};

        // Batch mode: set all 4 LED states at once
        if (value.top_on !== undefined || value.top_off !== undefined ||
            value.bottom_on !== undefined || value.bottom_off !== undefined) {
            const commands = [
                ['01', '03', value.top_on],
                ['01', '04', value.top_off],
                ['04', '03', value.bottom_on],
                ['04', '04', value.bottom_off],
            ];

            for (const [ledId, mode, color] of commands) {
                if (color === undefined) continue;
                const colorHex = color.replace('#', '').toLowerCase();
                if (!/^[0-9a-f]{6}$/.test(colorHex)) {
                    throw new Error(`Invalid color "${color}" — expected 6-digit hex RGB`);
                }
                await sendC4(meta.device, `c4.dmx.led ${ledId} ${mode} ${colorHex}`);
            }

            if (value.top_on) state.c4_led_top_on = value.top_on;
            if (value.top_off) state.c4_led_top_off = value.top_off;
            if (value.bottom_on) state.c4_led_bottom_on = value.bottom_on;
            if (value.bottom_off) state.c4_led_bottom_off = value.bottom_off;

            return {state};
        }

        // Single LED mode
        const {led = 'top', color, mode = 'on'} = value;

        if (!color) {
            throw new Error('c4_led requires "color" (6-digit hex RGB) or batch keys (top_on, top_off, etc.)');
        }

        const ledId = LED_IDS[led] ?? led;
        const colorHex = color.replace('#', '').toLowerCase();

        if (!/^[0-9a-f]{6}$/.test(colorHex)) {
            throw new Error(`Invalid color "${color}" — expected 6-digit hex RGB like "ff0000"`);
        }

        const modeCode = LED_MODES[mode] ?? mode;

        await sendC4(meta.device, `c4.dmx.led ${ledId} ${modeCode} ${colorHex}`);
        state[`c4_led_${led}_${mode}`] = colorHex;

        return {state};
    },
};

// ─── toZigbee: Raw C4 Text Command ──────────────────────────────────
//
// For experimentation. The "0s<seq> " prefix and "\r\n" suffix are auto-added.
//
//   {"c4_cmd": "c4.dmx.led 01 03 ff0000"}
//   {"c4_cmd": "c4.dmx.pwr b5"}

const tzControl4Cmd = {
    key: ['c4_cmd'],
    convertSet: async (entity, key, value, meta) => {
        if (typeof value !== 'string') {
            throw new Error('c4_cmd expects a string, e.g. "c4.dmx.led 01 03 ff0000"');
        }

        const sent = await sendC4(meta.device, value);
        return {state: {c4_last_cmd: sent}};
    },
};

// ─── Definition ──────────────────────────────────────────────────────

/** @type{import('zigbee-herdsman-converters/lib/types').DefinitionWithExtend} */
const definition = {
    zigbeeModel: [
        'C4-APD120',   // Adaptive phase dimmer 120V
        'C4-DIM',      // Standard in-wall dimmer
        'C4-KD120',    // Keypad dimmer 120V
        'C4-KD277',    // Keypad dimmer 277V
        'C4-FPD120',   // Forward phase dimmer 120V
        'LDZ-102',     // Legacy dimmer model
    ],
    fingerprint: [{manufacturerID: 43981}],
    model: 'C4-Dimmer',
    vendor: 'Control4',
    description: 'Control4 Zigbee In-Wall Dimmer',
    extend: [
        // LED light entities — MUST come before light() so endpoint-restricted
        // converters are checked first (they skip non-matching endpoints,
        // letting the unrestricted light() converter handle the main dimmer).
        c4LedLight({endpointName: 'led_top_on',     ledId: '01', modeCode: '03',
                    description: 'Top LED color when dimmer load is ON'}),
        c4LedLight({endpointName: 'led_top_off',    ledId: '01', modeCode: '04',
                    description: 'Top LED color when dimmer load is OFF'}),
        c4LedLight({endpointName: 'led_bottom_on',  ledId: '04', modeCode: '03',
                    description: 'Bottom LED color when dimmer load is ON'}),
        c4LedLight({endpointName: 'led_bottom_off', ledId: '04', modeCode: '04',
                    description: 'Bottom LED color when dimmer load is OFF'}),
        // Main dimmer — standard Zigbee HA on/off + brightness
        light({configureReporting: false}),
    ],
    toZigbee: [tzControl4Led, tzControl4Cmd],
    meta: {disableDefaultResponse: true},
    endpoint: (device) => ({
        default: 1,
        led_top_on: 1,
        led_top_off: 1,
        led_bottom_on: 1,
        led_bottom_off: 1,
    }),
    configure: async (device, coordinatorEndpoint, definition) => {
        // ONLY configure endpoint 1 — the standard Zigbee HA endpoint.
        // Do NOT touch endpoints 196/197 (proprietary C4).
        const endpoint = device.getEndpoint(1);
        if (!endpoint) return;

        await endpoint.bind('genOnOff', coordinatorEndpoint);
        await endpoint.bind('genLevelCtrl', coordinatorEndpoint);
    },
};

export default definition;
