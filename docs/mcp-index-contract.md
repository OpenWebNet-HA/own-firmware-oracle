# `mcp_index.json` Schema Contract (v1.2.0)

`results/mcp_index.json` is the deterministic, cryptographically hash-pinned contract between `own-firmware-oracle` (producer) and [`openwebnet-mcp`](https://github.com/OpenWebNet-HA/openwebnet-mcp) / CLI comparison tools (`tools/compare.py`, consumers).

---

## 1. Top-Level Structure

```json
{
  "format_version": "1.0.0",
  "generator": "own-firmware-oracle",
  "schema_version": "1.2.0",
  "total_unique_inputs": 440,
  "verdicts_sha256": "<64-hex-char canonical SHA-256>",
  "gateways": [ ... ],
  "verdicts": {
    "<input_frame>": [ ... ]
  }
}
```

### Versioning Policy
- **`format_version` (`"1.0.0"`)**: Wire container compatibility version. Consumers check `format_version.startswith("1.")`.
- **`schema_version` (`"1.2.0"`)**: Semantic schema version for `gateways[]` and `verdicts{}` fields.
  - `1.0.0`: Initial release covering emulated gateways only.
  - `1.1.0`: Added full-fleet inventory (`status: "emulated" | "catalogued"`) so `gateways[]` reflects all 15 catalogued firmware packages.
  - `1.2.0`: Added `gateways[].reply_semantics`, which says what a `reply` in that gateway's full suites means. Verdict entries are unchanged, and so is `verdicts_sha256`.

---

## 2. Cryptographic Integrity (`verdicts_sha256`)

`verdicts_sha256` is the SHA-256 digest of the canonical, whitespace-free, key-sorted JSON serialization of the `verdicts` object:

```python
canonical_json = json.dumps(verdicts, sort_keys=True, separators=(",", ":"))
verdicts_sha256 = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
```

- **Why `verdicts` only?** Cataloguing a new bare-metal or pending gateway in `catalog/*/*.yaml` adds a `"catalogued"` record to `gateways[]` without altering any empirical verdict, leaving `verdicts_sha256` stable.
- Both `python tools/mcp_index.py --check` (in CI) and `FirmwareOracle.load()` (in `openwebnet-mcp`) recompute and verify `verdicts_sha256` on load.

---

## 3. `gateways[]` Entries

Each item in `gateways[]` is sorted by `(product, version)` and contains:

| Field | Type | Description |
| :--- | :--- | :--- |
| `product` | `string` | Gateway model identifier (e.g. `"MH200N"`, `"MyHomeServer1"`, `"F454"`). |
| `version` | `string` | 6-digit firmware version string (e.g. `"010108"`, `"028206"`). |
| `status` | `"emulated" \| "catalogued"` | `"emulated"` if oracle suites have been executed; `"catalogued"` if unpacked/catalogued only. |
| `image_sha256` | `string` | 64-hex SHA-256 of the inner firmware image from `catalog/<product>/<version>.yaml`. |
| `target_sha256` | `string` | 64-hex SHA-256 of the primary daemon binary for `"emulated"` gateways, or `""` for `"catalogued"`. |
| `suites` | `list[string]` | Sorted list of suite names executed on this gateway (`[]` when `status == "catalogued"`). |
| `reply_semantics` | `"empty_bus_txack" \| "pic_ack" \| "unknown" \| ""` | What `reply` means in this gateway's full suites (see below). `""` when `status == "catalogued"`. |

### `reply_semantics`

The full suites run with the `pic` responder and no devices on the bus
([`findings/empty-bus-reply-semantics.md`](../findings/empty-bus-reply-semantics.md)):

- **`"empty_bus_txack"`** (F460, F461, MyHomeServer1): this firmware reports success only after a device TX-ACK (`$00800\r` on 3-digit RPMsg, `$08\r` in 2-digit v8 PIC mode), which an empty bus never gives. `reply: "nack"` with `verdict: "out"` means the frame **went out** and no device acknowledged it. It is not a refusal and not a divergence from a gateway that ACKs. `nack` with `silent` is still a refusal.
- **`"pic_ack"`**: the harness PIC sent the TX outcome this firmware counts as success (`$19\r` / `$00\r` on legacy 2-digit PIC), so `reply` is the translator's own answer.
- **`"unknown"`**: no `responder=` header, any other or mixed responder, or no target spec in `oracle/targets/`.

`tools/mcp_index.py` derives the value from the target spec (`bus_device`, `version`) via `oracle.bus.needs_device_txack()`, not from a list of gateways.

---

## 4. `verdicts[frame][]` Entries

Keys in `verdicts` are exact input strings from `.cases` / `.seq` suites (`*1*1*21##` for `down` OpenWebNet frames, or space-separated hex `a8 21 ...` for `up` bus injections). Each list is sorted deterministically by `(product, version, suite, line_number)`:

| Field | Type | Description |
| :--- | :--- | :--- |
| `direction` | `"down" \| "up"` | Client-to-gateway (`down`) or bus-to-gateway (`up`). |
| `product` | `string` | Gateway model identifier. |
| `version` | `string` | Firmware version string. |
| `image_sha256` | `string` | Firmware image SHA-256 from the TSV header. |
| `target_sha256` | `string` | Primary binary SHA-256 from the TSV header. |
| `suite` | `string` | Suite name (e.g. `"lights-level"`, `"sound-who22"`). |
| `suite_sha256` | `string` | SHA-256 of the `.cases` / `.seq` file. |
| `reply` | `"ack" \| "nack" \| "-"` | Client session outcome (`*#*1##`, `*#*0##`, or no reply). Interpret it with the gateway's `reply_semantics`. |
| `verdict` | `"out" \| "silent" \| "crash" \| "skipped"` | Whether the gateway emitted output on the bus/session. |
| `bus_frames` | `list[string]` | Bus telegrams emitted (`bus:` items from the TSV `output` column). |
| `emitted_own` | `list[string]` | OpenWebNet frames emitted (`own:` items from the TSV `output` column). |
| `source_tsv` | `string` | Relative path to the committed TSV (`results/<product>/<version>/oracle/full/<suite>.tsv`). |
| `line_number` | `int` | 1-based line number in `source_tsv`. |

---

## 5. Validation Gate

Run:

```bash
python tools/mcp_index.py --check
```

This verifies both that `results/mcp_index.json` passes `validate_index_data()` (schema v1.2.0 + `verdicts_sha256` integrity) and that it is byte-for-byte identical to the index rebuilt from `catalog/` and `results/`.
