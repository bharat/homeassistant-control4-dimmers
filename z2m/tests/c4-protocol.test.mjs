/**
 * Tests for the Control4 Zigbee protocol module.
 *
 * Tests the pure logic layer (color math, protocol formatting, response
 * parsing, device detection) without any Z2M or zigbee-herdsman dependencies.
 */

import {describe, it, expect, beforeEach} from 'vitest';
import {
    // Constants
    C4_MIB_PROFILE, C4_CLUSTER,
    BUTTONS, LED_IDS, LED_MODES, ACTION_VALUES, C4_LED_GAMMA,
    DIM_TYPE_MAP, MODEL_NAMES, MODEL_DESCRIPTIONS, GENBASIC_ATTRS,

    // Color conversion
    applyGamma, hsvToRgbHex, xyToRgbHex, rgbHexToHs,

    // Sequence counter
    nextSeq, resetSeqCounter, getSeqCounter,

    // Protocol formatting
    formatSetCommand, formatGetCommand, buildRawFrame, buildSendOptions,

    // Response parsing
    parseLedColorResponse, parseDimResponse, parseResponseSeq,

    // Button event parsing
    parseButtonEvent,

    // Device detection
    classifyDeviceType, getButtonsForDeviceType, buildLedColorState,

    // Color validation
    isValidColorHex, normalizeColorHex,
} from '../converters/c4-protocol.mjs';


// ═══════════════════════════════════════════════════════════════════════
// Constants
// ═══════════════════════════════════════════════════════════════════════

describe('Constants', () => {
    it('C4_MIB_PROFILE is 0xC25C', () => {
        expect(C4_MIB_PROFILE).toBe(0xC25C);
        expect(C4_MIB_PROFILE).toBe(49756);
    });

    it('C4_CLUSTER is 1', () => {
        expect(C4_CLUSTER).toBe(1);
    });

    it('BUTTONS has 6 entries with correct IDs', () => {
        expect(BUTTONS).toHaveLength(6);
        expect(BUTTONS[0]).toEqual({idx: 0, id: '00'});
        expect(BUTTONS[5]).toEqual({idx: 5, id: '05'});
    });

    it('LED_IDS maps names to C4 button IDs', () => {
        expect(LED_IDS.top).toBe('01');
        expect(LED_IDS.bottom).toBe('04');
        expect(LED_IDS['0']).toBe('00');
        expect(LED_IDS['5']).toBe('05');
    });

    it('LED_MODES maps on/off to C4 mode codes', () => {
        expect(LED_MODES.on).toBe('03');
        expect(LED_MODES.off).toBe('04');
    });

    it('ACTION_VALUES has 24 entries (6 buttons × 4 action types)', () => {
        // 6 buttons × (press + scene + click_1..4) = 6 × 6 = 36
        // Wait: press, scene, click_1, click_2, click_3, click_4 = 6 per button
        expect(ACTION_VALUES).toHaveLength(36);
        expect(ACTION_VALUES).toContain('button_0_press');
        expect(ACTION_VALUES).toContain('button_5_click_4');
    });

    it('DIM_TYPE_MAP maps dim codes to device types', () => {
        expect(DIM_TYPE_MAP['01']).toBe('dimmer');
        expect(DIM_TYPE_MAP['02']).toBe('keypaddim');
    });

    it('MODEL_NAMES and MODEL_DESCRIPTIONS cover all device types', () => {
        for (const type of ['dimmer', 'keypaddim', 'keypad']) {
            expect(MODEL_NAMES[type]).toBeDefined();
            expect(MODEL_DESCRIPTIONS[type]).toBeDefined();
        }
    });

    it('GENBASIC_ATTRS includes essential ZCL attributes', () => {
        expect(GENBASIC_ATTRS).toContain('manufacturerName');
        expect(GENBASIC_ATTRS).toContain('modelId');
        expect(GENBASIC_ATTRS).toContain('powerSource');
    });
});


// ═══════════════════════════════════════════════════════════════════════
// Color Conversion — Gamma Correction
// ═══════════════════════════════════════════════════════════════════════

describe('applyGamma', () => {
    it('maps 0 to 0', () => {
        expect(applyGamma(0)).toBe(0);
    });

    it('maps 1 to 255', () => {
        expect(applyGamma(1)).toBe(255);
    });

    it('maps 0.5 to ~64 with γ=2.0 (0.5² × 255 = 63.75)', () => {
        expect(applyGamma(0.5)).toBe(64);
    });

    it('clamps negative values to 0', () => {
        expect(applyGamma(-0.5)).toBe(0);
    });

    it('clamps values > 1 to 255', () => {
        expect(applyGamma(1.5)).toBe(255);
    });

    it('compresses low values (γ > 1 makes dark colors darker)', () => {
        // 0.1 → 0.1² = 0.01 → 0.01 × 255 = 2.55 → 3
        expect(applyGamma(0.1)).toBe(3);
    });
});


// ═══════════════════════════════════════════════════════════════════════
// Color Conversion — HSV to RGB Hex
// ═══════════════════════════════════════════════════════════════════════

describe('hsvToRgbHex', () => {
    it('converts pure red (0°, 100%, 1)', () => {
        expect(hsvToRgbHex(0, 100, 1)).toBe('ff0000');
    });

    it('converts pure green (120°, 100%, 1)', () => {
        expect(hsvToRgbHex(120, 100, 1)).toBe('00ff00');
    });

    it('converts pure blue (240°, 100%, 1)', () => {
        expect(hsvToRgbHex(240, 100, 1)).toBe('0000ff');
    });

    it('converts white (0°, 0%, 1)', () => {
        expect(hsvToRgbHex(0, 0, 1)).toBe('ffffff');
    });

    it('converts black (any hue, any sat, 0)', () => {
        expect(hsvToRgbHex(0, 100, 0)).toBe('000000');
        expect(hsvToRgbHex(180, 50, 0)).toBe('000000');
    });

    it('default v=1 when not specified', () => {
        expect(hsvToRgbHex(0, 100)).toBe('ff0000');
    });

    it('handles near-blue (241°, 92%) — the gamma correction use case', () => {
        // Before gamma: would produce 1814ff (washed out)
        // After gamma (γ=2.0): should produce 0000ff or very close
        const result = hsvToRgbHex(241, 92, 1);
        // Blue channel should dominate; red and green should be very low
        const r = parseInt(result.substring(0, 2), 16);
        const g = parseInt(result.substring(2, 4), 16);
        const b = parseInt(result.substring(4, 6), 16);
        expect(b).toBe(255);
        expect(r).toBeLessThan(5);
        expect(g).toBeLessThan(5);
    });

    it('handles yellow (60°, 100%, 1)', () => {
        expect(hsvToRgbHex(60, 100, 1)).toBe('ffff00');
    });

    it('handles cyan (180°, 100%, 1)', () => {
        expect(hsvToRgbHex(180, 100, 1)).toBe('00ffff');
    });

    it('handles magenta (300°, 100%, 1)', () => {
        expect(hsvToRgbHex(300, 100, 1)).toBe('ff00ff');
    });

    it('half brightness red (0°, 100%, 0.5)', () => {
        // v=0.5, so max channel = 0.5, with gamma: (0.5)^2 * 255 = 63.75 ≈ 64
        const result = hsvToRgbHex(0, 100, 0.5);
        expect(result).toBe('400000');
    });
});


// ═══════════════════════════════════════════════════════════════════════
// Color Conversion — XY to RGB Hex
// ═══════════════════════════════════════════════════════════════════════

describe('xyToRgbHex', () => {
    it('returns 000000 when y=0 (singularity)', () => {
        expect(xyToRgbHex(0.3, 0)).toBe('000000');
    });

    it('converts D65 white point (0.3127, 0.3290) to near-white', () => {
        const result = xyToRgbHex(0.3127, 0.3290);
        // Should be close to ffffff (all channels high)
        const r = parseInt(result.substring(0, 2), 16);
        const g = parseInt(result.substring(2, 4), 16);
        const b = parseInt(result.substring(4, 6), 16);
        expect(r).toBeGreaterThan(200);
        expect(g).toBeGreaterThan(200);
        expect(b).toBeGreaterThan(200);
    });

    it('converts red-ish CIE coordinates', () => {
        // CIE coordinates for a reddish color
        const result = xyToRgbHex(0.6, 0.3);
        const r = parseInt(result.substring(0, 2), 16);
        const g = parseInt(result.substring(2, 4), 16);
        expect(r).toBeGreaterThan(g); // Red should dominate
    });
});


// ═══════════════════════════════════════════════════════════════════════
// Color Conversion — RGB Hex to HS
// ═══════════════════════════════════════════════════════════════════════

describe('rgbHexToHs', () => {
    it('converts pure red ff0000', () => {
        expect(rgbHexToHs('ff0000')).toEqual({hue: 0, saturation: 100});
    });

    it('converts pure green 00ff00', () => {
        expect(rgbHexToHs('00ff00')).toEqual({hue: 120, saturation: 100});
    });

    it('converts pure blue 0000ff', () => {
        expect(rgbHexToHs('0000ff')).toEqual({hue: 240, saturation: 100});
    });

    it('converts white ffffff', () => {
        expect(rgbHexToHs('ffffff')).toEqual({hue: 0, saturation: 0});
    });

    it('converts black 000000', () => {
        expect(rgbHexToHs('000000')).toEqual({hue: 0, saturation: 0});
    });

    it('converts yellow ffff00', () => {
        expect(rgbHexToHs('ffff00')).toEqual({hue: 60, saturation: 100});
    });

    it('converts cyan 00ffff', () => {
        expect(rgbHexToHs('00ffff')).toEqual({hue: 180, saturation: 100});
    });

    it('handles C4 default blue 0000ff → hue=240, sat=100', () => {
        const result = rgbHexToHs('0000ff');
        expect(result.hue).toBe(240);
        expect(result.saturation).toBe(100);
    });
});


// ═══════════════════════════════════════════════════════════════════════
// Color Conversion — Round-trip fidelity
// ═══════════════════════════════════════════════════════════════════════

describe('Color round-trip fidelity', () => {
    // For pure colors, HSV→RGB→HS should round-trip perfectly
    const pureCases = [
        {name: 'red',     h: 0,   s: 100, expected: 'ff0000'},
        {name: 'green',   h: 120, s: 100, expected: '00ff00'},
        {name: 'blue',    h: 240, s: 100, expected: '0000ff'},
        {name: 'yellow',  h: 60,  s: 100, expected: 'ffff00'},
        {name: 'cyan',    h: 180, s: 100, expected: '00ffff'},
        {name: 'magenta', h: 300, s: 100, expected: 'ff00ff'},
        {name: 'white',   h: 0,   s: 0,   expected: 'ffffff'},
    ];

    for (const {name, h, s, expected} of pureCases) {
        it(`${name}: HSV(${h}, ${s}) → RGB → HS preserves hue/saturation`, () => {
            const hex = hsvToRgbHex(h, s, 1);
            expect(hex).toBe(expected);
            const hs = rgbHexToHs(hex);
            expect(hs.hue).toBe(h);
            expect(hs.saturation).toBe(s);
        });
    }
});


// ═══════════════════════════════════════════════════════════════════════
// Sequence Counter
// ═══════════════════════════════════════════════════════════════════════

describe('Sequence Counter', () => {
    beforeEach(() => {
        resetSeqCounter(0);
    });

    it('starts from reset value and increments', () => {
        expect(nextSeq()).toBe('0001');
        expect(nextSeq()).toBe('0002');
        expect(nextSeq()).toBe('0003');
    });

    it('wraps around at 0xFFFF', () => {
        resetSeqCounter(0xFFFE);
        expect(nextSeq()).toBe('ffff');
        expect(nextSeq()).toBe('0000');
        expect(nextSeq()).toBe('0001');
    });

    it('pads to 4 hex digits', () => {
        resetSeqCounter(0);
        expect(nextSeq()).toBe('0001');
        resetSeqCounter(0x00FF);
        expect(nextSeq()).toBe('0100');
    });

    it('getSeqCounter returns current value', () => {
        resetSeqCounter(42);
        expect(getSeqCounter()).toBe(42);
        nextSeq();
        expect(getSeqCounter()).toBe(43);
    });
});


// ═══════════════════════════════════════════════════════════════════════
// Protocol Text Formatting
// ═══════════════════════════════════════════════════════════════════════

describe('Protocol Text Formatting', () => {
    it('formatSetCommand creates 0s-prefixed command with CRLF', () => {
        expect(formatSetCommand('a9c8', 'c4.dmx.led 01 03 ffffff'))
            .toBe('0sa9c8 c4.dmx.led 01 03 ffffff\r\n');
    });

    it('formatGetCommand creates 0g-prefixed command with CRLF', () => {
        expect(formatGetCommand('0001', 'c4.dmx.dim'))
            .toBe('0g0001 c4.dmx.dim\r\n');
    });

    it('buildRawFrame returns object with correct structure', () => {
        const frame = buildRawFrame('0s0001 c4.dmx.led 01 03 ffffff\r\n', 1);
        expect(frame.cluster.ID).toBe(C4_CLUSTER);
        expect(frame.cluster.name).toBe('c4Mib');
        expect(frame.command.ID).toBe(0x35);
        expect(frame.header.transactionSequenceNumber).toBe(1);
        expect(frame.header.frameControl.disableDefaultResponse).toBe(true);
    });

    it('buildRawFrame.toBuffer returns raw ASCII bytes', () => {
        const text = '0s0001 c4.dmx.led 01 03 ffffff\r\n';
        const frame = buildRawFrame(text, 1);
        const buf = frame.toBuffer();
        expect(buf).toBeInstanceOf(Buffer);
        expect(buf.toString('ascii')).toBe(text);
    });

    it('buildSendOptions has correct C4 profile and settings', () => {
        const opts = buildSendOptions();
        expect(opts.profileId).toBe(C4_MIB_PROFILE);
        expect(opts.disableDefaultResponse).toBe(true);
        expect(opts.disableResponse).toBe(true);
        expect(opts.sendPolicy).toBe('immediate');
    });
});


// ═══════════════════════════════════════════════════════════════════════
// Response Parsing
// ═══════════════════════════════════════════════════════════════════════

describe('parseLedColorResponse', () => {
    it('extracts hex color from success response', () => {
        expect(parseLedColorResponse('0ra9c8 000 c4.dmx.led ffffff')).toBe('ffffff');
    });

    it('extracts hex color (lowercase)', () => {
        expect(parseLedColorResponse('0r0001 000 c4.dmx.led FF00CC')).toBe('ff00cc');
    });

    it('returns null for error response', () => {
        expect(parseLedColorResponse('0ra9c8 v01')).toBeNull();
    });

    it('returns null for null input', () => {
        expect(parseLedColorResponse(null)).toBeNull();
    });

    it('returns null for undefined input', () => {
        expect(parseLedColorResponse(undefined)).toBeNull();
    });

    it('returns null for empty string', () => {
        expect(parseLedColorResponse('')).toBeNull();
    });

    it('returns null for unrelated response', () => {
        expect(parseLedColorResponse('0r0001 000 c4.dmx.dim 01')).toBeNull();
    });
});

describe('parseDimResponse', () => {
    it('extracts dim type 01 (forward-phase)', () => {
        expect(parseDimResponse('0ra9c8 000 c4.dmx.dim 01')).toBe('01');
    });

    it('extracts dim type 02 (reverse-phase)', () => {
        expect(parseDimResponse('0r0001 000 c4.dmx.dim 02')).toBe('02');
    });

    it('returns null for error response (n01)', () => {
        expect(parseDimResponse('0r0001 n01')).toBeNull();
    });

    it('returns null for null input', () => {
        expect(parseDimResponse(null)).toBeNull();
    });

    it('returns null for timeout (undefined)', () => {
        expect(parseDimResponse(undefined)).toBeNull();
    });

    it('returns null for empty string', () => {
        expect(parseDimResponse('')).toBeNull();
    });
});

describe('parseResponseSeq', () => {
    it('extracts sequence from response', () => {
        expect(parseResponseSeq('0ra9c8 000 c4.dmx.led ffffff')).toBe('a9c8');
    });

    it('extracts sequence from error response', () => {
        expect(parseResponseSeq('0r0001 v01')).toBe('0001');
    });

    it('returns null for telemetry (0t prefix)', () => {
        expect(parseResponseSeq('0t0001 sa c4.dmx.bp 01')).toBeNull();
    });

    it('returns null for set command (0s prefix)', () => {
        expect(parseResponseSeq('0s0001 c4.dmx.led 01 03 ffffff')).toBeNull();
    });

    it('returns null for empty string', () => {
        expect(parseResponseSeq('')).toBeNull();
    });
});


// ═══════════════════════════════════════════════════════════════════════
// Button Event Parsing
// ═══════════════════════════════════════════════════════════════════════

describe('parseButtonEvent', () => {
    it('parses button press (bp)', () => {
        const event = parseButtonEvent('0t0001 sa c4.dmx.bp 01');
        expect(event).toEqual({
            action: 'button_1_press',
            buttonId: 1,
            type: 'press',
        });
    });

    it('parses button press for button 0', () => {
        const event = parseButtonEvent('0ta9c8 sa c4.dmx.bp 00');
        expect(event).toEqual({
            action: 'button_0_press',
            buttonId: 0,
            type: 'press',
        });
    });

    it('parses button press for button 5', () => {
        const event = parseButtonEvent('0tffff sa c4.dmx.bp 05');
        expect(event).toEqual({
            action: 'button_5_press',
            buttonId: 5,
            type: 'press',
        });
    });

    it('parses click count (cc)', () => {
        const event = parseButtonEvent('0t0001 sa c4.dmx.cc 00 04');
        expect(event).toEqual({
            action: 'button_0_click_4',
            buttonId: 0,
            clickCount: 4,
            type: 'click',
        });
    });

    it('parses single click', () => {
        const event = parseButtonEvent('0t0001 sa c4.dmx.cc 01 01');
        expect(event).toEqual({
            action: 'button_1_click_1',
            buttonId: 1,
            clickCount: 1,
            type: 'click',
        });
    });

    it('parses scene change (sc)', () => {
        const event = parseButtonEvent('0t0001 sa c4.dmx.sc 02');
        expect(event).toEqual({
            action: 'button_2_scene',
            buttonId: 2,
            type: 'scene',
        });
    });

    it('returns null for response (0r)', () => {
        expect(parseButtonEvent('0r0001 000 c4.dmx.led ffffff')).toBeNull();
    });

    it('returns null for telemetry status (c4.dmx.ls)', () => {
        expect(parseButtonEvent('0t0001 sa c4.dmx.ls 00 00 64 007a')).toBeNull();
    });

    it('returns null for empty string', () => {
        expect(parseButtonEvent('')).toBeNull();
    });

    it('returns null for non-C4 text', () => {
        expect(parseButtonEvent('hello world')).toBeNull();
    });
});


// ═══════════════════════════════════════════════════════════════════════
// Device Type Detection
// ═══════════════════════════════════════════════════════════════════════

describe('classifyDeviceType', () => {
    it('returns "dimmer" for dim type 01 (APD120 forward-phase)', () => {
        expect(classifyDeviceType('0r0001 000 c4.dmx.dim 01')).toBe('dimmer');
    });

    it('returns "keypaddim" for dim type 02 (KD120 reverse-phase)', () => {
        expect(classifyDeviceType('0r0001 000 c4.dmx.dim 02')).toBe('keypaddim');
    });

    it('returns "keypad" for null response (timeout)', () => {
        expect(classifyDeviceType(null)).toBe('keypad');
    });

    it('returns "keypad" for undefined response', () => {
        expect(classifyDeviceType(undefined)).toBe('keypad');
    });

    it('returns "keypad" for error response (n01)', () => {
        expect(classifyDeviceType('0r0001 n01')).toBe('keypad');
    });

    it('returns "keypaddim" for unknown dim type (future-proofing)', () => {
        expect(classifyDeviceType('0r0001 000 c4.dmx.dim 03')).toBe('keypaddim');
    });
});

describe('getButtonsForDeviceType', () => {
    it('returns 2 buttons for dimmer (idx 1 and 4)', () => {
        const buttons = getButtonsForDeviceType('dimmer');
        expect(buttons).toHaveLength(2);
        expect(buttons[0].idx).toBe(1);
        expect(buttons[1].idx).toBe(4);
    });

    it('returns 6 buttons for keypaddim', () => {
        expect(getButtonsForDeviceType('keypaddim')).toHaveLength(6);
    });

    it('returns 6 buttons for keypad', () => {
        expect(getButtonsForDeviceType('keypad')).toHaveLength(6);
    });
});


// ═══════════════════════════════════════════════════════════════════════
// LED Color State Building
// ═══════════════════════════════════════════════════════════════════════

describe('buildLedColorState', () => {
    it('builds ON state for white LED', () => {
        const state = buildLedColorState(1, 'on', 'ffffff');
        expect(state.state_button_1_on).toBe('ON');
        expect(state.brightness_button_1_on).toBe(254);
        expect(state.color_button_1_on).toEqual({hue: 0, saturation: 0});
        expect(state.color_mode_button_1_on).toBe('hs');
    });

    it('builds OFF state for black (000000)', () => {
        const state = buildLedColorState(4, 'off', '000000');
        expect(state.state_button_4_off).toBe('OFF');
        expect(state.brightness_button_4_off).toBe(0);
    });

    it('builds state for blue LED', () => {
        const state = buildLedColorState(0, 'off', '0000ff');
        expect(state.state_button_0_off).toBe('ON');
        expect(state.brightness_button_0_off).toBe(254);
        expect(state.color_button_0_off).toEqual({hue: 240, saturation: 100});
    });
});


// ═══════════════════════════════════════════════════════════════════════
// Color Hex Validation
// ═══════════════════════════════════════════════════════════════════════

describe('isValidColorHex', () => {
    it('accepts valid 6-digit lowercase hex', () => {
        expect(isValidColorHex('ff0000')).toBe(true);
        expect(isValidColorHex('000000')).toBe(true);
        expect(isValidColorHex('abcdef')).toBe(true);
    });

    it('rejects uppercase (must be lowercase)', () => {
        expect(isValidColorHex('FF0000')).toBe(false);
    });

    it('rejects 3-digit hex', () => {
        expect(isValidColorHex('fff')).toBe(false);
    });

    it('rejects 7-digit hex', () => {
        expect(isValidColorHex('ff00001')).toBe(false);
    });

    it('rejects hex with # prefix', () => {
        expect(isValidColorHex('#ff0000')).toBe(false);
    });

    it('rejects non-hex characters', () => {
        expect(isValidColorHex('gggggg')).toBe(false);
    });

    it('rejects empty string', () => {
        expect(isValidColorHex('')).toBe(false);
    });
});

describe('normalizeColorHex', () => {
    it('strips # prefix', () => {
        expect(normalizeColorHex('#ff0000')).toBe('ff0000');
    });

    it('lowercases uppercase hex', () => {
        expect(normalizeColorHex('FF00CC')).toBe('ff00cc');
    });

    it('handles already-normalized hex', () => {
        expect(normalizeColorHex('0000ff')).toBe('0000ff');
    });

    it('handles # + uppercase', () => {
        expect(normalizeColorHex('#FFFFFF')).toBe('ffffff');
    });
});
