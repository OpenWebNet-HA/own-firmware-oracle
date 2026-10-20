# SPDX-License-Identifier: Apache-2.0
"""Fast deterministic OpenWebNet plant engine for Tier B trace replay."""

from __future__ import annotations

from typing import Any

from oracle.plant.models import Exchange
from oracle.plant.topology import PlantTopology


class OwnPlantEngine:
    """Evaluates OpenWebNet frames against a PlantTopology and gateway rules."""

    def __init__(
        self,
        topology: PlantTopology | Any,
        *,
        gateway_model: str | None = None,
        oracle_index: dict[str, Any] | None = None,
    ) -> None:
        self.topology: PlantTopology = (
            topology if isinstance(topology, PlantTopology) else PlantTopology(topology)
        )
        self.gateway_model = gateway_model or self.topology.config.gateway_product
        self.oracle_index = oracle_index or {}
        self._now_ms: float = 0.0
        self.bt_termo_crashed: bool = False

    def reset(self) -> None:
        """Reset plant state, simulation clock, and daemon crash state."""
        self._now_ms = 0.0
        self.bt_termo_crashed = False
        self.topology.reset()

    def tick(self, now_ms: float) -> list[str]:
        """Advance simulation clock and return spontaneous OpenWebNet event frames."""
        self._now_ms = now_ms
        events: list[str] = []
        for cover in self.topology.covers.values():
            if cover.move_until_ms is not None and now_ms >= cover.move_until_ms:
                cover.what = 0
                cover.move_until_ms = None
                events.append(f"*2*0*{cover.config.where}##")
        return events

    def step(self, tx: str, *, now_ms: float | None = None) -> Exchange:
        """Execute a single OpenWebNet command or query frame and return an Exchange."""
        if now_ms is not None:
            self._now_ms = now_ms

        if (
            not tx.startswith("*")
            or not tx.endswith("##")
            or tx in ("*#*1##", "*#*0##")
        ):
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")

        body = tx[1:-2]
        if body.startswith("#"):
            return self._step_status_or_dim(tx, body[1:])
        return self._step_normal(tx, body)

    def _step_normal(self, tx: str, body: str) -> Exchange:
        parts = body.split("*")
        if len(parts) < 2:
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")
        who_str = parts[0]
        what_str = parts[1]
        where_str = parts[2] if len(parts) > 2 else ""

        if who_str == "1":
            return self._step_who1_action(tx, what_str, where_str)
        if who_str == "2":
            return self._step_who2_action(tx, what_str, where_str)
        if who_str == "4":
            return self._step_who4_action(tx, what_str, where_str)
        if who_str == "14":
            return self._step_who14_action(tx, what_str, where_str)
        return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")

    def _step_status_or_dim(self, tx: str, body: str) -> Exchange:
        parts = body.split("*")
        if len(parts) < 2:
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")
        who_str = parts[0]
        where_str = parts[1]
        rest = parts[2:]

        if who_str == "13":
            return self._step_who13(tx, where_str, rest)
        if who_str == "1":
            return self._step_who1_query_or_dim(tx, where_str, rest)
        if who_str == "2":
            return self._step_who2_query_or_dim(tx, where_str, rest)
        if who_str == "4":
            return self._step_who4_query_or_dim(tx, where_str, rest)
        if who_str == "18":
            return self._step_who18_query_or_dim(tx, where_str, rest)
        return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")

    def _step_who13(self, tx: str, where_str: str, rest: list[str]) -> Exchange:
        if where_str != "" or not rest:
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")
        dim = rest[0]
        if dim.startswith("#"):
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*1##")
        table = {
            "0": "*#13**0*22*51*00*999##",
            "1": "*#13**1*00*18*10*2026##",
            "10": "*#13**10*192*0*2*92##",
            "11": "*#13**11*255*255*255*0##",
            "12": "*#13**12*0*3*80*0*4*97##",
            "15": "*#13**15*200##",
            "16": "*#13**16*2*0*11##",
            "17": "*#13**17*4*2*0##",
            "19": "*#13**19*0*8*33*56##",
            "22": "*#13**22*22*51*02*999*00*18*10*2026##",
            "23": "*#13**23*5*10*35##",
            "24": "*#13**24*2*26*4##",
        }
        if dim == "0" and len(rest) > 1:
            ans = "*#13**0*22*51*02*999##"
            return Exchange(
                tx=tx, replies=(ans,), ack_or_nack="*#*1##", events_after=(ans,)
            )
        if dim in table:
            ans = table[dim]
            return Exchange(
                tx=tx, replies=(ans,), ack_or_nack="*#*1##", events_after=(ans,)
            )
        return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")

    def _step_who1_general(self, tx: str, what_str: str, what_base: int) -> Exchange:
        events: list[str] = [f"*1*{what_str}*0##"]
        groups_before = {
            light.config.group: self.topology.group_any_on(light.config.group, 0)
            for light in self.topology.lights.values()
            if light.config.subbus == 0 and light.config.group > 0
        }
        for light in self.topology.lights.values():
            if light.config.subbus == 0 and not light.config.pul and not light.locked:
                light.what = 0 if what_base == 0 else 1
        for g, was_on in sorted(groups_before.items()):
            now_on = self.topology.group_any_on(g, 0)
            if now_on != was_on:
                events.append(f"*#1*#{g}*15*{1 if now_on else 0}##")
        return Exchange(
            tx=tx,
            replies=(),
            ack_or_nack="*#*1##",
            events_after=tuple(events),
        )

    def _step_who1_group(
        self, tx: str, what_str: str, what_base: int, grp: int
    ) -> Exchange:
        events = [f"*1*{what_str}*#{grp}##"]
        for light in self.topology.lights.values():
            if (
                light.config.subbus == 0
                and light.config.group == grp
                and not light.locked
            ):
                was_on = self.topology.group_any_on(grp, 0)
                light.what = 0 if what_base == 0 else 1
                events.append(f"*1*{light.what}*{light.config.where}##")
                now_on = self.topology.group_any_on(grp, 0)
                if now_on != was_on:
                    events.append(f"*#1*#{grp}*15*{1 if now_on else 0}##")
        return Exchange(
            tx=tx,
            replies=(),
            ack_or_nack="*#*1##",
            events_after=tuple(events),
        )

    def _step_who1_light(self, tx: str, what_base: int, where_str: str) -> Exchange:
        light = self.topology.lights[where_str]
        if light.locked or what_base in (30, 31):
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*1##", events_after=())
        grp = light.config.group
        sub = light.config.subbus
        was_on = self.topology.group_any_on(grp, sub) if grp > 0 else False
        if what_base == 18:
            light.what = 1
            ev_list = [f"*1*1*{where_str}##"]
            if grp > 0 and sub == 0 and not was_on:
                ev_list.append(f"*#1*#{grp}*15*1##")
            light.what = 0
            ev_list.append(f"*1*0*{where_str}##")
            if grp > 0 and sub == 0 and not self.topology.group_any_on(grp, sub):
                ev_list.append(f"*#1*#{grp}*15*0##")
            return Exchange(
                tx=tx,
                replies=(),
                ack_or_nack="*#*1##",
                events_after=tuple(ev_list),
            )
        new_what = 0 if what_base == 0 else 1
        light.what = new_what
        ev_list = [f"*1*{new_what}*{where_str}##"]
        if grp > 0 and sub == 0:
            now_on = self.topology.group_any_on(grp, sub)
            if now_on != was_on:
                ev_list.append(f"*#1*#{grp}*15*{1 if now_on else 0}##")
        return Exchange(
            tx=tx,
            replies=(),
            ack_or_nack="*#*1##",
            events_after=tuple(ev_list),
        )

    def _step_who1_action(self, tx: str, what_str: str, where_str: str) -> Exchange:
        what_base_str = what_str.split("#", maxsplit=1)[0]
        if not what_base_str.isdigit():
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")
        what_base = int(what_base_str)

        if where_str == "0":
            return self._step_who1_general(tx, what_str, what_base)

        if (
            where_str in ("00", "1", "2", "3", "4", "5", "6", "7", "8", "9")
            or "#4#" in where_str
        ) and (
            where_str not in self.topology.lights
            and where_str not in self.topology.dimmers
        ):
            ev_area = (
                (f"*1*{what_str}*{where_str}##",) if "#4#" not in where_str else ()
            )
            return Exchange(
                tx=tx, replies=(), ack_or_nack="*#*1##", events_after=ev_area
            )

        if where_str.startswith("#") and where_str[1:].isdigit():
            return self._step_who1_group(tx, what_str, what_base, int(where_str[1:]))

        if where_str in self.topology.lights:
            return self._step_who1_light(tx, what_base, where_str)

        if where_str in self.topology.dimmers:
            dimmer = self.topology.dimmers[where_str]
            if dimmer.locked:
                return Exchange(
                    tx=tx, replies=(), ack_or_nack="*#*1##", events_after=()
                )
            if what_base == 0:
                dimmer.what = 0
                dimmer.dim1_level = 100
                dimmer.dim1_speed = 2
            elif 1 <= what_base <= 10:
                dimmer.what = what_base
                dimmer.dim1_level = dimmer.curve.get(what_base, 200)
                dimmer.dim1_speed = 2
            ev_frame = f"*1*{dimmer.what}*{where_str}##"
            return Exchange(
                tx=tx,
                replies=(),
                ack_or_nack="*#*1##",
                events_after=(ev_frame,),
            )

        ack = "*#*0##" if self.gateway_model in ("F461", "F460") else "*#*1##"
        return Exchange(tx=tx, replies=(), ack_or_nack=ack)

    def _step_who1_status_query(self, tx: str, where_str: str) -> Exchange:
        if where_str in ("0", "00"):
            rep = tuple(
                f"*1*{light.what}*{light.config.where}##"
                for light in self.topology.lights.values()
                if light.config.subbus == 0
            )
            evt_list = list(rep)
            if where_str == "0":
                for cover in self.topology.covers.values():
                    if cover.config.subbus == 0:
                        evt_list.insert(
                            1 if len(evt_list) >= 1 else 0,
                            f"*2*{cover.what}*{cover.config.where}##",
                        )
            return Exchange(
                tx=tx,
                replies=rep,
                ack_or_nack="*#*1##",
                events_after=tuple(evt_list),
            )
        if where_str in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*1##", events_after=())
        if where_str.startswith("#") and where_str[1:].isdigit():
            grp = int(where_str[1:])
            rep = tuple(
                f"*1*{light.what}*{light.config.where}##"
                for light in self.topology.lights.values()
                if light.config.subbus == 0 and light.config.group == grp
            )
            return Exchange(tx=tx, replies=rep, ack_or_nack="*#*1##", events_after=rep)
        if (
            len(where_str) == 2
            and where_str.isdigit()
            and int(where_str, 16) in self.topology.fault_channels
        ):
            f1 = f"*1*19*{where_str}##"
            f2 = f"*#1001*{where_str}*11*111110111111111111110111##"
            return Exchange(
                tx=tx,
                replies=(f1, f2),
                ack_or_nack="*#*1##",
                events_after=(f1, f2),
            )
        if where_str in self.topology.lights:
            st = f"*1*{self.topology.lights[where_str].what}*{where_str}##"
            return Exchange(
                tx=tx, replies=(st,), ack_or_nack="*#*1##", events_after=(st,)
            )
        if where_str in self.topology.dimmers:
            st = f"*1*{self.topology.dimmers[where_str].what}*{where_str}##"
            return Exchange(
                tx=tx, replies=(st,), ack_or_nack="*#*1##", events_after=(st,)
            )
        return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")

    def _step_who1_query_or_dim(
        self, tx: str, where_str: str, rest: list[str]
    ) -> Exchange:
        if not rest:
            return self._step_who1_status_query(tx, where_str)

        dim_code = rest[0]
        if dim_code == "1" and len(rest) == 1:
            if where_str in self.topology.dimmers:
                d = self.topology.dimmers[where_str]
                st = f"*#1*{where_str}*1*{d.dim1_level}*{d.dim1_speed}##"
                return Exchange(
                    tx=tx, replies=(st,), ack_or_nack="*#*1##", events_after=(st,)
                )
            if where_str in self.topology.lights:
                light = self.topology.lights[where_str]
                st = f"*1*{light.what}*{where_str}##"
                return Exchange(
                    tx=tx, replies=(st,), ack_or_nack="*#*1##", events_after=(st,)
                )
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")

        if dim_code == "2" and len(rest) == 1 and where_str in self.topology.lights:
            st = f"*#1*{where_str}*2*0*0*0##"
            return Exchange(
                tx=tx, replies=(st,), ack_or_nack="*#*1##", events_after=(st,)
            )

        if dim_code == "#2" and len(rest) == 4 and where_str in self.topology.lights:
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*1##", events_after=())

        if (
            (dim_code == "4" and len(rest) == 1)
            or (dim_code == "#4" and len(rest) >= 3)
        ) and self.gateway_model in ("MH200", "MH200N"):
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*1##", events_after=())

        if dim_code == "#1" and len(rest) >= 3:
            return self._step_who1_dim1_write(tx, where_str, rest)

        return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")

    def _step_who1_dim1_write(
        self, tx: str, where_str: str, rest: list[str]
    ) -> Exchange:
        if not (rest[1].isdigit() and rest[2].isdigit()):
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")
        lvl = int(rest[1])
        spd = int(rest[2])
        if not (101 <= lvl <= 200 and 0 <= spd <= 255):
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")
        if where_str in self.topology.dimmers:
            d = self.topology.dimmers[where_str]
            d.dim1_level = lvl
            d.what = d._level_to_what(lvl)
            rep_spd = 5 if spd == 0 else spd
            d.dim1_speed = 2 if spd == 0 else spd
            ev = f"*#1*{where_str}*1*{lvl}*{rep_spd}##"
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*1##", events_after=(ev,))
        return Exchange(tx=tx, replies=(), ack_or_nack="*#*1##", events_after=())

    def _step_who2_action(self, tx: str, what_str: str, where_str: str) -> Exchange:
        if "#4#" in where_str:
            base, _ = where_str.split("#4#", 1)
            if len(base) == 1 or base in ("0", "00", "10"):
                return Exchange(
                    tx=tx, replies=(), ack_or_nack="*#*1##", events_after=()
                )

        what_head = what_str.split("#", maxsplit=1)[0]
        what_num = int(what_head) if what_head.isdigit() else 0

        if where_str in self.topology.covers:
            cover = self.topology.covers[where_str]
            if self.gateway_model in ("MH200", "MH200N"):
                if not cover.locked:
                    cover.what = what_num
                    cover.move_until_ms = (
                        self._now_ms + float(cover.config.travel_time_ms)
                        if what_num in (1, 2)
                        else None
                    )
                ev = f"*2*{cover.what}*{where_str}##"
                return Exchange(
                    tx=tx, replies=(), ack_or_nack="*#*1##", events_after=(ev,)
                )
            if cover.locked:
                ev_locked = (
                    (f"*2*1000#0*{where_str}##",)
                    if what_num == 0
                    else (
                        f"*2*1000#0*{where_str}##",
                        f"*2*1000#{what_num}*{where_str}##",
                    )
                )
                return Exchange(
                    tx=tx,
                    replies=(),
                    ack_or_nack="*#*1##",
                    events_after=ev_locked,
                )
            cover.what = what_num
            if what_num == 0:
                cover.move_until_ms = None
                ev_unlocked: tuple[str, ...] = (
                    f"*2*1000#0*{where_str}##",
                    f"*2*0*{where_str}##",
                )
            else:
                cover.move_until_ms = self._now_ms + float(cover.config.travel_time_ms)
                ev_unlocked = (
                    f"*2*1000#0*{where_str}##",
                    f"*2*0*{where_str}##",
                    f"*2*1000#{what_num}*{where_str}##",
                    f"*2*{what_num}*{where_str}##",
                )
            return Exchange(
                tx=tx,
                replies=(),
                ack_or_nack="*#*1##",
                events_after=ev_unlocked,
            )

        ev_unpop = (
            (f"*2*1000#0*{where_str}##",)
            if what_num == 0
            else (
                f"*2*1000#0*{where_str}##",
                f"*2*1000#{what_num}*{where_str}##",
            )
        )
        return Exchange(
            tx=tx,
            replies=(),
            ack_or_nack="*#*0##",
            events_after=ev_unpop,
        )

    def _step_who2_query_or_dim(
        self, tx: str, where_str: str, rest: list[str]
    ) -> Exchange:
        if not rest:
            if where_str == "0":
                rep_list: list[str] = []
                evt_list: list[str] = []
                for cover in self.topology.covers.values():
                    if cover.config.subbus == 0:
                        st = f"*2*{cover.what}*{cover.config.where}##"
                        rep_list.extend([st, st])
                        evt_list.append(st)
                for light in self.topology.lights.values():
                    if light.config.subbus == 0:
                        evt_list.append(f"*1*{light.what}*{light.config.where}##")
                for cover in self.topology.covers.values():
                    if cover.config.subbus == 0:
                        evt_list.append(f"*2*{cover.what}*{cover.config.where}##")
                return Exchange(
                    tx=tx,
                    replies=tuple(rep_list),
                    ack_or_nack="*#*1##",
                    events_after=tuple(evt_list),
                )
            if "#4#" in where_str:
                base, sub_s = where_str.split("#4#", 1)
                if len(base) == 1 and base.isdigit() and sub_s.isdigit():
                    area = int(base)
                    sub = int(sub_s)
                    rep = tuple(
                        f"*2*{c.what}*{c.config.where}##"
                        for c in self.topology.covers.values()
                        if c.config.subbus == sub and c.config.area == area
                    )
                    return Exchange(
                        tx=tx,
                        replies=rep,
                        ack_or_nack="*#*1##",
                        events_after=rep,
                    )
            if where_str in self.topology.covers:
                c = self.topology.covers[where_str]
                st = f"*2*{c.what}*{where_str}##"
                return Exchange(
                    tx=tx,
                    replies=(st,),
                    ack_or_nack="*#*1##",
                    events_after=(st,),
                )
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")

        if rest == ["10"]:
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*1##", events_after=())
        return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")

    def _step_who14_action(self, tx: str, what_str: str, where_str: str) -> Exchange:
        if what_str not in ("0", "1"):
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")
        if "#4#" in where_str:
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*1##", events_after=())
        lock_flag = what_str == "0"
        if where_str in self.topology.lights:
            self.topology.lights[where_str].locked = lock_flag
            ev = f"*14*{what_str}*{where_str}##"
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*1##", events_after=(ev,))
        if where_str in self.topology.covers:
            c = self.topology.covers[where_str]
            c.locked = lock_flag
            if lock_flag:
                c.what = 0
                c.move_until_ms = None
                return Exchange(
                    tx=tx,
                    replies=(),
                    ack_or_nack="*#*1##",
                    events_after=(
                        f"*14*0*{where_str}##",
                        f"*2*0*{where_str}##",
                    ),
                )
            return Exchange(
                tx=tx,
                replies=(),
                ack_or_nack="*#*1##",
                events_after=(f"*14*1*{where_str}##",),
            )
        return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")

    def _step_who4_action(self, tx: str, what_str: str, where_str: str) -> Exchange:
        if self.bt_termo_crashed:
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")
        if where_str.startswith("#"):
            z_clean = where_str.lstrip("#").split("#", maxsplit=1)[0]
            ok = z_clean.isdigit() and (
                int(z_clean) == 0 or int(z_clean) in self.topology.thermo_zones
            )
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*1##" if ok else "*#*0##")

        if not where_str.isdigit() or int(where_str) not in self.topology.thermo_zones:
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")

        return self._apply_who4_zone_mode(tx, what_str, int(where_str))

    def _apply_who4_zone_mode(self, tx: str, what_str: str, z: int) -> Exchange:
        tz = self.topology.thermo_zones[z]
        if what_str == "311":
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")
        if what_str == "303":
            tz.mode = 303
        elif what_str == "302":
            if tz.mode != 303:
                tz.mode = 202
        elif what_str in ("0", "1"):
            tz.valve_heating = int(what_str)
            if 1 in tz.actuators:
                tz.actuators[1] = int(what_str)
            if tz.mode != 303:
                tz.mode = int(what_str)
        elif what_str.isdigit():
            tz.mode = int(what_str)
        elif what_str.startswith("312#"):
            arg = what_str.split("#", 1)[1]
            if arg.isdigit():
                tz.fan_speed = int(arg)
            elif not all(p.isdigit() for p in arg.split("#")):
                return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")
        else:
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")
        return Exchange(tx=tx, replies=(), ack_or_nack="*#*1##", events_after=())

    def _step_who4_query_or_dim(
        self, tx: str, where_str: str, rest: list[str]
    ) -> Exchange:
        if self.bt_termo_crashed:
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")

        if rest and rest[0] == "22":
            if self.gateway_model == "F461":
                self.bt_termo_crashed = True
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")

        if where_str.startswith("#"):
            if where_str == "#0" and rest == ["20"] and 2 in self.topology.thermo_zones:
                tz0 = self.topology.thermo_zones[2]
                rep0 = tuple(
                    f"*#4*2#{act_id}*20*{val}##"
                    for act_id, val in sorted(tz0.actuators.items())
                )
                return Exchange(
                    tx=tx, replies=rep0, ack_or_nack="*#*1##", events_after=rep0
                )
            z_clean = where_str[1:].split("#", maxsplit=1)[0]
            ok = (
                z_clean.isdigit()
                and bool(rest)
                and rest[0].startswith("#")
                and (int(z_clean) == 0 or int(z_clean) in self.topology.thermo_zones)
            )
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*1##" if ok else "*#*0##")

        if "#" in where_str:
            return self._step_who4_subaddress(tx, where_str, rest)

        if (
            not where_str.isdigit()
            or (bool(rest) and len(where_str) > 1 and where_str.startswith("0"))
            or not 1 <= int(where_str) <= 99
        ):
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")
        z = int(where_str)

        if not rest:
            return self._step_who4_zone_status(tx, z)

        dim = rest[0]
        if dim == "#0" and len(rest) >= 2 and rest[1].isdigit():
            if z in self.topology.thermo_zones:
                self.topology.thermo_zones[z].temp_deci = int(rest[1])
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*1##", events_after=())

        if z not in self.topology.thermo_zones:
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")

        return self._step_who4_zone_dim(tx, z, dim, rest)

    def _step_who4_subaddress(
        self, tx: str, where_str: str, rest: list[str]
    ) -> Exchange:
        z_part, a_part = where_str.split("#", 1)
        if (
            z_part.isdigit()
            and a_part.isdigit()
            and rest == ["20"]
            and int(z_part) in self.topology.thermo_zones
        ):
            tz = self.topology.thermo_zones[int(z_part)]
            act_id = int(a_part)
            if act_id in tz.actuators:
                st = f"*#4*{int(z_part)}#{act_id}*20*{tz.actuators[act_id]}##"
                return Exchange(
                    tx=tx, replies=(st,), ack_or_nack="*#*1##", events_after=(st,)
                )
        return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")

    def _step_who4_zone_status(self, tx: str, z: int) -> Exchange:
        if z not in self.topology.thermo_zones:
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")
        tz = self.topology.thermo_zones[z]
        dim12_sp = 350 if tz.mode == 202 else tz.setpoint_deci
        burst = (
            f"*#4*{z}*0*{tz.temp_deci:04d}##",
            f"*#4*{z}*12*{dim12_sp:04d}*{tz.function}##",
            f"*4*{tz.mode}*{z}##",
            f"*#4*{z}*14*{tz.setpoint_deci:04d}*{tz.function}##",
            f"*#4*{z}*13*{tz.offset}##",
        )
        return Exchange(tx=tx, replies=burst, ack_or_nack="*#*1##", events_after=burst)

    def _step_who4_zone_dim(
        self, tx: str, z: int, dim: str, rest: list[str]
    ) -> Exchange:
        tz = self.topology.thermo_zones[z]
        single_dims: dict[str, str] = {
            "0": f"*#4*{z}*0*{tz.temp_deci:04d}##",
            "2": f"*#4*{z}*2*0350##",
            "3": f"*#4*{z}*3*0070##",
            "4": f"*#4*{z}*4*0##",
            "6": f"*#4*{z}*6*0##",
            "13": f"*#4*{z}*13*{tz.offset}##",
            "13#1": f"*#4*{z}*13*{tz.offset}##",
            "14": f"*#4*{z}*14*{tz.setpoint_deci:04d}*{tz.function}##",
            "19": f"*#4*{z}*19*{tz.valve_cooling}*{tz.valve_heating}##",
            "60": f"*#4*{z}*60*34##",
        }
        if dim in single_dims:
            st = single_dims[dim]
            return Exchange(
                tx=tx, replies=(st,), ack_or_nack="*#*1##", events_after=(st,)
            )
        if dim == "12":
            dim12_sp = 350 if tz.mode == 202 else tz.setpoint_deci
            rep = (
                f"*#4*{z}*12*{dim12_sp:04d}*{tz.function}##",
                f"*4*{tz.mode}*{z}##",
            )
            return Exchange(tx=tx, replies=rep, ack_or_nack="*#*1##", events_after=rep)
        if dim == "20":
            ev = tuple(
                f"*#4*{z}#{act_id}*20*{val}##"
                for act_id, val in sorted(tz.actuators.items())
            )
            return Exchange(
                tx=tx, replies=ev[:1], ack_or_nack="*#*1##", events_after=ev
            )
        if dim == "11":
            if tz.mode == 1 or tz.valve_heating == 1:
                return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")
            st = f"*#4*{z}*11*{tz.fan_speed}##"
            return Exchange(
                tx=tx, replies=(st,), ack_or_nack="*#*1##", events_after=(st,)
            )
        if dim.startswith("#"):
            return self._step_who4_zone_dim_write(tx, z, dim, rest)
        return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")

    def _step_who4_zone_dim_write(
        self, tx: str, z: int, dim: str, rest: list[str]
    ) -> Exchange:
        tz = self.topology.thermo_zones[z]
        if dim == "#14" and len(rest) >= 3 and rest[1].isdigit() and rest[2].isdigit():
            tz.setpoint_deci = int(rest[1])
            m_val = int(rest[2])
            if m_val == 1:
                tz.mode = 1
                tz.valve_heating = 1
                tz.valve_cooling = 5
                tz.actuators[1] = 1
                tz.actuators[2] = 5
            elif m_val == 2:
                tz.mode = 0
                tz.valve_heating = 0
                tz.valve_cooling = 5
                tz.actuators[1] = 0
                tz.actuators[2] = 5
            elif m_val == 3:
                if tz.mode in (202, 303):
                    tz.mode = 0
                if tz.mode == 0:
                    vc = 8 if tz.setpoint_deci < 200 else 5
                    tz.valve_cooling = vc
                    tz.actuators[2] = vc
        elif dim == "#11" and len(rest) >= 2 and rest[1].isdigit():
            tz.fan_speed = int(rest[1])
        return Exchange(tx=tx, replies=(), ack_or_nack="*#*1##", events_after=())

    def _step_who18_query_or_dim(
        self, tx: str, where_str: str, rest: list[str]
    ) -> Exchange:
        if not rest:
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")
        dim = rest[0]
        if dim == "1200":
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")
        if (
            where_str in self.topology.energy_meters
            or where_str in self.topology.energy_actuators
        ) and dim in ("113", "51", "53", "54", "#1200#1"):
            return Exchange(tx=tx, replies=(), ack_or_nack="*#*1##", events_after=())
        return Exchange(tx=tx, replies=(), ack_or_nack="*#*0##")


OwnEngine = OwnPlantEngine
