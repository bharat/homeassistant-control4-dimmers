class Light {
    withBrightness() { return this; }
    withColor() { return this; }
    withEndpoint() { return this; }
    withDescription() { return this; }
}

class Enum {
    constructor() {}
    withEndpoint() { return this; }
    withDescription() { return this; }
}

export {Light, Enum};
export const access = {STATE: 1, SET: 2, STATE_SET: 3};
