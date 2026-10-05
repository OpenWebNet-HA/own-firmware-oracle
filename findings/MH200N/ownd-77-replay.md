# Replay of OWNd#77 Audit Fixes on MH200N

## 1. Executive Summary

In OWNd issue #77, @gdluck conducted a firmware-as-oracle audit of BTicino MyHomeServer1 (`028206`) identifying ten protocol and parser defects across Lighting, Thermoregulation, Energy, Scenario routing, and CEN+ subsystems.

This document records the empirical replay of those ten audit scenarios against the **MH200N** gateway firmware (`010108`, image SHA256 `e32d3404302f3619bbbb564ae50554ab81ac6ef818b6e5a2290142a5e2ab5fb3`) under QEMU emulation.

The results establish:
1. **Universal Gateway Behavior (Holds on MH200N)**:
   - **Fix 7 (Lighting Brightness & Dimmer Levels)**: MH200N strictly refuses level write `100` (`*#1*31*#1*100*0##`), while accepting level `101` and parameterized switch-off commands (`*1*0#SPEED*WHERE##`).
   - **Fix 8 (Local Interface Routing for WHO 0 & WHO 14)**: Scenario frames (`WHO 0`) and private bus management (`WHO 14`) with interface addressing (`#4#01`, `#4#02`) correctly route through the respective local bus interface bytes on the SCS bus.
   - **Fix 9 (CEN+ / Dry Contact WHO 25 Differentiation)**: Generic WHO 25 event commands emit distinctive SCS telegrams and are not misclassified as simple contact state toggles.
   - **Fix 10 (Refusal of Specification Violations)**: All eleven invalid or malformed frames identified in the conformance matrix audit are universally NACKed and silent on MH200N.

2. **Architectural Differences Between Gateways**:
   - **Fixes 1–5 (Thermoregulation WHO 4)**: On MyHomeServer1, `bt_termo` translates canonical central unit modes (303, 1, 0, 311, 102, 202) and plain zone fan speeds (0..3). On MH200N, `openserver` only attaches `bt_luci` and `bt_device` as active OpenWebNet clients in default configuration (`stack_open.xml`); consequently, all standalone `WHO 4` commands sent via OpenWebNet are NACKed.
   - **Fix 6 (Energy Dimension Parameters WHO 18)**: Similarly, MH200N's OpenWebNet server does not route `WHO 18` by default, whereas MyHomeServer1 accepts parameterized energy queries (`511#M#D`, `52#Y#M`).

---

## 2. Evidence and Suite Provenance

All test suites were executed under the `full` harness using bubblewrap process sandboxing, PTY-backed simulated SCS bus (`line` framer, `pic` responder), and verified against `tools/check.py`.

| Fix | Suite Name | MH200N TSV Result | Check Result | Status on MH200N |
|---|---|---|---|---|
| Fix 1 | `thermo-central-mode` | `results/MH200N/010108/oracle/full/thermo-central-mode.tsv` | `.../checks/thermo-central-mode.tsv` | Gateway-specific (NACKed by openserver) |
| Fix 2 | `thermo-fan-speed` | `results/MH200N/010108/oracle/full/thermo-fan-speed.tsv` | `.../checks/thermo-fan-speed.tsv` | Gateway-specific (NACKed by openserver) |
| Fix 3 | `thermo-zone-mode` | `results/MH200N/010108/oracle/full/thermo-zone-mode.tsv` | `.../checks/thermo-zone-mode.tsv` | Gateway-specific (NACKed by openserver) |
| Fix 4 | `thermo-actuator` | `results/MH200N/010108/oracle/full/thermo-actuator.tsv` | `.../checks/thermo-actuator.tsv` | Gateway-specific (NACKed by openserver) |
| Fix 5 | `thermo-valve` | `results/MH200N/010108/oracle/full/thermo-valve.tsv` | `.../checks/thermo-valve.tsv` | Gateway-specific (NACKed by openserver) |
| Fix 6 | `energy-params` | `results/MH200N/010108/oracle/full/energy-params.tsv` | `.../checks/energy-params.tsv` | Gateway-specific (NACKed by openserver) |
| Fix 7 | `lights-level` | `results/MH200N/010108/oracle/full/lights-level.tsv` | `.../checks/lights-level.tsv` | **Holds** (Identical to MHS1) |
| Fix 8 | `interface-routing` | `results/MH200N/010108/oracle/full/interface-routing.tsv` | `.../checks/interface-routing.tsv` | **Holds** (Identical to MHS1) |
| Fix 9 | `who25-messages` | `results/MH200N/010108/oracle/full/who25-messages.tsv` | `.../checks/who25-messages.tsv` | **Holds** (Emits to SCS bus) |
| Fix 10 | `matrix-refused` | `results/MH200N/010108/oracle/full/matrix-refused.tsv` | `.../checks/matrix-refused.tsv` | **Holds** (All 11 refused) |

---

## 3. Detailed Findings

### 3.1 Fix 7: Lighting Level 100 Refusal and Speed Switch-Off
- **Stimulus**: `*#1*31*#1*100*0##` vs `*#1*31*#1*101*0##` and `*1*0#SPEED*31##`.
- **MH200N Behavior**:
  - `*#1*31*#1*100*0##` is NACKed (`reply: nack`, `verdict: silent`).
  - `*#1*31*#1*101*0##` is ACKed and emits `$06D13101420D0D0100\r` to the SCS bus.
  - Parameterized switch-off forms (`*1*0#0*31##`, `*1*0#1*31##`, `*1*0#5*31##`, `*1*0#255*31##`) are all ACKed and emit level commands with the corresponding transition speed encoded in the final byte.
- **Conclusion**: Both MH200N and MyHomeServer1 reject level `100` as a brightness command. OWNd's routing of brightness 0 to switch-off and clamping levels to 101..200 is confirmed correct across both product families.

### 3.2 Fix 8: Interface Addressing for Scenarios and WHO 14
- **Stimulus**: Frames addressing interface 01 vs 02 on a local bus (`#4#01` vs `#4#02`).
- **MH200N Behavior**:
  - `*0*1*31#4#01##` emits bus telegram `$06EC01000031001401\r`.
  - `*0*1*31#4#02##` emits bus telegram `$06EC02000031001401\r`.
  - `*14*0*31#4#01##` emits bus telegram `$06EC01000031001100\r`.
  - `*14*0*31#4#02##` emits bus telegram `$06EC02000031001100\r`.
- **Conclusion**: The interface index byte (`01` vs `02`) is directly embedded in the fifth character position of the SCS telegram. OWNd's extraction of the interface property is validated.

### 3.3 Fix 10: Refusal of Conformance Matrix Errata
- **Stimulus**: 11 frames formerly cited in older matrix documentation (`*2*1*21#4#1##`, `*#1*1*#1*20##`, `*#2*1*#1*50##`, `*#18*51*52##`, `*25*21*0001##`, `*15*01*0001##`, etc.).
- **MH200N Behavior**: Every single frame generates `reply: nack` and `verdict: silent`.
- **Conclusion**: None of these legacy forms are accepted by real firmware; their removal from OWNd conformance matrix examples is fully corroborated.

### 3.4 Gateway Specialization: Thermoregulation and Energy Routing
- In MH200N's default runtime architecture (`stack_open.xml`), OpenWebNet routing table registers:
  - `bt_luci` for WHO `0, 1, 2, 14, 15, 25, 1000`
  - `bt_device` for WHO `13, 1001, 1004, 1005, 1008, 1013, 1018, 1022, 1027`
- The heating translator (`bt_termo`) and energy translator (`bt_energia`) exist in the firmware filesystem but are not connected as OpenWebNet client channels to `openserver`.
- As a consequence, standalone OpenWebNet commands targeting WHO 4 or WHO 18 directly to an MH200N receive a NACK.
- This highlights the importance of gateway profiles: client libraries must not assume every gateway translates every WHO subsystem identically.
