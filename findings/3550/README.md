# 3550 temperature control central unit (99 zones)

Catalog entry: [`catalog/3550/030020.yaml`](../../catalog/3550/030020.yaml).
Manifest: built by `oracle.yml` in GitHub Actions once the entry is on `main`.

## Why it is here

OpenWebNet-HA/OWNd#97 decodes the WHO 4 holiday plan (`115#P`, `215#P`) and
multi-day vacation (`13DDD#P`, `23DDD#P`, `33DDD#P`) frames. The
`thermo-holiday-program` suite pins what every emulated gateway puts on the bus
for those commands. What a central unit *reports* while a holiday or vacation
runs is the central's own behaviour, not a gateway's, and no community capture
of it exists yet. The 3550 firmware is the only first-hand source for it.

## Provenance

| Layer | Name | Size | SHA-256 |
| --- | --- | --- | --- |
| Vendor download (TiThermo 2.0) | `Version 2_0.zip` | 9,656,492 | `c5dee2e9…fa33d04` |
| Firmware package | `3550_030020.fwz` (ZipCrypto, password = model, `3550`) | 82,142 | `ef2e3ccd…1c7a471` |
| Flash image | `3550_030020.mot` (Motorola S-records) | 281,774 | in the manifest |

The TiThermo manual (section 8, *Firmware update*, pp. 31–32) shows the same
packaging: `.fwz` files in TiThermo's `firmware` folder, info dialog
"FILE MITSUBISHI / 3550", flashed over the serial programming cable
(item 335919 or 3559).

## Static facts (firmware 3.0.20)

Verified by decoding the S-records; no emulation.

- **Memory:** S2 records cover `0x0E0000`–`0x0FFFFF` (115,464 bytes). The top
  36 bytes are the M16C fixed vector table: reset at `0x0E7BBA`, every other
  fixed vector at one default handler (`0x0E7C77`). A variable vector table sits
  at `0x0FDF00` with UART and timer handlers at `0x0E8ADC`–`0x0E8D72`. That
  layout is the Mitsubishi/Renesas M16C/60 series (Ghidra language
  `M16C/60:LE:16:default`). QEMU has no M16C target, so this image is for static
  analysis only.
- **Bus line templates** at `0x0E0014`–`0x0E00C3`, in the same `$`-framed
  serial dialect the gateways' bus servers use (see `bus_framer: line`):
  `$0490003000`, `$0499003000`, `$0493003000`, `$049F0030E0`, `$04BF003000`,
  `$04BA003000`, `$0496003000`, `$042F003000`, `$06D1000302F0000000`,
  `$06D100030200000000`, `$06D200030500000000`, `$06D200030400000000`.
- **UI string tables** are 17-byte fields indexed `language × 0x11`, in seven
  languages. The daily holiday (`HOLIDAY` / `FESTIVO` / `JOUR FERIE`, table at
  `0x0E34A2`) and the multi-day vacation (`HOLIDAYS` / `FERIE` / `VACANCES` /
  `URLAUB`, table at `0x0E3519`) are separate menus.

## Open

- The routine that builds the central's bus report while a holiday or vacation
  runs (does the report carry the return program; does the day count go down).
- Whether TiThermoBasic 11.4 (same vendor pages) carries a 4-zone central image.
