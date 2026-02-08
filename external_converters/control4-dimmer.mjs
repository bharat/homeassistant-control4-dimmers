/**
 * Zigbee2MQTT External Converter for Control4 Zigbee Dimmers
 *
 * Control4 dimmers expose standard Zigbee HA profile on endpoint 1:
 *   - Cluster 0x0006 (genOnOff) for on/off
 *   - Cluster 0x0008 (genLevelCtrl) for dimming
 *
 * Known quirks:
 *   - modelId is returned as empty string from genBasic (breaks auto-discovery)
 *   - modelID field is absent from database after failed interview
 *   - Endpoints 196/197 refuse simpleDescriptor requests (interview failures)
 *   - Device does NOT send ZCL default responses (must use disableDefaultResponse)
 *   - manufId (43981 / 0xABCD) IS available even after failed interview
 *
 * Matching strategy: fingerprint on manufacturerID (43981) since modelID
 * is unavailable. zigbeeModel entries are kept for manual database patches.
 *
 * Factory reset: Press top 13x, bottom 4x, top 13x (13-4-13)
 */

import {light} from 'zigbee-herdsman-converters/lib/modernExtend';

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
    extend: [light({configureReporting: false})],
    meta: {disableDefaultResponse: true},
    endpoint: (device) => ({default: 1}),
    configure: async (device, coordinatorEndpoint, definition) => {
        // ONLY configure endpoint 1 — the standard Zigbee HA endpoint.
        // Do NOT touch endpoints 196/197 (proprietary C4).
        const endpoint = device.getEndpoint(1);
        if (!endpoint) return;

        await endpoint.bind('genOnOff', coordinatorEndpoint);
        await endpoint.bind('genLevelCtrl', coordinatorEndpoint);
    },
};

export default definition;
