# The bare WHO 22 power frame `*22*1*WHERE##` / `*22*0*WHERE##` crashes the sound daemon on 9 of 13 gateways

**Epistemic status:** `firmware_observed` (emulated, `harness=full`, `reset=each`), 13 gateway
images, two fresh runs per gateway (fresh containers, run 2 started after run 1 had finished),
byte-identical TSVs on 13 of 13 (156 of 156 rows agree in reply, verdict and bus output).
Nothing here is `experimentally_confirmed`: the bare form was deliberately never sent to a
physical gateway. The published form was sent to a physical F461 2.0.11 on 2026-11-04
(`*22*1#4#1*2#1##` and `*22*0#4#1*2#1##`, both ACKed, no sound device on that plant).

**Suite:** [`oracle/cases/sound-who22-bare-power.cases`](../oracle/cases/sound-who22-bare-power.cases)
(12 independent `down` steps). Rows:
`results/<product>/<version>/oracle/full/sound-who22-bare-power.tsv` (run 1; run 2 was
byte-identical and is not committed, as for every other suite); image and target SHA-256 are in
each file's header. Pairs with OWNd#163, which refuses the bare form in the send layer.

## The frames

The WHO 22 specification (v1.1, 3.1.1 / 3.1.2 for a source target, 3.2.1 for an amplifier)
publishes amplifier power as `*22*WHAT#MULTIMEDIA_TYPE#AREA*WHERE##`, WHAT `1` ON and `0` OFF.
OWNd's `OWNSoundDiffusionCommand.turn_on` / `turn_off` build the bare form without the
`#MULTIMEDIA_TYPE#AREA` parameters, `*22*1*WHERE##` / `*22*0*WHERE##`. The suite sends the bare
ON and OFF and the published ON (`*22*1#4#1*WHERE##`) to the four WHERE classes:

| WHERE | Class |
|---|---|
| `2#1` | point-to-point amplifier (area 2, point 1) |
| `3#1#1` | source-and-amplifier target |
| `4#1` | area 4, point 1 |
| `6` | all amplifiers |

## Verdicts (all 13 gateways, run 1; run 2 identical)

| Gateway | Bare `*22*1*WHERE##` / `*22*0*WHERE##` (8 rows) | Published `*22*1#4#1*WHERE##` (4 rows) |
|---|---|---|
| F453 `010100` | `nack` / **`crash`** | `ack` / `out` |
| F453AV `030014` | `nack` / **`crash`** | `ack` / `out` |
| F454 `020051` | `nack` / **`crash`** | `ack` / `out` |
| F459 `020105` | `nack` / **`crash`** | `ack` / `out` |
| F460 `020012` | `nack` / **`crash`** | `ack` / `out` |
| F461 `020011` | `nack` / **`crash`** | `ack` / `out` |
| MH200N `010108` | `nack` / **`crash`** | `ack` / `out` |
| MyHomeServer1 `028206` | `nack` / **`crash`** | `ack` / `out` |
| 3488 `030205` | `nack` / **`crash`** | `ack` / `out` |
| MH202 `010024` | `nack` / `silent` | `nack` / `out` (forwarded, empty-bus NACK) |
| F450 `020010` | `nack` / `silent` | `nack` / `silent` |
| H4684 `020054` | `nack` / `silent` | `nack` / `silent` |
| MH200 `020102` | `nack` / `silent` | `nack` / `silent` |

On the nine crashing gateways every bare form crashes, whatever the WHERE class: the crash is
in the parameter handling of the power WHAT, not in the target. The published form goes out on
the bus on the same nine gateways (F461: `*22*1#4#1*2#1##` -> `$00490018F13`, `*22*1#4#1*3#1#1##`
-> `$00490018113`, `*22*1#4#1*4#1##` -> `$004A0018113`, `*22*1#4#1*6##` -> `$004A00F8113`) and on
MH202 (`$0490018F13`, `$049001813`, `$04A001813`, `$04A00F8113`, with the empty-bus NACK of its
`reply_semantics`). F450, H4684 and MH200 drop both forms.

## Committed oracle rows

104 of the 156 rows are `crash` (9 gateways x 8 bare rows), 16 are `ack`/`out`, 4 `nack`/`out`,
32 `nack`/`silent`. The index [`results/mcp_index.json`](../results/mcp_index.json) grows from 653
to 664 unique inputs (11 of the 12 frames are new; `*22*1#4#1*3#1#1##` already had rows from
`sound-who22`).

## Consequence for clients

A client that emits the bare form takes the sound daemon down on every bt_multi / bt_difson
gateway of the fleet that implements WHO 22. OWNd#163 refuses `*22*0*WHERE##` / `*22*1*WHERE##`
in every send path with `reason="destructive_frame"` (the pattern of the WHO 4 DIMENSION 22 and
WHO 18 DIMENSION 52 guards) while `OWNCommand.parse` stays permissive and the builders are
unchanged; the rows are pinned in OWNd's `tests/golden/firmware_oracle.json`.

## What is not settled

- Which process crashes: the harness records the daemon crash (`verdict=crash`) but not the
  process name; the WHO 22 handler lives in `bt_multi` on the F45x / MyHomeServer1 line and
  `bt_difson` on the older line, so both are candidates.
- Whether a physical amplifier changes the outcome: the bare form was never sent to hardware.
- Whether `*22*0#4#0*WHERE##` (OWNd's `turn_off_atomic`) and `*22*0#4#1*WHERE##` differ on a
  physical amplifier; both are published forms and both go out on the emulated bus.
