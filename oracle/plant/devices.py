# SPDX-License-Identifier: Apache-2.0
"""Clean-room virtual SCS/OpenWebNet device state machines."""

from __future__ import annotations

from oracle.plant.models import (
    CoverConfig,
    DimmerConfig,
    EnergyActuatorConfig,
    EnergyMeterConfig,
    LightConfig,
    ScenarioConfig,
    ThermoZoneConfig,
)
from oracle.plant.scs_codec import encode_extended_inbound, encode_short_inbound

DEFAULT_F414_LEVELS: dict[int, int] = {
    0: 100,
    1: 100,
    2: 119,
    3: 129,
    4: 139,
    5: 149,
    6: 159,
    7: 169,
    8: 163,
    9: 174,
    10: 200,
}

DEFAULT_F418U2_LEVELS: dict[int, int] = {
    0: 100,
    1: 100,
    2: 110,
    3: 110,
    4: 120,
    5: 130,
    6: 140,
    7: 150,
    8: 160,
    9: 180,
    10: 200,
}

DEFAULT_FAULT_MASK: bytes = b"\x89\xfb\xff\xf7"


THERMO_ACTUATOR_STATUS_BYTES: dict[int, int] = {
    0: 0x01,
    1: 0x00,
    2: 0x10,
    3: 0x11,
    4: 0x12,
    5: 0x20,
    6: 0x21,
    7: 0x22,
    8: 0x23,
    9: 0x2F,
}


class LightDevice:
    """Clean-room relay light point (WHO=1, 14, e.g. F411/4)."""

    def __init__(self, config: LightConfig) -> None:
        self.config = config
        self.what: int = config.initial_what
        self.locked: bool = config.initial_locked

    def reset(self) -> None:
        """Reset relay state and lock flag to initial fixture values."""
        self.what = self.config.initial_what
        self.locked = self.config.initial_locked

    def status_frame(self, *, digits: int) -> bytes:
        """Encode current relay status as a short B8 inbound telegram."""
        state_byte = 0x00 if self.what > 0 else 0x01
        return encode_short_inbound(
            0xB8,
            self.config.bus_addr,
            0x12,
            state_byte,
            digits=digits,
            subbus=self.config.subbus,
        )

    def handle_short(
        self,
        b1: int,
        b2: int,
        b3: int,
        *,
        digits: int,
    ) -> list[bytes]:
        """Handle a 4-byte P2P command or status request addressed to this relay."""
        if b1 != 0x00:
            return []
        if b2 == 0x15 and b3 == 0x00:
            return [self.status_frame(digits=digits)]
        if b2 == 0x11 and b3 == 0x00:
            self.locked = True
            return [
                encode_short_inbound(
                    0xB8,
                    self.config.bus_addr,
                    0x11,
                    0x00,
                    digits=digits,
                    subbus=self.config.subbus,
                )
            ]
        if b2 == 0x10 and b3 == 0x00:
            self.locked = False
            return [
                encode_short_inbound(
                    0xB8,
                    self.config.bus_addr,
                    0x10,
                    0x00,
                    digits=digits,
                    subbus=self.config.subbus,
                )
            ]
        if b2 == 0x12:
            if self.locked:
                return []
            if b3 == 0x01:
                self.what = 0
                return [self.status_frame(digits=digits)]
            if b3 == 0x00 or (b3 & 0x0F) == 0x0D:
                self.what = 1
                return [self.status_frame(digits=digits)]
            if b3 == 0x03:
                self.what = 0 if self.what > 0 else 1
                return [self.status_frame(digits=digits)]
            if b3 in (0x04, 0x86):
                # WHAT=18 impulse: ON then immediately OFF
                self.what = 1
                on_frame = self.status_frame(digits=digits)
                self.what = 0
                off_frame = self.status_frame(digits=digits)
                if self.config.group > 0 and self.config.subbus == 0:
                    grp_on = encode_extended_inbound(
                        bytes(
                            [
                                0xD5,
                                self.config.group,
                                0x01,
                                0x34,
                                0x07,
                                0x00,
                                0x00,
                                0x00,
                            ]
                        ),
                        digits=digits,
                    )
                    grp_off = encode_extended_inbound(
                        bytes(
                            [
                                0xD5,
                                self.config.group,
                                0x01,
                                0x34,
                                0x07,
                                0x00,
                                0x00,
                                0x01,
                            ]
                        ),
                        digits=digits,
                    )
                    return [on_frame, grp_on, off_frame, grp_off]
                return [on_frame, off_frame]
        return []


class DimmerDevice:
    """Clean-room dimmer actuator (WHO=1, F414 / F418U2)."""

    def __init__(self, config: DimmerConfig) -> None:
        self.config = config
        default_curve = (
            DEFAULT_F418U2_LEVELS
            if config.model.upper() == "F418U2"
            else DEFAULT_F414_LEVELS
        )
        self.curve: dict[int, int] = {**default_curve, **config.discrete_levels}
        self.what: int = config.initial_what
        self.dim1_level: int = config.initial_dim1_level
        self.dim1_speed: int = config.initial_dim1_speed
        self.locked: bool = config.initial_locked

    def reset(self) -> None:
        """Reset dimmer state to initial fixture values."""
        self.what = self.config.initial_what
        self.dim1_level = self.config.initial_dim1_level
        self.dim1_speed = self.config.initial_dim1_speed
        self.locked = self.config.initial_locked

    @staticmethod
    def _level_to_what(dim1_level: int) -> int:
        if dim1_level <= 100:
            return 0
        if dim1_level >= 195:
            return 10
        pct = dim1_level - 100
        return max(2, min(10, (pct // 10) + 2))

    def status_byte(self) -> int:
        """Return the SCS B8 status byte for the current discrete WHAT step."""
        if self.what <= 0:
            return 0x01
        if self.what == 1:
            return 0x00
        step = min(10, max(2, self.what))
        return ((step - 1) << 4) | 0x0D

    def status_frame(self, *, digits: int) -> bytes:
        """Encode current coarse dimmer state as a short B8 inbound telegram."""
        return encode_short_inbound(
            0xB8,
            self.config.bus_addr,
            0x12,
            self.status_byte(),
            digits=digits,
            subbus=self.config.subbus,
        )

    def handle_short(
        self,
        b1: int,
        b2: int,
        b3: int,
        *,
        digits: int,
    ) -> list[bytes]:
        """Handle a 4-byte P2P command or status request addressed to this dimmer."""
        if b1 != 0x00:
            return []
        if b2 == 0x15 and b3 == 0x00:
            return [self.status_frame(digits=digits)]
        if b2 == 0x11 and b3 == 0x00:
            self.locked = True
            return [
                encode_short_inbound(
                    0xB8,
                    self.config.bus_addr,
                    0x11,
                    0x00,
                    digits=digits,
                    subbus=self.config.subbus,
                )
            ]
        if b2 == 0x10 and b3 == 0x00:
            self.locked = False
            return [
                encode_short_inbound(
                    0xB8,
                    self.config.bus_addr,
                    0x10,
                    0x00,
                    digits=digits,
                    subbus=self.config.subbus,
                )
            ]
        if b2 == 0x12:
            if self.locked:
                return []
            if b3 == 0x01:
                self.what = 0
                self.dim1_level = 100
                self.dim1_speed = 2
                return [self.status_frame(digits=digits)]
            if b3 == 0x00:
                self.what = 1
                self.dim1_level = self.curve.get(1, 100)
                self.dim1_speed = 2
                return [self.status_frame(digits=digits)]
            if (b3 & 0x0F) == 0x0D:
                hi = (b3 >> 4) & 0x0F
                if 1 <= hi <= 9:
                    self.what = hi + 1
                    self.dim1_level = self.curve.get(self.what, 100 + hi * 10)
                    self.dim1_speed = 2
                    return [self.status_frame(digits=digits)]
        return []

    def handle_extended(self, payload: bytes, *, digits: int) -> list[bytes]:
        """Handle an 8-byte D1 extended command or query addressed to this dimmer."""
        b2 = payload[2]
        op = payload[3]
        sub = payload[4]
        kind = payload[5]
        val1 = payload[6]
        val2 = payload[7]

        if op == 0x42 and sub == 0x0D:
            if kind == 0x0D:
                # *#1*WHERE*#1*<level>*<speed>##
                pct = max(0, min(100, val1))
                self.dim1_level = 100 + pct
                self.what = self._level_to_what(self.dim1_level)
                reply_spd = 5 if val2 == 0 else val2
                self.dim1_speed = 2 if val2 == 0 else val2
                ans = bytes(
                    [0xD2, self.config.bus_addr, b2, 0x44, 0x0D, 0x0D, pct, reply_spd]
                )
                return [encode_extended_inbound(ans, digits=digits)]
            if kind == 0x01:
                # *1*<what>#<speed>*WHERE##
                pct = max(0, min(100, val1))
                self.dim1_level = 100 + pct
                self.what = self._level_to_what(self.dim1_level)
                self.dim1_speed = 2 if val2 == 0 else val2
                return [self.status_frame(digits=digits)]
        if op == 0x43 and sub == 0x0D:
            pct = max(0, min(100, self.dim1_level - 100))
            if kind == 0x0D:
                ans = bytes(
                    [
                        0xD2,
                        self.config.bus_addr,
                        b2,
                        0x44,
                        0x0D,
                        0x0D,
                        pct,
                        self.dim1_speed,
                    ]
                )
                return [encode_extended_inbound(ans, digits=digits)]
            if kind == 0x00 and self.config.model.upper() != "F418U2":
                ans = bytes(
                    [
                        0xD2,
                        self.config.bus_addr,
                        b2,
                        0x44,
                        0x0D,
                        0x00,
                        pct,
                        self.dim1_speed,
                    ]
                )
                return [encode_extended_inbound(ans, digits=digits)]
        return []


class CoverDevice:
    """Clean-room roller shutter / automation actuator (WHO=2, 14, e.g. F411/2)."""

    def __init__(self, config: CoverConfig) -> None:
        self.config = config
        self.what: int = config.initial_what
        self.level: int = config.initial_level
        self.locked: bool = config.initial_locked
        self.move_until_ms: float | None = None

    def reset(self) -> None:
        """Reset shutter state, position, lock, and timer."""
        self.what = self.config.initial_what
        self.level = self.config.initial_level
        self.locked = self.config.initial_locked
        self.move_until_ms = None

    def status_byte(self) -> int:
        """Return the SCS B8 status byte (0A=stop, 08=up, 09=down)."""
        if self.what == 1:
            return 0x08
        if self.what == 2:
            return 0x09
        return 0x0A

    def status_frame(self, *, digits: int) -> bytes:
        """Encode current shutter state as a short B8 inbound telegram."""
        return encode_short_inbound(
            0xB8,
            self.config.bus_addr,
            0x12,
            self.status_byte(),
            digits=digits,
            subbus=self.config.subbus,
        )

    def handle_short(
        self,
        b1: int,
        b2: int,
        b3: int,
        *,
        digits: int,
        now_ms: float = 0.0,
    ) -> list[bytes]:
        """Handle a 4-byte P2P command or status request addressed to this shutter."""
        if b1 != 0x00:
            return []
        if b2 == 0x15 and b3 == 0x00:
            return [self.status_frame(digits=digits)]
        if b2 == 0x11 and b3 == 0x00:
            self.locked = True
            self.what = 0
            self.move_until_ms = None
            return [
                encode_short_inbound(
                    0xB8,
                    self.config.bus_addr,
                    0x11,
                    0x00,
                    digits=digits,
                    subbus=self.config.subbus,
                ),
                self.status_frame(digits=digits),
            ]
        if b2 == 0x10 and b3 == 0x00:
            self.locked = False
            return [
                encode_short_inbound(
                    0xB8,
                    self.config.bus_addr,
                    0x10,
                    0x00,
                    digits=digits,
                    subbus=self.config.subbus,
                )
            ]
        if b2 == 0x12 and b3 in (0x08, 0x09, 0x0A):
            if self.locked:
                return []
            if b3 == 0x0A:
                self.what = 0
                self.move_until_ms = None
            elif b3 == 0x08:
                self.what = 1
                self.move_until_ms = now_ms + float(self.config.travel_time_ms)
            else:
                self.what = 2
                self.move_until_ms = now_ms + float(self.config.travel_time_ms)
            return [self.status_frame(digits=digits)]
        return []

    def tick(self, now_ms: float, *, digits: int) -> list[bytes]:
        """Check whether timed shutter movement has completed at now_ms."""
        if self.move_until_ms is not None and now_ms >= self.move_until_ms:
            self.what = 0
            self.move_until_ms = None
            return [self.status_frame(digits=digits)]
        return []


class ThermoZoneDevice:
    """Clean-room thermoregulation zone and probe simulator (WHO=4, KW4691)."""

    def __init__(self, config: ThermoZoneConfig) -> None:
        self.config = config
        self.temp_deci: int = config.initial_temp_deci
        self.setpoint_deci: int = config.initial_setpoint_deci
        self._season: str = "heating"
        self.mode = config.initial_mode
        self.function: int = config.initial_function
        self.offset: str = config.initial_offset
        self.valve_heating: int = config.initial_valve_heating
        self.valve_cooling: int = config.initial_valve_cooling
        self.actuator_status: int = config.initial_actuator_status
        self.actuators: dict[int, int] = (
            dict(config.actuators)
            if config.actuators
            else {1: config.initial_actuator_status}
        )
        self.fan_speed: int = config.initial_fan_speed
        self.humidity: int | None = config.initial_humidity

    def reset(self) -> None:
        """Reset zone state to initial fixture values."""
        self.temp_deci = self.config.initial_temp_deci
        self.setpoint_deci = self.config.initial_setpoint_deci
        self._season = "heating"
        self.mode = self.config.initial_mode
        self.function = self.config.initial_function
        self.offset = self.config.initial_offset
        self.valve_heating = self.config.initial_valve_heating
        self.valve_cooling = self.config.initial_valve_cooling
        self.actuator_status = self.config.initial_actuator_status
        self.actuators = (
            dict(self.config.actuators)
            if self.config.actuators
            else {1: self.config.initial_actuator_status}
        )
        self.fan_speed = self.config.initial_fan_speed
        self.humidity = self.config.initial_humidity

    @property
    def mode(self) -> int:
        """The zone mode, the WHAT of ``*4*M*Z##`` reported with dimension 12."""
        return self._mode

    @mode.setter
    def mode(self, value: int) -> None:
        # The season follows the mode written: WHAT 0 (conditioning) and the
        # 2xx cooling programs select cooling, WHAT 1 and the 1xx heating
        # programs select heating, the 3xx generic modes (303 off included)
        # keep the season in force. A zone that starts in a 3xx mode starts in
        # heating, which is what the standalone KW4691 of the physical F461
        # bench reported in heating season.
        self._mode = value
        if value == 0 or 200 <= value <= 299:
            self._season = "cooling"
        elif value == 1 or 100 <= value <= 199:
            self._season = "heating"

    @property
    def season(self) -> str:
        """Return the season in force, ``"heating"`` or ``"cooling"``.

        Tracked under the mode, as the physical KW4691 does (MyHOME fixture
        ``f461_live_2026-10-15``, ``run3_climate`` t=163.5..395.0): OFF
        (``*4*303*2##``) entered from cooling keeps cooling underneath, so
        ``*#4*2*11##`` goes on answering ``11*0``; ``*4*1*2##`` and
        ``*4*0*2##`` sent while OFF flip the underlying season without leaving
        OFF (dimension 12 keeps reporting 303), and only a setpoint write
        leaves OFF, in the manual mode of that season.
        """
        return self._season

    def _active_side(self) -> int:
        """Return the dimension 19 value of the active season's output."""
        if self.season == "cooling":
            return self.valve_cooling
        return self.valve_heating

    def _mode_byte(self) -> int:
        if self.mode == 0:
            return 0xF1
        if self.mode == 102:
            return 0xF2
        if self.mode == 202:
            return 0xF3
        if self.mode == 303:
            return 0xFF
        if self.mode in {208, 2}:
            return 0x12
        if self.mode == 108:
            return 0x10
        return 0xF0

    def actuator_frame(self, act_id: int, *, digits: int) -> bytes:
        """Encode actuator ``act_id`` as ``B7 <80+Z> 3<A> <state>``.

        The gateway reports the telegram as ``*#4*Z#A*20*V##``.
        """
        return encode_short_inbound(
            0xB7,
            0x80 + self.config.bus_addr,
            0x30 | (act_id & 0x0F),
            THERMO_ACTUATOR_STATUS_BYTES.get(self.actuators[act_id], 0x01),
            digits=digits,
        )

    def actuator_frames(self, *, digits: int) -> list[bytes]:
        """Encode every actuator of the zone, lowest actuator number first."""
        return [self.actuator_frame(a, digits=digits) for a in sorted(self.actuators)]

    def handle_short(self, payload: bytes, *, digits: int) -> list[bytes]:
        """Handle a 4-byte WHO=4 command or query."""
        b0, b1, b2, b3 = payload[0], payload[1], payload[2], payload[3]
        addr = self.config.bus_addr

        # Virtual probe write B4 <Z> 30 <temp>
        if b0 == 0xB4 and b1 == addr and b2 == 0x30:
            self.temp_deci = b3
            return []

        # All-zones actuators query B9 00 30 10 (*#4*#0*20##, *#4*0#0*20##):
        # every zone answers for itself, as both actuators of the physical
        # F461 bench did within 130 ms of the request.
        if payload == b"\xb9\x00\x30\x10":
            return self.actuator_frames(digits=digits)

        # Zone actuators query A9 <Z> 30 10 (*#4*Z*20##) or A9 <80+Z> 30 10
        # (*#4*Z#0*20## on the gateways that set the sub-address bit)
        if b0 == 0xA9 and b1 in (addr, 0x80 + addr) and b2 == 0x30 and b3 == 0x10:
            return self.actuator_frames(digits=digits)

        # Single actuator status query 99 <80+Z> 3<A> 10 (*#4*Z#A*20##)
        if b0 == 0x99 and b1 == (0x80 + addr) and b3 == 0x10:
            act_id = b2 & 0x0F
            if act_id in self.actuators:
                return [self.actuator_frame(act_id, digits=digits)]
            return []

        # Setpoint write 90/91/92 <Z> 3<m> <half_deg> (*#4*Z*#14*T*M##)
        if (
            b0 in (0x90, 0x91, 0x92)
            and b1 == self.config.bus_addr
            and (b2 & 0xF0) == 0x30
        ):
            return self._handle_setpoint_write(b0, b2, b3, digits=digits)

        # Zone mode write 93 <Z> 30 <cmd> (*4*M*Z##). The outputs follow the
        # new mode: OFF (303) releases every actuator of the zone, any other
        # mode drives the active season's actuator on the setpoint /
        # temperature gap like a setpoint write does and, like a setpoint
        # write that changes the season, releases the other season's output
        # (_drive_active_actuator, after the f461_live_2026-10-15 captures).
        # Emulation only: the zone form *4*303*2## has no physical
        # row (the central form *4*303*#2## leaves the standalone KW4691 of
        # the F461 bench unchanged, reports/data/live_scan_confirm_2026-10-26).
        if b0 == 0x93 and b1 == self.config.bus_addr and b2 == 0x30:
            return self._handle_mode_write(b3, digits=digits)

        # Zone queries 99 <Z> 3<P> <dim>
        if b0 == 0x99 and b1 == self.config.bus_addr and (b2 & 0xF0) == 0x30:
            return self._handle_zone_query(b2 & 0x0F, b3, digits=digits)
        return []

    def _handle_mode_write(self, b3: int, *, digits: int) -> list[bytes]:
        # 00 manual heating (*4*1*Z##), 01 manual cooling (*4*0*Z##), 0F off
        # (*4*303*Z##); any other byte is left unanswered. A season written
        # while the zone is OFF only moves the underlying season.
        if b3 not in (0x00, 0x01, 0x0F):
            return []
        if b3 == 0x0F:
            self.mode = 303
        elif self.mode == 303:
            self._season = "heating" if b3 == 0x00 else "cooling"
        else:
            self.mode = 1 if b3 == 0x00 else 0
        hi = (self.setpoint_deci >> 8) & 0xFF
        lo = self.setpoint_deci & 0xFF
        mb = self._mode_byte()
        ans = bytes([0xD2, self.config.bus_addr, 0x03, 0x04, 0x12, mb, hi, lo])
        if self.mode == 303:
            reports = self._release_actuators(digits=digits)
        else:
            reports = self._drive_active_actuator(digits=digits)
        return [encode_extended_inbound(ans, digits=digits), *reports]

    def _handle_setpoint_write(
        self, b0: int, b2: int, b3: int, *, digits: int
    ) -> list[bytes]:
        self.setpoint_deci = b3 * 5
        if b0 == 0x90:
            self.mode = 1
            self.function = ((b2 & 0x03) + 1) if (b2 & 0x03) < 3 else 3
        elif b0 == 0x91:
            self.mode = 0
            self.function = 2
        else:
            # Generic function 3 (*#4*Z*#14*T*3##) keeps the season: on the
            # F461 2.0.11 bench a generic write after a cooling write left
            # the zone in cooling with no season event
            # (reports/data/live_scan_confirm_2026-10-26). It leaves OFF in
            # the manual mode of the underlying season (f461_live_2026-10-15
            # run3_climate t=394.846: *#4*2*#14*0200*3## in 303 over cooling
            # reported *4*0*2##).
            self.function = 3
            if self.mode == 303:
                self.mode = 0 if self._season == "cooling" else 1
        hi = (self.setpoint_deci >> 8) & 0xFF
        lo = self.setpoint_deci & 0xFF
        ans = bytes(
            [0xD2, self.config.bus_addr, 0x03, 0x04, 0x0E, hi, lo, self.function]
        )
        return [
            encode_extended_inbound(ans, digits=digits),
            *self._drive_active_actuator(digits=digits),
        ]

    def _drive_active_actuator(self, *, digits: int) -> list[bytes]:
        """Drive the active season's actuator and release the other season's.

        The demand is the setpoint / temperature gap: heating while the
        setpoint is above the measured temperature, cooling while it is below.
        The active output is actuator 1 in heating and actuator 2 in cooling;
        a binary valve (dimension 20 value below 5) switches to 1 / 0, a fan
        coil (5 and above) runs at the speed in force (6..8 for speeds 1..3, 9
        when the speed is automatic) or rests at 5. Either season may drive
        either kind of output, so a heating-side fan coil keeps its 5..9
        values and goes on answering dimension 11. The inactive season's
        output is released (valve 0, fan coil 5) first, so the two outputs
        are never on together: on the physical KW4691 of the F461 2.0.11
        bench (MyHOME fixture ``f461_live_2026-10-15``) the switch to heating
        ``*4*1*2##`` with the cooling fan coil 2#2 running released it to
        ``*#4*2#2*20*5##`` (``probe_followup`` t=80.437..82.905), and the
        switch to cooling ``*4*0*2##`` with the heating valve 2#1 on released
        it to ``*#4*2#1*20*0##`` / ``*#4*2*19*5*0##`` (``probe_fancoil``
        t=9.446..16.536). A changed actuator reports itself with one
        ``B7 <80+Z> 3<A> <state>`` telegram, the status form the gateway
        shows as ``*#4*Z#A*20*V##``, the release before the drive; the
        physical thermostat did so 1-2 s after the echo, the virtual plant
        answers at once.
        """
        if self.season == "cooling":
            active_id, inactive_id = 2, 1
            demand = self.setpoint_deci < self.temp_deci
        else:
            active_id, inactive_id = 1, 2
            demand = self.setpoint_deci > self.temp_deci
        if self.actuators.get(active_id, self._active_side()) >= 5:
            speed = 5 + self.fan_speed if 1 <= self.fan_speed <= 3 else 9
            active_state = speed if demand else 5
        else:
            active_state = 1 if demand else 0
        inactive_off = 5 if self.actuators.get(inactive_id, 0) >= 5 else 0
        return [
            *self._set_actuator(inactive_id, inactive_off, digits=digits),
            *self._set_actuator(active_id, active_state, digits=digits),
        ]

    def _set_actuator(self, act_id: int, state: int, *, digits: int) -> list[bytes]:
        """Move actuator ``act_id`` to ``state``; report it when it changed."""
        if act_id not in self.actuators or self.actuators[act_id] == state:
            return []
        self.actuators[act_id] = state
        if act_id == 2:
            self.valve_cooling = state
        else:
            self.valve_heating = state
        return [self.actuator_frame(act_id, digits=digits)]

    def _release_actuators(self, *, digits: int) -> list[bytes]:
        """Switch every actuator off (valve 0, fan coil 5) for the OFF mode."""
        reports: list[bytes] = []
        for act_id, state in sorted(self.actuators.items()):
            off = 5 if state >= 5 else 0
            reports += self._set_actuator(act_id, off, digits=digits)
        return reports

    def _handle_zone_query(self, probe: int, b3: int, *, digits: int) -> list[bytes]:
        if b3 == 0x00:
            if probe == 0 or probe in self.config.probes:
                hdr = 0xB5 if self.temp_deci >= 256 else 0xB4
                return [
                    encode_short_inbound(
                        hdr,
                        self.config.bus_addr,
                        0x30 | probe,
                        self.temp_deci & 0xFF,
                        digits=digits,
                    )
                ]
            return []
        if probe != 0:
            return []
        hi = (self.setpoint_deci >> 8) & 0xFF
        lo = self.setpoint_deci & 0xFF
        addr = self.config.bus_addr
        fs = self.fan_speed & 0xFF
        replies: dict[int, bytes] = {
            0x12: bytes([0xD2, addr, 0x03, 0x04, 0x12, self._mode_byte(), hi, lo]),
            0x0E: bytes([0xD2, addr, 0x03, 0x04, 0x0E, hi, lo, self.function]),
            0x0D: bytes([0xD2, addr, 0x03, 0x04, 0x0D, 0x00, 0x00, 0x00]),
            # Valve status (dimension 19): the heating then the cooling output,
            # each as the actuator status byte; the F461 2.0.11 reports them
            # as *#4*Z*19*<cooling>*<heating>## (01 20 -> 19*5*0).
            0x13: bytes(
                [
                    0xD2,
                    addr,
                    0x03,
                    0x04,
                    0x13,
                    THERMO_ACTUATOR_STATUS_BYTES.get(self.valve_heating, 0x01),
                    THERMO_ACTUATOR_STATUS_BYTES.get(self.valve_cooling, 0x01),
                    0x00,
                ]
            ),
        }
        # Fan speed (dimension 11): answered only when the active season's
        # output is a fan coil (side value >= 5). A valve side leaves the
        # request unanswered, so the gateway NACKs on its bus timeout, as the
        # physical KW4691 does in heating season (2.1 s on the F461 2.0.11).
        if self._active_side() >= 5:
            replies[0x0B] = bytes([0xD2, addr, 0x03, 0x04, 0x0B, fs, 0x00, 0x00])
        if b3 in replies:
            return [encode_extended_inbound(replies[b3], digits=digits)]
        return []

    def handle_extended(self, payload: bytes, *, digits: int) -> list[bytes]:
        """Handle an 8-byte D1 WHO=4 command or query (D2 00 for the general form)."""
        addr = self.config.bus_addr
        # Humidity read (dimension 60): D1 <Z> 03 03 08 00 00 00 for *#4*Z*60##,
        # D2 00 03 03 08 00 00 00 for *#4*0*60##. Only a zone with a humidity
        # probe answers, D2 <Z> 03 04 08 00 00 <humidity>; the others stay
        # silent, and the harness answers the unanswered extended read with an
        # immediate $000 (no TX-ACK, oracle/bus.py), which the gateway reports
        # as a NACK for *#4*Z*60##. The physical bus timeout is not emulated
        # for extended reads, and no fixture has a probe-less zone yet.
        if (
            payload[2:] == b"\x03\x03\x08\x00\x00\x00"
            and (payload[0], payload[1]) in ((0xD1, addr), (0xD2, 0x00))
            and self.humidity is not None
        ):
            return [
                encode_extended_inbound(
                    bytes([0xD2, addr, 0x03, 0x04, 0x08, 0x00, 0x00, self.humidity]),
                    digits=digits,
                )
            ]
        if (
            payload[0] == 0xD1
            and payload[1] == self.config.bus_addr
            and payload[2] == 0x03
            and payload[3] == 0x02
            and payload[4] == 0x0B
        ):
            # Fan speed write (*#4*Z*#11*S##): a fan coil that is running
            # follows the new speed (dimension 20 value 5 + S, 9 for
            # automatic) and reports itself, so dimensions 11 and 20 agree;
            # a valve side is not touched, and a zone that is OFF only keeps
            # the speed for later (f461_live_2026-10-15 run3_climate
            # t=324.972..336.783: *#4*2*#11*1..3,0## in 303 were echoed and
            # persisted with 2#2 resting at 20*5).
            self.fan_speed = payload[5]
            fan_reports = (
                self._drive_active_actuator(digits=digits)
                if self._active_side() >= 5 and self.mode != 303
                else []
            )
            return [
                encode_extended_inbound(
                    bytes(
                        [
                            0xD2,
                            self.config.bus_addr,
                            0x03,
                            0x04,
                            0x0B,
                            self.fan_speed,
                            0x00,
                            0x00,
                        ]
                    ),
                    digits=digits,
                ),
                *fan_reports,
            ]
        return []


class EnergyMeterDevice:
    """Clean-room F520 energy management meter (WHO=18, 5N -> A1..A9)."""

    def __init__(self, config: EnergyMeterConfig) -> None:
        self.config = config
        self.active_power_w: int = config.active_power_w
        self.total_energy_wh: int = config.total_energy_wh
        self.current_month_wh: int = config.current_month_wh
        self.partial_energy_wh: int = config.partial_energy_wh

    def reset(self) -> None:
        """Reset meter readings to initial fixture values."""
        self.active_power_w = self.config.active_power_w
        self.total_energy_wh = self.config.total_energy_wh
        self.current_month_wh = self.config.current_month_wh
        self.partial_energy_wh = self.config.partial_energy_wh

    def handle_extended(self, payload: bytes, *, digits: int) -> list[bytes]:
        """Handle an 8-byte D1 WHO=18 query or command."""
        addr = self.config.bus_addr
        if payload[0] != 0xD1 or payload[1] != addr or payload[2] != 0x02:
            return []
        op, p4, p5 = payload[3], payload[4], payload[5]
        if (op == 0x33 and p4 == 0x00 and p5 == 0x1D) or (
            op == 0x32 and p4 == 0x00 and p5 == 0x02 and payload[6] == 0x1D
        ):
            # *#18*5N*113## active power query or *#18*5N*#1200#1*<val>## toggle
            w = self.active_power_w & 0xFFFF
            ans = bytes([0xD2, addr, 0x02, 0x34, 0x00, 0x1D, (w >> 8) & 0xFF, w & 0xFF])
            return [encode_extended_inbound(ans, digits=digits)]
        if op == 0x33 and p4 == 0x50 and p5 in (0x15, 0x16, 0x17, 0x18):
            val = (
                self.total_energy_wh
                if p5 == 0x15
                else (self.current_month_wh if p5 == 0x17 else self.partial_energy_wh)
            )
            f1 = bytes([0xD2, addr, 0x02, 0x35, 0x81, 0x50, p5, 0x00])
            f2 = bytes(
                [
                    0xD2,
                    addr,
                    0x02,
                    0x35,
                    0xA0,
                    (val >> 16) & 0xFF,
                    (val >> 8) & 0xFF,
                    val & 0xFF,
                ]
            )
            return [
                encode_extended_inbound(f1, digits=digits),
                encode_extended_inbound(f2, digits=digits),
            ]
        return []


class EnergyActuatorDevice:
    """Clean-room F522/F523 energy actuator (WHO=18, 7N#0 -> B1..B9)."""

    def __init__(self, config: EnergyActuatorConfig) -> None:
        self.config = config
        self.state: int = config.initial_state
        self.active_power_w: int = config.active_power_w
        self.total_energy_wh: int = config.total_energy_wh

    def reset(self) -> None:
        """Reset energy actuator state to initial fixture values."""
        self.state = self.config.initial_state
        self.active_power_w = self.config.active_power_w
        self.total_energy_wh = self.config.total_energy_wh

    def handle_extended(self, payload: bytes, *, digits: int) -> list[bytes]:
        """Handle an 8-byte D1 WHO=18 query or command on 7N#0."""
        addr = self.config.bus_addr
        if payload[0] != 0xD1 or payload[1] != addr or payload[2] != 0x12:
            return []
        op, p4, p5 = payload[3], payload[4], payload[5]
        if op == 0x33 and p4 == 0x00 and p5 == 0x1D:
            w = self.active_power_w & 0xFFFF
            ans = bytes([0xD2, addr, 0x12, 0x34, 0x00, 0x1D, (w >> 8) & 0xFF, w & 0xFF])
            return [encode_extended_inbound(ans, digits=digits)]
        if op == 0x33 and p4 == 0x70 and p5 == 0x05:
            ans = bytes([0xD2, addr, 0x12, 0x34, 0x70, 0x05, 0x00, self.state & 0xFF])
            return [encode_extended_inbound(ans, digits=digits)]
        if op == 0x31 and p4 == 0x70 and p5 in (0x00, 0x01):
            self.state = p5
            ans = bytes([0xD2, addr, 0x12, 0x34, 0x70, 0x05, 0x00, self.state & 0xFF])
            return [encode_extended_inbound(ans, digits=digits)]
        if op == 0x33 and p4 == 0x50 and p5 == 0x15:
            val = self.total_energy_wh
            f1 = bytes([0xD2, addr, 0x12, 0x35, 0x81, 0x50, 0x15, 0x00])
            f2 = bytes(
                [
                    0xD2,
                    addr,
                    0x12,
                    0x35,
                    0xA0,
                    (val >> 16) & 0xFF,
                    (val >> 8) & 0xFF,
                    val & 0xFF,
                ]
            )
            return [
                encode_extended_inbound(f1, digits=digits),
                encode_extended_inbound(f2, digits=digits),
            ]
        return []


class ScenarioDevice:
    """Clean-room CEN (WHO=15) / CEN+ (WHO=25) scenario button module."""

    def __init__(self, config: ScenarioConfig) -> None:
        self.config = config

    def reset(self) -> None:
        """No mutable state for scenario pushbuttons."""


class FaultChannel:
    """Clean-room WHO=1 / WHO=1001 autodiagnostic fault channel (WHAT=19 + DIM 11)."""

    def __init__(self, bus_addr: int, mask_bytes: bytes = DEFAULT_FAULT_MASK) -> None:
        self.bus_addr = bus_addr
        self.mask_bytes = mask_bytes

    def handle_short(self, b1: int, b2: int, b3: int, *, digits: int) -> list[bytes]:
        """Respond to status query 00 15 00 with B8 <addr> 12 0E + D2 <addr> 01 04."""
        if b1 == 0x00 and b2 == 0x15 and b3 == 0x00:
            short_ans = encode_short_inbound(
                0xB8, self.bus_addr, 0x12, 0x0E, digits=digits
            )
            ext_ans = encode_extended_inbound(
                bytes([0xD2, self.bus_addr, 0x01, 0x04, *self.mask_bytes[:4]]),
                digits=digits,
            )
            return [short_ans, ext_ans]
        return []


RelayChannel = LightDevice
DimmerChannel = DimmerDevice
TimedShutter = CoverDevice
ThermoZone = ThermoZoneDevice
EnergyMeter = EnergyMeterDevice
EnergyActuator = EnergyActuatorDevice
