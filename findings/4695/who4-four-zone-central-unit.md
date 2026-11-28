# WHO 4 Four-Zone Central Unit (`4695` `v01.01.03`) & Gateway Fleet Audit (`MyHOME#994`)

**Epistemic status:** `firmware_observed` (static analysis + M16C/60 P-code verification of `LH4695_AM5875_010103.fwz`, emulated `harness=full` across all 13 active gateways, and closed-loop `harness=plant:cu4695` across 6 gateways). Nothing here is labelled `experimentally_confirmed` beyond cross-referencing the reporter's `F454 2.0.51` captures in [OpenWebNet-HA/MyHOME#994](https://github.com/OpenWebNet-HA/MyHOME/issues/994).

- **Original `4695` M16C/60 P-Code Runner:** [`tools/pcode_4695.py`](../../tools/pcode_4695.py) (`pypcode` `4.1.0`, SLEIGH `M16C/60:LE:16:default`, `4` suites / `79` steps). Executes the unmodified 128 KB `flat_0E0000_0FFFFF.bin` image from hardware reset with deterministic peripheral stubs (32 KB I2C EEPROM, UART0 PIC RX/TX queue, fast-return non-protocol hardware wait hooks, periodic 10-bit `AN0` ADC load-check input) and the architectural 20-bit `LDE.B`/`LDE.W disp20[A0]` SLEIGH adjustment:
  ```bash
  python tools/pcode_4695.py \
    --fwz LH4695_AM5875_010103.fwz \
    --mot 4695_010103.mot \
    --flash-bin flat_0E0000_0FFFFF.bin \
    --out who4-4695-pcode-execution.json
  ```
- **Stateless Fleet Suite:** [`oracle/cases/thermo-four-zone-central.cases`](../../oracle/cases/thermo-four-zone-central.cases) (`83` steps: `63` `down` + `20` `up`, `suite_sha256=cb6fc697aa434a0b048f8cee1cfad6d4c58449a849847acfb1d47bf11d44d5a9`) -> `results/<Product>/<Version>/oracle/full/thermo-four-zone-central.tsv` (13 gateways).
- **Closed-Loop Virtual Plant Fixture & Suite:**
  - Fixture: [`oracle/plant/fixtures/cu4695.yaml`](../../oracle/plant/fixtures/cu4695.yaml) (`plant_sha256=9450834f28e11fc6a0703f8d0d44c691e7fff9b3d8931d87fbad2caa433e3c0f`)
  - Suite: [`oracle/plant_cases/plant-cu4695.seq`](../../oracle/plant_cases/plant-cu4695.seq) (`38` steps, `suite_sha256=92fc6fb2bac6a406ab2a07402d407b1e4b992c88237423e6a2ca8323ca5db144`) -> `results/<Product>/<Version>/oracle/plant-cu4695/plant-cu4695.tsv` (`F454/020051`, `MyHomeServer1/028206`, `MH200N/010108`, `MH200/020102`, `H4684/020054`, `F461/020011`).

---

## 1. `4695` (`HC/HS/L/N/NT4695`, `AM5875`) Firmware Provenance & ROM Facts

| Artifact | Size (bytes) | SHA-256 |
|---|---:|---|
| `LH4695_AM5875_010103.fwz` (`ThermorCentralUnit v01.01.03`, ZipCrypto) | 109,864 | `b01630d5f4d4fb45ee3392279635cde4e3575778fe69afae505d71a7c211a3be` |
| `info.txt` (package metadata inside `.fwz`) | 188 | `6d71fe6e03f38ceaafc3f28e41a2fce5be774ed34c78413d518d8318a931c1b3` |
| `4695_010103.mot` (Motorola `1` `S0` + `7,638` `S2` + `1` `S8`, `0x0E0000..0x0FFFFF`) | 350,878 | `3a760549e6c9b376308e29a1a6b46f5bb8f57a1b6f2715fff2f063324588ed01` |
| Decoded Renesas M16C/60 Flash (`0x0E0000..0x0FFFFF`, `0xFF`-filled) | 131,072 | `254e3daed3534a4bcbf31a42534f75c3a8148e4e65fdcbcd0ea73aa4c16c91e9` |

### Verified `4695 v01.01.03` Protocol Facts (`firmware_observed`)

1. **Base Address `0x14B9 = ZA * 10 + ZB` (`1..99`, `#0#1..#0#99`; `1..96` when all four zones `N..N+3` lie in `1..99`)**:
   - Computed at `0x0E9532` from configurators `ZA` and `ZB` (`1..99`, managing 4 consecutive zones `N .. N+3`; e.g. `ZA=2, ZB=1` -> `0x15 = 21` `#0#21` managing zones `21..24`, matching BTicino Technical Guide `U1809C` pp. 54–55).
   - At `0x0EA185..0x0EA19B`, incoming extended frames with `B1 == 0x00` (`#0`, `#1`, `#3`) or `B1 != 0x14B9` are unconditionally dropped.
2. **Built-In Master Zone `N` Drops Direct `90/91/92/93` Writes (`0x0E987C..0x0E989C`)**:
   - The `4695` contains the Zone `N` temperature probe internally (answering `99 <N> 30 <00|12|0E|0D|13>` polls at `0x0E9C7F..0x0EA180`), but its short-frame RX dispatcher (`0x0E987C`) handles only `0x99` (read) and `0x95` on `B1 == 0x14B9`.
   - Direct zone setpoint (`90/91/92 <N> 30 <T*2>`) and zone mode (`93 <N> 30 <cmd>`) writes to Zone `N` (`Z=1` on `#0#1`) are **silently dropped**, and no external slave probe exists at `Z=N`. External slave probes (`N+1..N+3`, e.g. `Z=3, 4`) DO accept direct `90/91/92/93` writes on gateways that forward them. On `#0#N` writes while built-in Zone `N` follows central (`0x1557 == 0x0E`), original `4695` `01.01.03` emits Zone `N` `DIMENSION 12` (`$06 D2 <N> 03 04 12 ...`) when central operating mode or Zone `N` effective setpoint changes (`dim14-10`) and omits it when repeating an identical mode and setpoint (`dim14-11`) or when Zone `N` is in local override (whereas the simplified `plant:cu4695` Python model omits immediate Zone `N` `DIMENSION 12` unconditionally).
3. **`C2` Controlled-Zone Subcode (`#Z` / `#Z#N` / `*4*311*#0#N##`) Has NO Write Handler**:
   - Under `B3 = 0x02` (`0x0EA1D2..0x0EA222`), `4695` handles `B4 = 0xC1` and `0xBA` — **`B4 = 0xC2` is absent**.
   - Both `*#4*#3*#14*0180*3##` (`D1 00 03 02 C2 03 02 24`) and BTouch `*#4*#3#1*#14*0175*3##` (`D1 01 03 02 C2 03 02 23`, `#994` trace 2 frame 37), as well as `*4*311*#0#1##` (`D1 01 03 02 C2 00 00 00`), are dropped by `4695`. `C2` is implemented **read-only** under `B3 = 0x03` (`0x0EACC6` -> `0x0EBD1A`, `*#4*#3#1##`).
4. **`C1` Season Filter (`0x0EA31F..0x0EA347`) — Generic `M=3` Works in Both Seasons**:
   - `B5` high nibble `0x10` (`M=1` `*#4*#0#N*#14*TTTT*1##` -> `C1 12`, `1101..1103`, `103`, `102`) is accepted **only** in Heating (`0x17B5 == 0`) and dropped in Cooling (`0x17B5 == 1`).
   - `B5` high nibble `0x20` (`M=2` -> `C1 22`, `2101..2103`, `203`, `202`) is accepted **only** in Cooling (`0x17B5 == 1`) and dropped in Heating.
   - `B5` high nibble `0x00` (`M=3` `*#4*#0#N*#14*TTTT*3##` -> `C1 02`, `3101..3103`, `3100`, `303`, `302`) bypasses the season filter and succeeds in **both** Heating and Cooling.
5. **`*4*3100*#0#N##` (`C1 01 FF`) vs `*4*1/0*#0#N##` (`C1 F0/F1` at `0x0F4ED0`)**:
   - All 13 emulated gateways encode `*4*3100*#0#1##` as `D1 01 03 02 C1 01 FF 00` (`B5=0x01, B6=0xFF`, `CU=00` on Gen A), which hits `0x0EA383` and **resumes active weekly program `0x17B9` (`0..2` = Prog `1..3`) in both seasons**.
   - Conversely, `*4*1*#0#1##` (`C1 F0`) and `*4*0*#0#1##` (`C1 F1`) call `0x0F4ED0`:
     - If `requested_season == 0x17B5` (`0x0F4ED6`), `4695` exits immediately with **`TX=[]`**, leaving `0x17B4 = 6` (`OFF`) untouched!
     - If `requested_season != 0x17B5` (`0x0F4EEE`), `4695` switches season into **Protection (`C0 17`/`C0 27` -> `*4*102/202*#0#N##`)**, not weekly program or manual.
   - Scenarios `1201..1203` / `3200` (`B5 = 0x13/0x03`) are dropped (`0x03` absent from `0x0EA34A`); `4695` implements daily Holiday mode with weekly return program `1..3` (`B6 = 0x00..0x02`) under `B5 = 0x04/0x14/0x24` (`*4*115#1101..315#3103*#0#N##`, per `U1809F_U_EN.pdf` p. 15) and multi-day vacation entry/exit under `B5 = 0x05/0x15/0x25` (`*4*13DDD#P..33DDD#P*#0#N##`, `*4*3000*#0#N##`, `*4*3000#P*#0#N##`). Manual `DIMENSION 14` setpoints (`B5 = 0x02/0x12/0x22`) accept `5.0..35.0 °C` (`B6 = 0x0A..0x46`) and drop `35.5..40.0 °C` (`0x47..0x50`).
6. **`C0` Diagnostic Flags (`0x0EB8FB..0x0EB965`) & Real ADC Backup-Battery Fault (`WHAT 31`)**:
   - `D2 <CU> 03 04 C0 <op> <arg> <flags>` encodes: bit 0 (`0`=`*4*20*` remote disabled, `1`=`*4*21*` remote enabled), bit 1 (`0x02`=`*4*30*` probe failure), bit 2 (`0x04`=`*4*31*` backup battery fault from 10-bit `AD0` `0x03C0` load check at `0x0EC9E9..0x0ECBFD`), bit 3 (`0x08`=`*4*22*` >=1 zone in Local OFF), bit 4 (`0x10`=`*4*23*` Local Protection), bit 5 (`0x20`=`*4*24*` Local Manual).
7. **Actuator `DIMENSION 20` (`WHERE = #0#1` Spec Collision vs Gateway Decoding)**:
   - `4695`'s own pump/actuator bus frames (`BF <CU> 30 00/01`, `BA 00/<CU> 30 00/01`) are `silent` on all gateways.
   - Although the OpenWebNet WHO=4 PDF specification (`ownkb:claim:c003280`) writes Master Pump Actuator 1 as `*#4*#0#1*20*V##` (creating a grammar collision with 4-zone central unit `#0#1`), actual Gen B/C/D gateway translators (`bt_termo` / `coso`) decode `B7 80 31 01` (`0x80 | 0`, `N=1`) as `*#4*0#1*20*0##`, `B7 00 31 01` (`0x00`, `N=1`) as `*#4*100*20*0##`, and `B7 00 30 01` (`0x00`, `N=0`) as `*#4*#0*20*0##`, while outbound `*#4*#0#1*20##` on Gen C/D (`F454`, `F459`, `F450`, `MyHomeServer1`, `F461`, `F460`, `3488`; `MH202` `010024` returns `nack, silent` with no bus output) sends `A9 01 30 10` (querying Zone 1 actuator `1#1` -> `*#4*1#1*20*0##`).

---

## 2. Four-Generation Gateway WHO=4 Matrix (All 13 Emulated Gateways)

| Generation | Emulated Gateways | `#0#C` (`#0#1..#0#21`) Outbound & Inbound | Direct Zone `Z` Writes (`*#4*Z*#14*T*M##`, `*4*303*Z##`) | `*#4*#0#1*14##` | Empty-Bus Reply Semantics |
|---|---|---|---|---|---|
| **Gen A** | `MH200 020102`, `H4684 020054` | **Collapses `#0#C` & `#Z#C` -> `00`** outbound (`D1 00...`, `99 00 30 C0`) and **strips `#C` -> `#0` / `#Z`** inbound; `NACK, silent` on `#0#1` `1`/`0`/`103`/`203`/`311` | **`nack, silent`** (all direct `Z` writes refused) | `nack, silent` | `pic_ack` |
| **Gen B** | `MH200N 010108`, `F453AV 030014`, `F453 010100` | Preserves `#0#C` (`01..15`) in both directions | **`nack, silent` on `*#4*Z*#14*T*M##`, `*4*303*Z##`, `*4*102/202*Z##`**; rewrites `*4*1/0*1##` -> `D1 01 03 02 C1 F0/F1 00 00` (`#0#1`!) | `nack, silent` (`F453` also emits `D2` instead of `D1` on `*#4*#Z##`) | `pic_ack` |
| **Gen C** | `F454 020051`, `F459 020105`, `F450 020010`, `MH202 010024` | Preserves `#0#C` (`01..15`) in both directions | **Translates** (`90/91/92 <Z> 30 <T*2>`, `93 <Z> 30 0F/02/03`) | `nack, silent` | `pic_ack` |
| **Gen D** | `MyHomeServer1 028206`, `F461 020011`, `F460 020012`, `3488 030205` | Preserves `#0#C` (`01..15`) in both directions (`$004`/`$006` on `F460`/`F461`, `$104`/`$106` on `3488`) | **Translates** (`90/91/92 <Z> 30 <T*2>`, `93 <Z> 30 0F/02/03`) | Forwards **`99 01 30 0E`** (Zone 1 `DIM 14`) | `empty_bus_txack` |

Note: Weekly program commands `*4*1101..1103*#0#1##`, `*4*2101..2103*#0#1##`, and `*4*3101..3103*#0#1##` are accepted and translated onto the SCS bus by **all 13/13 gateways** (`D1 01 03 02 C1 11/21/01 00..02 00` on Gen B/C/D; `D1 00...` on Gen A), whereas `*4*110#0180*#0#1##` / `210#` / `310#` are `nack, silent` across all 13/13 gateways (event-only syntax).

---

## 3. Selected Closed-Loop Highlights (`plant-cu4695.tsv`)

| Step | Input (`down`) | `F454/020051` | `MyHomeServer1/028206` & `F461/020011` | `MH200N/010108` | `MH200/020102` & `H4684/020054` |
|---:|---|---|---|---|---|
| 1 | `*#4*#0#1##` | `ack` -> `cmd:*4*1103*#0#1## \| *4*20*#0#1## \| *4*31*#0#1##` | `ack` -> `cmd`+`evt` `1103`, `20`, `31` | `ack` -> `cmd`+`evt` `1103`, `20`, `31` | **`nack`** (`99 00 30 C0` dropped) |
| 15 | `*4*0*#0#1##` *(Heat -> Cool)* | `ack` (`C0 27` + Z3,Z4 `0xF3`) | `ack` -> **`evt:*4*202*#0#1##`** + Z3,Z4 `202` | `ack` -> **`evt:*4*202*#0#1##`** + Z3,Z4 `202` | **`nack, silent`** |
| 16 | `*#4*#0#1*#14*0200*1##` *(Cooling `M=1`)* | `ack` *(dropped by 4695!)* | **`nack`** *(dropped by 4695!)* | `ack` *(dropped by 4695!)* | `ack` *(dropped)* |
| 17 | `*#4*#0#1*#14*0200*3##` *(Cooling `M=3`)* | `ack` (`C0 22 28` + Z3,Z4 `0200`) | **`ack`** -> `evt:*4*210#0200*#0#1##` + Z3,Z4 `0200` | **`ack`** -> `evt:*4*210#0200*#0#1##` + Z3,Z4 `0200` | `ack` *(dropped)* |
| 20 | `*#4*#3#1*#14*0175*3##` | `ack` *(`C2` dropped by 4695!)* | **`nack`** *(`C2` dropped by 4695!)* | `ack` *(`C2` dropped by 4695!)* | `ack` *(dropped)* |
| 21 | `*#4*1*#14*0180*3##` *(Z=1 direct)* | `ack` *(`92 01` dropped by 4695!)* | `ack` *(`92 01` dropped by 4695!)* | **`nack, silent`** | **`nack, silent`** |
| 23 | `*#4*3*#14*0180*3##` *(Z=3 direct)* | `ack` -> Z3 `0180` | `ack` -> `evt:*#4*3*14*0180*3##` | **`nack, silent` (Gen B!)** | **`nack, silent`** |
| 25 | `*#4*#0#1##` *(after `*4*303*3##`)* | `ack` -> `cmd` includes **`*4*22*#0#1##`** | `ack` -> `cmd`+`evt` includes **`*4*22*#0#1##`** | `ack` *(Z=3 `303` was NACKed)* | `nack` |
| 31 | `*#4*#0*20##` | `ack` -> `cmd:*#4*0#1*20*0## \| 1#1 \| 3#1 \| 4#1` | `ack` -> `cmd`+`evt` `0#1*20*0` + `1#1`, `3#1`, `4#1` | `ack` -> `cmd:*#4*0#1*20*0## \| evt:*#4*0#1*20*0##` + `1#1`, `3#1`, `4#1` | `nack, silent` |
| 32 | `*#4*0#1*20##` | `nack, silent` *(Gen C drops `0#N`)* | `ack` -> `cmd`+`evt` `*#4*0#1*20*0##` | `ack` -> `cmd`+`evt` `*#4*0#1*20*0##` | `nack, silent` |
| 36 | `*4*1*#0#1##` *(same season while `OFF`)* | `ack` *(`TX=[]`, stays `103` OFF!)* | **`nack`** *(`TX=[]`, stays `103` OFF!)* | `ack` *(`TX=[]`, stays `103` OFF!)* | `nack, silent` |
| 37 | `*#4*#0#1##` *(poll after `*4*1*#0#1##`)* | `ack` -> **`cmd:*4*103*#0#1##` (still OFF!)** | `ack` -> **`cmd`+`evt` `*4*103*#0#1##` (still OFF!)** | `ack` -> **`cmd`+`evt` `*4*103*#0#1##` (still OFF!)** | `nack` |
| 38 | `*4*3100*#0#1##` *(restore)* | `ack` (`C1 01 FF` -> `C0 11 02 04`) | **`ack`** -> **`evt:*4*1103*#0#1##`** *(wakes from OFF!)* | **`ack`** -> **`evt:*4*1103*#0#1##`** *(wakes from OFF!)* | `ack` *(dropped)* |
