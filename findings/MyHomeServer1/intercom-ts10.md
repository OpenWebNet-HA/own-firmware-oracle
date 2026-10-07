# Empirical Evaluation of BTicino 10" Touch Screen (TS10) Video Door Entry & Intercom (WHO 8) Across Gateway Fleet

## 1. Executive Summary

This document records the empirical execution of the `intercom-ts10` suite across the BTicino gateway fleet:
- **MyHomeServer1** (`028206`, image SHA-256 `aa1cd393e4ae4262b3b2c4f9f25b8e831adbed648975505e2084c801f2b7f5e7`)
- **MH202** (`010024`, image SHA-256 `bb561eb2b6b476de21cc4981234108b0698c2a58b1e229f2909ef4e49aef9df7`)
- **MH200N** (`010108`, unpacked image SHA-256 `e32d3404302f3619bbbb564ae50554ab81ac6ef818b6e5a2290142a5e2ab5fb3`)
- **F454** (`020051`, image SHA-256 `15f80bc673e53fa105a68725b5a6d3ccf06767d1bae0e6139803de34e485f0f3`)

The suite encompasses 59 test cases derived directly from the reference BTicino Touch Screen 10" client implementation (`libqtdevices` tag `TS10_1_0_23`, specifically `videodoorentry_device.cpp`, `videodoorentry_device.h`, and `test/test_videodoorentry_device.cpp`) and the OpenWebNet protocol specifications:
1. **Door Lock Actuators**: Activate/open lock (`*8*19*WHERE##`) and release lock (`*8*20*WHERE##`).
2. **Staircase Lighting**: Switch ON (`*8*21*WHERE##`) and switch OFF (`*8*22*WHERE##`).
3. **Camera Auto-Switching & Cycling**: Camera ON (`*8*4#CALLER*CAMERA##`) and external unit cycling (`*8*6#CALLER*MASTER_CALLER##`).
4. **Camera PTZ Movements**: Up, down, left, right pan/tilt press (`1`) and release (`2`) commands (`*8*59#..` to `*8*62#..`).
5. **Intercom Calling Modes**: Internal bus intercom (`*8*1#6#2#CALLER*CALLEE##`), external bus intercom (`*8*1#7#2#CALLER*CALLEE##`), and pager broadcast (`*8*1#14#2#CALLER*4##`).
6. **Call Setup, Sessions & Teleloop**: Call answering (`*8*2#..##`), call termination with `END_ALL_CALLS = 4` (`*8*3#KIND#MMTYPE*4WHERE##`), stop video reproduction (`*8*3#KIND#3*WHERE##`), VCT process initialization (`*8*37#MODE*WHERE##`), teleloop control (`*8*76##` through `*8*79##`), and multimedia amplifier silencing (`*8*63##` / `*8*64##`).
7. **Status & Diagnostic Queries**: Actuator and station status inspection (`*#8*WHERE##`).

---

## 2. Evidence and Suite Provenance

- **Suite File**: `oracle/cases/intercom-ts10.cases`
- **Suite SHA-256**: `57339b7f4a32bc2548a4e72b8a74f162fd2c647c69f95f1c3c4a665f6dcfc119`
- **Rows**: 59
- **Harness**: `full` (QEMU ARM user-mode, bubblewrap sandbox, PTY SCS bus with PIC responder)

### Gateway Run Breakdown

| Gateway | Version | Image SHA-256 | ACK (out) | No Reply (out) | NACK (out) | NACK (silent) | Total Rows |
|---|---|---|---|---|---|---|---|
| **MyHomeServer1** | `028206` | `aa1cd393...` | 0 | 0 | 0 | 59 | 59 |
| **MH202** | `010024` | `bb561eb2...` | 0 | 0 | 0 | 59 | 59 |
| **MH200N** | `010108` | `e32d3404...` | 0 | 0 | 0 | 59 | 59 |
| **F454** | `020051` | `15f80bc6...` | 0 | 0 | 0 | 59 | 59 |

---

## 3. Key Empirical & Architectural Findings

### 3.1. Subsystem Isolation & Routing Topology
Empirical verification confirms that standard automation gateways do not bridge or process WHO 8 commands:
- **MyHomeServer1 (`028206`)**: Contains translators for lighting (`bt_luci`), thermo (`bt_termo`), sound/media (`bt_multi`), and energy (`bt_supervisione`), but does not bundle any video door entry daemon. Every WHO 8 frame is immediately rejected with `*#*0##` (NACK) by `openserver` without hitting the SCS bus.
- **MH200N (`010108`)**: Acts purely as a scenario programmer and automation gateway; all 59 WHO 8 frames return `nack/silent`.
- **MH202 (`010024`)**: While MH202 bundles `bt_vct`, inspection of `stack_open.xml` proves it only routes `<chi>6.7</chi>` (Door Entry & Multimedia Video) to `bt_vct`, leaving `WHO 8` unrouted.
- **F454 (`020051`)**: Inspection of `stack_open.xml` confirms that F454 is the designated Audio/Video gateway routing `WHO 8`:
  ```xml
  <client_06>
    <name>bt_vct</name>
    <working_mode>0</working_mode>
    <time>4</time>
    <chi>8</chi>
  </client_06>
  ```
  When `bt_vct` is inactive or disconnected, `openserver` returns `*#*0##` for all WHO 8 commands.

### 3.2. Reference Protocol Specifications from `libqtdevices TS10_1_0_23`

Because video door entry operates peer-to-peer across touch screens, entrance panels, and handset units, the definitive OpenWebNet specification is defined by BTicino's reference touch screen client:

#### Door Lock & Staircase Light Actuators
- **Open Lock**: `*8*19*WHERE##` (WHERE = actuator address or entrance panel, e.g. `*8*19*20##`).
- **Release Lock**: `*8*20*WHERE##` (WHERE = actuator address, e.g. `*8*20*20##`).
- **Staircase Light ON**: `*8*21*WHERE##` (WHERE = station or general `0`).
- **Staircase Light OFF**: `*8*22*WHERE##` (WHERE = station or general `0`).

#### Camera Controls & Pan/Tilt/Zoom (PTZ) Movements
- **Camera ON / Auto-Switching**: `*8*4#CALLER*CAMERA##` (e.g. `*8*4#11*20##` turns on camera 20 from station 11).
- **Cycle External Units**: `*8*6#CALLER*MASTER_CALLER##` (cycles video stream to subsequent camera).
- **PTZ Movements**:
  - `MOVE_UP = 59`: Press `*8*59#1*CAMERA##`, Release `*8*59#2*CAMERA##`.
  - `MOVE_DOWN = 60`: Press `*8*60#1*CAMERA##`, Release `*8*60#2*CAMERA##`.
  - `MOVE_LEFT = 61`: Press `*8*61#1*CAMERA##`, Release `*8*61#2*CAMERA##`.
  - `MOVE_RIGHT = 62`: Press `*8*62#1*CAMERA##`, Release `*8*62#2*CAMERA##`.

#### Intercom Calling Architecture
- **Internal Intercom (Same SCS Bus)**: `*8*1#6#2#CALLER*CALLEE##` (Kind `6`, MMType `2` = Audio).
- **External Intercom (Across SCS Interfaces)**: `*8*1#7#2#CALLER*CALLEE##` (Kind `7`, MMType `2` = Audio).
- **Pager Broadcast**: `*8*1#14#2#CALLER*4##` (Kind `14`, MMType `2` = Audio, broadcast address `4`).
- **Pager Answer**: `*8*2#14#2#CALLER*4##`.

#### Session Lifecycle & Termination
- **Answering Call**: `*8*2#KIND#MMTYPE*WHERE##`.
- **Termination**: `*8*3#KIND#MMTYPE*4WHERE##` (where `4` prepended to WHERE signifies `END_ALL_CALLS`).
- **Stop Video Reproduction**: `*8*3#KIND#3*WHERE##` (MMType `3` indicates video stream termination while preserving audio).
- **VCT Process Initialization**: `*8*37#1*WHERE##` (SCS bus mode), `*8*37#2*WHERE##` (IP mode).
- **Multimedia Amplifier Silencing**: `*8*63*WHERE##` (mute local audio diffusion during call), `*8*64*WHERE##` (restore local audio diffusion).
