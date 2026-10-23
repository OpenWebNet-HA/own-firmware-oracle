# WHO 1 motion detector sharing an actuator address (MyHOME#777)

**Epistemic status:** `firmware_observed` (emulated, `harness=full`), 12 gateway
images. Rows from the `suites` workflow on PR #56 (run
[39967360131](https://github.com/OpenWebNet-HA/own-firmware-oracle/actions/runs/39967360131)):
every target ran twice and both runs were byte-identical. Nothing here is
`experimentally_confirmed`; the live anchor is the reporter's F454 capture.

**Suite:** [`oracle/cases/who1-motion-detector.cases`](../oracle/cases/who1-motion-detector.cases)
(5 `down` and 18 `up` independent steps, suite SHA-256 `44185bd0a90b…`).
Rows: `results/<product>/<version>/oracle/full/who1-motion-detector.tsv`;
image and target SHA-256 are in each file's header.

## Question

In [OpenWebNet-HA/MyHOME#777](https://github.com/OpenWebNet-HA/MyHOME/issues/777)
a Legrand 067226 detector (no relay) is configured at `A=5 PL=2` (52) and drives
an F411 channel at 52, behind an F454 `2.0.51`. The reporter captured:

- `*1*34*52##` on movement and `*1*39*52##` about 45 s after the last one;
- replies to `*#1*52*5##`, `*#1*52*6##` and `*#1*52*7##` with WHERE 0:
  `*#1*0*5*2##`, `*#1*0*6*0##`, `*#1*0*7*0*0*45##`; no reply at all for
  `*#1*53*7##` (an F411 output without a detector).

The suite asks:

1. Which SCS frame does the firmware turn into `*1*34*52##` and `*1*39*52##`?
2. Which bus frames does the gateway emit for the four requests?
3. Are the WHERE 0 replies on the bus, or does the gateway drop the address of
   a reply that carries 52?

## How the `up` steps are written

`up` bytes go to the firmware's PIC serial line unchanged
([`pic-inbound-protocol.md`](pic-inbound-protocol.md)). 2-digit gateways read
`$17<4 bytes>\r` and `$18<8 bytes>\r`; F460 / F461 read `$017…` / `$018…`. Each
frame is listed in both forms; every gateway ignores the other family's form,
so those rows are `-` by construction and are left out below.

## Results

`-` = no OpenWebNet output. Gateways that read the frame's PIC form:

| SCS frame | Output | Gateways |
| :--- | :--- | :--- |
| `B8 52 1D 00` | `*1*34*52##` | F453, F453AV, F454, F459, F460, F461, MH200N, MH202, MyHomeServer1 |
| | `*1*32*52##` | MH200 |
| | `-` | F450, H4684 |
| `B8 52 1D 01` | `*1*39*52##` | F454, F459, F460, F461, MH200N, MH202, MyHomeServer1 |
| | `-` | F450, F453, F453AV, H4684, MH200 |
| `D1 52 01 44 10 00 00 00` | `*#1*52*6*0##` | F453, F453AV, F454, F459, F460, F461, MH200N, MH202, MyHomeServer1 |
| | `*#1*52*5*0##` | MH200 |
| `D1 52 01 44 15 00 00 02` | `*#1*52*5*2##` | F453, F453AV, F454, F459, F460, F461, MH200N, MH202, MyHomeServer1 |
| | `*#1*52*4*2##` | MH200 |
| `D1 52 01 44 16 00 00 2D` | `*#1*52*7*0*0*45##` | F453, F453AV, F454, F459, F460, F461, MH200N, MH202, MyHomeServer1 |
| `D5 00 21 44 10 00 00 00` | `*#1*0*6*0##` | F454, F459, F460, F461, MH200N, MH202, MyHomeServer1 |
| `D5 00 21 44 15 00 00 02` | `*#1*0*5*2##` | F454, F459, F460, F461, MH200N, MH202, MyHomeServer1 |
| `D5 00 21 44 16 00 00 2D` | `*#1*0*7*0*0*45##` | F454, F459, F460, F461, MH200N, MH202, MyHomeServer1 |
| `D2 00 01 34 16 00 00 2D` | `*#1*0*7*0*0*45##` | F454, F459, F460, F461, MH200N, MH202, MyHomeServer1 |
| | `*#1*00*7*0*0*45##` | F453, F453AV |

F450 and H4684 give no output for any `up` row, and MH200 none for the rows not
listed with it.

Requests (`down`):

| Request | Bus frame | Gateways | No bus output |
| :--- | :--- | :--- | :--- |
| `*#1*52##` | `52 00 15 00` | all but F450 | F450 |
| `*#1*52*5##` | `D1 52 01 43 15 00 00 00` | F453, F453AV, F454, F459, F460, F461, MH200N, MH202, MyHomeServer1 | F450, H4684 |
| | `D1 52 01 43 10 00 00 00` | MH200 | |
| `*#1*52*6##` | `D1 52 01 43 10 00 00 00` | the nine above | F450, H4684, MH200 |
| `*#1*52*7##` | `D1 52 01 43 16 00 00 00` | the nine above | F450, H4684, MH200 |
| `*#1*53*7##` | `D1 53 01 43 16 00 00 00` | the nine above | F450, H4684, MH200 |

On this empty bus every gateway NACKs every request except MH202, which ACKs.

## Answers

1. **WHAT 34 / 39.** SCS presence status, class `1D`: value `00` → `*1*34*WHERE##`,
   `01` → `*1*39*WHERE##`, at the frame's own address, on the seven current
   gateways (F454, F459, F460, F461, MH200N, MH202, MyHomeServer1). F453 /
   F453AV decode `34` but not `39`. This is the SCS counterpart of the ZigBee
   WHO 1 table (`34` movement, `39` end of movement).
2. **Requests.** `*#1*52*{5,6,7}##` go out as `D1 52 01 43 {15,10,16} 00 00 00`.
3. **WHERE 0.** On the reporter's F454 (and the same six other gateways) the
   reporter's three replies come out byte-exact from frames whose address byte
   is `00`; the same replies carrying `52` come out with `52`. So the WHERE 0 is
   on the bus. That the 067226 is the sender is inferred from the capture (52
   answers, 53 does not), not shown by the firmware, and two address-00 forms
   (`D5 00 21 44 …`, `D2 00 01 34 …`) give the same output, so the exact frame
   the detector sends is not identified.

## Not interpreted

- MH200 `020102`: `*1*32*52##` for presence `00`, and DIMENSION numbers one or
  two lower than the other gateways for the `D1` replies. Recorded verbatim;
  whether this is the older firmware's table or a harness artefact is open.
- Why the gateway NACKs `*#1*52*5##` on the real plant while a WHERE 0 reply
  arrives. With `harness=full` there is no device, so every request NACKs; a
  plant fixture that answers with an address-00 frame would test it.
