# Verification of BTicino 10" Touch Screen (TS10) Energy & Load Management on MyHomeServer1 & MH202

## 1. Executive Summary

This document records the empirical execution of the `energy-ts10` suite across the BTicino gateway fleet:
- **MyHomeServer1** (`028206`, image SHA-256 `aa1cd393e4ae4262b3b2c4f9f25b8e831adbed648975505e2084c801f2b7f5e7`)
- **MH202** (`010024`, image SHA-256 `bb561eb2b6b476de21cc4981234108b0698c2a58b1e229f2909ef4e49aef9df7`)
- **MH200N** (`010108`, unpacked image SHA-256 `e32d3404302f3619bbbb564ae50554ab81ac6ef818b6e5a2290142a5e2ab5fb3`)

The suite encompasses 65 test cases derived directly from the reference BTicino Touch Screen 10" client implementation (`libqtdevices` tag `TS10_1_0_23`) and the OpenWebNet protocol specifications:
1. **WHO 18 (Energy Management)**: Instantaneous power, cumulative totalizers, historical graph requests (8-bit, 16-bit, and 32-bit), automatic reporting control (`DIMENSION 1200`), and energy actuator forcing/reset.
2. **Stop&Go Breakers (F16EB)**: Breaker open/close, differential test triggers (positive/negative), automatic reset enabling/disabling, self-test frequency configuration, and status mask inspection.
3. **WHO 3 (Load Management / F421)**: Central unit multi-dimension measurements (`WHERE=10`) and priority load reconnect commands (`#1..#8`).

---

## 2. Evidence and Suite Provenance

- **Suite File**: `oracle/cases/energy-ts10.cases`
- **Suite SHA-256**: `ae576deb4f97c6af81e22c221f24dadb99f4813610e99e19ed96fc1d137896ad`
- **Rows**: 65
- **Harness**: `full` (QEMU ARM user-mode, bubblewrap sandbox, PTY SCS bus with PIC responder)

### Gateway Run Breakdown

| Gateway | Version | Image SHA-256 | ACK (out) | No Reply (out) | NACK (out) | NACK (silent) | Total Rows |
|---|---|---|---|---|---|---|---|
| **MyHomeServer1** | `028206` | `aa1cd393...` | 12 | 12 | 25 | 16 | 65 |
| **MH202** | `010024` | `bb561eb2...` | 40 | 18 | 0 | 7 | 65 |
| **MH200N** | `010108` | `e32d3404...` | 0 | 0 | 0 | 65 | 65 |

> [!NOTE]
> On MyHomeServer1, 25 commands generate `reply=nack` with `verdict=out`. The daemon (`bt_supervisione`) translates and broadcasts the SCS telegrams to the physical bus, but returns `*#*0##` to the OpenWebNet command connection. On MH202, these exact same commands are accepted with `*#*1##` (`reply=ack`), and the emitted SCS telegrams are 100% byte-identical across both gateways.

---

## 3. Key Empirical Discoveries

### 3.1. TS10 Historical Series vs. Classic OpenWebNet Dimensions
In `libqtdevices TS10_1_0_23` (`energy_device.cpp`), graph requests use WHAT commands (e.g. `*18*57#M#D*WHERE##`), whereas older documentation cited dimension queries (e.g. `*#18*WHERE*511#M#D##`).

Empirical verification proves that both syntaxes translate to the **exact same SCS telegrams**:

| TS10 WHAT Command | Classic Dimension Frame | Emitted SCS Telegram (Hex) | Decoded Parameters |
|---|---|---|---|
| `*18*57#10#1*51##` | `*#18*51*511#10#1##` | `$06D1A10233501A0A01\r` | Month 0x0A (10), Day 0x01 (1) |
| `*18*58#10*51##` | `*#18*51*512#10##` | `$06D1A10233501B0A00\r` | Month 0x0A (10) |
| `*18*59#10*51##` | `*#18*51*513#10##` | `$06D1A10233501C0A00\r` | Month 0x0A (10) |

Both gateways encode date/month parameters in hexadecimal directly into bytes 7 and 8 of the extended SCS frame.

### 3.2. Automatic Reporting (DIMENSION 1200)
`*#18*WHERE*#1200#Type*Time##` controls automated periodic push of energy telegrams:
- **Stop Reporting (`Time=0`)**: `*#18*51*#1200#1*0##` emits `$06D1A1023200021D00\r` (last byte `0x00`).
- **Start Reporting (`Time=255`)**: `*#18*51*#1200#1*255##` emits `$06D1A1023200021DFF\r` (last byte `0xFF`).

### 3.3. Actuator Control & Totalizer Reset
Actuators configured in energy management (`WHERE=71#0`):
- **Timed Force Off (15 min)**: `*18*73#15*71#0##` emits `$06D1B1123270020F00\r` (byte 7 is `0x0F` = 15).
- **Indefinite Force Off**: `*18*73*71#0##` emits `$06D1B112327002FF00\r` (byte 7 is `0xFF` = permanent).
- **End Force Off**: `*18*74*71#0##` emits `$06D1B1123270020000\r` (byte 7 is `0x00` = cancel).
- **Reset Totalizer 1**: `*18*75#1*71#0##` emits `$06D1B1123270040100\r`.
- **Reset Totalizer 2**: `*18*75#2*71#0##` emits `$06D1B1123270040200\r`.

### 3.4. Stop&Go Automatic Reset Breakers (F16EB)
Addresses formatted as `1N` (e.g. `11` for line 1):
- **Open Breaker**: `*18*21*11##` emits `$06D101023210010200\r`.
- **Close Breaker**: `*18*22*11##` emits `$06D101023210010100\r`.
- **Differential Test (Positive)**: `*18*23*11##` emits `$06D101023210010601\r`.
- **Differential Test (Negative)**: `*18*24*11##` emits `$06D101023210010600\r`.
- **Auto-Reset Activate**: `*18*26*11##` emits `$06D101023210010401\r`.
- **Auto-Reset Deactivate**: `*18*27*11##` emits `$06D101023210010400\r`.
- **Tracking Activate**: `*18*28*11##` emits `$06D101023210010501\r`.
- **Tracking Deactivate**: `*18*29*11##` emits `$06D101023210010500\r`.
- **Write Self-Test Frequency (10 days)**: `*#18*11*#212*10##` emits `$06D1010232101C000A\r` (`0x0A` = 10).
- **Read Self-Test Frequency**: `*#18*11*212##` emits `$06D1010233101C0000\r`.
- **Read Status Mask (Dim 250)**: `*#18*11*250##` emits `$06D101023310100000\r`.

### 3.5. WHO 3 (Load Management / F421)
WHO 3 is handled by `bt_energia` (available on MH202, omitted on MyHomeServer1):
- `*#3*#1##` / `*#3*#2##` (priority line status): Emits `$03AA001614\r`.
- `*#3*10*0##` (all central unit 10 measurements): Emits 4 successive bus frames (`...1613`, `...1612`, `...1610`, `...1611`).
- `*#3*10*1##` (instantaneous power): Emits `$03AA001613\r`.
- `*#3*10*2##` (voltage): Emits `$03AA001612\r`.
- `*#3*10*3##` (current): Emits 2 frames (`$03AA001610\r` and `$03AA001611\r`).
- `*#3*10*4##` (power factor): Emits 4 frames (`$03AA001606\r` through `...1609\r`).
- `*3*2*#1##` (force priority 1 load): Emits `$04B7011300\r`.
- `*3*2*#2##` (force priority 2 load): Emits `$04B7021300\r`.

---

## 4. Cross-Gateway Comparison Table

| Category | Input Frame | MyHomeServer1 Reply / Bus | MH202 Reply / Bus | MH200N Reply |
|---|---|---|---|---|
| **Stop&Go Frequency Write** | `*#18*11*#212*10##` | `nack` / `$06D1010232101C000A\r` | `ack` / `$06D1010232101C000A\r` | `nack` (silent) |
| **Stop&Go Frequency Read** | `*#18*11*212##` | `ack` / `$06D1010233101C0000\r` | `ack` / `$06D1010233101C0000\r` | `nack` (silent) |
| **Stop&Go Status Mask** | `*#18*11*250##` | `ack` / `$06D101023310100000\r` | `ack` / `$06D101023310100000\r` | `nack` (silent) |
| **Stop&Go Open** | `*18*21*11##` | `nack` / `$06D101023210010200\r` | `ack` / `$06D101023210010200\r` | `nack` (silent) |
| **Stop&Go Close** | `*18*22*11##` | `nack` / `$06D101023210010100\r` | `ack` / `$06D101023210010100\r` | `nack` (silent) |
| **Stop&Go Diff Test (+)** | `*18*23*11##` | `nack` / `$06D101023210010601\r` | `ack` / `$06D101023210010601\r` | `nack` (silent) |
| **Stop&Go Diff Test (-)** | `*18*24*11##` | `nack` / `$06D101023210010600\r` | `ack` / `$06D101023210010600\r` | `nack` (silent) |
| **Stop&Go AutoReset On** | `*18*26*11##` | `nack` / `$06D101023210010401\r` | `ack` / `$06D101023210010401\r` | `nack` (silent) |
| **Stop&Go AutoReset Off** | `*18*27*11##` | `nack` / `$06D101023210010400\r` | `ack` / `$06D101023210010400\r` | `nack` (silent) |
| **Stop&Go Tracking On** | `*18*28*11##` | `nack` / `$06D101023210010501\r` | `ack` / `$06D101023210010501\r` | `nack` (silent) |
| **Stop&Go Tracking Off** | `*18*29*11##` | `nack` / `$06D101023210010500\r` | `ack` / `$06D101023210010500\r` | `nack` (silent) |
| **Stop Auto Report** | `*#18*51*#1200#1*0##` | `nack` / `$06D1A1023200021D00\r` | `ack` / `$06D1A1023200021D00\r` | `nack` (silent) |
| **Start Auto Report** | `*#18*51*#1200#1*255##`| `nack` / `$06D1A1023200021DFF\r` | `ack` / `$06D1A1023200021DFF\r` | `nack` (silent) |
| **Write Threshold** | `*#18*51*#517#1*1234##`| `nack` / `$06D1A10232500604D2\r` | `ack` / `$06D1A10232500604D2\r` | `nack` (silent) |
| **Instantaneous Power** | `*#18*51*51##` | `ack` / `$06D1A1023350150000\r` | `ack` / `$06D1A1023350150000\r` | `nack` (silent) |
| **Hourly Series (Dim 511)**| `*#18*51*511#10#1##` | `-` / `$06D1A10233501A0A01\r` | `-` / `$06D1A10233501A0A01\r` | `nack` (silent) |
| **Hourly Series (TS10 57)**| `*18*57#10#1*51##` | `nack` / `$06D1A10233501A0A01\r` | `ack` / `$06D1A10233501A0A01\r` | `nack` (silent) |
| **Daily Series (Dim 512)** | `*#18*51*512#10##` | `-` / `$06D1A10233501B0A00\r` | `-` / `$06D1A10233501B0A00\r` | `nack` (silent) |
| **Daily Series (TS10 58)** | `*18*58#10*51##` | `nack` / `$06D1A10233501B0A00\r` | `ack` / `$06D1A10233501B0A00\r` | `nack` (silent) |
| **Monthly Series (Dim 513)**| `*#18*51*513#10##` | `-` / `$06D1A10233501C0A00\r` | `-` / `$06D1A10233501C0A00\r` | `nack` (silent) |
| **Monthly Series (TS10 59)**| `*18*59#10*51##` | `nack` / `$06D1A10233501C0A00\r` | `ack` / `$06D1A10233501C0A00\r` | `nack` (silent) |
| **Actuator Timed Force** | `*18*73#15*71#0##` | `nack` / `$06D1B1123270020F00\r` | `ack` / `$06D1B1123270020F00\r` | `nack` (silent) |
| **Actuator Infinite Force**| `*18*73*71#0##` | `nack` / `$06D1B112327002FF00\r` | `ack` / `$06D1B112327002FF00\r` | `nack` (silent) |
| **Actuator Cancel Force** | `*18*74*71#0##` | `nack` / `$06D1B1123270020000\r` | `ack` / `$06D1B1123270020000\r` | `nack` (silent) |
| **Actuator Reset Total 1** | `*18*75#1*71#0##` | `nack` / `$06D1B1123270040100\r` | `ack` / `$06D1B1123270040100\r` | `nack` (silent) |
| **Actuator Reset Total 2** | `*18*75#2*71#0##` | `nack` / `$06D1B1123270040200\r` | `ack` / `$06D1B1123270040200\r` | `nack` (silent) |
| **WHO 3 CU Power** | `*#3*10*1##` | `nack` (silent) | `-` / `$03AA001613\r` | `nack` (silent) |
| **WHO 3 CU Voltage** | `*#3*10*2##` | `nack` (silent) | `-` / `$03AA001612\r` | `nack` (silent) |
| **WHO 3 CU Current** | `*#3*10*3##` | `nack` (silent) | `-` / `$03AA001610\r` + `...1611\r` | `nack` (silent) |
| **WHO 3 CU Power Factor** | `*#3*10*4##` | `nack` (silent) | `-` / 4 frames (`...1606`..`1609`) | `nack` (silent) |
| **WHO 3 Force Load 1** | `*3*2*#1##` | `nack` (silent) | `ack` / `$04B7011300\r` | `nack` (silent) |
| **WHO 3 Force Load 2** | `*3*2*#2##` | `nack` (silent) | `ack` / `$04B7021300\r` | `nack` (silent) |

---

## 5. Architectural Conclusions

1. **Protocol Parity**: Across all 49 WHO 18 test cases where bus output is triggered, MyHomeServer1 and MH202 exhibit **100% byte-identical SCS output**.
2. **Gateway Response Nuance**: MyHomeServer1 returns NACK (`*#*0##`) for commands that MH202 ACKs (`*#*1##`), despite executing the identical bus translation. OWNd client code communicating with MyHomeServer1 must not treat NACK on these specific WHO 18 commands as execution failure if SCS event monitoring confirms bus activity.
3. **WHO 3 Subsystem Architecture**: Load shedding and central unit F421 diagnostics reside strictly in the dedicated `bt_energia` translator. Firmware packages lacking `bt_energia` (such as standard MyHomeServer1 builds) reject WHO 3 requests outright (`nack` / `silent`).
