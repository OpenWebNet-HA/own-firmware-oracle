# Findings: MH200N

Conclusions go here as markdown, each pointing at specific rows in
`results/MH200N/<version>/manifest.tsv` (and later the oracle TSVs). Facts in
our own words only -- no binaries, no disassembly, no decompiled code.

## Findings Index
- [Lighting WHAT 19 / WHO 1001 Autodiagnostics Co-occurrence](what19.md) -- Analysis of the unpublished `WHAT 19` status code, demonstrating that it represents an actuator hardware fault/anomaly rather than an "ON" state, and documenting its co-occurrence with `WHO 1001` Dimension 11 diagnostic masks.
- [Replay of OWNd#77 Audit Fixes on MH200N](ownd-77-replay.md) -- Empirical replay of the 10 protocol and parser defects audited in OWNd#77 across Lighting, Thermoregulation, Energy, and CEN+ subsystems, establishing which behaviors hold universally and which differ due to gateway daemon specialization.

## Status of Investigations
- **WHAT 19 / WHO 1001 DIM 11 fault mask** (see OpenWebNet-HA/MyHOME#593, #611):
  - **Resolved (Gateway translation)**: `bt_luci` translates incoming SCS status byte `'E'` into OpenWebNet `*1*19*WHERE##`. Simultaneously, extended SCS frame telemetry is mapped by `bt_device` / `libdiag.so` into `*#1001*WHERE*11*<bitmask>##`. Verified via oracle test suite `results/MH200N/010108/oracle/full/what19.tsv` and cross-validated with live evidence `EVID-MH200-WHAT19-FAULT`.
  - **Open (Actuator firmware)**: The exact semantic meaning of individual bitmask positions (e.g., bits 6 and 21) is determined by the physical actuator's microcontroller firmware, not the gateway.

