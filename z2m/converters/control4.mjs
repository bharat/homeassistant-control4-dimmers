/**
 * Zigbee2MQTT External Converter for Control4 Zigbee Devices
 *
 * Unified converter supporting all newer C4 in-wall devices:
 *   - C4-APD120 (Adaptive Phase Dimmer) — 2 LED buttons, 1 load
 *   - C4-KD120 (Keypad Dimmer) — 6 LED buttons, 1 load
 *   - C4-KC120277 (Configurable Keypad) — 6 LED buttons, no load
 *
 * All newer C4 devices share identical endpoint structures (1, 196, 197)
 * and the same `c4.dmx.*` text protocol. Device type differentiation
 * occurs at runtime via C4 text protocol probing.
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
 * Runtime device detection (probed during c4_detect):
 *   - Query c4.dmx.led 02 03 → has button 02? (APD120 has only 01, 04)
 *   - Query c4.dmx.dim → has load? (pure keypads don't respond)
 *   - Detection matrix:
 *       No btn02 + load → dimmer (APD120)
 *       btn02 + load → keypaddim (KD120)
 *       btn02 + no load → keypad (KC120277)
 *
 * Matching: single catch-all fingerprint on manufacturerID 43981.
 * Factory reset: Press top 13x, bottom 4x, top 13x (13-4-13)
 */

import {light} from 'zigbee-herdsman-converters/lib/modernExtend';
import {Light, Enum, access} from 'zigbee-herdsman-converters/lib/exposes';
import {
    C4_MIB_PROFILE, C4_CLUSTER,
    BUTTONS, LED_IDS, LED_MODES, ACTION_VALUES,
    hsvToRgbHex, xyToRgbHex, rgbHexToHs,
    nextSeq, getSeqCounter,
    buildRawFrame, buildSendOptions,
    formatSetCommand, formatGetCommand,
    parseLedColorResponse, parseDimResponse, parseResponseSeq,
    parseButtonEvent,
    classifyDeviceType, getButtonsForDeviceType, buildLedColorState,
    isValidColorHex, normalizeColorHex,
    DIM_TYPE_MAP, MODEL_NAMES, MODEL_DESCRIPTIONS, GENBASIC_ATTRS,
} from './lib/c4-protocol.mjs';

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

    const frame = buildRawFrame(text, getSeqCounter() & 0xFF);
    const options = buildSendOptions();

    await ep.sendRequest(frame, options);
    return text.trim();
}

async function sendC4(device, cmdBody) {
    const seq = nextSeq();
    return sendC4Raw(device, formatSetCommand(seq, cmdBody));
}

async function queryC4(device, cmdBody) {
    const seq = nextSeq();
    return sendC4Raw(device, formatGetCommand(seq, cmdBody));
}

// ─── Response Queue for Synchronous Query/Response ──────────────────
//
// The C4 text protocol is asynchronous: queries are sent on EP 1 and
// responses arrive on EP 197. This response queue enables awaitable
// query/response patterns used during device detection and LED reading.
//
// How it works:
// 1. queryC4WithResponse() registers a Promise resolver keyed by seq
// 2. The query is sent to the device
// 3. fzControl4Response receives the response, checks pendingQueries
// 4. If the seq matches, the Promise is resolved with the response text
// 5. If timeout expires, the Promise resolves with null

const pendingQueries = new Map();

async function queryC4WithResponse(device, cmdBody, timeoutMs = 3000) {
    const seq = nextSeq();

    return new Promise((resolve) => {
        const timer = setTimeout(() => {
            pendingQueries.delete(seq);
            console.error(`[C4 Q/R] Timeout for seq ${seq}: ${cmdBody}`);
            resolve(null);
        }, timeoutMs);

        pendingQueries.set(seq, (responseText) => {
            clearTimeout(timer);
            pendingQueries.delete(seq);
            resolve(responseText);
        });

        sendC4Raw(device, formatGetCommand(seq, cmdBody)).catch((err) => {
            clearTimeout(timer);
            pendingQueries.delete(seq);
            console.error(`[C4 Q/R] Send failed for seq ${seq}: ${err.message}`);
            resolve(null);
        });
    });
}

// ─── Response Parsers (imported from c4-protocol.mjs) ────────────────

// ─── Device Type Detection ──────────────────────────────────────────
//
// Uses a SINGLE C4 command to identify all three device types:
//   c4.dmx.dim response:
//     "01" → APD120 (forward-phase dimmer, 2-button rocker)
//     "02" → KD120  (reverse-phase keypad dimmer, 6 buttons + load)
//     error/n01 → KC120277 (configurable keypad, 6 buttons, no load)
//
// NOTE: The earlier approach of probing c4.dmx.led 02 03 (button 02
// existence) does NOT work — all C4 devices respond to LED queries for
// all 6 slots, including the 2-button APD120 (unused slots = 000000).
//
// Returns: 'dimmer' | 'keypaddim' | 'keypad' | 'unknown'

async function detectDeviceType(device) {
    console.error(`[C4 DETECT] Probing device ${device.ieeeAddr}...`);

    const dimResp = await queryC4WithResponse(device, 'c4.dmx.dim', 3000);
    console.error(`[C4 DETECT] c4.dmx.dim response: ${dimResp || '(timeout)'}`);

    const deviceType = classifyDeviceType(dimResp);
    console.error(`[C4 DETECT] Device type: ${deviceType}`);
    return deviceType;
}

// ─── Read Stored LED Colors ─────────────────────────────────────────
//
// C4 devices store LED colors in firmware (persisted across power cycles
// and network migrations). This reads all stored colors for the device's
// button set and returns them as a state update object.

async function readStoredColors(device, deviceType) {
    const buttons = getButtonsForDeviceType(deviceType);

    const state = {};
    for (const btn of buttons) {
        for (const [mode, suffix] of [['03', 'on'], ['04', 'off']]) {
            const resp = await queryC4WithResponse(device, `c4.dmx.led ${btn.id} ${mode}`, 2000);
            const hex = parseLedColorResponse(resp);
            if (hex) {
                Object.assign(state, buildLedColorState(btn.idx, suffix, hex));
                console.error(`[C4 DETECT] LED ${btn.id} mode ${mode}: #${hex}`);
            } else {
                console.error(`[C4 DETECT] LED ${btn.id} mode ${mode}: no response`);
            }
        }
    }
    return state;
}

// ─── ModernExtend: C4 LED Light Entity ──────────────────────────────
//
// Creates a Home Assistant light entity with color picker for one LED
// state (e.g. "button 1 LED when load is ON"). Each instance returns a
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

    const defaultColor = {hue: 0, saturation: 0};

    const toZigbee = [{
        key: ['state', 'brightness', 'color', 'color_hs', 'color_xy', 'color_temp', 'color_mode'],
        endpoints: [endpointName],
        convertSet: async (entity, key, value, meta) => {
            const msg = meta.message || {};
            console.error(`[C4 LED ${endpointName}] convertSet key=${key} value=${JSON.stringify(value)} msg_keys=${Object.keys(msg)}`);

            const stKey  = `state_${endpointName}`;
            const brKey  = `brightness_${endpointName}`;
            const colKey = `color_${endpointName}`;

            const cur = meta.state || {};
            let state      = cur[stKey]  ?? 'OFF';
            let brightness = cur[brKey]  ?? 0;
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
                if (brightness > 0 && state !== 'ON') {
                    state = 'ON';
                    result.state = 'ON';
                }
            } else if (key === 'color' || key === 'color_hs') {
                const c = value || {};
                if (c.hue !== undefined) color = {...color, hue: c.hue};
                else if (c.h !== undefined) color = {...color, hue: c.h};
                if (c.saturation !== undefined) color = {...color, saturation: c.saturation};
                else if (c.s !== undefined) color = {...color, saturation: c.s};
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

// ─── ModernExtend: C4 Button Config Select Entity ───────────────────
//
// Creates a Home Assistant select entity for button configuration.
// Stores the value in Z2M state only — no device command is sent.
// Used for button behavior (keypad/toggle/on/off) and LED mode
// (follow_load/follow_connection/push_release/programmed).

function c4ButtonConfig({endpointName, key, description, options}) {
    const expose = new Enum(key, access.STATE_SET, options)
        .withEndpoint(endpointName)
        .withDescription(description);

    const toZigbee = [{
        key: [key],
        endpoints: [endpointName],
        convertSet: async (entity, k, value, meta) => {
            console.error(`[C4 CONFIG] ${endpointName}.${key} = ${value}`);
            return {state: {[key]: value}};
        },
    }];

    return {exposes: [expose], fromZigbee: [], toZigbee, isModernExtend: true};
}

// ─── toZigbee: Set LED Colors (Raw MQTT) ─────────────────────────────
//
// Sets LED colors for a single LED+mode, or all 4 dimmer LEDs at once.
//
// Single LED:
//   {"c4_led": {"led": "1", "color": "ff0000"}}
//   {"c4_led": {"led": "top", "color": "ff0000", "mode": "on"}}
//   {"c4_led": {"led": "04", "color": "0000ff", "mode": "off"}}
//
// All 4 dimmer LEDs at once:
//   {"c4_led": {"top_on": "ffffff", "top_off": "000000",
//               "bottom_on": "000000", "bottom_off": "0000ff"}}

const tzControl4Led = {
    key: ['c4_led'],
    convertSet: async (entity, key, value, meta) => {
        const state = {};

        // Batch mode: set all 4 dimmer LED states at once
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
                const colorHex = normalizeColorHex(color);
                if (!isValidColorHex(colorHex)) {
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
        const colorHex = normalizeColorHex(color);

        if (!isValidColorHex(colorHex)) {
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
//   {"zcl_read": {"cluster": "genBasic"}}
//   {"zcl_read": {"cluster": "genBasic", "attributes": ["modelId"]}}
//   {"zcl_read": {"cluster": 0, "attributes": [0,1,2,3,4,5,6,7]}}
//   {"zcl_read": {"endpoint": 1, "cluster": "genBasic"}}

const tzControl4ZclRead = {
    key: ['zcl_read'],
    convertSet: async (entity, key, value, meta) => {
        const epId = value.endpoint || 1;
        const ep = meta.device.getEndpoint(epId);
        if (!ep) throw new Error(`Endpoint ${epId} not found`);

        const cluster = value.cluster ?? 'genBasic';
        let attributes = value.attributes;

        if (!attributes && (cluster === 'genBasic' || cluster === 0)) {
            attributes = GENBASIC_ATTRS;
        }

        if (!attributes || attributes.length === 0) {
            throw new Error('zcl_read requires "attributes" array (or use cluster "genBasic" for defaults)');
        }

        console.error(`[C4 PROBE] Reading EP ${epId} cluster ${cluster}: ${JSON.stringify(attributes)}`);

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

// ─── toZigbee: Device Type Detection + LED Color Reading ─────────────
//
// Runtime detection: probes the device to determine type (dimmer,
// keypaddim, or keypad), then reads all stored LED colors from firmware
// and populates HA state.
//
// Run once after pairing:
//   {"c4_detect": true}
//
// Also reads stored LED colors and auto-populates HA state, so migrated
// devices show their existing C4 colors without manual reconfiguration.

const tzControl4Detect = {
    key: ['c4_detect'],
    convertSet: async (entity, key, value, meta) => {
        const device = meta.device;

        // Step 1: Detect device type
        const deviceType = await detectDeviceType(device);

        // Step 2: Read stored LED colors from firmware
        const colorState = await readStoredColors(device, deviceType);

        // Step 3: Build the full state update
        const state = {
            c4_device_type: deviceType,
            ...colorState,
        };

        // Step 4: Update device metadata
        device.manufacturerName = 'Control4';
        device.powerSource = 'Mains (single phase)';
        device.interviewCompleted = true;

        // Store device type in device.meta for use by other converters
        if (!device.meta) device.meta = {};
        device.meta.c4_device_type = deviceType;
        device.save();

        state.c4_detect_result = {
            ieee_address: device.ieeeAddr,
            device_type: deviceType,
            model: MODEL_NAMES[deviceType] ?? 'unknown',
            description: MODEL_DESCRIPTIONS[deviceType] ?? 'Unknown Control4 device',
            colors_read: Object.keys(colorState).length,
        };

        console.error(`[C4 DETECT] Complete: ${JSON.stringify(state.c4_detect_result)}`);
        return {state};
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
    cluster: 'genPowerCfg', // C4 uses cluster ID 1, which ZCL maps to genPowerCfg
    type: ['raw'],
    convert: async (model, msg, publish, options, meta) => {
        try {
            const text = Buffer.from(msg.data).toString('ascii').trim();
            if (!text) return;

            const epId = msg.endpoint?.ID ?? '?';
            console.error(`[C4 RECV] EP ${epId}: ${text}`);

            // ── Check for pending query responses (response queue) ──
            const respSeq = parseResponseSeq(text);
            if (respSeq) {
                const handler = pendingQueries.get(respSeq);
                if (handler) {
                    console.error(`[C4 Q/R] Resolved pending query seq ${respSeq}`);
                    handler(text);
                }
            }

            const result = {c4_response: text, c4_response_ep: epId};

            // ── Parse button/event messages ──
            const event = parseButtonEvent(text);
            if (event) {
                result.action = event.action;
                console.error(`[C4 BUTTON] ${event.type}: ${event.action}`);

                // Smart behavior on press: if button has load-control behavior,
                // send the genOnOff command to EP1 immediately.
                if (event.type === 'press') {
                    const behavior = meta.state?.[`button_${event.buttonId}_behavior`];
                    if (behavior && behavior !== 'keypad') {
                        try {
                            const ep1 = msg.device.getEndpoint(1);
                            if (ep1) {
                                const cmd = behavior === 'toggle_load' ? 'toggle'
                                          : behavior === 'load_on'    ? 'on'
                                          : behavior === 'load_off'   ? 'off'
                                          : null;
                                if (cmd) {
                                    console.error(`[C4 BUTTON] Smart behavior: genOnOff.${cmd}`);
                                    await ep1.command('genOnOff', cmd, {});
                                }
                            }
                        } catch (err) {
                            console.error(`[C4 BUTTON] Smart behavior failed: ${err.message}`);
                        }
                    }
                }
                return result;
            }

            return result;
        } catch (e) {
            // Not ASCII data — ignore
            return;
        }
    },
};

// ─── Unified Definition ──────────────────────────────────────────────
//
// Slim Z2M converter for all newer Control4 in-wall devices:
//   - C4-APD120 (dimmer): 2 buttons + load
//   - C4-KD120 (keypad dimmer): 6 buttons + load
//   - C4-KC120277 (pure keypad): 6 buttons, no load
//
// Entity layout per device (Z2M side only):
//   - 1 main dimmer light (standard Zigbee HA, harmless on pure keypads)
//   - 1 action entity (button press events)
//   - Utility converters: c4_led, c4_cmd, c4_query, zcl_read, c4_probe,
//     c4_identify, c4_detect
//
// LED colors and button config are managed entirely in HA, not Z2M.
// LED colors are stored as flat hex attributes (c4_led_N_on/off) in
// Z2M state, read by the HA integration on startup.

/** @type{import('zigbee-herdsman-converters/lib/types').DefinitionWithExtend} */
const definition = {
    zigbeeModel: [
        'C4-APD120',    // Adaptive phase dimmer 120V
        'C4-DIM',       // Standard in-wall dimmer
        'C4-KD120',     // Keypad dimmer 120V
        'C4-KD277',     // Keypad dimmer 277V
        'C4-FPD120',    // Forward phase dimmer 120V
        'C4-KC120277',  // Configurable keypad 120V/277V
        'LDZ-102',      // Legacy dimmer model
    ],
    fingerprint: [{manufacturerID: 43981}],
    model: 'C4-Zigbee',
    vendor: 'Control4',
    description: 'Control4 Zigbee Device (Dimmer/Keypad)',
    icon: 'https://i.postimg.cc/hPrYf7JD/dimmer.png',
    extend: [
        light({configureReporting: false}),
    ],
    exposes: [
        new Enum('action', access.STATE, ACTION_VALUES)
            .withDescription('Button press events'),
    ],
    fromZigbee: [fzControl4Response],
    toZigbee: [
        tzControl4Led, tzControl4Cmd, tzControl4Query,
        tzControl4ZclRead, tzControl4Probe, tzControl4Identify,
        tzControl4Detect,
    ],
    meta: {disableDefaultResponse: true},
    configure: async (device, coordinatorEndpoint, definition) => {
        const endpoint = device.getEndpoint(1);
        if (!endpoint) return;

        // ── Register EP197 on coordinator for C4 message reception ──
        //
        // C4 devices send responses and button events on profile 0xC25C,
        // cluster 1, destination EP 197. By default, the coordinator only
        // has EP 1 (HA) and EP 242 (Green Power). Without EP 197 registered,
        // herdsman drops incoming C4 messages at the adapter level.
        //
        // This creates EP 197 in herdsman's coordinator model. A Z2M restart
        // may be required for the EZSP firmware to register the endpoint.
        try {
            const coordinator = coordinatorEndpoint.getDevice();
            let coordEp197 = coordinator.getEndpoint(197);
            if (!coordEp197) {
                coordEp197 = coordinator.createEndpoint(197);
                console.error(`[C4 CONFIG] Created EP 197 on coordinator`);
            }
            // Ensure profile and clusters are set for C4 text protocol
            if (coordEp197.profileID !== C4_MIB_PROFILE) {
                coordEp197.profileID = C4_MIB_PROFILE;
                coordEp197.deviceID = 0;
                coordEp197.inputClusters = [C4_CLUSTER];
                coordEp197.outputClusters = [C4_CLUSTER];
                coordinator.save();
                console.error(`[C4 CONFIG] Configured coordinator EP 197: profile=0x${C4_MIB_PROFILE.toString(16)}, cluster=${C4_CLUSTER}`);
                console.error(`[C4 CONFIG] *** Z2M RESTART REQUIRED for EP 197 to be registered on EZSP firmware ***`);
            }
        } catch (e) {
            console.error(`[C4 CONFIG] Could not register EP 197 on coordinator: ${e.message}`);
            console.error(`[C4 CONFIG] Button events may not work. See docs/device-identification.md for workaround.`);
        }

        // ── Bind standard HA clusters on EP 1 ──
        try {
            await endpoint.bind('genOnOff', coordinatorEndpoint);
            await endpoint.bind('genLevelCtrl', coordinatorEndpoint);
        } catch (e) {
            console.error(`[C4 CONFIG] Cluster binding failed (may be normal for keypads): ${e.message}`);
        }

        // Set metadata that genBasic can't provide (C4 locks it down).
        // C4 devices fail the standard Zigbee interview because they don't
        // respond to many genBasic reads, but they work fine otherwise.
        device.manufacturerName = 'Control4';
        device.powerSource = 'Mains (single phase)';
        device.interviewCompleted = true;
        device.save();

        // ── Auto-detect device type via C4 text protocol ──
        //
        // On the very first C4 device pairing, EP 197 was just created above
        // and EZSP firmware may not have registered it yet — detection will
        // timeout since responses can't reach us. After a Z2M restart EP 197
        // is active and detection works. Either way, c4_detect can be run
        // manually later if needed.
        try {
            const deviceType = await detectDeviceType(device);
            if (!device.meta) device.meta = {};
            device.meta.c4_device_type = deviceType;
            device.save();
            console.error(`[C4 CONFIG] Auto-detected device type: ${deviceType} (${MODEL_NAMES[deviceType] ?? 'unknown'})`);
        } catch (e) {
            console.error(`[C4 CONFIG] Auto-detection failed (expected on first pairing before Z2M restart): ${e.message}`);
            console.error(`[C4 CONFIG] Run {"c4_detect": true} after restarting Z2M to detect device type and read LED colors.`);
        }

        console.error(`[C4 CONFIG] Device ${device.ieeeAddr} configured.`);
    },
};

// ─── Export ──────────────────────────────────────────────────────────

export default definition;
