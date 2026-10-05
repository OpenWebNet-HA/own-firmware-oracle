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

## Catalog: MH200N 1.1.8 (first entry)

The MH200N firmware is a public BTicino download, so CI can fetch + verify it
with no R2 needed. Layer chain, auto-detected by `unpack.py`:

```
FW_MH200N_vers_010108.zip      wrapper (not encrypted)
 └ scheduler_010108.fwz        zip, vendor password scheme
    ├ Info.txt                 article MH200N / 003565, fw 010108
    └ scheduler_rel_1_1_8.zip  zip, vendor password scheme
       ├ Info_Rel.txt          flash map (16 MB, kernel@0x100000, rootfs@0x200000, app@0x620000)
       ├ uzImage               U-Boot uImage  → ARM kernel
       ├ ubtweb_only.gz        U-Boot uImage + gzip → ext2 rootfs
       ├ ubtweb_only_recovery.gz   recovery rootfs
       └ btweb_app.zip         the application (bt_* translators)
```

## Catalog: MyHomeServer1 2.82.06 (second entry)

The image @gdluck used for OWNd#77 (#613). The vendor serves the `.fwz`
itself, so `wrapper` and `image` are the same file. The checkout link sends no
`Content-Length`, so `fwfetch` stops reading at the catalog size and the
SHA-256 is the proof.

Per the vendor packaging convention ("the password is the model", #613 comment
18720022), the outer archive opens with `MyHomeServer1` (matching `<name>` in
`fwz.xml`).

```
SMARTGW_028206.fwz                                zip (ZipCrypto: MyHomeServer1)
 ├ fwz.xml                                        metadata (5.0.67, v2.82.6)
 ├ uImage.zip                                     kernel 5.10.35 + DTBs
 ├ btweb_only.ext4.gz.sha256.sig.zip              application rootfs
 │  └ btweb_only.ext4.gz                          ext4 (~1 GiB uncompressed)
 │     ├ home/bticino/bin/                        bt_luci, bt_device, coso, ...
 │     └ home/bticino/libcoso/                    translator plugins
 └ btweb_only_recovery.ext4.gz (+ .sig.zip)       recovery rootfs
```

56,602 rows in `results/MyHomeServer1/028206/manifest.tsv`. A second run gives
a **zero diff**.

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

- **Phase 1: fetch → verify → unpack → manifest.** Done for MH200N 1.1.8 and
  MyHomeServer1 2.82.06.
- **Phase 2b: first suite (`lights-level`) runs on both gateways** with a zero
  diff on re-run; boundary facts are in `results/*/*/oracle/boundary/`.
  Adding the next gateway follows [docs/adding-a-gateway.md](docs/adding-a-gateway.md).
- **Not started:** the three goals above as integrations (Encyclopedia
  evidence kind, MCP firmware verdicts, golden-corpus gate). They are proposals
  until a PR lands in the consuming repo.
- **Phase 2 (original plan):** the SCS-bus emulator (`oracle/`) and the first question — which
  bus frames make `bt_luci` / `bt_device` emit WHAT 19, and what each WHO 1001
  DIM 11 mask bit means — cross-checked live on an MH200. Design:
  [docs/oracle-architecture.md](docs/oracle-architecture.md). The firmware-free
  parts are in place and unit-tested, and **boundary discovery is done for the
  MH200N**: `python -m oracle.run discover` stages the sysroot from the image,
  runs each program jailed under `qemu-arm` and writes
  `results/MH200N/010108/oracle/boundary/*.tsv`. `scsserver` drives a PIC on
  `/dev/ttyPIC` (a pty stands in), `openserver` serves OpenWebNet on TCP 20000
  under the full stack. Next is 2b: the first suite through that stack. Needs
  `qemu-user-static` (binfmt_misc with the `F` flag) and `bubblewrap`.

## License

[Apache License 2.0](LICENSE). Covers this repo's own tooling, results and
findings only — never any vendor firmware, which is not redistributed here.
The scope and copyright line are in [NOTICE](NOTICE).
