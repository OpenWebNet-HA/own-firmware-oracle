# Findings: MyHomeServer1

Empirical OpenWebNet protocol conclusions for the **MyHomeServer1** gateway firmware (`028206`, image SHA-256 `aa1cd393e4ae4262b3b2c4f9f25b8e831adbed648975505e2084c801f2b7f5e7`).

All conclusions are derived from reproducible runs under QEMU emulation with simulated SCS bus PTYs, recorded in deterministic TSV files under `results/MyHomeServer1/028206/oracle/full/` and indexed in `results/mcp_index.json`. Facts only — no binaries, disassembly, or decompiled code.

## Findings Index

- [**Touch Screen (TS10) Thermoregulation Protocol Verification**](thermo-ts10.md)
  Empirical execution of 58 thermoregulation test cases (WHO 4) from the BTicino Touch Screen 10" client reference (`libqtdevices` tag `TS10_1_0_23`). Validates compound probe addressing (`#probe#central`), manual/heating setpoints, timed manual and holiday modes with duration encoding, 16 weekly programs/scenarios, and calendar dimensions (30, 31, 32).

- [**Touch Screen (TS10) Sound Diffusion Protocol Verification**](sound-who22.md)
  58 test cases covering WHO 22 volume, tone controls, equalizer presets, source selection, amplifier states, and follow-me matrix routing. Compares MyHomeServer1 (`bt_multi`), F454, and MH200N, demonstrating byte-identical SCS output between MHS1 and F454 for direct volume commands while MH200N strictly enforces its subsystem boundary.

- [**Touch Screen (TS10) Energy & Load Management Verification**](energy-ts10.md)
  65 test cases covering WHO 18 (Energy Management) and WHO 3 (Load Management / F421). Demonstrates that TS10 WHAT commands (`*18*57..`) and classic dimension frames (`*#18*..*511..`) generate identical extended SCS telegrams. Verifies automated push reporting (`DIMENSION 1200`) and Stop&Go differential breaker commands across MyHomeServer1 and MH202.

- [**Touch Screen (TS10) Video Door Entry & Intercom Verification**](intercom-ts10.md)
  59 test cases covering WHO 8 door lock activation, camera cycling, PTZ movements, calling modes, and session management. Proves subsystem boundary isolation across 4 gateways: automation gateways (MyHomeServer1, MH200N, MH202) reject WHO 8, whereas F454 routes WHO 8 to `bt_vct`.
