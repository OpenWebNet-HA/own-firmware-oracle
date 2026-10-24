# WHO 13 gateway identification: which requests a gateway answers, and two that crash it

**Epistemic status:** `firmware_observed` (emulated, `harness=full`). The crash rows
are from three fresh runs each; the other rows from local runs of the same images
with the same harness (qemu-user 8.2.2, Docker on macOS, `--no-sandbox`: the
network namespace of the CI jail could not be used there, see "Method"). Nothing
here is `experimentally_confirmed`; the live anchor is one F461 `2.0.11`.

**Suite:** [`oracle/cases/who13-identification.cases`](../oracle/cases/who13-identification.cases)
(9 independent steps, `reset=each`). Rows:
`results/<product>/<version>/oracle/full/who13-identification.tsv` once `suites.yml`
has produced them; they supersede the local tables below.

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

Each step on a freshly started gateway. The local runs were made with
`--no-sandbox`, so the daemons saw the container's own network interface instead
of the jail's dummy `eth0` (192.168.1.1/24): the IP and MAC **values** in the table
are therefore those of the container and differ between runs, while the
**verdicts** (who answers, who crashes) do not. In the CI jail the IP is
`192.168.1.1` and the MAC the pinned `02:00:00:00:00:01` (own-firmware-oracle#57),
which is what makes the suite reproducible there.

## Rows (local run; verdicts to be confirmed by the CI rows)

| Frame | F450 `020010` | F453 `010100` | F453AV `030014` | F454 `020051` | F459 `020105` | F460 `020012` | F461 `020011` | H4684 `020054` | MH200 `020102` | MH200N `010108` | MH202 `010024` | MyHomeServer1 `028206` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `*#1013**1##` | ack, out `*#1013**1*0*0*0*0##` | — | — | ack, out `*#1013**1*0*0*0*0##` | — | — | — | ack, out `*#1013**1*19##` | ack, out `*#1013**1*4##` | — | ack, out `*#1013**1*0*0*0*0##` | — |
| `*#1013*0*1##` | ack, out `*#1013**1*0*0*0*0##` | — | — | ack, out `*#1013**1*0*0*0*0##` | — | — | — | ack, out `*#1013**1*19##` | ack, out `*#1013**1*4##` | — | ack, out `*#1013**1*0*0*0*0##` | — |
| `*#13**10##` | ack, out `*#13**10*172*17*0*5##` | — | — | ack, out `*#13**10*172*17*0*4##` | — | — | — | **crash** | **crash** | — | ack, out `*#13**10*172*17*0*2##` | — |
| `*#13**11##` | ack, out `*#13**11*255*255*0*0##` | — | — | ack, out `*#13**11*255*255*0*0##` | — | — | — | **crash** | **crash** | — | ack, out `*#13**11*255*255*0*0##` | — |
| `*#13**12##` | ack, out `*#13**12*46*116*69*186*79*232##` | — | — | ack, out `*#13**12*26*137*158*147*25*187##` | — | — | — | **crash** | **crash** | — | ack, out `*#13**12*114*84*217*80*99*118##` | — |
| `*#13**15##` | ack, out `*#13**15*200##` | — | — | ack, out `*#13**15*200##` | — | — | — | ack, out `*#13**15*13##` | ack, out `*#13**15*4##` | — | ack, out `*#13**15*200##` | — |
| `*#13**16##` | ack, out `*#13**16*2*0*10##` | — | — | ack, out `*#13**16*2*0*51##` | — | — | — | ack, out `*#13**16*2*0*54##` | ack, out `*#13**16*2*1*2##` | — | ack, out `*#13**16*1*0*24##` | — |
| `*#13**23##` | ack, out `*#13**23*2*6*32##` | — | — | ack, out `*#13**23*2*6*32##` | — | — | — | ack, out `*#13**23*2*0*5##` | ack, out `*#13**23*0*0*9##` | — | ack, out `*#13**23*2*6*32##` | — |
| `*#13**24##` | ack, out `*#13**24*##` | — | — | ack, out `*#13**24##` | — | — | — | ack, out `*#13**24*3*0*0##` | ack, out `*#13**24*3*0*5##` | — | ack, out `*#13**24*##` | — |

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
  MH200** (`nack`, verdict `crash`). F453, F453AV and MH200N ACK DIMENSION 12
  without a value; F459, F460, F461 and MyHomeServer1 answer `0*0*0*0` for the MAC
  (they read a configuration file); F450, F454 and MH202 answer the interface's
  real MAC and IP.
- **Kernel and distribution** (`*#13**23##`, `*#13**24##`) are answered by all
  twelve (the kernel version is the one the jail reports through `QEMU_UNAME`).

For a client: ask DIMENSION 15 first, WHO 1013 only on `200`, and the network
dimensions only from a model known to answer them. OWNd's `request_mac_address`
and MyHOME's manual identification were changed accordingly.

## Live anchor

F461 `2.0.11` (2026-10-23, read-only): `*#13**15*200##`, `*#13**16*2*0*11##`,
`*#13**12*0*3*80*159*63*151##`, `*#13**10*192*168*0*92##`, `*#13**11*255*255*255*0##`,
`*#13**23*5*10*35##`, `*#13**24*2*26*4##`, `*#1013**1*134*15*5*0##`: the real
gateway answers the MAC and IP the emulated one answers as `0*0*0*0` (it reads them
from its running system, not a file), and `OBJECT_MODEL 134` where the factory
configuration gives `0*0*0*0`.

## What the suite does not settle

- How the physical H4684 and MH200 behave: a daemon exit under qemu-user; the
  supervisor's restart is not observed.
- The MAC and IP values on gateways that read them from a file: factory values.
