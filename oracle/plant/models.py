# SPDX-License-Identifier: Apache-2.0
"""Data models for deterministic SCS/OpenWebNet plant simulation and trace exchanges."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SCSAddress:
    """Parsed OpenWebNet / SCS address for WHO=1, 2, 14.

    Attributes:
        area: Area index (``0..10`` for p2p/area, ``0`` for group/general).
        pl: Point or group index (``1..15`` for p2p, ``1..255`` for group, ``0`` else).
        interface: Optional F422 sub-bus interface number (``1..15`` for ``#4#<IF>``).
        four_digit_area0: Whether Area 0 P2P address used 4 digits (``0001..0015``).
        kind: Address scope (``"p2p"``, ``"area"``, ``"group"``, or ``"general"``).
    """

    area: int
    pl: int
    interface: int | None = None
    four_digit_area0: bool = False
    kind: str = "p2p"

    def to_scs_byte(self) -> int:
        """Convert (area, pl) into the 8-bit SCS address byte."""
        if self.kind == "general":
            return 0x00
        if self.kind == "group":
            return self.pl & 0xFF
        if self.kind == "area":
            return self.area & 0xFF
        return ((self.area & 0x0F) << 4) | (self.pl & 0x0F)

    def to_own_where(self) -> str:
        """Format back to canonical OpenWebNet WHERE string."""
        suffix = f"#4#{self.interface:02d}" if self.interface is not None else ""
        if self.kind == "general":
            return f"0{suffix}"
        if self.kind == "group":
            return f"#{self.pl}{suffix}"
        if self.kind == "area":
            base = "00" if self.area == 0 else str(self.area)
            return f"{base}{suffix}"
        if self.area == 0:
            if self.pl >= 10 or self.four_digit_area0:
                return f"00{self.pl:02d}{suffix}"
            return f"0{self.pl}{suffix}"
        if self.area == 10 or self.pl >= 10:
            return f"{self.area:02d}{self.pl:02d}{suffix}"
        return f"{self.area}{self.pl}{suffix}"

    @classmethod
    def parse_own_where(cls, where: str) -> SCSAddress:
        """Parse an OpenWebNet WHERE string into an SCSAddress."""
        return parse_own_where(where)


def parse_own_where(where: str) -> SCSAddress:
    """Parse an OpenWebNet WHERE string into an SCSAddress."""
    interface: int | None = None
    base = where
    if "#4#" in where:
        base, sub_str = where.split("#4#", 1)
        if not sub_str.isdigit():
            raise ValueError(f"invalid sub-bus interface in WHERE={where!r}")
        interface = int(sub_str)

    if base == "0":
        return SCSAddress(area=0, pl=0, interface=interface, kind="general")
    if base.startswith("#") and base[1:].isdigit():
        grp = int(base[1:])
        if not 1 <= grp <= 255:
            raise ValueError(f"group out of range in WHERE={where!r}")
        return SCSAddress(area=0, pl=grp, interface=interface, kind="group")
    if base == "00":
        return SCSAddress(area=0, pl=0, interface=interface, kind="area")
    if len(base) == 1 and base in "123456789":
        return SCSAddress(area=int(base), pl=0, interface=interface, kind="area")
    if base == "10":
        return SCSAddress(area=10, pl=0, interface=interface, kind="area")
    if len(base) == 2 and base.isdigit():
        area = int(base[0])
        pl = int(base[1])
        if 1 <= pl <= 9:
            return SCSAddress(area=area, pl=pl, interface=interface, kind="p2p")
    if len(base) == 4 and base.isdigit():
        area = int(base[:2])
        pl = int(base[2:])
        if 0 <= area <= 10 and 1 <= pl <= 15:
            return SCSAddress(
                area=area,
                pl=pl,
                interface=interface,
                four_digit_area0=(area == 0),
                kind="p2p",
            )
    raise ValueError(f"unsupported OpenWebNet WHERE={where!r}")


@dataclass(frozen=True)
class Exchange:
    """A normalized OpenWebNet command session exchange from a hardware trace or bundle.

    Attributes:
        tx: Client-transmitted OpenWebNet command or query frame (e.g. ``*#1*21##``).
        replies: Pre-ACK response frames received on the command session.
        ack_or_nack: Terminal handshake frame (``*#*1##`` or ``*#*0##``).
        events_after: Asynchronous frames emitted on the event session within the
            settle window following this command exchange.
        source_line: 1-based line or entry index in the source trace file.
        retry_without_result: True if extracted from a retry/reconnect row without
            an explicit terminating ``dir=="result"`` row.
    """

    tx: str
    replies: tuple[str, ...]
    ack_or_nack: str
    events_after: tuple[str, ...] = ()
    source_line: int = 0
    retry_without_result: bool = False

    @property
    def ack(self) -> bool:
        """Return True when ack_or_nack is ``*#*1##``."""
        return self.ack_or_nack == "*#*1##"


@dataclass(frozen=True)
class LightConfig:
    """Configuration for a relay light point (WHO=1, 14)."""

    where: str
    bus_addr: int
    subbus: int = 0
    initial_what: int = 0
    area: int = 0
    group: int = 0
    pul: bool = False
    initial_locked: bool = False


@dataclass(frozen=True)
class DimmerConfig:
    """Configuration for a dimmer actuator (WHO=1, F414 / F418U2)."""

    where: str
    bus_addr: int
    model: str = "F414"
    subbus: int = 0
    initial_what: int = 10
    initial_dim1_level: int = 200
    initial_dim1_speed: int = 2
    area: int = 0
    group: int = 0
    pul: bool = False
    initial_locked: bool = False
    discrete_levels: dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CoverConfig:
    """Configuration for a roller shutter / automation actuator (WHO=2, 14)."""

    where: str
    bus_addr: int
    subbus: int = 0
    advanced: bool = False
    travel_time_ms: int = 5000
    initial_what: int = 0
    initial_level: int = 50
    area: int = 0
    group: int = 0
    initial_locked: bool = False


@dataclass(frozen=True)
class ThermoZoneConfig:
    """Configuration for a thermoregulation zone (WHO=4)."""

    zone: int
    bus_addr: int
    initial_temp_deci: int = 249
    initial_setpoint_deci: int = 245
    initial_mode: int = 1
    initial_function: int = 3
    initial_offset: str = "00"
    initial_valve_heating: int = 0
    initial_valve_cooling: int = 5
    initial_actuator_status: int = 0
    actuators: dict[int, int] = field(default_factory=dict)
    initial_fan_speed: int = 0
    probes: tuple[int, ...] = ()
    initial_humidity: int | None = None

    def __post_init__(self) -> None:
        # A percentage, answered as one byte of the dimension 60 reply: a
        # value outside 0..100 is a fixture error, not something to mask,
        # and so is anything but an int (bool is an int subclass: True would
        # have passed as a 1 % probe).
        if self.initial_humidity is not None and (
            isinstance(self.initial_humidity, bool)
            or not isinstance(self.initial_humidity, int)
            or not 0 <= self.initial_humidity <= 100
        ):
            raise ValueError(
                f"thermo zone {self.zone}: initial_humidity "
                f"{self.initial_humidity!r} is not a percentage (0..100)"
            )


@dataclass(frozen=True)
class EnergyMeterConfig:
    """Configuration for an F520 energy management meter (WHO=18, 5N)."""

    where: str
    bus_addr: int
    active_power_w: int = 450
    total_energy_wh: int = 123456
    current_month_wh: int = 12345
    partial_energy_wh: int = 6789
    daily_wh: dict[tuple[int, int], int] = field(default_factory=dict)
    monthly_wh: dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class EnergyActuatorConfig:
    """Configuration for an F522/F523 energy actuator (WHO=18, 7N#0)."""

    where: str
    bus_addr: int
    initial_state: int = 0
    active_power_w: int = 0
    total_energy_wh: int = 0


@dataclass(frozen=True)
class ScenarioConfig:
    """Configuration for a CEN (WHO=15) or CEN+ (WHO=25) scenario button module."""

    where: str
    bus_addr: int
    who: int = 15
    buttons: tuple[int, ...] = (1, 2)


@dataclass(frozen=True)
class PlantTopologyConfig:
    """Declarative plant topology loaded from a YAML fixture."""

    name: str
    gateway_product: str = "F461"
    gateway_version: str = "020011"
    lights: dict[str, LightConfig] = field(default_factory=dict)
    dimmers: dict[str, DimmerConfig] = field(default_factory=dict)
    covers: dict[str, CoverConfig] = field(default_factory=dict)
    thermo_zones: dict[int, ThermoZoneConfig] = field(default_factory=dict)
    energy_meters: dict[str, EnergyMeterConfig] = field(default_factory=dict)
    energy_actuators: dict[str, EnergyActuatorConfig] = field(default_factory=dict)
    scenarios: dict[str, ScenarioConfig] = field(default_factory=dict)
    fault_addresses: frozenset[int] = field(default_factory=frozenset)
    fault_masks: dict[int, bytes] = field(default_factory=dict)
    subbus_interfaces: frozenset[int] = field(default_factory=frozenset)


@dataclass(frozen=True)
class PlantBundle:
    """Bundled plant topology and correlated hardware exchanges for trace replay."""

    gateway_model: str
    gateway_fw: str | None
    source_id: str
    topology: PlantTopologyConfig
    exchanges: tuple[Exchange, ...] = ()
