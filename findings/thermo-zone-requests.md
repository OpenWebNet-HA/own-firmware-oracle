# WHO 4 zone requests: status, temperature, actuators and humidity, per gateway

**Epistemic status:** `firmware_observed` (emulated, `harness=full`), 12 gateway
images. Nothing here is `experimentally_confirmed`; the live anchor is one F461
`2.0.11` plant (below).

**Suite:** [`oracle/cases/thermo-zone-requests.cases`](../oracle/cases/thermo-zone-requests.cases)
(16 independent steps, `reset=each`, suite SHA-256 `03e229a2cd1b…`).
Rows: `results/<product>/<version>/oracle/full/thermo-zone-requests.tsv` once
`suites.yml` has produced them; the tables below come from a local run of the
same images with the same harness (qemu-user 8.2.2, Docker on macOS) and are
superseded by the CI rows.

## Question

A client that cannot use the general heating status `*#4*0##` (refused on nine of
the twelve emulated gateways: `thermo-ts10`, `ownd-pr82-heating`) has to keep each
zone fresh with per-zone requests. MyHOME#185, #257 and #674 are about exactly
that, and MyHOME's climate entity derives `hvac_action` from the actuators
(DIMENSION 20) and shows humidity (DIMENSION 60). No oracle row covered the
per-zone requests for a plain zone, an actuator of a zone, the central form
`*#4*#0*…##` or a zone behind an interface (`Z#4#IF`). The suite asks:

1. Which of `*#4*Z##`, `*#4*Z*0##`, `*#4*Z*12##`, `*#4*Z*14##`, `*#4*Z*19##`
   reach the bus?
2. Which gateways forward the actuator request `*#4*Z*20##`, the per-actuator
   form `*#4*Z#N*20##`, the all-zones form `*#4*#0*20##` and the Encyclopedia's
   all-actuators forms `*#4*Z#0*20##` (one zone) and `*#4*0#0*20##` (every
   zone)?
3. Which gateways forward the humidity request `*#4*Z*60##` and `*#4*0*60##`?
4. What happens to the same requests for a zone behind an interface
   (`*#4*2#4#01##`, `*#4*2#4#01*0##`, `*#4*2#4#01*20##`)?

## Method

Each step is sent to `openserver` on a freshly started gateway. `reply` is the
session answer, `verdict` whether a bus frame went out; on F460, F461 and
MyHomeServer1 a `nack` after `out` is the empty-bus TX-ACK rule
([`empty-bus-reply-semantics.md`](empty-bus-reply-semantics.md)), not a refusal.
`nack, silent` means the translator dropped the request.

## Rows (local run, to be replaced by the CI rows)

| Frame | F450 `020010` | F453 `010100` | F453AV `030014` | F454 `020051` | F459 `020105` | F460 `020012` | F461 `020011` | H4684 `020054` | MH200 `020102` | MH200N `010108` | MH202 `010024` | MyHomeServer1 `028206` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `*#4*#0*20##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, out | nack, silent | nack, out (no device) |
| `*#4*0#0*20##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, out | nack, silent | nack, out (no device) |
| `*#4*0*60##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, silent | nack, silent | nack, out (no device) |
| `*#4*2##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, out | nack, out | nack, out | ack, out | nack, out (no device) |
| `*#4*2#0*20##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, out | nack, silent | nack, out (no device) |
| `*#4*2#1*20##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, out | ack, out | nack, out (no device) |
| `*#4*2#2*20##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, out | ack, out | nack, out (no device) |
| `*#4*2#4#01##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, out | nack, out | nack, silent | **crash** | nack, silent |
| `*#4*2#4#01*0##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, out | nack, out | nack, silent | nack, silent | nack, silent |
| `*#4*2#4#01*20##` | nack, out | nack, silent | nack, silent | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, silent | ack, out | nack, out (no device) |
| `*#4*2*0##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, out | nack, out | nack, out | ack, out | nack, out (no device) |
| `*#4*2*12##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, out | nack, out | nack, out | ack, out | nack, out (no device) |
| `*#4*2*14##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, out | nack, out | nack, out | ack, out | nack, out (no device) |
| `*#4*2*19##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, out | nack, out | nack, out | ack, out | nack, out (no device) |
| `*#4*2*20##` | nack, out | nack, silent | nack, silent | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, silent | nack, silent | nack, out (no device) |
| `*#4*2*60##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, silent | nack, silent | nack, out (no device) |

## What the rows say

- **Zone status and dimensions 0, 12, 14, 19** (`*#4*2##`, `*#4*2*0##`, …) go out on every
  gateway (MH202 ACKs them itself). A per-zone poll works everywhere the general
  `*#4*0##` does not.
- **Actuators, DIMENSION 20.** The per-zone form `*#4*2*20##` is dropped by F453,
  F453AV, H4684, MH200, MH200N and MH202 and forwarded by the other six. The
  per-actuator form `*#4*2#N*20##` is forwarded by ten (dropped by H4684 and
  MH200), the all-zones form `*#4*#0*20##` by nine (dropped by H4684, MH200 and
  MH202). The Encyclopedia's all-actuators forms `*#4*2#0*20##` (one zone) and
  `*#4*0#0*20##` (every zone) follow `*#4*#0*20##` exactly on all twelve: the
  same nine forward them, H4684, MH200 and MH202 drop them. On the bus
  `*#4*0#0*20##` becomes the same SCS frame as `*#4*#0*20##` on every forwarding
  gateway, and `*#4*2#0*20##` a frame addressed to zone 2 (F453, F453AV and
  MH200N write the address byte as `82`, the other six as `02`). So F453,
  F453AV and MH200N, which drop `*#4*2*20##`, forward the `Z#0` and `0#0`
  forms; the MH202 drops every all-actuators form and, of the actuator requests,
  forwards only `*#4*Z#N*20##` (plain or through an interface).
  A client that wants actuator state on an F453-class gateway must ask per
  actuator, per zone with `Z#0`, or for all zones, not with `*#4*Z*20##`.
- **Humidity, DIMENSION 60.** `*#4*2*60##` and `*#4*0*60##` are dropped by
  F450, F453, F453AV, F454, H4684, MH200, MH200N and MH202; only F459, F460,
  F461 and MyHomeServer1 forward them. Polling humidity on the other eight
  costs a NACK and nothing else.
- **A zone behind an interface.** `*#4*2#4#01##` and `*#4*2#4#01*0##` are dropped
  by ten gateways; only H4684 and MH200 forward them. **On the MH202 `010024`
  the zone status through an interface, `*#4*2#4#01##`, crashes the
  thermoregulation daemon** (`nack`, verdict `crash`, the same in three fresh runs; the
  MH200 forwards the same frame); the other two interface forms do not. The actuator request through an interface, `*#4*2#4#01*20##`,
  is forwarded by seven (F450, F454, F459, F460, F461, MH202, MyHomeServer1) and
  dropped by five (F453, F453AV, H4684, MH200, MH200N).

## Live anchor

On an F461 `2.0.11` with one standalone thermostat (zone 2, two actuators) the
gateway answered `*#4*2##` with dimensions 0, 12, 14, 13 and the season frame,
`*#4*2*20##` and `*#4*#0*20##` with `*#4*2#1*20*0##` and `*#4*2#2*20*5##`, and
`*#4*2*60##` / `*#4*0*60##` with `*#4*2*60*39##`, which matches the `out` rows
for the F461 above (2026-10-23, read-only capture).

## What the suite does not settle

- Whether a gateway that drops a request with the factory configuration forwards
  it once zones are declared in a MyHOME Suite project. The dropped requests are
  dropped by the translator before any bus activity, which does not depend on
  attached devices, but the project configuration is not emulated.
- The MH202 crash is a process exit under qemu-user; how the physical gateway's
  supervisor handles it is not observed.
