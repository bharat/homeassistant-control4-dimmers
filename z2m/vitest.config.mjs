import {defineConfig} from 'vitest/config';

export default defineConfig({
    test: {
        // Mock Z2M dependencies so tests can import directly from control4.mjs
        alias: {
            'zigbee-herdsman-converters/lib/modernExtend': new URL('./tests/__mocks__/modernExtend.mjs', import.meta.url).pathname,
            'zigbee-herdsman-converters/lib/exposes': new URL('./tests/__mocks__/exposes.mjs', import.meta.url).pathname,
        },
    },
});
