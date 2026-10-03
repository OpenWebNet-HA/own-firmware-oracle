# Oracle architecture (phase 2)

Phase 1 turns a firmware image into a verified `manifest.tsv`. Phase 2 runs
the gateway's own translator programs against a simulated SCS bus and records
what they do. The records are TSV files; findings cite rows in them.

This document fixes the moving parts, the boundaries between them and the
order in which they get built. The code under `oracle/` follows it.

## 1. Questions the oracle answers

| Direction | Input | Observed | Example question |
|---|---|---|---|
| **down** | an OpenWebNet frame sent to the gateway | the gateway's reply (`ACK` / `NACK` / none) and every bus frame it emits | Does `*#1*31*#1*100*0##` reach the bus? (gdluck: no) |
| **up** | a bus frame injected on the simulated bus | every OpenWebNet frame the gateway emits on its event side | Which bus frame makes `bt_luci` emit `*1*19*31##`? |
| **sequence** | an ordered mix of down and up steps | the same, per step | After a WHAT 19 event, what does `*#1*31##` return? |

Every answer is a **firmware fact** about one image: it says what that
firmware does, not what the plant does. gdluck's caveat holds here too: a
silent bus can mean "no device answered", so a missing reply proves nothing
on its own; an emitted frame does.

What the oracle **cannot** answer: what a WHO 1001 DIM 11 mask bit *means*.
The actuator sets those bits; the gateway only translates them. The oracle
can show which injected bus bits come out at which mask position. A finding
may say "position N is set iff stimulus S" only when a row shows that toggle;
positions that never flip stay unresolved. Positions are numbered 1-based
from the left, the convention `EVID-MH200-WHAT19-FAULT` uses ("bits 6 and 21
cleared" in `111110111111111111110111`).

### 1.1 Which evidence wins

Live evidence outranks the oracle, and the oracle outranks OWNd, **for the
product and firmware the evidence was captured on**. A disagreement is written
down as a `diverge`; it never rewrites a capture. The first live anchor,
`EVID-MH200-WHAT19-FAULT`, was captured on an **MH200 running firmware
2.1.0**, while the first image here is **MH200N 1.1.8**: a different product
and firmware. Rows checked against it are cross-product until an MH200 2.1.0
image is in the catalog, and the check says so.

Suites only use addresses that are already public (`74` from that capture);
other installation-specific WHEREs stay out of the repo.

## 2. Constraints

1. **Ground rules (README).** Black-box first: we run the programs and watch
   their inputs and outputs. No disassembly, no decompiled code, no vendor
   bytes in the repo or in CI artifacts. `tools/guard.py` stays the backstop,
   and also refuses any file containing a NUL byte, so a blob renamed to
   `.tsv` cannot slip through.
2. **Reproducible.** The same image, target, harness and case file give a
   byte-identical TSV. No timestamps, no PIDs, no ports in a record.
3. **Runs where the firmware may run.** Locally, or in the protected
   `firmware` environment on `main` (`oracle.yml`). Never on a fork PR.
4. **Untrusted code.** The firmware is 2012 ARM userland we did not write. It
   runs in a sandbox with no network and no host filesystem beyond its own
   staged copy.
5. **Per image, comparable across images.** The same case file runs against
   every catalogued gateway, so a diff of two TSVs is a diff of two firmwares.

## 3. System overview

```mermaid
flowchart LR
  subgraph phase1 [phase 1, exists]
    CAT[catalog/*.yaml] --> FETCH[fwfetch.py] --> UNPACK[unpack.py] --> MAN[(manifest.tsv)]
  end
  subgraph phase2 [phase 2, this document]
    TGT[oracle/targets/*.yaml] --> STAGE[stage: sysroot in a temp dir]
    MAN -. sha256 check .-> TGT
    FETCH --> STAGE
    STAGE --> SB
    subgraph SB [sandbox: bwrap, no network, temp dir only]
      DRV[driver] -- OWN text --> OWNSIDE[OWN adapter]
      OWNSIDE <--> FW[firmware processes under qemu-arm]
      FW <--> BUSSIDE[bus adapter: pty]
      BUSSIDE <--> BUS[simulated SCS bus + responders]
      BUS -- frames --> DRV
    end
    CASES[oracle/cases/*] --> DRV
    DRV --> REC[(results/.../oracle/*.tsv)]
  end
  REC --> FIND[findings/*.md]
  LIVE[read-only live probe on a real gateway] -.cross-check.-> FIND
```

| Component | Module | Firmware needed? | Built |
|---|---|---|---|
| Target spec | `oracle/target.py`, `oracle/targets/` | no (checks hashes against the manifest) | 2a |
| Case files | `oracle/cases.py`, `oracle/cases/` | no | 2a |
| Sandbox + qemu command lines | `oracle/sandbox.py` | no (builds argv only) | 2a |
| Boundary discovery | `oracle/discover.py` | yes, to produce the trace; parser is not | 2a |
| Simulated bus, framers, responders | `oracle/bus.py` | no | 2a |
| Driver loop | `oracle/driver.py` | no (talks to a `Target` protocol) | 2a |
| Recorder | `oracle/record.py` | no | 2a |
| Stage (sysroot) | `oracle/stage.py` | yes | 2b |
| `QemuTarget` (real adapters) | `oracle/qemu_target.py` | yes | 2b, after discovery |
| Live cross-check | `tools/live_probe.py` | no (needs a real gateway) | 2c |

Everything marked "no" is unit-tested in `pr.yml` on synthetic fixtures, like
phase 1.

## 4. The two cut points

The firmware has two edges we must replace: where OpenWebNet text comes in,
and where SCS bytes go out. Picking those cuts is the main design decision.

### 4.1 What the MH200N 1.1.8 manifest shows

From `results/MH200N/010108/manifest.tsv` (file names and types only):

* translators in the app zip: `bt_luci`, `bt_device`, `bt_termo`, `bt_multi`,
  `bt_lighting`, `bt_alarm`, `bt_energia`, `bt_supervisione`, …;
* `scsserver` and `libopenscs.so.0.0` in both the app zip and the recovery
  rootfs;
* `cfg/stack_open.xml` (application) and `cfg/conf.xml` (recovery):
  configuration, likely the wiring between those processes;
* kernel `2.4.19-rmk7-pxa2-btweb`: ARM, XScale PXA2xx, no hardware FPU.

The working hypothesis is: translators link `libopenscs` and talk to
`scsserver`; `scsserver` owns the device node that reaches the bus
transceiver. **2a's first job is to confirm or refute this by observation**
(section 5), not by reading code.

### 4.2 Bus side

| Option | How | Faithful | Cost | Verdict |
|---|---|---|---|---|
| **B1 hardware cut** | run the vendor `scsserver` too; the device node it opens is a symlink in the staged sysroot to a pty we own | highest: the vendor `scsserver` stays in the loop | learn the transceiver byte stream from what `scsserver` writes | **default** |
| B2 socket cut | replace `scsserver` with our own server speaking the client protocol of `libopenscs` | loses `scsserver` behaviour | re-implement a vendor IPC protocol | fallback if B1 needs hardware we cannot fake (ioctls with no file equivalent) |
| B3 libc shim | `LD_PRELOAD` shim wrapping libc `open` / `ioctl` / `read` / `write` for the bus device only | high; also keeps the boundaries of each `write()` | an ARM **OABI** cross-toolchain for a 2002-era libc; still runs inside `qemu-arm` | fallback when discovery shows ioctls a pty cannot answer |
| B4 vendor-library cut | shim replacing `libopenscs` functions | — | needs the library's function signatures, i.e. reading the binary | **rejected** (ground rules) |
| B5 system emulation | `qemu-system-arm` booting the 2.4.19 PXA kernel with a modelled bus device | highest | a PXA board model plus a device model for the bus hardware | not v1 |

The pty trick works because `qemu-arm -L <sysroot>` resolves an absolute path
inside the sysroot first: `/dev/ttyS1` opened by the guest becomes
`<sysroot>/dev/ttyS1`, which we point at our pty slave. If `scsserver` issues
termios ioctls, a pty answers them like a UART.

Every option runs the ARM binary under `qemu-arm` on an x86 host, a shim
included: an `LD_PRELOAD` library is guest code too. So the order is pty
first (no guest code of ours), libc shim second, never "shim instead of
qemu". The adapter that produced a row is in its header, so a pty result is
never silently compared with a shim or system-emulation one. gdluck's
`oracle2/`, if published, fits as one more adapter behind the same `Target`
protocol.

### 4.3 OpenWebNet side

| Option | How | When |
|---|---|---|
| **O1 full stack** | boot every process the init scripts start; connect to TCP 20000 inside the sandbox's network namespace like OWNd does (command and event sessions) | default: tests routing, sessions and ACK/NACK exactly as a client sees them |
| O2 unit | start one translator; feed it on whatever channel it reads OWN text from | when the full stack does not boot under emulation (gdluck could not emulate `openserver` on MyHOMEServer1) |

The harness used is part of every record's header (`harness=full` or
`harness=unit:<binary>`), because the answers can differ: a frame `openserver`
rejects never reaches a translator.

## 5. Boundary discovery (phase 2a, first step)

Run the target under `qemu-arm -strace` inside the sandbox with an empty bus
and capture the syscall trace. `oracle/discover.py` reduces it to
`results/<product>/<version>/oracle/boundary.tsv` (the rows below are
illustrative; nothing has been traced yet):

```
# product=MH200N version=010108 target=scsserver target_sha256=c6a6…
# oracle_version=1
kind	detail	result
open	/dev/ttyS1 O_RDWR|O_NOCTTY	ok
ioctl	fd=/dev/ttyS1 req=0x5401	ok
bind	unix:/tmp/scs.sock	ok
connect	inet:127.0.0.1:20000	ECONNREFUSED
```

These are observations of behaviour (which files, sockets and ioctl request
numbers a program uses), not code. They decide B1 vs B2 and O1 vs O2 per
target and fill the `boundary:` block of `oracle/targets/<product>/<version>.yaml`.
Until that block is filled, a target cannot run cases (`target.py` refuses).

Discovery is also where emulation risks surface early:

* **OABI.** Linux 2.4 ARM binaries use the old syscall ABI. `qemu-arm`
  supports it (non-EABI ELF header → OABI path); confirm on the first run.
* **FPA floats.** No FPU on the PXA; the binaries may use FPA instructions the
  kernel emulated (NWFPE). `qemu-arm` linux-user carries the same emulator.
* **Kernel version checks.** `-r 2.4.19` makes `uname` report the original
  release.
* **Hardware the bus does not cover**: watchdog, MTD flash, RTC ioctls. Each
  gets a file, a pty or `/dev/null` in the staged sysroot, recorded in the
  target spec. A program that needs more than that is a B2 / O2 case.

## 6. Components

### 6.1 Target spec (`oracle/targets/<product>/<version>.yaml`)

Names the programs to run and pins each one to a manifest row by path and
SHA-256. `target.py` loads it, validates every name and checks every hash
against the committed `manifest.tsv`, so a TSV can never claim a binary the
manifest does not contain.

```yaml
product: MH200N
version: "010108"
sysroot:                         # layers overlaid in order, by manifest prefix
  - "…/ubtweb_only.gz~payload~gunzip:"   # rootfs
  - "…/btweb_app.zip!"                   # application
programs:
  bt_luci:   { path: home/bticino/bin/bt_luci,   sha256: 1ef8… }
  scsserver: { path: home/bticino/bin/scsserver, sha256: c6a6… }
boundary:
  status: pending                # discovery not run yet
```

### 6.2 Sandbox (`oracle/sandbox.py`)

One `bwrap` around the **whole run** (driver + firmware), not around each
process, so the driver can reach the firmware's loopback sockets:

* `--unshare-all` (no network beyond a private loopback, no IPC, own PID
  namespace), `--die-with-parent`, `--clearenv` with `TZ=UTC`;
* host `/usr`, `/lib*` read-only (Python and `qemu-arm`), the per-run work
  dir read-write, nothing else of the host;
* the staged sysroot is mounted **read-only**, with a tmpfs over each
  directory discovery shows the program writes to (`/tmp`, `/var`, ...); it is
  deleted after the run like `unpack.py` does;
* each firmware process gets a CPU-time and an address-space limit
  (`prlimit`), so a runaway program becomes a `crash` or `timeout` row, not a
  stuck job.

Inside, each firmware process is `qemu-arm -L <sysroot> -r 2.4.19 <binary>`.

### 6.3 Simulated bus (`oracle/bus.py`)

* **`Port`**: bytes in and out of the bus adapter (a pty master in production,
  a queue in tests).
* **`Framer`**: splits the byte stream into frames. `IdleGapFramer` (a frame
  is a burst followed by silence) is the default: it assumes nothing about
  SCS. `DelimitedFramer` (start/end bytes; the community `A8 … A3` framing)
  is a hypothesis a finding has to earn, like any checksum; the core has no
  SCS parser. A pty loses the boundaries between the firmware's `write()`
  calls, which is one reason to fall back to the libc shim.
* **`Responder`**: the simulated devices on the bus. `Silent` (no device),
  `AckAll` (every frame acknowledged), and scripted responders that answer
  status requests for a given address. The responder is part of the record
  header, because "no device answered" changes what firmware does.
* **`Bus`**: logs every frame with a sequence number and a direction
  (`gw>bus`, `bus>gw`), injects frames, and `settle()`s: polls until no byte
  has moved for `quiet_ms`, or `max_ms` passed. Time comes from an injected
  clock so tests run instantly.

Raw bytes are the evidence. Decoding SCS bytes into fields is a separate,
optional layer of our own; a finding that relies on a decode says so.

### 6.4 Case files (`oracle/cases/<suite>.cases`)

Plain text, one step per line, committed:

```
; lights: point on/off with speed (gdluck fix 7)
down  *1*0*31##
down  *#1*31*#1*100*0##
up    a8 31 00 12 01 22 a3
```

* `down` takes OpenWebNet text, `up` hex bytes, `;` starts a comment (`#` is
  part of OpenWebNet).
* Malformed frames are allowed on purpose: what the firmware does with them is
  a fact too. A line only has to be TSV-safe printable ASCII.
* A step is identified by `(direction, input)`, not its line number, so
  editing a suite does not renumber the others. Duplicates are an error.
* Large grids (every WHAT × address form) are produced by a generator script
  and the **output** is committed, so a suite is always reviewable text.
* A sequence suite (`.seq`) keeps the steps in order and records them as one
  unit; ordinary suites are independent steps, sorted in the record.

### 6.5 Driver (`oracle/driver.py`)

Runs a suite against anything implementing the `Target` protocol
(`send_own`, `inject_bus`, `settle`, `alive`, `restart`):

1. clear the bus log, perform the step, `settle()`;
2. collect the gateway's reply and every frame emitted since the step;
3. classify: `out` (something emitted), `silent`, `crash` (process died →
   restart before the next step), `timeout` (still busy at `max_ms`);
   in a `.seq`, every step after a crash is `skipped`;
4. hand the row to the recorder. Outputs are tagged by side, `bus:<hex>` or
   `own:<frame>`, so one row shows both what reached the bus and what the
   gateway said on its event side.

Output produced while a program boots belongs to no step and is drained
before the first one.

Each independent step gets a fresh process (`reset=each`, the default), so
no step can see state another left behind. Sharing a process across N steps
(`reset=batch-N`) is faster under qemu but is allowed only once the
reset-each TSV for that suite is stable and the batch TSV is identical to it.
A suite that is order-sensitive must be a `.seq`.

### 6.6 Recorder (`oracle/record.py`)

`results/<product>/<version>/oracle/<harness>/<suite>.tsv`, where `<harness>`
is `full` or `unit-<program>` (illustrative rows; the bus bytes are
placeholders):

```
# product=MH200N version=010108
# image_sha256=e32d…
# harness=unit:bt_luci target_sha256=1ef8…
# adapter=pty-1 reset=each
# bus=pty framer=idle:20 responder=silent settle_ms=300
# suite=lights-level suite_sha256=…
# oracle_version=1
direction	input	reply	verdict	output
down	*#1*31*#1*100*0##	nack	silent	-
down	*1*0*31##	ack	out	bus:a8 31 00 12 01 22 a3 | own:*1*0*31##
up	a8 31 00 12 01 22 a3	-	out	own:*1*0*31##
```

* rows of a `.cases` suite sorted by `(direction, input)`, a `.seq` in step
  order; within a row, outputs keep emission order, joined with ` | `;
* every header key that can change an answer is in the header, so
  `plan.py` can key staleness on (image sha256, target sha256, suite sha256,
  oracle version), just as phase 1 keys on (image sha256, tool version);
* fields are escaped like `manifest.tsv`, plus non-ASCII and `|`, so one row
  stays one ASCII line whatever the firmware emits; a zero diff on re-run is
  the reproducibility check.

Time never enters a row, but it is not virtual either: under `qemu-user` the
firmware's own timeouts run on the host clock. `settle_ms` is generous, and a
suite counts as stable only after two runs give a zero diff.

### 6.7 Checker (derived, not part of the record)

`tools/check.py` (2c) reads an oracle TSV and writes
`results/<product>/<version>/checks/<suite>.tsv` with two extra columns per
row: `ownd` (the pinned OWNd version's parse of each `own:` output, or the
parse error; a failure is a value, never a dropped row) and `live`
(`agree` / `diverge` / `unchecked`, with the evidence id). It is a separate
file so an OWNd bump or a new capture never makes an oracle TSV stale.

### 6.8 Evidence labels and the live cross-check

Findings use gdluck's labels, so OWNd#77 and this repo read the same way:

| Label | Source here |
|---|---|
| **Firmware** | a row in an oracle TSV |
| **Gateway** | a read-only probe on a real gateway (`tools/live_probe.py`: status and dimension requests only, refuses anything else) |
| **Emitted** | firmware output fed through OWNd's parser |
| **Code** | follows from OWNd / MyHOME source alone |

A finding about the MH200 should carry **Firmware** and **Gateway** both;
MyHOMEServer1 results from OWNd#77 do not transfer to the MH200 without a row
from this repo.

## 7. CI

| Workflow | Runs | Firmware |
|---|---|---|
| `pr.yml` | lint (`tools tests oracle`), guard, unit tests of every "no firmware" component | never |
| `oracle.yml` (template) | phase 1 per stale image; phase 2 adds one job per stale (image, target, suite) | protected `firmware` env, `main` only |

The oracle job installs `qemu-user` and `bubblewrap`, uploads **only** TSVs,
and the publish job's allow-list grows from `*/*/manifest.tsv` to
`*/*/manifest.tsv` and `*/*/oracle/**/*.tsv`. Sandbox and network isolation
hold in CI too: the runner's network is irrelevant to a process in a private
network namespace.

## 8. Build order

| Step | Delivers | Done when |
|---|---|---|
| **2a scaffold** | everything marked "no firmware" in section 3, with tests | `pr.yml` green |
| **2a discovery** | `boundary.tsv` for `scsserver`, `bt_luci`, `bt_device`; filled `boundary:` blocks | a trace per program, OABI / FPA confirmed or ruled out |
| **2b first light** | `stage.py`, `QemuTarget`, one down suite (`lights-level`) on MH200N | zero diff on re-run; `*1*1*31##` gives a bus frame |
| **2c WHAT 19** | up suite over the bus frames that could carry the fault, through `bt_luci` then `bt_device`; `check.py` against `EVID-MH200-WHAT19-FAULT` | finding in `findings/MH200N/`: which bus input gives `*1*19*74##` and which mask positions toggle, or that neither program emits it (MyHOME#593, #611) |
| **2d replay OWNd#77** | gdluck's frame lists as suites, run on MH200N | per fix: holds / differs on MH200N |
| **2e second image** | F454 2.0.51 or MH202 1.0.24 in the catalog; same suites | a cross-image TSV diff |

## 9. Open risks

* **Full stack may not boot** under user-mode emulation (init expects
  hardware). O2 per translator is the fallback; the header says which ran.
* **Clock-dependent output** (`bt_device` time frames). Guest time is host
  time under qemu-user; such rows are normalised by the recorder and marked,
  rather than faked with a guest-side library we would have to write.
* **Framing hypothesis wrong.** `IdleGapFramer` still records raw bursts, so
  the evidence survives a wrong guess; only the decode layer changes.
* **Licence of the case lists.** Frame lists taken from OWNd#77 are facts
  about a protocol; credit gdluck in the suite comment.
