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
import {Enum, access} from 'zigbee-herdsman-converters/lib/exposes';

// ═══════════════════════════════════════════════════════════════════════
// C4 Text Protocol — Pure Logic (no Z2M dependencies)
//
// Everything below this banner through the next ═══ line is pure
// protocol logic: constants, color math, formatting, parsing, and
// device detection. Exported so tests can import them directly.
// ═══════════════════════════════════════════════════════════════════════

// ─── Protocol Constants ──────────────────────────────────────────────

export const C4_MIB_PROFILE = 0xC25C; // 49756 — C4 "MIB" profile for text commands
export const C4_CLUSTER     = 1;      // Proprietary cluster (NOT genPowerCfg)

// ─── Button Layout ──────────────────────────────────────────────────
//
// All newer C4 devices have a 6-slot chassis. Button IDs are hex (00–05).
//
// APD120 dimmer uses only: 01 (top rocker), 04 (bottom rocker)
// KD120 keypad dimmer uses: 00–05 (rocker + 4 keypad buttons)
// KC120277 pure keypad uses: 00–05 (6 configurable slots)

export const BUTTONS = [
    {idx: 1, id: '00'},
    {idx: 2, id: '01'},
    {idx: 3, id: '02'},
    {idx: 4, id: '03'},
    {idx: 5, id: '04'},
    {idx: 6, id: '05'},
];

// Legacy name map for the raw c4_led interface
export const LED_IDS = {
    top: '01', bottom: '04',
    '1': '00', '2': '01', '3': '02', '4': '03', '5': '04', '6': '05',
};

export const LED_MODES = {
    on:  '03', // Color shown when the dimmer load is ON
    off: '04', // Color shown when the dimmer load is OFF
};

// ─── Local Load Paddle (GitHub issue #117) ──────────────────────────
//
// Load-bearing devices (APD120 dimmers, KD keypad-dimmers) expose the two
// halves of the physical load paddle on a SEPARATE wire-id space from the
// six configurable button-array slots. The prod log tally (2026-07-19)
// proved two distinct spaces:
//   bp/cc/sc 00-05 = the configurable button-array slots (button_1..6)
//   bp/cc/sc 07    = top/up paddle half   (observed with an up-ramp)
//   bp/cc/sc 08    = bottom/down paddle half (observed with a down-ramp)
// The two spaces coexist: one APD120 emitted both. Paddle halves are
// surfaced as their own paddle_up / paddle_down actions rather than
// extending the button_N enum (which would produce out-of-enum names the
// HA integration drops).

export const PADDLE_WIRE_IDS = {
    0x07: 'paddle_up',
    0x08: 'paddle_down',
};

export const PADDLE_TARGETS = ['paddle_up', 'paddle_down'];

// The action variants shared by buttons and paddles: a bare press, a scene
// change, and click counts 1..4. Buttons and paddles use the same grammar,
// differing only in the prefix (button_N vs paddle_up / paddle_down).
function actionVariants(prefix) {
    return [
        `${prefix}_press`,
        `${prefix}_scene`,
        `${prefix}_click_1`,
        `${prefix}_click_2`,
        `${prefix}_click_3`,
        `${prefix}_click_4`,
    ];
}

// Action values for button and paddle events (the frozen MQTT enum contract).
export const ACTION_VALUES = [
    ...BUTTONS.flatMap(btn => actionVariants(`button_${btn.idx}`)),
    ...PADDLE_TARGETS.flatMap(actionVariants),
];

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

export const C4_LED_GAMMA = 2.0;

export function applyGamma(value01) {
    // Apply gamma to a 0–1 channel value, return 0–255 integer
    return Math.round(255 * Math.pow(Math.max(0, Math.min(1, value01)), C4_LED_GAMMA));
}

export function hsvToRgbHex(h, s, v = 1) {
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

export function xyToRgbHex(x, y) {
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

export function rgbHexToHs(hex) {
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

export function nextSeq() {
    seqCounter = (seqCounter + 1) & 0xFFFF;
    return seqCounter.toString(16).padStart(4, '0');
}

/** Reset counter to a known value (for testing) */
export function resetSeqCounter(value = 0) {
    seqCounter = value & 0xFFFF;
}

/** Get current counter value (for testing) */
export function getSeqCounter() {
    return seqCounter;
}

// ─── Protocol Text Formatting ────────────────────────────────────────

/** Format a SET command string (0s prefix) */
export function formatSetCommand(seq, cmdBody) {
    return `0s${seq} ${cmdBody}\r\n`;
}

/** Format a GET command string (0g prefix) */
export function formatGetCommand(seq, cmdBody) {
    return `0g${seq} ${cmdBody}\r\n`;
}

/** Build a raw frame object for sending via herdsman's sendRequest() */
export function buildRawFrame(text, seqByte) {
    const rawBytes = Buffer.from(text, 'ascii');
    return {
        cluster: {ID: C4_CLUSTER, name: 'c4Mib'},
        command: {ID: 0x35, name: 'c4TextCmd'},
        header: {
            transactionSequenceNumber: seqByte,
            frameControl: {frameType: 1, direction: 0, disableDefaultResponse: true},
        },
        toBuffer: () => rawBytes,
    };
}

/** Standard send options for C4 text protocol */
export function buildSendOptions() {
    return {
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
}

// ─── Response Parsers ───────────────────────────────────────────────

/** Parse LED color from response: "0r<seq> 000 c4.dmx.led <RRGGBB>" → hex string or null */
export function parseLedColorResponse(responseText) {
    if (!responseText) return null;
    const match = responseText.match(/000 c4\.dmx\.led (\w{6})/);
    return match ? match[1].toLowerCase() : null;
}

/** Parse dimmer type from response: "0r<seq> 000 c4.dmx.dim <XX>" → type code or null */
export function parseDimResponse(responseText) {
    if (!responseText) return null;
    const match = responseText.match(/000 c4\.dmx\.dim (\w+)/);
    return match ? match[1] : null;
}

/** Extract sequence number from a response: "0r<seq> ..." → seq string or null */
export function parseResponseSeq(text) {
    const match = text.match(/^0r(\w{4})\s/);
    return match ? match[1] : null;
}

// ─── Button Event Parsing ───────────────────────────────────────────

const loggedUnknownWireIds = new Set();

/** Reset the once-per-wire-id unknown-id log guard (for testing). */
export function resetC4ButtonLogState() {
    loggedUnknownWireIds.clear();
}

/**
 * Resolve a hex wire id from a c4.dmx.bp/cc/sc frame to an action target.
 * Pure logic (apart from a once-per-id diagnostic log).
 *
 *   0x00-0x05 -> button_1..button_6 (the configurable button-array slots)
 *   0x07/0x08 -> paddle_up / paddle_down (the local load paddle halves)
 *
 * Any other id (0x06, or 0x09+) is outside every known space: we keep the
 * historical button_(N+1) mapping so nothing that used to flow stops, but
 * log it once under [C4 BUTTON] as an unknown wire id. Returns null only
 * when the hex is unparseable.
 */
export function resolveButtonTarget(wireIdHex) {
    const wireId = parseInt(wireIdHex, 16);
    if (Number.isNaN(wireId)) return null;

    const paddle = PADDLE_WIRE_IDS[wireId];
    if (paddle) return {prefix: paddle, paddle};

    if (wireId < 0x00 || wireId > 0x05) {
        if (!loggedUnknownWireIds.has(wireId)) {
            loggedUnknownWireIds.add(wireId);
            console.error(
                `[C4 BUTTON] Unknown wire id 0x${wireId.toString(16).padStart(2, '0')}: ` +
                'not a button slot (0x00-0x05) or paddle half (0x07/0x08)',
            );
        }
    }
    return {prefix: `button_${wireId + 1}`, buttonId: wireId + 1};
}

/** Attach the identity field (buttonId or paddle) for a resolved target. */
function targetIdentity(target) {
    return target.paddle ? {paddle: target.paddle} : {buttonId: target.buttonId};
}

export function parseButtonEvent(text) {
    // Button press: 0t<seq> sa c4.dmx.bp <btn>
    const bpMatch = text.match(/^0t\w+ sa c4\.dmx\.bp (\w+)/);
    if (bpMatch) {
        const target = resolveButtonTarget(bpMatch[1]);
        if (!target) return null;
        return {action: `${target.prefix}_press`, ...targetIdentity(target), type: 'press'};
    }

    // Click count: 0t<seq> sa c4.dmx.cc <btn> <count>
    const ccMatch = text.match(/^0t\w+ sa c4\.dmx\.cc (\w+) (\w+)/);
    if (ccMatch) {
        const target = resolveButtonTarget(ccMatch[1]);
        if (!target) return null;
        const count = parseInt(ccMatch[2], 16);
        return {action: `${target.prefix}_click_${count}`, ...targetIdentity(target), clickCount: count, type: 'click'};
    }

    // Scene change: 0t<seq> sa c4.dmx.sc <btn>
    const scMatch = text.match(/^0t\w+ sa c4\.dmx\.sc (\w+)/);
    if (scMatch) {
        const target = resolveButtonTarget(scMatch[1]);
        if (!target) return null;
        return {action: `${target.prefix}_scene`, ...targetIdentity(target), type: 'scene'};
    }

    return null;
}

// ─── Load Status Telemetry Parsing ──────────────────────────────────
//
// Control4 devices broadcast unsolicited load-status telemetry on every
// load change, as C4 text frames on EP 197:
//
//   0t<seq> sa c4.dmx.ls 00 00 <level> 0078 0000 0000 ...
//
// The THIRD data field after "c4.dmx.ls" is the current load level as a
// hex percent (0x00..0x64). This is how a manual paddle press reports its
// new level without any ZCL reporting (GitHub issue #101).

export function parseLoadStatus(text) {
    const match = text.match(/^0t\w+ sa c4\.dmx\.ls (\w+) (\w+) (\w+)/);
    if (!match) return null;

    const level = parseInt(match[3], 16);
    if (Number.isNaN(level) || level > 0x64) {
        // Out of the valid 0..100 percent range: treat as unparsed.
        console.error(`[C4 LS] Ignoring out-of-range load level: ${text}`);
        return null;
    }

    return {level};
}

// ─── Device Type Detection Logic ─────────────────────────────────────

export const DIM_TYPE_MAP = {
    '01': 'dimmer',    // C4-APD120 (forward-phase, 2 buttons)
    '02': 'keypaddim', // C4-KD120  (reverse-phase, 6 buttons + load)
};

/** Determine device type from c4.dmx.dim response text. Pure logic. */
export function classifyDeviceType(dimResponseText) {
    const dimType = parseDimResponse(dimResponseText);
    if (dimType && DIM_TYPE_MAP[dimType]) {
        return DIM_TYPE_MAP[dimType];
    } else if (dimType) {
        // Unknown dim type but has load — treat as keypaddim
        return 'keypaddim';
    } else {
        // No response / error = no load = pure keypad
        return 'keypad';
    }
}

/**
 * True when a c4.dmx.dim response is an explicit "no load" answer rather than
 * a dim code. A true keypad ANSWERS the dim probe instead of staying silent:
 * prod (issue #123) observed the negative "0r<seq> n01", and the generic
 * error form is "0r<seq> v<NN>" (e.g. v01). Either arrives as a real response
 * that carries no dim code, which proves the device is reachable and drives
 * no load. Pure logic.
 */
export function isC4DimNegativeResponse(rawText) {
    if (!rawText) return false;
    return /\bn01\b/.test(rawText) || /^0r\w+\s+v\d{2}\b/.test(rawText);
}

/**
 * Classify a c4.dmx.dim probe outcome from its raw response text. Pure logic.
 *
 *   {kind: 'heal', dimCode}  a dim code answer proves a load type
 *   {kind: 'negative'}       an explicit no-load answer (n01 / v01 error form)
 *   {kind: 'silent'}         no response at all (a timeout)
 *
 * The distinction matters for self-heal: a negative answer confirms a keypad
 * immediately, while silence only counts toward the silent-probe budget.
 */
export function classifyDimProbeResponse(rawText) {
    if (rawText == null) return {kind: 'silent'};
    const dimCode = parseDimResponse(rawText);
    if (dimCode) return {kind: 'heal', dimCode};
    if (isC4DimNegativeResponse(rawText)) return {kind: 'negative'};
    return {kind: 'silent'};
}

// ─── Self-Heal Confidence Model (GitHub issue #115) ─────────────────
//
// A c4.dmx.dim probe that times out was historically read as "no load =
// pure keypad" and persisted forever, so a single transient Zigbee timeout
// at detection time became a permanent wrong classification. We now attach
// a confidence marker to every keypad verdict:
//
//   confirmed: proven by a dim answer, or by N consecutive silent probes
//   assumed:   a single no-answer probe; may be a timeout artifact
//
// Load types (dimmer / keypaddim) are always confirmed: the device answered
// the dim query, which proves it drives a load. Only "keypad" can be
// assumed. Legacy stored keypad state that predates this marker is treated
// as assumed by construction, because prod has proven such verdicts can be
// timeout artifacts.

export const C4_CONFIDENCE_CONFIRMED = 'confirmed';
export const C4_CONFIDENCE_ASSUMED  = 'assumed';

// Consecutive silent dim probes required to upgrade an assumed keypad to a
// confirmed keypad (and stop probing it).
export const C4_MAX_SILENT_PROBES = 3;

/**
 * Derive the effective confidence of a device's current classification from
 * its herdsman meta. Backward compatible: any keypad verdict without an
 * explicit "confirmed" marker (including legacy stored state) is assumed.
 *
 * @param {object} [meta] - device.meta
 */
export function effectiveConfidence(meta) {
    if (meta && meta.c4_type_confidence === C4_CONFIDENCE_CONFIRMED) {
        return C4_CONFIDENCE_CONFIRMED;
    }
    return C4_CONFIDENCE_ASSUMED;
}

/**
 * Given the current classification and a piece of load evidence, decide the
 * corrected device type, or null if no correction is warranted. Pure logic.
 *
 *   dim answer is authoritative: 01 -> dimmer, any other code -> keypaddim.
 *   ls telemetry: only proves the device drives a load, not which kind, so
 *                  it upgrades an (assumed) keypad or unclassified device to
 *                  keypaddim as the safe default, but never downgrades an
 *                  existing dimmer / keypaddim.
 *   paddle telemetry: a local load paddle half (bp 07/08) only exists on a
 *                  load-bearing device, so it is load proof identical to ls
 *                  (issue #117): upgrades an assumed keypad / unclassified
 *                  device to keypaddim, never downgrades.
 *
 * @param {string|undefined|null} currentType
 * @param {{dimCode?: string, ls?: boolean, paddle?: boolean}} evidence
 */
export function healTypeFromEvidence(currentType, evidence) {
    if (evidence && evidence.dimCode != null) {
        const healed = DIM_TYPE_MAP[evidence.dimCode] ?? 'keypaddim';
        return healed !== currentType ? healed : null;
    }
    if (evidence && (evidence.ls || evidence.paddle)) {
        if (currentType === 'keypad' || currentType == null) return 'keypaddim';
        return null;
    }
    return null;
}

/** Get button list for a device type */
export function getButtonsForDeviceType(deviceType) {
    if (deviceType === 'dimmer') {
        return BUTTONS.filter(b => b.idx === 2 || b.idx === 5);
    }
    return BUTTONS; // keypaddim and keypad use all 6 slots
}

/** Build state object from a read LED color (flat hex attribute) */
export function buildLedColorState(buttonIdx, suffix, hexColor) {
    return {
        [`c4_led_${buttonIdx}_${suffix}`]: hexColor,
    };
}

// ─── Color Hex Validation ────────────────────────────────────────────

export function isValidColorHex(str) {
    return /^[0-9a-f]{6}$/.test(str);
}

export function normalizeColorHex(str) {
    return str.replace('#', '').toLowerCase();
}

// ─── Model Metadata ──────────────────────────────────────────────────

export const MODEL_NAMES = {
    dimmer: 'C4-APD120',
    keypaddim: 'C4-KD120',
    keypad: 'C4-KC120277',
};

export const MODEL_DESCRIPTIONS = {
    dimmer: 'Control4 Adaptive Phase Dimmer',
    keypaddim: 'Control4 Keypad Dimmer',
    keypad: 'Control4 Configurable Keypad',
};

export const GENBASIC_ATTRS = [
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

// ═══════════════════════════════════════════════════════════════════════
// Z2M Converter — I/O layer (depends on zigbee-herdsman)
// ══════════════════════════════════════════════════════════���════════════

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

    const dimCode = parseDimResponse(dimResp);
    const deviceType = classifyDeviceType(dimResp);

    // A dim answer proves the load type (confirmed). A no-answer keypad
    // verdict is low confidence by construction: it may be a transient
    // Zigbee timeout rather than a true keypad (GitHub issue #115), so the
    // self-heal machinery is allowed to revisit it later.
    const confidence = dimCode ? C4_CONFIDENCE_CONFIRMED : C4_CONFIDENCE_ASSUMED;
    console.error(`[C4 DETECT] Device type: ${deviceType} (confidence: ${confidence})`);
    return {deviceType, dimCode, confidence};
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

// LED colors and button config are managed entirely by the HA custom
// component via c4_cmd / c4.dmx.led commands. The converter only needs
// tzControl4Led/tzControl4Cmd in toZigbee for raw MQTT control.

// @deprecated — removed from extend, kept for reference.
function _unused_c4LedLight({endpointName, ledId, modeCode, description}) {
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

// @deprecated — removed from extend, kept for reference.
function _unused_c4ButtonConfig({endpointName, key, description, options}) {
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
        const {deviceType, dimCode, confidence} = await detectDeviceType(device);

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

        // Store device type + confidence in device.meta for use by other
        // converters and the self-heal machinery (issue #115). A manual
        // c4_detect that times out yields an assumed keypad, which the active
        // probe campaign may later confirm or heal.
        if (!device.meta) device.meta = {};
        device.meta.c4_device_type = deviceType;
        device.meta.c4_type_confidence = confidence;
        device.meta.c4_dim_code = dimCode ?? null;
        device.save();

        state.c4_detect_result = {
            ieee_address: device.ieeeAddr,
            device_type: deviceType,
            confidence,
            dim_code: dimCode ?? null,
            model: MODEL_NAMES[deviceType] ?? 'unknown',
            description: MODEL_DESCRIPTIONS[deviceType] ?? 'Unknown Control4 device',
            colors_read: Object.keys(colorState).length,
        };

        console.error(`[C4 DETECT] Complete: ${JSON.stringify(state.c4_detect_result)}`);
        return {state};
    },
};

// ─── Debounced Load State Read (manual paddle sync) ─────────────────
//
// Control4 devices do NOT support ZCL attribute reporting (the load light
// is registered with configureReporting: false), so a manual paddle press
// at the wall never pushes a state update to Z2M. The Z2M light entity
// only moves when Z2M itself commands the device, leaving it stale after
// physical interaction.
//
// C4 devices DO answer ZCL reads, and button presses arrive as C4 text
// telemetry. So on every button event we schedule a read of the load
// state (genOnOff.onOff + genLevelCtrl.currentLevel on EP1). The standard
// light() fromZigbee handlers pick those read responses up and update
// state/brightness automatically.
//
// The read is debounced per device: a dimmer paddle hold emits a burst of
// events, so we wait until the device has been quiet for the debounce
// window before reading, letting the load settle and coalescing the burst
// into a single read.

export const C4_STATE_READ_DEBOUNCE_MS = 750;

const c4StateReadTimers = new Map();

// ─── Throttled Load State Publish (unsolicited ls telemetry) ─────────
//
// The primary path for GitHub issue #101: Control4 devices push their new
// load level as c4.dmx.ls telemetry on every load change, so we do not
// need a ZCL read at all in the common case.
//
// This is a THROTTLE, not a coalesce-to-one. Each ls frame stores the
// latest level and resets a per-device trailing-edge timer; we publish
// once the device has been quiet for C4_LOAD_STATE_DEBOUNCE_MS. The
// contract is: at most one publish per 500 ms quiet window, and the final
// settled level is ALWAYS published.
//
// What that means in practice depends on ls cadence:
//   - A tight burst (frames under 500 ms apart, e.g. a fast dim ramp)
//     coalesces into a single publish carrying the final level.
//   - A real ls ramp on live hardware has 500 to 1500 ms inter-frame gaps
//     (field data, issue #118), so the quiet window fires mid-ramp and the
//     ramp produces SEVERAL publishes (one per gap that exceeds 500 ms).
//     This is intended: HA tracks the ramp live as throttled updates, and
//     the last publish is guaranteed to carry the settled level.
//
// Publishing uses the standard light() fields (state + brightness) that
// the light() extend already exposes, so HA updates without any new
// entities or MQTT contract changes. At level 0 we publish {state: 'OFF'}
// with NO brightness key: the device resumes at its previous level via
// on_level, so wiping HA's last-on brightness would only degrade the
// slider. Nonzero levels publish both state and brightness.

export const C4_LOAD_STATE_DEBOUNCE_MS = 500;

const c4LoadStateTimers = new Map();

// Per-device timestamp (ms) of the most recent ls frame seen. Used to
// demote the #102 ZCL read to a fallback: if ls telemetry already arrived,
// the scheduled read is redundant and gets skipped.
const c4LastLsSeen = new Map();

/**
 * Schedule a trailing-edge debounced publish of load state for a device.
 *
 * Each ls frame stores the latest level and resets the per-device timer;
 * once the device goes quiet for C4_LOAD_STATE_DEBOUNCE_MS, we publish the
 * latest level for that window (see the header comment: on live hardware a
 * single ramp yields several such windows). At level 0 we publish only
 * {state: 'OFF'} with no brightness key; nonzero levels publish both. Also
 * records the ls-seen timestamp so scheduleC4StateRead can skip its
 * fallback read.
 *
 * @param {object} device - herdsman device (needs ieeeAddr)
 * @param {number} level - load level as a percent (0..100)
 * @param {function} publish - the fz publish callback
 */
export function scheduleC4LoadStatePublish(device, level, publish) {
    if (!device) return;

    const key = device.ieeeAddr;
    c4LastLsSeen.set(key, Date.now());

    const existing = c4LoadStateTimers.get(key);
    if (existing) clearTimeout(existing.timer);

    const entry = {level, timer: null};
    entry.timer = setTimeout(() => {
        c4LoadStateTimers.delete(key);
        const lvl = entry.level;
        publish(lvl > 0
            ? {state: 'ON', brightness: Math.round(lvl * 255 / 100)}
            : {state: 'OFF'});
    }, C4_LOAD_STATE_DEBOUNCE_MS);

    c4LoadStateTimers.set(key, entry);
}

/**
 * Schedule a debounced read of the load on/off + level state for a device.
 *
 * Each call resets the per-device timer; the read fires once the device has
 * been quiet for C4_STATE_READ_DEBOUNCE_MS. Pure keypads have no load, so
 * they are skipped. Read failures are logged and swallowed (never thrown).
 *
 * This is now a FALLBACK behind the ls-telemetry path: if an ls frame
 * arrived after the read was scheduled, the debounced load-state publish
 * already refreshed HA, so the read is skipped.
 *
 * @param {object} device - herdsman device (needs ieeeAddr + getEndpoint)
 * @param {string} [deviceType] - c4_device_type from meta.state, if known
 */
export function scheduleC4StateRead(device, deviceType) {
    if (!device) return;

    // Pure keypads drive no load, so there is nothing to read. When the
    // device type is unknown we attempt the read anyway (guarded below).
    if (deviceType === 'keypad') return;

    const key = device.ieeeAddr;
    const scheduledAt = Date.now();
    const existing = c4StateReadTimers.get(key);
    if (existing) clearTimeout(existing);

    const timer = setTimeout(async () => {
        c4StateReadTimers.delete(key);

        // Fallback demotion: if an ls frame arrived after this read was
        // scheduled, the debounced load-state publish already updated HA.
        const lastLs = c4LastLsSeen.get(key);
        if (lastLs !== undefined && lastLs > scheduledAt) {
            console.error(`[C4 STATE] Skipping ZCL read for ${key}; ls telemetry already refreshed state`);
            return;
        }

        let ep1;
        try {
            ep1 = device.getEndpoint(1);
        } catch (err) {
            console.error(`[C4 STATE] getEndpoint(1) failed: ${err.message}`);
            return;
        }
        if (!ep1) return;

        try {
            await ep1.read('genOnOff', ['onOff']);
        } catch (err) {
            console.error(`[C4 STATE] genOnOff read failed: ${err.message}`);
        }
        try {
            await ep1.read('genLevelCtrl', ['currentLevel']);
        } catch (err) {
            console.error(`[C4 STATE] genLevelCtrl read failed: ${err.message}`);
        }
    }, C4_STATE_READ_DEBOUNCE_MS);

    c4StateReadTimers.set(key, timer);
}

// ─── Self-Heal: Reclassification + Active Probe Campaign (issue #115) ─
//
// Two mechanisms correct a device mis-classified as keypad by a silent
// dim-probe timeout, without ever requiring a manual c4_detect:
//
//   PASSIVE: any load evidence that arrives on its own (an ls broadcast or
//   a c4.dmx.dim answer) proves the device drives a load, so we reclassify
//   immediately in fzControl4Response via applyC4Heal.
//
//   ACTIVE: for a keypad that is still only "assumed", we re-run the dim
//   probe a few times (jittered start, exponential backoff) until either it
//   answers (heal) or it stays silent C4_MAX_SILENT_PROBES times in a row
//   (upgrade to a confirmed keypad and stop). Confirmed keypads are never
//   probed again in this process, and the confirmed marker is persisted so a
//   Z2M restart does not restart the campaign from zero.

// Jitter window for the first probe of a device, spreading radio traffic so
// a fleet of keypads does not all probe at once on startup.
export const C4_PROBE_INITIAL_MAX_MS = 60000;

// Base gap between silent probes; doubled after each additional silence.
export const C4_PROBE_BACKOFF_MS = 30000;

const c4ProbeCampaigns = new Map();

/** Persist self-heal fields onto device.meta and flush via device.save(). */
function persistC4Meta(device, fields) {
    if (!device.meta) device.meta = {};
    if (fields.type !== undefined) device.meta.c4_device_type = fields.type;
    if (fields.confidence !== undefined) device.meta.c4_type_confidence = fields.confidence;
    if (fields.dimCode !== undefined) device.meta.c4_dim_code = fields.dimCode;
    if (fields.silentProbes !== undefined) device.meta.c4_silent_probes = fields.silentProbes;
    if (typeof device.save === 'function') device.save();
}

/** Tear down any in-flight probe campaign for a device. */
function stopC4ProbeCampaign(device) {
    if (!device) return;
    const campaign = c4ProbeCampaigns.get(device.ieeeAddr);
    if (campaign && campaign.timer) clearTimeout(campaign.timer);
    c4ProbeCampaigns.delete(device.ieeeAddr);
}

/** Reset all in-memory self-heal campaign state (for testing). */
export function resetC4HealState() {
    for (const campaign of c4ProbeCampaigns.values()) {
        if (campaign.timer) clearTimeout(campaign.timer);
    }
    c4ProbeCampaigns.clear();
}

/**
 * Reclassify a device from load evidence and persist the correction. Returns
 * the state fragment to publish (containing the frozen-contract
 * c4_device_type plus an extended c4_detect_result), or null when no
 * correction is warranted. Idempotent: once a device is confirmed at the
 * target type, repeat evidence is a no-op so the probe path and the response
 * path can both observe the same answer without double publishing.
 *
 * @param {object} device - herdsman device (needs ieeeAddr, meta, save)
 * @param {string|undefined|null} currentType - c4_device_type before healing
 * @param {{dimCode?: string, ls?: boolean}} evidence
 * @param {function} [publish] - optional fz publish callback
 */
export function applyC4Heal(device, currentType, evidence, publish) {
    if (!device) return null;

    const newType = healTypeFromEvidence(currentType, evidence);
    if (!newType) return null;

    const already = device.meta &&
        device.meta.c4_device_type === newType &&
        device.meta.c4_type_confidence === C4_CONFIDENCE_CONFIRMED;
    if (already) return null;

    const dimCode = evidence.dimCode != null ? evidence.dimCode : (device.meta?.c4_dim_code ?? null);
    let evidenceDesc;
    if (evidence.dimCode != null) {
        evidenceDesc = `c4.dmx.dim answer ${evidence.dimCode}`;
    } else if (evidence.paddle) {
        evidenceDesc = 'c4.dmx.bp local load paddle telemetry';
    } else {
        evidenceDesc = 'c4.dmx.ls load telemetry';
    }

    console.error(`[C4 HEAL] ${device.ieeeAddr}: ${currentType ?? '(none)'} -> ${newType} (evidence: ${evidenceDesc})`);

    // Reclassification changes c4_device_type at runtime. We mirror the
    // c4_detect flow exactly: update device.meta + device.save() and publish
    // the new c4_device_type. Z2M computes exposes/slot layout at definition
    // load, so a Z2M restart may be required for the exposes to fully refresh;
    // the stored meta and published c4_device_type are correct immediately.
    persistC4Meta(device, {type: newType, confidence: C4_CONFIDENCE_CONFIRMED, dimCode});

    // Load is proven, so any active probe campaign is done.
    stopC4ProbeCampaign(device);

    const state = {
        c4_device_type: newType,
        c4_detect_result: {
            ieee_address: device.ieeeAddr,
            device_type: newType,
            confidence: C4_CONFIDENCE_CONFIRMED,
            dim_code: dimCode,
            model: MODEL_NAMES[newType] ?? 'unknown',
            description: MODEL_DESCRIPTIONS[newType] ?? 'Unknown Control4 device',
            healed: true,
            evidence: evidenceDesc,
        },
    };

    if (typeof publish === 'function') publish(state);
    return state;
}

/**
 * Upgrade an assumed keypad to a confirmed keypad. Called both after enough
 * silence and, per issue #123, immediately on an explicit negative dim answer;
 * the evidence/logReason override lets each path record its own proof.
 */
function markConfirmedC4Keypad(device, publish, opts = {}) {
    const evidence = opts.evidence ??
        `${C4_MAX_SILENT_PROBES} consecutive silent c4.dmx.dim probes`;
    const logReason = opts.logReason ??
        `keypad confirmed after ${C4_MAX_SILENT_PROBES} silent c4.dmx.dim probes`;

    persistC4Meta(device, {confidence: C4_CONFIDENCE_CONFIRMED});
    console.error(`[C4 HEAL] ${device.ieeeAddr}: ${logReason}`);

    if (typeof publish === 'function') {
        publish({
            c4_detect_result: {
                ieee_address: device.ieeeAddr,
                device_type: 'keypad',
                confidence: C4_CONFIDENCE_CONFIRMED,
                dim_code: null,
                model: MODEL_NAMES.keypad,
                description: MODEL_DESCRIPTIONS.keypad,
                healed: false,
                evidence,
            },
        });
    }
}

/**
 * Start (once per process) an active dim-probe campaign for an assumed
 * keypad. No-op for non-keypads and for already-confirmed keypads. The first
 * probe is jittered across C4_PROBE_INITIAL_MAX_MS; subsequent silent probes
 * back off exponentially. A dim answer heals via the response path; three
 * silences confirm the keypad and stop the campaign.
 *
 * @param {object} device - herdsman device
 * @param {string|undefined|null} deviceType - current c4_device_type
 * @param {function} [publish] - fz publish callback (for confirm/heal state)
 * @param {object} [opts] - test seams: probeFn, random, initialMaxMs, backoffMs
 */
/**
 * Normalize a probeFn return value into a {kind, dimCode?} outcome. The
 * default probeFn returns a classifyDimProbeResponse object, but the legacy
 * test seam contract is a bare dim code string (heal) or null (silent), so
 * both shapes are accepted.
 */
function normalizeProbeOutcome(result) {
    if (result == null) return {kind: 'silent'};
    if (typeof result === 'object') return result;
    return {kind: 'heal', dimCode: result};
}

export function scheduleC4ProbeCampaign(device, deviceType, publish, opts = {}) {
    if (!device) return;
    if (deviceType !== 'keypad') return;                           // never probe a load-bearing device
    if (effectiveConfidence(device.meta) === C4_CONFIDENCE_CONFIRMED) return; // never re-probe a confirmed keypad
    if (c4ProbeCampaigns.has(device.ieeeAddr)) return;             // one campaign per process lifetime

    const probeFn = opts.probeFn ?? (async (dev) =>
        classifyDimProbeResponse(await queryC4WithResponse(dev, 'c4.dmx.dim', 3000)));
    const random = opts.random ?? Math.random;
    const initialMaxMs = opts.initialMaxMs ?? C4_PROBE_INITIAL_MAX_MS;
    const backoffMs = opts.backoffMs ?? C4_PROBE_BACKOFF_MS;

    const campaign = {silentCount: device.meta?.c4_silent_probes ?? 0, timer: null};
    c4ProbeCampaigns.set(device.ieeeAddr, campaign);

    const runProbe = async () => {
        campaign.timer = null;

        let outcome;
        try {
            outcome = normalizeProbeOutcome(await probeFn(device));
        } catch (err) {
            console.error(`[C4 HEAL] Probe failed for ${device.ieeeAddr}: ${err.message}`);
            outcome = {kind: 'silent'};
        }

        if (outcome.kind === 'heal') {
            // The dim response also flows through fzControl4Response, which
            // heals and publishes; applyC4Heal here is idempotent (no-op if
            // already healed) so the injected-probe test path still heals.
            applyC4Heal(device, deviceType, {dimCode: outcome.dimCode}, publish);
            c4ProbeCampaigns.delete(device.ieeeAddr);
            return;
        }

        if (outcome.kind === 'negative') {
            // A true keypad answers the dim probe with an explicit no-load
            // response rather than timing out. That is proof, not silence, so
            // confirm immediately instead of burning the silent-probe budget
            // and its exponential backoff (issue #123).
            markConfirmedC4Keypad(device, publish, {
                logReason: 'keypad confirmed by explicit n01 answer',
                evidence: 'explicit n01 answer',
            });
            c4ProbeCampaigns.delete(device.ieeeAddr);
            return;
        }

        campaign.silentCount += 1;
        persistC4Meta(device, {silentProbes: campaign.silentCount});

        if (campaign.silentCount >= C4_MAX_SILENT_PROBES) {
            markConfirmedC4Keypad(device, publish);
            c4ProbeCampaigns.delete(device.ieeeAddr);
            return;
        }

        campaign.timer = setTimeout(runProbe, backoffMs * Math.pow(2, campaign.silentCount - 1));
    };

    campaign.timer = setTimeout(runProbe, Math.floor(random() * initialMaxMs));
}

// ─── Startup Arming of the Probe Campaign (issue #123) ──────────────
//
// scheduleC4ProbeCampaign was originally only kicked off from
// fzControl4Response, so a device had to emit C4 text traffic before its
// campaign armed. Quiet keypads never did, so in prod 6 of 8 devices sat
// unprobed for 25 minutes. We now arm every assumed-keypad device at z2m
// startup via the definition's onEvent 'start' hook. A device whose stored
// classification is a load type (dimmer / keypaddim) or a confirmed keypad is
// skipped by scheduleC4ProbeCampaign; an absent classification is treated as
// an assumed keypad so a never-detected device still gets probed. The
// per-device jitter inside the campaign keeps a fleet from probing at once,
// and the fz-side arming stays as an idempotent supplement.

/**
 * onEvent handler for the definition. Arms the self-heal probe campaign for
 * assumed-keypad (or unclassified) devices when z2m starts.
 *
 * @param {string} type - z2m event type; only 'start' arms.
 * @param {object} data - z2m event data (unused).
 * @param {object} device - herdsman device.
 * @param {object} options - device options (unused).
 * @param {object} [state] - last published device state, if any.
 * @param {object} [opts] - test seam forwarded to scheduleC4ProbeCampaign
 *                          (probeFn, random, initialMaxMs, backoffMs, publish).
 *                          z2m never passes this; only tests do.
 */
export async function c4OnEvent(type, data, device, options, state, opts = {}) {
    if (type !== 'start' || !device) return;

    const currentType = state?.c4_device_type ?? device.meta?.c4_device_type ?? 'keypad';
    scheduleC4ProbeCampaign(device, currentType, opts.publish, opts);
}

/**
 * Short device identity for log attribution (issue #118 item 4, extended to
 * the [C4 BUTTON] and [C4 LS] prefixes in issue #123). Uses the ieeeAddr, plus
 * the friendly name when the device object carries one cheaply.
 */
function c4DeviceLabel(device) {
    if (!device) return '?';
    const ieee = device.ieeeAddr ?? '?';
    const name = device.friendlyName ?? device.meta?.friendlyName;
    return name ? `${ieee} (${name})` : ieee;
}

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

            const currentType = meta.state?.c4_device_type ?? msg.device?.meta?.c4_device_type;

            // Kick off the active self-heal campaign for an assumed keypad on
            // the first message seen from this device (idempotent thereafter).
            // No-op for load-bearing devices and confirmed keypads (issue #115).
            scheduleC4ProbeCampaign(msg.device, currentType, publish);

            // ── Passive self-heal: a c4.dmx.dim answer proves a load ──
            //
            // Any dim answer (solicited by a probe or otherwise) is authoritative
            // load evidence, so reclassify keypad/none immediately (issue #115).
            const dimAnswerCode = parseDimResponse(text);
            if (dimAnswerCode) {
                const healState = applyC4Heal(msg.device, currentType, {dimCode: dimAnswerCode});
                if (healState) Object.assign(result, healState);
            }

            // ── Parse button/event messages ──
            const event = parseButtonEvent(text);
            if (event) {
                result.action = event.action;
                console.error(`[C4 BUTTON] ${c4DeviceLabel(msg.device)} ${event.type}: ${event.action}`);

                // Passive self-heal (issue #117): a local load paddle half only
                // exists on load-bearing hardware, so a paddle event proves the
                // device drives a load. Upgrade an assumed keypad / unclassified
                // device to keypaddim (never downgrades an existing load type).
                if (event.paddle) {
                    const healState = applyC4Heal(msg.device, currentType, {paddle: true});
                    if (healState) Object.assign(result, healState);
                }

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
                                    console.error(`[C4 BUTTON] ${c4DeviceLabel(msg.device)} Smart behavior: genOnOff.${cmd}`);
                                    await ep1.command('genOnOff', cmd, {});
                                }
                            }
                        } catch (err) {
                            console.error(`[C4 BUTTON] ${c4DeviceLabel(msg.device)} Smart behavior failed: ${err.message}`);
                        }
                    }
                }

                // Sync Z2M's load state after any button event. Manual paddle
                // presses never report their state (no ZCL reporting), and the
                // smart-behavior genOnOff command above ALSO does not update Z2M
                // state on its own, so this debounced read covers both paths.
                scheduleC4StateRead(msg.device, meta.state?.c4_device_type);

                return result;
            }

            // ── Parse unsolicited load-status telemetry (issue #101) ──
            //
            // Devices push their new load level on every change. Coalesce
            // the dim-ramp burst and publish the settled state/brightness.
            const loadStatus = parseLoadStatus(text);
            if (loadStatus) {
                console.error(`[C4 LS] ${c4DeviceLabel(msg.device)} Load level ${loadStatus.level}%`);

                // Passive self-heal: unsolicited ls telemetry proves the device
                // drives a load, so an assumed keypad becomes keypaddim (issue #115).
                const healState = applyC4Heal(msg.device, currentType, {ls: true});
                if (healState) Object.assign(result, healState);

                // Mute the raw c4_response for unsolicited ls telemetry: the
                // debounced scheduleC4LoadStatePublish below already carries the
                // level to HA, so echoing c4_response here would double MQTT
                // volume during a ramp (issue #118). Query responses (the 0r<seq>
                // form) are NOT ls telemetry and keep publishing c4_response via
                // the fall-through return below; only this telemetry case is muted.
                delete result.c4_response;
                delete result.c4_response_ep;

                scheduleC4LoadStatePublish(msg.device, loadStatus.level, publish);
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
    // Arm the self-heal probe campaign for quiet assumed-keypads at startup
    // (issue #123). The light() extend adds no onEvent, so a definition-level
    // onEvent composes cleanly. Only the trailing opts is dropped here; z2m's
    // extra meta arg is intentionally ignored.
    onEvent: async (type, data, device, options, state) =>
        c4OnEvent(type, data, device, options, state),
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
            const {deviceType, dimCode, confidence} = await detectDeviceType(device);
            if (!device.meta) device.meta = {};
            device.meta.c4_device_type = deviceType;
            device.meta.c4_type_confidence = confidence;
            device.meta.c4_dim_code = dimCode ?? null;
            device.save();
            console.error(`[C4 CONFIG] Auto-detected device type: ${deviceType} (${MODEL_NAMES[deviceType] ?? 'unknown'}, confidence: ${confidence})`);
        } catch (e) {
            console.error(`[C4 CONFIG] Auto-detection failed (expected on first pairing before Z2M restart): ${e.message}`);
            console.error(`[C4 CONFIG] Run {"c4_detect": true} after restarting Z2M to detect device type and read LED colors.`);
        }

        console.error(`[C4 CONFIG] Device ${device.ieeeAddr} configured.`);
    },
};

// ─── Export ──────────────────────────────────────────────────────────

export default definition;
