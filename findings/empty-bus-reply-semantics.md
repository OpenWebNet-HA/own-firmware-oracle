# Empty-Bus `reply` Semantics on F460 / F461 / MyHomeServer1

## Rule

In the `full` suites (`harness=full`, `responder=pic`) of **F460 `020012`**,
**F461 `020011`** and **MyHomeServer1 `028206`**, `reply` comes from an empty bus:

| `reply` | `verdict` | Meaning |
| :--- | :--- | :--- |
| `nack` | `out` | The translator **forwarded** the frame to the bus; no device acknowledged it, because there is no device. This is **not** a gateway refusal. |
| `nack` | `silent` | Nothing went to the bus: the gateway refused the frame or did not translate it. |
| `ack` | `out` / `silent` | The gateway ACKed without waiting for a device (e.g. WHO 18 `$006D1A…` reads). |

**`verdict` shows whether the translator forwarded the frame.** On these three
gateways, `reply` shows whether a device acknowledged on the bus, and in a `pic`
run no device ever does. It means the same as on a real plant only when the run has
a plant responder (`responder=pic:plant:<fixture>`, `results/*/*/oracle/plant-*/`).

`results/mcp_index.json` (schema `1.2.0`) records this per gateway as
`gateways[].reply_semantics`:

| Value | Gateways | Meaning of `reply` |
| :--- | :--- | :--- |
| `empty_bus_txack` | F460, F461, MyHomeServer1 | No device TX-ACK on an empty bus; read `nack`+`out` as "went out". |
| `pic_ack` | F450, F453, F453AV, F454, F459, H4684, MH200, MH200N, MH202 | The harness PIC sent the TX outcome this firmware counts as success, so `reply` is the translator's own answer. |
| `unknown` | — | No `responder=` header, another or mixed responder, or no target spec. |
| `""` | catalogued gateways | No suites, no replies. |

`tools/mcp_index.py` derives the value from `oracle/bus.py:needs_device_txack()` and
the target spec (`oracle/targets/<product>/<version>.yaml`). It does not use a list
of gateways.

## Mechanism

[`pic-inbound-protocol.md`](pic-inbound-protocol.md) has the binary evidence. In short:

- **3-digit RPMsg (F460, F461; `bus_device: /dev/ttyRPMSG30`)**: `Richiesta_Invio_Frame`
  sets `ACK PIC` only when `esito_tx[3] == 0`, which only `$00800\r` produces.
  `$019\r` / `$000\r` leave it at `-1`.
- **2-digit v8 PIC mode (MyHomeServer1 `028206`: `fwrev = 82 > 27`, `fwrel = 6 > 0`)**:
  `Write()` rejects `$19\r` / `$00\r` and returns success only on `$08\r`.
- `PicResponder` with a `Silent` inner (the plain `pic` responder of every full suite)
  answers each write with `$019`/`$000` (3-digit) or `$19`/`$00` (2-digit). On the
  targets above, `scsserver` retries 3 times (~2.06 s), returns `*01*00#` (`NAK PIC`),
  and `openserver` answers `*#*0##`, but the bytes were already written (`verdict=out`).
- Legacy 2-digit firmware (`fwrev <= 27` or `fwrel == 0`: F453, F454, MH200, MH200N,
  MH202, H4684, …) counts `$19\r` / `$00\r` as success, so their `pic` runs ACK such
  writes.

The rule is the same as in `PicResponder` for plant runs. There it sends `$00800` /
`$08` only when the plant has a device for the frame, and `$019` / `$000` otherwise.

## Evidence

**1. The three gateways have the same counts across all 22 full suites**
(`reply`/`verdict` over `results/<gw>/<ver>/oracle/full/*.tsv`):

| Gateway | `ack`/`out` | `nack`/`out` | `nack`/`silent` | `ack`/`silent` |
| :--- | ---: | ---: | ---: | ---: |
| F460 `020012` | 81 | **228** | 230 | 1 |
| F461 `020011` | 81 | **228** | 230 | 1 |
| MyHomeServer1 `028206` | 81 | **228** | 230 | 1 |
| MH200N `010108` (legacy, for contrast) | 328 | 53 | 162 | 1 |
| F454 `020051` (legacy) | 334 | 51 | 154 | 1 |

228 of 309 `out` rows are `nack` on all three gateways. Two PIC transports (3-digit
RPMsg and 2-digit v8 UART) and different translator builds give identical counts,
because both transports share the device-TX-ACK rule.

**2. Plant runs ACK the same kinds of frames once a device answers.**

| Frame | Full suite (`pic`) | Plant run (`pic:plant:…`) |
| :--- | :--- | :--- |
| `*1*1*0##` (general ON) | `nack`/`out` on F460, F461, MHS1 ([`lights-area0.tsv:32`](../results/F461/020011/oracle/full/lights-area0.tsv)); `ack`/`out` on MH200N | — |
| `*1*0*0##` (general OFF) | — | `ack`/`out` on F461 and MHS1 ([`plant-f461-bench.tsv:21`](../results/F461/020011/oracle/plant-f461-bench/plant-f461-bench.tsv)) |
| `*#18*51*#1200#1*0##` | `nack`/`out` on F460, F461, MHS1 ([`energy-ts10.tsv:21`](../results/F461/020011/oracle/full/energy-ts10.tsv)) | — |
| `*#18*51*#1200#1*1##` | — | `ack`/`out` on F461 ([`plant-energy-f520.tsv:19`](../results/F461/020011/oracle/plant-f461-bench/plant-energy-f520.tsv)) |
| `*#1*08##` (address the plant leaves unpopulated) | — | `nack`/`out` on F461 and MHS1 ([`plant-f461-bench.tsv:34`](../results/F461/020011/oracle/plant-f461-bench/plant-f461-bench.tsv)) |

The last row shows that the plant reproduces the full-suite `nack`/`out` exactly when
no device owns the address.

**3. Live F461 2.0.11 hardware ACKs frames that the full suite records as `nack`/`out`.**
These captures are in OpenWebNet-HA/MyHOME,
`tests/fixtures/traces/f461_live_2026-10-18/` and
`tests/fixtures/traces/f461_who14_live_2026-10-18/`, and were reviewed in
OpenWebNet-HA/MyHOME#745. The physical F461 ACKed `*1*1*0##` (0.13 s, both F411/4
relays switched), `*#2*0##` (1.29 s, cover `05` answered) and
`*#18*51*#1200#1*0##`. All three are `nack`/`out` in the full suites.

## Why annotate instead of re-running

The full suites were not re-run with a responder that ACKs every write:

- **"ACK everything" models a plant that does not exist.** A real F461 NACKs an
  unpopulated address (`*#1*08##` above). A responder that ACKs every write would
  change `nack` from "empty bus" to "every address populated", which is also not
  true of any plant. Which addresses exist is defined by the plant fixtures, and
  those runs already exist (`results/plant_index.json`).
- **Results stay hash-pinned and reproducible.** The `suites` workflow re-runs every
  full suite and fails on any verdict change. Annotating the index leaves all 264
  full-suite TSVs, their headers (`responder=pic`, `oracle_version=3`) and
  `verdicts_sha256` (`2f64fe33…`) unchanged. Only `gateways[]` gains a field.
- **The information is already in the data.** `verdict` and `bus_frames` were always
  correct. Only the interpretation of `reply` was missing.

## Consumer guidance

- Join each verdict entry to its gateway's `reply_semantics`. For `empty_bus_txack`,
  treat `nack`+`out` as "forwarded to the bus" (equivalent to `ack` on a populated
  address), and do not report it as a cross-gateway divergence against a `pic_ack`
  gateway's `ack`.
- `nack`+`silent` is a refusal on every gateway.
- For populated-bus ACK/NACK behaviour, use `results/plant_index.json`.
- `pic_ack` does not mean a real legacy gateway ACKs an unpopulated address. It only
  says that the harness gave the success outcome. No hardware capture of a legacy
  gateway writing to an unpopulated address is pinned here yet.
