"""Unit tests for tools/pcode_4695.py (synthetic M16C/60 harness; no firmware)."""

from __future__ import annotations

import builtins
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import pcode_4695


@dataclass(frozen=True)
class FakeSpace:
    name: str


@dataclass(frozen=True)
class FakeVarnode:
    space: FakeSpace
    offset: int
    size: int


@dataclass(frozen=True)
class FakeOpCode:
    name: str


@dataclass(frozen=True)
class FakePcodeOp:
    opcode: FakeOpCode
    inputs: tuple[FakeVarnode, ...]
    output: FakeVarnode | None = None


@dataclass(frozen=True)
class FakeTranslation:
    ops: tuple[FakePcodeOp, ...]


CONST = FakeSpace("const")
REG = FakeSpace("register")
UNIQ = FakeSpace("unique")
RAM = FakeSpace("RAM")
BAD = FakeSpace("bogus")


class FakeContext:
    """Deterministic fake pypcode Context mapping PC addresses to P-code programs."""

    def __init__(self, programs: dict[int, tuple[FakePcodeOp, ...]]) -> None:
        self.programs = programs
        self.registers: dict[str, FakeVarnode] = {
            "SP": FakeVarnode(REG, 0x00, 2),
            "R0L": FakeVarnode(REG, 0x04, 1),
            "A0": FakeVarnode(REG, 0x08, 2),
            "R1": FakeVarnode(REG, 0x10, 2),
        }

    def translate(
        self,
        code: bytes,
        *,
        base_address: int = 0,
        max_instructions: int = 1,
    ) -> FakeTranslation:
        del code, max_instructions
        ops = self.programs.get(base_address)
        if ops is None:
            return FakeTranslation(())
        return FakeTranslation(ops)


class FakePypcodeModule:
    __version__ = "4.1.0-fake"

    def __init__(self, programs: dict[int, tuple[FakePcodeOp, ...]]) -> None:
        self.programs = programs
        self.requested_langs: list[str] = []

    def Context(self, langid: str) -> FakeContext:
        self.requested_langs.append(langid)
        return FakeContext(self.programs)


def _blank_rom(reset_pc: int = 0x0E84EF) -> bytes:
    rom = bytearray(b"\xff" * pcode_4695.FLASH_SIZE)
    rom[0x1FFFC:0x1FFFF] = reset_pc.to_bytes(3, "little")
    return bytes(rom)


def _imark(pc: int, size: int = 2) -> FakePcodeOp:
    return FakePcodeOp(FakeOpCode("IMARK"), (FakeVarnode(RAM, pc, size),))


def test_load_pypcode_success_and_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    monkeypatch.setattr(pcode_4695.importlib, "import_module", lambda _name: sentinel)
    assert pcode_4695.load_pypcode() is sentinel

    def fail_import(_name: str) -> object:
        raise ImportError("no pypcode")

    monkeypatch.setattr(pcode_4695.importlib, "import_module", fail_import)
    with pytest.raises(RuntimeError, match="pypcode is required"):
        pcode_4695.load_pypcode()


def test_sxt_and_emulator_rom_validation() -> None:
    assert pcode_4695.sxt(0x7F, 8) == 127
    assert pcode_4695.sxt(0x80, 8) == -128
    assert pcode_4695.sxt(0xFFFF, 16) == -1

    with pytest.raises(ValueError, match="Expected 131072-byte"):
        pcode_4695.M16CEmulator(b"\x00" * 16, pypcode_mod=FakePypcodeModule({}))


def test_emulator_memory_adc_and_hooks() -> None:
    fake_mod = FakePypcodeModule({})
    emu = pcode_4695.M16CEmulator(_blank_rom(), pypcode_mod=fake_mod)
    assert fake_mod.requested_langs == [pcode_4695.SLEIGH_LANGUAGE_ID]

    # Flash writes are read-only no-ops; RAM writes persist
    orig_flash_byte = emu.read_u(0x0E0000, 1)
    emu.write_u(0x0E0000, 1, 0x12)
    assert emu.read_u(0x0E0000, 1) == orig_flash_byte
    emu.write_u(0x1000, 2, 0x1234)
    assert emu.read_u(0x1000, 2) == 0x1234

    # ADC register 0x03C0 uses battery_adc_raw when AN0 is selected (0x03D6 & 7 == 0)
    emu.ram[0x03D6] = 0x00
    emu.battery_adc_raw = 0x0580  # masked to 10-bit 0x0180
    assert emu.read_u(0x03C0, 2) == 0x0180
    emu.ram[0x03D6] = 0x01
    assert emu.read_u(0x03C0, 2) == 0x0200
    assert emu.read_u(0x03C0, 1) == 0x00

    # Push return address helper
    def push_ret(ret_pc: int) -> None:
        emu.set_reg("SP", 0x2000)
        emu.write_u(0x2000, 3, ret_pc)

    # Fast RTS hook (normal)
    push_ret(0x0E9000)
    assert emu.handle_hook(0x0EE60A) is True
    assert emu.pc == 0x0E9000
    assert emu.get_reg("SP") == 0x2003

    # ADC_POLL_HOOK_PC skipped when allow_adc_poll is True, hooked when False
    push_ret(0x0E9004)
    emu.allow_adc_poll = True
    assert emu.handle_hook(pcode_4695.ADC_POLL_HOOK_PC) is False
    emu.allow_adc_poll = False
    assert emu.handle_hook(pcode_4695.ADC_POLL_HOOK_PC) is True
    assert emu.pc == 0x0E9004

    # EEPROM write (valid + invalid bounds)
    push_ret(0x0E9010)
    emu.ram[0x2100:0x2104] = b"ABCD"
    emu.write_u(0x2003, 4, 0x2100)  # src
    emu.write_u(0x2007, 4, 0x0010)  # ee_addr
    emu.write_u(0x200B, 2, 4)  # n
    assert emu.handle_hook(pcode_4695.EEPROM_WRITE_HOOK_PC) is True
    assert emu.get_reg("R0L") == 1
    assert bytes(emu.eeprom[0x10:0x14]) == b"ABCD"

    push_ret(0x0E9012)
    emu.write_u(0x2003, 4, 0x2100)
    emu.write_u(0x2007, 4, pcode_4695.EEPROM_SIZE)
    emu.write_u(0x200B, 2, 4)
    assert emu.handle_hook(pcode_4695.EEPROM_WRITE_HOOK_PC) is True
    assert emu.get_reg("R0L") == 0

    # EEPROM read (valid + invalid bounds)
    push_ret(0x0E9020)
    emu.write_u(0x2003, 4, 0x0010)  # ee_addr
    emu.write_u(0x2007, 4, 0x2200)  # dst
    emu.write_u(0x200B, 2, 4)  # n
    assert emu.handle_hook(pcode_4695.EEPROM_READ_HOOK_PC) is True
    assert emu.get_reg("R0L") == 1
    assert bytes(emu.ram[0x2200:0x2204]) == b"ABCD"

    push_ret(0x0E9022)
    emu.write_u(0x2003, 4, 0x0010)
    emu.write_u(0x2007, 4, 0x2200)
    emu.write_u(0x200B, 2, 0)
    assert emu.handle_hook(pcode_4695.EEPROM_READ_HOOK_PC) is True
    assert emu.get_reg("R0L") == 0

    # UART0 TX hook: $24, $2603, and generic SCS TX frame
    for frame, expected_rx in (
        (b"$24\r", b"$250011\r"),
        (b"$2603\r", b"$26030010\r"),
        (b"$06D2010304C0122001\r", b"$00\r"),
    ):
        push_ret(0x0E9030)
        emu.ram[0x15B2] = len(frame)
        emu.ram[0x12D6 : 0x12D6 + len(frame)] = frame
        assert emu.handle_hook(pcode_4695.UART0_TX_HOOK_PC) is True
        assert emu.rx_queue[-1] == expected_rx
    assert emu.tx_log == ["$24", "$2603", "$06D2010304C0122001"]

    # Non-hook PC returns False
    assert emu.handle_hook(0x0E84EF) is False

    # feed_rx_if_idle validation + delivery + busy skip
    emu.rx_queue = [b"$00\r"]
    emu.ram[0x15AD] = 1
    emu.feed_rx_if_idle()
    assert len(emu.rx_queue) == 1

    emu.ram[0x15AD] = 0
    emu.feed_rx_if_idle()
    assert emu.ram[0x15AD] == 1
    assert emu.ram[0x15B0] == 4
    assert bytes(emu.ram[0x12C2:0x12C6]) == b"$00\r"

    emu.ram[0x15AD] = 0
    emu.rx_queue = [b"BAD"]
    with pytest.raises(ValueError, match="Malformed PIC RX frame"):
        emu.feed_rx_if_idle()

    emu.ram[0x15AD] = 0
    emu.rx_queue = [b"$MISSING_CR"]
    with pytest.raises(ValueError, match="Malformed PIC RX frame"):
        emu.feed_rx_if_idle()


def test_pcode_execution_all_ops_branches_and_lde_fix() -> None:
    r1 = FakeVarnode(REG, 0x10, 2)
    r0l = FakeVarnode(REG, 0x04, 1)
    u0 = FakeVarnode(UNIQ, 0x00, 2)
    u1 = FakeVarnode(UNIQ, 0x04, 1)
    ram_slot = FakeVarnode(RAM, 0x1000, 2)
    c_zero = FakeVarnode(CONST, 0, 1)
    c_one = FakeVarnode(CONST, 1, 1)
    c_two = FakeVarnode(CONST, 2, 1)
    c_five = FakeVarnode(CONST, 5, 1)
    c_neg1 = FakeVarnode(CONST, 0xFF, 1)
    c_0x80 = FakeVarnode(CONST, 0x80, 1)
    c_0x7f = FakeVarnode(CONST, 0x7F, 1)

    programs: dict[int, tuple[FakePcodeOp, ...]] = {
        # Exercise unary, binary, LOAD, STORE, SUBPIECE, CALLOTHER + unique/RAM
        0x0E0100: (
            _imark(0x0E0100, 2),
            FakePcodeOp(FakeOpCode("COPY"), (FakeVarnode(CONST, 0x1234, 2),), u0),
            FakePcodeOp(FakeOpCode("COPY"), (u0,), ram_slot),
            FakePcodeOp(FakeOpCode("COPY"), (ram_slot,), r1),
            FakePcodeOp(FakeOpCode("COPY"), (r1,), u0),
            FakePcodeOp(FakeOpCode("INT_ZEXT"), (c_0x80,), u0),
            FakePcodeOp(FakeOpCode("INT_SEXT"), (c_0x80,), u0),
            FakePcodeOp(FakeOpCode("INT_2COMP"), (c_five,), u1),
            FakePcodeOp(FakeOpCode("INT_NEGATE"), (c_zero,), u1),
            FakePcodeOp(FakeOpCode("BOOL_NEGATE"), (c_zero,), u1),
            FakePcodeOp(FakeOpCode("BOOL_NEGATE"), (c_one,), u1),
            FakePcodeOp(FakeOpCode("POPCOUNT"), (c_0x7f,), u1),
            FakePcodeOp(FakeOpCode("INT_EQUAL"), (c_one, c_one), u1),
            FakePcodeOp(FakeOpCode("INT_EQUAL"), (c_one, c_two), u1),
            FakePcodeOp(FakeOpCode("INT_NOTEQUAL"), (c_one, c_two), u1),
            FakePcodeOp(FakeOpCode("INT_NOTEQUAL"), (c_one, c_one), u1),
            FakePcodeOp(FakeOpCode("INT_LESS"), (c_one, c_two), u1),
            FakePcodeOp(FakeOpCode("INT_LESS"), (c_two, c_one), u1),
            FakePcodeOp(FakeOpCode("INT_LESSEQUAL"), (c_one, c_one), u1),
            FakePcodeOp(FakeOpCode("INT_LESSEQUAL"), (c_two, c_one), u1),
            FakePcodeOp(FakeOpCode("INT_SLESS"), (c_neg1, c_one), u1),
            FakePcodeOp(FakeOpCode("INT_SLESS"), (c_one, c_neg1), u1),
            FakePcodeOp(FakeOpCode("INT_SLESSEQUAL"), (c_neg1, c_neg1), u1),
            FakePcodeOp(FakeOpCode("INT_SLESSEQUAL"), (c_one, c_neg1), u1),
            FakePcodeOp(FakeOpCode("INT_ADD"), (c_five, c_two), u1),
            FakePcodeOp(FakeOpCode("INT_SUB"), (c_five, c_two), u1),
            FakePcodeOp(FakeOpCode("INT_CARRY"), (c_0x80, c_0x80), u1),
            FakePcodeOp(FakeOpCode("INT_CARRY"), (c_one, c_one), u1),
            FakePcodeOp(FakeOpCode("INT_SCARRY"), (c_0x7f, c_one), u1),
            FakePcodeOp(FakeOpCode("INT_SCARRY"), (c_0x80, c_0x80), u1),
            FakePcodeOp(FakeOpCode("INT_SCARRY"), (c_one, c_one), u1),
            FakePcodeOp(FakeOpCode("INT_SBORROW"), (c_0x80, c_one), u1),
            FakePcodeOp(FakeOpCode("INT_SBORROW"), (c_0x7f, c_neg1), u1),
            FakePcodeOp(FakeOpCode("INT_SBORROW"), (c_five, c_two), u1),
            FakePcodeOp(FakeOpCode("INT_XOR"), (c_five, c_two), u1),
            FakePcodeOp(FakeOpCode("INT_AND"), (c_five, c_one), u1),
            FakePcodeOp(FakeOpCode("INT_OR"), (c_five, c_two), u1),
            FakePcodeOp(FakeOpCode("INT_LEFT"), (c_one, c_two), u1),
            FakePcodeOp(FakeOpCode("INT_RIGHT"), (c_five, c_one), u1),
            FakePcodeOp(FakeOpCode("INT_SRIGHT"), (c_0x80, c_one), u1),
            FakePcodeOp(FakeOpCode("INT_MULT"), (c_five, c_two), u1),
            FakePcodeOp(FakeOpCode("INT_DIV"), (c_five, c_two), u1),
            FakePcodeOp(FakeOpCode("INT_DIV"), (c_five, c_zero), u1),
            FakePcodeOp(FakeOpCode("INT_REM"), (c_five, c_two), u1),
            FakePcodeOp(FakeOpCode("INT_REM"), (c_five, c_zero), u1),
            FakePcodeOp(FakeOpCode("INT_SDIV"), (c_neg1, c_two), u1),
            FakePcodeOp(FakeOpCode("INT_SDIV"), (c_neg1, c_zero), u1),
            FakePcodeOp(FakeOpCode("INT_SREM"), (c_neg1, c_two), u1),
            FakePcodeOp(FakeOpCode("INT_SREM"), (c_neg1, c_zero), u1),
            FakePcodeOp(FakeOpCode("BOOL_XOR"), (c_one, c_zero), u1),
            FakePcodeOp(FakeOpCode("BOOL_XOR"), (c_zero, c_one), u1),
            FakePcodeOp(FakeOpCode("BOOL_AND"), (c_one, c_one), u1),
            FakePcodeOp(FakeOpCode("BOOL_AND"), (c_one, c_zero), u1),
            FakePcodeOp(FakeOpCode("BOOL_OR"), (c_zero, c_one), u1),
            FakePcodeOp(FakeOpCode("BOOL_OR"), (c_zero, c_zero), u1),
            FakePcodeOp(FakeOpCode("PIECE"), (c_one, c_two), u0),
            FakePcodeOp(FakeOpCode("SUBPIECE"), (u0, FakeVarnode(CONST, 1, 1)), u1),
            FakePcodeOp(
                FakeOpCode("STORE"), (c_zero, FakeVarnode(CONST, 0x1010, 2), u0)
            ),
            FakePcodeOp(
                FakeOpCode("LOAD"), (c_zero, FakeVarnode(CONST, 0x1010, 2)), u0
            ),
            FakePcodeOp(FakeOpCode("CALLOTHER"), (c_zero,), u1),
            FakePcodeOp(FakeOpCode("CALLOTHER"), (c_zero,), None),
        ),
        # LDE.B disp20[A0] architectural 20-bit address override (bytes at 0x0E0102)
        0x0E0102: (
            _imark(0x0E0102, 5),
            FakePcodeOp(
                FakeOpCode("LOAD"), (c_zero, FakeVarnode(CONST, 0x0000, 2)), r0l
            ),
        ),
        # Const BRANCH, CBRANCH (skip + const + abs), CALL, RETURN
        0x0E0107: (
            _imark(0x0E0107, 1),
            FakePcodeOp(FakeOpCode("BRANCH"), (FakeVarnode(CONST, 2, 1),)),
            FakePcodeOp(FakeOpCode("COPY"), (c_zero,), r0l),  # skipped
            FakePcodeOp(FakeOpCode("CBRANCH"), (FakeVarnode(CONST, 2, 1), c_zero)),
            FakePcodeOp(FakeOpCode("CBRANCH"), (FakeVarnode(CONST, 2, 1), c_one)),
            FakePcodeOp(FakeOpCode("COPY"), (c_zero,), r0l),  # skipped
            FakePcodeOp(FakeOpCode("BRANCH"), (FakeVarnode(RAM, 0x0E0120, 3),)),
        ),
        0x0E0120: (
            _imark(0x0E0120, 1),
            FakePcodeOp(FakeOpCode("CBRANCH"), (FakeVarnode(RAM, 0x0E0130, 3), c_one)),
        ),
        0x0E0130: (
            _imark(0x0E0130, 1),
            FakePcodeOp(FakeOpCode("CALL"), (FakeVarnode(RAM, 0x0E0140, 3),)),
        ),
        0x0E0140: (
            _imark(0x0E0140, 1),
            FakePcodeOp(FakeOpCode("RETURN"), (FakeVarnode(CONST, 0x0E0150, 3),)),
        ),
        # Empty ops (no IMARK -> ilen fallback = 1)
        0x0E0150: (),
        # Unhandled opcode
        0x0E0151: (_imark(0x0E0151, 1), FakePcodeOp(FakeOpCode("FLOAT_ADD"), ())),
        # Unsupported read space
        0x0E0152: (
            _imark(0x0E0152, 1),
            FakePcodeOp(FakeOpCode("COPY"), (FakeVarnode(BAD, 0, 1),), r0l),
        ),
        # Unsupported write space
        0x0E0153: (
            _imark(0x0E0153, 1),
            FakePcodeOp(FakeOpCode("COPY"), (c_one,), FakeVarnode(BAD, 0, 1)),
        ),
        # Missing output varnode
        0x0E0154: (
            _imark(0x0E0154, 1),
            FakePcodeOp(FakeOpCode("COPY"), (c_one,), None),
        ),
    }

    rom = bytearray(_blank_rom(reset_pc=0x0E0100))
    # Place LDE.B disp20[A0] (0x74 0x90 + 3-byte LE disp20 = 0x0E0200) at 0x0E0102
    rom[0x0102:0x0107] = bytes([0x74, 0x90, 0x00, 0x02, 0x0E])
    # Place target byte 0xA5 at 0x0E0200 + A0(0x05) = 0x0E0205
    rom[0x0205] = 0xA5

    emu = pcode_4695.M16CEmulator(bytes(rom), pypcode_mod=FakePypcodeModule(programs))
    emu.set_reg("A0", 0x0005)

    # Step 0x0E0100 (all ops)
    emu.step_instruction()
    assert emu.pc == 0x0E0102
    assert emu.get_reg("R1") == 0x1234
    assert emu.read_u(0x1010, 2) == 0x0102

    # Repeat 0x0E0100 to exercise icache hit
    emu.pc = 0x0E0100
    emu.step_instruction()
    assert emu.pc == 0x0E0102

    # Step 0x0E0102 (LDE.B disp20[A0] 20-bit override)
    emu.step_instruction()
    assert emu.get_reg("R0L") == 0xA5
    assert emu.pc == 0x0E0107

    # Branch chain: 0x0E0107 -> 0x0E0120 -> 0x0E0130 -> 0x0E0140 -> 0x0E0150 -> 0x0E0151
    for expected_pc in (0x0E0120, 0x0E0130, 0x0E0140, 0x0E0150, 0x0E0151):
        emu.step_instruction()
        assert emu.pc == expected_pc

    # Error paths: unhandled op, unsupported read space, unsupported write space
    with pytest.raises(RuntimeError, match="Unhandled PcodeOp FLOAT_ADD"):
        emu.step_instruction()

    emu.pc = 0x0E0152
    with pytest.raises(RuntimeError, match="Unsupported read space bogus"):
        emu.step_instruction()

    emu.pc = 0x0E0153
    with pytest.raises(RuntimeError, match="Unsupported write space bogus"):
        emu.step_instruction()

    emu.pc = 0x0E0154
    with pytest.raises(RuntimeError, match="missing output varnode"):
        emu.step_instruction()

    # Hook path inside step_instruction
    emu.set_reg("SP", 0x2000)
    emu.write_u(0x2000, 3, 0x0E0150)
    emu.pc = 0x0EE60A
    emu.step_instruction()
    assert emu.pc == 0x0E0150


def test_run_main_ticks_and_budget_exhaustion() -> None:
    # Self-loop at 0x0E84EF (main loop PC)
    loop_prog: dict[int, tuple[FakePcodeOp, ...]] = {
        0x0E84EF: (
            _imark(0x0E84EF, 1),
            FakePcodeOp(FakeOpCode("BRANCH"), (FakeVarnode(RAM, 0x0E84EF, 3),)),
        ),
        0x0E8500: (
            _imark(0x0E8500, 1),
            FakePcodeOp(FakeOpCode("BRANCH"), (FakeVarnode(RAM, 0x0E8500, 3),)),
        ),
    }
    emu = pcode_4695.M16CEmulator(
        _blank_rom(reset_pc=0x0E84EF), pypcode_mod=FakePypcodeModule(loop_prog)
    )
    tx, insns = emu.run_main_ticks(loops=3, tick_timers=False)
    assert tx == []
    assert insns == 3

    tx, insns = emu.run_main_ticks(loops=2, tick_timers=True, pulse_battery_adc=True)
    assert tx == []
    assert insns == 2
    assert emu.ram[0x14F8] == 1

    # Non-main-loop self-loop exhausts instruction budget
    emu.pc = 0x0E8500
    with pytest.raises(RuntimeError, match="Instruction budget exhausted"):
        emu.run_main_ticks(loops=1, max_insns=5)

    # Snapshot state formatting (cooling + unknown mode fallback)
    emu.ram[0x17B5] = 1
    emu.ram[0x17B4] = 99
    emu.ram[0x1557] = 0x05
    snap = emu.snapshot_state()
    assert snap["season"] == "cooling"
    assert snap["operating_mode"] == "mode_99"
    assert snap["zone1_local_mode"] == "local_offset"


def test_sanitization_digest_checks_and_cli_build_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Sanitization checks
    pcode_4695.validate_sanitized_json('{"ok": true, "byte": "0x46"}\n')
    with pytest.raises(ValueError, match="Unsanitized hex address"):
        pcode_4695.validate_sanitized_json('{"leak": "0x0E987C"}\n')
    with pytest.raises(ValueError, match="Private filesystem path"):
        pcode_4695.validate_sanitized_json('{"leak": "/mnt/c/secret"}\n')

    # Digest mismatch raises ValueError
    bad_file = tmp_path / "bad.bin"
    bad_file.write_bytes(b"not-the-rom")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        pcode_4695.build_record(bad_file)

    # Build all 4 suites + CLI main() end-to-end with patched digest pins
    loop_prog: dict[int, tuple[FakePcodeOp, ...]] = {
        0x0E84EF: (
            _imark(0x0E84EF, 1),
            FakePcodeOp(FakeOpCode("BRANCH"), (FakeVarnode(RAM, 0x0E84EF, 3),)),
        ),
    }
    rom_bytes = _blank_rom(reset_pc=0x0E84EF)
    flash_bin = tmp_path / "flat_0E0000_0FFFFF.bin"
    fwz_file = tmp_path / "LH4695_AM5875_010103.fwz"
    mot_file = tmp_path / "4695_010103.mot"
    out_json = tmp_path / "record.json"
    flash_bin.write_bytes(rom_bytes)
    fwz_file.write_bytes(b"synthetic-fwz")
    mot_file.write_bytes(b"synthetic-mot")

    monkeypatch.setattr(
        pcode_4695, "EXPECTED_FLASH_SHA256", hashlib.sha256(rom_bytes).hexdigest()
    )
    monkeypatch.setattr(
        pcode_4695, "EXPECTED_FWZ_SHA256", hashlib.sha256(b"synthetic-fwz").hexdigest()
    )
    monkeypatch.setattr(
        pcode_4695, "EXPECTED_MOT_SHA256", hashlib.sha256(b"synthetic-mot").hexdigest()
    )
    fake_mod = FakePypcodeModule(loop_prog)
    monkeypatch.setattr(pcode_4695, "load_pypcode", lambda: fake_mod)

    # Call build_record without optional fwz/mot/runner_commit
    rec_min = pcode_4695.build_record(flash_bin)
    tool_min = rec_min["tool"]
    assert isinstance(tool_min, dict)
    assert "runner_commit" not in tool_min
    assert tool_min["runner_sha256"] == pcode_4695.runner_sha256()

    # Call CLI main() with all options
    rc = pcode_4695.main(
        [
            "--fwz",
            str(fwz_file),
            "--mot",
            str(mot_file),
            "--flash-bin",
            str(flash_bin),
            "--runner-commit",
            "deadbeef",
            "--out",
            str(out_json),
        ]
    )
    assert rc == 0
    stdout = capsys.readouterr().out
    assert "4 suites, 79 steps" in stdout

    loaded = json.loads(out_json.read_text(encoding="utf-8"))
    assert loaded["tool"]["runner_commit"] == "deadbeef"
    assert loaded["tool"]["runner_path"] == "tools/pcode_4695.py"
    assert loaded["tool"]["pypcode_version"] == "4.1.0-fake"
    assert len(loaded["suites"]) == 4
    total_steps = sum(
        len(cfg["steps"])
        for suite in loaded["suites"]
        for cfg in suite["configurations"]
    )
    assert total_steps == 79
    assert builtins.isinstance(loaded["firmware_under_test"], dict)
