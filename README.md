# own-firmware-oracle

A reproducible pipeline that turns a gateway firmware image into **facts** about
the OpenWebNet protocol — frame formats, code tables, behaviour — for
interoperability with [OWNd](https://github.com/OpenWebNet-HA/OWNd) and
[MyHOME](https://github.com/OpenWebNet-HA/MyHOME).

It grew out of [discussion #613](https://github.com/orgs/OpenWebNet-HA/discussions/613)
and @gdluck's firmware-as-oracle work in
[OWNd#77](https://github.com/OpenWebNet-HA/OWNd/pull/77).

## Why this exists: three goals

The firmware is the only source that says what a gateway **actually does**
with a frame. The manuals are sometimes wrong, community captures cover only
the plants we happen to have, and OWNd / MyHOME have so far been judged against
both. The oracle adds a third kind of evidence: *this firmware image, given this
input, answers X and puts Y on the bus*, reproducible by anyone.

The pipeline is a means. These are the ends:

| # | Goal | Consumer |
|---|---|---|
| 1 | [Improve the Encyclopedia](#1-improve-the-encyclopedia) | [OpenWebNet-Encyclopedia](https://github.com/OpenWebNet-HA/OpenWebNet-Encyclopedia) |
| 2 | [Improve openwebnet-mcp](#2-improve-openwebnet-mcp) | [openwebnet-mcp](https://github.com/OpenWebNet-HA/openwebnet-mcp) and every AI assistant that uses it |
| 3 | [Test OWNd and MyHOME against real firmware](#3-test-ownd-and-myhome) | [OWNd](https://github.com/OpenWebNet-HA/OWNd), [MyHOME](https://github.com/OpenWebNet-HA/MyHOME) |

### What the oracle can and cannot tell you

Read this before using any result for any of the goals. The oracle supports two
execution modes:

- **Stateless Gateway Mode (`harness=full`)**: Emulates the gateway's OpenWebNet
  and SCS translator daemons against a stateless PIC serial ACK stub (`PicResponder`).
- **Closed-Loop Gateway + Virtual Plant Mode (`harness=plant:<fixture>`, [`oracle/plant/`](oracle/plant/))**:
  Connects the emulated gateway firmware (`openserver`, `scsserver`, `bt_luci`,
  `coso`, `bt_termo`, `bt_energia`) to a stateful virtual SCS plant
  (`PlantResponder`) over the simulated 2-wire SCS bus, producing **complete
  end-to-end virtual traces** (`bus:` outbound, `bus_rx:` inbound device replies,
  `cmd:` command-session replies, `evt:` event-session broadcasts, and
  transactional `ack` / `nack` outcomes).

| It can answer | It cannot answer |
|---|---|
| Does the gateway ACK or NACK this OpenWebNet frame (both with a stateless PIC stub and against a populated/unpopulated virtual plant)? | Physical electrical characteristics or analog bus timing of a live 2-wire SCS line |
| Which bus frame(s) does the gateway emit (`bus:`), and which inbound device telegrams (`bus_rx:`) does the virtual plant return, byte for byte? | Undocumented actuator-internal bitmasks not yet captured from hardware (unless modeled in the plant fixture, e.g. `WHO 1001 DIM 11` fault channels) |
| Which OpenWebNet replies (`cmd:`) and event broadcasts (`evt:`) come out across sequential multi-step state transitions (`.seq` virtual traces)? | Anything about a gateway whose image we have not catalogued |
| Does gateway A differ from gateway B for the same input or the same virtual plant topology? | Whether a *silent* run in stateless mode (`harness=full`) means "refused" (use `harness=plant:<fixture>` to test closed-loop device replies and unpopulated NACKs) |

Every row is a fact about **one image** (and, in closed-loop mode, **one pinned
plant fixture**). The same frame gave different answers
on MH200N 1.1.8 and MyHomeServer1 2.82.06 (compare
`results/*/*/oracle/full/lights-level.tsv`), so a result never transfers to
another product or firmware without its own row. When evidence disagrees the
order is **live capture > oracle > OWNd / MyHOME code**, for the product and
firmware the evidence was taken on ([architecture §1.1](docs/oracle-architecture.md)).

### 1. Improve the Encyclopedia

The Encyclopedia's core values ask for evidence that is labelled, versioned and
reproducible, and for "not observed" never to turn into "does not exist". The
oracle supplies a new evidence kind that fits that model:

- **A new provenance class next to `evidence/` field captures.** A firmware
  record cites image SHA-256, target SHA-256, suite SHA-256 and the TSV row, so
  another researcher can rerun it and get a zero diff. Version scope is the
  exact firmware, never "OpenWebNet".
- **Negative results become citable.** A NACK, or a frame that never reaches
  the bus, is a firmware fact. Example: `*#1*31*#1*100*0##` (level write with
  100) is NACKed across all 11 emulated gateways, while level 101 and the
  switch-off forms are accepted.
- **Applicability tables from diffs.** The same case file runs on every
  gateway, so "which gateways accept X" is a TSV diff, not prose written from
  memory. Use it to fill the per-gateway columns and the version scope of
  claims, and to retire "applies to all gateways" wording.
- **Closing open questions with a suite.** Each entry in the Encyclopedia's
  `open-questions.md` that is about gateway translation (WHAT 19, DIM 7 / DIM 4
  handling, general/area scope) should name the suite that would answer it, or
  say that the oracle cannot (see the table above).
- **Privacy stays intact.** Suites use public or synthetic WHEREs only, results
  contain no plant data, and no vendor bytes enter either repo.

How to hand a finding over: write `findings/<product>/<topic>.md`, cite the TSV
rows and the three hashes, state the epistemic status (`firmware_observed`,
never `experimentally_confirmed`, which stays reserved for live hardware), and
open a PR on the Encyclopedia that cites it. Add a `Gateway` row from a live
read-only probe whenever the claim is about a real product.

### 2. Improve openwebnet-mcp

The MCP is the "judge": it checks grammar, and its own consumers have already
been bitten by treating that as meaning (the WHO 15 and WHO 25 catalogs were
wrong and three fixtures followed them; WHO 22 is still a stub). The oracle
adds a layer the judge lacked: **does a real gateway accept this?**

Shipped and active integrations:

<!-- MCP_METRICS_START -->
1. **Ship results as data.** (**Shipped**) Published a deterministic, hash-pinned
   index at `results/mcp_index.json` (567 unique frames, 7,344 verdicts across 12
   active gateway emulators). The MCP stays offline and read-only; it consumes
   this generated corpus like it does for the Machine KB.
<!-- MCP_METRICS_END -->
2. **`parse_and_validate_frame` and `lookup_firmware_verdict` per catalogued
   gateway.** (**Shipped**) Returns `ack`, `nack`, emitted bus telegrams, and
   SHA-256 provenance across all emulated gateways. "Legal grammar, NACKed by
   MyHomeServer1 2.82.06" is the answer an assistant gets directly.
3. **`draft_own_frame` pre-flight.** (**Shipped**) Runs oracle pre-flight checks
   against the target gateway (or full fleet) and warns on frames that firmware
   is known to NACK before handing them to the user.
4. **A `compare_gateways(frame, suite)` tool.** (**Shipped**) Returns the
   per-firmware comparison table or suite divergences in `openwebnet-mcp` (and
   via `tools/compare.py` on the CLI).
5. **Audit the catalog with the oracle.** (**In Progress**) 22 suites covering
   BTicino TS10 reference subsystems (WHO 4, WHO 22, WHO 18 & WHO 3, WHO 8),
   Area 0 lighting, holiday/vacation programs, and hardware trace corpora have
   been audited across the fleet, replacing specification stubs with empirical
   gateway verdicts and byte-level bus outputs.
6. **Share the live probe.** (**Shipped / Ongoing**) `myhome-gateway` (live bus)
   and the oracle's read-only `tools/live_probe.py`
   (`oracle/canary/canary-live.cases`) enforce a strict read-only frame
   allow-list so the `Gateway` evidence label means the same thing in both.

### 3. Test OWNd and MyHOME

OWNd and MyHOME are tested against a golden corpus whose only hard facts are
community plant captures. The oracle makes firmware a second, cheap and
repeatable test source that needs no plant:

- **Corpus gate.** Feed every `down` frame of the golden corpus to each
  catalogued firmware (`oracle/cases/trace-corpus.cases`). A fixture that the
  firmware NACKs, or that emits something else, is flagged before it ships. Add
  `firmware-oracle` as a fixture provenance ranked above spec readings and below
  a capture.
- **Parser conformance without hardware.** `up` suites inject bus frames; what
  the firmware emits on the OpenWebNet side is run through OWNd's parser (the
  `Emitted` label via `tools/check.py --all --report`). A parse failure on a
  real firmware's output is an OWNd bug.
- **Replay known audits.** gdluck's ten MyHOMEServer1 findings (OWNd#77) are
  suites; run them on every other gateway to see which hold, which differ, and
  which need a gateway-specific profile.
- **Derive gateway profiles instead of hand-writing them.** MyHOME's
  firmware-aware gateway profiles encode which commands a gateway accepts;
  the oracle's per-firmware TSVs are the evidence those capability flags
  should cite, and a test can assert profile and TSV agree.
- **Regression on new firmware.** When a vendor publishes a new image, the
  weekly `oracle` workflow produces a new manifest and TSV set; the diff to
  the previous version is the list of behaviour changes to check in MyHOME.

### Lessons learned

1. **One firmware is not all firmware.** The first suite already differs
   between MH200N and MyHomeServer1, and the first live anchor
   (`EVID-MH200-WHAT19-FAULT`) came from an MH200 2.1.0 while the first image
   was an MH200N 1.1.8 (an `MH200` 2.1.2 image has since been catalogued and
   emulated). Label every row cross-product whenever exact firmware versions
   differ.
2. **Emitted proves, silent does not.** A missing reply can mean no device
   answered. Only accepted-and-emitted rows are positive evidence.
3. **The oracle sees the translator, not the device.** It cannot give a
   device-set bitmask meaning. Say so in the finding instead of guessing.
4. **Determinism is the product.** Sorted TSV, no timestamps, no PIDs, a zero
   diff on re-run, hashes in every header. That is what lets a maintainer review
   a claim without trusting the author.
5. **Grammar is not behaviour.** A frame being legal says nothing about a
   gateway accepting it. Keep the judge (grammar), the oracle (firmware) and
   the capture (plant) as separate, labelled sources.
6. **Adding a gateway is a recipe, not research.** Eight steps, each with a
   "done when" ([docs/adding-a-gateway.md](docs/adding-a-gateway.md)); the
   second image needed a target YAML and a few harness generalizations, not a
   new design.
7. **Black box first, facts only.** No binaries, disassembly or decompiled code
   leave the machine; `tools/guard.py` enforces it. That is what makes the
   results publishable and the project safe to share.

## Closed-Loop Gateway + Virtual Plant: Reproducing Full Virtual Traces

Originally, the firmware oracle emulated only the **gateway translator stack** (`openserver`, `scsserver`, `bt_luci`, `coso`, `bt_termo`, `bt_energia`) attached to a stateless PIC serial ACK stub (`PicResponder`). That answered *what the gateway emits on the SCS bus for an OpenWebNet input*, or *what OpenWebNet frame comes out for a single injected bus telegram*, but the physical plant was absent: status queries (`*#1*06##`) had no device to answer them, commands did not mutate actuator state across steps, and unpopulated addresses could not be distinguished from populated ones.

With **[`oracle/plant/`](oracle/plant/)**, the oracle now combines **real emulated gateway firmware** with a **stateful virtual SCS plant** in a closed loop (`harness=plant:<fixture>`, `responder=pic:plant:<fixture>`), allowing us to execute and reproduce **complete end-to-end virtual traces** without physical hardware.

### How the Closed-Loop Gateway + Plant Pipeline Works

```
OpenWebNet Client (TCP 20000: Command + Event Sessions)
        │  down: *1*1*06##, *#1*06##, *#4*2##, *#2*1#4#02##
        ▼
┌───────────────────────────────────────────────────────────────────┐
│ Emulated Gateway Firmware (QEMU user-mode + bubblewrap jail)      │
│   openserver ◄──► bt_luci / coso / bt_termo / bt_energia          │
│                         │                                         │
│                         ▼                                         │
│                     scsserver                                     │
└─────────────────────────┬─────────────────────────────────────────┘
        │                 │                 ▲
        │ 1. Outbound     │ 2. PIC TX-ACK   │ 3. Inbound Device
        │    bus:         │    bus_rx:      │    Telegrams (bus_rx:)
        │    $003.. / $03 │    $00800\r /   │    $017B8.. / $17B8..
        ▼                 │    $08\r / $19\r│    $018D2.. / $18E4..
┌─────────────────────────┴─────────────────┴───────────────────────┐
│ Virtual SCS Plant (`oracle/plant/` — `PicResponder(PlantResponder)`) │
│   • Relays (`LightDevice`) + Group `DIMENSION 15` (`B5 <G> D0`)   │
│   • Non-Linear Dimmers (`DimmerDevice`: `F414`, `F418U2` curves)  │
│   • Shutters (`CoverDevice`: travel timers, `WHO=14` locks)       │
│   • Sub-Bus Bridge (`F422` `#4#01` / `#4#02` `EC`/`E4` wrapping)  │
│   • Climate (`ThermoZoneDevice`: 4-step `bt_termo` `KW4691`)      │
│   • Energy (`EnergyMeterDevice` `F520`, `EnergyActuatorDevice`)   │
│   • Fault Channels (`FaultChannel`: `WHAT=19` + `WHO=1001` mask)  │
└───────────────────────────────────────────────────────────────────┘
```

1. **Outbound Translation (`bus:`)**: The test driver sends an OpenWebNet frame (`down`) on TCP port 20000. The emulated gateway daemon translates it into an outbound PIC serial frame (`$03`/`$04`/`$06` on 2-digit UART gateways; `$003`/`$004`/`$006` on 3-digit RPMsg gateways).
2. **PIC Handshake (`bus_rx:`)**: Reverse engineering of `f461/scsserver` and `myhomeserver1/scsserver` ([`findings/pic-inbound-protocol.md`](findings/pic-inbound-protocol.md)) established the exact PIC TX-outcome handshakes:
   - **3-Digit RPMsg (`F460`, `F461`)**: `Richiesta_Invio_Frame` requires `$00800\r` (`esito_tx[3] == 0`) when a device acknowledges the bus telegram, or `$019\r` / `$000\r` on an unpopulated address (causing `scsserver` to retry 3 times and return `*01*00#` $\to$ OpenWebNet NACK `*#*0##` after ~2.06 s). The `full` suites run on an empty bus, so on F460, F461 and MyHomeServer1 their `nack`/`out` rows mean "no device acknowledged", not "refused" ([`findings/empty-bus-reply-semantics.md`](findings/empty-bus-reply-semantics.md)).
   - **2-Digit UART (`MH200`, `MH200N`, `MyHomeServer1`, `H4684`)**: Legacy firmware (`fwrev <= 27`, e.g. `MH200` `020102` and `MH200N` `010108`) expects `$19\r` for `$03` and `$00\r` for `$04`/`$06`, whereas v8 PIC mode (`fwrev > 27`, e.g. `MyHomeServer1` `028206`) requires `$08\r`. Because `mhs1/scsserver` reads 20 bytes per `read()` syscall and discards bytes after the first `\r`, `PicResponder` paces 2-digit inbound device frames by `25.0 ms`.
3. **Stateful Device Execution & Inbound Bus Reply (`bus_rx:`)**: `PlantResponder` decodes the SCS payload, updates the target device(s) in `PlantTopology`, and emits `$17<8hex>\r` / `$18<16hex>\r` (2-digit) or `$017<8hex>\r` / `$018<16hex>\r` (3-digit) back into `scsserver`.
4. **Closed-Loop OpenWebNet Output (`cmd:` and `evt:`)**: The gateway's translator daemons decode the inbound device telegrams and emit command-session responses (`cmd:`), event-session broadcasts (`evt:`), and the final `ack` / `nack` (`reply`), all recorded deterministically in `results/<product>/<version>/oracle/plant-<fixture>/<suite>.tsv` and indexed in [`results/plant_index.json`](results/plant_index.json).

> **Why Clean-Room Instead of `MyHomeSystemEmulator`?**
> We audited the archived Qt4 C++ [`OpenWebNet-HA/MyHomeSystemEmulator`](https://github.com/OpenWebNet-HA/MyHomeSystemEmulator) (`4f93f44ee5ec`) against real gateway binaries and hardware captures ([`findings/vdk-emulator-audit.md`](findings/vdk-emulator-audit.md)). That repository never modeled the 2-wire SCS byte protocol (passing ASCII `OpenMsg` strings across `btscsbus.cpp`), emitted `*#*1##` ACKs directly from device plugins, lacked dimmer/thermostat/shutter state machines, failed to isolate `F422` sub-bus broadcasts, and mixed LGPL-2.1+ with unlicensed files. `oracle/plant/` is therefore implemented **100% clean-room under Apache-2.0** from firmware binary analysis and physical hardware captures.

---

### Concrete Examples of Reproduced Virtual Traces

Using two hardware-pinned plant fixtures — [`oracle/plant/fixtures/f461-bench.yaml`](oracle/plant/fixtures/f461-bench.yaml) (modeling the physical `F461` test bench) and [`oracle/plant/fixtures/mh200-bench.yaml`](oracle/plant/fixtures/mh200-bench.yaml) (modeling the `MH200` hardware bench with `F414`/`F418U2` dimmers and `F422` sub-bus bridges) — the oracle reproduces multi-step real hardware captures as deterministic `.seq` virtual traces across `F461/020011`, `MH200/020102`, and `MyHomeServer1/028206`:

#### Example 1: Stateful Relay Switching, Group `DIMENSION 15` Transitions & Unpopulated Address NACKs (`F461/020011`)

From [`results/F461/020011/oracle/plant-f461-bench/plant-f461-bench.tsv`](results/F461/020011/oracle/plant-f461-bench/plant-f461-bench.tsv) (reproducing the `F461` physical bench captures from `2026-10-15`..`2026-10-19`):

| Step | Input (`down`) | `reply` | Outbound Gateway Bus (`bus:`) | Inbound Virtual Plant (`bus_rx:`) | OpenWebNet Output (`cmd:` / `evt:`) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1. Initial poll | `*#1*06##` | `ack` | `$00306001500\r` | `$00800\r`, `$017B8061201\r` | `cmd:*1*0*06## \| evt:*1*0*06##` |
| 2. Turn ON `06` | `*1*1*06##` | `ack` | `$00306001200\r` | `$00800\r`, `$017B8061200\r`, `$018D501013407000000\r` | `evt:*1*1*06## \| evt:*#1*#1*15*1##` |
| 3. Poll after ON | `*#1*06##` | `ack` | `$00306001500\r` | `$00800\r`, `$017B8061200\r` | `cmd:*1*1*06## \| evt:*1*1*06##` |
| 4. Turn OFF `06` | `*1*0*06##` | `ack` | `$00306001201\r` | `$00800\r`, `$017B8061201\r`, `$018D501013407000001\r` | `evt:*1*0*06## \| evt:*#1*#1*15*0##` |
| 5. Group `#1` ON | `*1*1*#1##` | `ack` | `$004B5011200\r` | `$00800\r`, `$017B5011200\r`, `$017B8061200\r`, `$018D501013407000000\r`, `$017B8071200\r` | `evt:*1*1*#1## \| evt:*1*1*#1## \| evt:*1*1*06## \| evt:*#1*#1*15*1## \| evt:*1*1*07##` |
| 6. Unpopulated `08` | `*#1*08##` | **`nack`** | `$00308001500\r` | `$019\r` *(no device ACK)* | *(none — `scsserver` retries 3× and NACKs)* |

- **What this proves**: When relay `06` (a member of Group 1) switches from OFF to ON, the virtual plant emits both the point status (`$017B8061200\r`) and the extended group status frame (`$018D501013407000000\r`), which `f461/bt_luci` (`23scs_answer_group_status` at `0x443d0`) translates into `evt:*#1*#1*15*1##`. Conversely, querying unpopulated addresses (`*#1*08##`, `*#1*25##`, `*#1*50##`, `*#1*99##`) returns `$019\r`, reproducing the exact hardware `nack` timeout.

#### Example 2: Shutter Pre-Move `STOP` & 4-Step `bt_termo` Climate Burst (`F461/020011` & `MyHomeServer1/028206`)

Also in [`plant-f461-bench.tsv`](results/F461/020011/oracle/plant-f461-bench/plant-f461-bench.tsv) (and cross-verified on [`MyHomeServer1/028206`](results/MyHomeServer1/028206/oracle/plant-f461-bench/plant-f461-bench.tsv)):

- **Motorized Shutter (`WHO=2`, `WHERE=05`)**:
  - Input: `down *2*1*05##` (Move UP)
  - Gateway `coso` emits **two** outbound SCS commands: pre-move `STOP` (`$0030500120A\r`) followed by `UP` (`$00305001208\r`).
  - Virtual `CoverDevice` answers both (`$017B805120A\r`, `$017B8051208\r`), and the gateway emits the exact 4-frame hardware event sequence:
    `evt:*2*1000#0*05## | evt:*2*0*05## | evt:*2*1000#1*05## | evt:*2*1*05##`
- **4-Step Thermoregulation Zone Status Poll (`WHO=4`, `down *#4*2##`)**:
  - Gateway `bt_termo` (`0xad60..0xae70`) issues 4 sequential SCS queries: `$00499023000\r` (temperature), `$00499023012\r` (mode + setpoint), `$0049902300E\r` (local setpoint), and `$0049902300D\r` (local offset).
  - Virtual `ThermoZoneDevice` (`KW4691`) answers with `$017B40230F9\r` (`24.9 °C`), `$018D202030412F000F5\r` (`24.5 °C`, heating mode `1`), `$018D20203040E00F503\r`, and `$018D20203040D000000\r`.
  - Gateway emits the exact 5-frame burst on both `cmd:` and `evt:`:
    `*#4*2*0*0249## | *#4*2*12*0245*3## | *4*1*2## | *#4*2*14*0245*3## | *#4*2*13*00##`
  - Querying actuator status `down *#4*2*20##` (`$004A9023010\r`) returns both actuator states (`$017B7823101\r`, `$017B7823220\r`), which `bt_termo` translates to `cmd:*#4*2#1*20*0## | evt:*#4*2#1*20*0## | evt:*#4*2#2*20*5##`.

#### Example 3: Non-Linear Hardware Dimmers (`F414` & `F418U2`) & `WHAT=19` Fault Co-occurrence (`MH200/020102`)

From [`results/MH200/020102/oracle/plant-mh200-bench/plant-mh200-bench.tsv`](results/MH200/020102/oracle/plant-mh200-bench/plant-mh200-bench.tsv) (reproducing Encyclopedia captures `cap-mh200-f414-dimmer-20260926`, `cap-mh200-f418u2-dimmer-20260927`, and `EVID-MH200-WHAT19-FAULT`):

| Step | Input (`down`) | `reply` | Outbound Gateway Bus (`bus:`) | Inbound Virtual Plant (`bus_rx:`) | OpenWebNet Output (`cmd:` / `evt:`) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1. `F414` initial poll | `*#1*14##` | `ack` | `$0314001500\r` | `$19\r`, `$17B814129D\r` | `cmd:*1*10*14## \| evt:*1*10*14##` |
| 2. `F414` set level 5 | `*1*5*14##` | `ack` | `$031400124D\r` | `$19\r`, `$17B814124D\r` | `evt:*1*5*14##` |
| 3. `F414` `DIM 1` query | `*#1*14*1##` | `ack` | `$06D11401430D000000\r` | `$00\r`, `$18D21401440D003102\r` | `cmd:*#1*14*1*149*2## \| evt:*#1*14*1*149*2##` |
| 4. `F418U2` set level 5 | `*1*5*62##` | `ack` | `$036200124D\r` | `$19\r`, `$17B862124D\r` | `evt:*1*5*62##` |
| 5. `F418U2` poll after | `*#1*62##` | `ack` | `$0362001500\r` | `$19\r`, `$17B862124D\r` | `cmd:*1*5*62## \| evt:*1*5*62##` |
| 6. Fault channel `75` | `*#1*75##` | `ack` | `$0375001500\r` | `$19\r`, `$17B875120E\r`, `$18D275010489FBFFF7\r` | `cmd:*1*19*75## \| evt:*1*19*75##` |

#### Example 4: `F422` Sub-Bus Bridge Scope & Broadcast Isolation (`MH200/020102`)

From [`results/MH200/020102/oracle/plant-mh200-bench/plant-f422-subbus.tsv`](results/MH200/020102/oracle/plant-mh200-bench/plant-f422-subbus.tsv) (reproducing Encyclopedia capture `cap-mh200-f422-scope-20260923` across `#4#01` and `#4#02`):

- **Sub-Bus Area Status Poll (`down *#2*1#4#02##`)**:
  - `MH200` wraps the Area 1 poll in an extended `EC` sub-bus telegram: `$06EC020000B3011500\r`.
  - Virtual `F422` interface `#4#02` returns `E4`-wrapped status telegrams for all three sub-bus shutters (`$18E4020000B811120A\r`, `...B812120A\r`, `...B813120A\r`), which `MH200` translates into:
    `cmd:*2*0*11#4#02## | cmd:*2*0*12#4#02## | cmd:*2*0*13#4#02##` (and matching `evt:` frames).
- **Sub-Bus Area Command Blocked vs. Point-to-Point Allowed**:
  - Area command `down *2*1*1#4#02##` emits `$06EC020000B301120A\r` + `$06EC020000B3011208\r`, receives PIC ACK `$00\r`, and produces **no sub-bus actuator movement**, matching the physical `F422` bridge hardware.
  - Point-to-point command `down *2*1*11#4#02##` (`$06EC0200001100120A\r`, `$06EC02000011001208\r`) moves shutter `11#4#02` and emits `$18E4020000B811120A\r` + `$18E4020000B8111208\r` $\to$ `evt:*2*0*11#4#02## | evt:*2*1*11#4#02##`.
- **Main-Bus Broadcast Isolation (`down *#1*0##`)**:
  - General main-bus poll `$04B1001500\r` returns only main-bus devices (`21`, `14`, `99`, `62`, `63`) and never leaks sub-bus devices (`11#4#01` or `11#4#02..13#4#02`).

#### Example 5: `F520` Energy Meter & Load Actuator Queries (`F461/020011`)

From [`results/F461/020011/oracle/plant-f461-bench/plant-energy-f520.tsv`](results/F461/020011/oracle/plant-f461-bench/plant-energy-f520.tsv):

- **Instantaneous Active Power (`down *#18*51*113##`)**:
  - Outbound: `$006D1A10233001D0000\r` $\to$ Inbound: `$00800\r`, `$018D2A10234001D01C2\r` (`0x01C2` = `450 W`) $\to$ Output: `cmd:*#18*51*113*450## | evt:*#18*51*113*450##`.
- **Two-Telegram Cumulative Energy Totals (`down *#18*51*51##` & `down *#18*51*54##`)**:
  - Outbound: `$006D1A1023350150000\r` $\to$ Inbound two-frame `D2` sequence (`$018D2A1023581501500\r` + `$018D2A10235A001E240\r`) $\to$ Output: `cmd:*#18*51*51*123456## | evt:*#18*51*51*123456##`.
- **Stop&Go / Energy Actuator Instantaneous Power (`down *#18*71#0*113##`)**:
  - Outbound: `$006D1B11233001D0000\r` $\to$ Inbound: `$00800\r`, `$018D2B11234001D0078\r` (`0x0078` = `120 W`) $\to$ Output: `cmd:*#18*71#0*113*120## | evt:*#18*71#0*113*120##`.

## What is and isn't public here

| Stage | Public on GitHub | Stays local |
|---|---|---|
| 0. Catalog | vendor URL, version, size, SHA-256 | the firmware image |
| 1. Unpack | `tools/unpack.py` + `manifest.tsv` (path, type, CPU, SHA-256) | the extracted files |
| 2. Oracle | emulator + simulated SCS bus & virtual plant (`oracle/plant/`), input frame lists | — |
| 3. Record | TSV: input frame → bus / plant / OpenWebNet frames, firmware & plant hashes in header | — |
| 4. Findings | `findings/<gateway>/*.md` pointing at TSV rows | decompiled C |

**No vendor binary, disassembly or decompiled code is ever committed.**
`tools/guard.py` enforces that in CI and as a pre-commit hook.

## Pipeline

```
catalog/<product>/<version>.yaml    product, version, sources[], sizes, sha256
tools/fwfetch.py                    materialize image by sha256 (vendor / R2 / --from-file)
tools/unpack.py                     layer-aware extractor  -> results/.../manifest.tsv
tools/plan.py                       which manifests are stale; result paths
tools/suite_plan.py                 which emulated suite TSVs are missing, stale, or regressed
tools/mcp_index.py                  build & verify results/mcp_index.json across the fleet
tools/plant_index.py                build & verify results/plant_index.json for virtual plant suites
tools/pic_spike.py                  closed-loop 2-digit / 3-digit PIC framing verification spike
tools/compare.py                    cross-gateway frame comparison, divergences & matrices
tools/verdict_diff.py               semantic verdict diff across git revisions / PRs
tools/check.py                      OWNd parser & live capture conformance evaluator
tools/expected_divergences.yaml     machine-checked ledger of expected live-trace divergences
tools/live_probe.py                 read-only live hardware canary runner & validator
tools/export_evidence.py            export oracle TSVs to Encyclopedia evidence bundles
tools/sync_readme.py                automated README metrics & table anti-drift sentinel
tools/guard.py                      refuse binaries / NUL bytes / oversized files
oracle/                             emulator + simulated SCS bus (see docs/oracle-architecture.md)
  targets/<product>/<version>.yaml  programs to run, pinned to manifest rows
  cases/*.cases|*.seq               stateless input steps: down = OWN text, up = bus hex
  plant/                            clean-room stateful virtual SCS plant & device state machines
  plant/fixtures/*.yaml             pinned virtual plant topologies (f461-bench, mh200-bench)
  plant_cases/*.seq                 closed-loop stateful virtual plant trace suites
  canary/canary-live.cases          read-only live hardware canary suite
results/<product>/<version>/        manifest.tsv, boundary TSVs, oracle TSVs, plant TSVs, check TSVs
results/mcp_index.json              hash-pinned cross-firmware verdict index (schema 1.2.0)
results/plant_index.json            hash-pinned closed-loop virtual plant verdict index (schema 1.0.0)
findings/<product>/*.md             conclusions pointing at TSV rows
.github/workflows/                  CI, see below
requirements/                       hash-locked CI dependencies
```

## CI

| Workflow | When | What |
|---|---|---|
| `pr` | every PR, `main` | ruff, ruff format, mypy `--strict`, zizmor, actionlint + shellcheck; guard, `mcp_index.py --check`, `plant_index.py --check`, `sync_readme.py --check`; pytest on Python 3.12–3.14 incl. a real-debugfs end-to-end test, coverage ratchet; `verdict_diff.py` PR summary; dependency review. `ci-ok` is the one check to require. |
| `reproduce` | PRs touching `tools/`, `catalog/`, `results/`; weekly | re-fetches every fresh, publicly downloadable image, re-runs `unpack`, fails on any byte of difference from `results/` |
| `oracle` | `main`, weekly | rebuilds stale manifests (new image or `TOOL_VERSION`) and opens one results PR |
| `suites` | PRs touching `oracle/`, `catalog/`, suite results; `main`, weekly | re-runs every emulated suite on every target under qemu-user (`tools/suite_plan.py`): fails when a re-run with the same inputs gives different verdicts, produces missing or stale results only if a second run reproduces them, and opens one results PR on `main`, and opens an issue when the weekly run regresses |
| `codeql` | every PR, `main`, weekly | CodeQL `security-extended` on the tools and on the workflows |
| `scorecard` | `main`, weekly | OpenSSF Scorecard, published + in code scanning |

None of them gives a fork PR a secret or a write token. Firmware only lands in
`$RUNNER_TEMP` and is deleted after the job; it is never uploaded as an artifact,
and only public vendor images are cached, on pull requests (`reproduce`, `suites`). Actions are pinned to commit SHAs, and Python deps are installed
with `--require-hashes`. Dependabot bumps both every month.

Contributing: `pip install pre-commit && pre-commit install` runs ruff and the guard
before each commit. To run everything the lint job runs:

```bash
pip install --require-hashes -r requirements/lint.txt -r requirements/test.txt
ruff check . && ruff format --check . && mypy && pytest --cov
```

## Run it locally

```bash
pip install pyyaml            # + boto3 only if you fetch from R2
# Filesystem tools, run inside bubblewrap (no network, one writable dir):
sudo apt install bubblewrap e2fsprogs squashfs-tools util-linux

# A) public image — fetch + verify from the vendor:
IMG=$(python tools/fwfetch.py catalog/MH200N/010108.yaml)

# B) a local copy — no upload. Pass the WRAPPER (the file whose size + sha256
#    the catalog records), not the inner .fwz:
IMG=$(python tools/fwfetch.py catalog/MH200N/010108.yaml --from-file ./FW_MH200N_vers_010108.zip)

python tools/unpack.py catalog/MH200N/010108.yaml "$IMG" \
    -o results/MH200N/010108/manifest.tsv
python tools/guard.py
```

A clean re-run must produce a **zero diff** (sorted TSV, no timestamps). That's
the reproducibility check, and the `reproduce` workflow runs it weekly against
the real vendor image.

`unpack.py` refuses any file that is not the catalog's wrapper (size + SHA-256)
or that does not contain the catalog's inner image, and roots every manifest
path at `wrapper.filename`, so the cache copy and a local copy give the same
TSV. Symlinks inside a filesystem are recorded as `symlink` rows (target
hashed), never followed. Every catalog file is validated by `tools/schema.py`:
plain-name product / version / filenames at `catalog/<product>/<version>.yaml`,
and `https://` vendor URLs on an allow-listed BTicino / Legrand host.

### Layers and the coverage gate

`unpack.py` detects layers by magic: zip (ZipCrypto via the catalog's
password scheme), U-Boot uImage, gzip / bzip2 / xz / lzma, tar, cpio, and the
ext2/3/4, squashfs and cramfs filesystems. ELF rows say CPU, word size, byte
order and, for ARM, the ABI (`ELF/ARM/exec/32le/oabi`), which is what the
oracle needs to pick an emulator.

JFFS2, UBI, FIT and 7z are recognised but not unpacked yet. Those, any
container its tool could not read, and any opaque `data` blob of 1 MiB or more
outside a filesystem fail the run. A new image therefore either unpacks
completely or says exactly which layer is missing. When a layer is genuinely
not worth unpacking (a bootloader, a sub-MCU blob), acknowledge it in the
catalog:

```yaml
undecoded_ok:
  - path: "<wrapper>!<member>"      # exactly as the gate printed it
    reason: "sub-MCU firmware, not a Linux image"
limits:
  max_expand_mib: 1024     # raise the 256 MiB bomb guard for a big image
```

An acknowledgement that no longer matches a row also fails, so the list
cannot go stale. Extraction tools (`debugfs`, `unsquashfs`, `fsck.cramfs`)
run inside bubblewrap; `--no-sandbox` runs them unconfined and must be asked
for explicitly.

## Catalog: Supported Gateways & Hardware

The oracle catalogues, unpacks, and tracks deterministic manifests for all standalone OpenWebNet gateway models released by BTicino and Legrand, as well as auxiliary touch screen consoles and interface hardware:

<!-- FLEET_TABLE_START -->
| Gateway | Firmware Version | System Architecture | Manifest Rows | Layer Types | Core Daemons / Firmware Artifact | Emulation Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **MH200N** | `010108` (1.1.8) | Linux ARMv5 `eabi5` | 879 | U-Boot, Ext2, Gzip, Zip | `bt_processi`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_supervisione`, `bt_energia`, `bt_difson`, `bt_multi`, `bt_alarm` | **Emulated** (Phase 2 — 26 suites, full parity) |
| **MH200** | `020102` (2.1.2) | Linux ARMv4 `oabi` | 279 | Ext2, Gzip, Zip | `bt_processi`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_energia`, `bt_difson`, `bt_alarm`, `bt_vct` | **Emulated** (Phase 2 — 26 suites, full parity) |
| **MyHomeServer1** | `028206` (2.82.6) | Linux ARMv7 `eabi5` | 56,602 | U-Boot, Ext4, Gzip, Zip | `bt_daemon`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `bt_supervisione`, `coso` | **Emulated** (Phase 2 — 26 suites, full parity) |
| **F454** | `020051` (2.0.51) | Linux ARMv5 `eabi5` | 5,413 | U-Boot, Ext3, Gzip, Zip | `bt_daemon`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `bt_difson`, `coso`, `bt_energia`, `bt_supervisione` | **Emulated** (Phase 2 — 26 suites, full parity) |
| **MH202** | `010024` (1.0.24) | Linux ARMv5 `eabi5` | 10,343 | U-Boot, Ext3, Gzip, Zip | `bt_daemon`, `openserver`, `scsserver`, `bt_device`, `bt_energia`, `bt_supervisione`, `coso` | **Emulated** (Phase 2 — 26 suites, full parity) |
| **F453AV** | `030014` (3.0.14) | Linux ARMv4 `oabi` | 1,283 | U-Boot, Ext2, Gzip, Zip | `bt_processi`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_difson`, `bt_multi`, `bt_vct`, `bt_energia`, `bt_supervisione` | **Emulated** (Phase 2 — 26 suites, full parity) |
| **F453** | `010100` (1.1.0) | Linux ARMv4 `oabi` | 1,090 | U-Boot, Ext2, Gzip, Zip | `bt_processi`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_difson`, `bt_multi`, `bt_vct`, `bt_energia`, `bt_supervisione`, `bt_alarm` | **Emulated** (Phase 2 — 26 suites, full parity) |
| **F455** | `010102` (1.1.2) | Bare-metal ARM Cortex-M | 3 | Zip, Monolithic `.bin` | Flash image `F455_1_1_2.bin` (301 KB, no OS) | Pending Emulation (Bare-metal MCU — zero matrix value) |
| **MH201** | `030644` (3.6.44) | Bare-metal ARM Cortex-M3 (STM32F217) | 3 | Zip, Monolithic `.bin` | Flash image `MH201_3_6_44_signed.bin` (502 KB, CMX-RTX, no OS) | Pending Emulation (Bare-metal MCU — zero matrix value) |
| **F461** | `020011` (2.0.11) | Linux AArch64 (ARM64) | 15,517 | Ext4, Gzip, Zip | `bt_daemon`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `bt_supervisione`, `coso` | **Emulated** (Phase 2 — 26 suites, full parity) |
| **F450** | `020010` (2.0.10) | Linux ARMv5 `eabi5` | 4,322 | U-Boot, Ext3, Gzip, Zip | `bt_daemon`, `bacclient`, `ebacgw`, `scsserver`, `bt_device`, `bt_termo` | **Emulated** (Phase 2 — 26 suites, full parity) |
| **F459** | `020105` (2.1.5) | Linux ARMv5 `eabi5` | 14,335 | U-Boot, Ext3, Gzip, Zip | `bt_daemon`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `bt_energia`, `bt_supervisione`, `coso` | **Emulated** (Phase 2 — 26 suites, full parity) |
| **F460** | `020012` (2.0.12) | Linux AArch64 (ARM64) | 15,569 | Ext4, Gzip, Zip | `bt_daemon`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `bt_supervisione`, `coso` | **Emulated** (Phase 2 — 26 suites, full parity) |
| **H4684** | `020054` (2.0.54) | Linux ARMv4 `oabi` | 225 | Ext2, Gzip, Zip | `bt_processi`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_difson`, `bt_vct`, `bt_energia`, `bt_alarm` | **Emulated** (Phase 2 — 26 suites, full parity) |
| **L4686SDK** | `012001` (1.20.1) | Bare-metal Microchip PIC18 (`0x000000`-`0x00EDDD`) | 3 | Zip, Intel HEX | OpenWebNet-to-SCS serial translator (`4686SDK_012001.HEX`, no OS) | Static analysis only (PIC18 serial/USB translator — no TCP/IP stack) |
| **L4561N** | `040006` (4.0.6) | Bare-metal Microcontroller | 3 | Zip, Intel HEX | Stereo control interface firmware (`rca_ir.HEX`) | Pending Emulation (Specialized bus interface — zero matrix value) |
| **3550** | `030020` (3.0.20) | Bare-metal Mitsubishi M16C (`0x0E0000`-`0x0FFFFF`) | 5 | Zip, ZipCrypto, Motorola S-record | 99-zone central unit firmware (`3550_030020.mot`, no OS) | Static analysis only (QEMU has no M16C target) |
<!-- FLEET_TABLE_END -->

### Architectural Differences: Linux vs. Bare-Metal Microcontroller

The firmware oracle encounters two fundamentally different system architectures across the catalog:

1. **Embedded Linux Gateways & Consoles**
   - **Architectures**: Linux ARMv4 `oabi` (MH200, F453, F453AV, H4684), ARMv5 `eabi5` (MH200N, MH202, F450, F454, F459), ARMv7 `eabi5` (MyHomeServer1), and AArch64 (F460, F461).
   - **Structure**: Multi-layer archives (`Zip`, `Gzip`, `U-Boot uImage`) containing standard Linux root filesystems (`Ext2`, `Ext3`, `Ext4`; `unpack.py` also supports `SquashFS` and `CramFS`). When unpacked, they produce hundreds to tens of thousands of individual user-space binaries, shared libraries, and configuration files.
   - **Oracle Execution**: Evaluated via user-space emulation (`qemu-arm` / `qemu-aarch64`) with simulated serial PTYs (`/dev/ttyPIC`, `/dev/ttyS1`, `/dev/ttymxc2`, `/dev/ttyRPMSG30`) and loopback TCP sockets.
2. **Bare-Metal Microcontroller Gateways & Bus Peripherals (F455, MH201, L4686SDK, L4561N, 3550)**
   - **Architectures**: ARM Cortex-M microcontrollers (F455: Cortex-M3/M4, vector table base `0x08000000`, initial SP `0x20004178`; MH201: STM32F217 Cortex-M3, vector table base `0x08000000`, initial SP `0x200041a0`), Microchip PIC18 (L4686SDK OpenWebNet USB & RS-232 serial bus translator, L4561N stereo bus interface), and Mitsubishi/Renesas M16C/60 (3550 99-zone temperature control central unit, `0x0E0000`–`0x0FFFFF`).
   - **Structure**: Monolithic flash images (`F455_1_1_2.bin` 301 KB, `MH201_3_6_44_signed.bin` 502 KB, `4686SDK_012001.HEX` and `rca_ir.HEX` Intel HEX, `3550_030020.mot` Motorola S-records) with no Linux operating system, shell, or filesystem. The bootloader, RTOS/TCP/IP stack (LwIP / CMX-RTX where applicable), OpenWebNet parser, and SCS transceiver logic are compiled directly into a single binary image.
   - **Manifest Size**: Explains why `results/F455/010102/manifest.tsv`, `results/MH201/030644/manifest.tsv`, `results/L4686SDK/012001/manifest.tsv`, `results/L4561N/040006/manifest.tsv`, and `results/3550/030020/manifest.tsv` contain only 3–5 entries each (wrapper archive, metadata file, and raw flash image).

### Gateway Census: Complete Ingestion vs. Excluded Hardware

The oracle project catalogues **100% of all standalone OpenWebNet IP/SCS gateways** for which BTicino or Legrand publicly released downloadable firmware update packages.

To ensure complete clarity regarding the BTicino/Legrand MyHOME product ecosystem, the table below details the ingested fleet versus hardware that is not part of the firmware oracle:

#### 1. Ingested Fleet (Released Firmware Packages)
<!-- FLEET_CENSUS_START -->
- **MH200N** (`010108` / 1.1.8): DIN scenario programmer & OpenWebNet gateway. *(Emulated — 26 suites, full matrix parity)*
- **MH200** (`020102` / 2.1.2): Legacy DIN scenario programmer & OpenWebNet gateway. *(Emulated — 26 suites, full matrix parity)*
- **MyHomeServer1** (`028206` / 2.82.6): Modern Linux gateway & IoT bridge. *(Emulated — 26 suites, full matrix parity)*
- **F454** (`020051` / 2.0.51): Web server audio/video DIN gateway. *(Emulated — 26 suites, full matrix parity)*
- **MH202** (`010024` / 1.0.24): Advanced scenario programmer & BACnet gateway. *(Emulated — 26 suites, full matrix parity)*
- **F459** (`020105` / 2.1.5): Hotel / hospitality driver manager gateway. *(Emulated — 26 suites, full matrix parity)*
- **F453AV** (`030014` / 3.0.14): DIN audio/video web server (ARMv4 OABI). *(Emulated — 26 suites, full matrix parity)*
- **F453** (`010100` / 1.1.0): DIN web server 6-module gateway (ARMv4 OABI). *(Emulated — 26 suites, full matrix parity)*
- **F460** (`020012` / 2.0.12): Hotel scenario programmer gateway (AArch64 / ARM64). *(Emulated — 26 suites, full matrix parity)*
- **F461** (`020011` / 2.0.11): Server gateway stack (AArch64 / ARM64). *(Emulated — 26 suites, full matrix parity)*
- **F450** (`020010` / 2.0.10): IP interface gateway (OPEN-BACnet). *(Emulated — 26 suites, BACnet side on the firmware's own `ebacgw` and its factory plant)*
- **F455** (`010102` / 1.1.2): Basic OpenWebNet IP interface (bare-metal ARM Cortex-M). *(Pending Emulation — bare-metal microcontroller flash image without OS/userland; basic lighting/shutter subset already 100% covered by Linux gateways with zero added value to the matrix)*
- **MH201** (`030644` / 3.6.44): Hotel guest room scenario module (bare-metal ARM Cortex-M3 STM32F217). *(Pending Emulation — bare-metal microcontroller flash image without OS/userland; basic lighting/shutter/scenario subset already covered by Linux gateways with zero added value to the matrix)*

*(Note: Auxiliary hardware (touch screen, bus interface, temperature control central unit) such as the **H4684** Colour Touch Screen (`catalog/H4684/020054.yaml`), **L4686SDK** OpenWebNet USB & RS-232 Interface (`catalog/L4686SDK/012001.yaml`), **L4561N** Stereo Control Interface (`catalog/L4561N/040006.yaml`), and **3550** Temperature Control Central Unit (`catalog/3550/030020.yaml`) are also catalogued with full cryptographic provenance, bringing total catalogued firmware packages to 17).*
<!-- FLEET_CENSUS_END -->

#### 2. Excluded Hardware & Legacy Devices (and Why)

<!-- EXCLUDED_HARDWARE_START -->
| Product SKU | Description | Exclusion Reason |
| :--- | :--- | :--- |
| **F452 / F452V** | First-generation Web Server DIN | Discontinued early 2000s hardware. Firmware was stored in masked ROM / EEPROM; no firmware update packages were ever published for download. |
| **F458 / 003599** | IP Server | Specialized telecom/IP server module; no public firmware archive distributed. |
| **HOMETOUCH 7" (3488 / 067259)** | Connected Touchscreen | Embedded Android touch display; firmware updates are distributed exclusively as full-device Android OTA updates, not OpenWebNet gateway images. |
| **Classe 300X (`344642`, `344742`)** | Video Internal Unit with Wi-Fi | 2-wire video internal unit with Netatmo cloud bridging; firmware updates are distributed exclusively as encrypted OTA cloud synchronization. |
| **Classe 300 EOS (`344842`, `344845`)** | Smart Video Internal Unit | Connected video internal unit with Alexa; firmware updated exclusively via Netatmo / Legrand cloud OTA. |
| **HC4690 / HD4690 / HS4690 (`067285`)** | Multimedia Touch Screen | 10-inch multimedia display console; specialized display firmware. |
| **F422 / 003562** | SCS-to-SCS Interface Router | Pure galvanic bus-to-bus bridge microcontroller; no IP interface or OpenWebNet parser. |
| **F429 / 002631** | SCS/DALI Gateway | Specialized DALI lighting interface controller; no OpenWebNet TCP server daemon. |
| **BMNE4000 / 048832** | SCS/ZigBee Gateway | Hardware radio bridge; firmware is embedded radio stack without standalone OpenWebNet daemon. |
| **3578 / 088328** | OpenWebNet ZigBee USB Stick | Telegesis ETRX2USB / Silicon Labs EM250 wireless dongle; no standalone firmware package published on Home Systems or bundled in MyHOME_Suite. |
| **H4691 / LN4691 / KM4691** | Thermostat with display (temperature control zone probe) | No firmware package published: their Home Systems product sheets carry manuals only, and neither MyHOME_Suite 3.5.38 nor TiThermo 2.0 bundles a probe image. |
| **L4600/4 / N4600/4** | 4-zone temperature control central unit | No firmware package published: the Home Systems product sheets carry manuals only. |
<!-- EXCLUDED_HARDWARE_END -->

---

## Published Findings & Subsystem Audits

Detailed technical findings documents with exact bytecode citations, cryptographic hashes, and architectural explanations are published in [`findings/`](findings/):

| Subsystem / Topic | Affected WHO | Gateways Investigated | Summary & Finding Link |
| :--- | :--- | :--- | :--- |
| **Autodiagnostics Co-occurrence** | WHO 1 (Lighting), WHO 1001 (Diag) | MH200N, MyHomeServer1 (and full 12-gateway fleet) | [**Lighting WHAT 19 / WHO 1001 Autodiagnostics Co-occurrence**](findings/MH200N/what19.md)<br>Proves `bt_luci` maps SCS fault `'E'` to `*1*19*WHERE##`, while `bt_device` / `libdiag.so` emit diagnostic mask `*#1001*WHERE*11*<bitmask>##`. Validated against capture `EVID-MH200-WHAT19-FAULT`. |
| **OWNd#77 Empirical Replay** | WHO 1, 4, 18, 15/25, 1004 | MH200N (and full 12-gateway fleet) | [**Replay of OWNd#77 Audit Fixes on MH200N**](findings/MH200N/ownd-77-replay.md)<br>Replay of 10 audit findings from OWNd#77 across 9 test suites, determining which behaviors hold across gateways and which depend on gateway-specific daemon pipelines. |
| **Area 0 Point-to-Point Lighting** | WHO 1 (Lighting) | Full 12-gateway fleet | [**Area 0 Point-to-Point Lighting Addresses (`A=0, PL=1..15`)**](findings/MH200N/area0-lighting.md)<br>28 test cases (`lights-area0.cases`) proving `*1*1*02##` (2-digit) addresses `A=0, PL=2` point-to-point, `*1*1*0010##`–`*1*1*0015##` (4-digit) addresses `A=0, PL=10..15`, `*1*1*0002##` is NACKed by 11/12 gateways, and `*1*1*2##` is strictly an Area 2 broadcast. |
| **Legacy MH200 Architecture & Area 0** | WHO 1, 3, 4, 5, 6, 7, 9, 13, 14, 15, 16, 17, 1001, 1013 | MH200 (`020102`) | [**MH200 Firmware 2.1.2 Architecture & Boundary Verification**](findings/MH200/README.md)<br>Establishes that the legacy MH200 (item 003535) runs a full ARMv4 OABI Linux OpenWebNet stack (`openserver`, `scsserver`, `bt_luci`, `bt_termo`, `bt_difson`, `bt_vct`) and resolves the Area 0 addressing boundary reported in MyHOME#684 / #713. |
| **F453 6-DIN Web Server Architecture** | WHO 0, 1, 2, 3, 4, 5, 6, 7, 9, 13, 14, 15, 16, 18, 22, 25, 1001..1022 | F453 (`010100`) | [**F453 Firmware 1.1.0 (`WS_6DIN`) Architecture & Provenance**](findings/F453/README.md)<br>Documents the 16 MB flash partition layout (`ubtweb_only_recovery.gz` + `btweb_app.zip`) recovered from `MyHOME_Suite_030538.exe`, including the complete 11-daemon ARMv4 OABI OpenWebNet stack (`bt_alarm` enabled on WHO 5/9 alongside `bt_luci`, `bt_termo`, `bt_multi`, `bt_supervisione`). |
| **TS10 Thermoregulation Protocol** | WHO 4 (Thermo) | MyHomeServer1, MH200N, MH202 (and full 12-gateway fleet) | [**TS10 Thermo Protocol Verification**](findings/MyHomeServer1/thermo-ts10.md)<br>Empirical validation of WHO 4 compound probe addressing (`#probe#central`), timed manual/holiday modes with duration encoding, 16 weekly programs/scenarios, and calendar dimensions (30, 31, 32). |
| **TS10 Sound Diffusion Protocol** | WHO 22 (Sound) | MyHomeServer1, F454, MH200N (and full 12-gateway fleet) | [**TS10 Sound Diffusion Protocol Verification**](findings/MyHomeServer1/sound-who22.md)<br>58 test cases covering volume, tone, sources, amplifier status, and matrix routing. Proves byte-identical SCS output between MHS1 (`bt_multi`) and F454, while MH200N strictly refuses WHO 22 at its subsystem boundary. |
| **TS10 Energy & Load Management** | WHO 18 (Energy), WHO 3 (Load Shedding) | MyHomeServer1, MH202, MH200N (and full 12-gateway fleet) | [**TS10 Energy & Load Management Verification**](findings/MyHomeServer1/energy-ts10.md)<br>65 test cases proving that TS10 WHAT commands (`*18*57..`) and classic dimension queries (`*#18*..*511..`) generate identical extended SCS telegrams. Verified periodic reporting (`DIMENSION 1200`) and Stop&Go breaker controls. |
| **TS10 Video Door Entry & Intercom** | WHO 8 (Door Entry / Intercom) | MyHomeServer1, MH202, MH200N, F454 (and full 12-gateway fleet) | [**TS10 Video Door Entry & Intercom Verification**](findings/MyHomeServer1/intercom-ts10.md)<br>59 test cases demonstrating subsystem boundary isolation: automation gateways (MHS1, MH200N, MH202) reject WHO 8, whereas F454 routes WHO 8 to `bt_vct`. |
| **OPEN-BACnet Gateway & Plant DB** | WHO 4 (Thermo), WHO 13 | F450 (`020010`) | [**F450 OPEN-BACnet Architecture**](findings/F450/open-bacnet-architecture.md) & [**BACnet `ebacgw` Plant Database**](findings/F450/bacnet-plant-database.md)<br>Documents F450's `bacclient` + `ebacgw` SOAP/BACnet bridge (no `openserver`) and derives the minimal SQLite `master.sqlite` plant seed from `ebacgw` and factory `conf.xml.def` so F450 runs deterministically on its own firmware daemons. |
| **OpenWebNet USB & RS-232 Interface** | WHO 0, 1, 2, 4, 5, 8, 13, 14, 15, 16, 18, 22, 25, 1001..1022 | L4686SDK (`012001`) | [**L4686SDK (`L4686/USB` / `3559`) PIC18 Static Analysis**](findings/L4686SDK/README.md) & [**Emulation Boundary**](findings/L4686SDK/emulation-strategy.md)<br>Static analysis of the Microchip PIC18 Intel HEX firmware (`4686SDK_012001.HEX`), documenting its `OPEN_SCS` memory map (`0x0000`–`0xEDDD`) and ROM lookup tables mapping ASCII OpenWebNet (`*...##`) directly to 2-wire SCS bus telegrams. |
| **99-Zone Thermo Central Unit** | WHO 4 (Thermo) | 3550 (`030020`) | [**3550 Temperature Control Central Unit Static Analysis**](findings/3550/README.md)<br>Static analysis of the Mitsubishi M16C/60 Motorola S-record firmware (`3550_030020.mot`), documenting its memory map (`0x0E0000`–`0x0FFFFF`), `$`-framed SCS bus templates, and separate daily holiday vs. multi-day vacation tables. |
| **Bare-Metal MCU & Bus Peripherals** | WHO 1, 2, 16, 17 | F455 (`010102`), MH201 (`030644`), L4561N (`040006`) | [**F455**](findings/F455/emulation-strategy.md), [**MH201**](findings/MH201/emulation-strategy.md) & [**L4561N Emulation Strategies**](findings/L4561N/emulation-strategy.md)<br>Hardware architecture and boundary cuts for the Cortex-M flash images (F455, MH201 CMX-RTX/LwIP) and Microchip PIC18 SCS stereo interface (`L4561N`, modeled on the virtual bus via `SoundSourceResponder`). |
| **Empty-Bus `reply` Semantics** | All (bus writes) | F460 (`020012`), F461 (`020011`), MyHomeServer1 (`028206`) | [**Empty-Bus `reply` Semantics on F460 / F461 / MyHomeServer1**](findings/empty-bus-reply-semantics.md)<br>On these gateways a full-suite `nack` with `verdict=out` means no device TX-ACK (`$00800` / `$08`) on the empty bus, not a refusal (228 of 309 `out` rows each). Plant runs and live F461 2.0.11 traces ACK the same frames. `mcp_index.json` marks them `reply_semantics: "empty_bus_txack"`. |
| **Inbound PIC / RPMsg Serial Framing** | WHO 1, 2, 4, 14, 18, 1001 | F461 (`020011`), MyHomeServer1 (`028206`), MH200 (`020102`), MH200N (`010108`) | [**Inbound PIC / RPMsg Serial Framing Protocol (`scsserver`)**](findings/pic-inbound-protocol.md)<br>Binary reverse engineering and live QEMU closed-loop spike (`tools/pic_spike.py`) establishing the 3-digit RPMsg (`$00800\r` TX-ACK + `$017`/`$018`) and 2-digit UART (`$19\r`/`$00\r`/`$08\r` TX-ACK + `$17`/`$18` with 25 ms read-buffer pacing) PIC handshakes. |
| **Gateway Clock & Time Zone** | WHO 13 (Gateway) | Full 12-gateway fleet | [**WHO 13 Clock Writes, Reads & the `T` Field**](findings/who13-clock.md)<br>29 steps (`who13-clock.cases`, `who13-clock-readback.seq`) on clock writes (DIM 0 / 1 / 22), reads, and malformed `T` (`999`, empty, `124`, `013`, `0530`). Nine gateways ACK every `T`; F454 and F459 NACK only the 4-digit `0530`; F450 stores and reports `0530`; MyHomeServer1 with its factory NTP setting never answers DIM 0 / 1 writes. MH200N's `bt_device` maps `T=002` to `Etc/GMT+2` (UTC−2). |
| **VDK `MyHomeSystemEmulator` Audit & Clean-Room Plant** | WHO 1, 2, 4, 13, 14, 18, 1001 | MH200 (`020102`), MH200N (`010108`), F454 (`020051`), MyHomeServer1 (`028206`), F461 (`020011`) | [**Audit of Archived `MyHomeSystemEmulator` vs. Real Firmware & Hardware**](findings/vdk-emulator-audit.md)<br>Documents 10 architectural and protocol divergences in `MyHomeSystemEmulator@4f93f44ee5ec` (missing SCS byte framing, plugin-emitted ACKs, Area 0 bug, missing `DIM 15` group status, dimmers, shutters, `F422` isolation, and `WHO=4` thermo) and details the clean-room Apache-2.0 [`oracle/plant/`](oracle/plant/) implementation. |

---

## How to Use: Concrete Examples

See [`docs/quickstart.md`](docs/quickstart.md) for the `< 10 minute` local setup and how to propose new `.cases` suites without running QEMU locally, [`docs/adding-a-gateway.md`](docs/adding-a-gateway.md) for onboarding new gateways, and [`docs/mcp-index-contract.md`](docs/mcp-index-contract.md) for the `mcp_index.json` schema contract.

### 1. Materializing & Unpacking a Firmware Image

Download the official vendor archive and unpack it into a deterministic filesystem manifest:

```bash
# A) Fetch and verify the vendor archive by SHA-256:
IMG=$(python tools/fwfetch.py catalog/MH200N/010108.yaml)

# B) Unpack container layers (Zip, U-Boot, Gzip, Ext2/3/4) into manifest.tsv:
python tools/unpack.py catalog/MH200N/010108.yaml "$IMG" \
  -o results/MH200N/010108/manifest.tsv

# C) Verify the workspace remains clean of raw binaries or NUL leaks:
python tools/guard.py
```

### 2. Validating Workspace Freshness and Integrity

```bash
# Check if any catalogued images or unpacked manifests are stale:
python tools/plan.py

# Check that README.md, results/mcp_index.json, and results/plant_index.json are in sync:
python tools/mcp_index.py --check
python tools/plant_index.py --check
python tools/sync_readme.py --check

# Enforce quality gates (ruff, mypy, test coverage):
ruff check .
ruff format --check .
mypy --strict tools oracle
pytest tests --cov=tools --cov=oracle --cov-report=term-missing
```

### 3. Running Emulated Oracle Test Suites (`oracle.run`)

Run a `.cases` test suite against an emulated gateway daemon under `qemu-user`:

```bash
# A) Run the lights-level suite against the emulated MH200N openserver:
python -m oracle.run suite \
  oracle/targets/MH200N/010108.yaml \
  oracle/cases/lights-level.cases \
  --image "$IMG" \
  -o results/MH200N/010108/oracle/full/lights-level.tsv

# B) Run the TS10 sound suite on F454 (serial PTY translation to SCS):
python -m oracle.run suite \
  oracle/targets/F454/020051.yaml \
  oracle/cases/sound-who22.cases \
  --image "$IMG_F454" \
  -o results/F454/020051/oracle/full/sound-who22.tsv

# C) Run the TS10 energy suite on MH202 (multi-daemon stack):
python -m oracle.run suite \
  oracle/targets/MH202/010024.yaml \
  oracle/cases/energy-ts10.cases \
  --image "$IMG_MH202" \
  -o results/MH202/010024/oracle/full/energy-ts10.tsv

# The generated TSV (oracle_version=3) contains deterministic rows:
# direction    input              reply    verdict    output
# down         *1*1*31##          ack      out        bus:24 30 33 33 31 30 30 31 32 30 30 0d
# down         *#1*31*#1*100*0##  nack     silent     -
```

### 4. Cross-Gateway Comparison, Semantic Diff, Live Canary & Evidence Export

```bash
# Compare a single frame across all emulated gateways (with decoded SCS bytes):
python tools/compare.py frame "*1*1*31##"

# List all cross-gateway divergences for a suite or render a gateway x frame matrix:
python tools/compare.py divergences --suite sound-who22
python tools/compare.py matrix --suite lights-level

# Summarize behavioural verdict changes across a PR or git range:
python tools/verdict_diff.py --base origin/main --head HEAD

# Check oracle outputs against OWNd parser & live hardware traces:
python tools/check.py --all --report

# Validate or execute the read-only live hardware canary suite:
python tools/live_probe.py --suite oracle/canary/canary-live.cases --validate-only

# Export an oracle TSV into a schema-valid OpenWebNet-Encyclopedia evidence bundle:
python tools/export_evidence.py results/MH200N/010108/oracle/full/what19.tsv \
  --out-dir /tmp/cap-mh200n-what19-20261015 --date 2026-10-15
```

### 5. Querying the Cross-Firmware Verdict Index (`results/mcp_index.json`)

The cross-firmware MCP index (`schema_version: "1.2.0"`) provides hash-pinned answers for AI assistants and parsers without needing to run emulators. Gateways are indexed with status `"emulated"` (verdicts present) or `"catalogued"` (fleet inventory entry, empty `target_sha256`, `suites: []`). The canonical `verdicts_sha256` digest covers the sorted verdicts map, ensuring deterministic answer integrity.

Each gateway also carries `reply_semantics`. On F460, F461 and MyHomeServer1 it is `"empty_bus_txack"`: their full suites run on an empty bus, and that firmware NACKs every write no device acknowledges, so `reply: "nack"` with `verdict: "out"` means the frame **went out**, not that the gateway refused it. Use `verdict` there, and `results/plant_index.json` for populated-bus replies ([`findings/empty-bus-reply-semantics.md`](findings/empty-bus-reply-semantics.md)).

```bash
# Check that the index matches all current oracle TSVs and passes schema validation:
python tools/mcp_index.py --check
```

Query the index directly in Python:

```python
import json
from pathlib import Path

# Load the hash-pinned index
index_path = Path("results/mcp_index.json")
index = json.loads(index_path.read_text(encoding="utf-8"))
print(f"Verdicts SHA-256: {index['verdicts_sha256']}")
print(f"Indexed frames: {len(index['verdicts'])}")
gateways = [
    f"{g['product']} {g['version']} [{g['status']}, {g['reply_semantics']}]"
    for g in index["gateways"]
]
print(f"Gateways: {gateways}")

# Lookup cross-gateway verdict for a sound volume command (WHO 22):
target_frame = "*#22*3#1#1*#1*20##"  # Direct volume 20

for entry in index["verdicts"].get(target_frame, []):
    print(f"[{entry['product']} {entry['version']}] Suite: {entry['suite']}")
    print(f"  Reply:      {entry['reply']}")  # 'ack' on MHS1 & F454, 'nack' on MH200N
    print(
        f"  Verdict:    {entry['verdict']}"
    )  # 'out' on MHS1 & F454, 'silent' on MH200N
    print(f"  Bus output: {entry['bus_frames']}")  # ['$0493018114\r' on MHS1 and F454]
```

### 6. Running Closed-Loop Virtual Plant Traces & Querying `results/plant_index.json`

Run a stateful `.seq` suite against an emulated gateway attached to a virtual SCS plant fixture (`f461-bench` or `mh200-bench`):

```bash
# A) Run the F461 bench sequence against emulated F461 (3-digit RPMsg PIC + f461-bench plant):
python -m oracle.run suite \
  oracle/targets/F461/020011.yaml \
  oracle/plant_cases/plant-f461-bench.seq \
  --image "$IMG_F461" \
  --plant f461-bench \
  -o results/F461/020011/oracle/plant-f461-bench/plant-f461-bench.tsv

# B) Run the MH200 dimmer & F422 sub-bus suites against emulated MH200 (2-digit UART PIC + mh200-bench plant):
python -m oracle.run suite \
  oracle/targets/MH200/020102.yaml \
  oracle/plant_cases/plant-mh200-bench.seq \
  --image "$IMG_MH200" \
  --plant mh200-bench \
  -o results/MH200/020102/oracle/plant-mh200-bench/plant-mh200-bench.tsv

python -m oracle.run suite \
  oracle/targets/MH200/020102.yaml \
  oracle/plant_cases/plant-f422-subbus.seq \
  --image "$IMG_MH200" \
  --plant mh200-bench \
  -o results/MH200/020102/oracle/plant-mh200-bench/plant-f422-subbus.tsv

# C) Rebuild or verify the hash-pinned virtual plant index:
python tools/plant_index.py --check
```

Inspect closed-loop virtual traces in Python via `results/plant_index.json`:

```python
import json
from pathlib import Path

plant_index = json.loads(Path("results/plant_index.json").read_text(encoding="utf-8"))
print(f"Plant suites: {plant_index['suites']}")
print(f"Unique closed-loop inputs: {plant_index['total_unique_inputs']}")

# Inspect the 4-step WHO=4 zone status burst on F461 + f461-bench:
for entry in plant_index["verdicts"]["*#4*2##"]:
    print(f"[{entry['product']} {entry['version']} / {entry['harness']}]")
    print(f"  Reply:       {entry['reply']}")
    print(f"  Bus TX:      {len(entry['bus_frames'])} frames")
    print(f"  Plant RX:    {len(entry['bus_rx'])} frames")
    print(f"  CMD replies: {entry['cmd_replies']}")
    print(f"  EVT events:  {entry['evt_events']}")
```

---

## Ground rules

Based on the framework in #613 (EU Software Directive 2009/24/EC Art. 5(3) /
Art. 6; protocols aren't copyrightable, CJEU C-406/10):

- only images we lawfully hold (official downloads or our own devices);
- the archive passwords are **published vendor packaging strings** (the model
  name / `bticino`, used by BTicino's own TiMH200N updater). `unpack.py` resolves
  them from each catalog entry's `password_scheme`; it does **not** brute-force or
  crack anything. This is "using a known password to open software we lawfully
  hold, to study it for interoperability";
- prefer black-box emulation over reading disassembly;
- publish facts in our own words; never binaries, disassembly or decompiled code;
- the only goal is interoperability with OWNd / MyHOME.

## Status

<!-- STATUS_PHASE1_START -->
- **Phase 1: Complete Fleet Ingestion & Unpack.** Done for all 13 standalone OpenWebNet gateways (MH200N, MH200, MyHomeServer1, F454, MH202, F453AV, F453, F455, MH201, F461, F450, F459, F460). Every manifest is verified byte-for-byte and covered by weekly CI reproducibility runs.
<!-- STATUS_PHASE1_END -->
- **Phase 2b: Cross-Gateway Translation Divergence.**
  - `lights-level` suite verified on both MH200N and MyHomeServer1.
  - **MH200N**: Immediate ACK (`*#*1##` within ~13ms) upon queuing to the PIC UART.
  - **MyHomeServer1**: Transactional confirmation model via `bt_luci`; awaits bus confirmation (type 4 frame) or times out at 2.0s with NACK (`*#*0##`).
- **Phase 2c: Autodiagnostics Co-occurrence (WHAT 19).** Completed and documented in [`findings/MH200N/what19.md`](findings/MH200N/what19.md). Proves `bt_luci` translates SCS `'E'` to `*1*19*WHERE##` while `bt_device` / `libdiag.so` emit diagnostic frame `*#1001*WHERE*11*<bitmask>##`. Validated against bus capture `EVID-MH200-WHAT19-FAULT`.
- **Phase 2d: Empirical Replay of OWNd#77 Audit Fixes.** Completed across 9 case suites (thermoregulation, energy, CEN+, interface routing, WHO 25) with deterministic outputs in `results/MH200N/010108/oracle/full/` and evaluation checks in `results/MH200N/010108/checks/`. Findings synthesized in [`findings/MH200N/ownd-77-replay.md`](findings/MH200N/ownd-77-replay.md).
- **Phase 2e: BTicino TS10 Reference Subsystem & Extended Protocol Verification.** Completed across the TS10 reference suites derived from `libqtdevices TS10_1_0_23` (OWNd#83) and extended subsystem suites:
  - **Thermoregulation (WHO 4)**: `thermo-ts10.cases`, `thermo-holiday-program.cases`, and `ownd-pr82-heating.cases` ([`findings/MyHomeServer1/thermo-ts10.md`](findings/MyHomeServer1/thermo-ts10.md)). Validates compound probe addressing (`#probe#central`), timed/holiday/vacation modes (`115#P`, `13DDD#P`), weekly programs 1..16, and calendar dimensions 30, 31, 32.
  - **Sound Diffusion (WHO 16 & WHO 22)**: `sound-who22.cases` and `sound-who16-source.cases` ([`findings/MyHomeServer1/sound-who22.md`](findings/MyHomeServer1/sound-who22.md)). Proves byte-identical SCS output between MHS1 (`bt_multi`) and F454 for volume, while MH200N strictly refuses WHO 22 at its subsystem boundary.
  - **Energy Management (WHO 18 & WHO 3)**: `energy-ts10.cases` ([`findings/MyHomeServer1/energy-ts10.md`](findings/MyHomeServer1/energy-ts10.md)). Proves exact equivalence between TS10 WHAT commands (`*18*57..`) and classic dimension frames (`*#18*..*511..`), automated reporting (`DIMENSION 1200`), and Stop&Go breaker controls.
  - **Video Door Entry & Intercom (WHO 8)**: `intercom-ts10.cases` ([`findings/MyHomeServer1/intercom-ts10.md`](findings/MyHomeServer1/intercom-ts10.md)). Confirms subsystem boundary isolation across automation gateways (MHS1, MH200N, MH202) versus Audio/Video routing on F454.
  - **Area 0 Lighting, Scenarios & Golden Trace Corpus**: `lights-area0.cases` ([`findings/MH200N/area0-lighting.md`](findings/MH200N/area0-lighting.md)), `ownd-pr82-lights.cases`, `scenario-who17.cases`, `matrix-refused.cases`, and `trace-corpus.cases` verified across all 11 emulated gateways.
<!-- STATUS_PHASE2F_START -->
- **Phase 2f: Gateway Fleet Target Emulation.** Expanded execution harness in `oracle/qemu_target.py` and target specifications in `oracle/targets/` supporting 12 active gateways under QEMU user emulation, achieving **full matrix parity across all 26 standard test suites (312 complete suite TSVs)**:
  - **MH200N** (`010108`): DIN scenario programmer (`openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_difson`, `bt_multi`, `bt_supervisione`, `bt_energia`, `bt_alarm` — 26 suites).
  - **MH200** (`020102`): Legacy DIN scenario programmer (`bt_processi`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_energia`, `bt_difson`, `bt_alarm`, `bt_vct` — 26 suites).
  - **MyHomeServer1** (`028206`): Multi-daemon Linux gateway (`openserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `bt_supervisione` on 30018/31018 — 26 suites).
  - **F454** (`020051`): Audio/Video web server DIN gateway (`bt_daemon`, `stackopen` serial PTY `/dev/ttyS1`, `bt_vct`, `openserver`, `scsserver` — 26 suites).
  - **MH202** (`010024`): Advanced scenario programmer & BACnet gateway (`bt_daemon`, `stackopen`, `bt_device`, `bt_energia`, `bt_supervisione`, `openserver`, `scsserver` — 26 suites).
  - **F459** (`020105`): Hospitality / hotel room gateway (`openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `bt_energia`, `bt_supervisione`, `coso` — 26 suites).
  - **F453AV** (`030014`): Legacy DIN audio/video gateway (`openserver`, `bt_vct`, `bt_processi` with `/dev/dsp1` audio DSP and `/dev/ttyPIC` PTY under ARMv4 OABI — 26 suites).
  - **F453** (`010100`): Legacy 6-module DIN web server (`openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_difson`, `bt_multi`, `bt_vct`, `bt_energia`, `bt_supervisione`, `bt_alarm` with `/dev/dsp1` and `/dev/ttyPIC` PTY under ARMv4 OABI — 26 suites).
  - **F460** (`020012`): Eliot AArch64 hotel scenario programmer stack (`openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `bt_supervisione`, `coso` on `/dev/ttyRPMSG30` PTY — 26 suites).
  - **F461** (`020011`): Eliot AArch64 server gateway stack (`openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `bt_supervisione`, `coso` on `/dev/ttyRPMSG30` PTY — 26 suites).
  - **F450** (`020010`): OPEN-BACnet gateway (`bacclient`, `ebacgw` on its factory BACnet plant, `scsserver`, `bt_device`, `bt_termo` — 26 suites).
  - **H4684** (`020054`): Colour touch screen console (`bt_processi`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_difson`, `bt_vct`, `bt_energia`, `bt_alarm` on `/dev/ttyPIC` PTY under ARMv4 OABI — 26 suites).
<!-- STATUS_PHASE2F_END -->
<!-- STATUS_PHASE3_START -->
- **Phase 3: Hash-Pinned MCP Verdict Index.** Completed schema 1.1.0 index covering the full catalogued fleet. `tools/mcp_index.py` aggregates verdicts across suites and gateways into `results/mcp_index.json`, protected by a canonical SHA-256 fingerprint (`verdicts_sha256`) for direct consumption by `openwebnet-mcp`. The index tracks **567 unique OpenWebNet frames** across **26 test suites** and **12 active gateways** (MH200N, MH200, MyHomeServer1, F454, MH202, F459, F453AV, F453, F460, F461, F450, H4684), delivering **7,344 deterministic verdict entries** with a zero-diff PR consistency gate in CI (`tools/mcp_index.py --check`). Catalogued devices without Linux userland (F455, MH201, L4686SDK, L4561N, 3550) are indexed with `status: "catalogued"` and empty suite arrays (bare-metal microcontroller flash firmware or interfaces without an OS; basic lighting/shutter/scenario OpenWebNet subsets already 100% covered).
<!-- STATUS_PHASE3_END -->
- **Phase 4: Cross-Gateway Analysis, Conformance & Anti-Drift Tooling.** Shipped offline analysis and verification tools (`tools/compare.py`, `tools/verdict_diff.py`, `tools/check.py`, `tools/live_probe.py`, `tools/export_evidence.py`, `tools/sync_readme.py`) with crash-awareness gates and automated CI step summaries.
- **Phase 5: Closed-Loop Virtual SCS Plant & Full Virtual Trace Reproduction.** Shipped clean-room stateful virtual SCS plant (`oracle/plant/`), hardware-pinned plant fixtures (`f461-bench.yaml`, `mh200-bench.yaml`), and closed-loop `.seq` suites (`oracle/plant_cases/*.seq`) verified under QEMU across `F461/020011` (3-digit RPMsg PIC), `MH200/020102` (2-digit legacy PIC), and `MyHomeServer1/028206` (2-digit v8 PIC with 25 ms UART frame pacing). Indexed 48 unique closed-loop inputs across 6 plant suite TSVs in `results/plant_index.json` (`tools/plant_index.py --check`), documented the 2-digit/3-digit PIC inbound protocols ([`findings/pic-inbound-protocol.md`](findings/pic-inbound-protocol.md)), and published the 10-point architectural audit of the archived `MyHomeSystemEmulator` ([`findings/vdk-emulator-audit.md`](findings/vdk-emulator-audit.md)).

## License

[Apache License 2.0](LICENSE). Covers this repo's own tooling, results and
findings only — never any vendor firmware, which is not redistributed here.
The scope and copyright line are in [NOTICE](NOTICE).
