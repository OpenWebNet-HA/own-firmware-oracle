# F450 (020010): the BACnet side runs on the firmware's own `ebacgw`

## What was wrong

The F450 target used to replace `ebacgw`, the gateway's BACnet service, with a
Python mock on TCP 1234 that answered `1` to every `getValue` / `setValue`.
`bacclient` (the F450's OpenWebNet server) talks to that service for every
thermo command, so the F450 thermo suites recorded the mock, not the firmware:

- the same frame gave `crash` on one run and `nack` on the next
  (`*#4*#0*#14*0210*1##` / `…0250…` swapped between two runs);
- every row's output was the stack's boot traffic, not a reply;
- `crash` only meant "some process of the stack exited"; nothing pointed at
  `bt_termo`, whose log stayed empty.

## How the F450 is put together (from its own files)

`cfg/stack_open.xml` starts five processes: p0 `bacclient`, p1 `ebacgw`,
p2 `bt_device`, p3 `scsserver`, p4 `bt_termo`.

| Piece | What the firmware shows |
|---|---|
| `cfg/restore/conf.xml.def` | factory configuration written by **TiOpenBacnet 2.0.1**: `bacnet_id` 123456 and one BACnet device per type, each on an OpenWebNet thermo address (`#1#1` fan coil … `#1#6` generic unit, `7` probe, `8` thermostat) |
| `cfg/BACnetTemplates.xml` | the BACnet objects behind each device type |
| `bacclient` | reads `conf.xml` and the templates (the only binary naming them and `bacnet_id`); SOAP client of `127.0.0.1:1234`; serves its own SOAP endpoint on **1235** (`soap_bind`, then an accept loop) after `getValue` of Device property 515 succeeds ("Waiting for eBACgw") |
| `ebacgw` | SOAP service `urn:bacnet_ws`; keeps the BACnet objects in `cfg/extra/50/master.sqlite`, copied to `/var/tmp/db/` at start |

On a real F450 that database is uploaded by TiOpenBacnet. TiOpenBacnet is not
in MyHOME_Suite 3.5.38 (whose F450 definition `devices/1556_*` covers the
OpenWebNet side only), not on the Home Systems product sheet, and not found on
the legacy bticino.be software pages.

## The database, derived from `ebacgw`'s own code

Decompiled with Ghidra 12.1.4 (ARM, `home/bticino/bin/ebacgw`, sha256
`b99d2566…`) in a sandbox with no network:

- **Columns.** `ebacgw` inserts Device rows itself at start:
  `INSERT INTO Object VALUES(%u,%u,%u,%u,%u,'%s')` called as
  `(8, <device>, 531, 1, 2, "47808")`, and its helpers select
  `… property=%u and idx=%d` and `SELECT value, Encoding …`. Hence
  `Object(type, instance, property, idx, encoding, value)`, `encoding` being
  the BACnet application tag (2 unsigned, 7 character string). The other
  tables and columns are the ones its queries name: `RemoteObjects`,
  `remotenetworks`, `routers`.
- **Start-up checks** (`FUN_0000a528`), in order:
  1. exactly one row `type=8 AND property=120` (vendor-identifier); its
     instance is the device id, else exit -4 ("Bacnet device id: [ERROR]");
  2. writes the Device properties it owns (531 BACnet/IP port 47808, 532, 120
     vendor-identifier **582**, 121 vendor-name `BTICINO`, model `eBACgw`,
     version `eBACgw 2.1.0`, …);
  3. property 514 as a non-zero integer, else exit -5 ("Webservice port");
  4. property 515 as a string, else exit -6 ("Adapter Webservice address");
  5. property 516 present, else exit -7 ("Adapter Webservice process"); its
     value is not read anywhere else.

So the target seeds exactly:

| Row | Value | Evidence |
|---|---|---|
| Device instance | 123456 | `<bacnet_id>` in the factory `conf.xml.def` |
| 120 vendor-identifier | 582 | the value `ebacgw` writes itself |
| 514 web service port | 1234 | `bacclient` calls `127.0.0.1:1234` |
| 515 adapter web service | `127.0.0.1:1235` | `bacclient` binds 1235; same `host:port` form as its own `127.0.0.1:1234` |
| 516 adapter process | `bacclient` | presence only |

Everything else (Device name, versions, the plant objects) `ebacgw` and
`bacclient` write at runtime, as on a device.

## Result

Running the firmware's own `ebacgw` on that database, the setpoint frames that
used to crash or NACK at random are ACKed and put the same telegrams on the bus
as MyHomeServer1, MH200N and F454 (`$06D1000302C1122A00` for 21.0 °C,
`…C1123200` for 25.0 °C), with no boot traffic in the row.

## Still open

- The factory `stack_open.xml` sets `bt_termo` (and `bacclient`'s thermo
  client) to `working_mode 0`. The target starts `bt_termo` as an installer's
  configuration would, since `conf.xml.def` configures thermostats and probes.
- A real `master.sqlite` from a configured F450 would confirm 515/516 byte for
  byte. Copy `/home/bticino/cfg/extra/50/master.sqlite` from any F450 (FTP or
  telnet) to compare.
