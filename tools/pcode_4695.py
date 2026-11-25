#!/usr/bin/env python3
"""pcode_4695 -- deterministic Renesas M16C/60 P-code runner for BTicino 4695 v01.01.03.

Executes the unmodified 128 KB M16C/60 Flash image (`flat_0E0000_0FFFFF.bin`,
unpacked from `LH4695_AM5875_010103.fwz` / `4695_010103.mot`) from hardware
reset using Ghidra's SLEIGH `M16C/60:LE:16:default` translator via `pypcode`,
with controlled peripheral substitutes and the architectural 20-bit
`LDE.B`/`LDE.W disp20[A0]` effective-address adjustment.

Emits a sanitized JSON execution record (with zero Flash/RAM code or variable
addresses, disassembly mnemonics, archive credentials, or host filesystem paths)
for independent verification of controller-side WHO=4 behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import platform
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

SLEIGH_LANGUAGE_ID = "M16C/60:LE:16:default"
FLASH_BASE = 0x0E0000
FLASH_SIZE = 0x20000
RAM_SIZE = 0x100000
EEPROM_SIZE = 0x8000

EXPECTED_FWZ_NAME = "LH4695_AM5875_010103.fwz"
EXPECTED_FWZ_SIZE = 109864
EXPECTED_FWZ_SHA256 = "b01630d5f4d4fb45ee3392279635cde4e3575778fe69afae505d71a7c211a3be"

EXPECTED_MOT_NAME = "4695_010103.mot"
EXPECTED_MOT_SIZE = 350878
EXPECTED_MOT_SHA256 = "3a760549e6c9b376308e29a1a6b46f5bb8f57a1b6f2715fff2f063324588ed01"

EXPECTED_FLASH_NAME = "flat_0E0000_0FFFFF.bin"
EXPECTED_FLASH_SIZE = 131072
EXPECTED_FLASH_SHA256 = (
    "254e3daed3534a4bcbf31a42534f75c3a8148e4e65fdcbcd0ea73aa4c16c91e9"
)

RUNNER_REPOSITORY = "OpenWebNet-HA/own-firmware-oracle"
RUNNER_RELPATH = "tools/pcode_4695.py"
CANONICAL_COMMAND = (
    "python tools/pcode_4695.py "
    "--fwz LH4695_AM5875_010103.fwz "
    "--mot 4695_010103.mot "
    "--flash-bin flat_0E0000_0FFFFF.bin "
    "--out who4-4695-pcode-execution.json"
)

MAIN_LOOP_PCS: frozenset[int] = frozenset({0x0E84EF, 0x0E851B, 0x0E8535})

# Non-protocol hardware wait loops (watchdog/LCD/RTC/keypad/buzzer) and the
# periodic ADC polling entry (0x0EFA38, unhooked during explicit ADC pulse steps).
FAST_RTS_HOOKS: frozenset[int] = frozenset(
    {
        0x0EE60A,
        0x0EE634,
        0x0EE660,
        0x0EE672,
        0x0EE7DC,
        0x0EEB40,
        0x0EEB8E,
        0x0EEBAC,
        0x0EF178,
        0x0EF15C,
        0x0EF254,
        0x0EEE46,
        0x0EEE8A,
        0x0EEEB4,
        0x0EFA38,
        0x0FB264,
        0x0FB2A0,
        0x0FB362,
        0x0FB3DC,
        0x0FB422,
    }
)
ADC_POLL_HOOK_PC = 0x0EFA38
EEPROM_READ_HOOK_PC = 0x0EF6E6
EEPROM_WRITE_HOOK_PC = 0x0EF8D6
UART0_TX_HOOK_PC = 0x0EE61C

MODE_NAMES: dict[int, str] = {
    1: "weekly_program",
    2: "manual",
    3: "daily_holiday",
    4: "vacation",
    5: "timed_manual",
    6: "off",
    7: "heating_protection",
    8: "cooling_protection",
}

Z1_LOCAL_MODE_NAMES: dict[int, str] = {
    0x0E: "following_central",
    0x0F: "local_off",
    0x02: "local_heating_protection",
    0x03: "local_cooling_protection",
}

UNSANITIZED_HEX_RE = re.compile(r"0x[0-9A-Fa-f]{4,}")
FORBIDDEN_PATH_TOKENS: tuple[str, ...] = ("/Users/", "/home/", "/mnt/")


class _SpaceLike(Protocol):
    name: str


class _VarnodeLike(Protocol):
    space: _SpaceLike
    offset: int
    size: int


class _OpCodeLike(Protocol):
    name: str


class _PcodeOpLike(Protocol):
    opcode: _OpCodeLike
    inputs: Sequence[_VarnodeLike]
    output: _VarnodeLike | None


class _TranslationLike(Protocol):
    ops: Sequence[_PcodeOpLike]


class _ContextLike(Protocol):
    registers: Mapping[str, _VarnodeLike]

    def translate(
        self,
        code: bytes,
        *,
        base_address: int = ...,
        max_instructions: int = ...,
    ) -> _TranslationLike: ...


class _PypcodeModuleLike(Protocol):
    __version__: str

    def Context(self, langid: str) -> _ContextLike: ...


def load_pypcode() -> _PypcodeModuleLike:
    """Import optional `pypcode` dependency on demand."""
    try:
        mod: Any = importlib.import_module("pypcode")
    except ImportError as exc:
        raise RuntimeError(
            "pypcode is required to execute M16C/60 firmware "
            "(install pypcode==4.1.0 in your local Python environment)"
        ) from exc
    return mod  # type: ignore[no-any-return]


def sxt(val: int, bits: int) -> int:
    """Sign-extend an unsigned `bits`-wide integer value."""
    sign = 1 << (bits - 1)
    val &= (1 << bits) - 1
    return (val ^ sign) - sign


def _int_carry(a: int, b: int, bits: int, _b_bits: int) -> int:
    return 1 if (a + b) >= (1 << bits) else 0


def _int_scarry(a: int, b: int, bits: int, _b_bits: int) -> int:
    sa, sb = sxt(a, bits), sxt(b, bits)
    total = sa + sb
    lo = -(1 << (bits - 1))
    hi = 1 << (bits - 1)
    return 1 if (total < lo or total >= hi) else 0


def _int_sborrow(a: int, b: int, bits: int, _b_bits: int) -> int:
    sa, sb = sxt(a, bits), sxt(b, bits)
    diff = sa - sb
    lo = -(1 << (bits - 1))
    hi = 1 << (bits - 1)
    return 1 if (diff < lo or diff >= hi) else 0


def _int_sdiv(a: int, b: int, bits: int, _b_bits: int) -> int:
    sa, sb = sxt(a, bits), sxt(b, bits)
    return int(sa / sb) if sb else 0


def _int_srem(a: int, b: int, bits: int, _b_bits: int) -> int:
    sa, sb = sxt(a, bits), sxt(b, bits)
    return (sa - int(sa / sb) * sb) if sb else 0


BinaryOpFn = Callable[[int, int, int, int], int]
UnaryOpFn = Callable[[int, int], int]

_BINARY_OPS: dict[str, BinaryOpFn] = {
    "INT_EQUAL": lambda a, b, _ba, _bb: 1 if a == b else 0,
    "INT_NOTEQUAL": lambda a, b, _ba, _bb: 1 if a != b else 0,
    "INT_LESS": lambda a, b, _ba, _bb: 1 if a < b else 0,
    "INT_LESSEQUAL": lambda a, b, _ba, _bb: 1 if a <= b else 0,
    "INT_SLESS": lambda a, b, ba, _bb: 1 if sxt(a, ba) < sxt(b, ba) else 0,
    "INT_SLESSEQUAL": lambda a, b, ba, _bb: 1 if sxt(a, ba) <= sxt(b, ba) else 0,
    "INT_ADD": lambda a, b, _ba, _bb: a + b,
    "INT_SUB": lambda a, b, _ba, _bb: a - b,
    "INT_CARRY": _int_carry,
    "INT_SCARRY": _int_scarry,
    "INT_SBORROW": _int_sborrow,
    "INT_XOR": lambda a, b, _ba, _bb: a ^ b,
    "INT_AND": lambda a, b, _ba, _bb: a & b,
    "INT_OR": lambda a, b, _ba, _bb: a | b,
    "INT_LEFT": lambda a, b, _ba, _bb: a << b,
    "INT_RIGHT": lambda a, b, _ba, _bb: a >> b,
    "INT_SRIGHT": lambda a, b, ba, _bb: sxt(a, ba) >> b,
    "INT_MULT": lambda a, b, _ba, _bb: a * b,
    "INT_DIV": lambda a, b, _ba, _bb: a // b if b else 0,
    "INT_REM": lambda a, b, _ba, _bb: a % b if b else 0,
    "INT_SDIV": _int_sdiv,
    "INT_SREM": _int_srem,
    "BOOL_XOR": lambda a, b, _ba, _bb: (1 if a else 0) ^ (1 if b else 0),
    "BOOL_AND": lambda a, b, _ba, _bb: 1 if (a and b) else 0,
    "BOOL_OR": lambda a, b, _ba, _bb: 1 if (a or b) else 0,
    "PIECE": lambda a, b, _ba, bb: (a << bb) | b,
}

_UNARY_OPS: dict[str, UnaryOpFn] = {
    "COPY": lambda a, _ba: a,
    "INT_ZEXT": lambda a, _ba: a,
    "INT_SEXT": sxt,
    "INT_2COMP": lambda a, _ba: -a,
    "INT_NEGATE": lambda a, _ba: ~a,
    "BOOL_NEGATE": lambda a, _ba: 1 if a == 0 else 0,
    "POPCOUNT": lambda a, _ba: a.bit_count(),
}


class M16CEmulator:
    """Minimal Renesas M16C/60 P-code interpreter tailored for BTicino 4695 "
    "v01.01.03."""

    def __init__(
        self,
        flat_rom: bytes,
        *,
        pypcode_mod: _PypcodeModuleLike | None = None,
    ) -> None:
        if len(flat_rom) != FLASH_SIZE:
            raise ValueError(
                f"Expected {FLASH_SIZE}-byte M16C/60 Flash image, got {len(flat_rom)}"
            )
        mod = load_pypcode() if pypcode_mod is None else pypcode_mod
        self.ctx = mod.Context(SLEIGH_LANGUAGE_ID)
        self.ram = bytearray(RAM_SIZE)
        self.ram[FLASH_BASE : FLASH_BASE + FLASH_SIZE] = flat_rom
        self.regs = bytearray(0x4000)
        self.eeprom = bytearray(b"\xff" * EEPROM_SIZE)
        self.icache: dict[int, tuple[int, list[_PcodeOpLike]]] = {}
        self.reg_map: dict[str, tuple[int, int]] = {
            name: (vn.offset, vn.size) for name, vn in self.ctx.registers.items()
        }
        self.pc = int.from_bytes(self.ram[0x0FFFFC:0x0FFFFF], "little")
        self.tx_log: list[str] = []
        self.rx_queue: list[bytes] = []
        self.steps = 0
        self.pic_addr_reply = b"$26030010\r"
        self.allow_adc_poll = False

        # SCS bus voltage present (active-low bit 3), no front-panel button pressed
        self.ram[0x03E5] = 0xF7
        self.ram[0x03ED] = 0xFF
        # Neutral rotary-knob reading on non-battery channels; healthy 10-bit AN0 (768)
        self.battery_adc_raw = 0x0300
        self.write_u(0x03C0, 2, 0x0200)
        self.fast_rts_hooks: frozenset[int] = FAST_RTS_HOOKS

    def get_reg(self, name: str) -> int:
        off, sz = self.reg_map[name]
        return int.from_bytes(self.regs[off : off + sz], "little")

    def set_reg(self, name: str, val: int) -> None:
        off, sz = self.reg_map[name]
        self.regs[off : off + sz] = (val & ((1 << (sz * 8)) - 1)).to_bytes(sz, "little")

    def read_u(self, addr: int, sz: int) -> int:
        if addr == 0x03C0 and sz == 2 and (self.ram[0x03D6] & 0x07) == 0:
            return self.battery_adc_raw & 0x03FF
        return int.from_bytes(self.ram[addr : addr + sz], "little")

    def write_u(self, addr: int, sz: int, val: int) -> None:
        if addr >= FLASH_BASE:
            return
        self.ram[addr : addr + sz] = (val & ((1 << (sz * 8)) - 1)).to_bytes(
            sz, "little"
        )

    def pop_rts(self) -> int:
        sp = self.get_reg("SP")
        ret = self.read_u(sp, 3)
        self.set_reg("SP", (sp + 3) & 0xFFFF)
        return ret

    def handle_hook(self, pc: int) -> bool:
        if pc in self.fast_rts_hooks and not (
            pc == ADC_POLL_HOOK_PC and self.allow_adc_poll
        ):
            self.pc = self.pop_rts()
            return True
        if pc == EEPROM_READ_HOOK_PC:
            sp = self.get_reg("SP")
            ee_addr = self.read_u(sp + 3, 4)
            dst = self.read_u(sp + 7, 4)
            n = self.read_u(sp + 11, 2)
            if n > 0 and ee_addr + n <= len(self.eeprom):
                self.ram[dst : dst + n] = self.eeprom[ee_addr : ee_addr + n]
                self.set_reg("R0L", 1)
            else:
                self.set_reg("R0L", 0)
            self.pc = self.pop_rts()
            return True
        if pc == EEPROM_WRITE_HOOK_PC:
            sp = self.get_reg("SP")
            src = self.read_u(sp + 3, 4)
            ee_addr = self.read_u(sp + 7, 4)
            n = self.read_u(sp + 11, 2)
            if n > 0 and ee_addr + n <= len(self.eeprom):
                self.eeprom[ee_addr : ee_addr + n] = self.ram[src : src + n]
                self.set_reg("R0L", 1)
            else:
                self.set_reg("R0L", 0)
            self.pc = self.pop_rts()
            return True
        if pc == UART0_TX_HOOK_PC:
            n = self.ram[0x15B2]
            raw = bytes(self.ram[0x12D6 : 0x12D6 + n])
            line = raw.decode("latin1", errors="replace").rstrip("\r\n")
            self.tx_log.append(line)
            if line == "$24":
                self.rx_queue.append(b"$250011\r")
            elif line.startswith("$26"):
                self.rx_queue.append(self.pic_addr_reply)
            else:
                self.rx_queue.append(b"$00\r")
            self.pc = self.pop_rts()
            return True
        return False

    def feed_rx_if_idle(self) -> None:
        if self.ram[0x15AD] == 0 and self.rx_queue:
            pkt = self.rx_queue.pop(0)
            if not (pkt.startswith(b"$") and pkt.endswith(b"\r")):
                raise ValueError(f"Malformed PIC RX frame: {pkt!r}")
            for i in range(20):
                self.ram[0x12C2 + i] = 0
            self.ram[0x12C2 : 0x12C2 + len(pkt)] = pkt
            self.ram[0x15B0] = len(pkt)
            self.ram[0x15AD] = 1

    def translate_one(self, pc: int) -> tuple[int, list[_PcodeOpLike]]:
        cached = self.icache.get(pc)
        if cached is not None:
            return cached
        tx = self.ctx.translate(
            bytes(self.ram[pc : pc + 16]), base_address=pc, max_instructions=1
        )
        ops = list(tx.ops)
        length = ops[0].inputs[0].size if ops and ops[0].opcode.name == "IMARK" else 1
        res = (length, ops)
        self.icache[pc] = res
        return res

    def _read_vn(self, vn: _VarnodeLike, uniq: dict[int, int]) -> int:
        sname = vn.space.name
        mask = (1 << (vn.size * 8)) - 1
        if sname == "const":
            return vn.offset & mask
        if sname == "register":
            return int.from_bytes(self.regs[vn.offset : vn.offset + vn.size], "little")
        if sname == "unique":
            val = 0
            for b in range(vn.size):
                val |= uniq.get(vn.offset + b, 0) << (8 * b)
            return val & mask
        if sname == "RAM":
            return self.read_u(vn.offset, vn.size)
        raise RuntimeError(f"Unsupported read space {sname}")

    def _write_vn(self, vn: _VarnodeLike, val: int, uniq: dict[int, int]) -> None:
        sname = vn.space.name
        mask = (1 << (vn.size * 8)) - 1
        val &= mask
        if sname == "register":
            self.regs[vn.offset : vn.offset + vn.size] = val.to_bytes(vn.size, "little")
        elif sname == "unique":
            for b in range(vn.size):
                uniq[vn.offset + b] = (val >> (8 * b)) & 0xFF
        elif sname == "RAM":
            self.write_u(vn.offset, vn.size, val)
        else:
            raise RuntimeError(f"Unsupported write space {sname}")

    @staticmethod
    def _require_output(op: _PcodeOpLike) -> _VarnodeLike:
        if op.output is None:
            raise RuntimeError(f"PcodeOp {op.opcode.name} missing output varnode")
        return op.output

    def _exec_branch_op(
        self, op: _PcodeOpLike, uniq: dict[int, int]
    ) -> tuple[int | None, int | None]:
        name = op.opcode.name
        ins = op.inputs
        if name == "BRANCH":
            tgt = ins[0]
            return (
                (sxt(tgt.offset, tgt.size * 8), None)
                if tgt.space.name == "const"
                else (None, tgt.offset)
            )
        if name == "CBRANCH":
            if self._read_vn(ins[1], uniq) == 0:
                return None, None
            tgt = ins[0]
            return (
                (sxt(tgt.offset, tgt.size * 8), None)
                if tgt.space.name == "const"
                else (None, tgt.offset)
            )
        if name in ("BRANCHIND", "CALLIND", "RETURN"):
            return None, self._read_vn(ins[0], uniq)
        if name == "CALL":
            return None, ins[0].offset
        raise RuntimeError(f"Unhandled PcodeOp {name}")

    def _exec_pcode_op(
        self,
        op: _PcodeOpLike,
        *,
        pc: int,
        ilen: int,
        is_lde_disp20: bool,
        uniq: dict[int, int],
    ) -> tuple[int | None, int | None]:
        """Execute one P-code op.

        Returns `(rel_idx_delta, branch_target_pc)` when control transfers,
        or `(None, None)` to advance to the next P-code op.
        """
        name = op.opcode.name
        ins = op.inputs

        if name == "IMARK":
            return None, None
        if name in _UNARY_OPS:
            out = self._require_output(op)
            self._write_vn(
                out,
                _UNARY_OPS[name](self._read_vn(ins[0], uniq), ins[0].size * 8),
                uniq,
            )
            return None, None
        if name in _BINARY_OPS:
            out = self._require_output(op)
            self._write_vn(
                out,
                _BINARY_OPS[name](
                    self._read_vn(ins[0], uniq),
                    self._read_vn(ins[1], uniq),
                    ins[0].size * 8,
                    ins[1].size * 8,
                ),
                uniq,
            )
            return None, None
        if name == "LOAD":
            out = self._require_output(op)
            addr = (
                (self.read_u(pc + ilen - 3, 3) + self.get_reg("A0")) & 0xFFFFF
                if is_lde_disp20
                else self._read_vn(ins[1], uniq)
            )
            self._write_vn(out, self.read_u(addr, out.size), uniq)
            return None, None
        if name == "STORE":
            addr = self._read_vn(ins[1], uniq)
            self.write_u(addr, ins[2].size, self._read_vn(ins[2], uniq))
            return None, None
        if name == "SUBPIECE":
            out = self._require_output(op)
            self._write_vn(
                out, self._read_vn(ins[0], uniq) >> (ins[1].offset * 8), uniq
            )
            return None, None
        if name == "CALLOTHER":
            if op.output is not None:
                self._write_vn(op.output, 0, uniq)
            return None, None
        return self._exec_branch_op(op, uniq)

    def step_instruction(self) -> None:
        pc = self.pc
        if self.handle_hook(pc):
            self.steps += 1
            return

        if pc in MAIN_LOOP_PCS:
            self.feed_rx_if_idle()

        ilen, ops = self.translate_one(pc)
        next_pc = pc + ilen
        uniq: dict[int, int] = {}
        # Stock Ghidra SLEIGH M16C/60:LE:16:default truncates 20-bit disp20 in
        # LDE.B/LDE.W disp20[A0], dst (opcodes 0x74 0x9x / 0x75 0x9x) to 16 bits;
        # compute the architectural 20-bit effective address.
        is_lde_disp20 = (
            self.ram[pc] in (0x74, 0x75) and (self.ram[pc + 1] & 0xF0) == 0x90
        )

        idx = 0
        n_ops = len(ops)
        while idx < n_ops:
            rel_delta, target_pc = self._exec_pcode_op(
                ops[idx],
                pc=pc,
                ilen=ilen,
                is_lde_disp20=is_lde_disp20,
                uniq=uniq,
            )
            if rel_delta is not None:
                idx += rel_delta
                continue
            if target_pc is not None:
                next_pc = target_pc
                break
            idx += 1

        self.pc = next_pc
        self.steps += 1

    def run_main_ticks(
        self,
        loops: int = 40,
        *,
        tick_timers: bool = True,
        pulse_battery_adc: bool = False,
        max_insns: int = 350000,
    ) -> tuple[list[str], int]:
        before_tx = len(self.tx_log)
        before_steps = self.steps
        hits = 0
        budget = max_insns
        prev_adc_poll = self.allow_adc_poll
        self.allow_adc_poll = pulse_battery_adc
        while hits < loops and budget > 0:
            if self.pc in MAIN_LOOP_PCS:
                hits += 1
                if tick_timers:
                    self.ram[0x14D3] = 1
                    self.ram[0x14D4] = 1
                    self.ram[0x0CFC] = 0 if pulse_battery_adc else 1
                    self.ram[0x0CFD] = 0
                    self.ram[0x0D06] = 0
                    self.ram[0x0D07] = 0
                    self.ram[0x0D10] = 0
                    self.ram[0x0D11] = 0
                if pulse_battery_adc and hits == 1:
                    # Fire one periodic timer tick into the unhooked main-loop
                    # ADC backup-battery load-check state machine on AN0.
                    self.ram[0x03D6] = 0
                    self.ram[0x0CFA] = 0
                    self.ram[0x0CFB] = 0
                    self.ram[0x0CFC] = 1
                    self.ram[0x0CFD] = 0
                    self.ram[0x0CFE] = 0
                    self.ram[0x0CFF] = 0
                    self.ram[0x14B0] = 1
                    self.ram[0x14F7] = 0
                    self.ram[0x14F8] = 1
            self.step_instruction()
            budget -= 1
        self.allow_adc_poll = prev_adc_poll
        if budget == 0:
            raise RuntimeError("Instruction budget exhausted")
        return self.tx_log[before_tx:], self.steps - before_steps

    def snapshot_state(self) -> dict[str, object]:
        mode_raw = self.ram[0x17B4]
        z1_mode_raw = self.ram[0x1557]
        cu_half = self.ram[0x153F]
        z1_half = self.ram[0x1554]
        return {
            "configured_base_zone": self.ram[0x14B9],
            "season": "heating" if self.ram[0x17B5] == 0 else "cooling",
            "operating_mode": MODE_NAMES.get(mode_raw, f"mode_{mode_raw}"),
            "weekly_program": self.ram[0x17B9] + 1,
            "vacation_days": self.ram[0x17BA],
            "central_setpoint_half_deg": cu_half,
            "central_setpoint_celsius": round(cu_half / 2.0, 1),
            "zone1_local_mode": Z1_LOCAL_MODE_NAMES.get(z1_mode_raw, "local_offset"),
            "zone1_effective_setpoint_half_deg": z1_half,
            "zone1_effective_setpoint_celsius": round(z1_half / 2.0, 1),
            "remote_control_enabled": bool(self.ram[0x17B8]),
            "battery_fault_level": self.ram[0x14F9],
        }


def boot_controller(
    flat_rom: bytes,
    *,
    za: int = 0,
    zb: int = 1,
    sla: int = 0,
    enabled_zone_slots: tuple[int, ...] = (1, 3, 4),
    ambient_tenths_c: int = 194,
    pypcode_mod: _PypcodeModuleLike | None = None,
) -> tuple[M16CEmulator, dict[str, object]]:
    """Boot the 4695 image from reset and initialize a deterministic baseline state."""
    emu = M16CEmulator(flat_rom, pypcode_mod=pypcode_mod)
    cfg = f"{za}{zb}{sla}".encode("ascii")
    emu.pic_addr_reply = b"$26030" + cfg + b"\r"
    _, boot_insns = emu.run_main_ticks(loops=60, max_insns=900000)

    # Clean deterministic initial plant state:
    # RTC calendar 2026-01-01, Heating season, Manual 16.0 C (half-deg 32),
    # Zone 1 following central, ambient 19.4 C, battery healthy (0).
    emu.ram[0x1517] = 1
    emu.ram[0x1518] = 1
    emu.ram[0x1519] = 26
    emu.ram[0x1557] = 0x0E
    emu.ram[0x17B5] = 0
    emu.ram[0x17B4] = 2
    emu.ram[0x17B9] = 0
    emu.ram[0x17BA] = 0
    emu.ram[0x153F] = 32
    emu.ram[0x1554] = 32
    emu.write_u(0x1552, 2, ambient_tenths_c)
    emu.ram[0x14F9] = 0
    emu.ram[0x13E2] = 0
    for slot in range(1, 5):
        emu.ram[0x152F + slot * 0x15] = 1 if slot in enabled_zone_slots else 0
    # Seed Slave Zone 3 (slot 3) setpoint = 32 (16.0 C) for C2 readback determinism
    emu.ram[0x152F + 3 * 0x15 + 0x10] = 32
    boot_tx = list(emu.tx_log)
    emu.tx_log.clear()

    base = emu.ram[0x14B9]
    enabled_zones = [base + slot - 1 for slot in enabled_zone_slots]
    init_meta: dict[str, object] = {
        "configurators": {"ZA": za, "ZB": zb, "SLA": sla},
        "pic_configurator_reply": f"$26030{za}{zb}{sla}",
        "boot_scs_tx": boot_tx,
        "rtc_calendar_date": "2026-01-01",
        "enabled_zones": enabled_zones,
        "zone1_ambient_celsius": round(ambient_tenths_c / 10.0, 1),
        "boot_instructions_executed": boot_insns,
        "initial_controller_state": emu.snapshot_state(),
    }
    return emu, init_meta


def run_scs_step(
    emu: M16CEmulator,
    step_id: str,
    description: str,
    pic_rx: str,
    own_equiv: str,
    *,
    loops: int = 35,
) -> dict[str, object]:
    before = emu.snapshot_state()
    emu.rx_queue.append((pic_rx + "\r").encode("ascii"))
    tx, insns = emu.run_main_ticks(loops=loops)
    after = emu.snapshot_state()
    return {
        "step_id": step_id,
        "description": description,
        "stimulus_kind": "scs_bus_rx",
        "stimulus": pic_rx,
        "openwebnet_equivalent": own_equiv,
        "instructions_executed": insns,
        "observed_scs_tx": tx,
        "state_before": before,
        "state_after": after,
    }


def run_adc_step(
    emu: M16CEmulator,
    step_id: str,
    description: str,
    adc_raw_10bit: int,
    samples: int = 1,
) -> dict[str, object]:
    before = emu.snapshot_state()
    emu.battery_adc_raw = adc_raw_10bit
    all_tx: list[str] = []
    total_insns = 0
    for _ in range(samples):
        tx, insns = emu.run_main_ticks(loops=15, pulse_battery_adc=True)
        all_tx.extend(tx)
        total_insns += insns
    after = emu.snapshot_state()
    return {
        "step_id": step_id,
        "description": description,
        "stimulus_kind": "adc_battery_load_check",
        "stimulus": f"adc_10bit_raw={adc_raw_10bit};samples={samples}",
        "openwebnet_equivalent": "internal periodic backup-battery load test",
        "instructions_executed": total_insns,
        "observed_scs_tx": all_tx,
        "state_before": before,
        "state_after": after,
    }


def build_suite_1_addressing(
    flat_rom: bytes,
    *,
    pypcode_mod: _PypcodeModuleLike | None = None,
) -> dict[str, object]:
    emu1, meta1 = boot_controller(
        flat_rom,
        za=0,
        zb=1,
        sla=0,
        enabled_zone_slots=(1, 3, 4),
        pypcode_mod=pypcode_mod,
    )
    steps = [
        run_scs_step(
            emu1,
            "addr-01",
            "Central #0#1 C0 status poll on configured base zone 1",
            "$17990130C0",
            "*#4*#0#1## (Gen B/C/D)",
        ),
        run_scs_step(
            emu1,
            "addr-02",
            "Broadcast central status poll B1=00 rejected by 4695",
            "$17990030C0",
            "*#4*#0## or Gen A *#4*#0#1##",
        ),
        run_scs_step(
            emu1,
            "addr-03",
            "Broadcast central C1 write B1=00 rejected by 4695",
            "$18D1000302C1022400",
            "*#4*#0*#14*0180*3## or Gen A *#4*#0#1*#14*0180*3##",
        ),
        run_scs_step(
            emu1,
            "addr-04",
            "Central C1 write targeting non-matching base zone B1=02 rejected",
            "$18D1020302C1022400",
            "*#4*#0#2*#14*0180*3##",
        ),
        run_scs_step(
            emu1,
            "addr-05",
            "Direct Heating setpoint write $04 90 to built-in master Zone 1 ignored",
            "$179001302A",
            "*#4*1*#14*0210*1## (Gen C/D)",
        ),
        run_scs_step(
            emu1,
            "addr-06",
            "Direct Cooling setpoint write $04 91 to built-in master Zone 1 ignored",
            "$179101302A",
            "*#4*1*#14*0210*2## (Gen C/D)",
        ),
        run_scs_step(
            emu1,
            "addr-07",
            "Direct Generic setpoint write $04 92 to built-in master Zone 1 ignored",
            "$179201302A",
            "*#4*1*#14*0210*3## (Gen C/D)",
        ),
        run_scs_step(
            emu1,
            "addr-08",
            "Direct OFF write $04 93 0F to built-in master Zone 1 ignored",
            "$179301300F",
            "*4*303*1## (Gen C/D)",
        ),
        run_scs_step(
            emu1,
            "addr-09",
            "Direct Protection write $04 93 02 to built-in master Zone 1 ignored",
            "$1793013002",
            "*4*102*1## / *4*302*1## (Gen C/D)",
        ),
        run_scs_step(
            emu1,
            "addr-10",
            "Direct AUTO write $04 93 00 to built-in master Zone 1 ignored",
            "$1793013000",
            "*4*311*1## (Gen C/D)",
        ),
        run_scs_step(
            emu1,
            "addr-11",
            "99-zone central-zone write B1=00, B3=02, B4=C2 ignored",
            "$18D1000302C201021E",
            "*#4*#1*#14*0150*3##",
        ),
        run_scs_step(
            emu1,
            "addr-12",
            "Four-zone central-zone write B1=01, B3=02, B4=C2 on #3#1 ignored",
            "$18D1010302C2030223",
            "*#4*#3#1*#14*0175*3##",
        ),
        run_scs_step(
            emu1,
            "addr-13",
            "Four-zone central-zone write B1=01, B3=02, B4=C2 on #1#1 ignored",
            "$18D1010302C2010224",
            "*#4*#1#1*#14*0180*3##",
        ),
        run_scs_step(
            emu1,
            "addr-14",
            "Central *4*311*#0#1## write B1=01, B3=02, B4=C2 ignored",
            "$18D1010302C2000000",
            "*4*311*#0#1## (Gen B/C/D)",
        ),
        run_scs_step(
            emu1,
            "addr-15",
            (
                "Read-only controlled-zone query B1=01, B3=03, B4=C2 on #3#1 answered "
                "with D2 C2"
            ),
            "$18D1010303C2030000",
            "*#4*#3#1## (Gen B/C/D)",
        ),
    ]

    emu2, meta2 = boot_controller(
        flat_rom,
        za=0,
        zb=2,
        sla=0,
        enabled_zone_slots=(1, 2, 3, 4),
        pypcode_mod=pypcode_mod,
    )
    steps_cu2 = [
        run_scs_step(
            emu2,
            "addr-16",
            "On #0#2 (ZA=0, ZB=2), direct $04 92 write to built-in Zone 2 ignored",
            "$1792023024",
            "*#4*2*#14*0180*3## (Gen C/D)",
        ),
        run_scs_step(
            emu2,
            "addr-17",
            (
                "On #0#2, central C1 write updates CU=02 and pushes slave overrides to "
                "zones 3..5"
            ),
            "$18D1020302C1022400",
            "*#4*#0#2*#14*0180*3## (Gen B/C/D)",
        ),
    ]

    emu21, meta21 = boot_controller(
        flat_rom,
        za=2,
        zb=1,
        sla=0,
        enabled_zone_slots=(1, 2, 3, 4),
        pypcode_mod=pypcode_mod,
    )
    steps_cu21 = [
        run_scs_step(
            emu21,
            "addr-18",
            (
                "On #0#21 (ZA=2, ZB=1 -> hex CU=15), central C0 status poll answered "
                "on CU=15"
            ),
            "$17991530C0",
            "*#4*#0#21## (Gen B/C/D)",
        ),
        run_scs_step(
            emu21,
            "addr-19",
            (
                "On #0#21, central C1 write updates CU=15 and pushes slave overrides "
                "to hex 16..18 (zones 22..24)"
            ),
            "$18D1150302C1022400",
            "*#4*#0#21*#14*0180*3## (Gen B/C/D)",
        ),
    ]

    return {
        "suite_id": "suite:addressing-and-c2-boundaries",
        "summary": (
            "Base address C = ZA*10 + ZB (#0#1, #0#2, #0#21), broadcast 00 rejection, "
            "built-in master Zone C $04 90..93 write drop, and B3=02 B4=C2 write "
            "absence vs B3=03 B4=C2 read query"
        ),
        "claim_ids": [
            "ownkb:claim:c074201",
            "ownkb:claim:c074202",
            "ownkb:claim:c074203",
            "ownkb:claim:c074204",
        ],
        "configurations": [
            {"configuration_id": "cu-01", **meta1, "steps": steps},
            {"configuration_id": "cu-02", **meta2, "steps": steps_cu2},
            {"configuration_id": "cu-21", **meta21, "steps": steps_cu21},
        ],
    }


def build_suite_2_c1_modes_holiday_vacation(
    flat_rom: bytes,
    *,
    pypcode_mod: _PypcodeModuleLike | None = None,
) -> dict[str, object]:
    emu, meta = boot_controller(
        flat_rom,
        za=0,
        zb=1,
        sla=0,
        enabled_zone_slots=(1, 3, 4),
        pypcode_mod=pypcode_mod,
    )
    steps = [
        run_scs_step(
            emu,
            "c1-01",
            "Select Heating weekly program 3 (*4*1103*#0#1##) in Heating season",
            "$18D1010302C1110200",
            "*4*1103*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-02",
            (
                "Cooling weekly program 2 (*4*2102*#0#1##) silently dropped in Heating "
                "season"
            ),
            "$18D1010302C1210100",
            "*4*2102*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-03",
            "Out-of-range weekly program index B6=03 (>= 3) silently dropped",
            "$18D1010302C1010300",
            "synthetic out-of-range B6=03",
        ),
        run_scs_step(
            emu,
            "c1-04",
            "Unmapped B5=00 (C1 00 FF 00) silently dropped",
            "$18D1010302C100FF00",
            "unmapped B5=0x00",
        ),
        run_scs_step(
            emu,
            "c1-05",
            "Cooling OFF (*4*203*#0#1##, C1 26) silently dropped in Heating season",
            "$18D1010302C1260000",
            "*4*203*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-06",
            "Generic OFF (*4*303*#0#1##, C1 06) enters OFF in Heating season",
            "$18D1010302C1060000",
            "*4*303*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-07",
            (
                "Same-season Heating command (*4*1*#0#1##, C1 F0) while OFF is a "
                "silent no-op leaving OFF active"
            ),
            "$18D1010302C1F00000",
            "*4*1*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-08",
            (
                "Active-program resume (*4*3100*#0#1##, C1 01 FF 00) wakes from OFF "
                "into stored weekly program 3"
            ),
            "$18D1010302C101FF00",
            "*4*3100*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-09",
            (
                "Cross-season switch to Cooling (*4*0*#0#1##, C1 F1) transitions into "
                "Cooling Protection (C0 27)"
            ),
            "$18D1010302C1F10000",
            "*4*0*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-10",
            (
                "Heating weekly program 1 (*4*1101*#0#1##, C1 11 00) silently dropped "
                "in Cooling season"
            ),
            "$18D1010302C1110000",
            "*4*1101*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-11",
            (
                "Generic weekly program 2 (*4*3102*#0#1##, C1 01 01) accepted in "
                "Cooling season"
            ),
            "$18D1010302C1010100",
            "*4*3102*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-12",
            "Cooling OFF (*4*203*#0#1##, C1 26) accepted in Cooling season",
            "$18D1010302C1260000",
            "*4*203*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-13",
            (
                "Same-season Cooling command (*4*0*#0#1##, C1 F1) while OFF is a "
                "silent no-op leaving OFF active"
            ),
            "$18D1010302C1F10000",
            "*4*0*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-14",
            (
                "Active-program resume (*4*3100*#0#1##, C1 01 FF 00) in Cooling "
                "resumes stored weekly program 2 (C0 21 01)"
            ),
            "$18D1010302C101FF00",
            "*4*3100*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-15",
            (
                "Cross-season switch back to Heating (*4*1*#0#1##, C1 F0) transitions "
                "into Heating Protection (C0 17)"
            ),
            "$18D1010302C1F00000",
            "*4*1*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-16",
            (
                "Scenario commands 1201 / 3200 (B5=13 / 03) are unimplemented and "
                "silently dropped"
            ),
            "$18D1010302C1130000",
            "*4*1201*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-17",
            (
                "Scenario last-active 3200 (B5=03, B6=FF) is unimplemented and "
                "silently dropped"
            ),
            "$18D1010302C103FF00",
            "*4*3200*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-18",
            (
                "Cooling daily Holiday (*4*215#2101*#0#1##, C1 24 00) silently dropped "
                "in Heating season"
            ),
            "$18D1010302C1240000",
            "*4*215#2101*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-19",
            (
                "Heating daily Holiday (*4*115#1102*#0#1##, C1 14 01) activates daily "
                "Holiday with weekly return program 2"
            ),
            "$18D1010302C1140100",
            "*4*115#1102*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-20",
            (
                "Generic daily Holiday (*4*315#3103*#0#1##, C1 04 02) updates daily "
                "Holiday weekly return program to 3"
            ),
            "$18D1010302C1040200",
            "*4*315#3103*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-21",
            (
                "Vacation-exit B5=05, B6=00 (*4*3000*#0#1##) while in daily Holiday "
                "(not multi-day vacation) is ignored"
            ),
            "$18D1010302C10500FF",
            "*4*3000*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-22",
            (
                "Cooling multi-day vacation (*4*23005#2102*#0#1##, C1 25 05 01) "
                "silently dropped in Heating season"
            ),
            "$18D1010302C1250501",
            "*4*23005#2102*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-23",
            (
                "Multi-day vacation entry with out-of-range return program B7=03 (>= "
                "3) silently dropped"
            ),
            "$18D1010302C1150503",
            "synthetic out-of-range B7=03 on vacation entry",
        ),
        run_scs_step(
            emu,
            "c1-24",
            (
                "Heating 5-day vacation (*4*13005#1102*#0#1##, C1 15 05 01) enters "
                "vacation and broadcasts C0 15 06 (remaining period includes current "
                "day)"
            ),
            "$18D1010302C1150501",
            "*4*13005#1102*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-25",
            (
                "Vacation exit (*4*3000*#0#1##, C1 05 00 FF) exits active vacation "
                "back to stored weekly program 2 (C0 11 01)"
            ),
            "$18D1010302C10500FF",
            "*4*3000*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-26",
            (
                "Vacation exit (*4*3000*#0#1##, C1 05 00 FF) repeated when already in "
                "weekly program is ignored"
            ),
            "$18D1010302C10500FF",
            "*4*3000*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-27",
            (
                "Generic 3-day vacation (*4*33003#3101*#0#1##, C1 05 03 00) enters "
                "vacation with return program 1 and broadcasts C0 15 04 (remaining "
                "period includes current day)"
            ),
            "$18D1010302C1050300",
            "*4*33003#3101*#0#1##",
        ),
        run_scs_step(
            emu,
            "c1-28",
            (
                "Vacation exit with explicit program (*4*3000#3103*#0#1##, C1 05 00 "
                "02) exits active vacation into weekly program 3 (C0 11 02)"
            ),
            "$18D1010302C1050002",
            "*4*3000#3103*#0#1##",
        ),
    ]
    return {
        "suite_id": "suite:c1-modes-holiday-vacation-and-season",
        "summary": (
            "C1 season filter, *4*3100*#0#C## active-program resume, "
            "same-season/cross-season *4*1/0*#0#C##, daily Holiday 115#..315# return "
            "program, multi-day vacation 13DDD#P..33DDD#P entry and 3000 / 3000#P "
            "exit, and unimplemented 1201..3216 / 3200 scenarios"
        ),
        "claim_ids": [
            "ownkb:claim:c074207",
            "ownkb:claim:c074208",
            "ownkb:claim:c074209",
            "ownkb:claim:c074210",
        ],
        "configurations": [
            {"configuration_id": "cu-01-modes", **meta, "steps": steps},
        ],
    }


def build_suite_3_adc_battery_and_flags(
    flat_rom: bytes,
    *,
    pypcode_mod: _PypcodeModuleLike | None = None,
) -> dict[str, object]:
    emu, meta = boot_controller(
        flat_rom,
        za=0,
        zb=1,
        sla=0,
        enabled_zone_slots=(1, 3, 4),
        pypcode_mod=pypcode_mod,
    )
    steps = [
        run_scs_step(
            emu,
            "adc-01",
            (
                "Baseline C0 poll with healthy 10-bit battery ADC (768): flags=01 "
                "(*4*21*#0#1## only, bit 2 clear)"
            ),
            "$17990130C0",
            "*#4*#0#1##",
        ),
        run_adc_step(
            emu,
            "adc-02",
            (
                "Single low-battery 10-bit ADC sample (384 in 246..505): 2-sample "
                "debounce holds battery_fault_level=0"
            ),
            384,
            samples=1,
        ),
        run_adc_step(
            emu,
            "adc-03",
            (
                "Second consecutive low-battery 10-bit ADC sample (384): sets "
                "battery_fault_level=1"
            ),
            384,
            samples=1,
        ),
        run_scs_step(
            emu,
            "adc-04",
            (
                "C0 poll after 2-sample low-battery ADC detection: flags=05 (0x01 | "
                "0x04 -> *4*21* + *4*31*#0#1##)"
            ),
            "$17990130C0",
            "*#4*#0#1##",
        ),
        run_adc_step(
            emu,
            "adc-05",
            (
                "Two consecutive healthy recovery ADC samples (592 > 516): clears "
                "battery_fault_level=0"
            ),
            592,
            samples=2,
        ),
        run_scs_step(
            emu,
            "adc-06",
            "C0 poll after battery ADC recovery: flags=01 (bit 2 cleared)",
            "$17990130C0",
            "*#4*#0#1##",
        ),
        run_adc_step(
            emu,
            "adc-07",
            (
                "Two consecutive discharged/missing-battery ADC samples (128 < 246): "
                "sets battery_fault_level=2"
            ),
            128,
            samples=2,
        ),
        run_scs_step(
            emu,
            "adc-08",
            "Slave Zone 3 reports Local OFF ($17 B7 03 30 FF)",
            "$17B70330FF",
            "slave Zone 3 *4*303*3## bus report",
        ),
        run_scs_step(
            emu,
            "adc-09",
            (
                "C0 poll shows flags=0D (0x01 remote + 0x04 battery *4*31* + 0x08 "
                "slave Local OFF *4*22*)"
            ),
            "$17990130C0",
            "*#4*#0#1##",
        ),
        run_scs_step(
            emu,
            "adc-10",
            "Slave Zone 3 reports Local Protection ($17 B7 03 30 F2)",
            "$17B70330F2",
            "slave Zone 3 *4*102*3## bus report",
        ),
        run_scs_step(
            emu,
            "adc-11",
            (
                "C0 poll shows flags=15 (0x01 remote + 0x04 battery *4*31* + 0x10 "
                "slave Local Protection *4*23*)"
            ),
            "$17990130C0",
            "*#4*#0#1##",
        ),
        run_scs_step(
            emu,
            "adc-12",
            (
                "Slave Zone 3 returns to active mode ($17 B7 03 30 F0), clearing slave "
                "override flags"
            ),
            "$17B70330F0",
            "slave Zone 3 *4*1*3## bus report",
        ),
        run_scs_step(
            emu,
            "adc-13",
            (
                "C0 poll after slave Zone 3 returns to active mode shows flags=05 "
                "(0x01 remote + 0x04 battery *4*31*)"
            ),
            "$17990130C0",
            "*#4*#0#1##",
        ),
    ]
    return {
        "suite_id": "suite:adc-battery-and-c0-status-flags",
        "summary": (
            "Periodic 10-bit ADC backup-battery load test with 2-sample debounce "
            "setting C0 B7 bit 2 (*4*31*#0#C##) and slave-probe local-override "
            "status bits (*4*22/23/24*#0#C##)"
        ),
        "claim_ids": ["ownkb:claim:c074211"],
        "configurations": [
            {"configuration_id": "cu-01-adc-flags", **meta, "steps": steps},
        ],
    }


def build_suite_4_dim14_bounds_and_queries(
    flat_rom: bytes,
    *,
    pypcode_mod: _PypcodeModuleLike | None = None,
) -> dict[str, object]:
    emu, meta = boot_controller(
        flat_rom,
        za=0,
        zb=1,
        sla=0,
        enabled_zone_slots=(1, 3, 4),
        pypcode_mod=pypcode_mod,
    )
    steps = [
        run_scs_step(
            emu,
            "dim14-01",
            "Minimum gateway manual setpoint 5.0 C (B6=0A, M=3) accepted",
            "$18D1010302C1020A00",
            "*#4*#0#1*#14*0050*3##",
        ),
        run_scs_step(
            emu,
            "dim14-02",
            (
                "Upper-bound manual setpoint 35.0 C (B6=46, M=3) accepted by 4695 (B6 "
                "<= 0x46)"
            ),
            "$18D1010302C1024600",
            "*#4*#0#1*#14*0350*3##",
        ),
        run_scs_step(
            emu,
            "dim14-03",
            (
                "Above-bound manual setpoint 35.5 C (B6=47, M=3) silently dropped; "
                "setpoint remains 35.0 C"
            ),
            "$18D1010302C1024700",
            "*#4*#0#1*#14*0355*3##",
        ),
        run_scs_step(
            emu,
            "dim14-04",
            (
                "Gateway maximum manual setpoint 40.0 C (B6=50, M=3) silently dropped; "
                "setpoint remains 35.0 C"
            ),
            "$18D1010302C1025000",
            "*#4*#0#1*#14*0400*3##",
        ),
        run_scs_step(
            emu,
            "dim14-05",
            "Heating M=1 setpoint 17.5 C (B5=12, B6=23) accepted in Heating season",
            "$18D1010302C1122300",
            "*#4*#0#1*#14*0175*1##",
        ),
        run_scs_step(
            emu,
            "dim14-06",
            (
                "Cooling M=2 setpoint 20.0 C (B5=22, B6=28) silently dropped in "
                "Heating season"
            ),
            "$18D1010302C1222800",
            "*#4*#0#1*#14*0200*2##",
        ),
        run_scs_step(
            emu,
            "dim14-07",
            "Switch to Cooling (*4*0*#0#1##, C1 F1) -> Cooling Protection",
            "$18D1010302C1F10000",
            "*4*0*#0#1##",
        ),
        run_scs_step(
            emu,
            "dim14-08",
            (
                "Heating M=1 setpoint 20.0 C (B5=12, B6=28) silently dropped in "
                "Cooling season"
            ),
            "$18D1010302C1122800",
            "*#4*#0#1*#14*0200*1##",
        ),
        run_scs_step(
            emu,
            "dim14-09",
            "Cooling M=2 setpoint 20.0 C (B5=22, B6=28) accepted in Cooling season",
            "$18D1010302C1222800",
            "*#4*#0#1*#14*0200*2##",
        ),
        run_scs_step(
            emu,
            "dim14-10",
            (
                "Generic M=3 setpoint 18.0 C (B5=02, B6=24) accepted in Cooling season "
                "and emits Zone 1 D2 12 when setpoint changes"
            ),
            "$18D1010302C1022400",
            "*#4*#0#1*#14*0180*3##",
        ),
        run_scs_step(
            emu,
            "dim14-11",
            (
                "Re-sending identical M=3 setpoint 18.0 C (mode and Zone 1 effective "
                "setpoint unchanged) omits Zone 1 D2 12 while pushing slave $04 91"
            ),
            "$18D1010302C1022400",
            "*#4*#0#1*#14*0180*3##",
        ),
        run_scs_step(
            emu,
            "dim14-12",
            "Extended central C0 query $18 D1 01 03 03 C0 is unimplemented and ignored",
            "$18D1010303C0000000",
            "synthetic extended C0 read",
        ),
        run_scs_step(
            emu,
            "dim14-13",
            (
                "Extended central DIMENSION 14 query $18 D1 01 03 03 14 is "
                "unimplemented and ignored"
            ),
            "$18D101030314000000",
            "synthetic extended DIM 14 read",
        ),
        run_scs_step(
            emu,
            "dim14-14",
            (
                "Short Zone 1 DIMENSION 14 query $17 99 01 30 0E answered with D2 0E "
                "(used by *#4*1*14## and Gen D *#4*#0#1*14##)"
            ),
            "$179901300E",
            "*#4*1*14## / Gen D *#4*#0#1*14##",
        ),
        run_scs_step(
            emu,
            "dim14-15",
            (
                "Short Zone 1 status query $17 99 01 30 11 (*#4*1##) answered with B7 "
                "mode + D2 12"
            ),
            "$1799013011",
            "*#4*1##",
        ),
        run_scs_step(
            emu,
            "dim14-16",
            (
                "Short Zone 1 ambient query $17 99 01 30 00 (*#4*1*0##) answered with "
                "B4 ambient"
            ),
            "$1799013000",
            "*#4*1*0##",
        ),
        run_scs_step(
            emu,
            "dim14-17",
            (
                "Short Zone 1 DIMENSION 12 query $17 99 01 30 12 (*#4*1*12##) answered "
                "with D2 12"
            ),
            "$1799013012",
            "*#4*1*12##",
        ),
        run_scs_step(
            emu,
            "dim14-18",
            (
                "Short Zone 1 DIMENSION 13 query $17 99 01 30 0D (*#4*1*13##) answered "
                "with D2 0D"
            ),
            "$179901300D",
            "*#4*1*13##",
        ),
        run_scs_step(
            emu,
            "dim14-19",
            (
                "Short Zone 1 DIMENSION 19 query $17 99 01 30 13 (*#4*1*19##) answered "
                "with D2 13"
            ),
            "$1799013013",
            "*#4*1*19##",
        ),
    ]
    return {
        "suite_id": "suite:dim14-bounds-queries-and-zone-sync",
        "summary": (
            "DIMENSION 14 manual-setpoint 5.0..35.0 C (B6 = 0x0A..0x46) acceptance "
            "and 35.5..40.0 C (0x47..0x50) drop, M=1/2/3 season filtering, Zone 1 vs "
            "#0#1 read queries, and conditional Zone C DIMENSION 12 emission"
        ),
        "claim_ids": [
            "ownkb:claim:c074212",
            "ownkb:claim:c074213",
            "ownkb:claim:c074214",
        ],
        "configurations": [
            {"configuration_id": "cu-01-dim14", **meta, "steps": steps},
        ],
    }


def _verify_pinned_digest(path: Path, expected_sha256: str, label: str) -> bytes:
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise ValueError(
            f"{label} SHA-256 mismatch: expected {expected_sha256}, got {actual}"
        )
    return data


def validate_sanitized_json(serialized: str) -> None:
    """Reject leaked 4+ hex-digit addresses or host filesystem paths."""
    bad_hex = UNSANITIZED_HEX_RE.findall(serialized)
    if bad_hex:
        raise ValueError(f"Unsanitized hex address leaked: {bad_hex}")
    for token in FORBIDDEN_PATH_TOKENS:
        if token in serialized:
            raise ValueError(f"Private filesystem path token leaked: {token}")


def runner_sha256() -> str:
    """Return the SHA-256 hex digest of this runner script."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _count_steps(suites: list[dict[str, Any]]) -> int:
    return sum(len(cfg["steps"]) for suite in suites for cfg in suite["configurations"])


def build_record(
    flash_bin: Path,
    *,
    fwz_path: Path | None = None,
    mot_path: Path | None = None,
    runner_commit: str | None = None,
    pypcode_mod: _PypcodeModuleLike | None = None,
) -> dict[str, Any]:
    """Verify input digests, run all four 4695 suites, and assemble the record."""
    if fwz_path is not None:
        _verify_pinned_digest(fwz_path, EXPECTED_FWZ_SHA256, EXPECTED_FWZ_NAME)
    if mot_path is not None:
        _verify_pinned_digest(mot_path, EXPECTED_MOT_SHA256, EXPECTED_MOT_NAME)
    flat_bytes = _verify_pinned_digest(
        flash_bin, EXPECTED_FLASH_SHA256, EXPECTED_FLASH_NAME
    )

    mod = load_pypcode() if pypcode_mod is None else pypcode_mod
    suites = [
        build_suite_1_addressing(flat_bytes, pypcode_mod=mod),
        build_suite_2_c1_modes_holiday_vacation(flat_bytes, pypcode_mod=mod),
        build_suite_3_adc_battery_and_flags(flat_bytes, pypcode_mod=mod),
        build_suite_4_dim14_bounds_and_queries(flat_bytes, pypcode_mod=mod),
    ]

    tool_meta: dict[str, object] = {
        "engine": "pypcode",
        "pypcode_version": mod.__version__,
        "sleigh_language_id": SLEIGH_LANGUAGE_ID,
        "python_runtime": f"CPython {platform.python_version()}",
        "runner_repository": RUNNER_REPOSITORY,
        "runner_path": RUNNER_RELPATH,
        "runner_sha256": runner_sha256(),
        "command": CANONICAL_COMMAND,
    }
    if runner_commit is not None:
        tool_meta["runner_commit"] = runner_commit

    return {
        "schema_version": "1.0.0",
        "record_id": "who4-4695-010103-m16c-pcode-execution",
        "method": "dynamic_firmware_oracle",
        "material": "firmware_oracle",
        "tool": tool_meta,
        "firmware_under_test": {
            "source_id": "ownkb:source:s001226",
            "device": (
                "BTicino 4695 (HC/HS/L/N/NT4695, AM5875) "
                "four-zone temperature central unit"
            ),
            "firmware_version": "01.01.03",
            "archive_name": EXPECTED_FWZ_NAME,
            "archive_size_bytes": EXPECTED_FWZ_SIZE,
            "archive_sha256": EXPECTED_FWZ_SHA256,
            "srecord_name": EXPECTED_MOT_NAME,
            "srecord_size_bytes": EXPECTED_MOT_SIZE,
            "srecord_sha256": EXPECTED_MOT_SHA256,
            "flash_image_name": EXPECTED_FLASH_NAME,
            "flash_image_size_bytes": EXPECTED_FLASH_SIZE,
            "flash_image_sha256": EXPECTED_FLASH_SHA256,
        },
        "conditions": [
            (
                "Executes the unmodified 128 KB M16C/60 Flash image "
                f"(SHA-256 {EXPECTED_FLASH_SHA256}) mapped read-only from hardware "
                "reset through PIC transceiver version ($24 -> $250011) and "
                "configurator ($2603 -> $26030<ZA><ZB><SLA>) initialization."
            ),
            (
                "Peripheral I/O boundaries are deterministic controlled substitutes: "
                "32 KB I2C EEPROM buffer, UART0 PIC frame RX/TX queue returning $00 "
                "TX-OK acknowledgements, fast return stubs for "
                "watchdog/LCD/RTC/keypad/buzzer hardware waits, system-tick "
                "advancement between main-loop iterations so queued zone-sync "
                "broadcasts complete, and controlled 10-bit ADC sample inputs for the "
                "periodic backup-battery load test."
            ),
            (
                "Sanitized publication record: raw Flash/RAM code and variable "
                "addresses, disassembly mnemonics, archive credentials, and host "
                "filesystem paths are omitted."
            ),
        ],
        "limitations": [
            (
                "Establishes instruction-level execution of 4695 Firmware 01.01.03 "
                "under controlled peripheral inputs; does not execute the on-board "
                "PIC transceiver firmware, external slave-probe firmware, or physical "
                "SCS bus hardware."
            ),
            (
                "Gateway OpenWebNet framing and ACK/NACK socket behavior remain "
                "established separately by the 19 pinned own-firmware-oracle gateway "
                "runs."
            ),
            (
                "Stock Ghidra SLEIGH M16C/60:LE:16:default has two translator quirks "
                "accounted for here: LDE.B/LDE.W disp20[A0] truncates disp20 to 16 "
                "bits (resolved by computing the architectural 20-bit disp20 + A0 "
                "address), and CMP.B:G carry uses signed s<= on byte operands (all "
                "tested program and setpoint bounds lie in 0x00..0x7F where signed "
                "and unsigned byte order agree)."
            ),
        ],
        "suites": suites,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Execute BTicino 4695 v01.01.03 M16C/60 Flash under pypcode SLEIGH "
            "and emit a sanitized JSON execution record."
        )
    )
    parser.add_argument(
        "--flash-bin",
        type=Path,
        required=True,
        help="Path to the decoded 128 KB flat_0E0000_0FFFFF.bin Flash image.",
    )
    parser.add_argument(
        "--fwz",
        type=Path,
        default=None,
        help="Optional path to LH4695_AM5875_010103.fwz to verify its SHA-256.",
    )
    parser.add_argument(
        "--mot",
        type=Path,
        default=None,
        help="Optional path to 4695_010103.mot to verify its SHA-256.",
    )
    parser.add_argument(
        "--runner-commit",
        default=None,
        help="Optional Git commit SHA to record in tool.runner_commit.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output path for the sanitized JSON execution record.",
    )
    args = parser.parse_args(argv)

    record = build_record(
        args.flash_bin,
        fwz_path=args.fwz,
        mot_path=args.mot,
        runner_commit=args.runner_commit,
    )
    serialized = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    validate_sanitized_json(serialized)
    args.out.write_text(serialized, encoding="utf-8")

    suites: list[dict[str, Any]] = record["suites"]
    total_steps = _count_steps(suites)
    sys.stdout.write(
        f"Wrote {args.out} ({len(serialized)} bytes, {len(suites)} suites, "
        f"{total_steps} steps)\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
