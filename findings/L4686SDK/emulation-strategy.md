# L4686SDK (012001) Bare-Metal Microcontroller Emulation Strategy

## Hardware & Firmware Architecture
- **Firmware Package**: `4686SDK_012001.fwz` (63,169 bytes, SHA-256 `381b75d99ab0b6ef91fe2fd1105685f97da65aa7f695d78b48e21b8f9518bd23`).
- **Unpacked Artifacts**:
  - `4686SDK_012001.HEX` (170,921 bytes, Intel HEX format, SHA-256 `9a0ab0e45c3172735f34b4f8be1770c11dd3108bc70a1101c4496b95fdc01d3c`).
  - `info.txt` (359 bytes, SHA-256 `79fb80148c574fb2d3808f749c63770c1fb337f9f100d3e7854852b3427066d5`).
- **Archive Format**: Unencrypted standard ZIP container (`password_scheme: none`).
- **Microcontroller Hardware**:
  - Microchip PIC18 family (8-bit Harvard architecture, `[TYPE]=FILE MICROCHIP`, `OPEN_SCS` signature).
  - Decoded flash footprint: 60,545 bytes across bootloader (`0x00000000`–`0x00000699`), application (`0x000007EC`–`0x0000EDDD`), factory ID (`0x0000FD00`–`0x0000FD36`), and PIC18 configuration words (`0x00300001`–`0x0030000D`).
- **Physical Device Function**:
  - Host-powered RS-232 (`L4686SDK`) and USB (`L4686/USB`, `3559`) OpenWebNet-to-SCS serial translator.
  - Exposes a virtual COM port (via FTDI FT232R USB-to-serial bridge on the USB hardware revision) speaking ASCII OpenWebNet (`*...##`) on the host side and 2-wire SCS bus telegrams on the plant side.

## Boundary & Emulation Cut
1. **Absence of Linux Userland and TCP/IP Stack**:
   - Unlike the Linux gateways (`MH200N`, `F453`, `F453AV`, `F454`, `MyHomeServer1`, etc.), `L4686SDK` is a single-chip PIC18 microcontroller with no operating system, no ELF binaries, and no TCP/IP stack (no port 20000 `openserver` command/event session multiplexer).
   - QEMU has no Microchip PIC18 target (`qemu-user` executes Linux ELF binaries).
2. **Static Table Extraction vs. Emulator Matrix**:
   - The `4686SDK_012001.HEX` ROM embeds explicit OpenWebNet-to-SCS lookup tables at `0xE540`–`0xEDDD` (documented in [`findings/L4686SDK/README.md`](README.md)), directly confirming the WHO `0, 1, 2, 4, 5, 8, 13, 14, 15, 16, 18, 22, 25` and `1001..1022` mappings in hardware ROM.
3. **Fleet Status & Catalog Boundary**:
   - `L4686SDK` (`012001`) is catalogued with full cryptographic and unpack manifest provenance in `catalog/L4686SDK/012001.yaml` and `results/L4686SDK/012001/manifest.tsv`.
   - In `results/mcp_index.json`, `L4686SDK` is recorded with `status: "catalogued"`, empty `suites: []`, and empty `target_sha256: ""` alongside the other non-Linux microcontroller targets (`F455`, `MH201`, `L4561N`, `3550`).
