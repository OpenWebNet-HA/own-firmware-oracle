# Quickstart: Running Suites, Proposing Cases & Querying the Oracle

This guide covers three common workflows:
1. **Run a test suite locally in under 10 minutes** (Linux or WSL2 Ubuntu).
2. **Propose a new `.cases` test suite without running QEMU locally** (CI computes and double-verifies the verdicts).
3. **Compare gateways, diff PR verdicts, and run the live hardware canary**.

---

## 1. Run One Suite Locally in < 10 Minutes (Ubuntu / WSL2)

The firmware oracle executes vendor ARM/AArch64 userland binaries inside an isolated `bubblewrap` + `qemu-user` sandbox.

### Prerequisites (Ubuntu 22.04 / 24.04 or WSL2)

```bash
sudo apt-get update && sudo apt-get install -y \
  bubblewrap qemu-user qemu-user-static squashfs-tools e2fsprogs unzip python3-venv
```

Create and activate the virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements/oracle.txt -r requirements/dev.txt
```

### Step-by-Step: Fetch, Unpack & Run `lights-level` on MH200N

```bash
# 1. Fetch and SHA-256 verify the official MH200N 1.1.8 firmware archive (~4 MB):
IMG=$(python tools/fwfetch.py catalog/MH200N/010108.yaml)

# 2. Verify that unpacking matches the committed manifest.tsv:
python tools/unpack.py catalog/MH200N/010108.yaml --image "$IMG" --check

# 3. Run the lights-level suite against the emulated MH200N daemon stack:
python -m oracle.run suite \
  oracle/targets/MH200N/010108.yaml \
  oracle/cases/lights-level.cases \
  --image "$IMG" \
  -o /tmp/mh200n-lights-level.tsv

# 4. Verify byte-for-bit determinism against the committed oracle record:
diff -u results/MH200N/010108/oracle/full/lights-level.tsv /tmp/mh200n-lights-level.tsv
```

When `diff -u` exits with `0`, your local environment reproduces the exact committed firmware behaviour.

---

## 2. Propose a New `.cases` Suite Without Running QEMU Locally

You **do not** need firmware images or QEMU installed locally to add new protocol test cases. The GitHub Actions `suites.yml` workflow automatically runs every `.cases` and `.seq` file across all 11 emulated gateways, verifies determinism by running each new/stale suite twice, and publishes the resulting TSVs to the `oracle/update-suites` branch.

### Step 1: Create `oracle/cases/<your-suite>.cases`

Each line is `<direction> <frame>`:
- `down <openwebnet_frame>`: sent to `openserver` on TCP port 20000; records the ACK/NACK reply and any resulting SCS bus frames.
- `up <hex_bytes>`: injected onto the simulated SCS bus; records any resulting OpenWebNet frames emitted to monitoring clients.

Example (`oracle/cases/lights-blink.cases`):

```text
; Lighting blink commands (WHO=1 WHAT=20..29)
down *1*20*21##
down *1*21*21##
down *1*22*21##
```

### Step 2: Validate Syntax Locally

Run the fast unit test suite (requires only Python + `pytest`, no QEMU or Linux needed):

```bash
pytest tests/test_oracle.py -k test_committed_suites_load
python tools/guard.py
```

### Step 3: Open a Pull Request

When your PR merges to `main` (or when `suites.yml` is triggered), CI executes the new suite across all emulated gateways, verifies two consecutive runs match byte-for-byte, and opens/updates the `oracle/update-suites` branch with the new `results/<Product>/<Version>/oracle/full/<your-suite>.tsv` files and refreshed `results/mcp_index.json`.

---

## 3. Compare Gateways, Diff PR Verdicts & Run the Live Canary

All analysis tools operate directly on the committed TSV corpus and `results/mcp_index.json`—no emulator required:

```bash
# Compare a single frame across all emulated gateways (with decoded SCS bytes):
python tools/compare.py frame "*1*1*31##"

# List all frames in a suite where gateways disagree on reply, verdict, or SCS payload:
python tools/compare.py divergences --suite sound-who22

# Render a full gateway x input Markdown matrix for a suite:
python tools/compare.py matrix --suite lights-level

# Summarize behavioural changes between two git revisions (collapsing header refreshes):
python tools/verdict_diff.py --base origin/main --head HEAD

# Run the OWNd parser & cross-tier conformance report:
python tools/check.py --all --report

# Validate the read-only live hardware canary suite:
python tools/live_probe.py --suite oracle/canary/canary-live.cases --validate-only

# Export an oracle TSV into an OpenWebNet-Encyclopedia evidence bundle:
python tools/export_evidence.py results/MH200N/010108/oracle/full/what19.tsv \
  --out-dir /tmp/cap-mh200n-what19-20261015 \
  --date 2026-10-15
```
