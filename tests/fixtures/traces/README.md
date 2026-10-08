# Live bus traces

Recordings from real plants, exported by the MyHOME integration's bus monitor.
Unlike everything under `results/`, these do not come from emulated firmware.

## `myhome_trace_MH200_l4561n_sound_source.json`

- Plant: MH200 gateway, firmware 2.0.32, with an L4561N stereo control interface
  as source 102 and the amplifier of zone 21.
- Recorded on 2026-10-07 between 20:17:42 and 20:18:22 (CEST): 44 frames, 30 tx and 14 rx.
- Sent: source on (`*16*0*102`) and off (`*16*10*102`, `*16*13*102`),
  station/track up (`6001`, `6003`, `6005`) and down (`6101`, `6102`, `6104`),
  preset write `*#16*102*#7*n##`, source 102 on zone 21 (`*16*102*21##`),
  volume up `1002`/`1003` and zone on `*16*0*21##`.

What it shows and what it does not:

- The capture is filtered on WHO 16 and WHERE 102/21, so the gateway's
  `*#*1##` / `*#*0##` replies are not in it. It does not say whether a
  command was ACKed or NACKed.
- None of the commands changed what was reported. Source 102 kept answering
  `*16*3*102##` (on), even after the two off commands. Zone 21 stayed at
  `*16*13*21##` (off) after `*16*0*21##`, and its volume stayed `0`. No
  frequency, preset or RDS frame came back after the station/track steps.
- The L4561N is an auxiliary stereo input, not a tuner, so station steps may
  have no meaning for it.

A repeat recording without the WHERE filter would show the ACK/NACK for each command.
