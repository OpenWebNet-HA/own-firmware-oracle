# F450 (020010) OPEN-BACnet Gateway Architecture

## Hardware & Firmware Architecture
- **Firmware Image**: `F450_020010.fwz` (SHA-256 `9e625012d3e8629bea15d608f7c1de160e971499a39f3fd86f0c68103da8b9da`).
- **Operating System**: Linux kernel 2.6.32 on ARM 32-bit EABI5.
- **Process Hierarchy**:
  - Boot script: `/etc/init.d/bt_daemon-apps.sh` starts `bt_daemon`.
  - Daemons: `scsserver` (owns SCS bus on `/dev/ttyS1`), `bt_device`, and `bt_termo`.
  - Gateway Bridge: `bacclient` and `ebacgw`.

## Protocol & Boundary Cut
- **Absence of `openserver`**: Standalone OpenWebNet gateways (`MH200N`, `F454`, `MH202`, `F453AV`, `F459`, `F460`, `F461`, `MyHomeServer1`) run `openserver` listening on TCP port 20000. F450 does NOT run `openserver`.
- **BACnet Interface**: F450 acts as a BACnet server/client gateway, translating OpenWebNet/SCS frames directly into BACnet protocol objects over BACnet/IP (UDP port 47808).
- **Target Specification**: Pinned in `oracle/targets/F450/020010.yaml` with roles `supervisor` (`bt_daemon`), `bus_server` (`scsserver`), and `translator` (`bacclient`, `ebacgw`, `bt_device`, `bt_termo`).
