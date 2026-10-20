# SPDX-License-Identifier: Apache-2.0
"""Clean-room plant topology loader (YAML fixtures, .plnt XML, and trace inference)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import yaml

from oracle.plant.devices import (
    DEFAULT_FAULT_MASK,
    CoverDevice,
    DimmerDevice,
    EnergyActuatorDevice,
    EnergyMeterDevice,
    FaultChannel,
    LightDevice,
    ScenarioDevice,
    ThermoZoneDevice,
)
from oracle.plant.models import (
    CoverConfig,
    DimmerConfig,
    EnergyActuatorConfig,
    EnergyMeterConfig,
    LightConfig,
    PlantTopologyConfig,
    ScenarioConfig,
    ThermoZoneConfig,
    parse_own_where,
)
from oracle.plant.scs_codec import where_to_bus_addr

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

_OWN_STATUS_RE = re.compile(r"^\*(\d+)\*([^*]+)\*([^#*]+(?:#[^#*]+)*)##$")
_OWN_DIM_RE = re.compile(r"^\*#(\d+)\*([^*]+)(?:\*([^#*]+(?:#[^#*]+)*))?(?:\*(.+))?##$")


def _int_val(val: Any, default: int = 0) -> int:
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        s = val.strip()
        if s.lower().startswith("0x"):
            return int(s, 16)
        if s.lstrip("-").isdigit():
            return int(s)
    return default


def _parse_yaml_lights(raw_lights: Any) -> dict[str, LightConfig]:
    lights: dict[str, LightConfig] = {}
    if isinstance(raw_lights, dict):
        for where_key, cfg in raw_lights.items():
            w = str(where_key)
            c = cfg if isinstance(cfg, dict) else {}
            default_addr, default_sub = where_to_bus_addr(w)
            bus_addr = _int_val(c.get("bus_addr"), default_addr)
            subbus = _int_val(c.get("subbus"), default_sub)
            lights[w] = LightConfig(
                where=w,
                bus_addr=bus_addr,
                subbus=subbus,
                initial_what=_int_val(c.get("initial_what"), 0),
                area=_int_val(c.get("area"), (bus_addr >> 4) & 0x0F),
                group=_int_val(c.get("group"), 0),
                pul=bool(c.get("pul", False)),
                initial_locked=bool(c.get("initial_locked", False)),
            )
    return lights


def _parse_yaml_dimmers(raw_dimmers: Any) -> dict[str, DimmerConfig]:
    dimmers: dict[str, DimmerConfig] = {}
    if isinstance(raw_dimmers, dict):
        for where_key, cfg in raw_dimmers.items():
            w = str(where_key)
            c = cfg if isinstance(cfg, dict) else {}
            default_addr, default_sub = where_to_bus_addr(w)
            bus_addr = _int_val(c.get("bus_addr"), default_addr)
            subbus = _int_val(c.get("subbus"), default_sub)
            disc_raw = c.get("discrete_levels") or {}
            disc = (
                {_int_val(k): _int_val(v) for k, v in disc_raw.items()}
                if isinstance(disc_raw, dict)
                else {}
            )
            dimmers[w] = DimmerConfig(
                where=w,
                bus_addr=bus_addr,
                model=str(c.get("model", "F414")),
                subbus=subbus,
                initial_what=_int_val(c.get("initial_what"), 10),
                initial_dim1_level=_int_val(c.get("initial_dim1_level"), 200),
                initial_dim1_speed=_int_val(c.get("initial_dim1_speed"), 2),
                area=_int_val(c.get("area"), (bus_addr >> 4) & 0x0F),
                group=_int_val(c.get("group"), 0),
                pul=bool(c.get("pul", False)),
                initial_locked=bool(c.get("initial_locked", False)),
                discrete_levels=disc,
            )
    return dimmers


def _parse_yaml_covers(raw_covers: Any) -> dict[str, CoverConfig]:
    covers: dict[str, CoverConfig] = {}
    if isinstance(raw_covers, dict):
        for where_key, cfg in raw_covers.items():
            w = str(where_key)
            c = cfg if isinstance(cfg, dict) else {}
            default_addr, default_sub = where_to_bus_addr(w)
            bus_addr = _int_val(c.get("bus_addr"), default_addr)
            subbus = _int_val(c.get("subbus"), default_sub)
            covers[w] = CoverConfig(
                where=w,
                bus_addr=bus_addr,
                subbus=subbus,
                advanced=bool(c.get("advanced", False)),
                travel_time_ms=_int_val(c.get("travel_time_ms"), 5000),
                initial_what=_int_val(c.get("initial_what"), 0),
                initial_level=_int_val(c.get("initial_level"), 50),
                area=_int_val(c.get("area"), (bus_addr >> 4) & 0x0F),
                group=_int_val(c.get("group"), 0),
                initial_locked=bool(c.get("initial_locked", False)),
            )
    return covers


def _parse_yaml_thermo(raw_thermo: Any) -> dict[int, ThermoZoneConfig]:
    thermo_zones: dict[int, ThermoZoneConfig] = {}
    if isinstance(raw_thermo, dict):
        for z_key, cfg in raw_thermo.items():
            z = _int_val(z_key)
            c = cfg if isinstance(cfg, dict) else {}
            bus_addr = _int_val(c.get("bus_addr"), z)
            act_raw = c.get("actuators") or {}
            act_map = (
                {_int_val(k): _int_val(v) for k, v in act_raw.items()}
                if isinstance(act_raw, dict)
                else {}
            )
            probes_raw = c.get("probes") or ()
            probes = (
                tuple(_int_val(p) for p in probes_raw)
                if isinstance(probes_raw, list)
                else ()
            )
            thermo_zones[z] = ThermoZoneConfig(
                zone=z,
                bus_addr=bus_addr,
                initial_temp_deci=_int_val(c.get("initial_temp_deci"), 249),
                initial_setpoint_deci=_int_val(c.get("initial_setpoint_deci"), 245),
                initial_mode=_int_val(c.get("initial_mode"), 1),
                initial_function=_int_val(c.get("initial_function"), 3),
                initial_offset=str(c.get("initial_offset", "00")),
                initial_valve_heating=_int_val(c.get("initial_valve_heating"), 0),
                initial_valve_cooling=_int_val(c.get("initial_valve_cooling"), 5),
                initial_actuator_status=_int_val(c.get("initial_actuator_status"), 0),
                actuators=act_map,
                initial_fan_speed=_int_val(c.get("initial_fan_speed"), 0),
                probes=probes,
            )
    return thermo_zones


def _parse_yaml_energy_meters(raw_meters: Any) -> dict[str, EnergyMeterConfig]:
    energy_meters: dict[str, EnergyMeterConfig] = {}
    if isinstance(raw_meters, dict):
        for where_key, cfg in raw_meters.items():
            w = str(where_key)
            c = cfg if isinstance(cfg, dict) else {}
            default_addr, _ = where_to_bus_addr(w, who=18)
            bus_addr = _int_val(c.get("bus_addr"), default_addr)
            energy_meters[w] = EnergyMeterConfig(
                where=w,
                bus_addr=bus_addr,
                active_power_w=_int_val(c.get("active_power_w"), 450),
                total_energy_wh=_int_val(c.get("total_energy_wh"), 123456),
                current_month_wh=_int_val(c.get("current_month_wh"), 12345),
                partial_energy_wh=_int_val(c.get("partial_energy_wh"), 6789),
            )
    return energy_meters


def _parse_yaml_energy_actuators(raw_actuators: Any) -> dict[str, EnergyActuatorConfig]:
    energy_actuators: dict[str, EnergyActuatorConfig] = {}
    if isinstance(raw_actuators, dict):
        for where_key, cfg in raw_actuators.items():
            w = str(where_key)
            c = cfg if isinstance(cfg, dict) else {}
            default_addr, _ = where_to_bus_addr(w)
            bus_addr = _int_val(c.get("bus_addr"), default_addr)
            energy_actuators[w] = EnergyActuatorConfig(
                where=w,
                bus_addr=bus_addr,
                initial_state=_int_val(c.get("initial_state"), 0),
                active_power_w=_int_val(c.get("active_power_w"), 0),
                total_energy_wh=_int_val(c.get("total_energy_wh"), 0),
            )
    return energy_actuators


def _parse_yaml_scenarios(raw_scenarios: Any) -> dict[str, ScenarioConfig]:
    scenarios: dict[str, ScenarioConfig] = {}
    if isinstance(raw_scenarios, dict):
        for where_key, cfg in raw_scenarios.items():
            w = str(where_key)
            c = cfg if isinstance(cfg, dict) else {}
            default_addr, _ = where_to_bus_addr(w)
            bus_addr = _int_val(c.get("bus_addr"), default_addr)
            btns_raw = c.get("buttons")
            btns = (
                tuple(_int_val(b) for b in btns_raw)
                if isinstance(btns_raw, list)
                else (1, 2)
            )
            scenarios[w] = ScenarioConfig(
                where=w,
                bus_addr=bus_addr,
                who=_int_val(c.get("who"), 15),
                buttons=btns,
            )
    return scenarios


def load_topology_yaml(path: Path) -> PlantTopologyConfig:
    """Load a PlantTopologyConfig from a YAML fixture file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected mapping at top of {path}")

    fault_raw = raw.get("fault_addresses") or []
    fault_addrs = (
        frozenset(_int_val(a) for a in fault_raw)
        if isinstance(fault_raw, list)
        else frozenset()
    )
    sub_raw = raw.get("subbus_interfaces") or []
    sub_ifs = (
        frozenset(_int_val(s) for s in sub_raw)
        if isinstance(sub_raw, list)
        else frozenset()
    )

    return PlantTopologyConfig(
        name=str(raw.get("name", path.stem)),
        gateway_product=str(raw.get("gateway_product", "F461")),
        gateway_version=str(raw.get("gateway_version", "020011")),
        lights=_parse_yaml_lights(raw.get("lights")),
        dimmers=_parse_yaml_dimmers(raw.get("dimmers")),
        covers=_parse_yaml_covers(raw.get("covers")),
        thermo_zones=_parse_yaml_thermo(raw.get("thermo_zones")),
        energy_meters=_parse_yaml_energy_meters(raw.get("energy_meters")),
        energy_actuators=_parse_yaml_energy_actuators(raw.get("energy_actuators")),
        scenarios=_parse_yaml_scenarios(raw.get("scenarios")),
        fault_addresses=fault_addrs,
        subbus_interfaces=sub_ifs,
    )


def load_plnt_xml(path: Path) -> PlantTopologyConfig:
    """Clean-room parser for declarative .plnt XML plant descriptions."""
    root = ET.fromstring(path.read_text(encoding="utf-8"))  # noqa: S314 - trusted local plant fixture
    name = root.attrib.get("name", path.stem)
    lights: dict[str, LightConfig] = {}
    dimmers: dict[str, DimmerConfig] = {}
    covers: dict[str, CoverConfig] = {}
    thermo_zones: dict[int, ThermoZoneConfig] = {}
    sub_ifs: set[int] = set()

    for dev_elem in root.findall(".//device"):
        dev_type = (
            dev_elem.attrib.get("type")
            or dev_elem.findtext("type")
            or dev_elem.attrib.get("model")
            or ""
        ).upper()
        where = (
            dev_elem.attrib.get("where") or dev_elem.findtext("where") or ""
        ).strip()
        if not where:
            continue
        if "#4#" in where:
            sub_ifs.add(parse_own_where(where).interface or 0)
        if "DIMMER" in dev_type or dev_type in ("F414", "F418U2"):
            bus_addr, subbus = where_to_bus_addr(where)
            dimmers[where] = DimmerConfig(
                where=where,
                bus_addr=bus_addr,
                model="F418U2" if "418" in dev_type else "F414",
                subbus=subbus,
                area=(bus_addr >> 4) & 0x0F,
            )
        elif "SHUTTER" in dev_type or "COVER" in dev_type or dev_type == "F411/2":
            bus_addr, subbus = where_to_bus_addr(where)
            covers[where] = CoverConfig(
                where=where,
                bus_addr=bus_addr,
                subbus=subbus,
                area=(bus_addr >> 4) & 0x0F,
            )
        elif "THERMO" in dev_type or dev_type == "KW4691":
            z = _int_val(where, 1)
            thermo_zones[z] = ThermoZoneConfig(zone=z, bus_addr=z)
        else:
            bus_addr, subbus = where_to_bus_addr(where)
            lights[where] = LightConfig(
                where=where,
                bus_addr=bus_addr,
                subbus=subbus,
                area=(bus_addr >> 4) & 0x0F,
            )

    return PlantTopologyConfig(
        name=name,
        lights=lights,
        dimmers=dimmers,
        covers=covers,
        thermo_zones=thermo_zones,
        subbus_interfaces=frozenset(sub_ifs),
    )


class PlantTopology:
    """Stateful virtual plant topology instantiated from a PlantTopologyConfig."""

    def __init__(self, config: PlantTopologyConfig) -> None:
        self.config = config
        self.lights: dict[str, LightDevice] = {
            w: LightDevice(cfg) for w, cfg in config.lights.items()
        }
        self.dimmers: dict[str, DimmerDevice] = {
            w: DimmerDevice(cfg) for w, cfg in config.dimmers.items()
        }
        self.covers: dict[str, CoverDevice] = {
            w: CoverDevice(cfg) for w, cfg in config.covers.items()
        }
        self.thermo_zones: dict[int, ThermoZoneDevice] = {
            z: ThermoZoneDevice(cfg) for z, cfg in config.thermo_zones.items()
        }
        self.energy_meters: dict[str, EnergyMeterDevice] = {
            w: EnergyMeterDevice(cfg) for w, cfg in config.energy_meters.items()
        }
        self.energy_actuators: dict[str, EnergyActuatorDevice] = {
            w: EnergyActuatorDevice(cfg) for w, cfg in config.energy_actuators.items()
        }
        self.scenarios: dict[str, ScenarioDevice] = {
            w: ScenarioDevice(cfg) for w, cfg in config.scenarios.items()
        }
        self.fault_channels: dict[int, FaultChannel] = {
            addr: FaultChannel(addr, config.fault_masks.get(addr, DEFAULT_FAULT_MASK))
            for addr in config.fault_addresses
        }

    @property
    def name(self) -> str:
        """Return the topology fixture name."""
        return self.config.name

    def reset(self) -> None:
        """Reset all devices in the plant to their initial state."""
        for light in self.lights.values():
            light.reset()
        for dimmer in self.dimmers.values():
            dimmer.reset()
        for cover in self.covers.values():
            cover.reset()
        for zone in self.thermo_zones.values():
            zone.reset()
        for meter in self.energy_meters.values():
            meter.reset()
        for act in self.energy_actuators.values():
            act.reset()
        for scen in self.scenarios.values():
            scen.reset()

    def group_any_on(self, group: int, subbus: int = 0) -> bool:
        """Return True if any light or dimmer in group on subbus is currently ON."""
        for light in self.lights.values():
            if (
                light.config.group == group
                and light.config.subbus == subbus
                and light.what > 0
            ):
                return True
        for dimmer in self.dimmers.values():
            if (
                dimmer.config.group == group
                and dimmer.config.subbus == subbus
                and dimmer.what > 0
            ):
                return True
        return False

    @classmethod
    def from_yaml(cls, path: Path) -> PlantTopology:
        """Load a PlantTopology from a YAML file path."""
        return cls(load_topology_yaml(path))

    @classmethod
    def from_fixture(cls, name: str) -> PlantTopology:
        """Load a named fixture from oracle/plant/fixtures/<name>.yaml."""
        cand = Path(name)
        if cand.is_file():
            return cls.from_yaml(cand)
        filename = name if name.endswith(".yaml") else f"{name}.yaml"
        fixture_path = FIXTURES_DIR / filename
        if not fixture_path.is_file():
            raise FileNotFoundError(f"unknown plant fixture: {name!r} ({fixture_path})")
        return cls.from_yaml(fixture_path)

    @classmethod
    def from_plnt_xml(cls, path: Path) -> PlantTopology:
        """Load a PlantTopology from a .plnt XML file."""
        return cls(load_plnt_xml(path))

    @staticmethod
    def _infer_status_frame(
        who: int,
        what_str: str,
        where: str,
        lights: dict[str, LightConfig],
        dimmers: dict[str, DimmerConfig],
        covers: dict[str, CoverConfig],
    ) -> None:
        if where.startswith("#") or where == "0":
            return
        try:
            baddr, sub = where_to_bus_addr(where)
        except ValueError:
            return
        if who == 1:
            what_base = _int_val(what_str.split("#", maxsplit=1)[0], 0)
            if 2 <= what_base <= 10:
                dimmers.setdefault(
                    where,
                    DimmerConfig(
                        where=where,
                        bus_addr=baddr,
                        subbus=sub,
                        initial_what=what_base,
                        area=(baddr >> 4) & 0x0F,
                    ),
                )
            elif where not in dimmers:
                lights.setdefault(
                    where,
                    LightConfig(
                        where=where,
                        bus_addr=baddr,
                        subbus=sub,
                        initial_what=1 if what_base == 1 else 0,
                        area=(baddr >> 4) & 0x0F,
                    ),
                )
        elif who == 2:
            covers.setdefault(
                where,
                CoverConfig(
                    where=where,
                    bus_addr=baddr,
                    subbus=sub,
                    area=(baddr >> 4) & 0x0F,
                ),
            )

    @classmethod
    def from_myhome_trace(
        cls,
        frames: list[tuple[str, str]],
        *,
        name: str = "trace",
        gateway_product: str = "F461",
    ) -> PlantTopology:
        """Infer a PlantTopology from observed rx frames in a MyHOME trace."""
        lights: dict[str, LightConfig] = {}
        dimmers: dict[str, DimmerConfig] = {}
        covers: dict[str, CoverConfig] = {}
        thermo_zones: dict[int, ThermoZoneConfig] = {}
        energy_meters: dict[str, EnergyMeterConfig] = {}

        for direction, raw in frames:
            if direction != "rx":
                continue
            m_st = _OWN_STATUS_RE.match(raw)
            if m_st:
                cls._infer_status_frame(
                    int(m_st.group(1)),
                    m_st.group(2),
                    m_st.group(3),
                    lights,
                    dimmers,
                    covers,
                )
            m_dim = _OWN_DIM_RE.match(raw)
            if m_dim:
                who = int(m_dim.group(1))
                where = m_dim.group(2)
                if who == 4 and where.isdigit() and 1 <= int(where) <= 99:
                    z = int(where)
                    thermo_zones.setdefault(z, ThermoZoneConfig(zone=z, bus_addr=z))
                elif who == 18 and where.startswith("5") and len(where) == 2:
                    baddr, _ = where_to_bus_addr(where)
                    energy_meters.setdefault(
                        where, EnergyMeterConfig(where=where, bus_addr=baddr)
                    )

        return cls(
            PlantTopologyConfig(
                name=name,
                gateway_product=gateway_product,
                lights=lights,
                dimmers=dimmers,
                covers=covers,
                thermo_zones=thermo_zones,
                energy_meters=energy_meters,
            )
        )
