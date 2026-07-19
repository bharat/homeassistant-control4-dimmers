/**
 * Tests for the debounced load-state publish driven by unsolicited
 * c4.dmx.ls telemetry (GitHub issue #101, primary fix).
 *
 * Control4 devices push their new load level on every load change. We
 * coalesce the dim-ramp burst with a trailing-edge debounce and publish
 * the settled state/brightness once, and we demote the #102 ZCL read to a
 * fallback that is skipped whenever ls telemetry already refreshed state.
 */

import {describe, it, expect, beforeEach, afterEach, vi} from 'vitest';
import {
    C4_LOAD_STATE_DEBOUNCE_MS,
    C4_STATE_READ_DEBOUNCE_MS,
    scheduleC4LoadStatePublish,
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

describe('scheduleC4LoadStatePublish (ls telemetry sync)', () => {
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
        expect(C4_LOAD_STATE_DEBOUNCE_MS).toBeGreaterThan(0);
    });

    it('publishes ON with scaled brightness for a mid level (19 -> 48)', async () => {
        const {device} = makeDevice('0x0101');
        const publish = vi.fn();

        scheduleC4LoadStatePublish(device, 19, publish);

        expect(publish).not.toHaveBeenCalled();
        await vi.advanceTimersByTimeAsync(C4_LOAD_STATE_DEBOUNCE_MS);

        expect(publish).toHaveBeenCalledTimes(1);
        expect(publish).toHaveBeenCalledWith({state: 'ON', brightness: 48});
    });

    it('publishes OFF with brightness 0 for level 0', async () => {
        const {device} = makeDevice('0x0102');
        const publish = vi.fn();

        scheduleC4LoadStatePublish(device, 0, publish);
        await vi.advanceTimersByTimeAsync(C4_LOAD_STATE_DEBOUNCE_MS);

        expect(publish).toHaveBeenCalledTimes(1);
        expect(publish).toHaveBeenCalledWith({state: 'OFF', brightness: 0});
    });

    it('coalesces a dim-ramp burst into one publish with the final level', async () => {
        const {device} = makeDevice('0x0103');
        const publish = vi.fn();

        // A burst of ls frames 200 ms apart, ending at level 0 (turned off).
        const levels = [4, 20, 89, 51, 33, 0];
        for (const lvl of levels) {
            scheduleC4LoadStatePublish(device, lvl, publish);
            await vi.advanceTimersByTimeAsync(200);
        }

        // Still within the debounce window after the last frame.
        expect(publish).not.toHaveBeenCalled();

        await vi.advanceTimersByTimeAsync(C4_LOAD_STATE_DEBOUNCE_MS);

        expect(publish).toHaveBeenCalledTimes(1);
        expect(publish).toHaveBeenCalledWith({state: 'OFF', brightness: 0});
    });

    it('debounces and publishes two devices independently', async () => {
        const a = makeDevice('0x01AA');
        const b = makeDevice('0x01BB');
        const publishA = vi.fn();
        const publishB = vi.fn();

        scheduleC4LoadStatePublish(a.device, 100, publishA);
        await vi.advanceTimersByTimeAsync(200);
        scheduleC4LoadStatePublish(b.device, 50, publishB);

        // Advance so device A's window elapses but device B's does not.
        await vi.advanceTimersByTimeAsync(C4_LOAD_STATE_DEBOUNCE_MS - 200);
        expect(publishA).toHaveBeenCalledTimes(1);
        expect(publishA).toHaveBeenCalledWith({state: 'ON', brightness: 255});
        expect(publishB).not.toHaveBeenCalled();

        // Finish device B's window.
        await vi.advanceTimersByTimeAsync(200);
        expect(publishB).toHaveBeenCalledTimes(1);
        expect(publishB).toHaveBeenCalledWith({state: 'ON', brightness: 128});
    });
});

describe('scheduleC4StateRead fallback demotion (issue #101 vs #102)', () => {
    beforeEach(() => {
        vi.useFakeTimers();
        vi.spyOn(console, 'error').mockImplementation(() => {});
    });

    afterEach(() => {
        vi.runOnlyPendingTimers();
        vi.useRealTimers();
        vi.restoreAllMocks();
    });

    it('suppresses the ZCL read when an ls frame arrives before it fires', async () => {
        const {device, ep1} = makeDevice('0x0201');

        // A button event schedules the fallback read.
        scheduleC4StateRead(device, 'dimmer');

        // An ls frame arrives before the read timer fires.
        await vi.advanceTimersByTimeAsync(100);
        scheduleC4LoadStatePublish(device, 42, vi.fn());

        // Let the read window fully elapse.
        await vi.advanceTimersByTimeAsync(C4_STATE_READ_DEBOUNCE_MS);

        // The read was skipped in favor of the ls-telemetry publish.
        expect(ep1.read).not.toHaveBeenCalled();
    });

    it('still fires the ZCL read when no ls frame arrives', async () => {
        const {device, ep1} = makeDevice('0x0202');

        scheduleC4StateRead(device, 'dimmer');
        await vi.advanceTimersByTimeAsync(C4_STATE_READ_DEBOUNCE_MS);

        expect(ep1.read).toHaveBeenCalledTimes(2);
        expect(ep1.read).toHaveBeenNthCalledWith(1, 'genOnOff', ['onOff']);
        expect(ep1.read).toHaveBeenNthCalledWith(2, 'genLevelCtrl', ['currentLevel']);
    });
});
