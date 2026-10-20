# SPDX-License-Identifier: Apache-2.0
"""Clean-room PlantResponder bridging oracle/bus.py and PlantTopology."""

from __future__ import annotations

from pathlib import Path

from oracle.plant.scs_codec import (
    decode_gateway_frame,
    encode_extended_inbound,
    encode_short_inbound,
)
from oracle.plant.topology import PlantTopology


class PlantResponder:
    """Stateful virtual plant responder implementing oracle/bus.py Responder."""

    def __init__(self, topology: PlantTopology, *, version: str | None = None) -> None:
        self.topology = topology
        self.version = version
        self.name = f"plant:{topology.name}"
        self._now_ms: float = 0.0

    @classmethod
    def from_fixture(
        cls, fixture: str, *, version: str | None = None
    ) -> PlantResponder:
        """Instantiate a PlantResponder from a named YAML fixture or path."""
        return cls(PlantTopology.from_fixture(fixture), version=version)

    @classmethod
    def from_yaml(cls, path: Path, *, version: str | None = None) -> PlantResponder:
        """Instantiate a PlantResponder from a YAML file path."""
        return cls(PlantTopology.from_yaml(path), version=version)

    def reset(self) -> None:
        """Reset all virtual plant devices and internal simulation clock."""
        self._now_ms = 0.0
        self.topology.reset()

    def tick(self, now_ms: float) -> list[bytes]:
        """Advance simulation clock and return any spontaneous end-of-travel frames."""
        self._now_ms = now_ms
        digits = 3 if self.topology.config.gateway_product in ("F461", "F460") else 2
        out: list[bytes] = []
        for cover in self.topology.covers.values():
            out.extend(cover.tick(now_ms, digits=digits))
        return out

    def has_p2p_device(self, frame: bytes) -> bool:
        """Return True if a physical device is wired at the P2P address of frame."""
        decoded = decode_gateway_frame(frame)
        if decoded is None or len(decoded.payload) != 4:
            return False
        b0 = decoded.payload[0]
        subbus = decoded.subbus
        return (
            any(
                d.config.bus_addr == b0 and d.config.subbus == subbus
                for d in self.topology.lights.values()
            )
            or any(
                d.config.bus_addr == b0 and d.config.subbus == subbus
                for d in self.topology.dimmers.values()
            )
            or any(
                d.config.bus_addr == b0 and d.config.subbus == subbus
                for d in self.topology.covers.values()
            )
            or (subbus == 0 and b0 in self.topology.fault_channels)
        )

    def respond(self, frame: bytes) -> list[bytes]:
        """Decode an outbound gateway frame and return inbound device reply frames."""
        decoded = decode_gateway_frame(frame)
        if decoded is None:
            return []

        digits = decoded.digits
        payload = decoded.payload
        subbus = decoded.subbus

        if len(payload) == 8 and payload[0] == 0xEC:
            subbus = payload[1]
            inner4 = payload[4:8]
            return self._handle_short(inner4, digits=digits, subbus=subbus)

        if len(payload) == 4:
            return self._handle_short(payload, digits=digits, subbus=subbus)

        if len(payload) == 8 and payload[0] in (0xD1, 0xD5):
            return self._handle_extended(payload, digits=digits, subbus=subbus)

        return []

    def _handle_short(self, payload: bytes, *, digits: int, subbus: int) -> list[bytes]:
        b0, b1, b2, b3 = payload[0], payload[1], payload[2], payload[3]

        # WHO=4 short telegrams
        if b0 in (0x90, 0x91, 0x92, 0x93, 0x99, 0xA9, 0xB4) and (b2 & 0xF0) == 0x30:
            out: list[bytes] = []
            for zone in self.topology.thermo_zones.values():
                out.extend(zone.handle_short(payload, digits=digits))
            return out

        # General / Area / Group broadcasts
        if b0 in (0xB1, 0xB3, 0xB5):
            return self._handle_broadcast(b0, b1, b2, b3, digits=digits, subbus=subbus)

        # Fault diagnostic channel
        if subbus == 0 and b0 in self.topology.fault_channels:
            return self.topology.fault_channels[b0].handle_short(
                b1, b2, b3, digits=digits
            )

        return self._handle_p2p_short(b0, b1, b2, b3, digits=digits, subbus=subbus)

    def _emit_group_Aggregate_if_changed(
        self, grp: int, subbus: int, was_on: bool, *, digits: int
    ) -> list[bytes]:
        if grp <= 0 or subbus != 0:
            return []
        now_on = self.topology.group_any_on(grp, subbus)
        if now_on == was_on:
            return []
        return [
            encode_extended_inbound(
                bytes(
                    [
                        0xD5,
                        grp,
                        0x01,
                        0x34,
                        0x07,
                        0x00,
                        0x00,
                        0x00 if now_on else 0x01,
                    ]
                ),
                digits=digits,
            )
        ]

    def _handle_p2p_short(
        self,
        b0: int,
        b1: int,
        b2: int,
        b3: int,
        *,
        digits: int,
        subbus: int,
    ) -> list[bytes]:
        out_frames: list[bytes] = []
        for light in self.topology.lights.values():
            if light.config.bus_addr == b0 and light.config.subbus == subbus:
                grp = light.config.group
                was_on = self.topology.group_any_on(grp, subbus) if grp > 0 else False
                out_frames.extend(light.handle_short(b1, b2, b3, digits=digits))
                if b2 == 0x12 and b3 not in (0x04, 0x86):
                    out_frames.extend(
                        self._emit_group_Aggregate_if_changed(
                            grp, subbus, was_on, digits=digits
                        )
                    )

        for dimmer in self.topology.dimmers.values():
            if dimmer.config.bus_addr == b0 and dimmer.config.subbus == subbus:
                grp = dimmer.config.group
                was_on = self.topology.group_any_on(grp, subbus) if grp > 0 else False
                out_frames.extend(dimmer.handle_short(b1, b2, b3, digits=digits))
                if b2 == 0x12:
                    out_frames.extend(
                        self._emit_group_Aggregate_if_changed(
                            grp, subbus, was_on, digits=digits
                        )
                    )

        for cover in self.topology.covers.values():
            if cover.config.bus_addr == b0 and cover.config.subbus == subbus:
                out_frames.extend(
                    cover.handle_short(b1, b2, b3, digits=digits, now_ms=self._now_ms)
                )

        return out_frames

    def _handle_broadcast(
        self,
        b0: int,
        b1: int,
        b2: int,
        b3: int,
        *,
        digits: int,
        subbus: int,
    ) -> list[bytes]:
        if b2 == 0x15 and b3 == 0x00:
            return self._handle_broadcast_poll(b0, b1, digits=digits, subbus=subbus)
        if b0 == 0xB5 and b2 == 0x12 and b3 in (0x00, 0x01):
            return self._handle_group_cmd(b1, b3, digits=digits, subbus=subbus)
        if b0 == 0xB1 and b2 == 0x12 and b3 in (0x00, 0x01):
            return self._handle_general_cmd(b3, digits=digits, subbus=subbus)
        if b0 == 0xB3 and b2 == 0x12:
            return self._handle_area_cmd(b1, b3, digits=digits, subbus=subbus)
        return []

    @staticmethod
    def _matches_scope(b0: int, b1: int, area: int, group: int) -> bool:
        return b0 == 0xB1 or (b0 == 0xB3 and area == b1) or (b0 == 0xB5 and group == b1)

    def _handle_broadcast_poll(
        self, b0: int, b1: int, *, digits: int, subbus: int
    ) -> list[bytes]:
        out: list[bytes] = []
        for light in self.topology.lights.values():
            if light.config.subbus == subbus and self._matches_scope(
                b0, b1, light.config.area, light.config.group
            ):
                out.append(light.status_frame(digits=digits))
        for dimmer in self.topology.dimmers.values():
            if dimmer.config.subbus == subbus and self._matches_scope(
                b0, b1, dimmer.config.area, dimmer.config.group
            ):
                out.append(dimmer.status_frame(digits=digits))
        for cover in self.topology.covers.values():
            if cover.config.subbus == subbus and self._matches_scope(
                b0, b1, cover.config.area, cover.config.group
            ):
                out.append(cover.status_frame(digits=digits))
        return out

    def _handle_group_cmd(
        self, b1: int, b3: int, *, digits: int, subbus: int
    ) -> list[bytes]:
        out: list[bytes] = []
        if digits == 3:
            out.append(
                encode_short_inbound(0xB5, b1, 0x12, b3, digits=digits, subbus=subbus)
            )
        for light in self.topology.lights.values():
            if (
                light.config.subbus == subbus
                and light.config.group == b1
                and not light.locked
            ):
                was_on = self.topology.group_any_on(b1, subbus)
                light.what = 0 if b3 == 0x01 else 1
                out.append(light.status_frame(digits=digits))
                out.extend(
                    self._emit_group_Aggregate_if_changed(
                        b1, subbus, was_on, digits=digits
                    )
                )
        for dimmer in self.topology.dimmers.values():
            if (
                dimmer.config.subbus == subbus
                and dimmer.config.group == b1
                and not dimmer.locked
            ):
                was_on = self.topology.group_any_on(b1, subbus)
                dimmer.handle_short(0x00, 0x12, b3, digits=digits)
                out.append(dimmer.status_frame(digits=digits))
                out.extend(
                    self._emit_group_Aggregate_if_changed(
                        b1, subbus, was_on, digits=digits
                    )
                )
        return out

    def _handle_general_cmd(self, b3: int, *, digits: int, subbus: int) -> list[bytes]:
        out: list[bytes] = []
        if digits == 3:
            out.append(
                encode_short_inbound(0xB1, 0x00, 0x12, b3, digits=digits, subbus=subbus)
            )
        groups_before: dict[int, bool] = {}
        for light in self.topology.lights.values():
            if light.config.subbus == subbus and light.config.group > 0:
                g = light.config.group
                groups_before.setdefault(g, self.topology.group_any_on(g, subbus))
        for light in self.topology.lights.values():
            if (
                light.config.subbus == subbus
                and not light.config.pul
                and not light.locked
            ):
                light.what = 0 if b3 == 0x01 else 1
        for dimmer in self.topology.dimmers.values():
            if (
                dimmer.config.subbus == subbus
                and not dimmer.config.pul
                and not dimmer.locked
            ):
                dimmer.handle_short(0x00, 0x12, b3, digits=digits)
        for g, was_on in sorted(groups_before.items()):
            out.extend(
                self._emit_group_Aggregate_if_changed(g, subbus, was_on, digits=digits)
            )
        return out

    def _handle_area_cmd(
        self, b1: int, b3: int, *, digits: int, subbus: int
    ) -> list[bytes]:
        # F422 sub-bus bridge blocks area cover movement commands
        if subbus > 0 and b3 in (0x08, 0x09, 0x0A):
            return []
        out: list[bytes] = []
        if b3 in (0x00, 0x01):
            if digits == 3:
                out.append(
                    encode_short_inbound(
                        0xB3, b1, 0x12, b3, digits=digits, subbus=subbus
                    )
                )
            for light in self.topology.lights.values():
                if (
                    light.config.subbus == subbus
                    and light.config.area == b1
                    and not light.config.pul
                    and not light.locked
                ):
                    light.what = 0 if b3 == 0x01 else 1
                    out.append(light.status_frame(digits=digits))
        return out

    def _handle_extended(
        self, payload: bytes, *, digits: int, subbus: int
    ) -> list[bytes]:
        addr, b2, op, sub = payload[1], payload[2], payload[3], payload[4]

        # WHO=1 Group status query/set (D5 <grp> 01 <32|33> 07 00 <subgrp> <val>)
        if payload[0] == 0xD5 and b2 == 0x01 and sub == 0x07 and payload[5] == 0x00:
            if op == 0x33:
                any_on = self.topology.group_any_on(addr, subbus)
                ans_bytes = bytes(
                    [
                        0xD5,
                        addr,
                        0x01,
                        0x34,
                        0x07,
                        0x00,
                        payload[6],
                        0x00 if any_on else 0x01,
                    ]
                )
                return [encode_extended_inbound(ans_bytes, digits=digits)]
            if op == 0x32:
                ans_bytes = bytes(
                    [0xD5, addr, 0x01, 0x34, 0x07, 0x00, payload[6], payload[7]]
                )
                return [encode_extended_inbound(ans_bytes, digits=digits)]

        # WHO=1 Dimmer extended commands/queries
        for dimmer in self.topology.dimmers.values():
            if dimmer.config.bus_addr == addr and dimmer.config.subbus == subbus:
                ans = dimmer.handle_extended(payload, digits=digits)
                if ans:
                    return ans

        # Unpopulated dimmer write (*#1*08*#1*150*0##) or WHO=2 DIM 10 (*#2*05*10##)
        if (op == 0x42 and sub == 0x0D and payload[5] == 0x0D) or (
            op == 0x43 and sub == 0x09 and payload[5] == 0x00
        ):
            echo = bytes([0xD2, *payload[1:]])
            return [encode_extended_inbound(echo, digits=digits)]

        # WHO=4 extended commands/queries
        if (b2 & 0x0F) == 0x03:
            for zone in self.topology.thermo_zones.values():
                ans = zone.handle_extended(payload, digits=digits)
                if ans:
                    return ans

        # WHO=18 Energy meters (b2 == 0x02) & actuators (b2 == 0x12)
        return self._handle_energy_extended(payload, addr=addr, b2=b2, digits=digits)

    def _handle_energy_extended(
        self, payload: bytes, *, addr: int, b2: int, digits: int
    ) -> list[bytes]:
        if b2 == 0x02:
            for meter in self.topology.energy_meters.values():
                if meter.config.bus_addr == addr:
                    return meter.handle_extended(payload, digits=digits)
        if b2 == 0x12:
            for act in self.topology.energy_actuators.values():
                if act.config.bus_addr == addr:
                    return act.handle_extended(payload, digits=digits)
        return []
