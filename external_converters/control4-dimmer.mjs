/**
 * Zigbee2MQTT External Converter for Control4 Zigbee Dimmers
 *
 * Control4 dimmers expose standard Zigbee HA profile on endpoint 1:
 *   - Cluster 0x0006 (genOnOff) for on/off
 *   - Cluster 0x0008 (genLevelCtrl) for dimming
 *
 * They also have a proprietary C4 endpoint (0xC4 / 196) with profile 0xC25D
 * that carries button press events (single, double, triple tap).
 *
 * Known quirks:
 *   - modelId is returned as empty string "" from genBasic
 *   - Endpoints 196/197 refuse simpleDescriptor requests (interview failures)
 *   - Device does NOT send ZCL default responses (must use disableDefaultResponse)
 *
 * Factory reset procedure (leave mesh + factory reset):
 *   Press top 13x, bottom 4x, top 13x (13-4-13)
 *
 * Tested models: C4-DIM, C4-KD120, C4-KD277, C4-APD120, LDZ-102-x
 *
 * Based on working SmartThings/Hubitat drivers by pstuart and iankberry.
 */

import * as m from 'zigbee-herdsman-converters/lib/modernExtend';
import {presets, access} from 'zigbee-herdsman-converters/lib/exposes';
import {bind} from 'zigbee-herdsman-converters/lib/reporting';

// ─── Proprietary C4 button event parser (endpoint 0xC4, profile 0xC25D) ───
const fzControl4ButtonEvents = {
    cluster: 'manuSpecificControl4',  // Will be caught by raw handler below
    type: ['raw'],
    convert: (model, msg, publish, options, meta) => {
        // C4 proprietary messages come on endpoint 196 (0xC4)
        if (msg.endpoint?.ID !== 196) return;

        // Parse the raw payload as text
        const payload = msg.data?.toString?.('utf8') || '';

        // C4 dimmer button event protocol:
        //   "sa c4.dm.cc 00 01" = single tap top (on)
        //   "sa c4.dm.cc 00 00" = single tap bottom (off)
        //   "sa c4.dm.cc 00 02" = double tap top
        //   "sa c4.dm.cc 01 02" = double tap bottom
        //   "sa c4.dm.cc 00 03" = triple tap top
        //   "sa c4.dm.cc 01 03" = triple tap bottom
        //   "sa c4.dm.t0c XX"   = dimming from top (XX = hex level)
        //   "sa c4.dm.b0c XX"   = dimming from bottom (XX = hex level)

        if (payload.includes('sa c4.dm.cc 00 01')) return {action: 'tap_top'};
        if (payload.includes('sa c4.dm.cc 00 00')) return {action: 'tap_bottom'};
        if (payload.includes('sa c4.dm.cc 00 02')) return {action: 'double_tap_top'};
        if (payload.includes('sa c4.dm.cc 01 02')) return {action: 'double_tap_bottom'};
        if (payload.includes('sa c4.dm.cc 00 03')) return {action: 'triple_tap_top'};
        if (payload.includes('sa c4.dm.cc 01 03')) return {action: 'triple_tap_bottom'};

        if (payload.includes('sa c4.dm.t0c') || payload.includes('sa c4.dm.b0c')) {
            // Extract the hex level value from the end of the string
            const parts = payload.trim().split(' ');
            const hexLevel = parts[parts.length - 1];
            const level = parseInt(hexLevel, 16);
            if (!isNaN(level)) {
                const brightness_pct = Math.round(level / 255 * 100);
                return {action: 'dimming', action_level: brightness_pct};
            }
        }

        return undefined;
    },
};

// ─── Custom toZigbee converter with disableDefaultResponse ───
// We wrap standard commands to always set disableDefaultResponse: true
const tzControl4OnOff = {
    key: ['state'],
    convertSet: async (entity, key, value, meta) => {
        const state = value.toLowerCase();
        const endpoint = entity.getDevice().getEndpoint(1) || entity;
        const options = {disableDefaultResponse: true};

        if (state === 'on') {
            await endpoint.command('genOnOff', 'on', {}, options);
        } else if (state === 'off') {
            await endpoint.command('genOnOff', 'off', {}, options);
        } else if (state === 'toggle') {
            await endpoint.command('genOnOff', 'toggle', {}, options);
        }

        return {state: {state: state === 'toggle' ? undefined : state.toUpperCase()}};
    },
    convertGet: async (entity, key, meta) => {
        const endpoint = entity.getDevice().getEndpoint(1) || entity;
        await endpoint.read('genOnOff', ['onOff']);
    },
};

const tzControl4Brightness = {
    key: ['brightness', 'brightness_percent'],
    convertSet: async (entity, key, value, meta) => {
        const endpoint = entity.getDevice().getEndpoint(1) || entity;
        const options = {disableDefaultResponse: true};

        let brightness;
        if (key === 'brightness_percent') {
            brightness = Math.round(Number(value) * 2.54);
        } else {
            brightness = Number(value);
        }

        // Use transition time if provided, otherwise default to 5 (0.5 seconds)
        const transtime = meta.message?.transition != null
            ? Math.round(meta.message.transition * 10)
            : 5;

        await endpoint.command(
            'genLevelCtrl',
            'moveToLevelWithOnOff',
            {level: brightness, transtime},
            options,
        );

        return {
            state: {
                brightness,
                state: brightness === 0 ? 'OFF' : 'ON',
            },
        };
    },
    convertGet: async (entity, key, meta) => {
        const endpoint = entity.getDevice().getEndpoint(1) || entity;
        await endpoint.read('genLevelCtrl', ['currentLevel']);
    },
};

// ─── fromZigbee converters for standard cluster reports ───
const fzControl4OnOff = {
    cluster: 'genOnOff',
    type: ['attributeReport', 'readResponse'],
    convert: (model, msg, publish, options, meta) => {
        if (msg.data.onOff !== undefined) {
            return {state: msg.data.onOff ? 'ON' : 'OFF'};
        }
    },
};

const fzControl4Brightness = {
    cluster: 'genLevelCtrl',
    type: ['attributeReport', 'readResponse'],
    convert: (model, msg, publish, options, meta) => {
        if (msg.data.currentLevel !== undefined) {
            return {
                brightness: msg.data.currentLevel,
                state: msg.data.currentLevel > 0 ? 'ON' : 'OFF',
            };
        }
    },
};

// ─── Device definition ───
// Control4 dimmers return empty modelId but manufacturer "Control4" or empty.
// We match on the fingerprint (profile + clusters) since modelId is unreliable.

/** @type{import('zigbee-herdsman-converters/lib/types').DefinitionWithExtend[]} */
export default [
    {
        // Match by zigbeeModel - you'll set this in database.db manually
        // Common values seen: "", "C4-DIM", or the device may report nothing.
        // Use whichever modelId shows up in YOUR database.db after pairing.
        zigbeeModel: [''],
        fingerprint: [
            // Standard Zigbee HA dimmer profile with C4's typical cluster set
            {
                profileId: 260,   // 0x0104 = Zigbee HA
                deviceId: 257,    // 0x0101 = Dimmable Light
                endpoints: [
                    {
                        ID: 1,
                        profileId: 260,
                        deviceId: 257,
                        inputClusters: [0, 3, 4, 5, 6, 8, 10],
                        outputClusters: [],
                    },
                ],
            },
        ],
        model: 'C4-DIM',
        vendor: 'Control4',
        description: 'Control4 Zigbee In-Wall Dimmer',
        fromZigbee: [fzControl4OnOff, fzControl4Brightness, fzControl4ButtonEvents],
        toZigbee: [tzControl4OnOff, tzControl4Brightness],
        exposes: [
            presets.light_brightness(),
            presets.action([
                'tap_top', 'tap_bottom',
                'double_tap_top', 'double_tap_bottom',
                'triple_tap_top', 'triple_tap_bottom',
                'dimming',
            ]),
        ],
        meta: {
            disableDefaultResponse: true,
        },
        configure: async (device, coordinatorEndpoint, definition) => {
            // Use endpoint 1 - the standard Zigbee HA endpoint
            const endpoint = device.getEndpoint(1);
            if (!endpoint) {
                throw new Error('Control4 dimmer: endpoint 1 not found');
            }

            // Bind clusters for reporting
            await bind(endpoint, coordinatorEndpoint, ['genOnOff', 'genLevelCtrl']);

            // Configure reporting for on/off state
            try {
                await endpoint.configureReporting('genOnOff', [
                    {
                        attribute: 'onOff',
                        minimumReportInterval: 0,
                        maximumReportInterval: 3600,
                        reportableChange: 0,
                    },
                ]);
            } catch (e) {
                // C4 dimmers may not support reporting configuration
                // This is non-fatal - we can still poll
                console.log('Control4: genOnOff reporting config failed (non-fatal):', e.message);
            }

            // Configure reporting for brightness level
            try {
                await endpoint.configureReporting('genLevelCtrl', [
                    {
                        attribute: 'currentLevel',
                        minimumReportInterval: 5,
                        maximumReportInterval: 3600,
                        reportableChange: 1,
                    },
                ]);
            } catch (e) {
                console.log('Control4: genLevelCtrl reporting config failed (non-fatal):', e.message);
            }
        },
        // Only use endpoint 1 for standard commands
        endpoint: (device) => {
            return {default: 1};
        },
        options: [
            {
                name: 'transition',
                description: 'Dimming transition time in seconds (default: 0.5)',
                type: 'number',
            },
        ],
    },
];
