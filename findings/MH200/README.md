# Findings: MH200

Conclusions and static facts for the BTicino MH200 Scenes Programmer (item 003535), firmware 2.1.2.

Catalog entry: [`catalog/MH200/020102.yaml`](../../catalog/MH200/020102.yaml).  
Manifest: [`results/MH200/020102/manifest.tsv`](../../results/MH200/020102/manifest.tsv).

## Why It Is Here

In OpenWebNet-HA/MyHOME#684 and OpenWebNet-HA/own-firmware-oracle#35, community member `@peterbeepping` reported that physical MH200 firmware 2.1.2 gateways NACKed 2-digit Area 0 addressing (`*1*1*02##`) on Home + Project installations, whereas the emulated MH200N 1.1.8 accepted it.

Legrand Support provided the official service pack `MH200_020102.zip` (firmware 2.1.2), which `@peterbeepping` shared in OpenWebNet-HA/MyHOME#713.

## Architectural Findings

1. **Full Linux & OpenWebNet Gateway Stack**:
   Historically, the MH200 (item 003535) was suspected of being an RS232-only serial programmer. Unpacking the firmware reveals a complete Linux system (kernel 2.4/2.6 ARM OABI) with the full OpenWebNet server stack:
   - `openserver`: OpenWebNet daemon listening on TCP 20000.
   - `scsserver`: SCS bus interface daemon on TCP 20001, driving UART `/dev/ttyPIC`.
   - Subsystem translators: `bt_luci` (WHO 1, 2, 14, 15, 1001), `bt_device` (WHO 13, 1013), `bt_termo` (WHO 4), `bt_energia` (WHO 3), `bt_difson` (WHO 16), `bt_alarm` (WHO 5, 9), `bt_vct` (WHO 6, 7), and `bt_taskmanager` (WHO 17).

2. **Compatibility with MH200N Emulation Harness**:
   All daemons are 32-bit little-endian ARM executables dynamically linked against `libc.so.6` and `libcommon.so`, allowing execution under QEMU user-mode emulation with the same PIC coprocessor simulation harness used for MH200N.

3. **Area 0 Point-to-Point Addressing**:
   Replaying lighting frames against `bt_luci` resolves the boundary difference between MH200 2.1.2 and MH200N 1.1.8, providing ground-truth verification alongside authentic hardware traces (OpenWebNet-HA/MyHOME#700).
