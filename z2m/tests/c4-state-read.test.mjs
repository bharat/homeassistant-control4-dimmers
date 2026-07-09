/**
 * Tests for the debounced load-state read that syncs Z2M after manual
 * paddle presses (GitHub issue #101).
 *
 * Control4 devices do not report ZCL attributes, so a physical paddle
 * press never updates Z2M's light entity. Button events arrive as C4
 * text telemetry; on each event we schedule a debounced ZCL read of the
 * load state so the standard light() handlers can refresh state/brightness.
 */

import {describe, it, expect, beforeEach, afterEach, vi} from 'vitest';
import {
    C4_STATE_READ_DEBOUNCE_MS,
    scheduleC4StateRead,
} from '../converters/control4.mjs';

// A tiny fake herdsman device: one EP1 whose read() is a spy.
function makeDevice(ieeeAddr, {readImpl} = {}) {
    const ep1 = {
        ID: 1,
        read: vi.fn(readImpl ?? (async () => ({}))),
    };
    const device = {
        ieeeAddr,
        getEndpoint: vi.fn((id) => (id === 1 ? ep1 : null)),
    };
    return {device, ep1};
}

describe('scheduleC4StateRead (manual paddle sync)', () => {
    beforeEach(() => {
        vi.useFakeTimers();
        vi.spyOn(console, 'error').mockImplementation(() => {});
    });

    afterEach(() => {
        vi.runOnlyPendingTimers();
        vi.useRealTimers();
        vi.restoreAllMocks();
    });

    it('exports a positive debounce delay', () => {
        expect(C4_STATE_READ_DEBOUNCE_MS).toBeGreaterThan(0);
    });

    it('a single event reads genOnOff + genLevelCtrl on EP1 after the delay', async () => {
        const {device, ep1} = makeDevice('0x0001');

        scheduleC4StateRead(device, 'dimmer');

        // Nothing fires before the debounce window elapses.
        await vi.advanceTimersByTimeAsync(C4_STATE_READ_DEBOUNCE_MS - 1);
        expect(ep1.read).not.toHaveBeenCalled();

        await vi.advanceTimersByTimeAsync(1);

        expect(ep1.read).toHaveBeenCalledTimes(2);
        expect(ep1.read).toHaveBeenNthCalledWith(1, 'genOnOff', ['onOff']);
        expect(ep1.read).toHaveBeenNthCalledWith(2, 'genLevelCtrl', ['currentLevel']);
    });

    it('coalesces a burst of events into exactly one read', async () => {
        const {device, ep1} = makeDevice('0x0002');

        // Five events 100 ms apart, simulating a dimmer paddle hold.
        for (let i = 0; i < 5; i++) {
            scheduleC4StateRead(device, 'keypaddim');
            await vi.advanceTimersByTimeAsync(100);
        }

        // Still within the debounce window after the last event.
        expect(ep1.read).not.toHaveBeenCalled();

        await vi.advanceTimersByTimeAsync(C4_STATE_READ_DEBOUNCE_MS);

        // One read of each attribute, not five.
        expect(ep1.read).toHaveBeenCalledTimes(2);
        expect(ep1.read).toHaveBeenCalledWith('genOnOff', ['onOff']);
        expect(ep1.read).toHaveBeenCalledWith('genLevelCtrl', ['currentLevel']);
    });

    it('never reads for a pure keypad (no load)', async () => {
        const {device, ep1} = makeDevice('0x0003');

        scheduleC4StateRead(device, 'keypad');
        await vi.advanceTimersByTimeAsync(C4_STATE_READ_DEBOUNCE_MS * 2);

        expect(ep1.read).not.toHaveBeenCalled();
        expect(device.getEndpoint).not.toHaveBeenCalled();
    });

    it('reads when the device type is unknown (absent)', async () => {
        const {device, ep1} = makeDevice('0x0004');

        scheduleC4StateRead(device, undefined);
        await vi.advanceTimersByTimeAsync(C4_STATE_READ_DEBOUNCE_MS);

        expect(ep1.read).toHaveBeenCalledTimes(2);
    });

    it('swallows a read failure without throwing', async () => {
        const {device, ep1} = makeDevice('0x0005', {
            readImpl: async () => {
                throw new Error('device unreachable');
            },
        });

        // Scheduling must not throw.
        expect(() => scheduleC4StateRead(device, 'dimmer')).not.toThrow();

        // Draining the timer must not surface the rejection.
        let drainError;
        try {
            await vi.advanceTimersByTimeAsync(C4_STATE_READ_DEBOUNCE_MS);
        } catch (err) {
            drainError = err;
        }
        expect(drainError).toBeUndefined();

        // Both reads were attempted even though the first rejected.
        expect(ep1.read).toHaveBeenCalledTimes(2);
    });

    it('debounces two devices independently', async () => {
        const a = makeDevice('0xAAAA');
        const b = makeDevice('0xBBBB');

        scheduleC4StateRead(a.device, 'dimmer');
        await vi.advanceTimersByTimeAsync(400);
        scheduleC4StateRead(b.device, 'dimmer');

        // Advance so device A's window elapses but device B's does not.
        await vi.advanceTimersByTimeAsync(C4_STATE_READ_DEBOUNCE_MS - 400);
        expect(a.ep1.read).toHaveBeenCalledTimes(2);
        expect(b.ep1.read).not.toHaveBeenCalled();

        // Finish device B's window.
        await vi.advanceTimersByTimeAsync(400);
        expect(b.ep1.read).toHaveBeenCalledTimes(2);
    });
});
