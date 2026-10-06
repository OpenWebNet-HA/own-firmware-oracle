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
  100) is NACKed by both catalogued gateways, while level 101 and the
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
can add a layer the judge lacks: **does a real gateway accept this?**

Proposed, in order of effort:

1. **Ship results as data.** Publish a deterministic, hash-pinned index
   derived from `results/**/oracle/**/*.tsv` (frame -> product, firmware,
   reply, emitted frames, row reference). The MCP stays offline and read-only;
   it just consumes a new generated corpus like it already does for the Machine
   KB.
2. **`parse_and_validate_frame` gains a `firmware_verdict` per catalogued
   gateway** (`ack`, `nack`, `emits <bus frame>`, `not tested`). "Legal grammar,
   NACKed by MyHomeServer1 2.82.06" is the answer an assistant needs.
3. **`draft_own_frame` pre-flight.** Refuse, or warn on, frames the target
   firmware is known to NACK instead of handing the user a frame that fails.
4. **A `compare_gateways(frame)` tool** that returns the per-firmware table.
5. **Audit the catalog with the oracle.** Run every frame the MCP can draft
   through the suites; each disagreement is a catalog bug or a gateway quirk,
   and either way it is a reviewable issue. This is how stubs like WHO 22
   get real content with a source behind them.
6. **Share the live probe.** `myhome-gateway` (live bus) and the oracle's
   planned read-only `tools/live_probe.py` should use one frame allow-list, so the
   `Gateway` evidence label means the same thing in both.

### 3. Test OWNd and MyHOME

OWNd and MyHOME are tested against a golden corpus whose only hard facts are
community plant captures. The oracle makes firmware a second, cheap and
repeatable test source that needs no plant:

- **Corpus gate.** Feed every `down` frame of the golden corpus to each
  catalogued firmware. A fixture that the firmware NACKs, or that emits
  something else, is flagged before it ships. Add `firmware-oracle` as a
  fixture provenance ranked above spec readings and below a capture.
- **Parser conformance without hardware.** `up` suites inject bus frames; what
  the firmware emits on the OpenWebNet side is run through OWNd's parser (the
  `Emitted` label; `tools/check.py` is planned, step 2c in the architecture). A parse failure on a real firmware's
  output is an OWNd bug.
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
   was an MH200N 1.1.8. Label every row cross-product until the matching image
   is catalogued.
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
tools/plan.py                       which images are stale; result paths
tools/guard.py                      refuse binaries / oversized files
oracle/                             emulator + simulated SCS bus  (phase 2,
                                    see docs/oracle-architecture.md)
  targets/<product>/<version>.yaml  programs to run, pinned to manifest rows
  cases/*.cases|*.seq               input steps: down = OWN text, up = bus hex
results/<product>/<version>/        manifest.tsv, oracle TSVs
findings/<product>/*.md             conclusions pointing at TSV rows
.github/workflows/                  CI, see below
requirements/                       hash-locked CI dependencies
```

## CI

| Workflow | When | What |
|---|---|---|
| `pr` | every PR, `main` | ruff, ruff format, mypy `--strict`, zizmor, actionlint + shellcheck; guard; pytest on Python 3.12–3.14 incl. a real-debugfs end-to-end test, coverage ratchet; dependency review. `ci-ok` is the one check to require. |
| `reproduce` | PRs touching `tools/`, `catalog/`, `results/`; weekly | re-fetches every fresh, publicly downloadable image, re-runs `unpack`, fails on any byte of difference from `results/` |
| `oracle` | `main`, weekly | rebuilds stale manifests (new image or `TOOL_VERSION`) and opens one results PR |
| `codeql` | every PR, `main`, weekly | CodeQL `security-extended` on the tools and on the workflows |
| `scorecard` | `main`, weekly | OpenSSF Scorecard, published + in code scanning |

None of them gives a fork PR a secret or a write token. Firmware only lands in
`$RUNNER_TEMP` and is deleted after the job; it is never cached and never uploaded
as an artifact. Actions are pinned to commit SHAs, and Python deps are installed
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

## Catalog: 10 Supported Gateways

The oracle catalogues, unpacks, and tracks deterministic manifests for all 10 OpenWebNet gateway models released by BTicino and Legrand:

| Gateway | Firmware Version | System Architecture | Manifest Rows | Layer Types | Core Daemons / Firmware Artifact |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **MH200N** | `010108` (1.1.8) | Linux ARMv5 `eabi5` | 1,328 | U-Boot, Ext2, Zip | `openserver`, `scsserver` |
| **MyHomeServer1** | `028206` (2.82.6) | Linux ARMv7 `eabi5` | 56,602 | U-Boot, Ext4, Zip | `openserver`, `bt_luci`, `bt_device`, `coso` |
| **F454** | `020051` (2.0.51) | Linux ARMv5 `eabi5` | 5,413 | JFFS2, CramFS, Zip | `bt_daemon`, `stackopen` (serial `/dev/ttyS1`) |
| **MH202** | `010024` (1.0.24) | Linux ARMv5 `eabi5` | 10,343 | SquashFS, Zip | `bt_daemon`, `stackopen` (scenarios + BACnet) |
| **F453AV** | `030014` (3.0.14) | Linux ARMv4 `oabi` | 1,283 | CramFS, Zip | `bt_processi` (legacy audio/video gateway) |
| **F455** | `010102` (1.1.2) | Bare-metal ARM Cortex-M | 3 | Monolithic `.bin` | Flash image `F455_1_1_2.bin` (301 KB, no OS) |
| **F461** | `020011` (2.0.11) | Linux AArch64 (ARM64) | 15,517 | Ext4, SquashFS, Zip | Server gateway stack (`Image` / kernel) |
| **F450** | `020010` (2.0.10) | Linux ARMv5 `eabi5` | 4,322 | JFFS2, Zip | Basic IP interface gateway stack |
| **F459** | `020105` (2.1.5) | Linux ARMv5 `eabi5` | 14,335 | SquashFS, Zip | Hospitality / hotel room gateway stack |
| **F460** | `020012` (2.0.12) | Linux AArch64 (ARM64) | 15,569 | Ext4, SquashFS, Zip | Hotel scenario programmer gateway stack |

### Architectural Differences: Linux vs. Bare-Metal Microcontroller

The firmware oracle encounters two fundamentally different system architectures across the catalog:

1. **Embedded Linux Gateways (9 of 10 models)**
   - **Architectures**: Linux ARMv4 `oabi` (F453AV), ARMv5 `eabi5` (MH200N, MH202, F450, F454, F459), ARMv7 (MyHomeServer1), and AArch64 (F460, F461).
   - **Structure**: Multi-layer archives containing standard root filesystems (Ext2/4, SquashFS, CramFS, JFFS2). When unpacked, they produce hundreds to tens of thousands of individual user-space binaries, shared libraries, and configuration files.
   - **Oracle Execution**: Evaluated via user-space emulation (`qemu-arm` / `qemu-aarch64`) with simulated serial PTYs or Unix domain sockets.
2. **Bare-Metal Microcontroller Gateway (F455 Basic Gateway)**
   - **Architecture**: ARM Cortex-M3/M4 bare-metal microcontroller (vector table base `0x08000000`, initial SP `0x20004178`).
   - **Structure**: Single monolithic flash image (`F455_1_1_2.bin`, 301 KB) with no operating system, shell, or filesystem. The bootloader, TCP/IP stack (LwIP), OpenWebNet parser, and SCS transceiver logic are compiled directly into a single binary image.
   - **Manifest Size**: Explains why `results/F455/010102/manifest.tsv` contains only 3 entries (wrapper archive, manifest XML, and the raw `.bin` image).

### Gateway Census: Complete Ingestion vs. Excluded Hardware

The oracle project catalogues **100% of all standalone OpenWebNet IP/SCS gateways** for which BTicino or Legrand publicly released downloadable firmware update packages (10 out of 10).

To ensure complete clarity regarding the BTicino/Legrand MyHOME product ecosystem, the table below details the ingested fleet versus hardware that is not part of the firmware oracle:

#### 1. Ingested Fleet (10 of 10 Released Firmware Packages)
- **MH200N** (`010108` / 1.1.8): DIN scenario programmer & OpenWebNet gateway.
- **MyHomeServer1** (`028206` / 2.82.6): Modern Linux gateway & IoT bridge.
- **F454** (`020051` / 2.0.51): Web server audio/video DIN gateway.
- **MH202** (`010024` / 1.0.24): Advanced scenario programmer & BACnet gateway.
- **F453AV** (`030014` / 3.0.14): DIN audio/video web server.
- **F455** (`010102` / 1.1.2): Basic OpenWebNet IP interface (bare-metal ARM Cortex-M).
- **F461** (`020011` / 2.0.11): Server gateway stack (AArch64 / ARM64).
- **F450** (`020010` / 2.0.10): IP interface gateway (OPEN-BACnet).
- **F459** (`020105` / 2.1.5): Hotel / hospitality driver manager gateway.
- **F460** (`020012` / 2.0.12): Hotel scenario programmer gateway (AArch64 / ARM64).

#### 2. Excluded Hardware & Legacy Devices (and Why)

| Product SKU | Description | Exclusion Reason |
| :--- | :--- | :--- |
| **F452 / F452V** | First-generation Web Server DIN | Discontinued early 2000s hardware. Firmware was stored in masked ROM / EEPROM; no firmware update packages were ever published for download. |
| **F453** | Enhanced Web Server DIN | Pre-Audio/Video version, replaced by F453AV. No separate public firmware download package exists. |
| **F458 / 003599** | IP Server | Specialized telecom/IP server module; no public firmware archive distributed. |
| **MH200 / 003535** | Legacy Scenes Programmer | Physical RS232 serial hardware predecessor to MH200N (no Ethernet OpenWebNet server daemon). |
| **MH201** | IP Scenario Module | Early DIN scenario module; no standalone public download archive. |
| **HOMETOUCH 7" (3488 / 067259)** | Connected Touchscreen | Embedded Android touch display; firmware updates are distributed exclusively as full-device Android OTA updates, not OpenWebNet gateway images. |
| **Classe 300X (`344642`, `344742`)** | Video Internal Unit with Wi-Fi | 2-wire video internal unit with Netatmo cloud bridging; firmware updates are delivered via encrypted OTA cloud synchronization. |
| **Classe 300 EOS (`344842`, `344845`)** | Smart Video Internal Unit | Connected video internal unit with Alexa; firmware updated exclusively via Netatmo / Legrand cloud OTA. |
| **H4684 / L4684 / LN4684A (`067283`, `078474`)** | Colour Touch Screen 3.5" & 10" | Embedded display consoles; firmware flashed via MyHOME_Suite or USB, not released as standalone gateway images. |
| **HC4690 / HD4690 / HS4690 (`067285`)** | Multimedia Touch Screen | 10-inch multimedia display console; specialized display firmware. |
| **F422 / 003562** | SCS-to-SCS Interface Router | Pure galvanic bus-to-bus bridge microcontroller; no IP interface or OpenWebNet parser. |
| **F429 / 002631** | SCS/DALI Gateway | Specialized DALI lighting interface controller; no OpenWebNet TCP server daemon. |
| **BMNE4000 / 048832** | SCS/ZigBee Gateway | Hardware radio bridge; firmware is embedded radio stack without standalone OpenWebNet daemon. |

---

## How to Use: Concrete Examples

### 1. Materializing & Unpacking a Firmware Image

Download the official vendor archive and unpack it into a deterministic filesystem manifest:

```bash
# A) Fetch and verify the vendor archive by SHA-256:
python tools/fwfetch.py catalog/MH200N/010108.yaml

# B) Unpack container layers (Zip, U-Boot, Ext2/4, SquashFS, CramFS) into manifest.tsv:
python tools/unpack.py catalog/MH200N/010108.yaml -o results/MH200N/010108/manifest.tsv

# C) Verify the workspace remains clean of raw binaries or NUL leaks:
python tools/guard.py
```

### 2. Validating Workspace Freshness and Integrity

```bash
# Check if any catalogued images or unpacked manifests are stale:
python tools/plan.py

# Enforce quality gates (ruff, mypy, test coverage):
ruff check .
ruff format --check .
mypy --strict tools oracle
pytest tests --cov=tools --cov=oracle --cov-report=term-missing
```

### 3. Running Emulated Oracle Test Suites (`oracle.run`)

Run a `.cases` test suite against an emulated gateway daemon under `qemu-arm`:

```bash
# Run the lights-level suite against the emulated MH200N openserver:
python -m oracle.run suite \
  --product MH200N \
  --version 010108 \
  --suite oracle/cases/lights-level.cases \
  -o results/MH200N/010108/oracle/full/lights-level.tsv

# The generated TSV contains deterministic rows:
# direction    input              reply    verdict    output
# down         *1*1*21##          ack      out        a8 21 00 12 01 22 a3
# down         *#1*31*#1*100*0##  nack     silent     -
```

### 4. Regression & Verification Checks (`tools/check.py`)

Validate that emulated outputs match expected protocol behavior:

```bash
# Check MH200N against the lights-level evaluation expectations:
python tools/check.py --product MH200N --version 010108 --suite lights-level

# Check MyHomeServer1 heating audit cases:
python tools/check.py --product MyHomeServer1 --version 028206 --suite ownd-pr82-heating
```

### 5. Querying the Cross-Firmware Verdict Index (`results/mcp_index.json`)

The cross-firmware MCP index provides hash-pinned answers for AI assistants and parsers without needing to run emulators:

```bash
# Check that the index matches all current oracle TSVs:
python tools/mcp_index.py --check
```

Query the index directly in Python:

```python
import json
from pathlib import Path

# Load the hash-pinned index
index_path = Path("results/mcp_index.json")
index = json.loads(index_path.read_text(encoding="utf-8"))
print(f"Index SHA-256: {index['index_sha256']}")
print(f"Catalogued targets: {list(index['targets'].keys())}")

# Lookup verdict for a specific OpenWebNet frame across all gateways:
target_frame = "*#1*31*#1*100*0##"  # Dimmer level write (100)

for target, target_data in index["targets"].items():
    suites = target_data.get("suites", {})
    for suite_name, rows in suites.items():
        for row in rows:
            if row["input"] == target_frame:
                print(f"[{target}] Suite: {suite_name}")
                print(f"  Reply:   {row['reply']}")  # 'nack'
                print(f"  Verdict: {row['verdict']}")  # 'silent'
                print(f"  Output:  {row['output']}")  # '-'
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

- **Phase 1: Complete Fleet Ingestion & Unpack.** Done for all 10 OpenWebNet gateways (MH200N, MyHomeServer1, F454, MH202, F453AV, F455, F461, F450, F459, F460). Every manifest is verified byte-for-byte and covered by weekly CI reproducibility runs.
- **Phase 2b: Cross-Gateway Translation Divergence.**
  - `lights-level` suite verified on both MH200N and MyHomeServer1.
  - **MH200N**: Immediate ACK (`*#*1##` within ~13ms) upon queuing to the PIC UART.
  - **MyHomeServer1**: Transactional confirmation model via `bt_luci`; awaits bus confirmation (type 4 frame) or times out at 2.0s with NACK (`*#*0##`).
- **Phase 2c: Autodiagnostics Co-occurrence (WHAT 19).** Completed and documented in `findings/MH200N/what19.md`. Proves `bt_luci` translates SCS `'E'` to `*1*19*WHERE##` while `bt_device` / `libdiag.so` emit diagnostic frame `*#1001*WHERE*11*<bitmask>##`. Validated against bus capture `EVID-MH200-WHAT19-FAULT`.
- **Phase 2d: Empirical Replay of OWNd#77 Audit Fixes.** Completed across 9 case suites (thermoregulation, energy, CEN+, interface routing, WHO 25) with deterministic outputs in `results/MH200N/010108/oracle/full/` and evaluation checks in `results/MH200N/010108/checks/`. Findings synthesized in `findings/MH200N/ownd-77-replay.md`.
- **Phase 3: Hash-Pinned MCP Verdict Index.** Completed. `tools/mcp_index.py` aggregates verdicts across suites and gateways into `results/mcp_index.json`, protected by a canonical SHA-256 fingerprint for consumption by `openwebnet-mcp`.

## License

[Apache License 2.0](LICENSE). Covers this repo's own tooling, results and
findings only — never any vendor firmware, which is not redistributed here.
The scope and copyright line are in [NOTICE](NOTICE).
