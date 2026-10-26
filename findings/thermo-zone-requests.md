# WHO 4 zone requests: status, temperature, actuators and humidity, per gateway

**Epistemic status:** `firmware_observed` (emulated, `harness=full`), 12 gateway
images. Nothing here is `experimentally_confirmed`; the live anchor is one F461
`2.0.11` plant (below).

**Suite:** [`oracle/cases/thermo-zone-requests.cases`](../oracle/cases/thermo-zone-requests.cases)
(23 steps, `reset=each` on `F450 020010` and `reset=0` on the other eleven
targets, suite SHA-256 `b6f67fab0793bc867407ae231155b121230c9103d9c9f1f35068bbac655ecdcc`).
Rows: `results/<product>/<version>/oracle/full/thermo-zone-requests.tsv`.

## Question

A client that cannot use the general heating status `*#4*0##` (refused on nine of
the twelve emulated gateways: `thermo-ts10`, `ownd-pr82-heating`) has to keep each
zone fresh with per-zone requests. MyHOME#185, #257 and #674 are about exactly
that, and MyHOME's climate entity derives `hvac_action` from the actuators
(DIMENSION 20) and shows humidity (DIMENSION 60). No oracle row previously covered
the per-zone requests for a plain zone, an actuator of a zone, the central form
`*#4*#0*...##` or a zone behind an interface (`Z#4#IF`). The suite asks:

1. Which of `*#4*Z##`, `*#4*Z*0##`, `*#4*Z*12##`, `*#4*Z*14##`, `*#4*Z*19##`
   reach the bus?
2. Which gateways forward the actuator request `*#4*Z*20##`, the per-actuator
   form `*#4*Z#N*20##`, the dimensionless actuator status `*#4*Z#N##`, the
   all-zones form `*#4*#0*20##` and the Encyclopedia's all-actuators forms
   `*#4*Z#0*20##` (one zone) and `*#4*0#0*20##` (every zone)?
3. Which gateways forward the humidity request `*#4*Z*60##` and `*#4*0*60##`?
4. Do any gateways actually route WHO 4 requests across `#4#IF` onto a sub-bus
   (`*#4*2#4#01##`, `*#4*2#4#12##`, `*#4*2#4#01*0##`, `*#4*2#4#01*11##`,
   `*#4*2#4#01*19##`, `*#4*2#4#01*20##`, `*#4*2#4#12*20##`, `*#4*2#1#4#01*20##`,
   `*#4*2#4#01*60##`), or do the outbound `bus:` bytes address local devices on
   the main bus?

## Method

Each step is sent to `openserver` against the emulated gateway stack. `reply` is
the command-session answer, `verdict` whether a bus frame went out (`out`),
nothing went out (`silent`), or a gateway daemon exited during the step
(`crash`). On F460, F461 and MyHomeServer1 a `nack` after `out` is the empty-bus
TX-ACK rule ([`empty-bus-reply-semantics.md`](empty-bus-reply-semantics.md)), not
a translator refusal; `nack, silent` means the translator rejected the request
before putting anything on the bus. Crucially, for `#4#IF` interface inputs,
`verdict=out` alone does not prove interface routing: the emitted `bus:` hex
must be checked for an extended interface bridge header (`$06EC<IF>...` or
`$06E4<IF>...`, as seen in `lighting.tsv` and `automation.tsv`) versus a plain
local-bus WHO 4 telegram (`$04...` / `$004...` / `$06D1...`).

## Committed oracle rows

| Frame | F450 `020010` | F453 `010100` | F453AV `030014` | F454 `020051` | F459 `020105` | F460 `020012` | F461 `020011` | H4684 `020054` | MH200 `020102` | MH200N `010108` | MH202 `010024` | MyHomeServer1 `028206` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `*#4*#0*20##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, out | nack, silent | nack, out (no device) |
| `*#4*0#0*20##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, out | nack, silent | nack, out (no device) |
| `*#4*0*60##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, silent | nack, silent | nack, out (no device) |
| `*#4*2##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, out | nack, out | nack, out | ack, out | nack, out (no device) |
| `*#4*2#0*20##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, out | nack, silent | nack, out (no device) |
| `*#4*2#1##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, out | nack, out | nack, silent | nack, **crash** | nack, silent |
| `*#4*2#1#4#01*20##` | nack, out | nack, silent | nack, silent | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, silent | ack, out | nack, out (no device) |
| `*#4*2#1*20##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, out | ack, out | nack, out (no device) |
| `*#4*2#2*20##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, out | ack, out | nack, out (no device) |
| `*#4*2#4#01##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, out | nack, out | nack, silent | nack, **crash** | nack, silent |
| `*#4*2#4#01*0##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, out | nack, out | nack, silent | nack, silent | nack, silent |
| `*#4*2#4#01*11##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent |
| `*#4*2#4#01*19##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, out | nack, out | nack, silent | nack, silent | nack, silent |
| `*#4*2#4#01*20##` | nack, out | nack, silent | nack, silent | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, silent | ack, out | nack, out (no device) |
| `*#4*2#4#01*60##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, silent | nack, silent | nack, out (no device) |
| `*#4*2#4#12##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, out | nack, out | nack, silent | nack, silent | nack, silent |
| `*#4*2#4#12*20##` | nack, out | nack, silent | nack, silent | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, silent | nack, silent | nack, out (no device) |
| `*#4*2*0##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, out | nack, out | nack, out | ack, out | nack, out (no device) |
| `*#4*2*12##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, out | nack, out | nack, out | ack, out | nack, out (no device) |
| `*#4*2*14##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, out | nack, out | nack, out | ack, out | nack, out (no device) |
| `*#4*2*19##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, out | nack, out | nack, out | ack, out | nack, out (no device) |
| `*#4*2*20##` | nack, out | nack, silent | nack, silent | nack, out | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, silent | nack, silent | nack, out (no device) |
| `*#4*2*60##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, out | nack, out (no device) | nack, out (no device) | nack, silent | nack, silent | nack, silent | nack, silent | nack, out (no device) |

## What the rows and bus bytes say

- **Zone status and dimensions 0, 12, 14, 19** (`*#4*2##`, `*#4*2*0##`, ...) go out on
  every gateway (MH202 ACKs them immediately on the command session). A per-zone
  poll on local `WHERE=Z` works everywhere the general `*#4*0##` does not.
- **Actuators, DIMENSION 20, and dimensionless `*#4*2#1##`.**
  - The per-zone form `*#4*2*20##` is dropped by F453, F453AV, H4684, MH200,
    MH200N and MH202 and forwarded (`$04A9023010\r` / `$004A9023010\r`) by the
    other six.
  - The per-actuator Dimension 20 form `*#4*2#N*20##` (tested on `2#1` and `2#2`)
    is forwarded by ten (`$0499823110\r` / `$00499823110\r` for `2#1`,
    `$0499823210\r` / `$00499823210\r` for `2#2`; dropped only by H4684 and
    MH200).
  - The all-zones form `*#4*#0*20##`, the single-zone all-actuators form
    `*#4*2#0*20##`, and the all-zones all-actuators form `*#4*0#0*20##` agree on
    all twelve gateways: forwarded by nine, dropped by H4684, MH200 and MH202.
    `*#4*0#0*20##` emits the identical SCS frame to `*#4*#0*20##` on every
    forwarding gateway, and `*#4*2#0*20##` addresses zone 2 (`82` on F453,
    F453AV and MH200N; `02` on the other six). The MH202 drops every
    all-actuators form and forwards only local `*#4*Z#N*20##`.
  - **Dimensionless `*#4*2#1##` is not an actuator status poll.** Ten gateways
    refuse it (`nack, silent` on nine; **`nack, crash` on MH202 `010024`**, where
    `coso` exits with `SIGSEGV`), and the remaining two (H4684 and MH200) strip
    `#1` and emit the 4-frame zone-status poll for local zone 2
    (`$0499023000\r`, `$0499023012\r`, `$049902300E\r`, `$049902300D\r`). Clients
    must query actuator state via Dimension 20 (`*#4*Z#N*20##` or `*#4*#0*20##`),
    never `*#4*Z#N##`.
- **Humidity, DIMENSION 60.** `*#4*2*60##` and `*#4*0*60##` are dropped by
  F450, F453, F453AV, F454, H4684, MH200, MH200N and MH202; only F459, F460,
  F461 and MyHomeServer1 emit the extended humidity query
  (`$06D102030308000000\r` / `$006D102030308000000\r` for zone 2). Polling
  humidity on the other eight costs a NACK and nothing else.
- **Zero of the twelve gateways route WHO 4 requests across `#4#IF`.**
  Inspecting the outbound `bus:` bytes across all nine `#4#IF` rows shows that
  **no gateway emits an `$06EC<IF>...` or `$06E4<IF>...` sub-bus frame**:
  1. **Prefix truncation to local zone 2 (`H4684`, `MH200` on status/dim 0/19;
     `F450`, `F454`, `F459`, `F460`, `F461`, `MyHomeServer1` on dim 20; `F459`,
     `F460`, `F461`, `MyHomeServer1` on dim 60):**
     - On H4684 and MH200, `*#4*2#4#01##` and `*#4*2#4#12##` emit the exact same
       local zone 2 frames (`$0499023000\r`, `$0499023012\r`, `$049902300E\r`,
       `$049902300D\r`) as `*#4*2##`; `*#4*2#4#01*0##` emits `$0499023000\r` and
       `*#4*2#4#01*19##` emits `$0499023013\r` (both local zone 2).
     - On F450, F454, F459, F460, F461 and MyHomeServer1, `*#4*2#4#01*20##`,
       `*#4*2#4#12*20##` and `*#4*2#1#4#01*20##` all emit `$04A9023010\r` (or
       `$004A9023010\r` on F460/F461), identical to `*#4*2*20##`: `#4#01`,
       `#4#12` and `#1#4#01` are all truncated at the first `#`, polling **local
       zone 2** on the main bus.
     - On F459, F460, F461 and MyHomeServer1, `*#4*2#4#01*60##` emits
       `$06D102030308000000\r` (or `$006D102030308000000\r`), identical to
       `*#4*2*60##` (local zone 2).
     - `*#4*2#4#01*11##` is `nack, silent` on all twelve gateways.
  2. **Trailing-token actuator misparse and `coso` crash on `MH202 010024`:**
     - In the committed TSV rows on MH202, both `*#4*2#4#01*20##` and
       `*#4*2#1#4#01*20##` emit `bus:24 30 34 39 39 38 34 33 31 31 30 0d`
       (`$0499843110\r`). Comparing that telegram byte-for-byte with
       `*#4*2#1*20##` (`$0499823110\r`, zone `2` = `82`, actuator `1` = `31`) and
       `*#4*2#2*20##` (`$0499823210\r`, zone `2` = `82`, actuator `2` = `32`)
       shows that `$0499843110\r` addresses **local zone 4 (`84`), actuator 1
       (`31`)** on the main bus, while `*#4*2#4#12*20##` is `nack, silent`.
     - Likewise, `*#4*2#4#01##` triggers the exact same `nack, crash` verdict as
       `*#4*2#1##`, whereas `*#4*2#4#12##` is `nack, silent`.
     - **Binary provenance (`results/MH202/010024/manifest.tsv`):** MH202 `010024`
       is the only target in the 12-gateway fleet that routes WHO 4 through `coso`
       (`/home/bticino/bin/coso`, SHA-256
       `6865c2d1d441d25effa31275080ded70ad3538a8a49288e3002ffb196b066175`) via
       `libplug_thermo_tr.so` (`/home/bticino/libcoso/libplug_thermo_tr.so`,
       SHA-256
       `5e2da864f01b19f4f14dde76e11786234b36c97f4f6778966d6a32f597d1452b`) and
       `libthermo.so` (`/home/bticino/lib/libthermo.so`, SHA-256
       `a84ac5be4d01eaadceafa4bbc54bee58e6794bf144398d64bb54c1f944686ff2`).
       Static inspection of `libthermo.so` (`device_recognition` at `0x18c4` and
       `scsDest` at `0x1dac`) confirms that 3-token `WHERE` strings `A#B#C` with
       `1 <= B <= 99` and `1 <= C <= 9` are classified as local actuator addresses
       with zone `B` and actuator `C` (`12 > 9` is rejected). In
       `libplug_thermo_tr.so`, `thermo_open2scs` (`0x1f20`) handles `STATE REQUEST`
       (`frame_type == 2`, `0x21b8`--`0x2250`) only when `WHERE` begins with `#`;
       for non-`#` actuator status requests (such as `*#4*2#1##` and
       `*#4*2#4#01##`) the outbound `GList *` remains `NULL` and
       `g_list_first(NULL)->data` (`ldr r3, [r0]` at `0x22b4`,
       `thermo_open2scs+0x394`) faults with `SIGSEGV`. During replay, `coso`'s
       signal handler writes to `/var/volatile/tmp/BTicino.log`:
       ```text
       main[W]: !!! SIGSEV
       main[W]: TRACE:
       main[W]: /home/bticino/bin/coso(sighandl+0x50) [0xabb0]
       main[W]: /lib/libc.so.6(__default_sa_restorer_v2+0) [0x4085c610]
       main[W]: /home/bticino/libcoso/libplug_thermo_tr.so(thermo_open2scs+0x394) [0x40f332b4]
       main[W]: /home/bticino/bin/coso(send_list_field+0x218) [0xb224]
       main[W]: /home/bticino/bin/coso(syn_receive+0x20) [0xb4c0]
       main[W]: /home/bticino/bin/coso(send_msgs2field+0x70) [0xb54c]
       main[W]: /home/bticino/bin/coso(sm_receive+0x94) [0xb5f8]
       main[W]: /home/bticino/bin/coso(appl_receive+0x124) [0xb738]
       ```

## Live anchor

On an F461 `2.0.11` with one standalone thermostat (zone 2, two actuators) the
gateway answered `*#4*2##` with dimensions 0, 12, 14, 13 and the season frame,
`*#4*2*20##` and `*#4*#0*20##` with `*#4*2#1*20*0##` and `*#4*2#2*20*5##`, and
`*#4*2*60##` / `*#4*0*60##` with `*#4*2*60*39##`, which matches the local-zone
`out` rows for the F461 above (2026-10-23, read-only capture).

## What the suite does not settle

- Whether a gateway that drops a local request with the factory configuration
  forwards it once zones are declared in a MyHOME Suite project. The dropped
  requests are rejected by the translator before any bus activity, which does
  not depend on attached devices, but a populated project configuration is not
  loaded in `harness=full`.
- On physical MH202 hardware, `home/bticino/cfg/stack_open.xml` registers `coso`
  under `bt_daemon` supervision, so `bt_daemon` respawns `coso` after the
  `SIGSEGV` caused by `*#4*2#1##` and `*#4*2#4#01##` (and, per static inspection
  of `device_recognition`, any `*#4*Z#N##` or `*#4*Z#4#01..09##` with
  `1 <= N <= 9`); exact recovery timing on live MH202 hardware is not measured
  here.
