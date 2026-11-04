# 3488 HOMETOUCH 7" touch screen (SMARTDES), firmware 3.2.5

Catalog entry: [`catalog/3488/030205.yaml`](../../catalog/3488/030205.yaml).
Manifest: [`results/3488/030205/manifest.tsv`](../../results/3488/030205/manifest.tsv)
(18,008 rows, built with `tools/unpack.py` in the project's docker
harness; `oracle.yml` rebuilds it once the entry is on `main`).

## Why it is here

The README listed HOMETOUCH among the excluded products as "an embedded
Android touch display whose firmware ships only as Android OTA updates". That
was an inference, and the vendor product page proves it wrong: it links
`SMARTDES_030205.fwz` directly, and the package is the same Linux stack the
emulated targets run. The unit is a MyHOME_Up touch screen and a video door
entry internal station, so it is auxiliary hardware like the H4684, but it
carries a full `openserver` / `scsserver` / translator stack of its own, which
makes it a Phase 2 emulation candidate rather than a static-only entry.

## Provenance

| Layer | Name | Size | SHA-256 |
| --- | --- | --- | --- |
| Vendor download (product page, Documentation, "Firmware") | `SMARTDES_030205.fwz` | 167,250,224 | `c88d7215…0101624` |
| Main rootfs | `btweb_only.ext4.gz.sha256.sig.zip` → `btweb_only.ext4.gz` → ext4, 1,024,000,000 bytes | 114,607,037 | in the manifest |
| Recovery rootfs | `btweb_only_recovery.ext4.gz.sha256.sig.zip` → ext4, 153,600,000 bytes | 46,356,100 | in the manifest |
| Kernel | `uImage.zip.sha256.sig.zip` → `uImage.zip` → `uImage` + 16 `mamoj_*.dtb` | 6,285,976 | in the manifest |

The `.fwz` is a stored zip; `fwz.xml` is ZipCrypto-encrypted and opens with
the package name `SMARTDES` (the `<name>` it declares), not with the article
number `3488`, so the catalog lists `SMARTDES` first. `fwz.xml` (`fwz_ver`
3.2.5, `xml_ver` 3.0, provider `btesuv3`) declares a `firmware` action (main
rootfs + kernel), a `recovery_firmware` action (recovery rootfs + kernel) and
seven `extra_download` members (`0.xtz`, `7.xtz`, `47.xtz`, `70.xtz`, `72.xtz`,
`74.xtz`, `90.xtz`) that are not in the package. Every daemon under
`/home/bticino/bin` has the same SHA-256 in the main and the recovery rootfs.

## Static facts (firmware 3.2.5)

From the manifest and the extracted trees; no program was run.

- **Platform.** U-Boot legacy `uImage`, `Linux-5.15.157-lts-next+gc9df163`,
  ARM, load and entry `0x10008000`, built 2025-12-10; `/lib/modules/5.15.157-lts-next+gc9df1633303c+p0`.
  The device trees name the board `BTicino Mamoj board` (`mamoj_dig0-base0`
  … `mamoj_dig4-base5`, `mamoj_mfg`, `mamoj_rev0_GW`, i.MX `fsl,*` bindings).
  User space is ARMv7 `eabi5-hf` (1,808 shared objects, 528 relocatable
  objects, 4 executables), Qt 5.15.7 (`BtSmartDes_qws`, `SmartToolHLS`,
  `configuration_manager`), dropbear, mosquitto, kea, flexisip, vsftpd. No
  Android runtime: `/etc/init.d` is a sysvinit tree (`rcS`, `bt_daemon-apps.sh`,
  `bt-networking.sh`, `S124_FwrUpdate.sh`), and `/home/bticino/sp/dbfiles_ws.xml`
  carries `<ver_webserver>3.2.5`.
- **Daemon stack** (`/home/bticino/cfg/stack_open.xml`, `<bt_daemon><stackopen>`
  p0..p16): `openserver`, `scsserver`, `bt_device`, `bt_vct`, `bt_av_media`,
  `bt_answering_machine`, `bt_upnp_hls`, `configuration_manager`, `bt_mhportal`,
  `SmartToolHLS`, `bt_dbus_manager`, `coso`, `bt_termo`, `bt_luci`,
  `bt_supervisione`, `bt_multi`, `bt_ipcamera`; the GUI slot `<gui><p0>` is
  `BtSmartDes_qws`. Libraries in `/home/bticino/lib` (`libopen`, `libopenscs`,
  `libopentrsl`, `libopenxml`, `libwhere`, `libautomation`, `libacca`, `libdiag`)
  and `/home/bticino/libcoso` (`libplug_scs`, `libplug_automation_*`,
  `libplug_aux_*`), the same names as on MyHomeServer1 `028206`.
- **OpenWebNet server** (`/home/bticino/cfg/openserver`): `OwnPort=20000`,
  `MaxClient=50`, `IsGateway=1`, `IsPermissive=1`, `ForceHMAC=1`, `HlsNeeded=8`,
  `CertCheck=1`, `[Range Ip] range_01=192.168.129.2`. The `[Stackopen]` client
  table routes WHO to translators: `bt_device` 13, 1001, 1004, 1005, 1008,
  1013, 1018, 1022, 1027; `bt_vct` 8; `bt_av_media` 7; `bt_termo` 4;
  `bt_supervisione` 18; `bt_multi` 22; `bt_luci` 0, 1, 14, 15, 25, 1000;
  `coso` 2, 9, 29. The binary carries the `*99*0##` / `*99*1##` / `*99*4##` /
  `*99*9##` / `*99*10##` / `*99*11##` / `*99*13##` session openers, `*98*2##`
  and `*#*1##`, and the strings "Force HMAC enable" / "HMAC %sable with
  password own".
- **Bus server** (`<scsserver>` in `stack_open.xml`): `port_0` 20001, `port_1`
  20002, `bus_type` 0, `max_num_client_up` 9, serial channel `/dev/ttymxc1` at
  115200 with `rts_alim` 1. The binary's compiled-in default is `/dev/ttyS1`;
  the configuration wins. Its PIC vocabulary (`$17`, `$18`, `$024`, `$02603`,
  `$02802`, `$0020000`, `$24000000EOF`, `$25…EOF`, `$26…EOF`, `$28…EOF`,
  `Richiesta_Invio_Frame`, `send_esito`) is the 2-digit UART dialect of
  [`findings/pic-inbound-protocol.md`](../pic-inbound-protocol.md) with the
  `EOF`-terminated clock/date writes; which TX-ACK form it expects is a
  discovery question, not a manifest fact.
- **Portal** (`/home/bticino/cfg/bt_mhportal`): `TLSCheck=1`, `OwnPort=20000`,
  watchdog file under `/var/tmp/bticino/bt_wd`, HPPP polling 300 s.
- **Boot** (`/etc/init.d/bt_daemon-apps.sh`): mounts the `extra` partition
  (`/dev/mmcblk0p7`) on `/home/bticino/cfg/extra`, keeps a 50 MiB FAT "usbkey"
  image labelled `SMARTDES` with the OSS licence bundle, switches
  `/var/tmp/stack_open.xml` to `stack_open_reduced.xml` when the usbkey holds
  user libraries, and reads the firmware version from `dbfiles_ws.xml`.
  `conf.xml.def.067259` under `cfg/restore` ties the image to article 067259
  (the Legrand catalogue number of the same unit).

## Open

- **Emulation.** The daemon set, the `stack_open.xml` shape, the ports
  (20000 / 20001 / 30xxx-31xxx) and the libraries match the MyHomeServer1
  target. A Phase 2 target (`oracle/targets/3488/030205.yaml`) needs the
  discovery pass (`python -m oracle.run discover … --program scsserver`) to
  pin the device node and the runtime files the daemons require; it is not in
  this change.
- Whether the HOMETOUCH, as an internal unit rather than a gateway, answers
  the full WHO set on 20000 or only what its `[Stackopen]` table lists, and
  what `IsPermissive=1` / `HlsNeeded=8` gate.
