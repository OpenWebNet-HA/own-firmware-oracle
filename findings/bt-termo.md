# WHO 4 Thermoregulation (`bt_termo` & `MH202` `libplug_thermo_tr.so`) — Complete Protocol & Generation Reference

**Epistemic status:** `firmware_observed` (static binary analysis of all 13 emulated gateway images in `own-firmware-oracle`, cross-checked against all 11 committed WHO 4 `harness=full` oracle suites across all 13 targets: `ownd-pr82-heating`, `thermo-actuator`, `thermo-central-mode`, `thermo-dim22`, `thermo-fan-speed`, `thermo-four-zone-central`, `thermo-holiday-program`, `thermo-ts10`, `thermo-valve`, `thermo-zone-mode`, `thermo-zone-requests`). Where a behavior is additionally corroborated by live physical captures (e.g. `F461` `2.0.11`, `F453` `2.0.10`, `MH200N`, `F453AV`, `MyHomeServer1`, `H4684`), it is explicitly tagged. No disassembly or decompiled code is included; all facts are clean-room behavioral specifications derived from binary inspection and oracle outputs.

> **Binary naming note:** No BTicino gateway firmware contains a binary named `bt_climate`. The WHO 4 (Thermoregulation / Climate) OpenWebNet-to-SCS translator is **`/home/bticino/bin/bt_termo`** on 12 of the 13 emulated gateways, and **`/home/bticino/bin/coso`** loading **`/home/bticino/libcoso/libplug_thermo_tr.so`** + **`/home/bticino/lib/libthermo.so`** on `MH202/010024`.

---

## Quick-Lookup Index (For AI & Tooling Queries)

Use the stable IDs and section anchors below when querying this document:

- **Catalog YAML tag:** Every `catalog/<product>/<version>.yaml` carries a `thermo:` mapping (`generation`, `translator`, `binary_sha256`, `findings`, `notes`).
- **Generation codes (`thermo.generation`):**
  - `"A"` — `H4684/020054`, `MH200/020102`
  - `"B"` — `F453/010100`, `F453AV/030014`, `MH200N/010108`
  - `"C"` — `F450/020010`, `F454/020051`, `F459/020105` (`F459` adds `DIM 60`)
  - `"C-coso"` — `MH202/010024` (independent GLib rewrite)
  - `"D"` — `MyHomeServer1/028206`, `3488/030205`, `F460/020012`, `F461/020011`
  - `"bare-metal"` — `F455/010102`, `MH201/030644`, `L4686SDK/012001` (not Linux `bt_termo`; not emulated)
  - `"central-unit"` — `3550/030020` (SCS central unit MCU, not a gateway)
  - `"none"` — `L4561N/040006` (audio/IR interface, no WHO 4)
- **Section map:**
  - [§1. Provenance & 13-Gateway Generation Matrix](#1-provenance--13-gateway-generation-matrix)
  - [§2. `WHERE` Addressing Grammar (`OWN <-> SCS`)](#2-where-addressing-grammar-own---scs)
  - [§3. `OWN -> SCS` Complete Specification (`F461/020011` Gen D Reference)](#3-own---scs-complete-specification-f461020011-gen-d-reference)
    - [§3.1 SCS Framing Primitives](#31-scs-framing-primitives)
    - [§3.2 Pre-Dispatch & Frame-Type Routing](#32-pre-dispatch--frame-type-routing)
    - [§3.3 Normal Commands (`*4*WHAT*WHERE##`)](#33-normal-commands-4whatwhere)
    - [§3.4 Status Requests (`*#4*WHERE##`) & Reply Collector (`0x63a0`)](#34-status-requests-4where--reply-collector-0x63a0)
    - [§3.5 Dimension READ (`*#4*WHERE*DIM##`)](#35-dimension-read-4wheredim)
    - [§3.6 Dimension WRITE (`*#4*WHERE*#DIM*V0*...##`)](#36-dimension-write-4wheredimv0)
  - [§4. `SCS -> OWN` Complete Specification (`F461/020011` Gen D Reference)](#4-scs---own-complete-specification-f461020011-gen-d-reference)
    - [§4.1 Short SCS Frames (`strlen == 11`)](#41-short-scs-frames-strlen--11)
    - [§4.2 Complete Actuator Code Table (`0..22`, `100..200`)](#42-complete-actuator-code-table-022-100200)
    - [§4.3 Extended SCS Commands (`$18 D1 x2`, `strlen == 19`)](#43-extended-scs-commands-18-d1-x2-strlen--19)
    - [§4.4 Extended SCS Status / Answers (`$18 D2 x4`, `strlen == 19`)](#44-extended-scs-status--answers-18-d2-x4-strlen--19)
    - [§4.5 Internal Decode Tables & Holiday Timer (`0x5390`, `0x54c0`, `0x58b0`, `0x5f50`)](#45-internal-decode-tables--holiday-timer-0x5390-0x54c0-0x58b0-0x5f50)
  - [§5. Cross-Generation Behavioral Deltas](#5-cross-generation-behavioral-deltas)
  - [§6. Verified Firmware Crashes (`CRASH-01..06`) & Protocol Quirks (`QUIRK-01..15`)](#6-verified-firmware-crashes-crash-0106--protocol-quirks-quirk-0115)
  - [§7. Consumer Implementation Rules (Quick Checklist)](#7-consumer-implementation-rules-quick-checklist)

---

## 1. Provenance & 13-Gateway Generation Matrix

### 1.1 Binary Provenance (Verbatim from `results/<product>/<version>/manifest.tsv`)

| Gen | Product | Version | Translator Binary | ELF Type | Size (B) | SHA-256 |
|---|---|---|---|---|---:|---|
| **A** | `H4684` | `020054` | `/home/bticino/bin/bt_termo` | `ELF/ARM/exec/32le/oabi` | 58,916 | `c21fceaaefd9e19601ecc3f959e88ebdb8f506e02b81d99dfcead502adbaa143` |
| **A** | `MH200` | `020102` | `/home/bticino/bin/bt_termo` | `ELF/ARM/exec/32le/oabi` | 55,564 | `33ddcc24da367192b303202314f0c9ec82bf9280da44045a2983a137a0c1d4f9` |
| **B** | `F453` | `010100` | `/home/bticino/bin/bt_termo` | `ELF/ARM/exec/32le/oabi` | 76,420 | `b28d02b1ec8ac565bc23c6c621d2b1d4a791c67c8f2bc2c1924e671198f6590f` |
| **B** | `F453AV` | `030014` | `/home/bticino/bin/bt_termo` | `ELF/ARM/exec/32le/oabi` | 85,780 | `45d4c4529876dd7e64bac2153382f138005838f186a26955c7c563ec6ec6a3e0` |
| **B** | `MH200N` | `010108` | `/home/bticino/bin/bt_termo` | `ELF/ARM/exec/32le/oabi` | 77,980 | `fa2e45e707b1a6d6ce31d04a54490e1ec7453bd2932b44aba23fc5bfeeac2b4c` |
| **C** | `F450` | `020010` | `/home/bticino/bin/bt_termo` | `ELF/ARM/exec/32le/eabi5` | 67,132 | `963cf11bddd078a8e2ad86c8d98efd0136f1cf83c3c79792064eb834f087cd3d` |
| **C** | `F454` | `020051` | `/home/bticino/bin/bt_termo` | `ELF/ARM/exec/32le/eabi5` | 58,892 | `d2c9f8e5f62d45e836f5b52780f8353d89cad57acd40540f6852b3be5461870f` |
| **C** | `F459` | `020105` | `/home/bticino/bin/bt_termo` | `ELF/ARM/exec/32le/eabi5` | 59,836 | `7925ff014a6708a4591fe6c7241ee004ae840589c9c97e86e0ad5c222f0c2a69` |
| **C-coso** | `MH202` | `010024` | `/home/bticino/libcoso/libplug_thermo_tr.so` | `ELF/ARM/dyn/32le/eabi5` | 36,520 | `5e2da864f01b19f4f14dde76e11786234b36c97f4f6778966d6a32f597d1452b` |
| **D** | `MyHomeServer1` | `028206` | `/home/bticino/bin/bt_termo` | `ELF/ARM/dyn/32le/eabi5-hf` | 75,960 | `ef2054ede288b166d5c8ea1e8951316b0172691f47f36181c2bb998caa1c45c6` |
| **D** | `3488` | `030205` | `/home/bticino/bin/bt_termo` | `ELF/ARM/dyn/32le/eabi5-hf` | 79,728 | `f755a29d1d6604b215d34925fcf23e9b270e6ad4cf86d840c0565db5ee1af25d` |
| **D** | `F460` | `020012` | `/home/bticino/bin/bt_termo` | `ELF/AArch64/dyn/64le` | 88,960 | `8df7400eb9364f77a23c9ca170b2238586e95ecb12acc32591177cd7b1c7b0d5` |
| **D** | `F461` | `020011` | `/home/bticino/bin/bt_termo` | `ELF/AArch64/dyn/64le` | 88,960 | `23958a97b4808421de65c13ab254bc76c0f68b10416e3c6f24b9adb108bcf29f` |

**Supporting shared libraries inspected on primary reference `F461/020011` and `MH202/010024`:**

- `F461/020011`:
  - `/home/bticino/lib/libopentrsl.so.1.0.0` (112,624 B, `ed586ca3ead2c6b5c55217a85d900e4f7bd6dc82483469a16b7a30921f155931`)
  - `/usr/lib/libcommon.so.1.0.0` (272,392 B, `245c3a3ac59f748d8256674e9bf25ced58c0f16595b10dea9f176442ac252738`)
  - `/home/bticino/lib/libwhere.so.1.0.0` (75,752 B, `05c974b5892909994075941ac42fd4c6505148e9593df3f7afb5b0700801640e`)
  - `/home/bticino/lib/libopen.so.1.0.0` (112,552 B, `1b57ecb0ad28d3a70ea6c4abbf95f42ec385f3016bc388b42d049a15252cb8ba`)
- `MH202/010024`:
  - `/home/bticino/bin/coso` (28,600 B, `6865c2d1d441d25effa31275080ded70ad3538a8a49288e3002ffb196b066175`)
  - `/home/bticino/lib/libthermo.so` (10,796 B, `a84ac5be4d01eaadceafa4bbc54bee58e6794bf144398d64bb54c1f944686ff2` — byte-identical to `F450/020010` `/home/bticino/lib/libthermo.so`)

> **Binary equivalence within Generation D:** `F460/020012` and `F461/020011` `bt_termo` have **0 instruction differences** (only ELF build-id metadata differs). Across all 11 committed WHO 4 oracle suites, `MyHomeServer1/028206`, `3488/030205`, `F460/020012`, and `F461/020011` have **0 semantic WHO 4 differences** (differing only in the `scsserver` wire prefix `$04`/`$06` vs `$004`/`$006` and in `WHO 1004` / `sendemail` presence on `MyHomeServer1`).

### 1.2 Per-Generation Capability Matrix

| Feature / Behavior | Gen A (`H4684`, `MH200`) | Gen B (`F453`, `F453AV`, `MH200N`) | Gen C (`F450`, `F454`, `F459`) | Gen C-coso (`MH202`) | Gen D (`MHS1`, `3488`, `F460`, `F461`) |
|---|---|---|---|---|---|
| `DIM 0` temperature format | **`0%d` (BUG: 2–3 digits `< 10.0°C`)** | `%04d` | `%04d` | `%04d` | `%04d` |
| Central `#0#C` multi-CU addressing | No (hardcodes `#0`) | Yes | Yes | Yes | Yes |
| `*#4*0##` behavior | Dropped (`silent`) | **BUG: sends `$04990030{00,12,0E,0D}` (zone 0!)** | Dropped (`silent`) | Dropped (`silent`) | Dropped (`silent`) |
| `*#4*Z##` (4-frame zone poll) | Yes | Yes | Yes | Yes | Yes |
| `DIM 2..8, 11, 21, 27, 28` | No | No | Yes | `2..8, 11, 21` (no `27/28`) | Yes |
| `DIM 15, 16, 17, 18, 32` | No | Yes | Yes | `15, 17, 32` | Yes |
| `DIM 20` read forms supported | None (`silent`) | `Z#N`, `0#0`, `#0` (no plain `Z`) | `Z#N`, `Z`, `0#0`, `#0` | **`Z#N` only** | `Z#N`, `Z`, `0#0`, `#0` |
| `DIM 19` / `DIM 20` actuator codes | Basic only (no `2F`/`3F`) | `0..13` | `0..13` | `0..13` | **`0..22` + `100..200` (0–10V)** |
| `DIM 22` split control | Rejected (`silent`) | `3#Z#N` only; **bare WHERE `SIGSEGV`** | `3#Z#N` only; **bare WHERE `SIGSEGV`** | Works (`3#Z#N` & `Z#N`), **no crash** | `3#Z#N` only; **bare WHERE `SIGSEGV`** |
| `DIM 29` | No | No | No | No | Yes |
| `DIM 60` humidity | No | No | **`F459` only** (`F450`/`F454` no) | No | Yes |
| Inbound `D1 02` / short `#DIM` write echoes | None | None | `#2,#3,#4,#5,#7,#8#,#11,#17` | None | **`#0,#2..5,#7,#8#,#11,#14,#17,#18,#20,#21,#22,#29,#30..32`** |
| `WHAT 39..45` & `4001`/`4002` pump calls | No | No | No | No | Yes |
| `D2 04 C1` `3000#` & `112#/212#/312#` (`#HH`) | No | No | No | No | Yes |
| `D2 04 C2` manual setpoint emission | 1 frame (`*4*110*#Z##`) | 1 frame | 1 frame | 1 frame | **2 frames (`*4*110*#Z##` + `*#4*#Z*14*...##`)** |
| Holiday synthetic timer (`0x5f50`) | No | `F453AV`, `MH200N` (not `F453`) | Yes | Yes | Yes |
| `WHO 1004` diagnostics in `bt_termo` | Active (full `*#1004*` strings) | Hooks compiled in | Hooks compiled in | None | `MHS1`: hooks compiled in; **`3488/F460/F461`: dead code** |
| `#4#II` interface routing | Truncated to local `Z` | Truncated / dropped | Truncated to local `Z` | **Misparsed as `Z=4, N=II`; `SIGSEGV` on `*#4*Z#4#II##`** | Truncated to local `Z` |

---

## 2. `WHERE` Addressing Grammar (`OWN <-> SCS`)

### 2.1 `OWN -> SCS`: `ThermoWhereTransl::scsDest(OpenMsg&)`
Verified at `F461/020011` `libopentrsl.so.1.0.0:0x15500..0x15750` (identical logic in `libcommon.so.1.0.0:0x2e5d0` and `libwhere.so.1.0.0:0xdbd0`). Returns a 3-field tuple `(dest_type, scs_addr_12bit, cu_zone)`:

| Input `WHERE` | Exact Match Condition | `dest_type` | `scs_addr` (12-bit hex) | `cu_zone` | Behavioral Notes |
|---|---|---:|---:|---:|---|
| `"0"` | `dove == "0"` and `livello == ""` | `0xD2` | `0x000` | `0` | General broadcast destination. |
| `"#"` (bare hash) | `dove == "#"` | — | — | — | **`CRASH-01` (`SIGSEGV`)**: `OpenMsg::where()` dereferences empty vector `begin() == NULL` at `libopentrsl:0x96f0`. |
| `#Z` (incl. `#0`) | `dove[0] == '#'` and `whereArgCnt() != 1` | `0xD1` | `0x000` | `where() & 0xFF` | **`#Z#4#II`** (`whereArgCnt() == 2`) falls here, silently dropping `#4#II` -> local `#Z`. |
| `#Z#C` (incl. `#0#C`) | `dove[0] == '#'` and `whereArgCnt() == 1` | `0xD1` | `strtol(arg0, 10)` (`C`) | `where() & 0xFF` | Multi-CU / 4-zone central (`C` in `scs_addr`, `Z` in `cu_zone`). |
| `0#0` | `whereArgCnt() == 1`, `A == 0 && B == 0` | `0xD2` | `0x000` | `0` | Identical to `"0"`. |
| `Z#0` (`Z != 0`) | `whereArgCnt() == 1`, `B == 0` | `0xD5` | `Z` | `0` | Zone actuator group (`0xD5`). |
| `Z#N` / `0#N` (`N != 0`) | `whereArgCnt() == 1`, `B != 0` | `0xD1` | `(N << 8) \| (Z + 0x80)` | `0` | Bit 7 (`0x80`) flags actuator/pump (e.g. `0#1` -> `0x180`, `1#2` -> `0x281`). |
| `00ZZ` (4 chars) | `dove[0..1] == "00"` and `strlen(dove) == 4` | `0xD5` | `strtol(dove + 2, 10)` | `0` | Zone group (`0xD5`, e.g. `0001` -> `D5 0x001`). |
| `PZZ` (3 chars, incl. `0ZZ`) | `strlen(dove) == 3` | `0xD1` | `((dove[0]-'0') << 8) + strtol(dove+1, 10)` | `0` | Slave/external probe (`100` -> `0x100`, `201` -> `0x201`). **`001`** (`strlen==3`) maps to `0x001`, identical to `"1"`. |
| Plain `1..99` / default | Fallback | `0xD1` | `strtol(dove, 10)` | `0` | **Root cause of `Z#4#II` truncation:** `whereArgCnt() == 2` skips `== 1`, `strtol("2#4#01")` returns `2`. |

- **`is_plain_zone(OpenMsg*)`** (`F461:0x52c0..0x5380`): true iff `whereFull()[0] != '#'`, `whereArgCnt() == 0`, and `1 <= where() <= 99`. Leading-zero strings `"01"`, `"001"`, `"0001"` all satisfy `is_plain_zone` (even though `"0001"` gets `dest_type = 0xD5`).
- **`MH202/010024` `libthermo.so:scsDest` (`0x1dac..0x1fbc`) difference:** When a 3-part address `A#B#C` (`livello != NULL` and `interfaccia != NULL`) arrives, `MH202` **ignores `A`** and encodes `dest_type = 0xD1`, `scs_addr = (C << 8) + (B + 0x80)` (`0x1e64..0x1ed0`). Thus `3#2#1` works natively without a rewrite, while `2#4#01` is misparsed as local zone 4 actuator 1 (`0x184`).

### 2.2 `SCS -> OWN`: `ThermoWhereTransl::where(char const*)`
Verified at `F461/020011` `libopentrsl.so.1.0.0:0x15760..0x15cbc` (identical in `libcommon.so.1.0.0:0x2e840`):

- **Short SCS (`strlen != 19`)**: `addr12 = ((B2 & 0x0F) << 8) | B1` (low nibble of `B2` is `P` in `3P`):
  - `addr12 == 0` -> `"#0"`
  - `P == 0` -> `"<B1>"` (decimal)
  - `P != 0` and `(B1 & 0x80) != 0` -> `"<B1 - 0x80>#<P>"` (actuator/pump, e.g. `B1=81, B2=32` -> `"1#2"`, `B1=80, B2=31` -> `"0#1"`)
  - `P != 0` and `(B1 & 0x80) == 0` -> `"<P><B1:02d>"` (external/slave probe, e.g. `B1=00, B2=31` -> `"100"`, `B1=05, B2=31` -> `"105"`)
- **Extended SCS (`strlen == 19`)**: `addr12 = ((B2 & 0xF0) << 4) | B1` (high nibble of `B2` is `P` in `P3`):
  - `B4 ∈ {0xC2, 0x8F}`: `Z = (B5 == 0x64 ? 0 : B5 > 0x7F ? B5 - 0x80 : B5)` -> `"#<Z>"` if `addr12 == 0`, else `"#<Z>#<addr12>"`
  - `B4 == 0x8C` -> `"0"`
  - `addr12 == 0` -> `"#0"`
  - `B4 ∈ {0xC0..0xC5, 0xCD}` -> `"#0#<addr12>"`
  - All other `B4` -> same `addr12` formatting as short frames.

---

## 3. `OWN -> SCS` Complete Specification (`F461/020011` Gen D Reference)

### 3.1 SCS Framing Primitives
`bt_termo` formats ASCII hex strings with `$04` / `$06` prefixes (which `scsserver` forwards as `$04`/`$06` on 2-digit UART gateways or `$004`/`$006` on 3-digit RPMsg gateways `F460`/`F461`/`3488`):

| Primitive | Addr (`F461`) | Wire Template (`{D}`=`dest_type`, `{AL}`=`scs_addr & 0xFF`, `{AH}`=`(scs_addr >> 8) & 0xF`) |
|---|---|---|
| `simpleScs` | `0x51b0..0x5220` | `$04{byte1:02X}{AL:02X}3{AH:01X}{byte3:02X}` (system nibble `3` hardcoded) |
| `extScsCmd` | `0x5230..0x52b8` | `$06{D:02X}{AL:02X}{AH:01X}302{d0:02X}{d1:02X}{d2:02X}{d3:02X}` |
| `extScsReq` | inlined (`0x124d0`) | `$06{D:02X}{AL:02X}{AH:01X}303{d0:02X}{d1:02X}{d2:02X}{d3:02X}` |
| `extScsAnsw` | inlined (`0x8604`) | `$06D2{AL:02X}{AH:01X}304{d0:02X}{d1:02X}{d2:02X}{d3:02X}` (DIM 17 simulation only) |

### 3.2 Pre-Dispatch & Frame-Type Routing (`FromOpen2SCS_Invia`, `0x6e00..0x7cd4`)
1. Copies `Extract_chi()` into a **5-byte** stack buffer (`0x6ee8`) and `Extract_dove()` into a **10-byte** stack buffer (`0x6efc`) via `__strcpy_chk` (`CRASH-03`).
2. **Pre-rewrite for `DIM 22` (`0x6f0c..0x7128`):** Whenever `chi == "4"`, `(IsMeasureFrame() || IsWriteFrame())`, and `Extract_grandezza() == "22"`, `bt_termo` **unconditionally** formats `sprintf(x22, "%s#%s", Extract_livello(), Extract_interfaccia())` without checking `dove == "3"`, constructs a temporary `OpenMsg`, and calls `scsDest()`. Any `WHERE` without `'#'` produces `"#"` -> **`CRASH-02` (`SIGSEGV`)**.
3. Frame types are tested in order:
   1. `IsNormalFrame()` (`*4*WHAT*WHERE##`) -> requires `chi == "4"` and `cosa[0] != '\0'`.
   2. `IsMeasureFrame()` (`*#4*WHERE*DIM##`) -> requires `chi == "4"` and `0 <= dim <= 60` (jump table `0x12c38`). **Rejects `WHO=1004` here**, making downstream `"1004"` blocks dead code on `3488`/`F460`/`F461`.
   3. `IsStateFrame()` (`*#4*WHERE##`) -> requires `chi == "4"` and `(unsigned)scs_addr <= 99`.
   4. `IsWriteFrame()` (`*#4*WHERE*#DIM*V0*...##`) -> unconditionally copies `Extract_valori(0)` into a **5-byte** buffer (`0x7c54`, **`CRASH-03`**), strips `#sub` from `grandezza`, requires `chi == "4"`. If none of the four types matched, returns **`+1`** (`0x7c38`) instead of `-1`.

### 3.3 Normal Commands (`*4*WHAT*WHERE##`)

All accepted commands send their SCS telegram and wait in **`0x63a0(mode=2)`** (SCS TX-ACK wait). `{Z}` denotes `cu_zone` (`w25`).

| `WHAT` | Handler Addr | `WHERE` & Parameter Constraints | SCS Telegram Emitted |
|---|---|---|---|
| `0` (Conditioning) | `0x7f68 -> 0x8f7c` / `0x7f80` | `is_plain_zone` (`1..99`) -> short; else (**no `#` check**) -> ext | Plain: `$0493{AL:02X}3{AH:01X}01`<br>Other: `$06{D:02X}{AL:02X}{AH:01X}302C1F10000` |
| `1` (Heating) | `0x78f4 -> 0x81c4` / `0x790c` | `is_plain_zone` (`1..99`) -> short; else (**no `#` check**) -> ext | Plain: `$0493{AL:02X}3{AH:01X}00`<br>Other: `$06{D:02X}{AL:02X}{AH:01X}302C1F00000` |
| `39` | `0x742c -> 0x7f10` | `is_plain_zone` (`1..99`) | `$0493{AL:02X}3{AH:01X}0D` |
| `40` (Release local offset) | `0x7438 -> 0x7efc` | `is_plain_zone` (`1..99`) | `$0493{AL:02X}3{AH:01X}0E` |
| `41` / `42` | `0x7448` / `0x745c` | `is_plain_zone` (`1..99`) | `$049F{AL:02X}3{AH:01X}E0` / `E1` |
| `45` | `0x79e4` | `is_plain_zone` (`1..99`) | `$06{D:02X}{AL:02X}{AH:01X}302F0FFFF00` |
| `102` / `202` / `302` (Protection) | `0xa3dc`/`0x7fbc`/`0x78d0` | Plain `1..99` -> short (`02`/`03`/`04`)<br>`#0`/`#0#C` (`Z==0`) -> `C1`<br>`#Z`/`#Z#C` (`Z!=0`) -> `C2` | Plain: `$0493{AL:02X}3{AH:01X}{02\|03\|04}`<br>`#0`/`#0#C`: `$06{D:02X}{AL:02X}{AH:01X}302C1{17\|27\|07}0000`<br>`#Z`/`#Z#C`: `$06{D:02X}{AL:02X}{AH:01X}302C2{Z:02X}{17\|27\|07}00` |
| `103` / `203` / `303` (OFF) | `0x7960 -> 0x81f4` / `0x800c` | Plain `1..99` -> short (`0F`)<br>`#0`/`#0#C` -> `C1`; `#Z`/`#Z#C` -> `C2` | Plain: `$0493{AL:02X}3{AH:01X}0F`<br>`#0`/`#0#C`: `$06{D:02X}{AL:02X}{AH:01X}302C1{16\|26\|06}0000`<br>`#Z`/`#Z#C`: `$06{D:02X}{AL:02X}{AH:01X}302C2{Z:02X}{16\|26\|06}00` |
| `111` / `211` / `311` (AUTO) | `0x76dc` | `strlen(dove) > 1 && dove[0] == '#'` | `$06{D:02X}{AL:02X}{AH:01X}302C2{Z:02X}{10\|20\|00}00` *(uses `C2` even for `#0`)* |
| `112#T#M` / `212#T#M` / `312#T#M` | `0x730c..0x7428` | **No WHERE or range check**; `whatArgCnt() == 2` | `$06{D:02X}{AL:02X}{AH:01X}302C1{19\|29\|09}{(T/5)&FF:02X}{M&FF:02X}` *(always `C1`, never `C2`!)* |
| `115` / `215` / `315` (`[#M1PP]`) | `0x8ce4`/`0x8d14`/`0x758c` | **`dove == "#0"` EXACT** (`#0#C` rejected!); `M ∈ {'1','2','3'}`, `01 <= PP <= 99` | `$06D1000302C1{14\|24\|04}{PP-1:02X}00` (`00` without `#`) |
| `1100..1199` / `2100..2199` / `3100..3199` | `0x80cc`/`0x7508`/`0x765c` | `#0` or `#0#C` (`dove[0]=='#' && Z==0`) | `$06{D:02X}{AL:02X}{AH:01X}302C1{11\|21\|01}{P:02X}00` (`1100/2100→00`, `3100→FF`, `x101..x199→00..62`) |
| `1200..1299` / `2200..2299` / `3200..3299` | `0x7548`/`0x8de4`/`0x77a0` | `#0` or `#0#C` (`dove[0]=='#' && Z==0`) | `$06{D:02X}{AL:02X}{AH:01X}302C1{13\|23\|03}{P:02X}00` (`1200/2200→00`, `3200→FF`, `x201..x299→00..62`) |
| `3000` (no `#`) / `3001` | `0xa40c`/`0x787c` | **`dove == "#0"` EXACT** | `$06D1000302C1050000` |
| `3000#M1PP` | `0x8d44..0x8db4` | **`dove == "#0"` EXACT**; `M='1'→15, '2'→25, '3'→05`, `01 <= PP <= 99` | `$06D1000302C1{15\|25\|05}00{PP-1:02X}` |
| `13000` / `13001..13255#M1PP` (also `23xxx`, `33xxx`) | `0x7768`/`0x7dc4`/`0x74c4` | **`dove == "#0"` EXACT** (`#0#C` rejected!); `M` ignored, `01 <= PP <= 99` | `$06D1000302C1{15\|25\|05}{days:02X}{PP-1:02X}` (`days=0, PP-1=0` for `x3000`) |
| `4001#Z` / `4002#Z` (Pump call) | `0x8180..0x81c0`, `0x8074`, `0x7624` | With `#Z`: `WHERE=0#1..0#9` & `Z<=99 && Z!=0` (signed). **Without `#`: no WHERE check (`Z=0`)!** | `$04{9D\|9E}{AL:02X}3{AH:01X}{Z&FF:02X}` |

### 3.4 Status Requests (`*#4*WHERE##`) & Reply Collector (`0x63a0`)

Global gate (`0x7a34..0x7a4c`): `chi == "4"` and `(unsigned)scs_addr <= 99` (`0x63`). Rejects `0`, `PZZ` (`100..999`), `Z#N`, `0#N`, `#Z#C` (`C > 99`).

| Request | Addr | SCS Telegram(s) Sent | Collector Mode |
|---|---|---|---|
| `*#4*Z##` (`1..99`, also `01..09`, `001..099`, `0001..0099`) | `0x6ba0..0x6cc8` | 4 frames: `$0499{ZZ}3000`, `$0499{ZZ}3012`, `$0499{ZZ}300E`, `$0499{ZZ}300D` | **Mode `3`** |
| `*#4*#0##`, `*#4*#0#C##` (`C <= 99`) | `0x8120..0x817c` | `$0499{C:02X}30C0` | **Mode `4`** |
| `*#4*#Z##`, `*#4*#Z#C##` (`1 <= Z <= 255, C <= 99`) | `0x7e54..0x7ef8` | `$06D1{C:02X}0303C2{Z:02X}0000` *(note: `Z==256` masks to `0`, hits `#0`!)* | **Mode `5`** |

**`0x63a0(mode, expected_dim, expected_where)` Reply Collector Rules (`0x63a0..0x6b90`):**

- **Timing:** Per-read `select()` timeout = **2 s** (`0x64d0..0x64f0`); total wall-clock timeout = **`> 7 s` (8 s)** (`0x6638`, `0x6870`).
- **Bus sentinels (`0x66c4..0x66dc`):** `$18D20003048F000000` returns `0` (ACK) immediately in all modes; `$18D2000304FFFFFFFF` returns `-1` (NACK) immediately in all modes.
- **Monitor fan-out:** Every valid translated SCS reply arriving while inside `0x63a0` is forwarded to `monitor_fd` (`0x6764`).
- **Global `got_dim` WHERE-bypass (`0x67a8..0x67c4 -> 0x6890`):** If `got_dim ∈ {2, 3, 18, 30, 31, 32}` and `got_dim == expected_dim`, `0x63a0` accepts and forwards to `client_fd` **without verifying `got_where` (`QUIRK-09`)**.
- **Hard 8-reply cap (`0x6828..0x6850`, `QUIRK-08`):** Multi-reply modes (`mode ∈ {1, 6, 9}`) return `0` (sending `*#*1##` to the command session) immediately when `reply_count == 8`! On plants with `> 8` actuators (`*#4*0#0*20##`) or `> 8` humidity probes (`*#4*0*60##`), frames `9..N` arrive on the bus after the command ACK.

| Mode | Caller(s) | Match Condition (`client_fd` forward) | Success (`0`) Termination |
|---:|---|---|---|
| `0` | Standard `*#4*W*DIM##` | `got_dim == expected_dim && (expected_where == 0 \|\| got_where == expected_where)` | First matching frame |
| `1` | `DIM 18` read, or `WHERE ∈ {"0","#0","0#0"}` read | Same as mode `0` | `reply_count == 8` OR 2 s silence / `> 7 s` elapsed with `1..8` replies |
| `2` | All normal commands, dim writes, `DIM 17` sim | Never forwards to `client_fd` | SCS ACK (`ret == 1`) or `$18D20003048F000000` |
| `3` | `*#4*Z##` 4-frame zone poll (`0x6ba0`) | `got_dim ∈ {0, 12, 13, 14} && got_where == expected_where` | **All 4** distinct dims `{0, 12, 13, 14}` received (missing any 1 -> NACK) |
| `4` | `*#4*#0##`, `*#4*#0#C##` | `dove[0] == '#' && strtol(dove + 1) == expected_where` | First match |
| `5` | `*#4*#Z##`, `*#4*#Z#C##` | Same as mode `4` | First match |
| `6` | `*#4*0ZZ*0##` 8-probe scan (`0x6cd0`) | `got_dim == 0 && (got_where - 100*k == expected_where)` for `k ∈ 1..8` | `reply_count == 8` OR timeout with `1..8` replies |
| `7` | Dead `WHO=1004` path | `got_dim == 21 && dove[0] == '#'` | Only `$18D20003048F000000` |
| `8` / `9` | Dead `WHO=1004` read (`dest != 0xD5` / `== 0xD5`) | `got_dim == expected_dim` (or `chi=="1004" && 1<=dim<=8`) | Mode `8`: first match; Mode `9`: up to 8 replies |

### 3.5 Dimension READ (`*#4*WHERE*DIM##`)
27 dimensions exist in the `0..60` switch table (`0x12c38`); all other dims (`1, 9, 10, 23..26, 33..59`, `> 60`, `< 0`) return `-1`. Default collector wait is **mode `0`**, upgraded to **mode `1`** when `dim == 18` or `WHERE ∈ {"0", "#0", "0#0"}`.

| DIM | Entry | `WHERE` / Value Constraints | SCS Telegram Emitted | Mode |
|---:|---|---|---|---|
| **0** | `0x8428` | `whereArgCnt() == 0`. **If `dove[0] == '0'`** (`0`, `01..09`, `001..099`, `0001..0099`): calls `0x6cd0(w21)` (8-probe scan, `QUIRK-05`)! Else (incl. `#Z`): single read. | `dove[0]=='0'`: 8 frames `$0499{ZZ}3{1..8}00`<br>Else: `$0499{AL:02X}3{AH:01X}00` | `6` / `0` (`1` if `#0`) |
| **2** | `0x84a4` | `num_valori == 0`, **no WHERE check** | `$0499{AL:02X}3{AH:01X}0C` | `0` (`1` if `0`/`#0`) |
| **3** | `0x8354` | `num_valori == 0`, **no WHERE check** | `$0499{AL:02X}3{AH:01X}0F` | `0` (`1` if `0`/`#0`) |
| **4 / 5 / 6 / 7** | `0x83c0`/`8328`/`82fc`/`81fc` | `is_plain_zone` (`1..99`) && `num_valori == 0` | `$06{D:02X}{AL:02X}{AH:01X}303{01\|02\|03\|05}000000` | `0` |
| **8** (`8#N`) | `0x8934` | `is_plain_zone` && `num_valori == 0` && `'#'` && `1 <= N <= 8` | `$06{D:02X}{AL:02X}{AH:01X}30304{(N-1):02X}0000` | `0` |
| **11 / 12 / 13** | `0x88f0`/`8918`/`86d8` | `is_plain_zone` (`1..99`) (`11` checks `num_valori == 0`) | `$0499{AL:02X}3{AH:01X}{0B\|12\|0D}` | `0` |
| **14** | `0x86c0` | `num_valori == 0`, **no WHERE check** (`#0#C` polls plain zone `C`!) | `$0499{AL:02X}3{AH:01X}0E` | `0` (`1` if `0`/`#0`) |
| **15** (`15`/`15#N`) | `0x8670` | `dove[0] != '#'`; optional `#N` (default `0`) | `$06{D:02X}{AL:02X}{AH:01X}30321{N&FF:02X}0000` | `0` (`1` if `0`) |
| **16** | `0x8630` | `dove[0] != '#'` && `AH != 0` (`[0-99]#[1-9]` or `PZZ`) | `$0499{AL:02X}3{AH:01X}90` | `0` |
| **17** (read) | `0x8548 -> 0x9290` | `Z#N` (`AL > 0x80 && AH != 0`) && `num_valori == 0` | `$0499{AL:02X}3{AH:01X}14` | `0` |
| **17** (sim) | `0x8548 -> 0x8570` | `Z#N` && **`num_valori == 3`** (`*#4*Z#N*17*V0*V1*V2##`, no `#`!), `30 <= V2 <= 400` | `$06D2{AL:02X}{AH:01X}30414{V0:02X}{V1:02X}{(V2/5):02X}` | `2` + monitor echo |
| **18** (`18`/`18#C`) | `0x8788` | **No WHERE check**; optional `#C` with `(C & 0xFF) <= 9` | `$06{D:02X}{AL:02X}{AH:01X}303CD{C&FF:02X}0000` | **`1`** |
| **19** | `0x876c` | `is_plain_zone` (`1..99`) | `$0499{AL:02X}3{AH:01X}13` | `0` |
| **20** | `0x8714` | `0`/`#0`/`0#0` -> `B9`; `Z`/`00ZZ` -> `A9`; `Z#N`/`0#N` -> `99` (rejects `PZZ`) | `$04{B9\|A9\|99}{AL:02X}3{AH:01X}10` | `0` (`1` if `0`/`#0`/`0#0`) |
| **21** | `0x86f4` | `0`/`#0`/`Z`/`00ZZ` or `Z#N` (rejects `0#N` and `PZZ`) | `$06{D:02X}{AL:02X}{AH:01X}30317000000` | `0` (`1` if `0`/`#0`) |
| **22** | `0x8838` | **Pre-rewrite `CRASH-02` if no `#`!** Requires `X#Z#N` (`AL > 0x80 && AH != 0`); sets `expected_where = strtol(X)` | `$06{D:02X}{AL:02X}{AH:01X}30316000000` | `0` |
| **27 / 28** | `0x8810`/`0x88c8` | `is_plain_zone` (`1..99`) && `num_valori == 0` | `$0499{AL:02X}3{AH:01X}{F1\|F2}` | `0` |
| **29** | `0x84d4` | Same WHERE rule as `DIM 21` | `$06{D:02X}{AL:02X}{AH:01X}30319000000` | `0` (`1` if `0`/`#0`) |
| **30 / 31 / 32** | `0x8530`/`8524`/`853c` | **No WHERE / `num_valori` check** | `$0499{AL:02X}3{AH:01X}{C3\|C4\|C5}` | `0` (`1` if `0`/`#0`) |
| **60** | `0x84bc` | `num_valori == 0`, **no WHERE check** | `$06{D:02X}{AL:02X}{AH:01X}30308000000` | `0` (`1` if `0`/`#0`) |

### 3.6 Dimension WRITE (`*#4*WHERE*#DIM*V0*...##`)
All 18 supported write dimensions execute the global 5-byte `V0` copy (`0x7c54`, **`CRASH-03`**) and wait in **mode `2`** after transmission.

| DIM | Entry | `WHERE` Constraint | Value Validation & Encoding | SCS Telegram Emitted |
|---:|---|---|---|---|
| **`#0`** | `0x7cd4` | `is_plain_zone` (`1..99`) | `num_valori == 1`; `(unsigned)V0 <= 511` (`0.0..51.1 °C`; no negatives) | `$06{D:02X}{AL:02X}{AH:01X}302F0{V0>>8:02X}{V0&FF:02X}00` |
| **`#2`** | `0x8c0c` | `is_plain_zone` | `num_valori == 1`; `250 <= V0 <= 400` (`25.0..40.0 °C`) | `$06{D:02X}{AL:02X}{AH:01X}3020C{V0>>8:02X}{V0&FF:02X}00` |
| **`#3`** | `0x8e40` | `is_plain_zone` | `num_valori == 1`; `30 <= V0 <= 200` (`3.0..20.0 °C`) | `$06{D:02X}{AL:02X}{AH:01X}3020F00{V0:02X}00` |
| **`#4` / `#5` / `#11`** | `0x8f04`/`8fe4`/`95d0` | `is_plain_zone` | `num_valori == 1`; `V0 <= 3` **signed** (`QUIRK-12`) | `$06{D:02X}{AL:02X}{AH:01X}302{01\|02\|0B}{V0&FF:02X}0000` |
| **`#7`** | `0x90b0` | `is_plain_zone` | `num_valori >= 2`; `M <= 3`, `F <= 5 \|\| F ∈ {31,32}` (signed). Uses `0x55f0(atoi("%d%d", M, F))` (table below). 2-char entries require `V2` (**`CRASH-04` if `< 3` args!**), `6 <= V2/5 <= 80`. Unmapped `(M, F)` reads uninitialized heap (`QUIRK-04`). | `$06{D:02X}{AL:02X}{AH:01X}30205{d1:02X}{d2:02X}00` |
| **`#8#P`** | `0x92c0` | `is_plain_zone` | `num_valori == 1`; `1 <= P <= 8`; `T = V0 / 5` (0.5 °C units) validated per `P`: `P1:21..80`, `P2:6..69`, `P3:20..40`, `P4:50..70`, `P5:6..79`, `P6:6..70`, `P7:20..80`, `P8:7..80` | `$06{D:02X}{AL:02X}{AH:01X}30204{(P-1):02X}{T:02X}00` |
| **`#14`** | `0x94b0` | Plain `1..99`; OR `#0`/`#0#C`/`#Z`/`#Z#C` **AND `V0[0] == '0'`** (`0x9504`, `QUIRK-07`) | **No `num_valori` check (`CRASH-04` if `< 2` args!)**. `6 <= V0/5 <= 80` (`3.0..40.4 °C`), `M ∈ {1, 2, 3}`. | Plain: `$04{90\|91\|92}{AL:02X}3{AH:01X}{T:02X}`<br>`#0`/`#0#C`: `$06{D:02X}{AL:02X}{AH:01X}302C1{12\|22\|02}{T:02X}00`<br>`#Z`/`#Z#C`: `$06{D:02X}{AL:02X}{AH:01X}302C2{Z:02X}{12\|22\|02}{T:02X}` |
| **`#17`** | `0x93d4` | `scs_addr > 0x7F` (`Z#N`, `0#N`, `PZZ`) | `num_valori == 3` (**no range checks**) | `$06{D:02X}{AL:02X}{AH:01X}30214{V0:02X}{V1:02X}{(V2/5):02X}` |
| **`#18`** | `0x9c00` | **None** | `whatArgCnt() == 2`; `V0 <= 9`, `V1 <= 255` (signed) | `$06{D:02X}{AL:02X}{AH:01X}302CD{V0:02X}{V1:02X}00` |
| **`#20`** | `0x9b64` | **None** (`0/#0→B6`, `AH!=0→96`, else `A6`) | 1 arg: `0..22` via §4.2 table; `>= 100` -> `(v + 0x1C) & 0xFF` (`100..200 → 80..E4`). 2 args: `V0 == 1`, `1 <= V1 <= 100 → V1 + 0x80` else `V1`. | `$04{B6\|96\|A6}{AL:02X}3{AH:01X}{byte3:02X}` |
| **`#21` / `#29`** | `0x9aa8`/`0xa0c0` | **None** | `>= 3` non-empty args; `#21`: `(V0&FF)<=3, (V1&FF)<=3, (V2&FF)<=100`; `#29`: `<=100, <=3, <=100` | `$06{D:02X}{AL:02X}{AH:01X}302{17\|19}{V0:02X}{V1:02X}{V2:02X}` |
| **`#22`** | `0x9694` | **Pre-rewrite `CRASH-02` if no `#`!** `scs_addr > 0x7F` (`3#Z#N`) | Empty defaults: `V0→FF` (`d2`), `V1→FF` (`d3`, else `V1/5`), `V2→0F` (`lo(d1)`), `V3→F0` (`hi(d1)`: `1→00, 0→10, 15→F0`, else `-1`). `d1 = V2 \| V3_hi`. | `$06{D:02X}{AL:02X}{AH:01X}30216{d1:02X}{d2:02X}{d3:02X}` |
| **`#30`** | `0x9e1c` | **None** | `1<=DD<=31`, `1<=MM<=12`, `YYYY >= 2005` (Gregorian leap check; `"029"`/`"031"` bypass `strcmp`). **Blocks thread with `sleep(1)` (`QUIRK-13`)!** | `$06{D:02X}{AL:02X}{AH:01X}302C3{DD:02X}{MM:02X}{(YYYY-2000)&FF:02X}` |
| **`#31` / `#32`** | `0xa2c0`/`0xa1b8` | **None** | `#31`: `0<=HH<=23`; `#32`: `0<=HH<=24`; `0<=MM<=59`. **Both block with `sleep(1)` (`QUIRK-13`)!** | `$06{D:02X}{AL:02X}{AH:01X}302{C4\|C5}{HH:02X}{MM:02X}00` |

**Complete `DIM 7` Write Table (`0x55f0`, input `atoi("%d%d", M, F)`):**
- **2-char (requires 3rd arg `V2`, `d2 = V2 / 5`):** `0*1 ("1") → 1A`, `1*1 ("11") → F0`, `2*1 ("21") → F1`, `3*1 ("31") → 17` *(note: `0*31` aliases `"31"` = `3*1`!)*.
- **4-char (`d1 d2`):** `0*2→F4FF`, `0*3→18FF`, `0*4→19FF`, `0*5→FFFF`, `1*0→F0FF`, `1*2→F2FF`, `1*3→10FF`, `1*4→11FF`, `2*0→F1FF`, `2*2→F3FF`, `2*3→12FF`, `2*4→13FF`, `3*0→17FF`, `3*2→16FF`, `3*3→14FF`, `3*4→15FF`, `1*31→F9FF`, `1*32→FAFF`.
- **All other pairs:** returns `"\n"` (2 bytes in 5-byte `malloc`, leaked on every call).

---

## 4. `SCS -> OWN` Complete Specification (`F461/020011` Gen D Reference)

`bt_termo` compiles two identical copies of `FromSCS2OPEN` (`0xa580` for command replies, `0xdfd0` for the monitor stream). It **never inspects `scs[0..2]`** (`$04`/`$06`/`$18`), branching solely on `strlen(scs) == 11` (short) vs `19` (extended). Extended frames require `B2[1] == '3'` (system 4) and check only `B3[1]` (`'2'` for `D1`, `'4'` for `D2`; `B3[0]` is ignored).

### 4.1 Short SCS Frames (`strlen == 11`: `$04 <B0> <B1> <B2> <B3>`, `B2[0] == '3'`)

| SCS Pattern | `WHERE` Source | Emitted OpenWebNet Frame | Notes / Addr |
|---|---|---|---|
| `B4 <Z> 30 <T>` / `B5 <Z> 30 <T>` | `B1` (`%d`) | `*#4*<Z>*0*<T:04d>##` | `B4`: `0..255` (`0.0..25.5 °C`); `B5`: `B3 + 256` (`25.6..51.1 °C`). No negative short temps (`0xacf8`). |
| `B4/B5 <Z> 3<P> <T>` (`P ∈ 1..8`) | `<P><Z:02d>` | `*#4*<P><Z:02d>*0*<T:04d>##` | Slave probes `P = 1..8` only (**no `P = 9`**) (`0xaac8`). |
| `B7 <Z> 3<x> {F0\|F1\|F2\|F3\|FF}` | `B1` (`%d`) | `*4*{1\|0\|102\|202\|303}*<Z>##` | Zone mode (`0xa904`). |
| `B7 <B1> 3<P> <ACT>` (`B3[0] != 'F'`) | `where(scs)` | `*#4*<W>*20*<V>##` | Actuator status via §4.2 table (`0xaeec`). |
| `90/91/92 <Z> 3<x> <T>` | `B1` (`%d`) | `*#4*<Z>*#14*<B3*5:04d>*{1\|2\|3}##` | Gen D inbound setpoint write echo (`0xb320`). |
| `93 <Z> 3<x> {0D\|0E}` | `B1` (`%d`) | `*4*{39\|40}*<Z>##` | `40` = release local offset (`0xb114`, `0xb25c`). |
| `96 <B1> 3<P> <ACT>` | `where(scs)` | `*#4*<W>*#20*<V>##` | Gen D inbound actuator write echo via §4.2 table (`0xbcb8`). |
| `9D <B1> 3<P> <V>` | `where(scs)` | `*4*4001#<V>*<W>##` | Pump call ON (`V = B3` decimal, `0xbebc`). |
| `9E <B1> 3<P> {FF\|<V>}` | `where(scs)` | `*4*4002*<W>##` (`FF`) / `*4*4002#<V>*<W>##` | Pump call OFF (`0xb0b8`, `0xc128`). |
| `9F/BF <B1> 3<P> {E0\|E1\|E2\|E5}` | `where(scs)` | `*4*{41\|42\|43\|44}*<W>##` | Undocumented short events (`0xcb50/cb2c/c9f8/a7cc`). |

### 4.2 Complete Actuator Code Table (`0..22`, `100..200`)
Identical across short `96` (`#20` echo), short `B7` (`20` status), extended `D2 04 13` (`19` valve status), and `#20` write (`0x12cf8`):

| SCS Hex | OWN `V` | Gen Support | SCS Hex | OWN `V` | Gen Support | SCS Hex | OWN `V` | Gen Support |
|---|---:|---|---|---:|---|---|---:|---|
| `0x01` | `0` | A, B, C, C-coso, D | `0x22` | `7` | A, B, C, C-coso, D | `0x25` | `15` | **D only** |
| `0x00` | `1` | A, B, C, C-coso, D | `0x23` | `8` | A, B, C, C-coso, D | `0x26` | `16` | **D only** |
| `0x10` | `2` | A, B, C, C-coso, D | `0x2F` | `9` | B, C, C-coso, D | `0x40` | `17` | **D only** |
| `0x11` | `3` | A, B, C, C-coso, D | `0x30` | `10` | B, C, C-coso, D | `0x41` | `18` | **D only** |
| `0x12` | `4` | A, B, C, C-coso, D | `0x31` | `11` | B, C, C-coso, D | `0x42` | `19` | **D only** |
| `0x20` | `5` | A, B, C, C-coso, D | `0x32` | `12` | B, C, C-coso, D | `0x43` | `20` | **D only** |
| `0x21` | `6` | A, B, C, C-coso, D | `0x3F` | `13` | B, C, C-coso, D | `0x44` | `21` | **D only** |
| | | | `0x24` | `14` | **D only** | `0x45` | `22` | **D only** |
| | | | | | | `0x80..0xE4` | `B - 0x1C` (`100..200`) | **D only** (0–10V `0..100%`) |

All other byte values return `-1` in `96`/`B7` and produce an **empty field `""`** in `D2 04 13` (`DIM 19`, e.g. `*#4*Z*19**1##`, `*#4*Z*19*0*##`).

### 4.3 Extended SCS Commands (`$18 D1 x2`, `strlen == 19`)
Splits at `0xab88` on **`B1 == "00"`** (requires `B2 == "03"`) vs **`B1 != "00"`**:

**Branch A (`B1 == "00"`, `#0` single central unit only):**

| `B4` | Condition & Payload | Emitted Frame / Side Effect | Notes |
|---|---|---|---|
| `C1` | `B5` (base 10) ∈ `{"05","15","25"}` | Buffers holiday WHAT (`33000`/`13000`/`23000` + `B6_hex`) at `[0x25018]`, sets `g_timeout = 10` | Emits nothing yet; waits for `C3` + `C4` (§4.5.4). |
| `C1` | `B5` (base 10) ∈ `{"02","12","22"}` | `*#4*#0*#14*<B6*5:04d>*{3\|1\|2}##` | Gen D echo. |
| `C2` | `B6` (base 10) ∈ `{"02","12","22"}` | `*#4*#<Z>*#14*<B7*5:04d>*{3\|1\|2}##` | Gen D echo. |
| `C3` | `DD=B5, MM=B6, YYYY=B7+2000` | `*#4*#0*#30*<DD:02d>*<MM:02d>*<YYYY>##` (+ triggers `0x5f50` if `hour>=0 && holiday_what!=-1`) | Missing `*#*1##` sentinel in `arr[1]` (`QUIRK-14`). |
| `C4` | `HH=B5, MM=B6` | `*#4*#0*#31*<HH:02d>*<MM:02d>##` (+ triggers `0x5f50` if `day>=0 && holiday_what!=-1`) | Missing `*#*1##` sentinel (`QUIRK-14`). |
| `C5` | `HH=B5, MM=B6` | `*#4*#0*#32*<HH:02d>*<MM:02d>##` | Missing `*#*1##` sentinel (`QUIRK-14`). |
| `CD` | `v1=B5, v2=B6` | `*#4*#0*#18*<v1>*<v2>##` | Gen D echo. |

**Branch B (`B1 != "00"`, zones, actuators, and multi-CU `#0#C`):**

| `B4` | Emitted OpenWebNet Frame | Notes / Gen |
|---|---|---|
| `01` / `02` / `0B` | `*#4*<W>*#{4\|5\|11}*<B5>##` | Gen C & D (`B5` hex -> dec) |
| `04` | `*#4*<W>*#8#<B5+1>*<B6*5:04d>##` | Gen C & D |
| `05` | `*#4*<W>*#7*<M*F>[*<B6*5:04d>]##` | Gen C & D (`0x58b0`; leaks `malloc(5)`) |
| `0C` / `0F` | `*#4*<W>*#{2\|3}*<B5[0]><((B5&F)<<8)\|B6:03d>##` | Gen C & D (supports `'1'` negative) |
| `14` | `*#4*<W>*#17*<B5>*<B6>*<B7*5:04d>##` | Gen C & D |
| `16` | `*#4*3#<W>*#22*<B6>*<T\|>*<lo\|15>*<1\|0\|15\|none>##` | Gen D (`hi(B5)` inverted: `0→1, 1→0`; nibble `0xF → *15`, asymmetric with `D2`!) |
| `17` / `19` | `*#4*<W>*#{21\|29}*<B5>*<B6>*<B7>##` | Gen D |
| `C1` / `C2` | `*#4*<W>*#14*<{B6\|B7}*5:04d>*{3\|1\|2}##` | Gen D (**no holiday buffering on `#0#C`!**) |
| `CD` | `*#4*<W>*#18*<B5>*<B6>##` | Gen D |
| `F0` (`B5=FF, B6=FF`) | **`*4*45*<W>##`** | Gen D (`0xd938`) |
| `F0` (else) | `*#4*<W>*#0*<B5[0]><val:03d>##` | Gen D (**`QUIRK-01`:** `strtol(B5, 16) <= 1` at `0xbb38` rejects ALL negative `#0` writes!) |

### 4.4 Extended SCS Status / Answers (`$18 D2 x4`, `strlen == 19`)

| `B4` | `WHERE` Source | Emitted OpenWebNet Frame(s) | Exact Formulas & Quirks |
|---|---|---|---|
| `01` / `02` / `03` / `0B` | `where()` | `*#4*<W>*{4\|5\|6\|11}*<B5>##` | `strtol(B5, 16)` |
| `04` | `where()` | `*#4*<W>*8#<B5+1>*<B6*5:04d>##` | Preset setpoint `1..8` |
| `05` | `where()` | `*#4*<W>*7*<M*F>[*<B6*5:04d>]##` | `0x58b0` (§4.5.3); if `B6 != "FF"` rewrites `0*1`/`M*0` -> `M*1`; unmapped emits `\n` & leaks `malloc(5)` (`QUIRK-04`) |
| `08` | `where()` | `*#4*<W>*60*<B7>##` | Reads **`B7`** (humidity %; Gen C `F459` & Gen D) |
| `0C` / `0F` | `where()` | `*#4*<W>*{2\|3}*<B5[0]><((B5&F)<<8)\|B6:03d>##` | High/low temperature threshold (supports `'1'` negative) |
| `0D` | `B1` (`%d`) | `*#4*<Z>*13*<s>##` | `0x54c0` (§4.5.2; includes `"6"`, `"7"`, `"8"`) |
| `0E` | `B1` (`%d`) | `*#4*<Z>*14*<B5[0]><val:03d>*3##` | Trailing `M=3` hardcoded (`0xc0e0`) |
| `12` | `B1` (`%d`) | **2 frames:** `*#4*<Z>*12*<B6[0]><val:03d>*3##` + `*4*<WHAT>*<Z>##` | `WHAT = 0x5390(B5)` (§4.5.1; **unchecked `-1` -> `*4*-1*<Z>##`**, `QUIRK-02`) |
| `13` | `B1` (`%d`) | `*#4*<Z>*19*<CV>*<HV>##` | **Swapped order:** `CV = §4.2(B6)`, `HV = §4.2(B5)`. Unmapped byte -> empty `""` (`QUIRK-03`) |
| `14` | `where()` | `*#4*<W>*17*<B5>*<B6>*<B7*5:04u>##` | `CreateDimensionMsgOpen` |
| `16` | `3#<where()>` | `*#4*3#<W>*22*<B6>*<T\|>*<lo\|>*<1\|0\|\|none>##` | `hi(B5)` inverted (`0→*1, 1→*0`); nibble `0xF` -> **empty `*`** (`QUIRK-06`) |
| `17` / `19` / `90` / `CD` | `where()` | `*#4*<W>*{21\|29\|16\|18}*...##` | `19` (`DIM 29`) requires `B5 <= 100`; `CD` (`DIM 18`) emits 2 values `B5*B6` |
| `21` | `where()` | `*#4*<W>*15*<lo(B5)>*<sign><mag:03u>*<hi(B5):04b>##` | Sign = **bit 15 (`0x8000`)** of `(B6<<8)\|B7`; 3rd field = 4-bit binary of `hi(B5)` |
| `C0` | `where()` (`#0`/`#0#C`) | **2–7 frame burst:**<br>Frame 1 (`B5` base 10): `11/21→1101/2101+B6`, `12/22→110/210#<B6*5:04d>`, `13/23→1201/2201+B6`, `14/24→115/215#<1101/2101+B6>`, `15/25→13000/23000+B6` *(suppressed while holiday timer armed!)*, `16/26→103/203`, `17/27→102/202`, `19/29→112/212#<B6*5:04d>`<br>Frames 2..7 (`B7` bitmask): `bit0: 0→20 / 1→21` (always), `bit1→30`, `bit2→31`, `bit3→22`, `bit4→23`, `bit5→24` | Never subtracts `1` on `#0#C` (unlike `C1`). |
| `C1` | `where()` (`#0`/`#0#C`) | `w24 = (strncmp(W, "#0#", 3) == 0 ? 1 : 0)` (`QUIRK-10`):<br>• `11/21/01 → *4*<{1101\|2101\|3101}+B6-w24>*<W>##`<br>• `13/23/03 → *4*<{1201\|2201\|3201}+B6-w24>*<W>##`<br>• `14/24/04 → *4*{115\|215\|315}#<{1101\|2101\|3101}+B6>*<W>##`<br>• `15/25/05` (`B6==0`) `→ *4*3000#<{1100\|2100\|3100}+B7>*<W>##`<br>• `15/25/05` (`B6!=0`) `→ *4*<{13000\|23000\|33000}+B6>#<{1101\|2101\|3101}+B7>*<W>##`<br>• `19/29/09 → *4*{112\|212\|312}#<B6*5:04u>#<B7:02u>*<W>##` (**`CRASH-05` if `B7>=100`!**)<br>• `00/10/20 → 311 / 108 / 507`; `06/16/26 → 303 / 103 / 203`; `07/17/27 → 302 / 102 / 202`<br>• `1B..1F → 408 / 407 / 402 / 410 / 508`; `F0..FF → 1 / 0 / 102 / 202 / 303`<br>• `02/12/22` (base 10) `→ *#4*<W>*14*<B6*5:04d>*{3\|1\|2}##` | `w24=1` causes `B6==0` on `#0#C` to emit `1100`/`2100`/`1200`/`2200`! |
| `C2` | `where()` (`#Z`/`#Z#C`) | • `12/22/02`: **2 frames** (Gen D): `*4*{110\|210\|310}*<W>##` + `*#4*<W>*14*<B7*5:04d>*{1\|2\|3}##` *(Gen A/B/C emit frame 1 only!)*<br>• `10/20/00 → 111/211/311`; `17/27/07 → 102/202/302`; `16/26/06 → 103/203/303`; **`19/29/09 → 104/204/304`** | `104/204/304` = timed manual / override on CU zone. |
| `C3/C4/C5` | `where()` | `*#4*<W>*30*<DD:02d>*<MM:02d>*2<YY:03d>##`; `*31*<HH:02d>*<MM:02d>##` (`HH<=23`); `*32*<HH:02d>*<MM:02d>##` (`HH<=24`) | `C4` rejects `HH=24`, `C5` allows `HH=24`. |
| `F1` / `F2` | `where()` | `*#4*<W>*{27\|28}*<bit_idx:0..15>*<B7>##` | 16-bit one-hot bit index across `(B5<<8)\|B6` (`B6: 02→1..80→7`, `B5: 01→8..80→15`, else `0`). |

### 4.5 Internal Decode Tables & Holiday Timer

#### 4.5.1 `mode_hex_to_what` (`0x5390`, used by `D2 04 12`)

| SCS `B5` | OWN `WHAT` | Meaning / Cross-Check | SCS `B5` | OWN `WHAT` | Meaning / Cross-Check |
|---|---:|---|---|---:|---|
| `0x10` | `108` | Heating Comfort (live `F461` pair with DIM 7 `1*3`) | `0x1E` | `410` | Auto-context Manual (`4xx` series) |
| `0x11` | `107` | Heating Eco (live `F461` pair with DIM 7 `1*4`) | `0x1F` | `508` | `5xx` series Comfort |
| `0x12` | `208` | Cooling Comfort (live `F461` pair with DIM 7 `2*3`) | `0x20` | `507` | `5xx` series Eco |
| `0x13` | `207` | Cooling Eco (pairs with DIM 7 `2*4`) | `0x21` | `502` | `5xx` series Protection |
| `0x1B` | `408` | Auto-context Comfort (`4xx` series) | `0x22` | `510` | `5xx` series Manual |
| `0x1C` | `407` | Auto-context Eco (`4xx` series) | `0xF0` / `0xF1` | `1` / `0` | Heating / Conditioning |
| `0x1D` | `402` | Auto-context Protection (`4xx` series) | `0xF2` / `0xF3` / `0xFF` | `102` / `202` / `303` | Antifreeze / Thermal Prot. / OFF |
| *other* | **`-1`** | Unchecked -> emits `*4*-1*<Z>##` (`QUIRK-02`) | | | |

#### 4.5.2 `offset_hex_to_str` (`0x54c0`, used by `D2 04 0D` -> `DIM 13`)

| SCS `B5` | `DIM 13` | SCS `B5` | `DIM 13` | SCS `B5` | `DIM 13` |
|---|---|---|---|---|---|
| `0x00` | `"00"` (`0°C`) | `0xE2` | `"13"` (`-3°C`) | `0x82` | `"7"` *(non-sequential!)* |
| `0x0A` | `"01"` (`+1°C`) | `0xEC` | `"12"` (`-2°C`) | `0x85` | `"8"` *(non-sequential!)* |
| `0x14` | `"02"` (`+2°C`) | `0xF6` | `"11"` (`-1°C`) | `0x86` | `"6"` (local override) |
| `0x1E` | `"03"` (`+3°C`) | `0x80` / `0x81` | `"4"` (local OFF) / `"5"` (local prot.) | *other* | `NULL` (drops frame) |

#### 4.5.3 `mode_hex_to_dim7` (`0x58b0`, used by `D1 02 05` & `D2 04 05`)

| SCS `B5` | `"M*F"` | SCS `B5` | `"M*F"` | SCS `B5` | `"M*F"` |
|---|---|---|---|---|---|
| `0x10` / `0x11` | `"1*3"` / `"1*4"` | `0x18` / `0x19` / `0x1A` | `"0*3"` / `"0*4"` / `"0*1"` | `0xF4` / `0xFF` | `"0*2"` / `"0*5"` |
| `0x12` / `0x13` | `"2*3"` / `"2*4"` | `0xF0` / `0xF1` | `"1*0"` / `"2*0"` | `0xF9` / `0xFA` | `"1*31"` / `"1*32"` |
| `0x14` / `0x15` / `0x16` / `0x17` | `"3*3"` / `"3*4"` / `"3*2"` / `"3*0"` | `0xF2` / `0xF3` | `"1*2"` / `"2*2"` | *other* | **`"\n"`** (`QUIRK-04`) |

#### 4.5.4 Holiday-End Synthetic Emitter (`0x5f50..0x61b8`)
Invoked ONLY from `D1 02 C3` (`0xbfc8`) and `D1 02 C4` (`0xc078`) when `B1 == "00"` (`#0` single CU) and `day >= 0`, `hour >= 0`, `holiday_what != -1`. Computes `days = (mktime(year, month, day, 0, 0, 0) - today_midnight) / 86400 + 1` (ignoring `hour`/`min`), emits `*4*<(holiday_what/1000)*1000 + days>*#0##` directly to `monitor_fd`, and resets globals to `-1`. While armed (`g_timeout > 0`), `D2 04 C0` suppresses `13DDD`/`23DDD` reports (`0xd0e0`, `0xcee4`).

---

## 5. Cross-Generation Behavioral Deltas

1. **Gen A (`H4684/020054`, `MH200/020102`) temperature truncation (`QUIRK-11`):** `DIM 0` formats temperatures with `*#4*%d*0*0%d##` and `*#4*%d%02d*0*0%d##` (`MH200:0xda90..daf0`), emitting 2–3 digit strings (`00`, `050`, `095`) for `0.0..9.9 °C` instead of 4 digits (`0000`, `0050`, `0095`).
2. **Gen B (`F453/010100`, `F453AV/030014`, `MH200N/010108`) `*#4*0##` non-broadcast bug (`QUIRK-15`):** Only Gen B forwards `*#4*0##` — because its `is_plain_zone` check omits `zone > 0`. It transmits point-to-point `$04 99 00 30 {00,12,0E,0D}` to non-existent zone `0` (confirmed in `thermo-four-zone-central.tsv:41`, `trace-corpus.tsv:52`, and physical `F453`/`MH200N`/`F453AV` captures yielding 0 replies and 1.3–8.8 s stalls). Gen B also omits `is_plain_zone` and `<= 3` validation on `DIM 11` write (`thermo-fan-speed.tsv`), and `F453/010100` lacks the holiday timer (`0x5f50`) added in `F453AV`/`MH200N`.
3. **Gen C (`F450/020010`, `F454/020051`, `F459/020105`):** Adds `DIM 2..8, 11, 21, 27, 28`, plain-zone `*#4*Z*20##` (`$04 A9 ZZ 30 10`), and inbound `D1 02` write echoes for `#2, #3, #4, #5, #7, #8#, #11, #17`. Only **`F459/020105`** supports `DIM 60` humidity (`thermo-zone-requests.tsv:18, 30, 38`). Unlike Gen D, Gen C `D2 04 C2` manual reports emit only 1 frame (`*4*110*#Z#C##`, omitting `*#4*#Z#C*14*...##`, `thermo-four-zone-central.tsv:81`), and `DIM 14` read still enforces `is_plain_zone` (rejecting `*#4*#0#1*14##`, `thermo-four-zone-central.tsv:10`).
4. **Gen C-coso (`MH202/010024`):** Independent GLib plugin (`libplug_thermo_tr.so` + `libthermo.so`). Never crashes on `DIM 22` (`thermo-dim22.tsv`), rejects `*#4*#0*20##` and `*#4*Z*20##` (`thermo-actuator.tsv`), accepts `*#4*#Z*#11*1##` (misaddressing `00`, `ownd-pr82-heating.tsv`), and crashes (`SIGSEGV`) on `*#4*Z#N##`, `*#4*Z#4#II##`, and `*#4*001..099*` (`CRASH-06`).
5. **Gen D (`MyHomeServer1/028206`, `3488/030205`, `F460/020012`, `F461/020011`):** Identical WHO 4 translation core across all four models. `MyHomeServer1/028206` retains compiled-in `WHO 1004` (`open2scs diagnostica`) and `sendemail` support, whereas `3488/030205`, `F460/020012`, and `F461/020011` stripped `WHO 1004` out (leaving dead code behind `chi != "4"` guards).

---

## 6. Verified Firmware Crashes (`CRASH-01..06`) & Protocol Quirks (`QUIRK-01..15`)

### 6.1 Crash Vectors

| ID | Affected Gateways | Trigger Frame / Bus Input | Root Cause (`F461/020011` / `MH202`) | Signal |
|---|---|---|---|---|
| **`CRASH-01`** | Gen B, C, D (`bt_termo`) | Any WHO 4 frame with bare `WHERE == "#"` (e.g. `*#4*###`, `*4*1*###`) | `ThermoWhereTransl::scsDest` (`libopentrsl:0x15618`) -> `OpenMsg::where()` dereferences empty `std::vector` (`begin == NULL`) at `0x96f0`. | `SIGSEGV` |
| **`CRASH-02`** | Gen B, C, D (`bt_termo`, 10 gateways) | `*#4*<W>*22##` or `*#4*<W>*#22*...##` where `<W>` has no `'#'` (e.g. `0`, `#0`, `2`) | Pre-rewrite at `0x6f60..0x70f0` produces `"#"`, triggering `CRASH-01` (`thermo-dim22.tsv`). | `SIGSEGV` |
| **`CRASH-03`** | Gen D (`bt_termo`; check B/C) | Any WHO 4 dim write with `strlen(V0) >= 5` (e.g. `*#4*1*#14*00200*1##`), OR any WHO 4 frame with `strlen(WHERE) >= 10` | Unconditional `__strcpy_chk(..., 5)` on `Extract_valori(0)` at `0x7c54` and `__strcpy_chk(..., 10)` on `Extract_dove()` at `0x6efc`. | `SIGABRT` |
| **`CRASH-04`** | Gen D (`bt_termo`) | `*#4*Z*#14*0200##` (`< 2` args) OR `*#4*Z*#7*{0..3}*1##` (`< 3` args) | Missing `num_valori` guard before `OpenMsg::whatArgN()` throws uncaught `std::runtime_error` (`0x94cc..0x9984`, `0x90d8..0x9780`). | `SIGABRT` |
| **`CRASH-05`** | Gen D (`bt_termo`, bus-side) | Inbound `$18 D2 ... 04 C1 {09\|19\|29} <B6> <B7>` with `B7 >= 0x64` (`100..255`) | `__sprintf_chk` writes 13 bytes (`"112#%04u#%02u"`) into 12-byte stack buffer `[sp+0x78]` (`0xdb38..0xdba8`). | `SIGABRT` |
| **`CRASH-06`** | `MH202/010024` (`coso`) | `*#4*Z#N##`, `*#4*Z#4#II##`, `*#4*001..099*...##` | NULL dereference in `libplug_thermo_tr.so` (`thermo-zone-requests.tsv`, `audit-crash-frames.md`). | `SIGSEGV` |

### 6.2 Protocol Quirks & Firmware Bugs

- **`QUIRK-01` (`D1 02 F0` negative `#0` filter):** `0xbb38` checks `strtol(B5, 16) <= 1`, rejecting all negative `#0` temperature write echoes (`B5 = "10".."11"`).
- **`QUIRK-02` (`D2 04 12` unchecked `-1`):** `0xadcc` formats `-1` from `0x5390` directly into `*4*-1*<Z>##`.
- **`QUIRK-03` (`DIM 19` swapped bytes & empty fields):** `B6` -> `CV` (1st), `B5` -> `HV` (2nd); unmapped bytes leave empty `""` (`*#4*Z*19**1##`, `*#4*Z*19*0*##`).
- **`QUIRK-04` (`DIM 7` heap leak, `\n` emission, uninitialized read):** `0x58b0` and `0x55f0` leak `malloc(5)` on every call; unmapped reads emit literal `\n` inside `*#4*W*7*\n##`; unmapped writes fall into 4-char hex parser at `0x9184` reading 2 uninitialized heap bytes.
- **`QUIRK-05` (`DIM 0` leading-zero 8-probe scan):** `*#4*01*0##` starts with `'0'`, entering `0x6cd0(1)` (8-probe scan `$0499013{1..8}00`) instead of single-zone read.
- **`QUIRK-06` (`DIM 22` nibble `0xF` asymmetry & swing bit inversion):** `hi(B5)` swing is inverted (`0→*1, 1→*0`); nibble `0xF` emits `*15` on `D1 02 16` (`#22`) vs empty `*` on `D2 04 16` (`22`).
- **`QUIRK-07` (`DIM 14` central leading-zero requirement & `DIM 14` read leak):** Central `#0`/`#Z` `#14` writes require `V0[0] == '0'` (`0x9504`). Gen D `DIM 14` read has no WHERE check, so `*#4*#0#C*14##` polls plain zone `C` (`$0499{C}300E`).
- **`QUIRK-08` (8-reply hard cap in `0x63a0` `mode ∈ {1, 6, 9}`):** `*#4*0#0*20##` and `*#4*0*60##` return `*#*1##` after reply #8 (`0x6844`); replies `9..N` still arrive on the bus afterward (event session, or bleeding into an immediate next command).
- **`QUIRK-09` (`0x63a0` WHERE-bypass for `DIM ∈ {2, 3, 18, 30, 31, 32}`):** `0x67a8` accepts any zone's reply matching `expected_dim` without verifying `got_where`.
- **`QUIRK-10` (`D2 04 C1` multi-CU `-1` off-by-one):** Subtracts `1` on `#0#C` for `11/21/01/13/23/03` (`B6==0` emits `1100/2100/1200/2200`), whereas `D2 04 C0` never subtracts `1`.
- **`QUIRK-11` (Gen A `0%d` sub-10 °C format):** `H4684`/`MH200` emit 2–3 digit temps (`00`, `085`) below `10.0 °C`.
- **`QUIRK-12` (Signed/truncated bounds & bare `4001`/`4002`):** Signed `<=` lets negative values pass on `#4/#5/#11/#7/#18` writes and `4001#Z`/`4002#Z`; `& 0xFF` before compare lets `256` pass on `DIM 18` read and `#21/#29` writes; bare `4001`/`4002` without `#Z` bypasses the `0#1..0#9` check.
- **`QUIRK-13` (`#30/#31/#32` blocking `sleep(1)`):** Each holiday/timed-manual date/time write blocks the single-threaded `bt_termo` loop for 1 full second before transmitting.
- **`QUIRK-14` (`D1 02 C3/C4/C5` missing `*#*1##` sentinel):** Causes 7 redundant 0-byte writes to `monitor_fd` on every holiday/timed-manual echo.
- **`QUIRK-15` (Gen B `*#4*0##` non-broadcast zone-0 poll):** `F453/010100`, `F453AV/030014`, and `MH200N/010108` forward `*#4*0##` only because `is_plain_zone` lacks `where > 0`; it sends point-to-point `$04 99 00 30 {00,12,0E,0D}` to non-existent zone `0` (`thermo-four-zone-central.tsv:41`) and stalls with zero replies on physical plants.

---

## 7. Consumer Implementation Rules (Quick Checklist)

For OpenWebNet client libraries (`OWNd`) and Home Assistant integrations (`MyHOME`):

1. **Block all crash frames before send:** Enforce guards for `CRASH-01` (`WHERE == "#"`), `CRASH-02` (`DIM 22` non-`3#Z#N`), `CRASH-03` (`len(V0) >= 5` on dim writes, `len(WHERE) >= 10`), `CRASH-04` (`#14` with `< 2` args, `#7*{0..3}*1` with `< 3` args), and `CRASH-06` (`MH202` `*#4*Z#N##`, `*#4*Z#4#II##`, `*#4*001..099*`).
2. **Never treat `*#4*0##` as a working general sweep on Gen B (`F453`, `F453AV`, `MH200N`):** It polls zone `0`, not all zones (`QUIRK-15`). Use `*#4*0#0*20##` (supported on Gen B/C/D, not Gen A or `MH202`) + per-zone reads instead.
3. **Decode single-side `DIM 19` independently:** Treat an empty field (`*#4*Z*19**1##`, `*#4*Z*19*0*##`) as absent on that side rather than discarding both sides (`QUIRK-03`).
4. **Parse all Gen C/D emitted `WHAT` & `DIM` values:** Include `107/108/207/208`, `402/407/408/410`, `502/507/508/510`, `104/204/304`, `1100/2100/1200/2200` + programs `1..99`, `4001#Z`/`4002#Z`/`4002`, `39..45`, `DIM 7` states `0/31/32`, `DIM 13` codes `6/7/8`, `112#/212#/312#` duration `#HH`, Gen A 2–3 digit `0xx` temps (`QUIRK-11`), and inbound Gen C/D `#DIM` write echoes (`#14`, `#11`, `#7`, `#20`).
5. **Respect `#0`-only and `C1`-only central command rules:** `115/215/315`, `3000/3001`, and `13xxx/23xxx/33xxx` require `WHERE == "#0"` exact on `bt_termo` (`#0#C` always NACKs); `112/212/312#T#M` always emits `C1` (central unit, not `C2` zone).
6. **Pace after `*#4*0#0*20##` and `*#4*0*60##` when `>= 8` replies arrive:** Allow late bus replies `9..N` to drain to the monitor session before issuing the next WHO 4 command (`QUIRK-08`).
7. **Never zero-pad 2-digit zone reads on `DIM 0`:** Send `*#4*1*0##`, never `*#4*01*0##` (`QUIRK-05`).
8. **Gate `WHO 1004` queries by generation:** Active on Gen A, hooks present on Gen B/C/`MyHomeServer1`, dead code on `3488`/`F460`/`F461`, absent on `MH202`.
