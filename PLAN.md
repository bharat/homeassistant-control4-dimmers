# Control4 Zigbee Integration for Home Assistant

## Current State

**This repo** (`homeassistant-control4-dimmers`): Scaffolded HA custom component from the blueprint template. Placeholder code, mixed naming (still references `integration_blueprint` in several files). No real Control4 logic.

**Custom Z2M Docker image** (`z2m/`): Production Docker image bundling our converter + herdsman profile patch. Currently running in production.

## Architecture

```mermaid
graph TB
  subgraph wall [In-Wall Hardware]
    APD120["C4-APD120 Dimmer"]
    KD120["C4-KD120 Keypad Dimmer"]
    KC120["C4-KC120277 Keypad"]
  end

  subgraph z2m_stack [Z2M Stack - Custom Docker Image]
    Converter["control4-dimmer.mjs\n(external converter)"]
    Herdsman["zigbee-herdsman fork\n(C4 profile 0xC25C whitelisted)"]
    Z2M["Zigbee2MQTT"]
  end

  subgraph ha [Home Assistant]
    MQTT_Int["Z2M MQTT Integration"]
    CustomComp["control4_dimmers\n(custom component)"]
    KeypadUI["Keypad Config UI\n(HA frontend panel)"]
  end

  wall -->|Zigbee 2.4GHz| Herdsman
  Herdsman --> Z2M
  Converter --> Z2M
  Z2M -->|MQTT| MQTT_Int
  MQTT_Int --> CustomComp
  CustomComp --> KeypadUI
```

## The Arc: 7 Phases (3 Newer Device Types Only -- No C4-KP6-Z)

### Phase 1: Clean Converter + Test Framework

Build a clean converter with a test harness.

**Directory structure:**

```
z2m/
  converters/
    control4.mjs           -- main converter
  tests/
    control4.test.mjs      -- unit tests for the converter
    fixtures/               -- mock device data, sample C4 responses
  package.json             -- vitest + zigbee-herdsman-converters dev dep
```

**Test coverage targets:**

- Color conversion: HSV-to-RGB, XY-to-RGB, gamma correction, round-trip fidelity
- C4 text protocol: command formatting, sequence numbering, response parsing
- LED color response parsing (`parseLedColorResponse`, `parseDimResponse`)
- Device detection logic (dimmer/keypaddim/keypad from `c4.dmx.dim` response)
- Button event parsing (bp, cc, sc patterns)
- fromZigbee: response queue mechanism, pending query resolution + timeout
- toZigbee: LED set command generation, batch mode, single mode
- Edge cases: empty responses, malformed text, timeout behavior

**Testing approach:** Use `vitest` with mocked `device.getEndpoint()` / `endpoint.sendRequest()`. The converter's architecture (pure functions for parsing, async functions for I/O) makes it naturally testable -- mock the I/O layer and test the logic directly.

### Phase 2: Herdsman Fork with C4 Profile Patch

The EZSP adapter in `zigbee-herdsman` silently drops messages on non-standard Zigbee profiles.

**Proper fix:** Fork `zigbee-herdsman`, apply the patch as a source-level change, publish as a scoped npm package or reference via git URL.

**The patch itself is small** (one line in `src/adapter/ember/ezsp/ezsp.ts`): add `|| apsFrame.profileId === 0xC25C` to the profile whitelist alongside Shelly's custom profile.

**Fork:** [bharat/zigbee-herdsman](https://github.com/bharat/zigbee-herdsman.git) (already forked from Koenkk/zigbee-herdsman)

**Steps:**

1. Create a branch `control4-profile-support` on the fork
2. Apply the one-line change in source (`src/adapter/ember/ezsp/ezsp.ts`)
3. Reference via `git+https://github.com/bharat/zigbee-herdsman.git#control4-profile-support` in the Docker build

**Upstream:** Deferred. We'll validate the full approach end-to-end first, then decide if/when to submit a PR. The Shelly precedent (custom profile whitelist) makes the case straightforward when we're ready.

### Phase 3: Custom Docker Image for Z2M

Build a Z2M Docker image that bundles the herdsman fork + converter. This is the deployment vehicle for production.

**Dockerfile approach:**

```
z2m/
  Dockerfile              -- based on koenkk/zigbee2mqtt, overlays our converter + herdsman
  docker-compose.yml      -- for local dev/testing
  .env.example            -- MQTT broker, Z2M data dir, coordinator device
```

**Build strategy:**

- Base: `koenkk/zigbee2mqtt:latest` (official image)
- Layer 1: Replace `zigbee-herdsman` with our fork (`npm install` or file copy)
- Layer 2: Copy `control4.mjs` into `/app/data/external_converters/`
- Result: Drop-in replacement for the stock Z2M image

**CI/CD:**

- GitHub Actions workflow: build on push to `main`, tag-based releases
- Push to `ghcr.io/bharat/zigbee2mqtt-control4:latest` and `:vX.Y.Z`
- Fast-track dev: `make deploy` script that builds locally and `docker save | ssh | docker load` to prod server
- Eventually Docker Hub if community adoption warrants

### Phase 4: Complete Device Support

The converter handles the protocol but several features are untested or incomplete:

**Remaining work:**

- Test unified converter with all 3 newer device types (APD120, KD120, KC120277)
- Test button events (bp, sc, cc) reaching HA as action events
- Test LED color control per-button (modes 03/04/05 for all 6 slots)
- Test smart behavior (button press triggers genOnOff toggle on EP1)
- Test `c4_detect` auto-population of stored LED colors
- Expose `c4.dmx.ls` telemetry as HA sensor entities (voltage, current, power, temperature, energy)
- Expose dimming table parameters (`c4.dm.tv`) as HA number entities (ramp rates, min/max brightness)
- Batch migration of remaining ~29 dimmers

### Phase 5: HA Custom Component (Keypad Configuration)

Once Z2M handles all device communication, the HA custom component serves a focused role: **keypad button configuration UI**.

The 6-slot C4 keypads have a modular button layout (1/2/3-slot buttons, rockers, up/down) that's configured via drag-and-drop in Composer Pro. We need an equivalent in HA.

**Scope:**

- Config flow: discover Control4 devices from Z2M MQTT
- Keypad configuration entity: button layout, per-button behavior, LED colors
- No device communication -- all commands route through Z2M MQTT
- HACS-compatible for easy community installation

### Phase 6: Keypad Configuration Frontend

A custom HA frontend panel (Lovelace card or panel) for visual keypad configuration:

- Visual representation of the 6-slot chassis
- Drag-and-drop button assembly (1-slot, 2-slot, 3-slot, rocker)
- Per-button settings: name, behavior (keypad/toggle/on/off), LED on-color, LED off-color
- Live preview of LED colors
- Writes configuration via MQTT to Z2M, which sends C4 text commands

This is the biggest UX piece and should come after all device support is solid.

### Phase 7: Upstream Contributions (Deferred)

Not starting upstream PRs until the full approach is validated end-to-end. When we're ready:

- PR to `zigbee-herdsman`: C4 profile whitelist (one-line change, Shelly precedent)
- PR to `zigbee-herdsman-converters`: Control4 device converter
- Clean up `console.error` debug calls, add proper Z2M logging
- Document the `sendRequest()` bypass (may need an official API from herdsman)
- Community documentation: migration guide, supported devices, troubleshooting

## Immediate Next Steps (What We Build First)

1. Set up `z2m/` directory with the cleaned converter and test framework
2. Write initial test suite covering color math, protocol formatting, and detection logic
3. Set up Docker build pipeline (Dockerfile + GitHub Actions)
4. Clean the HA custom component scaffold (fix naming, remove placeholder code)
