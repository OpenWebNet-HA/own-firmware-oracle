# Inbound PIC / RPMsg Serial Framing Protocol (`scsserver`)

## Summary

Phase 0 binary reverse engineering and live QEMU closed-loop execution (`tools/pic_spike.py`) across `F461/020011` (AArch64 / RPMsg 3-digit PIC protocol) and `MyHomeServer1/028206` (ARM32 / UART 2-digit PIC protocol) established the exact outbound-to-inbound PIC framing, TX-outcome handshakes, and read-buffer pacing rules required for deterministic closed-loop plant simulation.

---

## 1. 3-Digit RPMsg PIC Protocol (`F460/020012`, `F461/020011`)

### Binary Evidence (`f461/home/bticino/bin/scsserver`)

- **`Richiesta_Invio_Frame` (`0x35d0..0x3858`)**:
  - Initializes `esito_tx[3] = -1` (`0x35f0: mvn w0, wzr`).
  - Transmits the outbound PIC frame (`$003<8hex>\r`, `$004<8hex>\r`, or `$006<16hex>\r`) to `/dev/ttyRPMSG0`.
  - Enters a `select()` + `read_data()` (`0x4f10`, 1 byte per `read()` syscall) + `parsing_serial_rx()` (`0x41c0`) loop (`0x37b0..0x380c`).
  - Any inbound device bus telegrams (`$017<8hex>\r` short `cmd=17`, `$018<16hex>\r` extended `cmd=18`) arriving before or after the TX outcome are immediately forwarded by `0x46d0` to connected translator daemons (`bt_luci`, `coso`) as `*02*01*$017<8hex>#` or `*02*02*$018<16hex>#`.
  - The loop terminates when `cmd` is in `{1, 7, 8}` (`0x37f8..0x380c`), and `Richiesta_Invio_Frame` sets `send_esito = 1` (`*01*01#` = `Client SCS: ACK PIC`) if and only if `esito_tx[3] == 0` (`0x3810: cmp w1, #0; cset w20, eq`).
- **`parsing_serial_rx` (`0x41c0..0x46c0`)**:
  - `cmd == 8` (`$008<XX>\r`, `0x43a0..0x43c0`): parses `sscanf(buf, "08%02d", &esito_tx[3])`. Sending **`$00800\r`** sets `esito_tx[3] = 0` (`ACK PIC`).
  - `cmd == 19` (`$019\r`) / `cmd == 0` (`$000\r`): leaves `esito_tx[3] == -1`, causing `Richiesta_Invio_Frame` to time out after 3 retries and return `*01*00#` (`NAK PIC`), which accurately models an unpopulated/unacknowledged bus address. The plain `pic` responder of the `full` suites always answers this way, so their `nack`/`out` rows on F460/F461/MyHomeServer1 are empty-bus outcomes ([`empty-bus-reply-semantics.md`](empty-bus-reply-semantics.md)).
  - `cmd == 13` (`$013\r`): sets `tst_rst = 1` (MCU reset) — **must never be sent during normal operation**, as it triggers `Configure_MCU` re-initialization and drops translator sockets.

### Inbound Device Telegram Framing (3-Digit)

| Telegram Class | Outbound PIC Frame | TX Success ACK | Inbound Device Frame | Forwarded to Translator |
|---|---|---|---|---|
| Short SCS (4 B) | `$003<B0><B1><B2><B3>\r` | `$00800\r` | `$017<B0><B1><B2><B3>\r` | `*02*01*$017<B0><B1><B2><B3>#` |
| Thermo / Group (4 B) | `$004<B0><B1><B2><B3>\r` | `$00800\r` | `$017<B0><B1><B2><B3>\r` | `*02*01*$017<B0><B1><B2><B3>#` |
| Extended SCS (8 B) | `$006<B0..B7>\r` | `$00800\r` | `$018<B0..B7>\r` | `*02*02*$018<B0..B7>#` |

---

## 2. 2-Digit UART PIC Protocol (`MyHomeServer1/028206`, `MH200N/010108`, `H4684/020054`)

### Binary Evidence (`myhomeserver1/home/bticino/bin/scsserver`)

- **PIC Firmware Version Mode Switch (`0x4cc0..0x4da4`)**:
  - During `Configure_MCU`, `scsserver` sends `$24\r` and parses `$25%02d%02d%02d` into `(hwrev, fwrev, fwrel)`.
  - At `0x4d68..0x4d80`:
    - If `fwrev <= 27` (e.g. `010108` where `fwrev = 1`), `[0x17180]` is set to `2` (legacy PIC mode: `$19\r` acknowledges `$03`, `$00\r` acknowledges `$04`/`$06`).
    - If `fwrev > 27` and `fwrel > 0` (e.g. `MyHomeServer1` `028206` where `fwrev = 82, fwrel = 6`), `[0x17180]` is set to `8` (v8 PIC mode).
  - In `Write()` (`0x38f0..0x3c38`):
    - At `0x3b4c..0x3b5c` and `0x3c1c..0x3c2c`, when `[0x17180] == 8`, `Write()` rejects `$19\r` (`r4 = 4`) and `$00\r` (`r4 = 2`) and requires **`$08\r`** (`r4 = 8`) to return `1` (`*01*01#` = `Client SCS: ACK PIC`).
- **20-Byte `read()` Framing Constraint (`Read()` at `0x29f4..0x2cdc`)**:
  - Unlike `f461/scsserver` (which reads 1 byte at a time), `mhs1/scsserver` zeros a 21-byte buffer and executes `read(fd, buf, 20)` (`0x2a60: mov r2, #20; bl read`).
  - It extracts only the first `$`-prefixed frame up to the first `\n` (`0x0a`, translated from `\r` by termios `ICRNL`) and discards any trailing bytes in that 20-byte `read()` buffer on the next call.
  - Therefore, `PicResponder` paces 2-digit device bus frames (`$17...` / `$18...`) by `25.0 ms` via `tick(now_ms)` after sending the immediate 4-byte PIC TX-ACK (`$08\r` / `$19\r` / `$00\r`), ensuring each frame is read in its own `read()` syscall.

---

## 3. Empirical Spike Verification (`tools/pic_spike.py`)

Both `F461/020011` and `MyHomeServer1/028206` produced 7/7 `ack` (`verdict=out`) with identical OpenWebNet `cmd:` and `evt:` translations:

| Input Frame | Outbound Bus Payload | Inbound Device Payload | `reply` | `cmd:` | `evt:` |
|---|---|---|---|---|---|
| `*1*1*06##` | `0306001200` | `17B8061200` | `ack` | — | `*1*1*06##` |
| `*#1*06##` | `0306001500` | `17B8061200` | `ack` | `*1*1*06##` | `*1*1*06##` |
| `*#1*08*#1*150*0##` | `06D10801420D0D3200` | `18D20801440D0D3200` | `ack` | — | `*#1*08*1*150*0##` |
| `*#1*08*1##` | `06D10801430D0D0000` | `18D20801440D0D3200` | `ack` | `*#1*08*1*150*0##` | `*#1*08*1*150*0##` |
| `*2*1*05##` | `030500120A`, `0305001208` | `17B805120A`, `17B8051208` | `ack` | — | `*2*1000#0*05##`, `*2*0*05##`, `*2*1000#1*05##`, `*2*1*05##` |
| `*#4*2*0##` | `0499023000` | `17B40230F9` | `ack` | `*#4*2*0*0249##` | `*#4*2*0*0249##` |
| `*#18*51*113##` | `06D1A10233001D0000` | `18D2A10234001D01C2` | `ack` | `*#18*51*113*450##` | `*#18*51*113*450##` |
