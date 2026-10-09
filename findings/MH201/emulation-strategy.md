# MH201 (030644) Bare-Metal Microcontroller Emulation Strategy

## Hardware & Firmware Architecture
- **Firmware Image**: `MH201_3_6_44.fwz` (212,187 bytes, SHA-256 `8972720b01d17ce9cfa2bcf2e20b55a44e7b349acb56ed52cd8121fdfccf2f63`).
- **Unpacked Artifacts**:
  - `MH201_3_6_44_signed.bin` (501,760 bytes, raw binary payload, SHA-256 `1868f378d3aa46f12cc5987f71d6d5403eceb0e82f6240cb747d1cc250b0851d`).
  - `fwz.xml` (523 bytes, SHA-256 `3f46e448b6f400ee1f2c7f674b38acb1821cfdb81ef026cd6f943f10ac73e922`).
- **Microcontroller Hardware**:
  - STMicroelectronics STM32F217 ARM Cortex-M3 microcontroller (`MH201_217_BTL`).
  - Flash base `0x08000000`, Reset handler `0x080031c1` (Thumb entry `0x080031c0`).
  - Internal SRAM window `0x20000000-0x2001FFFF` (128 KiB), initial Stack Pointer `0x200041a0`.
- **Operating Environment & Stacks**:
  - Runs CMX-RTX (`SERVER: CMX/5.3, UPnP/1.0, Delta4/1.0`) with task dispatchers (`OPEN_SERVICE`, `X_OPEN_SERVICE`, `HTTPD`).
  - Embedded LwIP TCP/IP stack (`lwip_bind`, `lwip_recvfrom`, `lwip_sendto`, `lwip_stats`).
  - Native OpenWebNet listener on TCP port 20000 supporting session frames `*99*0##`, `*99*1##`, `*99*9##`, and `*99*11##`.
  - XML-OpenWebNet service (X_OPEN: `xmlns="http://www.bticino.it/xopen/v1"`).
  - Hotel guest room management scenario engine (`/conf_mh201.bin`, `/conf_room.bin`, room badge management).

## Boundary & Emulation Cut
1. **Userland Emulation (`qemu-arm`)**: Cannot run `MH201_3_6_44_signed.bin` via `qemu-arm` userland process runner because it is a bare-metal image lacking ELF headers, POSIX dynamic linking, and Linux syscall handlers.
2. **System-Level Emulation (`qemu-system-arm`)**:
   - Would require full board simulation (STM32F2xx memory map, NVIC, Ethernet MAC, flash controller, and mock UART for SCS bus transceiver).
   - Because basic lighting, shutter, and scenario command handling is already verified across the 9 emulated Linux gateways, bare-metal MCU emulation remains low priority for the oracle matrix.
3. **Fleet Status**:
   - `MH201` (030644) is catalogued with cryptographic provenance in `catalog/MH201/030644.yaml` and `results/MH201/030644/manifest.tsv`.
   - In `results/mcp_index.json`, MH201 is recorded with `status: "catalogued"`, empty test suites, and empty `target_sha256: ""` (identical to F455).
