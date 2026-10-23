# WHO 1 motion detector sharing an actuator address (MyHOME#777)

**Epistemic status:** `firmware_observed` (emulated, `harness=full`). The rows
in this note were recorded locally on F454 `020051` and F461 `020011` while the
suite was written; the committed TSVs are the ones CI produces for every
target (`suites.yml`), and they are what to cite. Nothing here is
`experimentally_confirmed`; the live anchor is the reporter's capture.

**Suite:** [`oracle/cases/who1-motion-detector.cases`](../oracle/cases/who1-motion-detector.cases)
(5 `down` and 18 `up` independent steps, `reset=each`).
Rows: `results/<product>/<version>/oracle/full/who1-motion-detector.tsv`.

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
frame is listed in both forms, and each family ignores the other's, so on every
target half of the `up` rows are `-` by construction.

## Local results (F454 `020051` and F461 `020011`, identical)

| Step | Output |
| :--- | :--- |
| `up` `B8 52 1D 00` | `*1*34*52##` |
| `up` `B8 52 1D 01` | `*1*39*52##` |
| `up` `D1 52 01 44 10 00 00 00` | `*#1*52*6*0##` |
| `up` `D1 52 01 44 16 00 00 2D` | `*#1*52*7*0*0*45##` |
| `up` `D5 00 21 44 10 00 00 00` | `*#1*0*6*0##` |
| `up` `D5 00 21 44 15 00 00 02` | `*#1*0*5*2##` |
| `up` `D5 00 21 44 16 00 00 2D` | `*#1*0*7*0*0*45##` |
| `up` `D2 00 01 34 16 00 00 2D` (F454) | `*#1*0*7*0*0*45##` |
| `down` `*#1*52*7##` (F454) | bus `$06D152014316000000` |
| `down` `*#1*52*5##` / `*6##` (F454) | bus `$06D1520143{15,10}000000` |

On F454 `up` `D1 52 01 44 15 00 00 02` gave no output; the addressed DIM 5
reply form is not settled by this suite.

A wider local sweep on F454 (byte 0 `D1`/`D2`/`D5`, address `52`/`00`, byte 2
`01`/`21`, byte 3 `44`/`34`; DIM 7 only) gave `*#1*52…` or `*#1*52#4#02…` for
every frame with address `52`, never WHERE 0. Only frames with address byte
`00` produced the reporter's `*#1*0*…##`. That sweep is not committed; the
suite keeps the frames that answer the question.

## Answers

1. **WHAT 34 / 39.** SCS presence status, class `1D`: value `00` → `*1*34*WHERE##`,
   `01` → `*1*39*WHERE##`, at the frame's own address. This is the SCS
   counterpart of the ZigBee WHO 1 table (`34` movement, `39` end of movement).
2. **Requests.** `*#1*52*{5,6,7}##` go out as extended frames
   `D1 52 01 43 {15,10,16} 00 00 00`.
3. **WHERE 0.** The reporter's three replies come out byte-exact from frames
   whose address byte is `00`; replies carrying `52` come out with `52`. So the
   WHERE 0 is on the bus. That the 067226 is the sender is inferred from the
   capture (52 answers, 53 does not), not shown by the firmware. More than one
   address-00 form gives the same output, so the exact frame the detector sends
   is not identified.

## Not answered here

Why the gateway NACKs `*#1*52*5##` on the real plant while a WHERE 0 reply
arrives. With `harness=full` there is no device, so every request NACKs; a
plant fixture that answers with an address-00 frame would test it.
