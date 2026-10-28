# WHO 4 DIMENSION 22 (split control): which forms the thermo daemon forwards, refuses or dies on

**Epistemic status:** `firmware_observed` (emulated, `harness=full`), 12 gateway
images, two fresh runs per gateway, byte-identical on 12 of 12. Nothing here is
`experimentally_confirmed`: no DIMENSION 22 frame was sent to a physical
gateway, and none should be until the crash is understood on hardware (the
live F461 `2.0.11` behind OWNd#136 rebooted on `*#4*2*22##`).

**Suite:** [`oracle/cases/thermo-dim22.cases`](../oracle/cases/thermo-dim22.cases)
(11 independent `down` steps, `reset=each`, suite SHA-256
`fe5b990d3a9367d5e1e99d7de204364633c0c5436ba476c5510bd0238ec7f284`). Rows:
`results/<product>/<version>/oracle/full/thermo-dim22.tsv`; image and target
SHA-256 are in each file's header.

## Question

OWNd#136 refuses every WHO 4 DIMENSION 22 frame, in any WHERE spelling, on the
strength of the two F461 `020011` crash frames in
[`tools/expected_divergences.yaml`](../tools/expected_divergences.yaml)
(`*#4*2*22##` and `*#4*#0*22##`). The Encyclopedia
(`functional/who-4-temperature-control/dimensions.md`, "DIMENSION 22 - split
control") publishes only the split forms `*#4*3#Z#N*22##` (request) and
`*#4*3#Z#N*#22*MOD*SP*VEL*SWING##` (write), and records the historical
touchscreen's partial writes `*#4*45#1*#22*3**1*1##`, `*#4*45#1*#22*4**1*1##`
and OFF `*#4*45#1*#22*0***##`. No committed row had replayed DIMENSION 22.
The suite asks:

1. Does only the zone / central form crash the thermoregulation daemon, or
   does every DIMENSION 22 frame?
2. Do the published split forms reach the bus, and what address do they carry?
3. What happens to the prefix-less actuator form `*#4*Z#N*22##` and to the
   touchscreen's partial writes?

## Method

Each step is sent to `openserver` against the emulated gateway stack with a
fresh daemon stack per step (`reset=each`), and the driver restarts the target
after a `crash` row (`oracle/driver.py`), so no crash masks another frame's
verdict. `reply` is the command-session answer; `verdict` is `out` when a bus
frame went out, `silent` when nothing did, and `crash` when a daemon of the
stack exited during the step. On F460, F461 and MyHomeServer1 a `nack` after
`out` is the empty-bus TX-ACK rule
([`empty-bus-reply-semantics.md`](empty-bus-reply-semantics.md)), not a
refusal. A supplementary F461 pass with one shared stack (`--reset 0`, restart
only after a crash) reproduced the same 11 rows. The crashing process was
attributed on F461, MH200N and MyHomeServer1 by polling every launched program
after the step and reading its console log.

## Committed oracle rows

| Frame | F450 `020010` | F453 `010100` | F453AV `030014` | F454 `020051` | F459 `020105` | F460 `020012` | F461 `020011` | H4684 `020054` | MH200 `020102` | MH200N `010108` | MH202 `010024` | MyHomeServer1 `028206` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `*#4*3#2#1*22##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out | nack, silent | nack, silent | nack, out | ack, out | nack, out |
| `*#4*3#2#1*#22*1*0215*2*0##` | ack, out | ack, out | ack, out | ack, out | ack, out | nack, out | nack, out | nack, silent | nack, silent | ack, out | ack, out | nack, out |
| `*#4*45#1*#22*3**1*1##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | ack, out | nack, silent |
| `*#4*45#1*#22*4**1*1##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | ack, out | nack, silent |
| `*#4*45#1*#22*0***##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent |
| `*#4*2#1*22##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | ack, out | nack, silent |
| `*#4*2#1*#22*1*0215*2*0##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | ack, out | nack, silent |
| `*#4*2*22##` | nack, **crash** | nack, **crash** | nack, **crash** | nack, **crash** | nack, **crash** | nack, **crash** | nack, **crash** | nack, silent | nack, silent | nack, **crash** | nack, silent | nack, **crash** |
| `*#4*2*#22*1*0215*2*0##` | nack, **crash** | nack, **crash** | nack, **crash** | nack, **crash** | nack, **crash** | nack, **crash** | nack, **crash** | nack, silent | nack, silent | nack, **crash** | nack, silent | nack, **crash** |
| `*#4*#0*22##` | nack, **crash** | nack, **crash** | nack, **crash** | nack, **crash** | nack, **crash** | nack, **crash** | nack, **crash** | nack, silent | nack, silent | nack, **crash** | nack, silent | nack, **crash** |
| `*#4*0*22##` | nack, **crash** | nack, **crash** | nack, **crash** | nack, **crash** | nack, **crash** | nack, **crash** | nack, **crash** | nack, silent | nack, silent | nack, **crash** | nack, silent | nack, **crash** |

## What the rows and bus bytes say

**Only the forms whose WHERE carries no actuator token crash.** On the nine
gateways that crash at all (F450, F453, F453AV, F454, F459, F460, F461, MH200N,
MyHomeServer1) the crash set is exactly `*#4*2*22##`, `*#4*2*#22*1*0215*2*0##`,
`*#4*#0*22##` and `*#4*0*22##`: zone read, zone write, central read, general
read. Where the process was attributed (F461, MH200N, MyHomeServer1) it is
`bt_termo` exiting with qemu status 139, console `qemu: uncaught target signal
11 (Segmentation fault)`, while `openserver`, `scsserver`, `bt_luci`,
`bt_device`, `bt_multi`, `bt_supervisione` and `coso` stay alive. For the other
six crashing gateways the process identity is inferred from the identical rows
and the same `bt_termo` translator in their target files. The ledger knew two
of these four frames for F461; the zone write and the general read are new.

**No form with an actuator token crashed anywhere**, in either run. The
published split request and write `*#4*3#2#1*22##` /
`*#4*3#2#1*#22*1*0215*2*0##` go out on the bus on ten of twelve gateways,
F461 included, as an extended `D1` telegram:

| Frame | Gateways | Telegram | Bytes after `D1` |
|---|---|---|---|
| `*#4*3#2#1*22##` | F450 F453 F453AV F454 F459 MH200N MH202 MyHomeServer1 (`$06D1…`), F460 F461 (`$006D1…`) | `$06D182130316000000\r` | `82 13 03 16 00 00 00` |
| `*#4*3#2#1*#22*1*0215*2*0##` | same ten | `$06D18213021612012B\r` | `82 13 02 16 12 01 2B` |
| `*#4*2#1*22##` | MH202 only | `$06D182130316000000\r` | identical to the `3#2#1` read |
| `*#4*2#1*#22*1*0215*2*0##` | MH202 only | `$06D18213021612012B\r` | identical to the `3#2#1` write |
| `*#4*45#1*#22*3**1*1##` | MH202 only | `$06D1AD130216010300\r` | `AD 13 02 16 01 03 00` |
| `*#4*45#1*#22*4**1*1##` | MH202 only | `$06D1AD130216010400\r` | `AD 13 02 16 01 04 00` |

Same decoding as [`thermo-zone-requests.md`](thermo-zone-requests.md)
(`*#4*2#1*20##` -> `$0499823110\r`: zone 2 = `82`, actuator 1 = `31`;
`*#4*2*60##` -> `$06D102030308000000\r`). The first byte after `D1` is
`0x80 + Z` (`82` for zone 2, `AD` = `0x80 + 45` for the touchscreen's zone 45),
the second is the actuator token (`13` for `N = 1`; only `N = 1` was tested),
then `03` for a read or `02` for a write, then `16` = 22, the dimension. **The
`3#` prefix is not serialised into the telegram**: MH202 emits byte-identical
telegrams for `3#2#1` and `2#1`, for the one `Z=2, N=1` pair tested. It still
matters to the translators: on the nine other forwarding gateways the
prefix-less `2#1` forms are `nack, silent`, so the prefix decides acceptance
even though it leaves no trace on the bus. On every forwarding gateway the
split forms address local zone Z, actuator N on the main bus, dimension 22;
nothing goes through an interface. Write payload, from three samples only:
the third byte `2B` = 43 is consistent with `SP=0215` in half-degree units
(21.5 °C), and is `00` in the two writes whose SP field is empty; one value
does not establish the conversion, and no other oracle row covers it. The
second byte `01` / `03` / `04` tracks MOD; the first byte is `12` for
`VEL=2, SWING=0` and `01` for `VEL=1, SWING=1`, so the fan / swing packing is
not resolvable from two samples. No gateway produced a reply frame (empty bus, no split unit
emulated), so the read-back format is untested.

**By gateway class.** The nine crashing gateways have identical rows except
for the reply to the `3#2#1` write (`ack` on F450, F453, F453AV, F454, F459,
MH200N; `nack` on F460, F461, MyHomeServer1, the empty-bus TX-ACK rule) and the
3-digit RPMsg prefix on F460 / F461. MH202 (`010024`, WHO 4 through `coso`,
not `bt_termo`) never crashes: it forwards both split forms, both prefix-less
`2#1` forms and the two non-OFF touchscreen writes, and refuses the OFF write
and the four non-actuator forms. H4684 (`020054`) and MH200 (`020102`) drop
all eleven. The touchscreen OFF `*#4*45#1*#22*0***##` is `nack, silent` on all
twelve.

**The stack came back after every crash.** No kept run log has a failed
restart, and the F461 shared-stack pass gave the same rows after each of the
four crashes. In the emulation the harness restarts `bt_termo`. A physical
F461 `2.0.11` rebooted on `*#4*2*22##` (OWNd#136); whether that reboot is the
supervisor's reaction to this `bt_termo` crash is not established here, since
the supervisor is not part of the emulated stack.

## Consequence for clients

Every crash row observed here has a WHERE without an actuator token (`Z`,
`0`, `#0`), and no row with an actuator token (`3#Z#N`, `Z#N`, tested with
`Z=2`, `N=1`, and the touchscreen's `45#1`) crashed. A guard that refuses
DIMENSION 22 for those three WHERE classes covers every observed crash;
refusing the published split forms as well (OWNd#136) is wider than the
evidence. The forms `0ZZ` and `#Z` have no rows in this suite: a client that
adds them to the refused set does so as a conservative, untested extension,
not on oracle coverage, and the rows do not clear WHERE spellings, zones,
actuator numbers or payloads the suite did not send. That narrows the
technical claim; it does not by itself make sending DIMENSION 22 to a
physical F461 advisable.

## What the suite does not settle

- Whether the physical F461 supervisor reboots the gateway after the
  `bt_termo` crash, and how long recovery takes.
- What a real split unit answers to the forwarded request and write (empty
  bus here), and the read-back format of DIMENSION 22.
- `bt_termo` behaviour for actuator numbers other than `N = 1`, other zones,
  and other `MOD` / `SP` / `VEL` / `SWING` payloads.
- Whether a populated MyHOME Suite project configuration changes which forms
  the translators accept (`harness=full` loads the factory configuration).
