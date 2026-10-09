# L4561N (040006) Bare-Metal Microcontroller Emulation Strategy

## Hardware & Firmware Architecture
- **Firmware Package**: `STEREO_CONTROL_BUS_v04.00.06.fwz` (10,538 bytes, SHA-256 `48b571e320295d01e41316810eafca57720e6a02c4f22ac196b057b0c2ca8a2c`).
- **Unpacked Artifacts**:
  - `rca_ir.HEX` (24,021 bytes, Intel HEX format, SHA-256 `31ad11168e2695b5243bae0200c0871218627bf21abfc2ea31dd363560671d8e`).
  - `info.txt` (147 bytes, SHA-256 `9e24ce9ebc9d067c8b9a0465fe6341d4c74d39da1718777474b6feb6c0eb0244`).
- **Encryption Scheme**: Password `L4561N` (matches candidate `$PRODUCT`).
- **Microcontroller Hardware**:
  - Microchip PIC18 family (8-bit Harvard architecture, `[TYPE]=Microchip`).
  - Flash memory footprint: 8,432 bytes mapped across segments `0x00000000`–`0x000046F9`.
  - Interrupt and reset vectors at `0x00000000`, `0x00000008` (high priority), and `0x00000018` (low priority).
  - Hardware configuration words at `0x00300001`–`0x0030000D` and device ID / user IDs at `0x00F00006`.
- **Physical Device Function**:
  - 4-DIN module connecting directly to the SCS 2-wire automation/sound bus.
  - Provides stereo RCA audio inputs and external IR emitter outputs for remote stereo component control.

## Boundary & Emulation Cut
1. **Absence of OpenWebNet IP Interface**:
   - The L4561N is an **SCS bus peripheral**, not an IP gateway.
   - It contains no Ethernet PHY, no TCP/IP stack, and no OpenWebNet listener on port 20000.
   - It cannot produce OpenWebNet `down` command replies or connection banners. Attempting to run it under QEMU userland or as an OpenWebNet IP server is an architectural mismatch.
2. **Virtual Bus Simulation in Oracle**:
   - In the `own-firmware-oracle` architecture, SCS bus devices are simulated on the virtual bus (`oracle/bus.py`).
   - The L4561N's exact bus behavior is implemented by `SoundSourceResponder` (`oracle/bus.py:212-265`), which handles sound source 102 status requests and control telegrams over `/dev/ttyPIC`.
3. **Fleet Status & Catalog Boundary**:
   - `L4561N` (040006) remains catalogued with complete cryptographic and unpack manifest provenance in `catalog/L4561N/040006.yaml` and `results/L4561N/040006/manifest.tsv`.
   - In `results/mcp_index.json`, `L4561N` is recorded with `status: "catalogued"`, empty `suites: []`, and empty `target_sha256: ""` (identical to bare-metal MCU modules `F455` and `MH201`).
