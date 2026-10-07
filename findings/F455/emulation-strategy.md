# F455 (010102) Bare-Metal Microcontroller Emulation Strategy

## Hardware & Firmware Architecture
- **Firmware Image**: `F455_1_1_2.fwz` (SHA-256 `cd99299fc729679b4eade46acda7bd4f5e619a0a9eac34a1f6a3b73b625a18f9`).
- **Unpacked Artifact**: `F455_1_1_2.bin` (301,016 bytes, raw binary payload, SHA-256 `6d61c308888ccd78f6fb289cf23e672203921a50931b838533a7a315c5d49ccc`).
- **Execution Model**: Unlike the Linux-based gateways (`MH200N`, `MyHomeServer1`, `F454`, `MH202`, `F453AV`, `F459`, `F460`, `F461`), the F455 runs as a bare-metal microcontroller (ARM Cortex-M core). It lacks an MMU, Linux kernel, and ELF dynamic linker userland.

## Boundary & Emulation Cut
1. **Linux Process Sandboxing (`qemu-arm`)**: Cannot run `F455_1_1_2.bin` via `qemu-arm` userland runner because there are no ELF headers, syscall tables, or POSIX libc environments.
2. **System-Level Emulation (`qemu-system-arm`)**:
   - Requires memory-map definition (vector table at `0x00000000` or flash offset `0x08000000`).
   - Requires mock peripheral drivers for internal SCS transceiver UART, flash controller, and timer interrupts.
3. **Phase 2 Status**:
   - `F455` (010102) remains catalogued and verified with full image provenance in `catalog/F455/010102.yaml` and `results/F455/010102/manifest.tsv`.
   - In `results/mcp_index.json`, F455 is explicitly tagged as `pending_emulation` until a full bare-metal Cortex-M board model is integrated.
