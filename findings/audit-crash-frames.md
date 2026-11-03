# Crash frames from the OWNd-vs-Encyclopedia audit replay: MH202 on WHO 1 / WHO 4 `001`, F460 / F461 on a four-digit DIMENSION 52 year

**Epistemic status:** `firmware_observed` (emulated, `harness=full`), 12 gateway
images, two fresh runs per gateway (fresh containers, run 2 started after run
1 had finished), byte-identical TSVs on 12 of 12 (864 of 864 rows agree in
reply, verdict and bus output). Nothing here is `experimentally_confirmed`:
none of these frames was sent to a physical gateway.

**Suite:** [`oracle/cases/ownd-audit-frames.cases`](../oracle/cases/ownd-audit-frames.cases)
(72 independent `down` steps, `reset=each`, suite SHA-256
`db03cc878d84d514b9e324c2d01d050264f2e4ccd0c1aad33937683c30e472ca`). Rows:
`results/<product>/<version>/oracle/full/ownd-audit-frames.tsv` (run 1; run 2
was byte-identical and is not committed, as for every other suite); image and
target SHA-256 are in each file's header. The suite
sends, pairwise, the frame an OWNd `master` builder emits and the form the
Encyclopedia functional page defines for the same request (test2 report 21,
"OWNd master vs the OpenWebNet Encyclopedia", 2026-10-28). No WHO 4
DIMENSION 22 frame is in the suite.

**Index.** 63 of the 72 frames are new to
[`results/mcp_index.json`](../results/mcp_index.json) (9 already had committed
rows in other suites), which grows from 589 to 652 inputs and past the 5 MiB
`MAX_BYTES` backstop of [`tools/guard.py`](../tools/guard.py); the two
generated indexes now have their own `INDEX_MAX_BYTES` ceiling (25 MiB) there.
The seven crash verdicts are also carried into
[`tools/expected_divergences.yaml`](../tools/expected_divergences.yaml)
(`crash_frames`), and the TSV lines are quoted verbatim below. Image and
target hashes of the three crashing gateways, from the TSV headers:

| Product | `image_sha256` | `target_sha256` |
|---|---|---|
| MH202 `010024` | `bb561eb2b6b476de21cc4981234108b0698c2a58b1e229f2909ef4e49aef9df7` | `5858374ef26a02c10381cb7ffd09cabbf762367ecce090e238cdde81c1ffced4` |
| F460 `020012` | `b5a50d606578f47ecbf0d327d7078b1357abb78425ea821ce743cd486b801b26` | `3822230ea71018536bcdd90008f0c44f39fd326da1ca48da76e9e69c5fa866f2` |
| F461 `020011` | `cb6f3e9d875c14f74a0791d7f3f420ce9d4babadb4459c1250c6cba7e741af2b` | `1ecabc82832aa3d341692ef5e4a426b1bc5804d2b2019520f3665358f1e33428` |

## Method

Each step is sent to `openserver` against the emulated gateway stack with a
fresh daemon stack per step (`reset=each`), and the driver restarts the
target after a `crash` row, so no crash masks another frame's verdict.
`reply` is the command-session answer; `verdict` is `out` when a bus frame
went out, `silent` when nothing did, and `crash` when a daemon of the stack
exited during the step. On F460, F461 and MyHomeServer1 a `nack` after `out`
is the empty-bus TX-ACK rule
([`empty-bus-reply-semantics.md`](empty-bus-reply-semantics.md)), not a
refusal. Unlike the [`thermo-dim22`](thermo-dim22.md) runs, this replay kept
no per-daemon console logs, so **the crashing process is not attributed**
for any of the seven rows (see "What is not settled").

## The crash frames

Seven rows, all `nack, crash`, identical in both runs. No other frame of the
72 crashed on any gateway.

### MH202 `010024`: WHO 1 status with an empty WHERE

`*#1*##` is what OWNd's `status('')` builder can emit when the WHERE is
empty: `*#1*` followed directly by `##`. The Encyclopedia defines no empty
WHERE for WHO 1 (general is `0`). Eleven gateways refuse it (`nack, silent`);
MH202 dies on it. The same step with a real WHERE (`*#1*12##`) goes out on
11 of 12.

```text
results/MH202/010024/oracle/full/ownd-audit-frames.tsv:16:down	*#1*##	nack	crash	-
```

### MH202 `010024`: the documented all-probes form `*#4*001##` and its DIMENSION 0 read

Per the Encyclopedia WHO 4 addressing page, `001..099` is "all probes in
zone" (master and slave probes of zone `ZZ`), distinct from `1..99` (the
master probe), so `*#4*001##` and `*#4*001*0##` are **valid documented
frames**, not OWNd misbuilds (OWNd collapses `001` onto zone `1`, report row
H2). Eleven gateways forward them. On the eight legacy 2-digit-PIC gateways
(F450, F453, F453AV, F454, F459, H4684, MH200, MH200N) `*#4*001*0##` fans out
to eight telegrams `$0499013100\r` .. `$0499013800\r` (zone 1, probes 1..8),
while `*#4*1*0##` gives `$0499013000\r` (probe 0 = master); F460, F461 and
MyHomeServer1 emit a single telegram, `$0499013100\r` on MyHomeServer1 and
`$00499013100\r` with the 3-digit RPMsg prefix on F460 / F461 (MyHomeServer1
`028206` runs a 2-digit v8 PIC on `/dev/ttymxc2`, so the single telegram is a
translator difference, not a transport one). The dimensionless `*#4*001##` is not
distinguished from `*#4*1##` (the same four zone-status telegrams). MH202
crashes on both `001` forms and answers both `1` forms with `ack, out`.

```text
results/MH202/010024/oracle/full/ownd-audit-frames.tsv:42:down	*#4*001##	nack	crash	-
results/MH202/010024/oracle/full/ownd-audit-frames.tsv:43:down	*#4*001*0##	nack	crash	-
```

For comparison, the same rows on MH200N `010108` (legacy 2-digit PIC, eight
telegrams) and F461 `020011` (3-digit RPMsg, one telegram; MyHomeServer1
`028206` also emits one, with the 2-digit `$04` prefix):

```text
results/MH200N/010108/oracle/full/ownd-audit-frames.tsv:42:down	*#4*001##	nack	out	bus:24 30 34 39 39 30 31 33 30 30 30 0d | bus:24 30 34 39 39 30 31 33 30 31 32 0d | bus:24 30 34 39 39 30 31 33 30 30 45 0d | bus:24 30 34 39 39 30 31 33 30 30 44 0d
results/MH200N/010108/oracle/full/ownd-audit-frames.tsv:43:down	*#4*001*0##	nack	out	bus:24 30 34 39 39 30 31 33 31 30 30 0d | bus:24 30 34 39 39 30 31 33 32 30 30 0d | bus:24 30 34 39 39 30 31 33 33 30 30 0d | bus:24 30 34 39 39 30 31 33 34 30 30 0d | bus:24 30 34 39 39 30 31 33 35 30 30 0d | bus:24 30 34 39 39 30 31 33 36 30 30 0d | bus:24 30 34 39 39 30 31 33 37 30 30 0d | bus:24 30 34 39 39 30 31 33 38 30 30 0d
results/F461/020011/oracle/full/ownd-audit-frames.tsv:42:down	*#4*001##	nack	out	bus:24 30 30 34 39 39 30 31 33 30 30 30 0d
results/F461/020011/oracle/full/ownd-audit-frames.tsv:43:down	*#4*001*0##	nack	out	bus:24 30 30 34 39 39 30 31 33 31 30 30 0d
```

The committed `thermo-zone-requests` rows already show MH202 crashing on two
other WHO 4 WHERE spellings, `*#4*2#1##` and `*#4*2#4#01##`
([`thermo-zone-requests.md`](thermo-zone-requests.md), where the process was
attributed to `coso`, the daemon that handles WHO 4 on MH202 instead of
`bt_termo`); those two rows are not in the ledger either and are not added
here, since this finding only carries the audit replay.

### F460 `020012` and F461 `020011`: four-digit year in a DIMENSION 52 request

The Encyclopedia WHO 18 dimensions page defines the monthly totalizer
request as `*#18*WHERE*52#Y#M##` with `Y` "in two-digit `yy` form". The
audit suite sent `52#2026#10`, a four-digit year, as the "documented" side
of OWNd's dimension-less `*#18*51*52*100##` (report row E1). That was a
mistake in the suite, but it produced a result: seven gateways forward the
four-digit form, F450 / H4684 / MH200 refuse it, and **F460 and F461 crash
after the telegram went out**. The documented two-digit form
`*#18*51*52#26#9##` (committed `energy-params` / `energy-ts10` rows) is
`ack, out` on both without a crash; that is one two-digit sample (year 26,
month 9) against one four-digit sample (2026, month 10), so the contrast is
between those two rows, not a rule for every year and month.

```text
results/F460/020012/oracle/full/ownd-audit-frames.tsv:27:down	*#18*51*52#2026#10##	nack	crash	bus:24 30 30 36 44 31 41 31 30 32 33 33 35 30 31 36 37 45 41 30 0d
results/F460/020012/oracle/full/ownd-audit-frames.tsv:28:down	*#18*51*52#2026#10*100##	nack	crash	bus:24 30 30 36 44 31 41 31 30 32 33 33 35 30 31 36 37 45 41 30 0d
results/F461/020011/oracle/full/ownd-audit-frames.tsv:27:down	*#18*51*52#2026#10##	nack	crash	bus:24 30 30 36 44 31 41 31 30 32 33 33 35 30 31 36 37 45 41 30 0d
results/F461/020011/oracle/full/ownd-audit-frames.tsv:28:down	*#18*51*52#2026#10*100##	nack	crash	bus:24 30 30 36 44 31 41 31 30 32 33 33 35 30 31 36 37 45 41 30 0d
```

The telegram is `$006D1A1023350167EA0\r` (3-digit prefix; `$06D1A1023350167EA0\r`
on the seven forwarding 2-digit gateways, e.g. F453, MH200N, MH202, where the
row is `ack, out`). Against the two-digit form's
`$06D1A1023350161A09\r` (`1A` = 26, `09` = 9), the two bytes that carry
year and month read `7E A0` instead of `yy MM`: the value 2026 (`0x7EA`) does
not fit the one-byte year field, and the month is no longer a separate
byte. The crash follows that telegram on the two RPMsg gateways only; which
process exits is not attributed in this run.

## Committed oracle rows (five crashing frames, seven `(gateway, frame)` crash cells; all 12 gateways, run 1; run 2 identical)

| Frame | F450 `020010` | F453 `010100` | F453AV `030014` | F454 `020051` | F459 `020105` | F460 `020012` | F461 `020011` | H4684 `020054` | MH200 `020102` | MH200N `010108` | MH202 `010024` | MyHomeServer1 `028206` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `*#1*##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, **crash** | nack, silent |
| `*#4*001##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out | nack, **crash** | nack, out |
| `*#4*001*0##` | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out | nack, **crash** | nack, out |
| `*#18*51*52#2026#10##` | nack, silent | ack, out | ack, out | ack, out | ack, out | nack, **crash** | nack, **crash** | nack, silent | nack, silent | ack, out | ack, out | ack, out |
| `*#18*51*52#2026#10*100##` | nack, silent | ack, out | ack, out | ack, out | ack, out | nack, **crash** | nack, **crash** | nack, silent | nack, silent | ack, out | ack, out | ack, out |

The ledger rows added for these frames (`clock_or_crash_known.crash_frames`
in [`tools/expected_divergences.yaml`](../tools/expected_divergences.yaml))
name the suite, say "daemon not attributed", and point here.

**3488 `030205` (added in Phase 2, not in the table above):** the four-digit-year DIMENSION 52 requests `*#18*51*52#2026#10##` and `*#18*51*52#2026#10*100##` are `crash` on the 3488 HOMETOUCH, after the telegram went out, as on F460 and F461; the daemon is not attributed by the run log. Ledger rows in `tools/expected_divergences.yaml`; rows in `results/3488/030205/oracle/full/ownd-audit-frames.tsv`.

## Consequence for clients

- A WHO 1 status request must never be built with an empty WHERE: besides
  being undefined, it crashes MH202 `010024`.
- `*#4*001##` / `*#4*001*0##` are documented and work on eleven gateways; a
  client that wants all probes of a zone needs the `0ZZ` form (eight probe
  telegrams on the legacy 2-digit-PIC gateways), but should not send it to
  MH202 `010024`.
- DIMENSION 52 requests should use the two-digit `52#yy#M` year the page
  documents; the four-digit year that was sent reaches the bus on seven
  gateways with a malformed year/month field and crashes F460 / F461,
  while the committed two-digit row went out without a crash.

## See also: firmware-vs-documentation rows from the same replay

Two results of the replay that are useful to clients, with their rows (run 1;
run 2 identical). Neither crashed anywhere.

**WHO 25: on `down` the firmware requires and strips the `3` prefix; on `up`
it prepends it.** `*#25*39##` goes out as `$04B4009009\r` (sensor **09**),
`*#25*35##` as `$04B4009005\r`, and `*#25*339##` as `$04B4009039\r` (sensor
39), on the nine WHO 25-forwarding gateways; the prefix-less `*#25*5##` is
refused on 12 of 12. So an OWNd event parser that strips a leading `3` from
the `up` WHERE matches the emulated gateways, but a `down` builder that emits
`*#25*39##` for "sensor 39" addresses sensor 09.

| Frame | F450 | F453 | F453AV | F454 | F459 | F460 | F461 | H4684 | MH200 | MH200N | MH202 | MyHomeServer1 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `*#25*39##` | nack, silent | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out | nack, silent | nack, silent | nack, out | ack, out | nack, out |
| `*#25*339##` | nack, silent | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out | nack, silent | nack, silent | nack, out | ack, out | nack, out |
| `*#25*35##` | nack, silent | nack, out | nack, out | nack, out | nack, out | nack, out | nack, out | nack, silent | nack, silent | nack, out | ack, out | nack, out |
| `*#25*5##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent |

**WHO 3: the documented general status `*#3*0##` is refused everywhere.**
`*#3*0##` (who-3 README) is `nack, silent` on 12 of 12 (also in the committed
`energy-ts10` rows); `*#3##` goes out on eight gateways as `$03AA001614\r`,
and `*#3*##` (OWNd's evidence note) only on F454 and F459.

| Frame | F450 | F453 | F453AV | F454 | F459 | F460 | F461 | H4684 | MH200 | MH200N | MH202 | MyHomeServer1 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `*#3*0##` | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent |
| `*#3##` | nack, silent | nack, out | nack, out | nack, out | nack, out | nack, silent | nack, silent | nack, out | nack, out | nack, out | nack, out | nack, silent |
| `*#3*##` | nack, silent | nack, silent | nack, silent | nack, out | nack, out | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent | nack, silent |

## What is not settled

- **Which daemon crashes.** No console logs were kept, so none of the seven
  rows names the process. On MH202 the WHO 4 translator is `coso` per
  [`thermo-zone-requests.md`](thermo-zone-requests.md), and the WHO 1 crash
  may be a different daemon; on F460 / F461 the DIMENSION 52 crash follows a
  telegram that `scsserver` already sent. A re-run with per-daemon logs would
  attribute all seven.
- **Physical hardware.** None of these frames was sent to a real gateway;
  whether a physical MH202, F460 or F461 reboots or merely loses a daemon is
  unknown.
- **Other spellings.** Only zone `001`, meter `51`, year `2026` / month `10`
  were sent; other three-digit zones, other meters and other out-of-range
  years have no rows.
- **The suite's 60 other rows** (program numbers, holiday dates, WHO 16 / 22
  / 18 ranges, door entry, alarm, clock — the 72 suite steps minus the five
  crashing frames and seven "See also" frames above) are in the committed
  TSVs (`ownd-audit-frames.tsv`) for completeness and are not claimed by this
  finding.
