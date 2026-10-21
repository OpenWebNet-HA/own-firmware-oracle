# WHO 13 gateway clock: writes, reads and the time-zone field `T`

**Epistemic status:** `firmware_observed` (emulated, `harness=full`), 12 gateway
images. Nothing here is `experimentally_confirmed`; live anchors are cited
where they exist.

**Suites:** [`oracle/cases/who13-clock.cases`](../oracle/cases/who13-clock.cases)
(20 independent steps, `reset=each`, suite SHA-256 `9df081fa5625…`) and
[`oracle/cases/who13-clock-readback.seq`](../oracle/cases/who13-clock-readback.seq)
(9 ordered steps on one boot, `reset=0`, suite SHA-256 `4b1e35200b78…`).
Rows: `results/<product>/<version>/oracle/full/who13-clock{,-readback}.tsv`;
image and target SHA-256 are in each file's header.

## Question

The WHO 13 `T` field is a sign digit (`0` = +, `1` = −) and a two-digit hour
offset (Encyclopedia `functional/who-13-integration-gateway/dimensions.md`), so
`*#13**#0*H*M*S*T##` cannot express a non-whole-hour zone. OWNd 2.0.0b11 now
refuses such offsets client-side, and MyHOME's `myhome.sync_time` assumes every
gateway accepts a clock write. No oracle row covered WHO 13 clock frames before
this suite. It asks:

1. Does each gateway accept, NACK or drop clock writes (DIMENSION 0, 1, 22), and
   does a write reach the bus?
2. What does each gateway answer to `*#13**0##`, `*#13**1##` and `*#13**22##`,
   and with which `T`?
3. What happens with `T` = `999`, empty, `124`, `013` and `0530`?
4. Does a write change what a later read reports?

## How clock values are recorded

The emulated gateway's clock is the host's wall clock, which no two runs share
and which the jail cannot set. `oracle/driver.py` therefore records the clock
values as placeholders: `*#13**0*{H}*{M}*{S}*<T>##` for answers, and `x` for the
time and date digits of the two PIC clock telegrams (`$06D2000906hhmmssZZ\r`,
`$06D2000907ddWMyyyy\r`). `T`, the zone byte `ZZ`, empty values and the field
count are recorded verbatim. In the unredacted development runs (hour `08`,
weekday `03`) every gateway zero-padded H and W to two digits and wrote Y with
four. Day and month were `21` and `10` on that day, so their padding was not
observed.

A consequence: the oracle cannot show whether a write changes the gateway's
time or date, only whether it changes `T`.

## Results

### Reads on a fresh boot

| `T` in `*#13**0##` / `*#13**22##` | Gateways |
| :--- | :--- |
| `999` | F460 020012, F461 020011, MyHomeServer1 028206 |
| `001` | F450 020010, F454 020051, F459 020105 |
| `000` | F453 010100, F453AV 030014, MH202 010024 |
| empty (`…*{S}*##`) | MH200 020102, MH200N 010108, H4684 020054 |

All 12 ACK the three reads and answer with the documented field count.
`T=999` on the Eliot stack (F460, F461) and MyHomeServer1 matches a live F461
2.0.11 answer, so `999` is what that firmware reports, not a plant setting. The
empty `T` on MH200, MH200N and H4684 is probably a harness artefact: a live
MH200N answers `001`, and the staged sysroot has no zone configuration.

### Writes (`*#13**#0*14*30*00*T##` and `*#13**#22*14*30*00*T*03*21*10*2026##`)

| Gateway | `000` `001` `002` `102` | `999` | empty | `124` `013` | `0530` | Reaches the bus |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| F450, F460, F461, MH202 | ack | ack | ack | ack | **ack** | no |
| F453, F453AV | ack | ack | ack | ack | **ack** | yes: time + date telegrams |
| F454 | ack | ack | ack | ack | nack | no |
| F459 | ack | ack | ack | ack | nack | when `T` is neither `001` nor empty: `$24\r`, a `$06DC907…` telegram (not a clock telegram) and `*#1013**11*…##` |
| MH200, MH200N, H4684 | nack + crash ¹ | nack + crash ¹ | ack | nack + crash ¹ | nack + crash ¹ | yes (empty `T`) |
| MyHomeServer1 | DIM 0: no reply ²; DIM 22: ack | same | same | same | same | no |

The DIMENSION 1 date write `*#13**#1*03*21*10*2026##` is ACKed without bus
output on 11 gateways; MyHomeServer1 does not answer it (²).

¹ **Harness artefact, not firmware refusal.** On all three, `bt_device`
handles the write by relinking `/etc/localtime` to a zone file (for `T=002`:
`Etc/GMT+2`), then opens `cfg/conf.xml`, the installation's configuration,
which the harness does not stage. The open fails and `bt_device` dereferences
NULL (SIGSEGV, seen in a qemu syscall trace), so openserver NACKs. An empty
`T` skips the zone step and is ACKed and broadcast. What these firmwares do
with a configured plant is not answered here. Note also that the POSIX zone
`Etc/GMT+2` is UTC−2: if `002` means UTC+2, the zone that `bt_device` links
has the opposite sign. This needs a live check before anyone relies on it.

² MyHomeServer1 sends neither ACK nor NACK to DIMENSION 0 and 1 writes within
the harness window (reply `-`), while DIMENSION 22 is ACKed. The cause is not
established; a clock service that `openserver` depends on and the target does
not emulate is the likeliest explanation.

### Read-back after a write (`who13-clock-readback.seq`)

| Gateway | `T` before | after `002` | after DIM 22 `102` | after `0530` |
| :--- | :--- | :--- | :--- | :--- |
| F450 | `001` | `002` | `102` | **`0530`** (stored verbatim) |
| F454, F459 | `001` | `002` | `102` | `102` (`0530` NACKed) |
| F453, F453AV, MH202 | `000` | `000` | `000` | `000` |
| F460, F461, MyHomeServer1 | `999` | `999` | `999` | `999` |
| MH200, MH200N, H4684 | empty | crash ¹ | — | — |

## Conclusions

- **No gateway rejects whole-hour clock writes.** The only NACKs are the
  `bt_device` crash on MH200, MH200N and H4684 (harness) and the missing reply
  on MyHomeServer1 DIMENSION 0 / 1. `myhome.sync_time` sends DIMENSION 22,
  which all 9 gateways without the crash ACK.
- **Non-standard `T` is mostly accepted.** Every value tested, including `999`,
  empty, `124`, `013` and the 4-digit `0530`, is ACKed by F450, F453, F453AV,
  F460, F461 and MH202. F454 and F459 NACK only the 4-digit form.
- **F450 stores a non-hour offset.** After `*#13**#0*14*30*00*0530##` it
  reports `T=0530`. That is the only firmware that echoes a non-standard
  offset back.
- **`T` is a reported setting on six gateways only.** F450, F454 and F459 store
  the written `T`. F453, F453AV, MH202, F460, F461 and MyHomeServer1 keep
  reporting `000` or `999` whatever is written, so a client cannot use
  `*#13**0##` to verify a sync on them.
- **`T=999` is a firmware answer** on F460, F461 and MyHomeServer1, not an
  invalid plant configuration.
