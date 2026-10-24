# WHO 13 gateway clock: writes, reads and the time-zone field `T`

**Epistemic status:** `firmware_observed` (emulated, `harness=full`), 12 gateway
images, plus qemu syscall traces where stated. Nothing here is
`experimentally_confirmed`; live anchors are cited where they exist.

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

The emulated gateway's clock is the host's wall clock (the jail cannot set it;
every program runs with `TZ=UTC`). `oracle/driver.py` therefore records:

- in a time answer, the hour as the firmware's offset from UTC, to the quarter
  hour (`*#13**0*{UTC}*{M}*{S}*000##`; `{UTC+2}` if the firmware reported a time
  two hours ahead), and minutes and seconds as `{M}` / `{S}`;
- in a date answer, `{W}*{D}*{MO}*{Y}`;
- in the PIC clock telegrams a 2-digit gateway puts on the bus after a clock
  write (`$06D2000906hhmmssZZ\r`, `$06D2000907ddWMyyyy\r`), the clock digits as
  `x`.

`T`, the zone byte `ZZ`, empty values and the field count are verbatim. In
the unredacted development runs (hour `08`, weekday `03`) every gateway
zero-padded H and W and wrote Y with four digits; day and month were `21` and
`10`, so their padding was not observed.

Consequence: the oracle cannot show whether a write changes the gateway's
time or date (the jail refuses `date -s` and `settimeofday`), only whether it
changes `T` and whether the firmware shifts its reported time by itself.

## Harness setup that matters here

- **`cfg/conf.xml` on MH200, MH200N, H4684.** The `bt_processi` gateways keep
  the installation's settings in `cfg/conf.xml`. F453 and F453AV stage a
  factory one from their recovery layer; MH200N ships the identical file
  (sha256 `cc35abae…`) only in a recovery layer its target does not overlay,
  and MH200 and H4684 ship none. Without it, `bt_device` handles a clock write
  with a non-empty `T` by relinking `/etc/localtime`, then opens
  `cfg/conf.xml`, gets ENOENT and dereferences NULL (SIGSEGV; openserver
  NACKs). The three targets now stage a `conf.xml` with only the factory file's
  element path (`setup/generale/clock/{master,dateformat}`: no address,
  password or plant data). For MH200 and H4684 that structure is borrowed from
  the same daemon family, not read from their own image. Side effect,
  re-verified on all 24 suites: only `trace-corpus` `*#5*0##` changes on the
  three targets. `bt_alarm` now reports the factory state (maintenance,
  battery OK, network present: `*5*0*##`, `*5*5*##`, `*5*7*##`) instead of the
  no-config state (`*5*1*##`, `*5*10*##`, `*5*6*##`).
- **MyHomeServer1 and F460 / F461 run with their factory
  `cfg/restore/conf.xml.def`** (linked by their targets), which sets
  `<fuso>999</fuso>`, `<town>Europe/Rome</town>` and `<abil_ntp>1</abil_ntp>`
  (NTP on).

## Results

### Reads on a fresh boot

| `T` in `*#13**0##` / `*#13**22##` | Gateways |
| :--- | :--- |
| `999` | F460 020012, F461 020011, MyHomeServer1 028206 |
| `001` | F450 020010, F454 020051, F459 020105 |
| `000` | F453 010100, F453AV 030014, H4684 020054, MH200 020102, MH200N 010108, MH202 010024 |

All 12 ACK the three reads with the documented field count, and all report
their time as `{UTC}`. `T=999` comes from the factory `<fuso>999</fuso>`
("zone given by `<town>`") on F460, F461 and MyHomeServer1. It matches a live
F461 2.0.11, so it is the factory answer, not a broken plant. A live MH200N
answers `001`; the emulated `000` is what the `bt_processi` family reports
with a factory `conf.xml` and a UTC clock.

### Writes (`*#13**#0*14*30*00*T##` and `*#13**#22*14*30*00*T*03*21*10*2026##`)

| Gateway | whole-hour `T` | `999` / empty / `124` / `013` | `0530` | DIM 1 date | Bus output |
| :--- | :--- | :--- | :--- | :--- | :--- |
| F450, F460, F461, MH202 | ack | ack | **ack** | ack | none (write echoed on the event session) |
| F453, F453AV, H4684, MH200, MH200N | ack | ack | **ack** | ack | time + date telegrams |
| F454 | ack | ack | **nack** | ack | none |
| F459 | ack | ack | **nack** | ack | when `T` is neither `001` nor empty: `$24\r`, a `$06DC907…` telegram (not a clock telegram) and `*#1013**11*…##` |
| MyHomeServer1 | DIM 0: **no reply** ¹; DIM 22: ack | same | same | **no reply** ¹ | none |

¹ **MyHomeServer1 ignores manual DIMENSION 0 / 1 clock writes while NTP is
enabled, which is the factory default.** A syscall trace shows `bt_device`
reading the whole frame and then doing nothing: no reply, no syscall, until
openserver gives up. This is firmware behaviour, not a missing service. In
the read-back sequence, the read sent right after such a write still gets its
answer, but no ACK (reply `-`): a client that waits for the write's ACK
before sending anything else stalls. A
local experiment (not committed: the target can hold only one configuration)
with `<abil_ntp>0</abil_ntp>` in the same factory file made the same writes
ACKed. With `<abil_ntp>0</abil_ntp>` and `<fuso>001</fuso>`, `T` also followed
the writes (`002`, then `102`), as on F450, F454 and F459. F460 and F461 ship
the same NTP-on settings but ACK the writes.

### Read-back after a write (`who13-clock-readback.seq`)

| Gateway | `T` before | after `002` | after DIM 22 `102` | after `0530` |
| :--- | :--- | :--- | :--- | :--- |
| F450 | `001` | `002` | `102` | **`0530`** (stored verbatim) |
| F454, F459 | `001` | `002` | `102` | `102` (`0530` NACKed) |
| F453, F453AV, H4684, MH200, MH200N, MH202 | `000` | `000` | `000` | `000` |
| F460, F461 | `999` | `999` | `999` | `999` |
| MyHomeServer1 | `999` | `999` (write unanswered ¹) | `999` | `999` (unanswered ¹) |

The reported time stays `{UTC}` after every write on every gateway: none
shifts its answer by the stored `T` itself.

### A DIMENSION 1 write is echoed only when it changes the date

The emulated gateway keeps the host's wall clock (see above), and a DIMENSION 1
(date) write is echoed on the event session only when the written date is not
the host's current date. Writing today's date gets no output at all.

The first version of this suite wrote Wednesday 21 October 2026, which was the
day it was recorded, so the committed rows said `silent` for the gateways that
echo (F450, F453, F453AV, F459, F460, F461, H4684, MH200, MH200N, MH202). On
23 October the same inputs gave `out` and `suites` failed with "same inputs,
different verdicts" on those ten ([run
39997891568](https://github.com/OpenWebNet-HA/own-firmware-oracle/actions/runs/39997891568)).
F454 and MyHomeServer1 stayed `silent` on 23 October (I did not test them with
another date). Nothing was flaky: every re-run gave the same answer, only the committed file was out of
date. The DIMENSION 22 writes (time + zone + date) are echoed in both cases,
so they do not depend on the day.

Experiment, F450 2.0.10, Saturday 24 October 2026 about 11:53 UTC, one fresh
gateway per row:

| Written | Output |
| :--- | :--- |
| `*#13**#1*06*24*10*2026##` (today) | none (`silent`) |
| `*#13**#1*03*21*10*2026##` (three days earlier) | `own:*#13**#1*03*21*10*2026##` |

The suite now writes Thursday 25 December 2025 (`*#13**#1*04*25*12*2025##`), a
date that can never be the host's date again. The experiment above is not a
suite: its answer depends on the day it runs, which is what a committed result
must not do.

### How `bt_device` turns `T` into a zone (MH200N, syscall trace)

On the no-`conf.xml` path, MH200N's `bt_device` links `/etc/localtime` to
`/usr/share/zoneinfo/Etc/GMT` + (`+` for sign digit `0`, `-` for `1`) + the
remaining digits as a number:

| `T` written | link target |
| :--- | :--- |
| `000`, `001`, `002`, `012` | `Etc/GMT+0`, `Etc/GMT+1`, `Etc/GMT+2`, `Etc/GMT+12` |
| `102`, `112` | `Etc/GMT-2`, `Etc/GMT-12` |
| `013`, `124`, `0530`, `999` | `Etc/GMT+13`, `Etc/GMT-24`, `Etc/GMT+530`, `Etc/GMT-99` |

The image's own `usr/share/zoneinfo/Etc/GMT+2` stores a UTC offset of −7200 s
(tzdata's POSIX convention: `Etc/GMT+2` is two hours **behind** UTC). So
whenever this path runs, `T=002` (documented as UTC+2) selects a UTC−2 zone,
`102` selects UTC+2, and `013`, `124`, `0530` and `999` name files the image
does not have, which leaves the zone at UTC. With the factory-style
`conf.xml`, the same writes take a different path: `bt_device` runs
`date -s <date>-14:30:00`, `hwclock --systohc --utc` and `/bin/bt_time.tcl`,
and does not touch `/etc/localtime`. Which path a configured plant takes
(a `conf.xml` with a `<fuso>` element) is not established here. The sign
inversion is therefore proven for the zone-link path, and it is the only
zone handling observed. Whether a live MH200N's displayed time moves the
wrong way after `myhome.sync_time` needs a live check.

## Conclusions

- **No gateway rejects whole-hour clock writes**, with one exception:
  MyHomeServer1 2.82.6 with NTP enabled (its factory default) silently
  ignores DIMENSION 0 and 1 writes and never answers them. `myhome.sync_time`
  sends DIMENSION 22, which all 12 ACK.
- **Non-standard `T` is mostly accepted.** Every value tested, including `999`,
  empty, `124`, `013` and the 4-digit `0530`, is ACKed by F450, F453, F453AV,
  F460, F461, H4684, MH200, MH200N and MH202. F454 and F459 NACK only the
  4-digit form.
- **F450 stores a non-hour offset.** After `*#13**#0*14*30*00*0530##` it
  reports `T=0530`, the only firmware that echoes a non-standard offset back.
- **`T` is a reported setting on three gateways only.** F450, F454 and F459
  store the written `T`; the other nine keep reporting `000` or `999`, so a
  client cannot use `*#13**0##` to confirm a sync on them.
- **`T=999` is the factory `<fuso>` value** on F460, F461 and MyHomeServer1,
  meaning "zone from `<town>`".
- **`bt_device`'s zone mapping has the POSIX sign trap** on MH200N: `T=002`
  links `Etc/GMT+2`, which is UTC−2.
