# Verification of BTicino 10" Touch Screen (TS10) Sound Diffusion Protocol (WHO 22)

## 1. Executive Summary

In commit `f2df920` of [OWNd#83](https://github.com/OpenWebNet-HA/OWNd/pull/83), OWNd began aligning with BTicino's 10-inch Touch Screen reference stack (`libqtdevices` tag `TS10_1_0_23`). The TS10 panel implements comprehensive control over the OpenWebNet Sound Diffusion subsystem (WHO 22) via `SoundSystemDevice`, `AmplifierDevice`, `SourceDevice`, and `RadioSourceDevice`.

This document records the empirical execution of the `sound-who22` test suite (58 test cases, suite SHA-256 `7bab0812...`) against three reference gateway firmwares under QEMU emulation with simulated SCS bus PTYs:
1. **MyHomeServer1 / 028206** (Linux ARM, unpacked rootfs SHA-256 `aa1cd393e4ae4262b3b2c4f9f25b8e831adbed648975505e2084c801f2b7f5e7`)
2. **F454 / 020051** (Linux ARM, unpacked rootfs SHA-256 `15f80bc673e53fa105a68725b5a6d3ccf06767d1bae0e6139803de34e485f0f3`)
3. **MH200N / 010108** (Linux ARM, unpacked rootfs SHA-256 `e32d3404302f3619bbbb564ae50554ab81ac6ef818b6e5a2290142a5e2ab5fb3`; package archive SHA-256 `61f8196d...`)

### Empirical Response Breakdown (MyHomeServer1)

Across all 58 cases, MyHomeServer1 (hosting the `bt_multi` sound translator daemon) yields the following empirical distribution:
- **38 ACK with SCS Bus Output (`ack` / `out`)**: Direct volume, relative volume step, tone controls (bass/treble), balance write, equalizer presets, loudness toggle, tuner step/preset/RDS commands, track navigation/selection, and standard amplifier power on/off.
- **4 ACK Silent (`ack` / `silent` / `-`)**: Standby commands de-allocating audio zones (`*22*34#4#0*3#0#6##`, `*22*34#4#1*3#1#1##`, `*22*34#4#2*3#2#0##`) and local bus configuration write (`*#22*5#2#21*11*22*33*7##`). These are validated and accepted by `bt_multi` at the OpenWebNet session layer, but do not emit telegrams to the SCS bus in isolation.
- **15 NACK with SCS Bus Output (`nack` / `out`)**:
  - **11 Status Queries**: Read requests for volume (`*#22*3#1#1*1##`, `*#22*3#2#0*1##`), amplifier state (`*#22*3#1#1*12##`), equalizer/tone (`*#22*3#1#1*2##`, `*#22*3#1#1*4##`, `*#22*3#1#1*19##`, `*#22*3#1#1*20##`), tuner/source state (`*#22*2#1*5##`, `*#22*2#1*6##`, `*#22*2#1*13##`), and local bus source status (`*#22*5#2#1*5##`). The gateway translates each query into an SCS polling telegram (`$0495...`, `$06D1...`, or `$04B5...`) and transmits it across the bus. Because our mock harness lacks active audio hardware to respond on the bus, the gateway's internal timer expires and returns `*#*0##` (NACK) to the OpenWebNet TCP client. The emitted SCS bytecode confirms the gateway's parsing and query generation.
  - **4 Follow Me Routing Frames** (`*22*35#4#...`): The gateway emits 2 to 3 sequential SCS telegrams to configure matrix routing on the bus, but returns `*#*0##` to the TCP client because the target amplifier/source handshake cannot complete on an unpopulated bus.
- **1 No Reply with SCS Bus Output (`-` / `out`)**: Balance query `*#22*3#1#1*17##`. The gateway emits `$06D101183308000000\r` onto the SCS bus, but does not emit a reply on the OpenWebNet TCP session before the 300 ms settle timeout.

### Cross-Gateway Comparison & Divergences

1. **Local Amplifier Power-On (`*22*1#4#0*5#3#0#0##`)**:
   - **MyHomeServer1**: Returns `ack` and emits `$04B0008003\r` (local bus opcode `B`).
   - **F454**: Returns `ack`, but is **silent** (no SCS telegram emitted).
   - *Note*: For local amplifier power-off (`*22*0#4#0*5#3#0#0##`), both MyHomeServer1 and F454 return `ack` and emit `$04B1008003\r`.

2. **Balance Query Reply (`*#22*3#1#1*17##`)**:
   - **MyHomeServer1**: Reply times out (`-`), emits `$06D101183308000000\r`.
   - **F454**: Reply returns `nack`, emits identical `$06D101183308000000\r`.

3. **F454 Hardware PIC Polling Probes (`$24\r`)**:
   - In F454 captures, `24 32 34 0d` (`$24\r`) appears periodically or interleaved with multi-frame transmissions. This is the PIC microcontroller version probe emitted by `scsserver` as part of its hardware keepalive loop (documented in `oracle/targets/F454/020051.yaml`), not an OpenWebNet sound protocol confirmation.

4. **MH200N Boundary Enforcement**:
   - Returns `nack` / `silent` / `-` for all 58 cases. As a logic scenario programmer lacking the `bt_multi` daemon, MH200N strictly refuses all WHO 22 frames without bus side-effects.

---

## 2. Test Provenance

- **Target Specifications**:
  - `oracle/targets/MyHomeServer1/028206.yaml`
  - `oracle/targets/F454/020051.yaml`
  - `oracle/targets/MH200N/010108.yaml`
- **Suite**: `oracle/cases/sound-who22.cases` (58 test stimuli, SHA-256 `7bab0812ef6d61cec6c73eb0bde20c34ac0ba377c5bd562b4e13cb5645a5b942`)
- **Results**:
  - `results/MyHomeServer1/028206/oracle/full/sound-who22.tsv`
  - `results/F454/020051/oracle/full/sound-who22.tsv`
  - `results/MH200N/010108/oracle/full/sound-who22.tsv`
- **Harness**: `full` (bubblewrap sandbox, QEMU ARM user-mode, OpenWebNet TCP command session + PTY SCS bus)

---

## 3. Bytecode Verification Summary

| Feature / Action | OpenWebNet Frame | MHS1 Reply | MHS1 SCS Telegram | F454 Reply | F454 SCS Telegram |
|---|---|---|---|---|---|
| **Direct Volume 20** | `*#22*3#1#1*#1*20##` | `ack` | `$0493018114\r` | `ack` | `$0493018114\r` |
| **Direct Volume 5** | `*#22*3#1#1*#1*5##` | `ack` | `$0493018105\r` | `ack` | `$0493018105\r` |
| **Volume Step Up 5** | `*22*3#5*3#1#1##` | `ack` | `$0494018105\r` | `ack` | `$0494018105\r` |
| **Volume Step Down 1**| `*22*4#1*3#1#1##` | `ack` | `$0494018111\r` | `ack` | `$0494018111\r` |
| **Equalizer Balance** | `*#22*3#1#1*#17*32##` | `ack` | `$06D101183208020000\r` | `ack` | `$06D101183208020000\r` |
| **Preset 2** | `*#22*3#1#1*#19*2##` | `ack` | `$06D10118320C330002\r` | `ack` | `$06D10118320C330002\r` |
| **Preset 11** | `*#22*3#1#1*#19*11##` | `ack` | `$06D10118320C33000B\r` | `ack` | `$06D10118320C33000B\r` |
| **Preset 16** | `*#22*3#1#1*#19*16##` | `ack` | `$06D10118320C330010\r` | `ack` | `$06D10118320C330010\r` |
| **Loudness Off** | `*#22*3#1#1*#20*0##` | `ack` | `$06D10118320C030080\r` | `ack` | `$06D10118320C030080\r` |
| **Loudness On** | `*#22*3#1#1*#20*1##` | `ack` | `$06D10118320C130080\r` | `ack` | `$06D10118320C130080\r` |
| **Bass Step Up** | `*22*36#1*3#1#1##` | `ack` | `$06D101183207C0C041\r` | `ack` | `$06D101183207C0C041\r` |
| **Bass Step Down** | `*22*37#1*3#1#1##` | `ack` | `$06D101183207C0C081\r` | `ack` | `$06D101183207C0C081\r` |
| **Treble Step Up** | `*22*40#1*3#1#1##` | `ack` | `$06D10118320741C0C0\r` | `ack` | `$06D10118320741C0C0\r` |
| **Treble Step Down** | `*22*41#1*3#1#1##` | `ack` | `$06D10118320781C0C0\r` | `ack` | `$06D10118320781C0C0\r` |
| **Direct Track 7** | `*#22*2#1*#6*7##` | `ack` | `$0493018FC6\r` | `ack` | `$0493018FC6\r` |
| **Direct Track 33** | `*#22*2#1*#6*33##` | `ack` | `$0493018FD1\r` | `ack` | `$0493018FD1\r` |
| **Track Prev** | `*22*31*2#1##` | `ack` | `$06D101F83501410400\r` | `ack` | `$06D101F83501410400\r` |
| **Track Next** | `*22*32*2#1##` | `ack` | `$06D101F83501420400\r` | `ack` | `$06D101F83501420400\r` |
| **Tuner Preset 3** | `*22*33#3*2#1##` | `ack` | `$0493018FD2\r` | `ack` | `$0493018FD2\r` |
| **Tuner Preset 6** | `*22*33#6*2#1##` | `ack` | `$0493018FD5\r` | `ack` | `$0493018FD5\r` |
| **Speaker Off (Bus)** | `*22*0#4#0*3#0#6##` | `ack` | `$0491068003\r` | `ack` | `$0491068003\r` |
| **Speaker Off (Local)**| `*22*0#4#0*5#3#0#0##` | `ack` | `$04B1008003\r` | `ack` | `$04B1008003\r` |
| **Speaker On (Bus)** | `*22*1#4#1*3#1#1##` | `ack` | `$0490018113\r` | `ack` | `$0490018113\r` |
| **Speaker On (Local)**| `*22*1#4#0*5#3#0#0##` | `ack` | `$04B0008003\r` | `ack` | **silent** (`-`) |
| **Standby (Area 0)** | `*22*34#4#0*3#0#6##` | `ack` | **silent** (`-`) | `ack` | **silent** (`-`) |
| **Status Query (Vol)**| `*#22*3#1#1*1##` | `nack` | `$0495018100\r` | `nack` | `$0495018100\r` (*+ PIC probe*) |
| **Status Query (Amp)**| `*#22*3#1#1*12##` | `nack` | `$049501818F\r` | `nack` | `$049501818F\r` (*+ PIC probe*) |
| **Query (Balance)** | `*#22*3#1#1*17##` | **-** (timeout)| `$06D101183308000000\r` | `nack` | `$06D101183308000000\r` |
| **Follow Me Routing**| `*22*35#4#0#7*3#0#0##`| `nack` | 3 frames (`$04A1...` `$0490...`) | `nack` | 3 frames (*interleaved PIC*) |
