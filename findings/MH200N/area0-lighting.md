# Area 0 Point-to-Point Lighting Addresses (A=0, PL=1..15)

## 1. Executive Summary

When commissioning installations via BTicino **Home + Project** (or modern Eliot tools such as F460), light point actuators can be progressively allocated in Area 0 (`A=0, PL=1..15`).
In issue [#35](https://github.com/OpenWebNet-HA/own-firmware-oracle/issues/35), community member `@peterbeepping` observed on an MH200 installation that:
1. Actuators at `A=0, PL=2` were reported by the gateway or tools as single-digit `WHERE=2`.
2. Sending `*1*1*2##` triggered an unintended **Area 2 broadcast**, switching on all lights in Area 2.
3. Sending standard 4-digit APL notation `*1*1*0002##` resulted in a **NACK**.

To resolve this ambiguity conclusively across the entire BTicino product portfolio, an empirical test suite (`lights-area0.cases`) containing 28 stimulus frames was designed and replayed across all 10 catalogued gateway targets under QEMU user emulation in `own-firmware-oracle`.

### Key Empirical Findings

1. **Two-Digit Syntax for Area 0, PL < 10 (`01`–`09`)**:
   - Firmware parsers across the fleet universally translate `*1*1*02##` to an SCS **Point-to-Point** telegram addressed to device `02` (hex `$0302001200\r` on MH200N/F454/MH202, `$00302001200\r` on F460/F461).
   - In contrast, 4-digit zero-padded forms (`*1*1*0001##`, `*1*1*0002##`) are **strictly NACKed / silent** by MH200N, F460, F461, F454, F459, F453AV, and MyHomeServer1 (only MH202 accepts `0002`).

2. **Four-Digit Syntax for Area 0, PL $\ge$ 10 (`0010`–`0015`)**:
   - For light points 10 through 15 in Area 0, `*1*1*0010##` and `*1*1*0015##` are accepted and translated to SCS Point-to-Point telegrams addressed to `0A` (10) and `0F` (15) (`$030A001200\r` and `$030F001200\r`).
   - A 2-digit form cannot be used for `PL >= 10` because `10` represents Area 1, PL 0 (`A=1, PL=0`).

3. **Single-Digit Syntax (`2`) is Always Area Broadcast**:
   - `*1*1*2##` is universally translated by firmware to an SCS **Area 2 Broadcast** telegram (`$04B3021200\r` / `$004B3021200\r`). It never addresses Point-to-Point `A=0, PL=2`.

---

## 2. Test Matrix and Fleet Parity

All test frames were executed against the active gateway fleet with full process sandboxing, PTY bus capture, and 100% deterministic assertion in `results/mcp_index.json`.

| OpenWebNet Stimulus | Gateway Interpretation | SCS Telegram (Hex / ASCII) | MH200N | F460 / F461 | MyHomeServer1 | MH202 |
|---|---|---|---|---|---|---|
| `*1*1*2##` | **Area 2 Broadcast** | `$04B3021200\r` (`B3 02`) | Out (`B3 02`) | Out (`B3 02`) | Out (`B3 02`) | Out (`B3 02`) |
| `*1*1*02##` | **Point-to-Point (A=0, PL=2)** | `$0302001200\r` (`02 00`) | **ACK (P2P)** | **ACK (P2P)** | **ACK (P2P)** | **ACK (P2P)** |
| `*1*1*0002##` | Syntax Error / Refused | None / Bus NACK (`$0020000\r`) | **NACK** | **NACK** | **NACK** | ACK (`02 00`) |
| `*1*1*0010##` | **Point-to-Point (A=0, PL=10)** | `$030A001200\r` (`0A 00`) | **ACK (P2P)** | **ACK (P2P)** | **ACK (P2P)** | **ACK (P2P)** |
| `*1*1*0015##` | **Point-to-Point (A=0, PL=15)** | `$030F001200\r` (`0F 00`) | **ACK (P2P)** | **ACK (P2P)** | **ACK (P2P)** | **ACK (P2P)** |
| `*1*1*02#4#01##` | **P2P on Sub-bus #4#01** | `$06EC01000002001200\r` | **ACK (P2P)** | **ACK (P2P)** | **ACK (P2P)** | **ACK (P2P)** |
| `*1*1*0010#4#01##` | **P2P on Sub-bus #4#01** | `$06EC0100000A001200\r` | **ACK (P2P)** | **ACK (P2P)** | **ACK (P2P)** | **ACK (P2P)** |

---

## 3. Disassembly & Architecture Analysis

In OpenWebNet firmware daemons (`openserver` and `bt_luci` across MH200N, F454, and F460):
- **Address String Ingestion**: The parser inspects the length and initial characters of the `WHERE` string:
  - Length 1 (`"1"`–`"9"`): Branch to Area Broadcast handler. The single digit is parsed as Area $A \in \{1..9\}$ with broadcast target code `0xB3`.
  - Length 2 (`"01"`–`"09"`): Detected as Area 0 Point-to-Point. The leading `'0'` flags Area 0, and the second character is converted directly to actuator address `0x01` through `0x09`.
  - Length 2 (`"10"`–`"99"`): Parsed as Area $A \in \{1..9\}$ with Point-to-Point `PL` $\in \{0..9\}$.
  - Length 4 (`"0010"`–`"0015"`): Detected as extended Area 0 addressing. The leading `"00"` flags Area 0, and `"10"`..`"15"` is parsed as PL 10..15, mapped to hex addresses `0x0A`..`0x0F`.
  - Length 4 (`"0001"`–`"0009"`): Rejected by default validation logic in `openserver` / `bt_luci` because length 4 addresses are expected to be either Room/Light (`RRLL`) or extended addresses starting with non-zero PL $\ge 10$. Only newer controllers (such as `MH202` `010024`) relaxed this constraint to strip redundant leading zeroes.

---

## 4. Implementation Guidance for OpenWebNet Drivers (OWNd & MyHOME)

### Command Generation
When addressing lighting devices in Area 0:
```python
def format_lighting_where(area: int, pl: int) -> str:
    """Format WHERE string according to BTicino firmware parsing rules."""
    if area == 0:
        if 1 <= pl <= 9:
            return f"0{pl}"      # Must be 2 digits: "01".."09"
        elif 10 <= pl <= 15:
            return f"00{pl}"     # Must be 4 digits: "0010".."0015"
        else:
            raise ValueError(f"Invalid PL {pl} for Area 0")
    elif 1 <= area <= 9:
        if 0 <= pl <= 9:
            return f"{area}{pl}" # 2 digits: "10".."99"
        elif 10 <= pl <= 15:
            return f"0{area}{pl}"# 4 digits: "0110".."0915"
```

### Event Ingestion
When parsing incoming OpenWebNet frames from the bus:
- Length 1 (`"1"`..`"9"`): Treat strictly as **Area Broadcast** (`area=int(s), pl=None`).
- Length 2 starting with `'0'` (`"01"`..`"09"`): Treat as **Point-to-Point** (`area=0, pl=int(s[1])`).
- Length 4 starting with `"00"` (`"0010"`..`"0015"`): Treat as **Point-to-Point** (`area=0, pl=int(s[2:])`).
- Note on gateway bugs: If a physical device or legacy gateway misreports status on the event session as single digit `"2"`, correlate against the configured entity registry to verify if an Area 0 light entity is registered with that address before interpreting as an area broadcast.
