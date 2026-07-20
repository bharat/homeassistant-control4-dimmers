/**
 * Tests for startup arming of the self-heal probe campaign and for the
 * explicit-negative-answer fast path (GitHub issue #123).
 *
 * The self-heal probe campaign (issue #115) originally armed only when a
 * device emitted C4 text traffic, so quiet keypads sat unprobed for a long
 * time in prod. We now arm every assumed-keypad device at z2m startup via the
 * definition's onEvent 'start' hook. Separately, a true keypad ANSWERS the dim
 * probe with an explicit negative ("0r<seq> n01") rather than timing out, so
 * that answer confirms the keypad immediately instead of burning the full
 * silent-probe budget. These tests also cover the extended log attribution for
 * the [C4 BUTTON] and [C4 LS] prefixes.
 */

import {describe, it, expect, beforeEach, afterEach, vi} from 'vitest';
import definition, {
    C4_CONFIDENCE_CONFIRMED,
    C4_CONFIDENCE_ASSUMED,
    C4_MAX_SILENT_PROBES,
    scheduleC4ProbeCampaign,
    resetC4HealState,
    c4OnEvent,
    classifyDimProbeResponse,
    isC4DimNegativeResponse,
} from '../converters/control4.mjs';

// A tiny fake herdsman device with a spyable save() and a mutable meta.
function makeDevice(ieeeAddr, meta = {}) {
    return {
        ieeeAddr,
        meta: {...meta},
        save: vi.fn(),
        getEndpoint: vi.fn(() => ({ID: 1, read: vi.fn(async () => ({})), command: vi.fn(async () => {})})),
    };
}

describe('classifyDimProbeResponse / isC4DimNegativeResponse (issue #123)', () => {
    it('classifies a timeout (null) as silent', () => {
        expect(classifyDimProbeResponse(null)).toEqual({kind: 'silent'});
    });

    it('classifies a dim code answer as heal', () => {
        expect(classifyDimProbeResponse('0r0001 000 c4.dmx.dim 02'))
            .toEqual({kind: 'heal', dimCode: '02'});
    });

    it('classifies an explicit n01 answer as negative', () => {
        expect(classifyDimProbeResponse('0r0001 n01')).toEqual({kind: 'negative'});
    });

    it('classifies a v01 error answer as negative', () => {
        expect(classifyDimProbeResponse('0r0001 v01')).toEqual({kind: 'negative'});
    });

    it('detects the negative and error forms but not a dim answer', () => {
        expect(isC4DimNegativeResponse('0r0001 n01')).toBe(true);
        expect(isC4DimNegativeResponse('0ra9c8 v01')).toBe(true);
        expect(isC4DimNegativeResponse('0r0001 000 c4.dmx.dim 01')).toBe(false);
        expect(isC4DimNegativeResponse(null)).toBe(false);
    });
});

describe('startup arming via c4OnEvent (issue #123)', () => {
    beforeEach(() => {
        vi.useFakeTimers();
        vi.spyOn(console, 'error').mockImplementation(() => {});
    });

    afterEach(() => {
        resetC4HealState();
        vi.runOnlyPendingTimers();
        vi.useRealTimers();
        vi.restoreAllMocks();
    });

    it('arms a campaign for an assumed keypad; the probe fires after jitter', async () => {
        const device = makeDevice('0x0B01', {c4_device_type: 'keypad'});
        const probeFn = vi.fn(async () => null);

        await c4OnEvent('start', {}, device, {}, undefined, {
            probeFn,
            random: () => 0.5,     // jitter to the middle of the window
            initialMaxMs: 60000,
            backoffMs: 1000,
        });

        // Nothing before the jittered start elapses.
        await vi.advanceTimersByTimeAsync(29999);
        expect(probeFn).not.toHaveBeenCalled();

        await vi.advanceTimersByTimeAsync(1);
        expect(probeFn).toHaveBeenCalledTimes(1);
    });

    it('arms an absent classification by treating it as an assumed keypad', async () => {
        const device = makeDevice('0x0B02', {}); // never detected
        const probeFn = vi.fn(async () => null);

        await c4OnEvent('start', {}, device, {}, undefined, {
            probeFn,
            random: () => 0,
            initialMaxMs: 60000,
            backoffMs: 1000,
        });

        await vi.advanceTimersByTimeAsync(0);
        expect(probeFn).toHaveBeenCalledTimes(1);
    });

    it('does not arm a confirmed keypad', async () => {
        const device = makeDevice('0x0B03', {
            c4_device_type: 'keypad',
            c4_type_confidence: C4_CONFIDENCE_CONFIRMED,
        });
        const probeFn = vi.fn(async () => null);

        await c4OnEvent('start', {}, device, {}, undefined, {probeFn, random: () => 0});
        await vi.advanceTimersByTimeAsync(120000);

        expect(probeFn).not.toHaveBeenCalled();
    });

    it('does not arm a load type', async () => {
        const device = makeDevice('0x0B04', {c4_device_type: 'dimmer'});
        const probeFn = vi.fn(async () => null);

        await c4OnEvent('start', {}, device, {}, undefined, {probeFn, random: () => 0});
        await vi.advanceTimersByTimeAsync(120000);

        expect(probeFn).not.toHaveBeenCalled();
    });

    it('is a no-op for a non-start event type', async () => {
        const device = makeDevice('0x0B05', {c4_device_type: 'keypad'});
        const probeFn = vi.fn(async () => null);

        await c4OnEvent('stop', {}, device, {}, undefined, {probeFn, random: () => 0});
        await vi.advanceTimersByTimeAsync(120000);

        expect(probeFn).not.toHaveBeenCalled();
    });

    it('exposes the onEvent hook on the definition', () => {
        expect(typeof definition.onEvent).toBe('function');
    });
});

describe('startup and fz arming are idempotent (issue #123)', () => {
    beforeEach(() => {
        vi.useFakeTimers();
        vi.spyOn(console, 'error').mockImplementation(() => {});
    });

    afterEach(() => {
        resetC4HealState();
        vi.runOnlyPendingTimers();
        vi.useRealTimers();
        vi.restoreAllMocks();
    });

    it('startup arming followed by fz-side arming does not double-probe', async () => {
        const device = makeDevice('0x0B06', {c4_device_type: 'keypad'});
        const probeFn = vi.fn(async () => null);

        // Startup path arms first.
        await c4OnEvent('start', {}, device, {}, undefined, {
            probeFn,
            random: () => 0,
            initialMaxMs: 60000,
            backoffMs: 1000,
        });

        // The fz path arms the same device again while the campaign is live.
        scheduleC4ProbeCampaign(device, 'keypad', vi.fn(), {
            probeFn,
            random: () => 0,
            backoffMs: 1000,
        });

        await vi.advanceTimersByTimeAsync(0);
        expect(probeFn).toHaveBeenCalledTimes(1);
    });
});

describe('explicit negative answer confirms a keypad immediately (issue #123)', () => {
    beforeEach(() => {
        vi.useFakeTimers();
        vi.spyOn(console, 'error').mockImplementation(() => {});
    });

    afterEach(() => {
        resetC4HealState();
        vi.runOnlyPendingTimers();
        vi.useRealTimers();
        vi.restoreAllMocks();
    });

    it('confirms on the first probe when the device answers n01', async () => {
        const device = makeDevice('0x0B07', {c4_device_type: 'keypad'});
        const publish = vi.fn();
        const probeFn = vi.fn(async () => classifyDimProbeResponse('0r0001 n01'));

        scheduleC4ProbeCampaign(device, 'keypad', publish, {
            probeFn,
            random: () => 0,
            initialMaxMs: 60000,
            backoffMs: 1000,
        });

        await vi.advanceTimersByTimeAsync(0);

        expect(probeFn).toHaveBeenCalledTimes(1);
        expect(device.meta.c4_type_confidence).toBe(C4_CONFIDENCE_CONFIRMED);
        expect(device.save).toHaveBeenCalled();
        expect(publish).toHaveBeenCalledWith(
            expect.objectContaining({
                c4_detect_result: expect.objectContaining({
                    confidence: C4_CONFIDENCE_CONFIRMED,
                    evidence: 'explicit n01 answer',
                }),
            }),
        );

        // No further probes: confirmation stopped the campaign.
        await vi.advanceTimersByTimeAsync(120000);
        expect(probeFn).toHaveBeenCalledTimes(1);
    });

    it('still follows the 3-silent-probe path on timeouts (regression)', async () => {
        const device = makeDevice('0x0B08', {c4_device_type: 'keypad'});
        const publish = vi.fn();
        const probeFn = vi.fn(async () => null); // always a timeout

        scheduleC4ProbeCampaign(device, 'keypad', publish, {
            probeFn,
            random: () => 0,
            initialMaxMs: 60000,
            backoffMs: 1000,
        });

        await vi.advanceTimersByTimeAsync(0);
        await vi.advanceTimersByTimeAsync(1000);
        await vi.advanceTimersByTimeAsync(2000);

        expect(probeFn).toHaveBeenCalledTimes(C4_MAX_SILENT_PROBES);
        expect(device.meta.c4_type_confidence).toBe(C4_CONFIDENCE_CONFIRMED);
        expect(device.meta.c4_device_type).toBe('keypad');

        await vi.advanceTimersByTimeAsync(120000);
        expect(probeFn).toHaveBeenCalledTimes(C4_MAX_SILENT_PROBES);
    });
});

describe('log attribution for [C4 BUTTON] and [C4 LS] (issue #123)', () => {
    const convert = definition.fromZigbee[0].convert;

    function makeMsg(text, ieeeAddr) {
        return {
            data: Buffer.from(text, 'ascii'),
            endpoint: {ID: 197},
            device: makeDevice(ieeeAddr, {}),
        };
    }

    beforeEach(() => {
        vi.useFakeTimers();
        vi.spyOn(console, 'error').mockImplementation(() => {});
    });

    afterEach(() => {
        resetC4HealState();
        vi.runOnlyPendingTimers();
        vi.useRealTimers();
        vi.restoreAllMocks();
    });

    it('includes the ieeeAddr in the [C4 BUTTON] line', async () => {
        const msg = makeMsg('0t0001 sa c4.dmx.bp 01', '0x0C11');
        const meta = {state: {c4_device_type: 'dimmer'}};

        await convert(definition, msg, vi.fn(), {}, meta);

        expect(console.error).toHaveBeenCalledWith(
            expect.stringMatching(/\[C4 BUTTON\].*0x0C11/),
        );
    });

    it('includes the ieeeAddr in the [C4 LS] line', async () => {
        const msg = makeMsg('0t0001 sa c4.dmx.ls 00 00 42 0078 0000 0000', '0x0C12');
        const meta = {state: {c4_device_type: 'keypaddim'}};

        await convert(definition, msg, vi.fn(), {}, meta);

        expect(console.error).toHaveBeenCalledWith(
            expect.stringMatching(/\[C4 LS\].*0x0C12/),
        );
    });
});
