# Findings: L4686SDK (OpenWebNet / SCS USB & RS-232 Interface)

Conclusions and static facts for the BTicino `L4686SDK` / `L4686/USB` (also marketed as `N4686SDK`, `NT4686SDK`, `3559`, Legrand `03559` / `088230`) OpenWebNet USB & RS-232 serial bus interface, firmware 1.20.1 (`4686SDK_012001.fwz`).

Catalog entry: [`catalog/L4686SDK/012001.yaml`](../../catalog/L4686SDK/012001.yaml).  
Manifest: [`results/L4686SDK/012001/manifest.tsv`](../../results/L4686SDK/012001/manifest.tsv).  
Emulation boundary: [`findings/L4686SDK/emulation-strategy.md`](emulation-strategy.md).

## Provenance

| Layer | Name | Size | SHA-256 |
| --- | --- | --- | --- |
| Public download (`homesystems-legrandgroup.com` `fileId=58107.23188.24810.50670` & bundled in `MyHOME_Suite_030538.exe`) | `4686SDK_012001.fwz` (unencrypted ZIP) | 63,169 | `381b75d99ab0b6ef91fe2fd1105685f97da65aa7f695d78b48e21b8f9518bd23` |
| Metadata manifest | `info.txt` | 359 | `79fb80148c574fb2d3808f749c63770c1fb337f9f100d3e7854852b3427066d5` |
| Flash image | `4686SDK_012001.HEX` (Microchip PIC18 Intel HEX) | 170,921 | `9a0ab0e45c3172735f34b4f8be1770c11dd3108bc70a1101c4496b95fdc01d3c` |

Vendor metadata (`info.txt`) identifies the package as:
- `[NOME]=FW INTERFACCIA OPEN-SCS`
- `[ARTICOLO1]=4686SDK`
- `[TYPE]=FILE MICROCHIP`
- Release notes (`[NOTE]`) document four protocol additions in this revision:
  1. Extended addressing on WHO 0, WHO 1, and WHO 2;
  2. Split IR management on WHO 4 (Thermoregulation);
  3. Energy Management on WHO 18;
  4. CEN+ / Dry Contact & IR scenario system on WHO 25.

## Static Facts (Firmware 1.20.1)

Verified by decoding the Intel HEX records (`60,545` populated bytes); no disassembly or vendor binary bytes are redistributed.

1. **Memory Map (Microchip PIC18F6622 / PIC18F6627 class, 64 KB Flash)**:
   - `0x00000000`–`0x00000699` (1,626 bytes): Bootloader with hardware reset (`0x0000`), high-priority interrupt (`0x0008`), and low-priority interrupt (`0x0018`) vectors, plus bootloader header string at `0x0020` (`OPEN_SCS\0` `103` `01.04.20.04`).
   - `0x000007EC`–`0x0000EDDD` (58,807 bytes): Main `OPEN_SCS` application firmware with relocated vectors at `0x0800` (entry `0xDF80`), `0x0808` (high ISR `0xE0F2`), and `0x0818` (low ISR `0xE146`), and application header at `0x0820` (`OPEN_SCS\0` `1P3    \0`).
   - `0x0000FD00`–`0x0000FD36` (55 bytes): Factory device signature (`OPEN_SCS`).
   - `0x00300001`–`0x0030000D` (10 bytes): PIC18 configuration words (`CONFIG1H..CONFIG7H`).

2. **Direct OpenWebNet-to-SCS Protocol Translation in ROM (`0xE540`–`0xEDDD`)**:
   Unlike the internal PIC coprocessors on Linux gateways (which exchange `$`-framed hex line telegrams over `/dev/ttyPIC` with `scsserver`), the `L4686SDK` parses and emits **ASCII OpenWebNet frames** (`*...##`) directly on its host UART (bridged to USB virtual COM via FTDI FT232R on `L4686/USB`):
   - **OpenWebNet Frame Literals (`0xE8EA`–`0xE90F`)**:
     - `*#*1##` (ACK) and `*#*0##` (NACK)
     - `*#1004*#0*23##` (WHO 1004 thermoregulation diagnostic probe)
     - `*13*22##` (WHO 13 date/time request)
   - **Supported WHO Subsystems (`0xE564`, `0xEC29`, `0xEC5A`)**:
     - Standard subsystems: WHO `0` (Scenarios), WHO `1` (Lighting), WHO `2` (Automation), WHO `4` (Thermoregulation), WHO `5` (Burglar Alarm), WHO `8` (Video Door Entry), WHO `13` (Gateway/Device Management), WHO `14` (Light+Shutters Actuator Lock), WHO `15` (CEN), WHO `16` (Sound System), WHO `18` (Energy Management), WHO `22` (Sound System v2), and WHO `25` (CEN+ / Dry Contacts).
     - Diagnostic subsystems (`0xEC29`): `1001` (`0x03E9`), `1004` (`0x03EC`), `1005` (`0x03ED`), `1008` (`0x03F0`), `1013` (`0x03F5`), `1018` (`0x03FA`), `1022` (`0x03FE`).
   - **WHO 4 Thermoregulation WHAT Table (`0xE876`–`0xE8E7`)**:
     - 18-entry lookup table mapping SCS mode bytes (`0x11`–`0x19` for heating, `0x21`–`0x29` for cooling/conditioning) to 16-bit OpenWebNet WHAT base codes: `102`/`202` (Protection), `103`/`203` (Off), `110`/`210` (Manual), `111`/`211` (Automatic), `112`/`212`, `115`/`215` (Holiday `115#P`/`215#P`), `13000`/`23000` (Multi-day Vacation `13DDD`/`23DDD`), `1100`/`2100` (Weekly Programs `1101..1103`/`2101..2103`), and `1200`/`2200` (Scenarios `1201..1216`/`2201..2216`).
   - **WHO 4 Dimension Table (`0xE950`–`0xE971`)**:
     - Maps thermoregulation dimensions `0` (probe temperature), `11` (fan coil speed), `12` (setpoint), `13` (local offset), `14` (compensated setpoint), `19` (valves status), `20` (actuator status), `22` (split control), and `30` (end date/hour of holiday).
   - **WHO 18 Energy Management Jump Table (`0xE9B1`–`0xEA08`)**:
     - 22-entry handler table (`0x95EE`–`0x9ADE`) translating WHO 18 power/current queries and actuator commands between OpenWebNet ASCII frames and extended SCS energy bus frames.

## Note on the `3578` / `088328` OpenWebNet ZigBee USB Stick

The BTicino `3578` / Legrand `088328` (*Chiavetta USB ZigBee* / *USB Stick for PC*, Telegesis ETRX2USB / Silicon Labs EM250 + CP2102 USB-UART bridge) is the wireless ZigBee counterpart to the wired `L4686/USB`. No standalone `.fwz` firmware update package was ever published on the Legrand Home Systems portal or bundled in `MyHOME_Suite` for the `3578` / `088328` stick; it is therefore recorded in the Excluded Hardware census.
