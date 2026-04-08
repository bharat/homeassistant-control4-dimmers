# Bridging Control4 Dimmers to Home Assistant

We love Control4 hardware. The build quality of their in-wall dimmers is
outstanding — solid, reliable, beautifully designed. We had over 30 of them
throughout the house, and they'd worked flawlessly for years.

But our needs had changed. We wanted the flexibility of Home Assistant —
custom automations, local control, integration with dozens of other device
types. Rather than rip perfectly good hardware out of the walls and replace
it, we wanted to bridge the two ecosystems: keep every Control4 dimmer and
keypad in place, and bring them into Home Assistant alongside everything
else.

The problem was that nobody had built this bridge. Not fully. There were
fragments — a SmartThings driver from 2014, a half-finished Zigbee2MQTT issue
from 2022, a few hopeful forum posts. But no complete solution. No LED
color control, no button events, no keypad support. Just on/off and dimming,
if you were lucky.

This is the story of how we pieced together a complete integration using
publicly available information scattered across a decade of forum posts,
GitHub issues, FCC filings, and one remarkable debug log that someone posted
to a forum in 2013 while troubleshooting their controller.

---

## The Starting Point: These Things Are Zigbee

The first question was whether Control4 dimmers even speak a standard
protocol. A quick look at Control4's own
[data sheet](https://www.control4.com/docs/product/wireless-adaptive-phase-dimmer/data-sheet/english/latest/wireless-adaptive-phase-dimmer-data-sheet-rev-b.pdf)
answered that: "ZigBee, IEEE 802.15.4, 2.4 GHz, 15-channel spread spectrum
radio." The
[installation guide](https://docs.control4.com/docs/product/wireless-adaptive-phase-dimmer/installation-guide-120v277v/english/revision/D/wireless-adaptive-phase-dimmer-installation-guide-120v277v-rev-d.pdf)
confirmed it. And the data sheet mentioned something tantalizing:
"programmable RGB LEDs." The LED color control wasn't a hidden feature — it
was right there on the product page.

If they're Zigbee, a standard coordinator should be able to talk to them.

## Migrating Devices to a New Mesh

The next step: migrating a Control4 dimmer to a new Zigbee mesh. The
factory reset procedure is well-documented. The
[Genesis Technologies button press guide](https://technet.genesis-technologies.ch/control4-zigbee-the-definitive-guide/)
has a comprehensive table for every Control4 Zigbee product ever made.
For in-wall dimmers and keypads, it's **13-4-13**: press the top button 13
times, bottom 4 times, top 13 times. The LEDs flash green, and the device
leaves its mesh.

The [c4forums.com community](https://www.c4forums.com/forums/topic/27777-keypad-dimmer-reset/)
explains why this specific sequence was chosen — it's too complex to trigger
accidentally, even by a child mashing buttons. The procedure is also in the
[FCC-filed user manual](https://fccid.io/R33C4APDKD/User-Manual/USER-MANUAL-1968590).

After the reset, press the top button 4 times. The LEDs blink yellow
(searching), then turn blue (joined). We enabled permit-join in Zigbee2MQTT,
and the dimmer appeared almost immediately.

## On/Off and Dimming Work Immediately

The device showed up in Zigbee2MQTT, but the interview partially failed — a
cosmetic issue we'd fix later. What mattered was that **on/off and dimming
worked immediately.** The dimmer responded to standard Zigbee commands on
endpoint 1 as if it were any other Zigbee light.

We weren't the first to discover this. Patrick Stuart (pstuart) had proven
it on SmartThings back in
[July 2014](https://community.smartthings.com/t/control4-keypad-zigbee-driver/3563).
With help from SmartThings engineer Andrew Urman, he'd decoded the endpoint
structure: endpoint 01 speaks standard Zigbee Home Automation (profile
0x0104, device type 0x0101 Dimmable Light) with all the expected clusters —
genOnOff, genLevelCtrl, genGroups, genScenes. His
[open-source driver](https://github.com/pstuart/smartthings-ps/blob/master/devicetypes/Control4%20Zigbee%20HA%20Dimmer.groovy)
was later [ported to Hubitat](https://github.com/iankberry/hubitat-control4-dimmer)
by iankberry, confirming continued compatibility.

There was even a
[zigbee-herdsman issue from 2020](https://github.com/Koenkk/zigbee-herdsman/issues/160)
where someone tried to get C4 dimmers working with Zigbee2MQTT. The
maintainer (Koenkk) suggested adding `disableDefaultResponse: true` — a
small but critical detail we'd need later.

Basic dimming was solved. But the LEDs were wrong — dim blue instead of
the original white-on-top, blue-on-bottom pattern that Control4 sets by
default. And there were no button events. The dimmer was a basic light — none of the thoughtful features that
make Control4 hardware special. We wanted to preserve the full experience.

## The Two-Layer Architecture

pstuart's 2014 SmartThings thread revealed why the LEDs weren't working.
When he paired his C4 keypad, the endpoint scan showed not just endpoint 01
(standard Zigbee HA) but also **endpoints C4, C5, and C6** running on
proprietary profiles. Button presses and LED control lived on these
proprietary endpoints — not the standard ones.

The raw Zigbee messages pstuart captured told the whole story. Here's one
he posted, with the hex payload:

```
profileId: 0xc25c, sourceEndpoint: 0xc5, destinationEndpoint: 0xc5,
data: [0x66, 0x64, 0x33, 0x20, 0x73, 0x61, 0x20, 0x63, 0x34, 0x2e, 0x6b,
       0x70, 0x2e, 0x62, 0x62, 0x20, 0x30, 0x32, 0x0d, 0x0a]
```

Profile 0xC25C. Endpoint 0xC5 (197 decimal). And that data array — what is
it? We converted the hex to ASCII:

```
fd3 sa c4.kp.bb 02\r\n
```

It's a **text command.** Human-readable ASCII. Sequence number "fd3", a
status announcement ("sa"), command `c4.kp.bb` (button begin), button `02`,
terminated with `\r\n`. The proprietary protocol is just text strings sent
over Zigbee.

pstuart had also captured the device's self-identification broadcast on
profile 0xC25D, and decoded it himself on the forum:

> "if I convert [...] from Hex to Ascii, I get `c4:control4_keypad:C4-KP2-Z`"

The two-layer architecture was clear: **standard Zigbee HA for basic on/off
and dimming, proprietary ASCII text protocol for everything else** — LED
colors, button events, device identification, power monitoring.

## The 2013 Debug Log: A Rosetta Stone

While searching for more protocol data, we found something extraordinary on
[c4forums.com](https://www.c4forums.com/forums/topic/13696-what-happened-director-not-accessible-hc800/).
In December 2013 — seven months *before* pstuart's SmartThings work — a user
named "leifmb" had posted a complete system diagnostic log from their
HC-800 controller. They'd rebooted it, captured the diagnostic output, and
pasted the log to the forum seeking help with a connectivity issue.

What the post contained was the **complete initialization sequence** for an
entire house of dimmers. Every command the controller sends to every device
on boot — in plain ASCII text.

The log was a Rosetta Stone. Here's what it contained:

**Dimming table configuration** — nine variables controlling ramp rates,
brightness limits, and cold-start behavior, sent to each dimmer:
```
0s5227 c4.dm.tv 00 02 000003e8    — Click Rate Up = 1000ms
0s5228 c4.dm.tv 00 03 000007d0    — Click Rate Down = 2000ms
0s5229 c4.dm.tv 00 01 00000064    — Default On Brightness = 100%
0s522a c4.dm.tv 00 04 00000fa0    — Hold Ramp Up = 4000ms
0s522b c4.dm.tv 00 05 00000fa0    — Hold Ramp Down = 4000ms
0s522c c4.dm.tv 00 08 00000005    — Min On = 5%
0s522d c4.dm.tv 00 06 00000064    — Max On = 100%
0s522e c4.dm.tv 00 09 00000000    — Cold Start Time = 0ms
0s522f c4.dm.tv 00 0a 00000000    — Cold Start Level = 0%
```

**LED color control** — the exact command format for setting button LED
colors, with mode bytes for ON-state (03), OFF-state (04), and immediate
override (05):
```
0s3434 c4.dmx.led 00 05 ff0000    — button 0, override, red
0s3435 c4.dmx.led 01 05 ff0000    — button 1, override, red
0s3439 c4.dmx.led 02 03 00cc00    — button 2, ON color, green
0s343a c4.dmx.led 02 04 cc0000    — button 2, OFF color, red
```

**Dimmer type identification** — the controller sending `c4.dmx.dim 03` and
every dimmer rejecting it with `v01` (invalid value). The error messages
labeled each device as "Forward Phase Dimmer":
```
0s5231 c4.dmx.dim 03
→ 0r5231 v01
→ Device Forward Phase Dimmer(399): The MIB packet: c4.dmx.dim 03 failed!
```

**The "MIB" label** — the error messages used the term "C4MIBBase" and
"MIB packet", revealing that Control4 internally calls this protocol layer
"MIB" (Management Information Base, a term borrowed from network management):
```
C4MIBBase: Received out of sequence packet() from: 000fff00002edf3f
```

**Device crypto key queries**, **power measurement timers**, and even
command namespaces for **other device types** — window coverings (`c4.wc0`),
HVAC (`c4.hpc`), zone locations (`c4.zr.loc`), and display messages
(`c4.ln.dm`). All using the same text protocol pattern.

All of this posted publicly by a regular user troubleshooting their system.
Nobody set out to document the protocol — but because the protocol is
human-readable ASCII, every debug log is inherently self-documenting.

Similar diagnostic output appears across many c4forums.com threads whenever
users troubleshoot lighting or mesh issues. Threads about
[power outage recovery](https://www.c4forums.com/forums/topic/38193-after-each-power-outage-lights-come-back-on-dim/),
[LED behavior](https://www.c4forums.com/forums/topic/41386-button-and-led-behavior/),
and [unresponsive lights](https://www.c4forums.com/forums/topic/26523-light-won%E2%80%99t-turn-off/)
all contain similar data.

## The DMX Connection

By this point we'd seen `c4.dmx.*`, `c4.dm.*`, `c4.kp.*`, `c4.sy.*`, and
`c4.als.*` as command namespaces. The "dmx" prefix nagged at us. Was it
random, or meaningful?

[DMX512](https://en.wikipedia.org/wiki/DMX512) is the universal standard for
professional lighting control — developed in 1986, used in every concert
venue and theater in the world. Control4 has deep roots in professional lighting — they
[manufacture DMX hardware](https://docs.control4.com/docs/product/vibrant-5-channel-dmx-decoder/dealer-installation-guide/english/latest/),
including a 5-channel DMX decoder that bridges their system to standard
ANSI DMX512-A fixtures.

The naming wasn't random. The `c4.dmx.*` namespace follows patterns familiar
from DMX and its bidirectional extension
[RDM (Remote Device Management)](https://wiki.openlighting.org/index.php/E1.20):
structured parameter addresses, GET/SET semantics, device identification
queries. Control4's Zigbee lighting protocol is inspired by the same domain
where they build hardware. Commands like `c4.dmx.led` (LED color),
`c4.dmx.dim` (dimmer type), `c4.dmx.ls` (load status) map naturally to
DMX-style channel/parameter thinking.

## ArcadeMachinist Decodes the Keypad (2022)

The next critical piece came from @ArcadeMachinist, who filed
[Zigbee2MQTT Issue #15361](https://github.com/Koenkk/zigbee2mqtt/issues/15361)
in December 2022. Working with an older C4-KP6-Z keypad and a Tasmota
ZbBridge for packet capture, they documented the complete `c4.kp.*` command
set:

```
0t<seq> sa c4.kp.bb <btn>       — button down
0t<seq> sa c4.kp.bc <btn>       — button up (click)
0t<seq> sa c4.kp.bh <btn>       — button held
0t<seq> sa c4.kp.be <btn>       — button released after hold
0t<seq> sa c4.kp.cc <btn> <n>   — click count

0s<seq> c4.kp.lv <btn> RRGGBB   — set momentary color
0s<seq> c4.kp.lf <btn> RRGGBB   — set "off" color
0s<seq> c4.kp.lo <btn> RRGGBB   — set "on" color
```

They posted Wireshark screenshots, working converter code, and — critically
— discovered the adapter constraint that had blocked everyone before them.

The problem was that Control4's proprietary text commands are sent on profile
0xC25C, but Zigbee coordinators only process frames on profiles they know
about. To send *and* receive C4 frames, you need to set a custom profile ID
per-call, and only **EZSP-based adapters** (Silicon Labs chips like the
SONOFF ZBDongle-E) support this. **ZNP-based adapters** (Texas Instruments
CC2652) set the profile ID at endpoint registration time in firmware — you
can receive but not send.

ArcadeMachinist figured this out through source code analysis, and Koenkk
(the zigbee-herdsman maintainer)
[confirmed it](https://github.com/Koenkk/zigbee2mqtt/issues/15361):

> "You are right, TI ZNP api doesn't support this. It will always use the
> profile ID of the endpoint."

This was the key insight: **we needed a Silicon Labs coordinator.**

## The Shelly Precedent

With the right adapter in hand, we still needed zigbee-herdsman (the Zigbee
library under Zigbee2MQTT) to actually accept frames on profile 0xC25C.
Out of the box, the EZSP adapter silently drops messages on unknown profiles.

But someone had already solved this problem. Shelly's Zigbee devices use
custom profile 0xC001, and the zigbee-herdsman codebase already contained a
whitelist entry for it:

```typescript
export const CUSTOM_SHELLY_PROFILE_ID = 0xc001;
// ...
apsFrame.profileId === ZSpec.CUSTOM_SHELLY_PROFILE_ID
```

Adding Control4's profile was mechanically identical — one constant
definition and one `||` clause in the incoming message handler. The
[zigbee-herdsman source](https://github.com/Koenkk/zigbee-herdsman) (GPL-3.0
licensed) showed us exactly where.

## Building the Converter

With the protocol structure understood and a working send/receive path, we
could start building the Zigbee2MQTT converter. The architecture had three
layers:

**Layer 1: Standard Zigbee.** On/off and dimming via `genOnOff` and
`genLevelCtrl` on endpoint 1. This was just `light()` from the Z2M
`modernExtend` library, with `configureReporting: false` (C4 devices don't
support standard ZCL reporting) and `disableDefaultResponse: true` (the
key detail from the 2020 zigbee-herdsman issue — C4 devices don't send
default responses, and without this flag every command times out after 6
seconds).

**Layer 2: The text protocol.** Sending raw ASCII bytes as the APS payload
on profile 0xC25C, cluster 1, endpoint 1. The biggest challenge was that
zigbee-herdsman's `endpoint.command()` always wraps payloads in a ZCL
header. The C4 protocol needs *no* framing — just raw ASCII. The solution:
call `endpoint.sendRequest()` directly with a frame object whose
`toBuffer()` returns the raw bytes. TypeScript's `private` modifier isn't
enforced at runtime.

**Layer 3: Response handling.** C4 is asynchronous — you send a query on
endpoint 1, and the response arrives from endpoint 197. We built a
response queue keyed by sequence number, with Promise-based resolution and
a 3-second timeout for devices that don't support a given command.

## Probing the Devices

With the converter framework in place, we could systematically probe our
own hardware. We knew the protocol format from the 2013 debug log and the
2022 GitHub issue. We knew the property names from Control4's publicly
accessible [product documentation](https://docs.control4.com/help/c4/software/cpro/dealer-composer-help/content/composerpro_userguide/set_dimmable_light_properties.htm),
which lists every configurable dimmer property: preset level, click rates,
hold ramp rates, min/max brightness, cold start values, LED colors, dimming
mode, and energy information.

Each property must have a corresponding protocol command. Starting from
the command patterns visible in the 2013 debug log — `c4.dm.tv` for dimming
parameters, `c4.dmx.led` for LED colors, `c4.dmx.dim` for dimmer type —
we sent GET requests and observed responses:

```
0g<seq> c4.dmx.led 01 03    → 0r<seq> 000 c4.dmx.led ffffff
0g<seq> c4.dmx.led 01 04    → 0r<seq> 000 c4.dmx.led 000000
0g<seq> c4.dmx.led 04 04    → 0r<seq> 000 c4.dmx.led 0000ff
0g<seq> c4.dmx.dim          → 0r<seq> 000 c4.dmx.dim 01
```

The LED colors stored in firmware matched the physical behavior: top LED
white when ON (`ffffff`), dark when OFF (`000000`); bottom LED blue when OFF
(`0000ff`). And `c4.dmx.dim` returned `01` — forward-phase dimming. These
colors and settings persist across power cycles and even network migrations,
which meant migrated devices could keep their existing configuration.

We discovered that the `c4.dmx.dim` response uniquely identifies all three
newer Control4 device types: `01` for the C4-APD120 (forward-phase dimmer),
`02` for the C4-KD120 (reverse-phase keypad dimmer), and an error response
for the C4-KC120277 (pure keypad with no load). This was the key to runtime
device detection — all newer C4 devices share identical Zigbee endpoint
structures and can't be differentiated by fingerprinting alone.

## Decoding Button Behavior and LED Modes on the KD120

The C4-KD120 keypad dimmer presented a deeper challenge than simple
dimmers. Its six buttons can each be independently configured for
different behaviors (toggle load, momentary, programmable) and LED modes
(follow load, push/release colors, static colors). Control4's Composer
software sets all of this — but how? The commands weren't in any public
documentation.

We knew from ArcadeMachinist's 2022 work that the older C4-KP6-Z keypad
used `c4.kp.*` commands for button events and LED colors. But when we
tried these on the KD120, every command returned `n01` — not implemented.
The KD120 uses the newer `c4.dmx.*` namespace exclusively.

### The LED Mode Mystery

The mystery started with a simple observation: some buttons on a
Composer-configured KD120 had "push/release" LED behavior — the LED
flashes a different color while the button is physically held down, then
reverts when released. Other buttons had static on/off colors that
tracked the load state. We could set on-color (mode 03) and off-color
(mode 04) easily, and override (mode 05) worked for immediate color
changes. But we couldn't figure out how to switch between LED modes.

We tried `c4.dmx.led` modes 01 and 02. They accepted SET commands
(`000` success) but returned `e00` on GET — write-only. Setting mode 01
to a color on button 5 (previously unconfigured) gave it push/release
behavior — the LED flashed that color on press. Setting mode 01 back to
`000000` disabled the flash. But the relationship was fragile:
re-sending a non-black color to mode 01 didn't always restore the
behavior.

### Systematic Protocol Probing

To solve this properly, we built `scripts/c4mqtt` — a diagnostic tool
that sends C4 commands via MQTT through our Z2M converter and captures
filtered log output with sequence-matched responses. With interactive
mode (`"wait": true` in command files), we could send a command, observe
the physical result at the keypad, and press Enter to continue.

We probed systematically:

**Mode scan** — tried GET on `c4.dmx.led` modes 07 through ff. Every
one returned `e00`. Only modes 01–06 exist, and mode 06 is invalid. The
complete LED mode map:

| Mode | Function | GET | SET |
|------|----------|-----|-----|
| 01 | Push/pressed color | `e00` (write-only) | `000` |
| 02 | Release color | `e00` (write-only) | `000` |
| 03 | On-state color | Returns color | `000` |
| 04 | Off-state color | Returns color | `000` |
| 05 | Override (persistent) | `e00` | `000` |
| 06 | Invalid | `e00` | `e00` |

**Command namespace scan** — probed `c4.dmx.key`, `c4.dmx.btn`,
`c4.dmx.lm`, `c4.dmx.but`, `c4.dmx.bm`, `c4.sy.ver`, `c4.sy.id`, and
`c4.sy.rst`. Response codes told us which commands existed:

- `e00` = command recognized, wrong arguments
- `n01` = command not implemented
- `v01` = command recognized, invalid value
- `000` = success

Two commands returned `e00` instead of `n01`: **`c4.dmx.key`** and
**`c4.dmx.btn`**. Both were recognized by the firmware but we hadn't
found the right argument format.

### Discovering c4.dmx.btn

We tried increasing numbers of arguments on `c4.dmx.btn`. Two args
(`btn 01 00`) returned `e00`. Then three args:

```
0s<seq> c4.dmx.btn 01 00 00    → 0r<seq> v01   (format right, value wrong!)
0s<seq> c4.dmx.btn 01 01 01    → 0r<seq> 000   (success!)
```

The `v01` response was the breakthrough — it meant the three-argument
format was correct, but value `00` was invalid for parameter `00`. We'd
found the syntax: `c4.dmx.btn <button_index> <param_id> <value>`.

We mapped the parameter space by trying every combination. Only parameter
`01` accepted values:

| Param | Values 00–05 | Values 06+ |
|-------|-------------|------------|
| 00 | All `v01` | All `v01` |
| **01** | **All `000`** | **All `000`** |
| 02–10 | All `v01` | All `v01` |

### Button Behavior Values

With interactive pauses, we set parameter 01 to each value on button 5
and physically tested the result:

| Value | Firmware Behavior |
|-------|------------------|
| 00 | Disabled — no response to press |
| 01 | Load on — single press turns load on |
| 02 | Toggle load — each press toggles on/off |
| 03 | Unknown — may be hold-related |
| 04 | Momentary — hold dims load off, release restores |
| 05 | Programmable — no firmware action, software-only events |

This is the command that Control4's Composer uses to configure what each
button does at the firmware level. Values 00–05 correspond to the button
behavior options visible in Composer's UI.

### The Override Discovery

The most important finding was about `c4.dmx.led` mode 05. We'd assumed
override was temporary — the 2013 debug log used it for momentary
effects. But testing revealed it's **persistent**: an override color
stays until another override replaces it, surviving across button
presses, load state changes, and extended time periods.

This explained a confusing observation. After we sent
`c4.dmx.led 02 05 ff0000` (override button 3 to red) and later
`c4.dmx.led 02 05 000000` (override to black), button 3 went black and
*stayed* black — even after we set mode 04 (off-color) to red. The
black override was sitting on top, hiding the on/off colors underneath.

We confirmed this with button 6, which had working Composer LED behavior
(red static, blue flash on press). Changing its button behavior with
`c4.dmx.btn 05 01 00` (disabled) did **not** affect its LED — it
continued showing red with blue flash. LED mode and button behavior are
completely independent systems in the firmware.

### The Practical Solution

With these findings, we chose a pragmatic approach: use mode 05 override
as the primary LED control mechanism, driven by software. Rather than
trying to configure the firmware's internal LED mode switching (which
would require cracking the still-unknown `c4.dmx.key` command or finding
the LED mode parameter in `c4.dmx.btn`), we:

1. Send `c4.dmx.btn <button> 01 <value>` to set the firmware button
   behavior (toggle, load on, disabled, etc.)
2. Store on/off colors in firmware via modes 03/04 (for future use if
   we decode the LED mode command)
3. Send mode 05 override with the correct color based on tracked entity
   state
4. Update the override in real-time via HA state change listeners

This gives us full control over both button behavior and LED colors,
with the LED tracking responding to any HA entity state change within
milliseconds. The remaining mystery — how Composer configures push/release
LED mode — is an optimization opportunity, not a blocker.

### Unsolved: c4.dmx.key

The `c4.dmx.key` command remains undecoded. It appeared in the 2013
HC-800 debug log and returns `e00` (recognized) on the KD120, but every
argument pattern we tried — single values, two-arg, three-arg with
hex colors — returned `e00`. It may require a different value encoding,
or it may be a GET-only command that needs the `0g` prefix (our
diagnostic tool sends `0s` for all commands). This is a candidate for
future investigation.

## What We Built

The result is a bridge that lets Control4 hardware participate fully in a
Home Assistant environment:

- **On/off and dimming** via standard Zigbee, no proprietary protocol needed
- **Per-button LED color control** with ON-state and OFF-state colors,
  using persistent mode 05 overrides driven by HA state tracking
- **Firmware button behavior** — configure each button as toggle, load-on,
  momentary, or programmable via `c4.dmx.btn`
- **Button press events** (press, hold, click count) as HA actions
- **Runtime device detection** — a single protocol query identifies which of
  three C4 device types you have
- **Stored LED color auto-population** — reads existing colors from firmware
  so dimmers retain their original Control4 configuration automatically
- **A custom Z2M Docker image** that bundles the converter and the
  zigbee-herdsman patch for one-step deployment

The project supports three device models (C4-APD120, C4-KD120, C4-KC120277)
and should work with any newer Control4 Zigbee device that shares the same
manufacturer ID (43981 / 0xABCD).

---

## The Sources That Made This Possible

Everything we built rests on information that was already public. Here is
every source we used, in roughly the order we found them.

### The Foundation: Proving Standard Zigbee Works

| Source | What it told us |
|--------|----------------|
| [Control4 Adaptive Phase Dimmer Data Sheet](https://www.control4.com/docs/product/wireless-adaptive-phase-dimmer/data-sheet/english/latest/) | C4 dimmers use Zigbee; LEDs are "programmable RGB" |
| [Control4 Installation Guide (Rev D)](https://docs.control4.com/docs/product/wireless-adaptive-phase-dimmer/installation-guide-120v277v/english/revision/D/) | Zigbee specs, installation requirements |
| [Genesis Technologies Button Press Guide](https://technet.genesis-technologies.ch/control4-zigbee-the-definitive-guide/) | Factory reset (13-4-13) and all other magic button sequences |
| [c4Forums: Keypad Dimmer Reset](https://www.c4forums.com/forums/topic/27777-keypad-dimmer-reset/) | Community confirmation of factory reset procedure |
| [FCC ID R33C4APDKD — User Manual](https://fccid.io/R33C4APDKD/User-Manual/USER-MANUAL-1968590) | Official user manual via FCC filing |
| [FCC ID R33C4APDKD — Internal Photos](https://fccid.io/R33C4APDKD/Internal-Photos/INTERNAL-PHOTOS-1968579) | PCB photos, chipset identification |

### Decoding the Protocol: Community Forum Posts & Open-Source Drivers

| Source | Date | What it told us |
|--------|------|----------------|
| [c4Forums: Director Not Accessible — HC800](https://www.c4forums.com/forums/topic/13696-what-happened-director-not-accessible-hc800/) | Dec 2013 | Complete Director boot log with `c4.dm.tv`, `c4.dmx.led`, `c4.dmx.dim`, `c4.dmx.key`, `c4.dmx.pmti` commands — the earliest public record of the C4 text protocol |
| [SmartThings: Control4 Keypad Zigbee Driver (pstuart)](https://community.smartthings.com/t/control4-keypad-zigbee-driver/3563) | Jul 2014 | Endpoint structure, profile IDs (0xC25C, 0xC25D), hex payloads decoded to ASCII, standard clusters on EP1 |
| [pstuart's SmartThings Driver (GitHub)](https://github.com/pstuart/smartthings-ps/blob/master/devicetypes/Control4%20Zigbee%20HA%20Dimmer.groovy) | 2014 | Apache 2.0 open-source driver; parses `c4.dm.cc` click count events; uses genOnOff and genLevelCtrl |
| [SmartThings: Pairing a Control4 Dimmer](https://community.smartthings.com/t/pairing-a-control4-dimmer/49001) | 2016 | Community confirmation of C4 dimmer pairing |
| [iankberry's Hubitat Driver (GitHub)](https://github.com/iankberry/hubitat-control4-dimmer) | — | Apache 2.0 Hubitat port confirming continued compatibility |
| [zigbee-herdsman Issue #160: C4 Dimmer Support](https://github.com/Koenkk/zigbee-herdsman/issues/160) | 2020 | `disableDefaultResponse: true` workaround for timeout errors |
| [Z2M Issue #4778: C4 Zigbee Module](https://github.com/Koenkk/zigbee2mqtt/issues/4778) | 2020 | Yale lock module using C4 Zigbee, additional community data |
| [HA Community: Pairing C4 LSZ Dimmer Switches](https://community.home-assistant.io/t/pairing-control4-lsz-102p10-w-dimmer-switches/339934) | 2021 | Embernet vs ZPro firmware distinction; older devices incompatible |
| [Z2M Discussion #14420: C4 with HA MQTT](https://github.com/Koenkk/zigbee2mqtt/discussions/14420) | 2022 | Community reports of C4 as "unsupported" in Z2M |
| [Z2M Issue #15361: C4-KP6-Z Keypad (ArcadeMachinist)](https://github.com/Koenkk/zigbee2mqtt/issues/15361) | Dec 2022 | Complete `c4.kp.*` command set; LED control; EZSP vs ZNP adapter constraint; Wireshark captures; working converter code |
| [c4Forums: After Power Outage Lights Come Back On Dim](https://www.c4forums.com/forums/topic/38193-after-each-power-outage-lights-come-back-on-dim/) | — | Troubleshooting thread with potential protocol traces |
| [c4Forums: Button and LED Behavior](https://www.c4forums.com/forums/topic/41386-button-and-led-behavior/) | — | LED behavior settings discussion |

### Understanding the Naming: DMX and Professional Lighting

| Source | What it told us |
|--------|----------------|
| [DMX512 (Wikipedia)](https://en.wikipedia.org/wiki/DMX512) | Industry-standard lighting control protocol; explains the "dmx" in `c4.dmx.*` |
| [ANSI E1.20 RDM Protocol](https://wiki.openlighting.org/index.php/E1.20) | Remote Device Management over DMX; GET/SET/RESPONSE pattern; parameter-based addressing |
| [RDM Developer Resources](https://rdmprotocol.org/rdm/developers/developer-resources) | Standard PIDs for device info, sensor values, configuration |
| [Control4 Vibrant 5-Channel DMX Decoder](https://docs.control4.com/docs/product/vibrant-5-channel-dmx-decoder/dealer-installation-guide/english/latest/) | Proof that Control4 manufactures DMX hardware |
| [Control4 Lighting Design Guide](https://www.control4.com/docs/product/lighting/dealer-design-guide/english/latest/) | C4's lighting ecosystem including DMX integration |

### The Technical Implementation Path

| Source | What it told us |
|--------|----------------|
| [zigbee-herdsman (GitHub)](https://github.com/Koenkk/zigbee-herdsman) | GPL-3.0; Shelly custom profile (0xC001) precedent for whitelisting 0xC25C |
| [zigbee-herdsman-converters (GitHub)](https://github.com/Koenkk/zigbee-herdsman-converters) | GPL-3.0; converter architecture, `modernExtend`, `endpoint.sendRequest()` |
| [Silicon Labs EmberZNet PRO](https://www.silabs.com/developer-tools/zigbee-emberznet) | EZSP protocol documentation for the adapter layer |
| [Control4 Zigbee Best Practices](https://docs.control4.com/docs/product/zigbee/best-practices/english/latest/) | ZigBee PRO mesh networking specs |

### Understanding the Configurable Properties

| Source | What it told us |
|--------|----------------|
| [Set Dimmable Light Properties](https://docs.control4.com/help/c4/software/cpro/dealer-composer-help/content/composerpro_userguide/set_dimmable_light_properties.htm) | Every configurable dimmer property: preset level, click rates, hold ramp rates, min/max, cold start, energy info, dimming mode |
| [Changing LED Colors](https://docs.control4.com/help/c4/software/cpro/dealer-composer-help/content/composerpro_userguide/changing_led_colors_on_a_switch.htm) | Top/Bottom LED On/Off color settings |
| [Configuring Keypad Dimmer Buttons](https://www.control4.com/help/c4/software/cpro/dealer-composer-help/content/composerpro_userguide/configuring_the_keypad_dimmer.htm) | Keypad button settings, LED behavior options |
| [Configure a Configurable Keypad](https://www.control4.com/help/c4/software/cpro/dealer-composer-help/content/composerpro_userguide/configure_a_configurable_keypad.htm) | 6-slot modular chassis, per-button On/Off colors |
| [Control4 DriverWorks API Reference](https://control4.github.io/docs-driverworks-api/) | Lua driver SDK structure (for understanding command architecture) |

---

## Acknowledgments

This project stands on the shoulders of people who shared their work
publicly:

- **Patrick Stuart (pstuart)** — proved in 2014 that C4 dimmers respond to
  standard Zigbee commands, and published the first open-source driver
- **iankberry** — ported the driver to Hubitat, confirming continued
  compatibility
- **ArcadeMachinist** — decoded the keypad protocol in 2022, discovered the
  EZSP/ZNP adapter constraint, and posted working converter code
- **samtherecordman** — early Z2M pioneer, contributed the
  `disableDefaultResponse` discovery
- **leifmb** — posted a detailed diagnostic log in December 2013 while
  troubleshooting an HC-800 — the earliest public record of the C4 text
  protocol
- **Koenkk** — created and maintains Zigbee2MQTT and zigbee-herdsman, the
  open-source foundation everything runs on
