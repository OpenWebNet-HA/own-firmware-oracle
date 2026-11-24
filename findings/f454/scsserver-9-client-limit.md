# `F454/020051` `scsserver` 9-Client TCP `20001` Table Limit

**Epistemic status:** `firmware_observed` (`F454` `2.0.51`, `F454_020051.fwz` SHA-256 `15f80bc673e53fa105a68725b5a6d3ccf06767d1bae0e6139803de34e485f0f3`, `home/bticino/bin/scsserver` SHA-256 `281bb0eee184420bda73b8cdd3c88b9b14cfd87814b7913c0cfc7b21a1e6f197`).

## Summary

While validating closed-loop WHO=4 (`bt_termo`) inbound device replies on `F454/020051` (`plant:cu4695`), outbound OpenWebNet commands (`down`) reached `/dev/ttyS1` (`bus:`) normally, whereas inbound `$17` / `$18` device replies (`bus_rx:`) were not forwarded to `bt_termo` when all eight translator daemons declared in [`oracle/targets/F454/020051.yaml`](../../oracle/targets/F454/020051.yaml) were launched simultaneously in alphabetical order.

Binary inspection of `f454/home/bticino/bin/scsserver` established the exact root cause: `scsserver` allocates a fixed **9-entry client table** (`9 * 0x14 = 0xb4` bytes) on TCP port `20001` and closes any 10th connection with `"Raggiunto numero massimo di CLIENT"`.

---

## Binary Facts (`f454/home/bticino/bin/scsserver`)

- **Client Table Allocation & Guard (`0x0a440..0x0a470`)**:
  - Each connected translator client occupies one `0x14`-byte (20-byte) slot in `scsserver`'s static client table.
  - On `accept()` at TCP `20001`, `scsserver` scans the table in `0x14`-byte strides up to offset `0xb4` (`0x0a454`, 9 slots total: indices `0..8`).
  - Once all 9 slots are occupied, `scsserver` logs `"Raggiunto numero massimo di CLIENT"` (`0x0a468`) and immediately closes the newly accepted socket.
- **Alphabetical Launch Exhaustion in `QemuTarget`**:
  - [`oracle/targets/F454/020051.yaml`](../../oracle/targets/F454/020051.yaml) declares 8 `role: translator` daemons: `bt_device`, `bt_difson`, `bt_energia`, `bt_luci`, `bt_multi`, `bt_supervisione`, `bt_termo`, and `coso`.
  - Most translator daemons open **two** TCP `20001` sockets to `scsserver` (a command/TX socket and a bus-event/RX listener socket).
  - Because `QemuTarget._restart_once()` launches translators in alphabetical order (`bt_device`, `bt_difson`, `bt_energia`, `bt_luci`, `bt_multi`, ...), the first five daemons consume all 9 slots before `bt_termo` (#7 alphabetically) connects its RX listener socket.
  - As a result, WHO=1 (`bt_luci`, #4 alphabetically) receives inbound `$17`/`$18` frames under `harness=full` (e.g. [`who1-motion-detector.md`](../who1-motion-detector.md)), whereas `bt_termo` (#7) is starved of inbound `$17`/`$18` frames unless unused non-WHO=4 translators are excluded from the run.

---

## Clean-Room Harness Support (`active_translators`)

To preserve **zero-diff reproducibility** across all existing `harness=full` and `harness=plant:*` suites while enabling full closed-loop WHO=4 (`bt_termo` / `coso`) execution on `F454/020051`:

1. `PlantTopologyConfig` ([`oracle/plant/models.py`](../../oracle/plant/models.py)) supports an optional `active_translators: frozenset[str]` field (empty by default, leaving `harness=full`, `f461-bench`, and `mh200-bench` completely unchanged).
2. When `harness` starts with `plant:` and the loaded fixture specifies `active_translators` (for example [`oracle/plant/fixtures/cu4695.yaml`](../../oracle/plant/fixtures/cu4695.yaml): `bt_termo`, `coso`, `bt_device`), `QemuTarget._active_clients()` ([`oracle/qemu_target.py`](../../oracle/qemu_target.py)) trims `stack_open.xml` and launches only the matching translator daemons (`<= 6` sockets on TCP `20001`).
3. With `active_translators` active in [`cu4695.yaml`](../../oracle/plant/fixtures/cu4695.yaml), `F454/020051` decodes every closed-loop `4695` `C0`, `C2`, Zone `DIM 0/12/13/14`, and `DIM 20` reply cleanly in [`results/F454/020051/oracle/plant-cu4695/plant-cu4695.tsv`](../../results/F454/020051/oracle/plant-cu4695/plant-cu4695.tsv) with `0 capped` CPU-idle waits.
