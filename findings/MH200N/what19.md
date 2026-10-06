# Lighting WHAT 19 and WHO 1001 Autodiagnostics Co-occurrence

## 1. Summary

In the standard BTicino OpenWebNet documentation for Lighting (`WHO 1`), `WHAT` values are defined for switching and dimming levels (`0` through `10`), timed actions (`11` through `18`), blinking cadences (`20` through `29`), and relative level stepping (`30` and `31`). The value `19` is conspicuously absent from all published specification tables.

Historically, this omission led client integrations (including early versions of OWNd and Home Assistant MyHOME) to treat unknown non-zero status values such as `WHAT 19` as active "ON" states (see OpenWebNet-HA/MyHOME#593, #611).

Analysis of the MH200N firmware (`010108`) under oracle simulation, cross-validated against physical hardware capture `EVID-MH200-WHAT19-FAULT`, resolves this behavior:

1. **`WHAT 19` is an actuator hardware fault / anomaly status**, not an undocumented "ON" or dimming level.
2. In the gateway translator (`bt_luci`), an incoming SCS bus status telegram carrying the anomaly status indicator byte (`'E'`) directly maps to internal status code 19, emitting `*1*19*WHERE##`.
3. An actuator in this condition co-emits an extended SCS diagnostic frame. In the gateway diagnostics subsystem (`bt_device` via `libdiag.so`), this triggers an autodiagnostic dimension frame: `*#1001*WHERE*11*<bitmask>##`.
4. Downstream applications and integrations must treat `WHAT 19` as a fault/anomaly indicator rather than a light turned ON.

---

## 2. Firmware Provenance and Oracle Evidence

The behavior was observed in firmware image `FW_MH200N_vers_010108.zip` (`scheduler_010108.fwz`, SHA256 `e32d3404302f3619bbbb564ae50554ab81ac6ef818b6e5a2290142a5e2ab5fb3`).

Key components identified in `results/MH200N/010108/manifest.tsv`:
- **`home/bticino/bin/bt_luci`** (SHA256 `1ef8de9e64b7a261664554f01a04d3739aa3bb3b4f8846e532e40adbca12179e`): Lighting subsystem translator.
- **`home/bticino/bin/bt_device`** (SHA256 `c54e00cd5dcb67975a741c784896bd429313483adf0f2f54acf77ed3bd3f3621`): Device and diagnostic subsystem manager.
- **`home/bticino/lib/libdiag.so.0.0`** (SHA256 `c96e7f80d3dd618487ce1d705f300eaaa59fd8d15042a521a658a194f26caaf0`): OpenWebNet / SCS diagnostic translation library.
- **`home/bticino/lib/libbase.so.0.0`** (SHA256 `ae22e41704655cf7fabb713214c7882abf7dfab7076e36cdfa00efdf4292ff79`): IPC and bus frame dispatch routines.

### Oracle Test Results
Oracle test suite `oracle/cases/what19.cases` stimulates a status query on WHERE address `74` (`*#1*74##`):

```tsv
# results/MH200N/010108/oracle/full/what19.tsv
direction	input	reply	verdict	output
down	*#1*74##	-	out	bus:24 30 33 37 34 30 30 31 35 30 30 0d | own:*1*19*74## | own:*#1001*74*11*111110111111111111110111##
```

Check run against `results/MH200N/010108/checks/what19.tsv`:
- **Oracle output**: Emits OpenWebNet lighting event `*1*19*74##` and diagnostic event `*#1001*74*11*111110111111111111110111##`.
- **OWNd classifier**: `OWNLightingEvent | OWNEvent`.
- **Live correlation**: `agree EVID-MH200-WHAT19-FAULT`.

---

## 3. Translator Subsystem Mechanics

### 3.1 Bus Architecture and Routing
On the physical gateway:
1. Low-level SCS telegrams are handled by a dedicated PIC microcontroller exposed at `/dev/ttyPIC`.
2. The `scsserver` daemon listens on internal port 20001 and reads framed telegrams from the PIC. Standard SCS frames are relayed as `*02*01*$17...#`, and extended frames as `*02*02*$18...#`.
3. Client translators (`bt_luci`, `bt_device`, etc.) connect to `scsserver` and parse these multiplexed frames using the common communication library (`libbase.so`).

### 3.2 Translation of Status in `bt_luci`
When a status inquiry `*#1*WHERE##` is issued:
- The gateway sends standard SCS write request `$03...` to the bus.
- The actuator returns a standard lighting status frame.
- Inside `bt_luci`, incoming SCS status messages are parsed by the lighting status handler. The handler evaluates the status character token in the payload:
  - Byte `'0'` (`0x30`) -> Maps to `WHAT 1` (Standard ON).
  - Byte `'1'` (`0x31`) -> Maps to `WHAT 0` (Standard OFF).
  - Bytes `'B'` and `'D'` -> Map to dimmer speed and level indications.
  - Byte `'E'` (`0x45`) -> Explicitly assigned to decimal value `19` (`0x13`).
- The message formatter generates the outbound OpenWebNet notification string `*1*19*WHERE##`.

Because byte `'E'` denotes an internal fault / error state in the SCS lighting actuator specification, `WHAT 19` is the gateway's direct representation of that error status.

### 3.3 Autodiagnostic Co-occurrence (`bt_device` / `libdiag.so`)
Simultaneously, a physical actuator encountering an anomaly condition reports its internal diagnostic telemetry via extended SCS telegrams (such as telegram type `D2S8E`):
- `bt_device` receives the extended frame from `scsserver`.
- Diagnostic translation in `libdiag.so` extracts the 24-bit diagnostic bitmask from the extended frame payload.
- The subsystem emits the autodiagnostic dimension frame:
  ```text
  *#1001*WHERE*11*<bitmask>##
  ```
- In capture `EVID-MH200-WHAT19-FAULT`, the bitmask reported is `111110111111111111110111`. When numbered 1-based from the left, bit 6 and bit 21 are cleared (`0`), indicating specific internal error flags on the physical actuator.

---

## 4. Implications for Downstream Consumers

### 4.1 False-Positive "ON" State in Home Assistant
Because standard specifications omitted `WHAT 19`, generic lighting parsers often fell back to assuming any non-zero state meant the light was illuminated:
- When an actuator tripped a breaker, experienced an overload, or had a load fault, it began reporting `WHAT 19`.
- Instead of alerting the user or marking the entity unavailable/faulted, Home Assistant showed the light as turned ON.
- Attempts by the user to turn the light OFF were either ignored or immediately reverted to ON when the actuator replied with `*1*19*WHERE##`.

### 4.2 Recommendations for OWNd and Home Assistant MyHOME
1. **Model `WHAT 19` as a Fault State**:
   - In OWNd: Map `WHAT 19` to an explicit state (e.g., `STATE_FAULT` or `STATE_ANOMALY`) rather than treating it as `STATE_ON`.
   - In `custom_components/myhome`: When `WHAT 19` is received for a light entity, mark the light entity as having an error or issue, or surface a diagnostic sensor / attribute `fault: true`.
2. **Correlate with `WHO 1001` Dimension 11**:
   - Diagnostic frames received on `*#1001*WHERE*11*<bitmask>##` should be linked to the device at `WHERE`.
   - Changes in the fault mask provide granular telemetry about the underlying cause (e.g. overcurrent, thermal protection, or bus voltage anomaly).

---

## 5. Epistemic Status and Verification

| Claim | Status | Evidence |
|---|---|---|
| `WHAT 19` emitted on SCS status byte `'E'` | **Proven** | MH200N 010108 firmware emulation (`results/MH200N/010108/oracle/full/what19.tsv`) |
| Co-occurrence with `WHO 1001` DIM 11 | **Proven** | MH200N 010108 oracle run and physical MH200 capture `EVID-MH200-WHAT19-FAULT` |
| `WHAT 19` represents error/anomaly | **Proven** | Mapping logic in `bt_luci` and correlation with cleared fault bits in DIM 11 |
| Exact meaning of DIM 11 bits 6 and 21 | **Open** | Defined by actuator internal firmware; gateway only translates the bitmask verbatim |
