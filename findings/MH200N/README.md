# Findings: MH200N

Conclusions go here as markdown, each pointing at specific rows in
`results/MH200N/<version>/manifest.tsv` (and later the oracle TSVs). Facts in
our own words only -- no binaries, no disassembly, no decompiled code.

## Open questions (phase 2 targets)
- **WHAT 19 / WHO 1001 DIM 11 fault mask** (see OpenWebNet-HA/MyHOME#593, #611).
  Which bus frame makes `bt_luci` / `bt_device` emit `*1*19*WHERE##`, and what
  does each DIM 11 mask bit mean? Cross-check live on the MH200.
