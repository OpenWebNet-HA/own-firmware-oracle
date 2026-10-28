# WHO 13 gateway identification: which requests a gateway answers, and two that crash it

**Epistemic status:** `firmware_observed` (emulated, `harness=full`), 12 gateway
images. Rows from the `suites` workflow on `main` after #59 (branch
`oracle/update-suites`, commit `112d916`, "oracle: new and refreshed suite
results"): a new suite enters `results/` only when a second run reproduces the
file byte for byte (`tools/suite_plan.py`, verdict `unstable` otherwise). The
crash rows were also reproduced in three fresh local runs each. Nothing here is
`experimentally_confirmed`; the live anchor is one F461 `2.0.11`.

**Suite:** [`oracle/cases/who13-identification.cases`](../oracle/cases/who13-identification.cases)
(9 independent steps, suite SHA-256
`99fa591dd8a72109824799045d5ee26d54205f5063265e80c80c57b873a325e5`; the
workflow ran them with `reset=each` on F450 `020010` and `reset=0` on the other
eleven targets, the reset policy of each target's committed suites). Rows:
`results/<product>/<version>/oracle/full/who13-identification.tsv`; image,
target SHA-256 and reset policy are in each file's header. DIMENSION 23, 24 and
the WHERE form `*#1013*0*1##` were already covered by `trace-corpus.cases` on
all twelve gateways (since 2026-10-15); this suite adds DIMENSION 10, 11, 12 and
`*#1013**1##` and puts the nine requests side by side.

## Question

A client that adds a gateway by hand (no SSDP) has only WHO 13 and WHO 1013 to
learn what it talks to. MyHOME's runtime asks `*#13**15##` first and `*#1013**1##`
only when the device type is the shared code `200` (MyHOME#420); its manual
config flow had started to ask the MAC (`*#13**12##`) as well. The suite asks:

1. Which of DIMENSION 15, 16, 10, 11, 12, 23, 24 and WHO 1013 `OBJECT_MODEL` does
   each firmware answer?
2. Does any of them harm the gateway?

DIMENSION 19 (uptime) and 27 (host name) are left out: their values depend on the
run, not on the firmware.

## Method

In the CI jail: the daemons see the jail's dummy `eth0` (`192.168.1.1/24`) and
the pinned MAC `02:00:00:00:00:01` (own-firmware-oracle#57), which is what makes
the IP and MAC **values** below reproducible. On F450 every step ran on a freshly
started gateway (`reset=each`); on the other eleven the nine steps ran in the
order of the table on one gateway start (`reset=0`), the harness restarting the
target after a `crash` row (`oracle/driver.py`), so the three crash rows of
H4684 and MH200 are each a fresh daemon exit and the rows after them come from
a restarted gateway. Earlier local runs with `--no-sandbox` and `reset=each` gave
the same verdicts with the container's own interface values.

## Rows

Cells: `ack, out <frame>` is the ACK followed by that OpenWebNet reply; `ack,
silent` is an ACK with no reply after it; **crash** is a `nack` after which the
target was no longer alive (verdict `crash`, `oracle/driver.py`).

| Frame | F450 `020010` | F453 `010100` | F453AV `030014` | F454 `020051` | F459 `020105` | F460 `020012` | F461 `020011` | H4684 `020054` | MH200 `020102` | MH200N `010108` | MH202 `010024` | MyHomeServer1 `028206` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `*#1013**1##` | ack, out `*#1013**1*0*0*0*0##` | ack, out `*#1013**1*0*0*0*0##` | ack, out `*#1013**1*0*0*0*0##` | ack, out `*#1013**1*0*0*0*0##` | ack, out `*#1013**1*0*0*0*0##` | ack, out `*#1013**1*0*0*0*0##` | ack, out `*#1013**1*0*0*0*0##` | ack, out `*#1013**1*19##` | ack, out `*#1013**1*4##` | ack, out `*#1013**1*0*0*0*0##` | ack, out `*#1013**1*0*0*0*0##` | ack, out `*#1013**1*0*0*0*0##` |
| `*#1013*0*1##` | ack, out `*#1013**1*0*0*0*0##` | ack, out `*#1013**1*0*0*0*0##` | ack, out `*#1013**1*0*0*0*0##` | ack, out `*#1013**1*0*0*0*0##` | ack, out `*#1013**1*0*0*0*0##` | ack, out `*#1013**1*0*0*0*0##` | ack, out `*#1013**1*0*0*0*0##` | ack, out `*#1013**1*19##` | ack, out `*#1013**1*4##` | ack, out `*#1013**1*0*0*0*0##` | ack, out `*#1013**1*0*0*0*0##` | ack, out `*#1013**1*0*0*0*0##` |
| `*#13**10##` | ack, out `*#13**10*192*168*1*1##` | ack, silent | ack, silent | ack, out `*#13**10*192*168*1*1##` | ack, out `*#13**10*192*168*1*1##` | ack, out `*#13**10*192*168*1*1##` | ack, out `*#13**10*192*168*1*1##` | **crash** | **crash** | ack, silent | ack, out `*#13**10*192*168*1*1##` | ack, out `*#13**10*192*168*1*1##` |
| `*#13**11##` | ack, out `*#13**11*255*255*255*0##` | ack, silent | ack, silent | ack, out `*#13**11*255*255*255*0##` | ack, out `*#13**11*255*255*255*0##` | ack, out `*#13**11*255*255*255*0##` | ack, out `*#13**11*255*255*255*0##` | **crash** | **crash** | ack, silent | ack, out `*#13**11*255*255*255*0##` | ack, out `*#13**11*255*255*255*0##` |
| `*#13**12##` | ack, out `*#13**12*2*0*0*0*0*1##` | ack, silent | ack, silent | ack, out `*#13**12*2*0*0*0*0*1##` | ack, out `*#13**12*0*0*0*0##` | ack, out `*#13**12*0*0*0*0##` | ack, out `*#13**12*0*0*0*0##` | **crash** | **crash** | ack, silent | ack, out `*#13**12*2*0*0*0*0*1##` | ack, out `*#13**12*0*0*0*0##` |
| `*#13**15##` | ack, out `*#13**15*200##` | ack, out `*#13**15*0##` | ack, out `*#13**15*0##` | ack, out `*#13**15*200##` | ack, out `*#13**15*200##` | ack, out `*#13**15*200##` | ack, out `*#13**15*200##` | ack, out `*#13**15*13##` | ack, out `*#13**15*4##` | ack, out `*#13**15*0##` | ack, out `*#13**15*200##` | ack, out `*#13**15*200##` |
| `*#13**16##` | ack, out `*#13**16*2*0*10##` | ack, out `*#13**16*1*1*0##` | ack, out `*#13**16*3*0*14##` | ack, out `*#13**16*2*0*51##` | ack, out `*#13**16*2*1*5##` | ack, out `*#13**16*2*0*12##` | ack, out `*#13**16*2*0*11##` | ack, out `*#13**16*2*0*54##` | ack, out `*#13**16*2*1*2##` | ack, out `*#13**16*1*1*8##` | ack, out `*#13**16*1*0*24##` | ack, out `*#13**16*2*82*6##` |
| `*#13**23##` | ack, out `*#13**23*2*6*32##` | ack, out `*#13**23*##` | ack, out `*#13**23*##` | ack, out `*#13**23*2*6*32##` | ack, out `*#13**23*2*6*32##` | ack, out `*#13**23*4*14*78##` | ack, out `*#13**23*4*14*78##` | ack, out `*#13**23*2*0*5##` | ack, out `*#13**23*0*0*9##` | ack, out `*#13**23*##` | ack, out `*#13**23*2*6*32##` | ack, out `*#13**23*5*10*35##` |
| `*#13**24##` | ack, out `*#13**24*##` | ack, out `*#13**24*3*0*1##` | ack, out `*#13**24*3*0*1##` | ack, out `*#13**24##` | ack, out `*#13**24*##` | ack, out `*#13**24*##` | ack, out `*#13**24*##` | ack, out `*#13**24*3*0*0##` | ack, out `*#13**24*3*0*5##` | ack, out `*#13**24*3*0*1##` | ack, out `*#13**24*##` | ack, out `*#13**24*2*23*10##` |

The crash rows were reproduced in three fresh runs on each of H4684 `020054` and
MH200 `020102` (`work/oracle-run/crash-recheck`, `pr-suites`), with `*#13**15##`,
`*#13**16##` and `*#1013**1##` answered normally before and after.

## What the rows say

- **Device type and firmware** (`*#13**15##`, `*#13**16##`) are answered by all
  twelve. The shared code `200` comes from F450, F454, F459, F460, F461, MH202 and
  MyHomeServer1; F453, F453AV and MH200N answer `0`; H4684 `13`; MH200 `4`.
- **WHO 1013 `OBJECT_MODEL`** (`*#1013**1##` and `*#1013*0*1##`) is answered by all
  twelve: `134`-class values only on H4684 (`19`) and MH200 (`4`) here; the others
  answer `0*0*0*0` with the factory configuration. Nothing crashed on it.
- **The network dimensions 10, 11 and 12 crash a daemon on the H4684 and the
  MH200** (`nack`, verdict `crash`). F453, F453AV and MH200N ACK DIMENSION 10, 11
  and 12 without a value (`ack, silent`); F459, F460, F461 and MyHomeServer1
  answer `0*0*0*0` for the MAC but the jail's IP and netmask (the MAC is not
  read from the interface, presumably from a stored configuration: an
  inference, the rows only show the value); F450, F454 and MH202 answer the
  interface's IP, netmask and MAC (`2*0*0*0*0*1`, the pinned
  `02:00:00:00:00:01`).
- **Kernel and distribution** (`*#13**23##`, `*#13**24##`) are ACKed by all
  twelve, but not all with a value: F453, F453AV and MH200N answer DIMENSION 23
  empty (`*#13**23*##`), and DIMENSION 24 is empty on F450, F459, F460, F461 and
  MH202 (`*#13**24*##`) and bare on F454 (`*#13**24##`). The kernel version is
  the `runtime.kernel_release` of the target spec, which the jail passes to
  qemu-user as `QEMU_UNAME` (`oracle/sandbox.py`): `2*6*32` for the ARMv5
  targets, `4*14*78` for F460 and F461, `5*10*35` for MyHomeServer1. It is not
  evidence about the physical gateway's kernel: the live F461 `2.0.11` reports
  `5*10*35` (below).

For a client: ask DIMENSION 15 first, WHO 1013 only on `200`, and the network
dimensions only from a model known to answer them. OWNd's `request_mac_address`
and MyHOME's manual identification were changed accordingly.

## Live anchor

F461 `2.0.11` (2026-10-23, read-only; MAC and LAN address rewritten to the synthetic `00:03:50:00:10:23` / `192.0.2.92` used by the MyHOME trace fixture): `*#13**15*200##`, `*#13**16*2*0*11##`,
`*#13**12*0*3*80*0*16*35##`, `*#13**10*192*0*2*92##`, `*#13**11*255*255*255*0##`,
`*#13**23*5*10*35##`, `*#13**24*2*26*4##`, `*#1013**1*134*15*5*0##`: the real
gateway answers the MAC and IP the emulated one answers as `0*0*0*0` (it reads them
from its running system, not a file), and `OBJECT_MODEL 134` where the factory
configuration gives `0*0*0*0`.

## What the suite does not settle

- How the physical H4684 and MH200 behave: a daemon exit under qemu-user; the
  supervisor's restart is not observed.
- The MAC and IP values on gateways that read them from a file: factory values.
