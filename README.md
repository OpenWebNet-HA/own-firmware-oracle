# own-firmware-oracle

A reproducible pipeline that turns a gateway firmware image into **facts** about
the OpenWebNet protocol — frame formats, code tables, behaviour — for
interoperability with [OWNd](https://github.com/OpenWebNet-HA/OWNd) and
[MyHOME](https://github.com/OpenWebNet-HA/MyHOME).

It grew out of [discussion #613](https://github.com/orgs/OpenWebNet-HA/discussions/613)
and @gdluck's firmware-as-oracle work in
[OWNd#77](https://github.com/OpenWebNet-HA/OWNd/pull/77).

## Why this exists: three goals

The firmware is the only source that says what a gateway **actually does**
with a frame. The manuals are sometimes wrong, community captures cover only
the plants we happen to have, and OWNd / MyHOME have so far been judged against
both. The oracle adds a third kind of evidence: *this firmware image, given this
input, answers X and puts Y on the bus*, reproducible by anyone.

The pipeline is a means. These are the ends:

| # | Goal | Consumer |
|---|---|---|
| 1 | [Improve the Encyclopedia](#1-improve-the-encyclopedia) | [OpenWebNet-Encyclopedia](https://github.com/OpenWebNet-HA/OpenWebNet-Encyclopedia) |
| 2 | [Improve openwebnet-mcp](#2-improve-openwebnet-mcp) | [openwebnet-mcp](https://github.com/OpenWebNet-HA/openwebnet-mcp) and every AI assistant that uses it |
| 3 | [Test OWNd and MyHOME against real firmware](#3-test-ownd-and-myhome) | [OWNd](https://github.com/OpenWebNet-HA/OWNd), [MyHOME](https://github.com/OpenWebNet-HA/MyHOME) |

### What the oracle can and cannot tell you

Read this before using any result for any of the goals.

| It can answer | It cannot answer |
|---|---|
| Does the gateway ACK or NACK this OpenWebNet frame? | What a device does with the bus frame (the bus side is a simulated PIC; the plant is absent) |
| Which bus frame(s) does it emit, byte for byte? | What a bit in a device-set mask means (WHO 1001 DIM 11: the actuator sets it, the gateway only translates it) |
| Which OpenWebNet frame comes out for a bus frame I inject (`up`)? | Anything about a gateway whose image we have not catalogued |
| Does gateway A differ from gateway B for the same input? | Whether a *silent* run means "refused" (a missing reply proves nothing; an emitted frame does) |

Every row is a fact about **one image**. The same frame gave different answers
on MH200N 1.1.8 and MyHomeServer1 2.82.06 (compare
`results/*/*/oracle/full/lights-level.tsv`), so a result never transfers to
another product or firmware without its own row. When evidence disagrees the
order is **live capture > oracle > OWNd / MyHOME code**, for the product and
firmware the evidence was taken on ([architecture §1.1](docs/oracle-architecture.md)).

### 1. Improve the Encyclopedia

The Encyclopedia's core values ask for evidence that is labelled, versioned and
reproducible, and for "not observed" never to turn into "does not exist". The
oracle supplies a new evidence kind that fits that model:

- **A new provenance class next to `evidence/` field captures.** A firmware
  record cites image SHA-256, target SHA-256, suite SHA-256 and the TSV row, so
  another researcher can rerun it and get a zero diff. Version scope is the
  exact firmware, never "OpenWebNet".
- **Negative results become citable.** A NACK, or a frame that never reaches
  the bus, is a firmware fact. Example: `*#1*31*#1*100*0##` (level write with
  100) is NACKed across all 11 emulated gateways, while level 101 and the
  switch-off forms are accepted.
- **Applicability tables from diffs.** The same case file runs on every
  gateway, so "which gateways accept X" is a TSV diff, not prose written from
  memory. Use it to fill the per-gateway columns and the version scope of
  claims, and to retire "applies to all gateways" wording.
- **Closing open questions with a suite.** Each entry in the Encyclopedia's
  `open-questions.md` that is about gateway translation (WHAT 19, DIM 7 / DIM 4
  handling, general/area scope) should name the suite that would answer it, or
  say that the oracle cannot (see the table above).
- **Privacy stays intact.** Suites use public or synthetic WHEREs only, results
  contain no plant data, and no vendor bytes enter either repo.

How to hand a finding over: write `findings/<product>/<topic>.md`, cite the TSV
rows and the three hashes, state the epistemic status (`firmware_observed`,
never `experimentally_confirmed`, which stays reserved for live hardware), and
open a PR on the Encyclopedia that cites it. Add a `Gateway` row from a live
read-only probe whenever the claim is about a real product.

### 2. Improve openwebnet-mcp

The MCP is the "judge": it checks grammar, and its own consumers have already
been bitten by treating that as meaning (the WHO 15 and WHO 25 catalogs were
wrong and three fixtures followed them; WHO 22 is still a stub). The oracle
adds a layer the judge lacked: **does a real gateway accept this?**

Shipped and active integrations:

<!-- MCP_METRICS_START -->
1. **Ship results as data.** (**Shipped**) Published a deterministic, hash-pinned
   index at `results/mcp_index.json` (509 unique frames, 6,528 verdicts across 12
   active gateway emulators). The MCP stays offline and read-only; it consumes
   this generated corpus like it does for the Machine KB.
<!-- MCP_METRICS_END -->
2. **`parse_and_validate_frame` and `lookup_firmware_verdict` per catalogued
   gateway.** (**Shipped**) Returns `ack`, `nack`, emitted bus telegrams, and
   SHA-256 provenance across all emulated gateways. "Legal grammar, NACKed by
   MyHomeServer1 2.82.06" is the answer an assistant gets directly.
3. **`draft_own_frame` pre-flight.** (**Shipped**) Runs oracle pre-flight checks
   against the target gateway (or full fleet) and warns on frames that firmware
   is known to NACK before handing them to the user.
4. **A `compare_gateways(frame, suite)` tool.** (**Shipped**) Returns the
   per-firmware comparison table or suite divergences in `openwebnet-mcp` (and
   via `tools/compare.py` on the CLI).
5. **Audit the catalog with the oracle.** (**In Progress**) 22 suites covering
   BTicino TS10 reference subsystems (WHO 4, WHO 22, WHO 18 & WHO 3, WHO 8),
   Area 0 lighting, holiday/vacation programs, and hardware trace corpora have
   been audited across the fleet, replacing specification stubs with empirical
   gateway verdicts and byte-level bus outputs.
6. **Share the live probe.** (**Shipped / Ongoing**) `myhome-gateway` (live bus)
   and the oracle's read-only `tools/live_probe.py`
   (`oracle/canary/canary-live.cases`) enforce a strict read-only frame
   allow-list so the `Gateway` evidence label means the same thing in both.

### 3. Test OWNd and MyHOME

OWNd and MyHOME are tested against a golden corpus whose only hard facts are
community plant captures. The oracle makes firmware a second, cheap and
repeatable test source that needs no plant:

- **Corpus gate.** Feed every `down` frame of the golden corpus to each
  catalogued firmware (`oracle/cases/trace-corpus.cases`). A fixture that the
  firmware NACKs, or that emits something else, is flagged before it ships. Add
  `firmware-oracle` as a fixture provenance ranked above spec readings and below
  a capture.
- **Parser conformance without hardware.** `up` suites inject bus frames; what
  the firmware emits on the OpenWebNet side is run through OWNd's parser (the
  `Emitted` label via `tools/check.py --all --report`). A parse failure on a
  real firmware's output is an OWNd bug.
- **Replay known audits.** gdluck's ten MyHOMEServer1 findings (OWNd#77) are
  suites; run them on every other gateway to see which hold, which differ, and
  which need a gateway-specific profile.
- **Derive gateway profiles instead of hand-writing them.** MyHOME's
  firmware-aware gateway profiles encode which commands a gateway accepts;
  the oracle's per-firmware TSVs are the evidence those capability flags
  should cite, and a test can assert profile and TSV agree.
- **Regression on new firmware.** When a vendor publishes a new image, the
  weekly `oracle` workflow produces a new manifest and TSV set; the diff to
  the previous version is the list of behaviour changes to check in MyHOME.

### Lessons learned

1. **One firmware is not all firmware.** The first suite already differs
   between MH200N and MyHomeServer1, and the first live anchor
   (`EVID-MH200-WHAT19-FAULT`) came from an MH200 2.1.0 while the first image
   was an MH200N 1.1.8 (an `MH200` 2.1.2 image has since been catalogued and
   emulated). Label every row cross-product whenever exact firmware versions
   differ.
2. **Emitted proves, silent does not.** A missing reply can mean no device
   answered. Only accepted-and-emitted rows are positive evidence.
3. **The oracle sees the translator, not the device.** It cannot give a
   device-set bitmask meaning. Say so in the finding instead of guessing.
4. **Determinism is the product.** Sorted TSV, no timestamps, no PIDs, a zero
   diff on re-run, hashes in every header. That is what lets a maintainer review
   a claim without trusting the author.
5. **Grammar is not behaviour.** A frame being legal says nothing about a
   gateway accepting it. Keep the judge (grammar), the oracle (firmware) and
   the capture (plant) as separate, labelled sources.
6. **Adding a gateway is a recipe, not research.** Eight steps, each with a
   "done when" ([docs/adding-a-gateway.md](docs/adding-a-gateway.md)); the
   second image needed a target YAML and a few harness generalizations, not a
   new design.
7. **Black box first, facts only.** No binaries, disassembly or decompiled code
   leave the machine; `tools/guard.py` enforces it. That is what makes the
   results publishable and the project safe to share.

## What is and isn't public here

| Stage | Public on GitHub | Stays local |
|---|---|---|
| 0. Catalog | vendor URL, version, size, SHA-256 | the firmware image |
| 1. Unpack | `tools/unpack.py` + `manifest.tsv` (path, type, CPU, SHA-256) | the extracted files |
| 2. Oracle | emulator + simulated SCS bus (our code), input frame lists | — |
| 3. Record | TSV: input frame → bus frame, firmware hash in the header | — |
| 4. Findings | `findings/<gateway>/*.md` pointing at TSV rows | decompiled C |

**No vendor binary, disassembly or decompiled code is ever committed.**
`tools/guard.py` enforces that in CI and as a pre-commit hook.

## Pipeline

```
catalog/<product>/<version>.yaml    product, version, sources[], sizes, sha256
tools/fwfetch.py                    materialize image by sha256 (vendor / R2 / --from-file)
tools/unpack.py                     layer-aware extractor  -> results/.../manifest.tsv
tools/plan.py                       which manifests are stale; result paths
tools/suite_plan.py                 which emulated suite TSVs are missing, stale, or regressed
tools/mcp_index.py                  build & verify results/mcp_index.json across the fleet
tools/compare.py                    cross-gateway frame comparison, divergences & matrices
tools/verdict_diff.py               semantic verdict diff across git revisions / PRs
tools/check.py                      OWNd parser & live capture conformance evaluator
tools/live_probe.py                 read-only live hardware canary runner & validator
tools/export_evidence.py            export oracle TSVs to Encyclopedia evidence bundles
tools/sync_readme.py                automated README metrics & table anti-drift sentinel
tools/guard.py                      refuse binaries / NUL bytes / oversized files
oracle/                             emulator + simulated SCS bus (see docs/oracle-architecture.md)
  targets/<product>/<version>.yaml  programs to run, pinned to manifest rows
  cases/*.cases|*.seq               input steps: down = OWN text, up = bus hex
  canary/canary-live.cases          read-only live hardware canary suite
results/<product>/<version>/        manifest.tsv, boundary TSVs, oracle TSVs, check TSVs
results/mcp_index.json              hash-pinned cross-firmware verdict index (schema 1.1.0)
findings/<product>/*.md             conclusions pointing at TSV rows
.github/workflows/                  CI, see below
requirements/                       hash-locked CI dependencies
```

## CI

| Workflow | When | What |
|---|---|---|
| `pr` | every PR, `main` | ruff, ruff format, mypy `--strict`, zizmor, actionlint + shellcheck; guard, `mcp_index.py --check`, `sync_readme.py --check`; pytest on Python 3.12–3.14 incl. a real-debugfs end-to-end test, coverage ratchet; `verdict_diff.py` PR summary; dependency review. `ci-ok` is the one check to require. |
| `reproduce` | PRs touching `tools/`, `catalog/`, `results/`; weekly | re-fetches every fresh, publicly downloadable image, re-runs `unpack`, fails on any byte of difference from `results/` |
| `oracle` | `main`, weekly | rebuilds stale manifests (new image or `TOOL_VERSION`) and opens one results PR |
| `suites` | PRs touching `oracle/`, `catalog/`, suite results; `main`, weekly | re-runs every emulated suite on every target under qemu-user (`tools/suite_plan.py`): fails when a re-run with the same inputs gives different verdicts, produces missing or stale results only if a second run reproduces them, and opens one results PR on `main`, and opens an issue when the weekly run regresses |
| `codeql` | every PR, `main`, weekly | CodeQL `security-extended` on the tools and on the workflows |
| `scorecard` | `main`, weekly | OpenSSF Scorecard, published + in code scanning |

None of them gives a fork PR a secret or a write token. Firmware only lands in
`$RUNNER_TEMP` and is deleted after the job; it is never uploaded as an artifact,
and only public vendor images are cached, on pull requests (`reproduce`, `suites`). Actions are pinned to commit SHAs, and Python deps are installed
with `--require-hashes`. Dependabot bumps both every month.

Contributing: `pip install pre-commit && pre-commit install` runs ruff and the guard
before each commit. To run everything the lint job runs:

```bash
pip install --require-hashes -r requirements/lint.txt -r requirements/test.txt
ruff check . && ruff format --check . && mypy && pytest --cov
```

## Run it locally

```bash
pip install pyyaml            # + boto3 only if you fetch from R2
# Filesystem tools, run inside bubblewrap (no network, one writable dir):
sudo apt install bubblewrap e2fsprogs squashfs-tools util-linux

# A) public image — fetch + verify from the vendor:
IMG=$(python tools/fwfetch.py catalog/MH200N/010108.yaml)

# B) a local copy — no upload. Pass the WRAPPER (the file whose size + sha256
#    the catalog records), not the inner .fwz:
IMG=$(python tools/fwfetch.py catalog/MH200N/010108.yaml --from-file ./FW_MH200N_vers_010108.zip)

python tools/unpack.py catalog/MH200N/010108.yaml "$IMG" \
    -o results/MH200N/010108/manifest.tsv
python tools/guard.py
```

A clean re-run must produce a **zero diff** (sorted TSV, no timestamps). That's
the reproducibility check, and the `reproduce` workflow runs it weekly against
the real vendor image.

`unpack.py` refuses any file that is not the catalog's wrapper (size + SHA-256)
or that does not contain the catalog's inner image, and roots every manifest
path at `wrapper.filename`, so the cache copy and a local copy give the same
TSV. Symlinks inside a filesystem are recorded as `symlink` rows (target
hashed), never followed. Every catalog file is validated by `tools/schema.py`:
plain-name product / version / filenames at `catalog/<product>/<version>.yaml`,
and `https://` vendor URLs on an allow-listed BTicino / Legrand host.

### Layers and the coverage gate

`unpack.py` detects layers by magic: zip (ZipCrypto via the catalog's
password scheme), U-Boot uImage, gzip / bzip2 / xz / lzma, tar, cpio, and the
ext2/3/4, squashfs and cramfs filesystems. ELF rows say CPU, word size, byte
order and, for ARM, the ABI (`ELF/ARM/exec/32le/oabi`), which is what the
oracle needs to pick an emulator.

JFFS2, UBI, FIT and 7z are recognised but not unpacked yet. Those, any
container its tool could not read, and any opaque `data` blob of 1 MiB or more
outside a filesystem fail the run. A new image therefore either unpacks
completely or says exactly which layer is missing. When a layer is genuinely
not worth unpacking (a bootloader, a sub-MCU blob), acknowledge it in the
catalog:

```yaml
undecoded_ok:
  - path: "<wrapper>!<member>"      # exactly as the gate printed it
    reason: "sub-MCU firmware, not a Linux image"
limits:
  max_expand_mib: 1024     # raise the 256 MiB bomb guard for a big image
```

An acknowledgement that no longer matches a row also fails, so the list
cannot go stale. Extraction tools (`debugfs`, `unsquashfs`, `fsck.cramfs`)
run inside bubblewrap; `--no-sandbox` runs them unconfined and must be asked
for explicitly.

## Catalog: Supported Gateways & Hardware

The oracle catalogues, unpacks, and tracks deterministic manifests for all standalone OpenWebNet gateway models released by BTicino and Legrand, as well as auxiliary touch screen consoles and interface hardware:

<!-- FLEET_TABLE_START -->
| Gateway | Firmware Version | System Architecture | Manifest Rows | Layer Types | Core Daemons / Firmware Artifact | Emulation Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **MH200N** | `010108` (1.1.8) | Linux ARMv5 `eabi5` | 879 | U-Boot, Ext2, Gzip, Zip | `bt_processi`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_supervisione`, `bt_energia`, `bt_difson`, `bt_multi`, `bt_alarm` | **Emulated** (Phase 2 — 22 suites, full parity) |
| **MH200** | `020102` (2.1.2) | Linux ARMv4 `oabi` | 279 | Ext2, Gzip, Zip | `bt_processi`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_energia`, `bt_difson`, `bt_alarm`, `bt_vct` | **Emulated** (Phase 2 — 22 suites, full parity) |
| **MyHomeServer1** | `028206` (2.82.6) | Linux ARMv7 `eabi5` | 56,602 | U-Boot, Ext4, Gzip, Zip | `bt_daemon`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `bt_supervisione`, `coso` | **Emulated** (Phase 2 — 22 suites, full parity) |
| **F454** | `020051` (2.0.51) | Linux ARMv5 `eabi5` | 5,413 | U-Boot, Ext3, Gzip, Zip | `bt_daemon`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `bt_difson`, `coso`, `bt_energia`, `bt_supervisione` | **Emulated** (Phase 2 — 22 suites, full parity) |
| **MH202** | `010024` (1.0.24) | Linux ARMv5 `eabi5` | 10,343 | U-Boot, Ext3, Gzip, Zip | `bt_daemon`, `openserver`, `scsserver`, `bt_device`, `bt_energia`, `bt_supervisione`, `coso` | **Emulated** (Phase 2 — 22 suites, full parity) |
| **F453AV** | `030014` (3.0.14) | Linux ARMv4 `oabi` | 1,283 | U-Boot, Ext2, Gzip, Zip | `bt_processi`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_difson`, `bt_multi`, `bt_vct`, `bt_energia`, `bt_supervisione` | **Emulated** (Phase 2 — 22 suites, full parity) |
| **F453** | `010100` (1.1.0) | Linux ARMv4 `oabi` | 1,090 | U-Boot, Ext2, Gzip, Zip | `bt_processi`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_difson`, `bt_multi`, `bt_vct`, `bt_energia`, `bt_supervisione`, `bt_alarm` | **Emulated** (Phase 2 — 22 suites, full parity) |
| **F455** | `010102` (1.1.2) | Bare-metal ARM Cortex-M | 3 | Zip, Monolithic `.bin` | Flash image `F455_1_1_2.bin` (301 KB, no OS) | Pending Emulation (Bare-metal MCU — zero matrix value) |
| **MH201** | `030644` (3.6.44) | Bare-metal ARM Cortex-M3 (STM32F217) | 3 | Zip, Monolithic `.bin` | Flash image `MH201_3_6_44_signed.bin` (502 KB, CMX-RTX, no OS) | Pending Emulation (Bare-metal MCU — zero matrix value) |
| **F461** | `020011` (2.0.11) | Linux AArch64 (ARM64) | 15,517 | Ext4, Gzip, Zip | `bt_daemon`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `bt_supervisione`, `coso` | **Emulated** (Phase 2 — 22 suites, full parity) |
| **F450** | `020010` (2.0.10) | Linux ARMv5 `eabi5` | 4,322 | U-Boot, Ext3, Gzip, Zip | `bt_daemon`, `bacclient`, `ebacgw`, `scsserver`, `bt_device`, `bt_termo` | **Emulated** (Phase 2 — 22 suites, full parity) |
| **F459** | `020105` (2.1.5) | Linux ARMv5 `eabi5` | 14,335 | U-Boot, Ext3, Gzip, Zip | `bt_daemon`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `bt_energia`, `bt_supervisione`, `coso` | **Emulated** (Phase 2 — 22 suites, full parity) |
| **F460** | `020012` (2.0.12) | Linux AArch64 (ARM64) | 15,569 | Ext4, Gzip, Zip | `bt_daemon`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `bt_supervisione`, `coso` | **Emulated** (Phase 2 — 22 suites, full parity) |
| **H4684** | `020054` (2.0.54) | Linux ARMv4 `oabi` | 225 | Ext2, Gzip, Zip | `bt_processi`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_difson`, `bt_vct`, `bt_energia`, `bt_alarm` | **Emulated** (Phase 2 — 22 suites, full parity) |
| **L4686SDK** | `012001` (1.20.1) | Bare-metal Microchip PIC18 (`0x000000`-`0x00EDDD`) | 3 | Zip, Intel HEX | OpenWebNet-to-SCS serial translator (`4686SDK_012001.HEX`, no OS) | Static analysis only (PIC18 serial/USB translator — no TCP/IP stack) |
| **L4561N** | `040006` (4.0.6) | Bare-metal Microcontroller | 3 | Zip, Intel HEX | Stereo control interface firmware (`rca_ir.HEX`) | Pending Emulation (Specialized bus interface — zero matrix value) |
| **3550** | `030020` (3.0.20) | Bare-metal Mitsubishi M16C (`0x0E0000`-`0x0FFFFF`) | 5 | Zip, ZipCrypto, Motorola S-record | 99-zone central unit firmware (`3550_030020.mot`, no OS) | Static analysis only (QEMU has no M16C target) |
<!-- FLEET_TABLE_END -->

### Architectural Differences: Linux vs. Bare-Metal Microcontroller

The firmware oracle encounters two fundamentally different system architectures across the catalog:

1. **Embedded Linux Gateways & Consoles**
   - **Architectures**: Linux ARMv4 `oabi` (MH200, F453, F453AV, H4684), ARMv5 `eabi5` (MH200N, MH202, F450, F454, F459), ARMv7 `eabi5` (MyHomeServer1), and AArch64 (F460, F461).
   - **Structure**: Multi-layer archives (`Zip`, `Gzip`, `U-Boot uImage`) containing standard Linux root filesystems (`Ext2`, `Ext3`, `Ext4`; `unpack.py` also supports `SquashFS` and `CramFS`). When unpacked, they produce hundreds to tens of thousands of individual user-space binaries, shared libraries, and configuration files.
   - **Oracle Execution**: Evaluated via user-space emulation (`qemu-arm` / `qemu-aarch64`) with simulated serial PTYs (`/dev/ttyPIC`, `/dev/ttyS1`, `/dev/ttymxc2`, `/dev/ttyRPMSG30`) and loopback TCP sockets.
2. **Bare-Metal Microcontroller Gateways & Bus Peripherals (F455, MH201, L4686SDK, L4561N, 3550)**
   - **Architectures**: ARM Cortex-M microcontrollers (F455: Cortex-M3/M4, vector table base `0x08000000`, initial SP `0x20004178`; MH201: STM32F217 Cortex-M3, vector table base `0x08000000`, initial SP `0x200041a0`), Microchip PIC18 (L4686SDK OpenWebNet USB & RS-232 serial bus translator, L4561N stereo bus interface), and Mitsubishi/Renesas M16C/60 (3550 99-zone temperature control central unit, `0x0E0000`–`0x0FFFFF`).
   - **Structure**: Monolithic flash images (`F455_1_1_2.bin` 301 KB, `MH201_3_6_44_signed.bin` 502 KB, `4686SDK_012001.HEX` and `rca_ir.HEX` Intel HEX, `3550_030020.mot` Motorola S-records) with no Linux operating system, shell, or filesystem. The bootloader, RTOS/TCP/IP stack (LwIP / CMX-RTX where applicable), OpenWebNet parser, and SCS transceiver logic are compiled directly into a single binary image.
   - **Manifest Size**: Explains why `results/F455/010102/manifest.tsv`, `results/MH201/030644/manifest.tsv`, `results/L4686SDK/012001/manifest.tsv`, `results/L4561N/040006/manifest.tsv`, and `results/3550/030020/manifest.tsv` contain only 3–5 entries each (wrapper archive, metadata file, and raw flash image).

### Gateway Census: Complete Ingestion vs. Excluded Hardware

The oracle project catalogues **100% of all standalone OpenWebNet IP/SCS gateways** for which BTicino or Legrand publicly released downloadable firmware update packages.

To ensure complete clarity regarding the BTicino/Legrand MyHOME product ecosystem, the table below details the ingested fleet versus hardware that is not part of the firmware oracle:

#### 1. Ingested Fleet (Released Firmware Packages)
<!-- FLEET_CENSUS_START -->
- **MH200N** (`010108` / 1.1.8): DIN scenario programmer & OpenWebNet gateway. *(Emulated — 22 suites, full matrix parity)*
- **MH200** (`020102` / 2.1.2): Legacy DIN scenario programmer & OpenWebNet gateway. *(Emulated — 22 suites, full matrix parity)*
- **MyHomeServer1** (`028206` / 2.82.6): Modern Linux gateway & IoT bridge. *(Emulated — 22 suites, full matrix parity)*
- **F454** (`020051` / 2.0.51): Web server audio/video DIN gateway. *(Emulated — 22 suites, full matrix parity)*
- **MH202** (`010024` / 1.0.24): Advanced scenario programmer & BACnet gateway. *(Emulated — 22 suites, full matrix parity)*
- **F459** (`020105` / 2.1.5): Hotel / hospitality driver manager gateway. *(Emulated — 22 suites, full matrix parity)*
- **F453AV** (`030014` / 3.0.14): DIN audio/video web server (ARMv4 OABI). *(Emulated — 22 suites, full matrix parity)*
- **F453** (`010100` / 1.1.0): DIN web server 6-module gateway (ARMv4 OABI). *(Emulated — 22 suites, full matrix parity)*
- **F460** (`020012` / 2.0.12): Hotel scenario programmer gateway (AArch64 / ARM64). *(Emulated — 22 suites, full matrix parity)*
- **F461** (`020011` / 2.0.11): Server gateway stack (AArch64 / ARM64). *(Emulated — 22 suites, full matrix parity)*
- **F450** (`020010` / 2.0.10): IP interface gateway (OPEN-BACnet). *(Emulated — 22 suites, BACnet side on the firmware's own `ebacgw` and its factory plant)*
- **F455** (`010102` / 1.1.2): Basic OpenWebNet IP interface (bare-metal ARM Cortex-M). *(Pending Emulation — bare-metal microcontroller flash image without OS/userland; basic lighting/shutter subset already 100% covered by Linux gateways with zero added value to the matrix)*
- **MH201** (`030644` / 3.6.44): Hotel guest room scenario module (bare-metal ARM Cortex-M3 STM32F217). *(Pending Emulation — bare-metal microcontroller flash image without OS/userland; basic lighting/shutter/scenario subset already covered by Linux gateways with zero added value to the matrix)*

*(Note: Auxiliary hardware (touch screen, bus interface, temperature control central unit) such as the **H4684** Colour Touch Screen (`catalog/H4684/020054.yaml`), **L4686SDK** OpenWebNet USB & RS-232 Interface (`catalog/L4686SDK/012001.yaml`), **L4561N** Stereo Control Interface (`catalog/L4561N/040006.yaml`), and **3550** Temperature Control Central Unit (`catalog/3550/030020.yaml`) are also catalogued with full cryptographic provenance, bringing total catalogued firmware packages to 17).*
<!-- FLEET_CENSUS_END -->

#### 2. Excluded Hardware & Legacy Devices (and Why)

<!-- EXCLUDED_HARDWARE_START -->
| Product SKU | Description | Exclusion Reason |
| :--- | :--- | :--- |
| **F452 / F452V** | First-generation Web Server DIN | Discontinued early 2000s hardware. Firmware was stored in masked ROM / EEPROM; no firmware update packages were ever published for download. |
| **F458 / 003599** | IP Server | Specialized telecom/IP server module; no public firmware archive distributed. |
| **HOMETOUCH 7" (3488 / 067259)** | Connected Touchscreen | Embedded Android touch display; firmware updates are distributed exclusively as full-device Android OTA updates, not OpenWebNet gateway images. |
| **Classe 300X (`344642`, `344742`)** | Video Internal Unit with Wi-Fi | 2-wire video internal unit with Netatmo cloud bridging; firmware updates are distributed exclusively as encrypted OTA cloud synchronization. |
| **Classe 300 EOS (`344842`, `344845`)** | Smart Video Internal Unit | Connected video internal unit with Alexa; firmware updated exclusively via Netatmo / Legrand cloud OTA. |
| **HC4690 / HD4690 / HS4690 (`067285`)** | Multimedia Touch Screen | 10-inch multimedia display console; specialized display firmware. |
| **F422 / 003562** | SCS-to-SCS Interface Router | Pure galvanic bus-to-bus bridge microcontroller; no IP interface or OpenWebNet parser. |
| **F429 / 002631** | SCS/DALI Gateway | Specialized DALI lighting interface controller; no OpenWebNet TCP server daemon. |
| **BMNE4000 / 048832** | SCS/ZigBee Gateway | Hardware radio bridge; firmware is embedded radio stack without standalone OpenWebNet daemon. |
| **3578 / 088328** | OpenWebNet ZigBee USB Stick | Telegesis ETRX2USB / Silicon Labs EM250 wireless dongle; no standalone firmware package published on Home Systems or bundled in MyHOME_Suite. |
| **H4691 / LN4691 / KM4691** | Thermostat with display (temperature control zone probe) | No firmware package published: their Home Systems product sheets carry manuals only, and neither MyHOME_Suite 3.5.38 nor TiThermo 2.0 bundles a probe image. |
| **L4600/4 / N4600/4** | 4-zone temperature control central unit | No firmware package published: the Home Systems product sheets carry manuals only. |
<!-- EXCLUDED_HARDWARE_END -->

---

## Published Findings & Subsystem Audits

Detailed technical findings documents with exact bytecode citations, cryptographic hashes, and architectural explanations are published in [`findings/`](findings/):

| Subsystem / Topic | Affected WHO | Gateways Investigated | Summary & Finding Link |
| :--- | :--- | :--- | :--- |
| **Autodiagnostics Co-occurrence** | WHO 1 (Lighting), WHO 1001 (Diag) | MH200N, MyHomeServer1 (and full 12-gateway fleet) | [**Lighting WHAT 19 / WHO 1001 Autodiagnostics Co-occurrence**](findings/MH200N/what19.md)<br>Proves `bt_luci` maps SCS fault `'E'` to `*1*19*WHERE##`, while `bt_device` / `libdiag.so` emit diagnostic mask `*#1001*WHERE*11*<bitmask>##`. Validated against capture `EVID-MH200-WHAT19-FAULT`. |
| **OWNd#77 Empirical Replay** | WHO 1, 4, 18, 15/25, 1004 | MH200N (and full 12-gateway fleet) | [**Replay of OWNd#77 Audit Fixes on MH200N**](findings/MH200N/ownd-77-replay.md)<br>Replay of 10 audit findings from OWNd#77 across 9 test suites, determining which behaviors hold across gateways and which depend on gateway-specific daemon pipelines. |
| **Area 0 Point-to-Point Lighting** | WHO 1 (Lighting) | Full 12-gateway fleet | [**Area 0 Point-to-Point Lighting Addresses (`A=0, PL=1..15`)**](findings/MH200N/area0-lighting.md)<br>28 test cases (`lights-area0.cases`) proving `*1*1*02##` (2-digit) addresses `A=0, PL=2` point-to-point, `*1*1*0010##`–`*1*1*0015##` (4-digit) addresses `A=0, PL=10..15`, `*1*1*0002##` is NACKed by 11/12 gateways, and `*1*1*2##` is strictly an Area 2 broadcast. |
| **Legacy MH200 Architecture & Area 0** | WHO 1, 3, 4, 5, 6, 7, 9, 13, 14, 15, 16, 17, 1001, 1013 | MH200 (`020102`) | [**MH200 Firmware 2.1.2 Architecture & Boundary Verification**](findings/MH200/README.md)<br>Establishes that the legacy MH200 (item 003535) runs a full ARMv4 OABI Linux OpenWebNet stack (`openserver`, `scsserver`, `bt_luci`, `bt_termo`, `bt_difson`, `bt_vct`) and resolves the Area 0 addressing boundary reported in MyHOME#684 / #713. |
| **F453 6-DIN Web Server Architecture** | WHO 0, 1, 2, 3, 4, 5, 6, 7, 9, 13, 14, 15, 16, 18, 22, 25, 1001..1022 | F453 (`010100`) | [**F453 Firmware 1.1.0 (`WS_6DIN`) Architecture & Provenance**](findings/F453/README.md)<br>Documents the 16 MB flash partition layout (`ubtweb_only_recovery.gz` + `btweb_app.zip`) recovered from `MyHOME_Suite_030538.exe`, including the complete 11-daemon ARMv4 OABI OpenWebNet stack (`bt_alarm` enabled on WHO 5/9 alongside `bt_luci`, `bt_termo`, `bt_multi`, `bt_supervisione`). |
| **TS10 Thermoregulation Protocol** | WHO 4 (Thermo) | MyHomeServer1, MH200N, MH202 (and full 12-gateway fleet) | [**TS10 Thermo Protocol Verification**](findings/MyHomeServer1/thermo-ts10.md)<br>Empirical validation of WHO 4 compound probe addressing (`#probe#central`), timed manual/holiday modes with duration encoding, 16 weekly programs/scenarios, and calendar dimensions (30, 31, 32). |
| **TS10 Sound Diffusion Protocol** | WHO 22 (Sound) | MyHomeServer1, F454, MH200N (and full 12-gateway fleet) | [**TS10 Sound Diffusion Protocol Verification**](findings/MyHomeServer1/sound-who22.md)<br>58 test cases covering volume, tone, sources, amplifier status, and matrix routing. Proves byte-identical SCS output between MHS1 (`bt_multi`) and F454, while MH200N strictly refuses WHO 22 at its subsystem boundary. |
| **TS10 Energy & Load Management** | WHO 18 (Energy), WHO 3 (Load Shedding) | MyHomeServer1, MH202, MH200N (and full 12-gateway fleet) | [**TS10 Energy & Load Management Verification**](findings/MyHomeServer1/energy-ts10.md)<br>65 test cases proving that TS10 WHAT commands (`*18*57..`) and classic dimension queries (`*#18*..*511..`) generate identical extended SCS telegrams. Verified periodic reporting (`DIMENSION 1200`) and Stop&Go breaker controls. |
| **TS10 Video Door Entry & Intercom** | WHO 8 (Door Entry / Intercom) | MyHomeServer1, MH202, MH200N, F454 (and full 12-gateway fleet) | [**TS10 Video Door Entry & Intercom Verification**](findings/MyHomeServer1/intercom-ts10.md)<br>59 test cases demonstrating subsystem boundary isolation: automation gateways (MHS1, MH200N, MH202) reject WHO 8, whereas F454 routes WHO 8 to `bt_vct`. |
| **OPEN-BACnet Gateway & Plant DB** | WHO 4 (Thermo), WHO 13 | F450 (`020010`) | [**F450 OPEN-BACnet Architecture**](findings/F450/open-bacnet-architecture.md) & [**BACnet `ebacgw` Plant Database**](findings/F450/bacnet-plant-database.md)<br>Documents F450's `bacclient` + `ebacgw` SOAP/BACnet bridge (no `openserver`) and derives the minimal SQLite `master.sqlite` plant seed from `ebacgw` and factory `conf.xml.def` so F450 runs deterministically on its own firmware daemons. |
| **OpenWebNet USB & RS-232 Interface** | WHO 0, 1, 2, 4, 5, 8, 13, 14, 15, 16, 18, 22, 25, 1001..1022 | L4686SDK (`012001`) | [**L4686SDK (`L4686/USB` / `3559`) PIC18 Static Analysis**](findings/L4686SDK/README.md) & [**Emulation Boundary**](findings/L4686SDK/emulation-strategy.md)<br>Static analysis of the Microchip PIC18 Intel HEX firmware (`4686SDK_012001.HEX`), documenting its `OPEN_SCS` memory map (`0x0000`–`0xEDDD`) and ROM lookup tables mapping ASCII OpenWebNet (`*...##`) directly to 2-wire SCS bus telegrams. |
| **99-Zone Thermo Central Unit** | WHO 4 (Thermo) | 3550 (`030020`) | [**3550 Temperature Control Central Unit Static Analysis**](findings/3550/README.md)<br>Static analysis of the Mitsubishi M16C/60 Motorola S-record firmware (`3550_030020.mot`), documenting its memory map (`0x0E0000`–`0x0FFFFF`), `$`-framed SCS bus templates, and separate daily holiday vs. multi-day vacation tables. |
| **Bare-Metal MCU & Bus Peripherals** | WHO 1, 2, 16, 17 | F455 (`010102`), MH201 (`030644`), L4561N (`040006`) | [**F455**](findings/F455/emulation-strategy.md), [**MH201**](findings/MH201/emulation-strategy.md) & [**L4561N Emulation Strategies**](findings/L4561N/emulation-strategy.md)<br>Hardware architecture and boundary cuts for the Cortex-M flash images (F455, MH201 CMX-RTX/LwIP) and Microchip PIC18 SCS stereo interface (`L4561N`, modeled on the virtual bus via `SoundSourceResponder`). |

---

## How to Use: Concrete Examples

See [`docs/quickstart.md`](docs/quickstart.md) for the `< 10 minute` local setup and how to propose new `.cases` suites without running QEMU locally, [`docs/adding-a-gateway.md`](docs/adding-a-gateway.md) for onboarding new gateways, and [`docs/mcp-index-contract.md`](docs/mcp-index-contract.md) for the `mcp_index.json` schema contract.

### 1. Materializing & Unpacking a Firmware Image

Download the official vendor archive and unpack it into a deterministic filesystem manifest:

```bash
# A) Fetch and verify the vendor archive by SHA-256:
IMG=$(python tools/fwfetch.py catalog/MH200N/010108.yaml)

# B) Unpack container layers (Zip, U-Boot, Gzip, Ext2/3/4) into manifest.tsv:
python tools/unpack.py catalog/MH200N/010108.yaml "$IMG" \
  -o results/MH200N/010108/manifest.tsv

# C) Verify the workspace remains clean of raw binaries or NUL leaks:
python tools/guard.py
```

### 2. Validating Workspace Freshness and Integrity

```bash
# Check if any catalogued images or unpacked manifests are stale:
python tools/plan.py

# Check that README.md and results/mcp_index.json are in sync:
python tools/mcp_index.py --check
python tools/sync_readme.py --check

# Enforce quality gates (ruff, mypy, test coverage):
ruff check .
ruff format --check .
mypy --strict tools oracle
pytest tests --cov=tools --cov=oracle --cov-report=term-missing
```

### 3. Running Emulated Oracle Test Suites (`oracle.run`)

Run a `.cases` test suite against an emulated gateway daemon under `qemu-user`:

```bash
# A) Run the lights-level suite against the emulated MH200N openserver:
python -m oracle.run suite \
  oracle/targets/MH200N/010108.yaml \
  oracle/cases/lights-level.cases \
  --image "$IMG" \
  -o results/MH200N/010108/oracle/full/lights-level.tsv

# B) Run the TS10 sound suite on F454 (serial PTY translation to SCS):
python -m oracle.run suite \
  oracle/targets/F454/020051.yaml \
  oracle/cases/sound-who22.cases \
  --image "$IMG_F454" \
  -o results/F454/020051/oracle/full/sound-who22.tsv

# C) Run the TS10 energy suite on MH202 (multi-daemon stack):
python -m oracle.run suite \
  oracle/targets/MH202/010024.yaml \
  oracle/cases/energy-ts10.cases \
  --image "$IMG_MH202" \
  -o results/MH202/010024/oracle/full/energy-ts10.tsv

# The generated TSV (oracle_version=3) contains deterministic rows:
# direction    input              reply    verdict    output
# down         *1*1*31##          ack      out        bus:24 30 33 33 31 30 30 31 32 30 30 0d
# down         *#1*31*#1*100*0##  nack     silent     -
```

### 4. Cross-Gateway Comparison, Semantic Diff, Live Canary & Evidence Export

```bash
# Compare a single frame across all emulated gateways (with decoded SCS bytes):
python tools/compare.py frame "*1*1*31##"

# List all cross-gateway divergences for a suite or render a gateway x frame matrix:
python tools/compare.py divergences --suite sound-who22
python tools/compare.py matrix --suite lights-level

# Summarize behavioural verdict changes across a PR or git range:
python tools/verdict_diff.py --base origin/main --head HEAD

# Check oracle outputs against OWNd parser & live hardware traces:
python tools/check.py --all --report

# Validate or execute the read-only live hardware canary suite:
python tools/live_probe.py --suite oracle/canary/canary-live.cases --validate-only

# Export an oracle TSV into a schema-valid OpenWebNet-Encyclopedia evidence bundle:
python tools/export_evidence.py results/MH200N/010108/oracle/full/what19.tsv \
  --out-dir /tmp/cap-mh200n-what19-20261015 --date 2026-10-15
```

### 5. Querying the Cross-Firmware Verdict Index (`results/mcp_index.json`)

The cross-firmware MCP index (`schema_version: "1.1.0"`) provides hash-pinned answers for AI assistants and parsers without needing to run emulators. Gateways are indexed with status `"emulated"` (verdicts present) or `"catalogued"` (fleet inventory entry, empty `target_sha256`, `suites: []`). The canonical `verdicts_sha256` digest covers the sorted verdicts map, ensuring deterministic answer integrity.

```bash
# Check that the index matches all current oracle TSVs and passes schema validation:
python tools/mcp_index.py --check
```

Query the index directly in Python:

```python
import json
from pathlib import Path

# Load the hash-pinned index
index_path = Path("results/mcp_index.json")
index = json.loads(index_path.read_text(encoding="utf-8"))
print(f"Verdicts SHA-256: {index['verdicts_sha256']}")
print(f"Indexed frames: {len(index['verdicts'])}")
gateways = [f"{g['product']} {g['version']} [{g['status']}]" for g in index["gateways"]]
print(f"Gateways: {gateways}")

# Lookup cross-gateway verdict for a sound volume command (WHO 22):
target_frame = "*#22*3#1#1*#1*20##"  # Direct volume 20

for entry in index["verdicts"].get(target_frame, []):
    print(f"[{entry['product']} {entry['version']}] Suite: {entry['suite']}")
    print(f"  Reply:      {entry['reply']}")  # 'ack' on MHS1 & F454, 'nack' on MH200N
    print(
        f"  Verdict:    {entry['verdict']}"
    )  # 'out' on MHS1 & F454, 'silent' on MH200N
    print(f"  Bus output: {entry['bus_frames']}")  # ['$0493018114\r' on MHS1 and F454]
```

---

## Ground rules

Based on the framework in #613 (EU Software Directive 2009/24/EC Art. 5(3) /
Art. 6; protocols aren't copyrightable, CJEU C-406/10):

- only images we lawfully hold (official downloads or our own devices);
- the archive passwords are **published vendor packaging strings** (the model
  name / `bticino`, used by BTicino's own TiMH200N updater). `unpack.py` resolves
  them from each catalog entry's `password_scheme`; it does **not** brute-force or
  crack anything. This is "using a known password to open software we lawfully
  hold, to study it for interoperability";
- prefer black-box emulation over reading disassembly;
- publish facts in our own words; never binaries, disassembly or decompiled code;
- the only goal is interoperability with OWNd / MyHOME.

## Status

<!-- STATUS_PHASE1_START -->
- **Phase 1: Complete Fleet Ingestion & Unpack.** Done for all 13 standalone OpenWebNet gateways (MH200N, MH200, MyHomeServer1, F454, MH202, F453AV, F453, F455, MH201, F461, F450, F459, F460). Every manifest is verified byte-for-byte and covered by weekly CI reproducibility runs.
<!-- STATUS_PHASE1_END -->
- **Phase 2b: Cross-Gateway Translation Divergence.**
  - `lights-level` suite verified on both MH200N and MyHomeServer1.
  - **MH200N**: Immediate ACK (`*#*1##` within ~13ms) upon queuing to the PIC UART.
  - **MyHomeServer1**: Transactional confirmation model via `bt_luci`; awaits bus confirmation (type 4 frame) or times out at 2.0s with NACK (`*#*0##`).
- **Phase 2c: Autodiagnostics Co-occurrence (WHAT 19).** Completed and documented in [`findings/MH200N/what19.md`](findings/MH200N/what19.md). Proves `bt_luci` translates SCS `'E'` to `*1*19*WHERE##` while `bt_device` / `libdiag.so` emit diagnostic frame `*#1001*WHERE*11*<bitmask>##`. Validated against bus capture `EVID-MH200-WHAT19-FAULT`.
- **Phase 2d: Empirical Replay of OWNd#77 Audit Fixes.** Completed across 9 case suites (thermoregulation, energy, CEN+, interface routing, WHO 25) with deterministic outputs in `results/MH200N/010108/oracle/full/` and evaluation checks in `results/MH200N/010108/checks/`. Findings synthesized in [`findings/MH200N/ownd-77-replay.md`](findings/MH200N/ownd-77-replay.md).
- **Phase 2e: BTicino TS10 Reference Subsystem & Extended Protocol Verification.** Completed across the TS10 reference suites derived from `libqtdevices TS10_1_0_23` (OWNd#83) and extended subsystem suites:
  - **Thermoregulation (WHO 4)**: `thermo-ts10.cases`, `thermo-holiday-program.cases`, and `ownd-pr82-heating.cases` ([`findings/MyHomeServer1/thermo-ts10.md`](findings/MyHomeServer1/thermo-ts10.md)). Validates compound probe addressing (`#probe#central`), timed/holiday/vacation modes (`115#P`, `13DDD#P`), weekly programs 1..16, and calendar dimensions 30, 31, 32.
  - **Sound Diffusion (WHO 16 & WHO 22)**: `sound-who22.cases` and `sound-who16-source.cases` ([`findings/MyHomeServer1/sound-who22.md`](findings/MyHomeServer1/sound-who22.md)). Proves byte-identical SCS output between MHS1 (`bt_multi`) and F454 for volume, while MH200N strictly refuses WHO 22 at its subsystem boundary.
  - **Energy Management (WHO 18 & WHO 3)**: `energy-ts10.cases` ([`findings/MyHomeServer1/energy-ts10.md`](findings/MyHomeServer1/energy-ts10.md)). Proves exact equivalence between TS10 WHAT commands (`*18*57..`) and classic dimension frames (`*#18*..*511..`), automated reporting (`DIMENSION 1200`), and Stop&Go breaker controls.
  - **Video Door Entry & Intercom (WHO 8)**: `intercom-ts10.cases` ([`findings/MyHomeServer1/intercom-ts10.md`](findings/MyHomeServer1/intercom-ts10.md)). Confirms subsystem boundary isolation across automation gateways (MHS1, MH200N, MH202) versus Audio/Video routing on F454.
  - **Area 0 Lighting, Scenarios & Golden Trace Corpus**: `lights-area0.cases` ([`findings/MH200N/area0-lighting.md`](findings/MH200N/area0-lighting.md)), `ownd-pr82-lights.cases`, `scenario-who17.cases`, `matrix-refused.cases`, and `trace-corpus.cases` verified across all 11 emulated gateways.
<!-- STATUS_PHASE2F_START -->
- **Phase 2f: Gateway Fleet Target Emulation.** Expanded execution harness in `oracle/qemu_target.py` and target specifications in `oracle/targets/` supporting 12 active gateways under QEMU user emulation, achieving **full matrix parity across all 22 standard test suites (264 complete suite TSVs)**:
  - **MH200N** (`010108`): DIN scenario programmer (`openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_difson`, `bt_multi`, `bt_supervisione`, `bt_energia`, `bt_alarm` — 22 suites).
  - **MH200** (`020102`): Legacy DIN scenario programmer (`bt_processi`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_energia`, `bt_difson`, `bt_alarm`, `bt_vct` — 22 suites).
  - **MyHomeServer1** (`028206`): Multi-daemon Linux gateway (`openserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `bt_supervisione` on 30018/31018 — 22 suites).
  - **F454** (`020051`): Audio/Video web server DIN gateway (`bt_daemon`, `stackopen` serial PTY `/dev/ttyS1`, `bt_vct`, `openserver`, `scsserver` — 22 suites).
  - **MH202** (`010024`): Advanced scenario programmer & BACnet gateway (`bt_daemon`, `stackopen`, `bt_device`, `bt_energia`, `bt_supervisione`, `openserver`, `scsserver` — 22 suites).
  - **F459** (`020105`): Hospitality / hotel room gateway (`openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `bt_energia`, `bt_supervisione`, `coso` — 22 suites).
  - **F453AV** (`030014`): Legacy DIN audio/video gateway (`openserver`, `bt_vct`, `bt_processi` with `/dev/dsp1` audio DSP and `/dev/ttyPIC` PTY under ARMv4 OABI — 22 suites).
  - **F453** (`010100`): Legacy 6-module DIN web server (`openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_difson`, `bt_multi`, `bt_vct`, `bt_energia`, `bt_supervisione`, `bt_alarm` with `/dev/dsp1` and `/dev/ttyPIC` PTY under ARMv4 OABI — 22 suites).
  - **F460** (`020012`): Eliot AArch64 hotel scenario programmer stack (`openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `bt_supervisione`, `coso` on `/dev/ttyRPMSG30` PTY — 22 suites).
  - **F461** (`020011`): Eliot AArch64 server gateway stack (`openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `bt_supervisione`, `coso` on `/dev/ttyRPMSG30` PTY — 22 suites).
  - **F450** (`020010`): OPEN-BACnet gateway (`bacclient`, `ebacgw` on its factory BACnet plant, `scsserver`, `bt_device`, `bt_termo` — 22 suites).
  - **H4684** (`020054`): Colour touch screen console (`bt_processi`, `openserver`, `scsserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_difson`, `bt_vct`, `bt_energia`, `bt_alarm` on `/dev/ttyPIC` PTY under ARMv4 OABI — 22 suites).
<!-- STATUS_PHASE2F_END -->
<!-- STATUS_PHASE3_START -->
- **Phase 3: Hash-Pinned MCP Verdict Index.** Completed schema 1.1.0 index covering the full catalogued fleet. `tools/mcp_index.py` aggregates verdicts across suites and gateways into `results/mcp_index.json`, protected by a canonical SHA-256 fingerprint (`verdicts_sha256`) for direct consumption by `openwebnet-mcp`. The index tracks **509 unique OpenWebNet frames** across **22 test suites** and **12 active gateways** (MH200N, MH200, MyHomeServer1, F454, MH202, F459, F453AV, F453, F460, F461, F450, H4684), delivering **6,528 deterministic verdict entries** with a zero-diff PR consistency gate in CI (`tools/mcp_index.py --check`). Catalogued devices without Linux userland (F455, MH201, L4686SDK, L4561N, 3550) are indexed with `status: "catalogued"` and empty suite arrays (bare-metal microcontroller flash firmware or interfaces without an OS; basic lighting/shutter/scenario OpenWebNet subsets already 100% covered).
<!-- STATUS_PHASE3_END -->
- **Phase 4: Cross-Gateway Analysis, Conformance & Anti-Drift Tooling.** Shipped offline analysis and verification tools (`tools/compare.py`, `tools/verdict_diff.py`, `tools/check.py`, `tools/live_probe.py`, `tools/export_evidence.py`, `tools/sync_readme.py`) with crash-awareness gates and automated CI step summaries.

## License

[Apache License 2.0](LICENSE). Covers this repo's own tooling, results and
findings only — never any vendor firmware, which is not redistributed here.
The scope and copyright line are in [NOTICE](NOTICE).
