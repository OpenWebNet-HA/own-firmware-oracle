# WHO 13 gateway identification: which requests a gateway answers, and two that crash it

**Epistemic status:** `firmware_observed` (emulated, `harness=full`), 12 gateway
images. The initial nine-step suite entered `results/` via the `suites` workflow
on `main` after #59 (branch `oracle/update-suites`, commit `112d916`, "oracle:
new and refreshed suite results"), and #71 (`oracle/who13-dim27`) added the
tenth step (`*#13**27##`); in both runs a suite enters `results/` only when a
second pass reproduces the file byte for byte (`tools/suite_plan.py`, verdict
`unstable` otherwise). The crash rows were also reproduced in three fresh local
runs each. Nothing here is `experimentally_confirmed`; the live anchors are an
F461 `2.0.11` and a MyHomeServer1 `3.87.13`.

**Suite:** [`oracle/cases/who13-identification.cases`](../oracle/cases/who13-identification.cases)
(10 independent steps, suite SHA-256
`718a8e0e59e9a6d7b0bdd4ebe78edac87200d1bb4d82ed57b8422b15ddad188c`; the
workflow ran them with `reset=each` on F450 `020010` and `reset=0` on the other
eleven targets, the reset policy of each target's committed suites). Rows:
`results/<product>/<version>/oracle/full/who13-identification.tsv`; image,
target SHA-256 and reset policy are in each file's header. DIMENSION 23, 24 and
the WHERE form `*#1013*0*1##` were already covered by `trace-corpus.cases` on
all twelve gateways (since 2026-10-15); this suite adds DIMENSION 10, 11, 12,
27 and `*#1013**1##` and puts the ten requests side by side.

## Question

A client that adds a gateway by hand (no SSDP) has only WHO 13 and WHO 1013 to
learn what it talks to. MyHOME's runtime asks `*#13**15##` first and `*#1013**1##`
only when the device type is the shared code `200` (MyHOME#420); its manual
config flow asks the MAC (`*#13**12##`) on models known to answer it
(MyHOME#799), while OWNd decodes `#`-separated ASCII model-name replies on
DIMENSION 27 (`*#13**27##`, `OWNGatewayEvent.model_name`, OWNd#139). The suite
asks:

1. Which of DIMENSION 15, 16, 10, 11, 12, 23, 24, 27 and WHO 1013
   `OBJECT_MODEL` does each firmware answer?
2. Does any of them harm the gateway?

DIMENSION 19 (uptime) is left out: its value depends on the run, not on the
firmware.

## Method

In the CI jail: the daemons see the jail's dummy `eth0` (`192.168.1.1/24`) and
the pinned MAC `02:00:00:00:00:01` (own-firmware-oracle#57), which is what makes
the IP and MAC **values** below reproducible. On F450 every step ran on a freshly
started gateway (`reset=each`); on the other eleven the ten steps ran in the
order of `who13-identification.cases` (`15`, `16`, `12`, `10`, `11`, `23`, `24`,
`27`, `*#1013**1##`, `*#1013*0*1##`; `oracle/record.py` sorts unordered `.cases`
rows lexicographically in the TSV and table below) on one gateway start
(`reset=0`), with `oracle/driver.py` restarting the target after a `crash` row
and `oracle/qemu_target.py` (`_recover_cmd_session`) reconnecting the command
socket after a `reply=-` step. The three crash rows of H4684 and MH200 are each
a fresh daemon exit and the rows after them come from a restarted gateway.
Earlier local runs with `--no-sandbox` and `reset=each` gave the same verdicts
with the container's own interface values.

## Rows

Cells: `ack, out <frame>` is the ACK followed by that OpenWebNet reply; `ack,
silent` / `nack, silent` is an ACK or NACK with no reply frame; `-, out <frame>`
is an OpenWebNet frame with no separate `*#*1##` / `*#*0##` delimiter after it;
**crash** is a `nack` after which the target was no longer alive (verdict
`crash`, `oracle/driver.py`).

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
| `*#13**27##` | ack, out `*#13**27*72#79#83#84##` | ack, out `*#13**27*66#84#87#69#66##` | ack, out `*#13**27*66#84#87#69#66##` | ack, out `*#13**27*72#79#83#84##` | ack, out `*#13**27*72#79#83#84##` | ack, out `*#13**27*72#79#83#84##` | ack, out `*#13**27*72#79#83#84##` | nack, silent | -, out `*#13**27*#*#*1##` | -, out `*#13**27*#*#*1##` | ack, out `*#13**27*72#79#83#84##` | -, out `*#13**27*#*#*1##` |

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
- **Model name / machine descriptor** (`*#13**27##`) never crashes a daemon, nor
  does any target read the Linux UTS hostname. Six targets (`F450`, `F454`,
  `F459`, `F460`, `F461`, `MH202`) answer `*#13**27*72#79#83#84##` (`"HOST"` in
  `#`-separated decimal ASCII codes, the factory/unpopulated fallback) followed
  by `ack`; `F453` and `F453AV` answer `*#13**27*66#84#87#69#66##` (`"BTWEB"`
  from their recovery `conf.xml`) followed by `ack`; `H4684` refuses cleanly
  (`nack, silent`). On `MH200`, `MH200N` and `MyHomeServer1` `028206` (whose
  staged configuration has an empty descriptor string), the reply on the
  command socket is `own:*#13**27*#*#*1##` (`reply=-`): an unterminated empty
  `*#13**27*#` (one `#` instead of `##`) concatenated directly with `*#*1##`, so
  a `##`-delimited stream reader absorbs the ACK into the malformed frame and
  never sees a separate `*#*1##`.

For a client: ask DIMENSION 15 first, WHO 1013 only on `200`, and the network
dimensions only from a model known to answer them (`MAC_REQUEST_SAFE_MODELS`
gates `OWNGatewayCommand.request_mac_address()` in MyHOME#799). On gateways
where the stored DIMENSION 27 descriptor is empty (`MH200`, `MH200N`, or an
unpopulated `MyHomeServer1`), sending `*#13**27##` swallows the command-session
`*#*1##` ACK into `*#13**27*#*#*1##` and stalls an ACK-waiting request until
its timeout; `OWNGatewayEvent.model_name` (OWNd#139) decodes `#`-separated
ASCII replies when received, while MyHOME's identification probe does not send
`*#13**27##`.

## Live anchor

- F461 `2.0.11` (2026-10-23, read-only; MAC and LAN address rewritten to the synthetic `00:03:50:00:10:23` / `192.0.2.92` used by the MyHOME trace fixture): `*#13**15*200##`, `*#13**16*2*0*11##`,
  `*#13**12*0*3*80*0*16*35##`, `*#13**10*192*0*2*92##`, `*#13**11*255*255*255*0##`,
  `*#13**23*5*10*35##`, `*#13**24*2*26*4##`, `*#13**27*70#52#54#49##` (`"F461"`),
  `*#1013**1*134*15*5*0##`: the real gateway answers the MAC, IP, `"F461"` model
  name and `OBJECT_MODEL 134` where the factory configuration gives `0*0*0*0`
  and `"HOST"`.
- MyHomeServer1 `3.87.13` (2026-10-24, read-only MyHOME trace fixture):
  `*#13**15*200##`, `*#13**27*77#121#72#111#109#101#83#101#114#118#101#114#49##`
  (`"MyHomeServer1"`), `*#1013**1*67*15*5*0##`: on physical hardware with a
  populated descriptor, DIMENSION 27 emits a properly `##`-terminated ASCII
  code sequence followed by `*#*1##`, whereas the emulated `028206` factory
  image has an empty descriptor and emits the unterminated `*#13**27*#*#*1##`.

## What the suite does not settle

- How the physical H4684 and MH200 behave: a daemon exit under qemu-user; the
  supervisor's restart is not observed.
- The MAC, IP and DIMENSION 27 descriptor values on gateways that populate them
  at boot or from plant files: factory/unpopulated values (`0*0*0*0`, `"HOST"`,
  `"BTWEB"`, or empty) appear in the jail.
