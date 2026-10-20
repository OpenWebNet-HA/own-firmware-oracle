from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import check
import pic_spike
import plant_index
from oracle import bus, driver, plant, qemu_target, record, target
from oracle import run as oracle_run
from oracle.plant import (
    CoverConfig,
    CoverDevice,
    DimmerConfig,
    DimmerDevice,
    EnergyActuatorConfig,
    EnergyActuatorDevice,
    EnergyMeterConfig,
    EnergyMeterDevice,
    Exchange,
    FaultChannel,
    LightConfig,
    LightDevice,
    OwnPlantEngine,
    PlantBundle,
    PlantResponder,
    PlantTopology,
    PlantTopologyConfig,
    ScenarioConfig,
    ScenarioDevice,
    SCSAddress,
    ThermoZoneConfig,
    ThermoZoneDevice,
    decode_gateway_frame,
    encode_extended_inbound,
    encode_short_inbound,
    load_topology_yaml,
    parse_own_where,
    parse_where_subbus,
    where_to_bus_addr,
)
from oracle.plant.scs_codec import unwrap_raw_scs, wrap_raw_scs


def test_scs_address_and_parse_own_where() -> None:
    assert plant.__all__
    addr_gen = parse_own_where("0")
    assert addr_gen == SCSAddress(area=0, pl=0, kind="general")
    assert addr_gen.to_scs_byte() == 0x00
    assert addr_gen.to_own_where() == "0"

    addr_grp = SCSAddress.parse_own_where("#5")
    assert addr_grp == SCSAddress(area=0, pl=5, kind="group")
    assert addr_grp.to_scs_byte() == 0x05
    assert addr_grp.to_own_where() == "#5"

    addr_area0 = parse_own_where("00")
    assert addr_area0.to_scs_byte() == 0x00
    assert addr_area0.to_own_where() == "00"

    addr_area2 = parse_own_where("2#4#01")
    assert addr_area2 == SCSAddress(area=2, pl=0, interface=1, kind="area")
    assert addr_area2.to_scs_byte() == 0x02
    assert addr_area2.to_own_where() == "2#4#01"

    addr_area10 = parse_own_where("10")
    assert addr_area10 == SCSAddress(area=10, pl=0, kind="area")
    assert addr_area10.to_own_where() == "10"

    addr_p2p_06 = parse_own_where("06")
    assert addr_p2p_06.to_scs_byte() == 0x06
    assert addr_p2p_06.to_own_where() == "06"

    addr_p2p_0006 = parse_own_where("0006")
    assert addr_p2p_0006.four_digit_area0 is True
    assert addr_p2p_0006.to_own_where() == "0006"

    addr_p2p_0012 = parse_own_where("0012")
    assert addr_p2p_0012.to_own_where() == "0012"

    addr_p2p_21 = parse_own_where("21")
    assert addr_p2p_21.to_scs_byte() == 0x21
    assert addr_p2p_21.to_own_where() == "21"

    addr_p2p_1015 = parse_own_where("1015#4#02")
    assert addr_p2p_1015.to_scs_byte() == 0xAF
    assert addr_p2p_1015.to_own_where() == "1015#4#02"

    with pytest.raises(ValueError, match="invalid sub-bus"):
        parse_own_where("21#4#xx")
    with pytest.raises(ValueError, match="group out of range"):
        parse_own_where("#0")
    with pytest.raises(ValueError, match="unsupported"):
        parse_own_where("20")
    with pytest.raises(ValueError, match="unsupported"):
        parse_own_where("9999")
    with pytest.raises(ValueError, match="unsupported"):
        parse_own_where("bad")

    bundle = PlantBundle(
        gateway_model="F461",
        gateway_fw="020011",
        source_id="test",
        topology=PlantTopologyConfig(name="test"),
        exchanges=(
            Exchange(tx="*#1*06##", replies=("*1*0*06##",), ack_or_nack="*#*1##"),
        ),
    )
    assert bundle.gateway_model == "F461"
    assert bundle.exchanges[0].ack is True


def test_scs_codec() -> None:
    raw4 = wrap_raw_scs(b"\x01\x02\x03\x04")
    assert unwrap_raw_scs(raw4) == b"\x01\x02\x03\x04"
    assert unwrap_raw_scs(b"\x01") is None
    assert unwrap_raw_scs(b"\x00" * 7) is None
    assert unwrap_raw_scs(b"\xa8\x01\x02\x03\x04\xff\xa3") is None
    with pytest.raises(ValueError, match="4 or 8 bytes"):
        wrap_raw_scs(b"\x01")
    d_raw = decode_gateway_frame(wrap_raw_scs(bytes.fromhex("EC01000011001200")))
    assert d_raw is not None
    assert d_raw.digits == 0
    assert d_raw.subbus == 1

    assert encode_short_inbound(0xB8, 0x06, 0x12, 0x00, digits=0) == wrap_raw_scs(
        bytes([0xB8, 0x06, 0x12, 0x00])
    )
    assert encode_short_inbound(0xB8, 0x06, 0x12, 0x00, digits=3) == b"$017B8061200\r"
    assert encode_short_inbound(0xB8, 0x06, 0x12, 0x00, digits=2) == b"$17B8061200\r"
    with pytest.raises(ValueError, match="out of range"):
        encode_short_inbound(999, 0x06, 0x12, 0x00)
    with pytest.raises(ValueError, match="unsupported PIC digit width"):
        encode_short_inbound(0xB8, 0x06, 0x12, 0x00, digits=9)
    assert (
        encode_short_inbound(0xB8, 0x11, 0x12, 0x00, digits=3, subbus=1)
        == b"$018E4010000B8111200\r"
    )
    assert (
        encode_short_inbound(0xB8, 0x11, 0x12, 0x00, digits=2, subbus=1)
        == b"$18E4010000B8111200\r"
    )

    ext = bytes.fromhex("D20801440D0D3200")
    assert encode_extended_inbound(ext, digits=0) == wrap_raw_scs(ext)
    assert encode_extended_inbound(ext, digits=3) == b"$018D20801440D0D3200\r"
    assert encode_extended_inbound(ext, digits=2) == b"$18D20801440D0D3200\r"
    with pytest.raises(ValueError, match="unsupported PIC digit width"):
        encode_extended_inbound(ext, digits=9)
    with pytest.raises(ValueError, match="8 bytes"):
        encode_extended_inbound(b"\x01\x02")

    assert decode_gateway_frame(b"plain") is None
    assert decode_gateway_frame(b"$024\r") is None
    assert decode_gateway_frame(b"$03ZZZZ\r") is None
    assert decode_gateway_frame(b"$03ZZZZZZZZ\r") is None
    assert decode_gateway_frame(b"$030102\r") is None

    d3 = decode_gateway_frame(b"$00306001200\r")
    assert d3 is not None
    assert d3.digits == 3
    assert d3.opcode == "03"
    assert d3.subbus == 0
    d_ec = decode_gateway_frame(b"$006EC01000011001200\r")
    assert d_ec is not None
    assert d_ec.subbus == 1
    assert d_ec.payload == bytes.fromhex("EC01000011001200")

    d_ec_short = decode_gateway_frame(b"$006EC01000011001200\r")
    assert d_ec_short is not None

    # 8-byte EC frame in payload
    d_ec8 = decode_gateway_frame(b"$006EC010000110012\r")
    assert d_ec8 is None

    assert parse_where_subbus("11#4#01") == ("11", 1)
    assert parse_where_subbus("11#4#bad") == ("11", 0)
    assert parse_where_subbus("11") == ("11", 0)

    assert where_to_bus_addr("0") == (0x00, 0)
    assert where_to_bus_addr("21") == (0x21, 0)
    assert where_to_bus_addr("51", who=18) == (0xA1, 0)
    assert where_to_bus_addr("71#0") == (0xB1, 0)
    assert where_to_bus_addr("74") == (0x74, 0)
    assert where_to_bus_addr("0A") == (0x0A, 0)
    with pytest.raises(ValueError, match="cannot map"):
        where_to_bus_addr("bad_where")


def test_devices_light_dimmer_cover() -> None:
    # LightDevice
    lc = LightConfig(where="06", bus_addr=0x06, group=1)
    ld = LightDevice(lc)
    assert ld.handle_short(0x01, 0x15, 0x00, digits=3) == []
    assert ld.status_frame(digits=3) == b"$017B8061201\r"
    assert ld.handle_short(0x00, 0x15, 0x00, digits=3) == [b"$017B8061201\r"]
    assert ld.handle_short(0x00, 0x12, 0x00, digits=3) == [b"$017B8061200\r"]
    assert ld.what == 1
    assert ld.handle_short(0x00, 0x12, 0x01, digits=2) == [b"$17B8061201\r"]
    assert ld.what == 0
    # Impulse WHAT=18 (b3=0x04) with group=1 and group=0
    imp = ld.handle_short(0x00, 0x12, 0x04, digits=3)
    assert len(imp) == 4
    ld_nogrp = LightDevice(LightConfig(where="06", bus_addr=0x06, group=0))
    assert len(ld_nogrp.handle_short(0x00, 0x12, 0x04, digits=3)) == 2
    assert ld.handle_short(0x00, 0x12, 0x99, digits=3) == []
    # Step / toggle b3=0x1D and 0x03
    assert ld.handle_short(0x00, 0x12, 0x1D, digits=3) == [b"$017B8061200\r"]
    assert ld.handle_short(0x00, 0x12, 0x03, digits=3) == [b"$017B8061201\r"]
    # Lock (0x11) / unlock (0x10)
    assert ld.handle_short(0x00, 0x11, 0x00, digits=3) == [b"$017B8061100\r"]
    assert ld.locked is True
    assert ld.handle_short(0x00, 0x12, 0x00, digits=3) == []
    assert ld.handle_short(0x00, 0x10, 0x00, digits=3) == [b"$017B8061000\r"]
    assert ld.locked is False
    assert ld.handle_short(0x00, 0x99, 0x00, digits=3) == []
    ld.reset()

    # DimmerDevice
    dc = DimmerConfig(where="31", bus_addr=0x31, model="F414", group=1)
    dd = DimmerDevice(dc)
    assert dd.handle_short(0x01, 0x15, 0x00, digits=2) == []
    assert dd.status_frame(digits=2) == b"$17B831129D\r"
    assert dd.handle_short(0x00, 0x15, 0x00, digits=2) == [b"$17B831129D\r"]
    assert dd.handle_short(0x00, 0x11, 0x00, digits=2) == [b"$17B8311100\r"]
    assert dd.handle_short(0x00, 0x12, 0x01, digits=2) == []
    assert dd.handle_short(0x00, 0x10, 0x00, digits=2) == [b"$17B8311000\r"]
    assert dd.handle_short(0x00, 0x12, 0x01, digits=2) == [b"$17B8311201\r"]
    assert dd.handle_short(0x00, 0x12, 0x00, digits=2) == [b"$17B8311200\r"]
    assert dd.handle_short(0x00, 0x12, 0x4D, digits=2) == [b"$17B831124D\r"]
    assert dd.handle_short(0x00, 0x12, 0x56, digits=2) == []
    assert dd.handle_short(0x00, 0x12, 0x0D, digits=2) == []
    assert dd.handle_short(0x00, 0x99, 0x00, digits=2) == []

    # Dimmer extended
    assert dd.handle_extended(bytes.fromhex("D13101430D0D0000"), digits=2) != []
    assert dd.handle_extended(bytes.fromhex("D13101430D000000"), digits=2) != []
    assert dd.handle_extended(bytes.fromhex("D13101430D990000"), digits=2) == []
    assert dd.handle_extended(bytes.fromhex("D13101420D0D3200"), digits=2) != []
    assert dd.handle_extended(bytes.fromhex("D13101420D013205"), digits=2) != []
    assert dd.handle_extended(bytes.fromhex("D13101420D993205"), digits=2) == []
    assert dd.handle_extended(bytes.fromhex("D131019900000000"), digits=2) == []
    dd.reset()

    dc_custom = DimmerConfig(
        where="08", bus_addr=0x08, discrete_levels={2: 0x1F, 5: 0x4C}
    )
    dd_custom = DimmerDevice(dc_custom)
    assert dd_custom._level_to_what(100) == 0
    assert dd_custom._level_to_what(105) == 2
    assert dd_custom._level_to_what(200) == 10

    # CoverDevice
    cc = CoverConfig(where="05", bus_addr=0x05, travel_time_ms=100)
    cd = CoverDevice(cc)
    assert cd.handle_short(0x01, 0x15, 0x00, digits=3) == []
    assert cd.status_frame(digits=3) == b"$017B805120A\r"
    cd.what = 1
    assert cd.status_frame(digits=3) == b"$017B8051208\r"
    cd.what = 2
    assert cd.status_frame(digits=3) == b"$017B8051209\r"
    cd.reset()

    assert cd.handle_short(0x00, 0x15, 0x00, digits=3) == [b"$017B805120A\r"]
    assert cd.handle_short(0x00, 0x12, 0x08, digits=3, now_ms=10.0) == [
        b"$017B8051208\r"
    ]
    assert cd.tick(50.0, digits=3) == []
    assert cd.tick(150.0, digits=3) == [b"$017B805120A\r"]
    assert cd.tick(200.0, digits=3) == []

    assert cd.handle_short(0x00, 0x12, 0x09, digits=3, now_ms=10.0) == [
        b"$017B8051209\r"
    ]
    assert cd.handle_short(0x00, 0x12, 0x0A, digits=3, now_ms=20.0) == [
        b"$017B805120A\r"
    ]

    # Cover lock while moving stops movement and emits lock + stop
    cd.handle_short(0x00, 0x12, 0x08, digits=3, now_ms=10.0)
    lock_ans = cd.handle_short(0x00, 0x11, 0x00, digits=3, now_ms=20.0)
    assert len(lock_ans) == 2
    assert cd.handle_short(0x00, 0x12, 0x08, digits=3, now_ms=30.0) == []
    assert cd.handle_short(0x00, 0x10, 0x00, digits=3, now_ms=40.0) == [
        b"$017B8051000\r"
    ]
    assert cd.handle_short(0x00, 0x99, 0x00, digits=3) == []


def test_devices_thermo_energy_scenario_fault() -> None:
    tc = ThermoZoneConfig(
        zone=2,
        bus_addr=0x02,
        actuators={1: 0, 2: 5},
        probes=(1, 2),
    )
    td = ThermoZoneDevice(tc)
    for m, expected_b in [
        (0, 0xF1),
        (102, 0xF2),
        (202, 0xF3),
        (303, 0xFF),
        (208, 0x12),
        (108, 0x10),
        (1, 0xF0),
    ]:
        td.mode = m
        assert td._mode_byte() == expected_b
    td.reset()

    assert td.handle_short(bytes.fromhex("B40230FA"), digits=3) == []
    assert td.temp_deci == 0xFA
    assert len(td.handle_short(bytes.fromhex("A9023010"), digits=3)) == 2
    assert len(td.handle_short(bytes.fromhex("99823110"), digits=3)) == 1
    assert td.handle_short(bytes.fromhex("99823910"), digits=3) == []
    assert len(td.handle_short(bytes.fromhex("90023228"), digits=3)) == 1
    assert len(td.handle_short(bytes.fromhex("90023328"), digits=3)) == 1
    assert len(td.handle_short(bytes.fromhex("91023128"), digits=3)) == 1
    assert len(td.handle_short(bytes.fromhex("92023228"), digits=3)) == 1
    assert len(td.handle_short(bytes.fromhex("93023000"), digits=3)) == 1
    assert len(td.handle_short(bytes.fromhex("93023001"), digits=3)) == 1
    assert len(td.handle_short(bytes.fromhex("9302300F"), digits=3)) == 1
    assert len(td.handle_short(bytes.fromhex("93023099"), digits=3)) == 1

    # Zone queries
    assert len(td.handle_short(bytes.fromhex("99023000"), digits=3)) == 1
    td.temp_deci = 300
    assert len(td.handle_short(bytes.fromhex("99023100"), digits=3)) == 1
    assert td.handle_short(bytes.fromhex("99023900"), digits=3) == []
    assert td.handle_short(bytes.fromhex("99023112"), digits=3) == []
    for dim_b in (0x12, 0x0E, 0x0D, 0x13, 0x0B):
        assert len(td.handle_short(bytes([0x99, 0x02, 0x30, dim_b]), digits=3)) == 1
    assert td.handle_short(bytes.fromhex("99023099"), digits=3) == []
    assert td.handle_short(bytes.fromhex("00000000"), digits=3) == []

    # Extended fan speed
    assert len(td.handle_extended(bytes.fromhex("D10203020B030000"), digits=3)) == 1
    assert td.handle_extended(bytes.fromhex("D10203990B030000"), digits=3) == []

    # EnergyMeterDevice
    emc = EnergyMeterConfig(where="51", bus_addr=0xA1)
    emd = EnergyMeterDevice(emc)
    assert emd.handle_extended(bytes.fromhex("D1990233001D0000"), digits=3) == []
    assert len(emd.handle_extended(bytes.fromhex("D1A10233001D0000"), digits=3)) == 1
    assert len(emd.handle_extended(bytes.fromhex("D1A1023200021D01"), digits=3)) == 1
    assert len(emd.handle_extended(bytes.fromhex("D1A1023350150000"), digits=3)) == 2
    assert len(emd.handle_extended(bytes.fromhex("D1A1023350170000"), digits=3)) == 2
    assert len(emd.handle_extended(bytes.fromhex("D1A1023350180000"), digits=3)) == 2
    assert emd.handle_extended(bytes.fromhex("D1A1029900000000"), digits=3) == []
    emd.reset()

    # EnergyActuatorDevice
    eac = EnergyActuatorConfig(where="71#0", bus_addr=0xB1)
    ead = EnergyActuatorDevice(eac)
    assert ead.handle_extended(bytes.fromhex("D1991233001D0000"), digits=3) == []
    assert len(ead.handle_extended(bytes.fromhex("D1B11233001D0000"), digits=3)) == 1
    assert len(ead.handle_extended(bytes.fromhex("D1B1123370050000"), digits=3)) == 1
    assert len(ead.handle_extended(bytes.fromhex("D1B1123170010000"), digits=3)) == 1
    assert len(ead.handle_extended(bytes.fromhex("D1B1123350150000"), digits=3)) == 2
    assert ead.handle_extended(bytes.fromhex("D1B1129900000000"), digits=3) == []
    ead.reset()

    # ScenarioDevice & FaultChannel
    sd = ScenarioDevice(ScenarioConfig(where="11", bus_addr=0x11))
    sd.reset()
    fc = FaultChannel(0x74)
    assert len(fc.handle_short(0x00, 0x15, 0x00, digits=2)) == 2
    assert fc.handle_short(0x00, 0x12, 0x00, digits=2) == []


def test_topology_loaders_and_responder(tmp_path: Path) -> None:
    f461_topo = PlantTopology.from_fixture("f461-bench")
    mh200_topo = PlantTopology.from_fixture("mh200-bench.yaml")
    assert f461_topo.name == "f461-bench"
    assert mh200_topo.name == "mh200-bench"
    f461_topo.reset()
    mh200_topo.reset()

    fixture_file = ROOT / "oracle" / "plant" / "fixtures" / "f461-bench.yaml"
    assert PlantTopology.from_fixture(str(fixture_file)).name == "f461-bench"
    with pytest.raises(FileNotFoundError, match="unknown plant fixture"):
        PlantTopology.from_fixture("nonexistent-bench")

    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("- not a mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected mapping"):
        load_topology_yaml(bad_yaml)

    min_yaml = tmp_path / "min.yaml"
    min_yaml.write_text(
        "name: min\n"
        "lights: null\n"
        "dimmers:\n  08:\n    bus_addr: '0x08'\n    discrete_levels: null\n"
        "covers:\n  05:\n    bus_addr: '5'\n"
        "thermo_zones:\n  1:\n    actuators: null\n    probes: null\n"
        "energy_meters:\n  51: null\n"
        "energy_actuators:\n  71#0: null\n"
        "scenarios:\n  11:\n    buttons: null\n"
        "fault_addresses: null\n"
        "subbus_interfaces: null\n",
        encoding="utf-8",
    )
    min_cfg = load_topology_yaml(min_yaml)
    assert min_cfg.name == "min"
    PlantTopology(min_cfg).reset()
    assert plant.topology._parse_yaml_covers(None) == {}
    assert plant.topology._int_val(None, 7) == 7
    assert plant.topology._int_val("bad", 7) == 7

    # XML loader
    plnt_file = tmp_path / "demo.plnt"
    plnt_file.write_text(
        '<plant name="demo">'
        '<device type="F418U2" where="08"/>'
        '<device type="SHUTTER" where="05#4#01"/>'
        '<device type="THERMO" where="2"/>'
        '<device type="RELAY" where="06"/>'
        '<device type="RELAY" where=""/>'
        '<device type="RELAY" where="11#4#02"/>'
        "</plant>",
        encoding="utf-8",
    )
    xml_topo = PlantTopology.from_plnt_xml(plnt_file)
    assert xml_topo.name == "demo"
    assert "08" in xml_topo.dimmers
    assert "05#4#01" in xml_topo.covers
    assert 2 in xml_topo.thermo_zones
    assert "06" in xml_topo.lights

    # Trace inference
    inferred = PlantTopology.from_myhome_trace(
        [
            ("tx", "*#1*06##"),
            ("rx", "*1*1*06##"),
            ("rx", "*1*5*31##"),
            ("rx", "*1*0*31##"),
            ("rx", "*1*1*0##"),
            ("rx", "*1*1*#1##"),
            ("rx", "*1*1*bad_where##"),
            ("rx", "*2*0*05##"),
            ("rx", "*99*1*06##"),
            ("rx", "*#4*2*0*0249##"),
            ("rx", "*#4*0*0*0249##"),
            ("rx", "*#18*51*113*450##"),
        ]
    )
    assert "06" in inferred.lights
    assert "31" in inferred.dimmers
    assert "05" in inferred.covers
    assert 2 in inferred.thermo_zones
    assert "51" in inferred.energy_meters

    # group_any_on with light and dimmer ON
    f461_topo.lights["06"].what = 1
    assert f461_topo.group_any_on(1, 0) is True
    f461_topo.lights["06"].what = 0
    f461_topo.lights["07"].what = 0
    assert f461_topo.group_any_on(1, 0) is False
    f461_topo.reset()

    # PlantResponder
    resp_f461 = PlantResponder.from_fixture("f461-bench")
    resp_mh200 = PlantResponder.from_yaml(
        ROOT / "oracle" / "plant" / "fixtures" / "mh200-bench.yaml"
    )
    assert resp_f461.respond(b"invalid") == []
    assert resp_f461.respond(b"$0060102030405060708\r") == []
    assert resp_f461.has_p2p_device(b"invalid") is False
    assert resp_f461.has_p2p_device(b"$0060102030405060708\r") is False
    assert resp_f461.has_p2p_device(b"$00306001200\r") is True
    assert resp_f461.has_p2p_device(b"$00399001200\r") is False

    # Short P2P light, dimmer, cover, EC subbus, and status polls (b2=0x15)
    assert resp_f461.respond(b"$00306001500\r") != []
    assert resp_f461.respond(b"$00306001200\r") != []
    assert resp_mh200.respond(b"$006EC01000021001200\r") != []
    assert resp_f461.respond(b"$00305001208\r") != []
    assert resp_f461.tick(10000.0) != []

    assert resp_mh200.respond(b"$0314001500\r") != []
    assert resp_mh200.respond(b"$0314001201\r") != []
    assert resp_mh200.tick(10000.0) == []

    # Dimmer in group 1 for group/general responder coverage
    grp_dim_cfg = PlantTopologyConfig(
        name="grp_dim",
        gateway_product="MH200",
        lights={
            "21": LightConfig(where="21", bus_addr=0x21, group=1, area=2),
            "22": LightConfig(
                where="22", bus_addr=0x22, group=0, area=9, initial_locked=True
            ),
        },
        dimmers={
            "31": DimmerConfig(where="31", bus_addr=0x31, group=1, area=3),
            "32": DimmerConfig(
                where="32", bus_addr=0x32, group=0, area=9, initial_locked=True
            ),
        },
        covers={"41": CoverConfig(where="41", bus_addr=0x41, group=1, area=4)},
        fault_addresses=frozenset({0x74}),
    )
    grp_topo = PlantTopology(grp_dim_cfg)
    grp_topo.lights["21"].what = 0
    grp_topo.dimmers["31"].what = 5
    assert grp_topo.group_any_on(1, 0) is True
    grp_topo.dimmers["31"].what = 0
    assert grp_topo.group_any_on(1, 0) is False
    resp_grp = PlantResponder(grp_topo)
    assert resp_grp.respond(b"$0331001201\r") != []
    assert resp_grp.respond(b"$0374001500\r") != []
    # Broadcast polls: B1 (general), B3 (area), B5 (group)
    assert resp_grp.respond(b"$03B1001500\r") != []
    assert resp_grp.respond(b"$03B3021500\r") != []
    assert resp_grp.respond(b"$03B5011500\r") != []
    # Group command (2-digit and 3-digit)
    assert resp_grp.respond(b"$03B5011200\r") != []
    assert resp_grp.respond(b"$03B5091200\r") == []
    assert resp_f461.respond(b"$003B5011200\r") != []
    # General command (2-digit and 3-digit)
    assert resp_grp.respond(b"$03B1001201\r") != []
    assert resp_f461.respond(b"$003B1001200\r") != []
    # Area command (2-digit, 3-digit, subbus cover block, unknown cmd)
    assert resp_grp.respond(b"$03B3021200\r") != []
    assert resp_f461.respond(b"$003B3001200\r") != []
    assert resp_f461.respond(b"$006EC010000B3011208\r") == []
    assert resp_f461.respond(b"$003B3011299\r") == []
    assert resp_f461.respond(b"$003B1009900\r") == []

    # WHO=4 short & extended via responder
    assert resp_f461.respond(b"$00499023000\r") != []
    assert resp_f461.respond(b"$006D10203020B020000\r") != []
    assert resp_f461.respond(b"$006D19903020B020000\r") == []

    # Extended dimmer, D5 group status, unpopulated dimmer echo, WHO=2 dim 10, WHO=18
    assert resp_f461.respond(b"$006D501013307000000\r") != []
    assert resp_f461.respond(b"$006D501013207000000\r") != []
    assert resp_f461.respond(b"$006D501019907000000\r") == []
    assert resp_mh200.respond(b"$06D11401430D0D0000\r") != []
    assert resp_mh200.respond(b"$06D114019900000000\r") == []
    assert resp_f461.respond(b"$006D10801420D0D3200\r") != []
    assert resp_f461.respond(b"$006D105014309000000\r") != []
    assert resp_f461.respond(b"$006D1A10233001D0000\r") != []
    assert resp_f461.respond(b"$006D1A90233001D0000\r") == []
    assert resp_f461.respond(b"$006D1B11233001D0000\r") != []
    assert resp_f461.respond(b"$006D1B91233001D0000\r") == []
    assert resp_f461.respond(b"$006D199990000000000\r") == []
    resp_f461.reset()


def test_own_plant_engine_comprehensive() -> None:
    f461_cfg = load_topology_yaml(
        ROOT / "oracle" / "plant" / "fixtures" / "f461-bench.yaml"
    )
    eng = OwnPlantEngine(f461_cfg)
    assert eng.step("bad").ack_or_nack == "*#*0##"
    assert eng.step("*#*1##").ack_or_nack == "*#*0##"
    assert eng.step("*1##").ack_or_nack == "*#*0##"
    assert eng.step("*#1##").ack_or_nack == "*#*0##"
    assert eng.step("*99*1*1##").ack_or_nack == "*#*0##"
    assert eng.step("*#99*1##").ack_or_nack == "*#*0##"

    # WHO=13
    assert eng.step("*#13*1*0##").ack_or_nack == "*#*0##"
    assert eng.step("*#13**#0*01*02##").ack_or_nack == "*#*1##"
    assert eng.step("*#13**0*02##").ack_or_nack == "*#*1##"
    assert eng.step("*#13**0##").ack_or_nack == "*#*1##"
    assert eng.step("*#13**99##").ack_or_nack == "*#*0##"

    # WHO=1 action & queries
    assert eng.step("*1*bad*06##").ack_or_nack == "*#*0##"
    assert eng.step("*1*0*0##", now_ms=1.0).ack_or_nack == "*#*1##"
    assert eng.step("*1*1*0##", now_ms=1.0).ack_or_nack == "*#*1##"
    assert eng.step("*1*0*0##").ack_or_nack == "*#*1##"
    assert eng.step("*1*1*00##").ack_or_nack == "*#*1##"
    assert eng.step("*1*1*1#4#01##").ack_or_nack == "*#*1##"
    assert eng.step("*1*1*#1##").ack_or_nack == "*#*1##"
    assert eng.step("*1*0*#1##").ack_or_nack == "*#*1##"
    assert eng.step("*1*1*#9##").ack_or_nack == "*#*1##"
    assert eng.step("*1*18*06##").ack_or_nack == "*#*1##"
    assert eng.step("*1*30*06##").ack_or_nack == "*#*1##"
    assert eng.step("*1*1*06##").ack_or_nack == "*#*1##"
    assert eng.step("*1*1*06##").ack_or_nack == "*#*1##"
    assert eng.step("*1*0*06##").ack_or_nack == "*#*1##"
    assert eng.step("*1*99*99##").ack_or_nack == "*#*0##"

    assert eng.step("*#1*0##").ack_or_nack == "*#*1##"
    assert eng.step("*#1*00##").ack_or_nack == "*#*1##"
    assert eng.step("*#1*1##").ack_or_nack == "*#*1##"
    assert eng.step("*#1*#1##").ack_or_nack == "*#*1##"
    assert eng.step("*#1*06##").ack_or_nack == "*#*1##"
    assert eng.step("*#1*99##").ack_or_nack == "*#*0##"
    assert eng.step("*#1*06*1##").ack_or_nack == "*#*1##"
    assert eng.step("*#1*99*1##").ack_or_nack == "*#*0##"
    assert eng.step("*#1*06*2##").ack_or_nack == "*#*1##"
    assert eng.step("*#1*06*#2*0*0*10##").ack_or_nack == "*#*1##"
    assert eng.step("*#1*08*#1*150*0##").ack_or_nack == "*#*1##"
    assert eng.step("*#1*08*#1*bad*0##").ack_or_nack == "*#*0##"
    assert eng.step("*#1*08*#1*99*0##").ack_or_nack == "*#*0##"
    assert eng.step("*#1*06*99##").ack_or_nack == "*#*0##"

    # WHO=14 lock/unlock on light and cover
    assert eng.step("*14*9*06##").ack_or_nack == "*#*0##"
    assert eng.step("*14*0*11#4#01##").ack_or_nack == "*#*1##"
    assert eng.step("*14*0*06##").ack_or_nack == "*#*1##"
    assert eng.step("*1*1*06##").events_after == ()
    assert eng.step("*14*1*06##").ack_or_nack == "*#*1##"
    assert eng.step("*14*0*99##").ack_or_nack == "*#*0##"

    # WHO=2 action, lock, tick, subbus
    assert eng.step("*2*1*1#4#01##").ack_or_nack == "*#*1##"
    assert eng.step("*2*1*05##", now_ms=10.0).ack_or_nack == "*#*1##"
    assert eng.tick(20.0) == []
    assert eng.tick(10000.0) == ["*2*0*05##"]
    assert eng.step("*2*0*05##").ack_or_nack == "*#*1##"
    assert eng.step("*14*0*05##").ack_or_nack == "*#*1##"
    assert eng.step("*2*1*05##").ack_or_nack == "*#*1##"
    assert eng.step("*2*0*05##").ack_or_nack == "*#*1##"
    assert eng.step("*14*1*05##").ack_or_nack == "*#*1##"
    assert eng.step("*2*1*99##").ack_or_nack == "*#*0##"
    assert eng.step("*2*0*99##").ack_or_nack == "*#*0##"

    assert eng.step("*#2*0##").ack_or_nack == "*#*1##"
    assert eng.step("*#2*1#4#01##").ack_or_nack == "*#*1##"
    assert eng.step("*#2*99#4#bad##").ack_or_nack == "*#*0##"
    assert eng.step("*#2*05##").ack_or_nack == "*#*1##"
    assert eng.step("*#2*05*10##").ack_or_nack == "*#*1##"
    assert eng.step("*#2*05*99##").ack_or_nack == "*#*0##"

    # WHO=4 action, queries, dim writes, crash
    assert eng.step("*4*1*#0##").ack_or_nack == "*#*1##"
    assert eng.step("*4*1*#99##").ack_or_nack == "*#*0##"
    assert eng.step("*4*1*99##").ack_or_nack == "*#*0##"
    assert eng.step("*4*311*2##").ack_or_nack == "*#*0##"
    assert eng.step("*4*312#1*2##").ack_or_nack == "*#*1##"
    assert eng.topology.thermo_zones[2].fan_speed == 1
    assert eng.step("*4*312#0210#1*2##").ack_or_nack == "*#*1##"
    assert eng.step("*4*312#bad*2##").ack_or_nack == "*#*0##"
    assert eng.step("*4*bad*2##").ack_or_nack == "*#*0##"
    assert eng.step("*4*1*2##").ack_or_nack == "*#*1##"
    assert eng.step("*#4*2*#14*0190*3##").ack_or_nack == "*#*1##"
    assert eng.step("*4*208*2##").ack_or_nack == "*#*1##"
    assert eng.step("*4*302*2##").ack_or_nack == "*#*1##"
    assert eng.step("*#4*2*12##").ack_or_nack == "*#*1##"
    assert eng.step("*4*303*2##").ack_or_nack == "*#*1##"
    assert eng.step("*4*302*2##").ack_or_nack == "*#*1##"
    assert eng.step("*4*1*2##").ack_or_nack == "*#*1##"
    assert eng.step("*#4*2*#14*0190*3##").ack_or_nack == "*#*1##"
    assert eng.step("*#4*2*#14*0190*3##").ack_or_nack == "*#*1##"
    assert eng.step("*#4*2*#14*0190*9##").ack_or_nack == "*#*1##"
    assert eng.step("*#4*2*#14*0210*1##").ack_or_nack == "*#*1##"
    assert eng.step("*#4*2*11##").ack_or_nack == "*#*0##"
    assert eng.step("*#4*2*#14*0220*2##").ack_or_nack == "*#*1##"
    assert eng.step("*#4*2*#11*2##").ack_or_nack == "*#*1##"
    assert eng.step("*#4*2*11##").ack_or_nack == "*#*1##"
    assert eng.step("*#4*2*#99*1##").ack_or_nack == "*#*1##"
    assert eng.step("*#4*2*99##").ack_or_nack == "*#*0##"
    assert eng.step("*#4*#2*#14*0200*1##").ack_or_nack == "*#*1##"
    assert eng.step("*#4*#0*20##").ack_or_nack == "*#*1##"
    assert eng.step("*#4*#99*14##").ack_or_nack == "*#*0##"
    assert eng.step("*#4*2#1*20##").ack_or_nack == "*#*1##"
    assert eng.step("*#4*2#9*20##").ack_or_nack == "*#*0##"
    assert eng.step("*#4*2#1*99##").ack_or_nack == "*#*0##"
    assert eng.step("*#4*02*0##").ack_or_nack == "*#*0##"
    assert eng.step("*#4*2##").ack_or_nack == "*#*1##"
    assert eng.step("*#4*99##").ack_or_nack == "*#*0##"
    assert eng.step("*#4*2*#0*0230##").ack_or_nack == "*#*1##"
    assert eng.step("*#4*99*#0*0230##").ack_or_nack == "*#*1##"
    assert eng.step("*#4*99*0##").ack_or_nack == "*#*0##"
    for d in ("0", "13", "14", "19", "20"):
        assert eng.step(f"*#4*2*{d}##").ack_or_nack == "*#*1##"
    eng.topology.thermo_zones[2].actuators.clear()
    assert eng.step("*4*1*2##").ack_or_nack == "*#*1##"

    # bt_termo crash on F461 DIM 22
    assert eng.step("*#4*2*22##").ack_or_nack == "*#*0##"
    assert eng.bt_termo_crashed is True
    assert eng.step("*4*1*2##").ack_or_nack == "*#*0##"
    assert eng.step("*#4*2*0##").ack_or_nack == "*#*0##"
    eng.reset()

    # WHO=18
    assert eng.step("*#18*51##").ack_or_nack == "*#*0##"
    assert eng.step("*#18*51*1200##").ack_or_nack == "*#*0##"
    assert eng.step("*#18*51*113##").ack_or_nack == "*#*1##"
    assert eng.step("*#18*99*113##").ack_or_nack == "*#*0##"

    # MH200 engine specifics (dimmer, fault channel, cover, dim 4)
    mh_eng = OwnPlantEngine(PlantTopology.from_fixture("mh200-bench"))
    assert mh_eng.step("*1*1*0##").ack_or_nack == "*#*1##"
    assert mh_eng.step("*1*18*21##").ack_or_nack == "*#*1##"
    assert mh_eng.step("*1*1*21##").ack_or_nack == "*#*1##"
    assert mh_eng.step("*#1*0##").ack_or_nack == "*#*1##"
    assert mh_eng.step("*#2*0##").ack_or_nack == "*#*1##"
    assert mh_eng.step("*#4*2*22##").ack_or_nack == "*#*0##"
    assert mh_eng.step("*1*0*14##").ack_or_nack == "*#*1##"
    assert mh_eng.step("*1*5*14##").ack_or_nack == "*#*1##"
    assert mh_eng.step("*1*11*14##").ack_or_nack == "*#*1##"
    mh_eng.topology.dimmers["14"].locked = True
    assert mh_eng.step("*1*5*14##").events_after == ()
    mh_eng.topology.dimmers["14"].locked = False
    assert mh_eng.step("*#1*14##").ack_or_nack == "*#*1##"
    assert mh_eng.step("*#1*14*1##").ack_or_nack == "*#*1##"
    assert mh_eng.step("*#1*14*#1*150*0##").ack_or_nack == "*#*1##"
    assert mh_eng.step("*#1*14*#1*150*3##").ack_or_nack == "*#*1##"
    assert mh_eng.step("*#1*74##").replies[0] == "*1*19*74##"
    assert mh_eng.step("*#1*14*4##").ack_or_nack == "*#*1##"
    assert mh_eng.step("*#1*14*#4*1*1##").ack_or_nack == "*#*1##"
    assert mh_eng.step("*1*1*88##").ack_or_nack == "*#*1##"
    assert mh_eng.step("*2*1*11#4#02##").ack_or_nack == "*#*1##"
    assert mh_eng.step("*2*0*11#4#02##").ack_or_nack == "*#*1##"
    mh_eng.topology.covers["11#4#02"].locked = True
    assert mh_eng.step("*2*1*11#4#02##").ack_or_nack == "*#*1##"


def test_bus_and_driver_plant_integration() -> None:
    assert bus.PicResponder(version="12")._is_2digit_v8() is False
    # PicResponder with PlantResponder (3-digit and 2-digit v8)
    r3 = bus.get_responder("pic:plant:f461-bench", version="020011")
    assert r3.name == "pic:plant:f461-bench"
    assert r3.respond(b"$00306001200\r") == [
        b"$00800\r",
        b"$017B8061200\r",
        b"$018D501013407000000\r",
    ]
    assert r3.respond(b"$00399001200\r") == [b"$019\r"]
    assert r3.respond(b"$00499023000\r") == [b"$00800\r", b"$017B40230F9\r"]
    assert r3.respond(b"$006EC01000011001000\r") == [b"$00800\r"]
    assert r3.respond(b"$006D199019900000000\r") == [b"$000\r"]

    r_direct = bus.get_responder("plant:f461-bench", version="020011")
    assert r_direct.name == "plant:f461-bench"

    r2 = bus.PicResponder(
        version="028206",
        inner=PlantResponder.from_fixture("mh200-bench"),
        pace_2digit_ms=10.0,
    )
    assert r2._is_2digit_v8() is True
    assert r2.respond(b"$0321001201\r") == [b"$08\r"]
    assert r2.tick(5.0) == []
    assert r2.tick(10.0) == []
    assert r2.tick(25.0) != []
    assert r2.respond(b"$06D11401430D0D0000\r") == [b"$08\r"]
    assert r2.respond(b"$06D199019900000000\r") == [b"$00\r"]
    r2.reset()

    r2_nopace = bus.PicResponder(
        version="028206",
        inner=PlantResponder.from_fixture("mh200-bench"),
        pace_2digit_ms=0.0,
    )
    assert len(r2_nopace.respond(b"$0321001201\r")) >= 2
    assert r2_nopace.tick(100.0) == []

    # Tick forwarding when inner responder emits cover completion
    r3_cover = bus.PicResponder(
        version="020011",
        inner=PlantResponder.from_fixture("f461-bench"),
    )
    r3_cover.respond(b"$00305001208\r")
    assert r3_cover.tick(10000.0) != []

    r2_cover = bus.PicResponder(
        version="028206",
        inner=PlantResponder.from_fixture("f461-bench"),
        pace_2digit_ms=10.0,
    )
    r2_cover.respond(b"$00305001208\r")
    assert r2_cover.tick(10000.0) == []
    assert r2_cover.tick(10020.0) != []

    # Bus._tick and Bus.device_frames
    fake_port = MagicMock()
    fake_port.read.return_value = b""
    b = bus.Bus(port=fake_port, framer=bus.LineFramer(), responder=r2)
    r2.respond(b"$0321001201\r")
    b._tick(10.0)
    b._tick(30.0)
    assert b.device_frames(0) != []

    # driver._drain with plant harness
    mock_tgt = MagicMock()
    mock_tgt.harness = "plant:f461-bench"
    mock_tgt.take_bus.return_value = [b"$00306001200\r"]
    mock_tgt.take_device_bus.return_value = [b"$00800\r", b"$017B8061200\r"]
    mock_tgt.take_cmd_events.return_value = ["*1*1*06##"]
    mock_tgt.take_ev_events.return_value = ["*1*1*06##"]
    drained = driver._drain(mock_tgt)
    assert any(x.startswith("bus:") for x in drained)
    assert any(x.startswith("bus_rx:") for x in drained)
    assert any(x.startswith("cmd:") for x in drained)
    assert any(x.startswith("evt:") for x in drained)

    # QemuTarget take_cmd_events, take_ev_events, and take_device_bus
    spec = target.load(
        ROOT / "oracle" / "targets" / "MH200N" / "010108.yaml", ROOT / "results"
    )
    tgt = qemu_target.QemuTarget(
        spec,
        ROOT / "nonexistent.zip",
        ROOT / "tmp_work",
        harness="plant:f461-bench",
        staged_sysroot=ROOT,
        sandboxed=False,
        bus_instance=b,
    )
    assert tgt._responder.name == "pic:plant:f461-bench"
    tgt._cmd_events = ["*1*1*06##"]
    tgt._own_events = ["*1*1*06##"]
    assert tgt.take_cmd_events() == ["*1*1*06##"]
    assert tgt.take_ev_events() == ["*1*1*06##"]
    assert isinstance(tgt.take_device_bus(), list)

    ns = argparse.Namespace(plant="f461-bench.yaml", harness="full", responder=None)
    oracle_run.apply_plant_flag(ns)
    assert ns.harness == "plant:f461-bench"
    assert ns.responder == "pic:plant:f461-bench"
    ns2 = argparse.Namespace(
        plant="mh200-bench", harness="plant:custom", responder="pic:custom"
    )
    oracle_run.apply_plant_flag(ns2)
    assert ns2.harness == "plant:custom"
    assert ns2.responder == "pic:custom"


def test_check_trace_and_f461_extractors(tmp_path: Path) -> None:
    # is_synthetic_oracle_trace
    assert check.is_synthetic_oracle_trace(Path("oracle_f454.json")) is True
    assert (
        check.is_synthetic_oracle_trace(
            Path("real.json"), {"capture": {"synthetic": True}}
        )
        is True
    )
    assert (
        check.is_synthetic_oracle_trace(
            Path("real.json"), {"capture": {"source": "firmware-oracle-v1"}}
        )
        is True
    )
    assert check.is_synthetic_oracle_trace(Path("real.json"), {"capture": {}}) is False
    assert (
        check.is_synthetic_oracle_trace(Path("real.json"), {"capture": "str"}) is False
    )
    assert check.is_synthetic_oracle_trace(Path("real.json"), None) is False

    # extract_gateway_model_and_fw across all 8 paths
    assert check.extract_gateway_model_and_fw(
        Path("x.json"), {"gateway": {"model": "F461", "firmware": "2.0.11"}}
    ) == ("F461", "2.0.11")
    assert check.extract_gateway_model_and_fw(
        Path("x.json"), {"gateway": "MH200N", "firmware": "1.1.8"}
    ) == ("MH200N", "1.1.8")
    assert check.extract_gateway_model_and_fw(
        Path("x.json"),
        {
            "data": {
                "gateway_profile": {
                    "model_name": "MyHOMEServer1",
                    "firmware_version": "2.82.6",
                }
            }
        },
    ) == ("MyHomeServer1", "2.82.6")
    assert check.extract_gateway_model_and_fw(
        Path("x.json"),
        {"data": {"gateway": {"model_name": "F454", "firmware_version": "2.0.12"}}},
    ) == ("F454", "2.0.12")
    assert check.extract_gateway_model_and_fw(
        Path("x.json"),
        {"data": {"config_entry": {"data": {"model": "MH202", "firmware": "1.2.3"}}}},
    ) == ("MH202", "1.2.3")
    assert check.extract_gateway_model_and_fw(
        Path("x.json"), {"meta": {"gateway": "F455", "firmware": "1.0.0"}}
    ) == ("F455", "1.0.0")
    assert check.extract_gateway_model_and_fw(
        Path("x.json"),
        {"environment": {"gateway_model": "CustomGW", "gateway_firmware": "1.0"}},
    ) == ("CustomGW", "1.0")
    assert check.extract_gateway_model_and_fw(Path("mh200_trace.json"), {}) == (
        "MH200",
        None,
    )
    sib_dir = tmp_path / "sib"
    sib_dir.mkdir()
    (sib_dir / "f461_capture.json").write_text("{}", encoding="utf-8")
    assert check.extract_gateway_model_and_fw(sib_dir / "session.log", {}) == (
        "F461",
        None,
    )
    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    assert check.extract_gateway_model_and_fw(
        clean_dir / "unknown.log",
        {"gateway": {"model": None}, "data": {"config_entry": {"data": None}}},
    ) == (
        None,
        None,
    )
    assert check.extract_gateway_model_and_fw(
        clean_dir / "nonexistent_sub" / "unknown.log", None
    ) == (
        None,
        None,
    )

    # extract_f461_exchanges (Schema A, Schema B, Schema C, edge cases)
    assert check.extract_f461_exchanges({}) == []
    f461_payload: dict[str, object] = {
        "gateway": {"model": "F461", "firmware": "020011"},
        "rows": [
            "not_a_dict",
            {"dir": "rx", "session": "evt", "frame": "*1*0*06##"},
            {"dir": "tx", "session": "cmd", "frame": ""},
            {
                "dir": "tx",
                "session": "cmd",
                "frame": "*#1*06##",
                "ack": True,
                "replies": ["*1*0*06##"],
                "events_after": ["*1*0*06##"],
                "reply_time_s": 0.1,
            },
            {"dir": "rx", "session": "cmd", "frame": "*1*0*06##"},
            {"dir": "result", "ack": True},
            {
                "dir": "tx",
                "session": "cmd",
                "frame": "*#1*07##",
                "ack": True,
                "replies": ["*1*0*07##"],
            },
            {
                "dir": "tx",
                "session": "cmd",
                "frame": "*1*1*06##",
            },
            "skip_non_dict",
            {"dir": "other"},
            {"dir": "rx", "session": "cmd", "frame": ""},
            {"dir": "rx", "session": "cmd", "frame": "*#*1##"},
            {"dir": "rx", "session": "evt", "frame": "*1*1*06##"},
            {
                "dir": "result",
                "ack": True,
                "replies": [],
                "events_after": ["*1*1*06##"],
                "reply_time_s": 0.2,
            },
            {
                "dir": "tx",
                "session": "cmd",
                "frame": "*#4*2*22##",
            },
            {"dir": "rx", "session": "cmd", "frame": "*1*0*06##"},
            {
                "dir": "tx",
                "session": "cmd",
                "frame": "*1*0*06##",
            },
            {"dir": "rx", "session": "evt", "frame": "*1*0*06##"},
            {
                "dir": "result",
                "ack": True,
                "replies": [],
            },
            {
                "dir": "tx",
                "session": "cmd",
                "frame": "*#4*2*22##",
            },
        ],
    }
    exs = check.extract_f461_exchanges(f461_payload, source_id="test_f461")
    assert len(exs) == 6
    assert [e["schema"] for e in exs] == ["A", "A", "B", "C", "B", "C"]

    trace_f = tmp_path / "f461_live.json"
    trace_f.write_text(json.dumps(f461_payload), encoding="utf-8")
    loaded_items = check._load_trace_file(trace_f)
    assert len(loaded_items) == 6

    # load_expected_divergences edge cases and _match_single_item expected_frames
    assert check.load_expected_divergences(tmp_path / "missing.yaml") == frozenset()
    bad_ledger = tmp_path / "bad_ledger.yaml"
    bad_ledger.write_text("- list\n", encoding="utf-8")
    assert check.load_expected_divergences(bad_ledger) == frozenset()
    empty_dict_ledger = tmp_path / "empty_dict.yaml"
    empty_dict_ledger.write_text("other: 1\n", encoding="utf-8")
    assert check.load_expected_divergences(empty_dict_ledger) == frozenset()
    ev_clock = check.EvidenceItem(
        evidence_id="clock_ev",
        tx_input="*#13**0##",
        rx_outputs=("*#13**0*12*00*00*00##",),
        gateway_models=("F454",),
        rx_reply="ack",
    )
    assert check._match_single_item(
        ev_clock,
        ["*#13**0*00*00*00*00##"],
        "F454",
        "020051",
        "out",
        "ack",
        frozenset({"*#13**0##"}),
    ).startswith("agree_bus")


def test_pic_spike_and_plant_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # SpikeResponder
    sr = pic_spike.SpikeResponder(delay_ms=0.0)
    assert sr.respond(b"plain") == []
    assert sr.respond(b"$00399999999\r") == []
    assert sr.respond(b"$00306001200\r") == [b"$017B8061200\r"]
    assert sr.respond(b"$0306001200\r") == [b"$17B8061200\r"]
    assert sr.tick(10.0) == []

    sr_delayed = pic_spike.SpikeResponder(delay_ms=10.0)
    assert sr_delayed.respond(b"$0306001200\r") == []
    assert sr_delayed.respond(b"$0306001500\r") == []
    assert sr_delayed.tick(5.0) == []
    assert sr_delayed.tick(20.0) == [b"$17B8061200\r"]
    assert sr_delayed.tick(25.0) == [b"$17B8061200\r"]
    sr_delayed.reset()

    # run_spike & pic_spike.main mocked
    spec = target.load(
        ROOT / "oracle" / "targets" / "F461" / "020011.yaml", ROOT / "results"
    )
    monkeypatch.setattr(target, "load", lambda p, r: spec)

    class DummySpikeQemu:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(qemu_target, "QemuTarget", DummySpikeQemu)
    monkeypatch.setattr(
        driver,
        "run_suite",
        lambda tgt, suite, restart_every: [
            record.Row("down", f, "ack", "out", ("own:*1*1*06##",))
            for f in pic_spike.SPIKE_FRAMES
        ],
    )
    rows = pic_spike.run_spike(
        Path("target.yaml"), Path("img.zip"), sysroot_path=tmp_path
    )
    assert len(rows) == 7
    rows2 = pic_spike.run_spike(Path("target.yaml"), Path("img.zip"), sysroot_path=None)
    assert len(rows2) == 7
    assert pic_spike.main(["target.yaml", "--image", "img.zip", "--delay-ms", "0"]) == 0

    # plant_index
    assert plant_index.parse_header(["not a comment", "# no_equals key=val"]) == {
        "key": "val"
    }
    plant_tsv = (
        tmp_path
        / "results"
        / "F461"
        / "020011"
        / "oracle"
        / "plant-f461-bench"
        / "plant-f461-bench.tsv"
    )
    plant_tsv.parent.mkdir(parents=True)
    header: dict[str, str] = {
        "product": "F461",
        "version": "020011",
        "image_sha256": "0" * 64,
        "harness": "plant:f461-bench",
        "target_sha256": "0" * 64,
        "adapter": "pty-1",
        "reset": "0",
        "bus": "pty",
        "framer": "line",
        "responder": "pic:plant:f461-bench",
        "settle_ms": "300.0",
        "suite": "plant-f461-bench",
        "suite_sha256": "0" * 64,
        "oracle_version": "1",
    }
    record.write(
        plant_tsv,
        header,
        [
            record.Row(
                "down",
                "*1*1*06##",
                "ack",
                "out",
                (
                    "bus:24 30 30 33 30 36 30 30 31 32 30 30 0d",
                    "bus_rx:24 30 30 38 30 30 0d",
                    "cmd:*1*1*06##",
                    "evt:*1*1*06##",
                ),
            ),
            record.Row("up", "24303137", "-", "silent", ()),
            record.Row("down", "*#1*06##", "ack", "out", ("own:*1*1*06##",)),
        ],
        ordered=True,
    )
    nosuite_tsv = (
        tmp_path
        / "results"
        / "F461"
        / "020011"
        / "oracle"
        / "plant-f461-bench"
        / "nosuite.tsv"
    )
    nosuite_tsv.write_text(
        "# product=F461\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*0*06##\tack\tsilent\t-\n",
        encoding="utf-8",
    )

    idx = plant_index.build_plant_index(tmp_path / "results")
    assert "plant-f461-bench" in idx["suites"]  # type: ignore[operator]
    assert plant_index.parse_plant_output_column("-") == ([], [], [], [])
    assert plant_index.parse_plant_output_column("unknown:1") == ([], [], [], [])
    assert plant_index.fixture_sha256("nonexistent") == ""
    assert plant_index.fixture_sha256("f461-bench.yaml") != ""

    out_idx = tmp_path / "plant_index.json"
    assert (
        plant_index.main(
            ["--results-dir", str(tmp_path / "results"), "--out", str(out_idx)]
        )
        == 0
    )
    assert (
        plant_index.main(
            [
                "--results-dir",
                str(tmp_path / "results"),
                "--out",
                str(out_idx),
                "--check",
            ]
        )
        == 0
    )
    out_idx.write_text("{}", encoding="utf-8")
    assert (
        plant_index.main(
            [
                "--results-dir",
                str(tmp_path / "results"),
                "--out",
                str(out_idx),
                "--check",
            ]
        )
        == 1
    )
    out_idx.unlink()
    assert (
        plant_index.main(
            [
                "--results-dir",
                str(tmp_path / "results"),
                "--out",
                str(out_idx),
                "--check",
            ]
        )
        == 1
    )
