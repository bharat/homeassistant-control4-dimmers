/**
 * Control4 Zigbee Text Protocol — Pure Logic Module
 *
 * This module contains all pure functions and constants for the C4 text
 * protocol. It has ZERO external dependencies so it can be tested directly
 * without mocking Z2M or zigbee-herdsman.
 *
 * The main converter (control4.mjs) imports from here for the I/O layer.
 */

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

// Action values for button events
export const ACTION_VALUES = BUTTONS.flatMap(btn => [
    `button_${btn.idx}_press`,
    `button_${btn.idx}_scene`,
    `button_${btn.idx}_click_1`,
    `button_${btn.idx}_click_2`,
    `button_${btn.idx}_click_3`,
    `button_${btn.idx}_click_4`,
]);

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
//
// Extracted from fromZigbee so we can test event parsing independently.
// Returns {action, buttonId, clickCount} or null if not an event.

export function parseButtonEvent(text) {
    // Button press: 0t<seq> sa c4.dmx.bp <btn>
    const bpMatch = text.match(/^0t\w+ sa c4\.dmx\.bp (\w+)/);
    if (bpMatch) {
        const wireId = parseInt(bpMatch[1], 16);
        const btnIdx = wireId + 1;
        return {action: `button_${btnIdx}_press`, buttonId: btnIdx, type: 'press'};
    }

    // Click count: 0t<seq> sa c4.dmx.cc <btn> <count>
    const ccMatch = text.match(/^0t\w+ sa c4\.dmx\.cc (\w+) (\w+)/);
    if (ccMatch) {
        const wireId = parseInt(ccMatch[1], 16);
        const btnIdx = wireId + 1;
        const count = parseInt(ccMatch[2], 16);
        return {action: `button_${btnIdx}_click_${count}`, buttonId: btnIdx, clickCount: count, type: 'click'};
    }

    // Scene change: 0t<seq> sa c4.dmx.sc <btn>
    const scMatch = text.match(/^0t\w+ sa c4\.dmx\.sc (\w+)/);
    if (scMatch) {
        const wireId = parseInt(scMatch[1], 16);
        const btnIdx = wireId + 1;
        return {action: `button_${btnIdx}_scene`, buttonId: btnIdx, type: 'scene'};
    }

    return null;
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
