# Findings: F453 (Web Server DIN 6-Module)

Conclusions and architectural facts for the BTicino `F453` Enhanced Web Server DIN (6 DIN modules, pre-Audio/Video generation), firmware 1.1.0 (`WS_6DIN_010100.fwz`).

Catalog entry: [`catalog/F453/010100.yaml`](../../catalog/F453/010100.yaml).  
Target specification: [`oracle/targets/F453/010100.yaml`](../../oracle/targets/F453/010100.yaml).  
Manifest: [`results/F453/010100/manifest.tsv`](../../results/F453/010100/manifest.tsv).

## Why It Is Here

The `F453` (`WS_6DIN`) is the 6-module DIN Web Server that preceded the 10-module `F453AV` (`WS_10DIN`). While no standalone `.fwz` link was listed on the public web catalog search page, BTicino bundled the complete factory firmware archive `WS_6DIN_010100.fwz` inside the official `MyHOME_Suite` installer (`MyHOME_Suite_030538.exe`, `fileId=58107.23188.24810.50670`, under `default/resTargetDir4/devices/912_2.0_BT/cfgRestore/WS_6DIN_010100.fwz`).

## Provenance

| Layer | Name | Size | SHA-256 |
| --- | --- | --- | --- |
| Bundled firmware package (`MyHOME_Suite_030538.exe` -> `default/resTargetDir4/devices/912_2.0_BT/cfgRestore/WS_6DIN_010100.fwz`) | `WS_6DIN_010100.fwz` (ZipCrypto, password = `F453`) | 11,781,988 | `da02a317e44827beef0b51fd65e4fdf4f53f36865eec85dbca130c14685ed626` |
| Inner release bundle | `WS_6DIN_rel_1_1_0.zip` (ZipCrypto, password = `bticino`) | 11,781,514 | `5595338041ad67e25cc99e1ebecd7f758632977e7bc5adff5f0ae4ef409adf31` |
| Root filesystem uImage | `ubtweb_only_recovery.gz` (U-Boot `ETLinux File System 3.0.1`, Ext2) | 3,926,419 | `1649f7d0436f0b05298db01297ec404a33a517822e25be86b3a3e87075a80ac9` |
| Application overlay | `btweb_app.zip` (ZipCrypto, password = `bticino`) | 1,677,070 | `27a4aac39c641c082ecc75359b3c88e0d7e57aeb3bd126bd8bc47e84ed221a45` |

## Architectural Findings

1. **16 MB Flash Partition Layout vs. 32 MB on F453AV**:
   `Info_Rel.txt` (`ff43f7b188630ab7ce998122d0c14c25ecc30951cffbe052bc43427e7a5cdeca`) documents the hardware differences between `F453` (`MACHINE=WS_6DIN`, U-Boot `setenv MACHINE F453`) and `F453AV` (`MACHINE=WS_10DIN`):
   - `F453` uses a 16 MB NOR flash map (`0x00000000`–`0x00FFFFFF`): U-Boot (`1.1.0`), Linux kernel (`uImage_btweb`, BTPXA 2.3.8 / Linux 2.4.19-rmk7-pxa2-btweb), recovery rootfs (`ubtweb_only_recovery.gz`), and a 6.75 MB JFFS2 partition (`/dev/mtdblock4` mounted at `/mnt/flash`).
   - `Info_Rel.txt` explicitly notes: *"Essendo la flash da 16MByte, non c'e' piu' una partizione di 'solo filesystem' (quello che nella WS_10DIN era mtdblock4). Ora nella flash c'e' il filesystem di recovery che viene sempre usato per fare il boot!"* — `F453` boots directly into the RAM disk unpacked from `ubtweb_only_recovery.gz` and overlays `btweb_app.zip` onto it at startup.

2. **Complete ARMv4 OABI OpenWebNet Daemon Stack (Including `bt_alarm`)**:
   `home/bticino/cfg/stack_open.xml` (`8774ea4c780dec01ad9e1f9139d2eaae3c899149a6a99ca6da8562b620b782b6`) configures `bt_processi` to supervise the full 11-daemon OpenWebNet stack over `/dev/ttyPIC`:
   - `openserver` (TCP 20000) and `scsserver` (TCP 20001, `/dev/ttyPIC`)
   - `bt_luci` (port 40001, WHO `0.1.2.14.15.25.1000`)
   - `bt_difson` (port 40016, WHO `16.1016`)
   - `bt_alarm` (port 40005, WHO `5.9`) — active in `F453` 1.1.0's `stack_open.xml` (`<p3>bin/bt_alarm</p3>`, `<client_03>`)
   - `bt_energia` (port 40003, WHO `3`)
   - `bt_termo` (port 40004, WHO `4`)
   - `bt_device` (port 40013, WHO `13.1001.1004.1005.1008.1013.1018.1022`)
   - `bt_vct` (port 40006, WHO `6.7`)
   - `bt_multi` (port 40022, WHO `22`)
   - `bt_supervisione` (port 40018, WHO `18`)

3. **Companion `WS_10DIN_020202.fwz` Archive (`F453AV` 2.2.2 / Arteor `573992`)**:
   `MyHOME_Suite_030538.exe` also bundles `default/resTargetDir4/devices/909_2.0_BT/cfgRestore/WS_10DIN_020202.fwz` (`15,392,654` bytes, SHA-256 `46e49812d22b6425c39fa8813679ac01e08b4ed4f1c65ddf302d81bbe0bee392`), which is the earlier firmware 2.2.2 release for `F453AV` (`[ARTICOLO1]=F453AV`, `[ARTICOLO2]=573992`, `[FW_VER]=020202`). Because the catalog already tracks the newer `F453AV` `030014` (3.0.14) release under `catalog/F453AV/030014.yaml`, `F453AV` remains pinned to `030014` while `F453` (`WS_6DIN`) is catalogued and emulated under `catalog/F453/010100.yaml`.
