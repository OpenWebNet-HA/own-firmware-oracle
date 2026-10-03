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
ci-templates/*.yml.txt              install into .github/workflows/
```

## Run it locally

```bash
pip install pyyaml            # + boto3 only if you fetch from R2
# e2fsprogs (debugfs) is needed to list ext filesystems; preinstalled on Linux

# A) public image — fetch + verify from the vendor:
IMG=$(python tools/fwfetch.py catalog/MH200N/010108.yaml)

# B) a local copy — no upload. Pass the WRAPPER (the file whose size + sha256
#    the catalog records), not the inner .fwz:
IMG=$(python tools/fwfetch.py catalog/MH200N/010108.yaml --from-file ./FW_MH200N_vers_010108.zip)

python tools/unpack.py catalog/MH200N/010108.yaml "$IMG" \
    -o results/MH200N/010108/manifest.tsv
python tools/guard.py
```

A clean re-run must produce a **zero diff** (sorted TSV, no timestamps) — that's
the reproducibility check.

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
