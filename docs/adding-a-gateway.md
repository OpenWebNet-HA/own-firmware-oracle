# Adding a Gateway to the Firmware Oracle

This guide documents the end-to-end playbook for adding a new gateway (gateways 3..N:
F454, MH202, MH201, MyHOMEServer1 newer revisions, etc.) into the firmware oracle.
Every step is verifiable with standard commands and produces deterministic artifacts.

---

## 1. Overview of the Ingestion Pipeline

```
  1. Catalog & Fetch
         │
         ▼
  2. Unpack & Manifest
         │
         ▼
  3. Coverage Gate (100% extraction)
         │
         ▼
  4. Target Specification (YAML)
         │
         ▼
  5. Discovery (strace syscall boundary facts)
         │
         ▼
  6. Configuration & Gateway Harness Validation
         │
         ▼
  7. Suite Execution & Determinism Check (Zero Diff)
         │
         ▼
  8. Cross-Gateway Comparison & Findings
```

---

## 2. Step-by-Step Playbook

### Step 1: Catalog & Fetch

1. Add the product and version entry to `catalog.tsv`:
   - Specify product name (e.g., `F454`), version string (e.g., `020051`), source URL, SHA-256 hash, and wrapper format (`zip`, `fwz`, `tar.gz`).
2. Run the fetch tool:
   ```bash
   python -m fetch --product <Product> --version <Version>
   ```
   **Done when:** The downloaded archive in `~/.cache/own-fw/` matches the expected SHA-256.

### Step 2: Unpack & Manifest

1. Unpack the image layers and generate the manifest:
   ```bash
   python -m unpack ~/.cache/own-fw/sha256/<hash>.<ext> -o results/<Product>/<Version>/manifest.tsv
   ```
2. Verify that the unpacker identifies all container and filesystem formats (zip, fwz, tar, ext4, cramfs, squashfs, gunzip).
   **Done when:** `results/<Product>/<Version>/manifest.tsv` exists, listing every file, symlink, and directory with SHA-256 and size.

### Step 3: Coverage Gate

1. Verify that unpacking is complete and lossless:
   ```bash
   python -m tools.guard
   ```
   **Done when:** All files are accounted for and no unextracted binary blobs or untracked payload residues remain.

### Step 4: Target Specification (`oracle/targets/<Product>/<Version>.yaml`)

Create the target YAML describing the gateway runtime environment:

1. **`sysroot`**: Manifest prefix expression pointing to the root filesystem layer (and optional application overlay layers):
   ```yaml
   product: MyHomeServer1
   version: "028206"
   sysroot:
     - "SMARTGW_028206.fwz!btweb_only.ext4.gz.sha256.sig.zip!btweb_only.ext4.gz~gunzip:"
   ```
2. **Bus Configuration**:
   - `bus_device`: UART device node opened by the bus server daemon (e.g. `/dev/ttyPIC` on MH200N, `/dev/ttymxc2` on MyHomeServer1, `/dev/ttyS0` on older targets).
   - `bus_framer`: Protocol framing dialect (`line` for `$`-framed lines ending in `\r`, or `idle:N`).
   - `bus_responder`: Simulated bus responder (`pic` for Bticino PIC coprocessor, or `silent`).
   - `own_auth`: OpenWebNet authentication method (`none` or `ip-allow`).
3. **Programs**:
   Pin all daemons by manifest path, SHA-256, and role:
   - `supervisor`: Process monitor (`bt_processi` or `bt_daemon`).
   - `own_server`: OpenWebNet daemon (`openserver`, typically port 20000).
   - `bus_server`: SCS bus driver (`scsserver`, typically port 20001).
   - `translator`: Subsystem translators (`bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `coso`).
4. **Runtime**:
   - `cwd`: Daemon working directory (usually `/home/bticino`).
   - `env`: Environment variables (e.g., `LD_LIBRARY_PATH: /home/bticino/lib`).
   - `tmpfs`: Writable guest tmpfs directories (e.g., `/tmp`, `/var/volatile`).
   - `dirs`: Empty guest directories created at boot.
   - `links`: Guest symlinks required by firmware daemons.
   - `files`: Simulated board files (e.g., `/sys/firmware/devicetree/base/model` or `/home/bticino/cfg/hosts`).
   - `devices`: Device mapping (e.g., `/dev/ttymxc2: pty`).
   - `stack_config`: Path to `stack_open.xml`.
   - `kernel_release`: Emulated kernel release (e.g., `2.4.19` for ARMv4/MH200N, `5.10.35` for Cortex-A7/MyHomeServer1).

### Step 5: Discovery

Run trace discovery for each program in the target spec:
```bash
python -m oracle.run discover oracle/targets/<Product>/<Version>.yaml \
  --image <path_to_image> \
  --program <program_name> \
  --seconds 5
```
**Done when:** `results/<Product>/<Version>/oracle/boundary/<program>.tsv` is generated, cataloging:
- System calls (file opens, ioctls, socket binds, connects).
- Serial port parameters (baud rate, parity, control modes).
- Socket port bindings and translator IPC ports.

### Step 6: Configuration & Gateway Harness Validation

1. **OpenServer Configuration Architecture**:
   - Older gateways (MH200N): `openserver` reads client lists and ports from XML (`stack_open.xml`).
   - Newer Linux gateways (MyHomeServer1): `openserver` reads from INI configuration (`/home/bticino/cfg/openserver`), waiting for all daemons defined under `[Stackopen]`. `prepare_stack_open()` trims both formats to active test clients.
2. **Network Requirements**:
   - Verify whether `openserver` checks local interfaces (e.g. `ioctl(SIOCGIFADDR, "eth0")`). The oracle sandbox automatically provisions loopback, dummy `eth0`, and multicast routes (`224.0.0.0/4`) in a fully private network namespace.

### Step 7: Suite Execution & Determinism Check

1. Execute the standard test suite:
   ```bash
   python -m oracle.run suite oracle/targets/<Product>/<Version>.yaml \
     oracle/cases/lights-level.cases \
     --image <path_to_image> \
     -o results/<Product>/<Version>/oracle/full/lights-level.tsv
   ```
2. Re-run the suite into a temporary file and verify zero diff:
   ```bash
   python -m oracle.run suite oracle/targets/<Product>/<Version>.yaml \
     oracle/cases/lights-level.cases \
     --image <path_to_image> \
     -o /tmp/check.tsv
   diff -u results/<Product>/<Version>/oracle/full/lights-level.tsv /tmp/check.tsv
   ```
   **Done when:** `diff -u` exits with code 0 (exact bit-for-bit match across runs).

### Step 8: Cross-Gateway Comparison & Findings

1. Diff the resulting test records against existing gateways (e.g. MH200N vs new gateway):
   - Compare input OpenWebNet frames and generated SCS bus frames.
   - Note discrepancies, supported dimensions, NACK behaviors, or lighting levels.
2. If discrepancies or bugs in firmware are observed, document them in `findings/<Product>/`.

---

## 3. Mandatory Quality Gates

Before opening a pull request for a new gateway:
- **Test suite:** `pytest tests --cov=oracle --cov=tools --cov-branch` must report **100.00% statement and branch coverage** (`fail_under = 100`).
- **Linter:** `ruff check .` must pass with zero warnings.
- **Type checker:** `mypy --strict oracle tools` must pass with zero issues.
- **Security & clean repo:** `python -m tools.guard` must pass (no raw binaries or decompiled code).
- **Git operations:** All git commands (`git add`, `git commit`, `git push`) must be run directly from Windows PowerShell.
