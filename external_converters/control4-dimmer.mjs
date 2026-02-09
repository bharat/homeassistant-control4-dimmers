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
//
// Two verbs: 0s = SET (write), 0g = GET (query). Both follow the same
// transport framing — only the verb prefix differs.

async function sendC4Raw(device, text) {
    const ep = device.getEndpoint(1);
    if (!ep) throw new Error('Endpoint 1 not found on device');

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

async function sendC4(device, cmdBody) {
    const seq = nextSeq();
    return sendC4Raw(device, `0s${seq} ${cmdBody}\r\n`);
}

async function queryC4(device, cmdBody) {
    const seq = nextSeq();
    return sendC4Raw(device, `0g${seq} ${cmdBody}\r\n`);
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

function c4LedLight({endpointName, ledId, modeCode, description,
                     defaultState = 'ON', defaultColor = {hue: 0, saturation: 0}}) {
    // NOTE: Z2M 2.7 derives HA entity names for light entities from the
    // endpoint name (e.g. "top_led_on" → "Top_led_on").  The withLabel() API
    // only affects generic exposes (binary/numeric/enum), not lights.
    // Users can rename entities in HA: Settings → Devices → entity → Name.
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
            let state      = cur[stKey]  ?? defaultState;
            let brightness = cur[brKey]  ?? (defaultState === 'ON' ? 254 : 0);
            let color      = cur[colKey] ?? defaultColor;

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

            if (value.top_on) state.c4_top_led_on = value.top_on;
            if (value.top_off) state.c4_top_led_off = value.top_off;
            if (value.bottom_on) state.c4_bottom_led_on = value.bottom_on;
            if (value.bottom_off) state.c4_bottom_led_off = value.bottom_off;

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

// ─── toZigbee: C4 GET Query ──────────────────────────────────────────
//
// Like c4_cmd but uses "0g" (GET) prefix instead of "0s" (SET).
// Responses arrive asynchronously on endpoint 197 and are captured
// by fzControl4Response (published as c4_response in device state).
//
//   {"c4_query": "c4.dmx.amb 01"}
//   {"c4_query": "c4.dmx.ls"}

const tzControl4Query = {
    key: ['c4_query'],
    convertSet: async (entity, key, value, meta) => {
        if (typeof value !== 'string') {
            throw new Error('c4_query expects a string, e.g. "c4.dmx.amb 01"');
        }

        const sent = await queryC4(meta.device, value);
        console.error(`[C4 QUERY] sent: ${sent}`);
        return {state: {c4_last_query: sent}};
    },
};

// ─── toZigbee: Read ZCL Attributes ──────────────────────────────────
//
// Read arbitrary cluster attributes for device interrogation.
// Results are returned in device state as probe_result.
//
//   {"zcl_read": {"cluster": "genBasic"}}                            — all standard genBasic attrs
//   {"zcl_read": {"cluster": "genBasic", "attributes": ["modelId"]}} — specific named attrs
//   {"zcl_read": {"cluster": 0, "attributes": [0,1,2,3,4,5,6,7]}}   — by numeric ID
//   {"zcl_read": {"endpoint": 1, "cluster": "genBasic"}}             — specific endpoint

const GENBASIC_ATTRS = [
    'zclVersion',          // 0x0000
    'applicationVersion',  // 0x0001
    'stackVersion',        // 0x0002
    'hwVersion',           // 0x0003
    'manufacturerName',    // 0x0004
    'modelId',             // 0x0005
    'dateCode',            // 0x0006
    'powerSource',         // 0x0007
    'swBuildId',           // 0x4000
];

const tzControl4ZclRead = {
    key: ['zcl_read'],
    convertSet: async (entity, key, value, meta) => {
        const epId = value.endpoint || 1;
        const ep = meta.device.getEndpoint(epId);
        if (!ep) throw new Error(`Endpoint ${epId} not found`);

        const cluster = value.cluster ?? 'genBasic';
        let attributes = value.attributes;

        // Default: read all standard genBasic attributes
        if (!attributes && (cluster === 'genBasic' || cluster === 0)) {
            attributes = GENBASIC_ATTRS;
        }

        if (!attributes || attributes.length === 0) {
            throw new Error('zcl_read requires "attributes" array (or use cluster "genBasic" for defaults)');
        }

        console.error(`[C4 PROBE] Reading EP ${epId} cluster ${cluster}: ${JSON.stringify(attributes)}`);

        // Try reading all at once first; if that fails (UNSUPPORTED_ATTRIBUTE),
        // fall back to reading one at a time to get everything the device supports.
        try {
            const result = await ep.read(cluster, attributes, {timeout: 10000});
            console.error(`[C4 PROBE] Result: ${JSON.stringify(result)}`);
            return {state: {probe_result: {cluster: String(cluster), endpoint: epId, attributes: result}}};
        } catch (batchErr) {
            console.error(`[C4 PROBE] Batch read failed (${batchErr.message}), trying one-by-one...`);
            const result = {};
            for (const attr of attributes) {
                try {
                    const val = await ep.read(cluster, [attr], {timeout: 10000});
                    Object.assign(result, val);
                    console.error(`[C4 PROBE]   ${attr} = ${JSON.stringify(val[attr] ?? val)}`);
                } catch (err) {
                    result[attr] = `<error: ${err.message}>`;
                    console.error(`[C4 PROBE]   ${attr} = ERROR: ${err.message}`);
                }
            }
            return {state: {probe_result: {cluster: String(cluster), endpoint: epId, attributes: result, note: 'read one-by-one (batch failed)'}}};
        }
    },
};

// ─── toZigbee: Comprehensive Device Probe ───────────────────────────
//
// Dumps everything we can learn about the device in one shot:
//   - All endpoints with their profile, deviceID, and cluster lists
//   - genBasic attributes from endpoint 1
//
//   {"c4_probe": true}

const tzControl4Probe = {
    key: ['c4_probe'],
    convertSet: async (entity, key, value, meta) => {
        const device = meta.device;
        const result = {timestamp: new Date().toISOString()};

        // ── Enumerate endpoints ──
        result.device = {
            ieeeAddr: device.ieeeAddr,
            networkAddress: device.networkAddress,
            manufacturerID: device.manufacturerID,
            manufacturerName: device.manufacturerName,
            modelID: device.modelID,
            type: device.type,
        };

        result.endpoints = {};
        for (const ep of device.endpoints) {
            result.endpoints[ep.ID] = {
                profileID: ep.profileID != null ? `0x${ep.profileID.toString(16).padStart(4, '0')}` : null,
                deviceID: ep.deviceID != null ? `0x${ep.deviceID.toString(16).padStart(4, '0')}` : null,
                inputClusters: ep.inputClusters || [],
                outputClusters: ep.outputClusters || [],
            };
        }

        // ── Read genBasic from EP 1 (one-by-one for resilience) ──
        const ep1 = device.getEndpoint(1);
        if (ep1) {
            result.genBasic = {};
            for (const attr of GENBASIC_ATTRS) {
                try {
                    const val = await ep1.read('genBasic', [attr], {timeout: 10000});
                    Object.assign(result.genBasic, val);
                } catch (err) {
                    result.genBasic[attr] = `<unsupported>`;
                }
            }
            console.error(`[C4 PROBE] genBasic: ${JSON.stringify(result.genBasic)}`);
        }

        console.error(`[C4 PROBE] Full result: ${JSON.stringify(result, null, 2)}`);
        return {state: {probe_result: result}};
    },
};

// ─── toZigbee: Live Device Identification ────────────────────────────
//
// Sets device metadata that genBasic can't provide (C4 locks it down).
// Triggered via MQTT while Z2M is running — no restart or database
// patching required.
//
//   {"c4_identify": true}

const tzControl4Identify = {
    key: ['c4_identify'],
    convertSet: async (entity, key, value, meta) => {
        const device = meta.device;

        // Set metadata that genBasic can't provide (C4 locks it down)
        device.manufacturerName = 'Control4';
        device.powerSource = 'Mains (single phase)';
        device.interviewCompleted = true;
        device.save();

        const result = {
            ieee_address: device.ieeeAddr,
            manufacturer_name: device.manufacturerName,
            manufacturer_id: device.manufacturerID,
            power_source: device.powerSource,
            interview_completed: device.interviewCompleted,
        };

        console.error(`[C4 IDENTIFY] ${device.ieeeAddr}: ${JSON.stringify(result)}`);
        return {state: {c4_identify_result: result}};
    },
};

// ─── fromZigbee: Capture C4 Text Protocol Responses ─────────────────
//
// C4 devices send responses and telemetry as raw ASCII on endpoint 197
// (0xC5), profile 0xC25C, cluster 1. Since there's no ZCL framing,
// herdsman fires a 'raw' event that we capture here.
//
// Response format: "0r<seq> 000 [data]" (success) or "0r<seq> v01" (error)
// Telemetry format: "0t<seq> sa <command> <data>"

const fzControl4Response = {
    cluster: 1,
    type: ['raw'],
    convert: (model, msg, publish, options, meta) => {
        try {
            const text = Buffer.from(msg.data).toString('ascii').trim();
            if (!text) return;

            const epId = msg.endpoint?.ID ?? '?';
            console.error(`[C4 RECV] EP ${epId}: ${text}`);

            return {c4_response: text, c4_response_ep: epId};
        } catch (e) {
            // Not ASCII data — ignore
            return;
        }
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
    icon: 'https://i.postimg.cc/hPrYf7JD/dimmer.png',
    extend: [
        // LED light entities — MUST come before light() so endpoint-restricted
        // converters are checked first (they skip non-matching endpoints,
        // letting the unrestricted light() converter handle the main dimmer).
        // C4 factory defaults: top=white when on, bottom=blue when off
        c4LedLight({endpointName: 'top_led_on',     ledId: '01', modeCode: '03',
                    description: 'Top LED color when dimmer load is ON',
                    defaultState: 'ON',  defaultColor: {hue: 0, saturation: 0}}),
        c4LedLight({endpointName: 'top_led_off',    ledId: '01', modeCode: '04',
                    description: 'Top LED color when dimmer load is OFF',
                    defaultState: 'OFF'}),
        c4LedLight({endpointName: 'bottom_led_on',  ledId: '04', modeCode: '03',
                    description: 'Bottom LED color when dimmer load is ON',
                    defaultState: 'OFF'}),
        c4LedLight({endpointName: 'bottom_led_off', ledId: '04', modeCode: '04',
                    description: 'Bottom LED color when dimmer load is OFF',
                    defaultState: 'ON',  defaultColor: {hue: 240, saturation: 100}}),
        // Main dimmer — standard Zigbee HA on/off + brightness
        light({configureReporting: false}),
    ],
    fromZigbee: [fzControl4Response],
    toZigbee: [tzControl4Led, tzControl4Cmd, tzControl4Query, tzControl4ZclRead, tzControl4Probe, tzControl4Identify],
    meta: {disableDefaultResponse: true, multiEndpoint: true},
    endpoint: (device) => ({
        default: 1,
        top_led_on: 1,
        top_led_off: 1,
        bottom_led_on: 1,
        bottom_led_off: 1,
    }),
    configure: async (device, coordinatorEndpoint, definition) => {
        // ONLY configure endpoint 1 — the standard Zigbee HA endpoint.
        // Do NOT touch endpoints 196/197 (proprietary C4).
        const endpoint = device.getEndpoint(1);
        if (!endpoint) return;

        await endpoint.bind('genOnOff', coordinatorEndpoint);
        await endpoint.bind('genLevelCtrl', coordinatorEndpoint);

        // Set metadata that genBasic can't provide (C4 locks it down).
        // This runs automatically on first pair / reconfigure.
        let changed = false;
        if (!device.manufacturerName) {
            device.manufacturerName = 'Control4';
            changed = true;
        }
        if (!device.powerSource) {
            device.powerSource = 'Mains (single phase)';
            changed = true;
        }
        if (changed) device.save();
    },
};

export default definition;
