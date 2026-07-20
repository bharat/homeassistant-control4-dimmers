/**
 * Tests for self-healing of keypads mis-classified by a silent dim-probe
 * timeout (GitHub issue #115).
 *
 * A c4.dmx.dim probe that times out was historically persisted as a
 * permanent "pure keypad" verdict, so a transient Zigbee timeout at
 * detection time became a wrong classification forever. These tests cover
 * the confidence model, the passive heal on load evidence (ls telemetry or
 * a dim answer), and the active probe campaign that confirms true keypads
 * without ever requiring a manual c4_detect.
 */

import {describe, it, expect, beforeEach, afterEach, vi} from 'vitest';
import {
    C4_CONFIDENCE_CONFIRMED,
    C4_CONFIDENCE_ASSUMED,
    C4_MAX_SILENT_PROBES,
    effectiveConfidence,
    healTypeFromEvidence,
    applyC4Heal,
    scheduleC4ProbeCampaign,
    resetC4HealState,
} from '../converters/control4.mjs';

// A tiny fake herdsman device with a spyable save() and a mutable meta.
function makeDevice(ieeeAddr, meta = {}) {
    const device = {
        ieeeAddr,
        meta: {...meta},
        save: vi.fn(),
    };
    return device;
}

describe('effectiveConfidence (backward compatibility)', () => {
    it('treats an explicit confirmed marker as confirmed', () => {
        expect(effectiveConfidence({c4_type_confidence: C4_CONFIDENCE_CONFIRMED}))
            .toBe(C4_CONFIDENCE_CONFIRMED);
    });

    it('treats legacy state without a marker as assumed', () => {
        expect(effectiveConfidence({c4_device_type: 'keypad'})).toBe(C4_CONFIDENCE_ASSUMED);
    });

    it('treats missing meta as assumed', () => {
        expect(effectiveConfidence(undefined)).toBe(C4_CONFIDENCE_ASSUMED);
    });
});

describe('healTypeFromEvidence (pure classification)', () => {
    it('dim code 01 heals a keypad to a dimmer', () => {
        expect(healTypeFromEvidence('keypad', {dimCode: '01'})).toBe('dimmer');
    });

    it('dim code 02 heals a keypad to a keypaddim', () => {
        expect(healTypeFromEvidence('keypad', {dimCode: '02'})).toBe('keypaddim');
    });

    it('an unknown nonzero dim code heals to keypaddim', () => {
        expect(healTypeFromEvidence('keypad', {dimCode: '07'})).toBe('keypaddim');
    });

    it('ls telemetry upgrades a keypad to keypaddim as the safe default', () => {
        expect(healTypeFromEvidence('keypad', {ls: true})).toBe('keypaddim');
    });

    it('ls telemetry upgrades an unclassified device to keypaddim', () => {
        expect(healTypeFromEvidence(undefined, {ls: true})).toBe('keypaddim');
    });

    it('ls telemetry never downgrades an existing dimmer', () => {
        expect(healTypeFromEvidence('dimmer', {ls: true})).toBeNull();
    });

    it('a dim answer matching the current type is a no-op', () => {
        expect(healTypeFromEvidence('dimmer', {dimCode: '01'})).toBeNull();
    });

    it('empty (non-load) evidence never reclassifies', () => {
        expect(healTypeFromEvidence('keypad', {})).toBeNull();
    });
});

describe('applyC4Heal (passive self-heal, issue #115)', () => {
    beforeEach(() => {
        vi.spyOn(console, 'error').mockImplementation(() => {});
    });

    afterEach(() => {
        resetC4HealState();
        vi.restoreAllMocks();
    });

    // (a) timeout-then-heal: a keypad assumed by a silent probe later emits
    // ls telemetry and is reclassified to keypaddim, logged, and persisted.
    it('heals an assumed keypad to keypaddim on ls telemetry', () => {
        const device = makeDevice('0x0A01', {
            c4_device_type: 'keypad',
            c4_type_confidence: C4_CONFIDENCE_ASSUMED,
        });
        const publish = vi.fn();

        const state = applyC4Heal(device, 'keypad', {ls: true}, publish);

        expect(state.c4_device_type).toBe('keypaddim');
        expect(device.meta.c4_device_type).toBe('keypaddim');
        expect(device.meta.c4_type_confidence).toBe(C4_CONFIDENCE_CONFIRMED);
        expect(device.save).toHaveBeenCalled();
        expect(publish).toHaveBeenCalledWith(
            expect.objectContaining({c4_device_type: 'keypaddim'}),
        );
        expect(state.c4_detect_result.healed).toBe(true);
        expect(console.error).toHaveBeenCalledWith(
            expect.stringContaining('[C4 HEAL]'),
        );
    });

    // (b) a dim answer of 01 upgrades a keypad to a dimmer.
    it('heals an assumed keypad to dimmer on a dim answer of 01', () => {
        const device = makeDevice('0x0A02', {
            c4_device_type: 'keypad',
        });
        const publish = vi.fn();

        const state = applyC4Heal(device, 'keypad', {dimCode: '01'}, publish);

        expect(state.c4_device_type).toBe('dimmer');
        expect(device.meta.c4_device_type).toBe('dimmer');
        expect(device.meta.c4_dim_code).toBe('01');
        expect(device.meta.c4_type_confidence).toBe(C4_CONFIDENCE_CONFIRMED);
    });

    // (d) legacy stored keypad state without a confidence marker is treated
    // as assumed and heals on ls telemetry.
    it('heals a legacy keypad that has no confidence marker', () => {
        const device = makeDevice('0x0A04', {c4_device_type: 'keypad'});
        expect(effectiveConfidence(device.meta)).toBe(C4_CONFIDENCE_ASSUMED);

        const state = applyC4Heal(device, 'keypad', {ls: true}, vi.fn());

        expect(state.c4_device_type).toBe('keypaddim');
        expect(device.meta.c4_type_confidence).toBe(C4_CONFIDENCE_CONFIRMED);
    });

    // (e) a confirmed keypad is not reclassified by non-load telemetry.
    it('does not reclassify a confirmed keypad on non-load evidence', () => {
        const device = makeDevice('0x0A05', {
            c4_device_type: 'keypad',
            c4_type_confidence: C4_CONFIDENCE_CONFIRMED,
        });
        const publish = vi.fn();

        const state = applyC4Heal(device, 'keypad', {}, publish);

        expect(state).toBeNull();
        expect(device.meta.c4_device_type).toBe('keypad');
        expect(publish).not.toHaveBeenCalled();
    });

    it('is idempotent once a device is confirmed at the target type', () => {
        const device = makeDevice('0x0A06', {
            c4_device_type: 'keypaddim',
            c4_type_confidence: C4_CONFIDENCE_CONFIRMED,
        });
        const publish = vi.fn();

        const state = applyC4Heal(device, 'keypaddim', {ls: true}, publish);

        expect(state).toBeNull();
        expect(publish).not.toHaveBeenCalled();
    });
});

describe('scheduleC4ProbeCampaign (active self-heal, issue #115)', () => {
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

    // (c) a true keypad goes silent 3 times, becomes confirmed, and is not
    // probed a fourth time.
    it('confirms a true keypad after 3 silent probes and stops', async () => {
        const device = makeDevice('0x0C01', {c4_device_type: 'keypad'});
        const publish = vi.fn();
        const probeFn = vi.fn(async () => null); // always silent

        scheduleC4ProbeCampaign(device, 'keypad', publish, {
            probeFn,
            random: () => 0,      // fire the first probe immediately
            initialMaxMs: 60000,
            backoffMs: 1000,
        });

        // Drive the whole campaign: jittered start, then two backoff gaps.
        await vi.advanceTimersByTimeAsync(0);
        await vi.advanceTimersByTimeAsync(1000);
        await vi.advanceTimersByTimeAsync(2000);

        expect(probeFn).toHaveBeenCalledTimes(C4_MAX_SILENT_PROBES);
        expect(device.meta.c4_device_type).toBe('keypad');
        expect(device.meta.c4_type_confidence).toBe(C4_CONFIDENCE_CONFIRMED);

        // No further probes after confirmation.
        await vi.advanceTimersByTimeAsync(60000);
        expect(probeFn).toHaveBeenCalledTimes(C4_MAX_SILENT_PROBES);
    });

    // (e) a confirmed keypad is never probed again in this process.
    it('never probes a confirmed keypad', async () => {
        const device = makeDevice('0x0C05', {
            c4_device_type: 'keypad',
            c4_type_confidence: C4_CONFIDENCE_CONFIRMED,
        });
        const probeFn = vi.fn(async () => null);

        scheduleC4ProbeCampaign(device, 'keypad', vi.fn(), {probeFn});
        await vi.advanceTimersByTimeAsync(120000);

        expect(probeFn).not.toHaveBeenCalled();
    });

    it('never probes a load-bearing device', async () => {
        const device = makeDevice('0x0C06', {c4_device_type: 'dimmer'});
        const probeFn = vi.fn(async () => null);

        scheduleC4ProbeCampaign(device, 'dimmer', vi.fn(), {probeFn});
        await vi.advanceTimersByTimeAsync(120000);

        expect(probeFn).not.toHaveBeenCalled();
    });

    it('a dim answer during the campaign heals and stops probing', async () => {
        const device = makeDevice('0x0C02', {c4_device_type: 'keypad'});
        const publish = vi.fn();
        const probeFn = vi.fn(async () => '02'); // device answers: it has a load

        scheduleC4ProbeCampaign(device, 'keypad', publish, {
            probeFn,
            random: () => 0,
            backoffMs: 1000,
        });

        await vi.advanceTimersByTimeAsync(0);

        expect(device.meta.c4_device_type).toBe('keypaddim');
        expect(device.meta.c4_type_confidence).toBe(C4_CONFIDENCE_CONFIRMED);

        // Campaign is over; no more probes.
        await vi.advanceTimersByTimeAsync(60000);
        expect(probeFn).toHaveBeenCalledTimes(1);
    });

    it('only starts one campaign per device per process', async () => {
        const device = makeDevice('0x0C03', {c4_device_type: 'keypad'});
        const probeFn = vi.fn(async () => null);

        scheduleC4ProbeCampaign(device, 'keypad', vi.fn(), {probeFn, random: () => 0, backoffMs: 1000});
        // A second call while the first campaign is live must be a no-op.
        scheduleC4ProbeCampaign(device, 'keypad', vi.fn(), {probeFn, random: () => 0, backoffMs: 1000});

        await vi.advanceTimersByTimeAsync(0);
        expect(probeFn).toHaveBeenCalledTimes(1);
    });

    // (f) probes are spaced with growing gaps (jitter, then exponential
    // backoff), asserted via fake timers.
    it('spaces silent probes with exponentially growing gaps', async () => {
        const device = makeDevice('0x0C0F', {c4_device_type: 'keypad'});
        const probeFn = vi.fn(async () => null);

        scheduleC4ProbeCampaign(device, 'keypad', vi.fn(), {
            probeFn,
            random: () => 0,   // deterministic: first probe at t=0
            initialMaxMs: 60000,
            backoffMs: 1000,
        });

        // First probe fires immediately (jitter = 0).
        await vi.advanceTimersByTimeAsync(0);
        expect(probeFn).toHaveBeenCalledTimes(1);

        // Second probe is one backoff unit later; not before.
        await vi.advanceTimersByTimeAsync(999);
        expect(probeFn).toHaveBeenCalledTimes(1);
        await vi.advanceTimersByTimeAsync(1);
        expect(probeFn).toHaveBeenCalledTimes(2);

        // Third probe is two backoff units later (the gap grew).
        await vi.advanceTimersByTimeAsync(1999);
        expect(probeFn).toHaveBeenCalledTimes(2);
        await vi.advanceTimersByTimeAsync(1);
        expect(probeFn).toHaveBeenCalledTimes(3);
    });

    it('resumes the silent count from persisted meta after a restart', async () => {
        // A device that has already gone silent twice before restart.
        const device = makeDevice('0x0C04', {
            c4_device_type: 'keypad',
            c4_silent_probes: 2,
        });
        const publish = vi.fn();
        const probeFn = vi.fn(async () => null);

        scheduleC4ProbeCampaign(device, 'keypad', publish, {
            probeFn,
            random: () => 0,
            backoffMs: 1000,
        });

        // One more silent probe should be enough to confirm (2 + 1 == 3).
        await vi.advanceTimersByTimeAsync(0);

        expect(probeFn).toHaveBeenCalledTimes(1);
        expect(device.meta.c4_type_confidence).toBe(C4_CONFIDENCE_CONFIRMED);
    });
});
