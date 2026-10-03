# own-firmware-oracle

A reproducible pipeline that turns a gateway firmware image into **facts** about
the OpenWebNet protocol — frame formats, code tables, behaviour — for
interoperability with [OWNd](https://github.com/OpenWebNet-HA/OWNd) and
[MyHOME](https://github.com/OpenWebNet-HA/MyHOME).

It grew out of [discussion #613](https://github.com/orgs/OpenWebNet-HA/discussions/613)
and @gdluck's firmware-as-oracle work in
[OWNd#77](https://github.com/OpenWebNet-HA/OWNd/pull/77).

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
oracle/                             emulator + simulated SCS bus  (phase 2)
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

- **Phase 1 (this repo): fetch → verify → unpack → manifest.** Done for MH200N.
- **Phase 2:** the SCS-bus emulator (`oracle/`) and the first question — which
  bus frames make `bt_luci` / `bt_device` emit WHAT 19, and what each WHO 1001
  DIM 11 mask bit means — cross-checked live on an MH200.

## License

[Apache License 2.0](LICENSE). Covers this repo's own tooling, results and
findings only — never any vendor firmware, which is not redistributed here.
The scope and copyright line are in [NOTICE](NOTICE).
