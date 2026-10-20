# SPDX-License-Identifier: Apache-2.0
"""Clean-room SCS bus telegram and PIC serial frame codec."""

from __future__ import annotations

from dataclasses import dataclass

from oracle.plant.models import SCSAddress, parse_own_where


@dataclass(frozen=True)
class DecodedPicFrame:
    """Decoded outbound gateway frame on the PIC serial / RPMsg link or raw SCS bus.

    Attributes:
        digits: ``3`` for 3-digit PIC protocol (``$003``, ``$004``, ``$006``),
            ``2`` for 2-digit PIC protocol (``$03``, ``$04``, ``$06``), or
            ``0`` for raw binary ``A8 ... <xor> A3`` SCS telegrams.
        opcode: PIC opcode string (``"03"``, ``"04"``, ``"06"``, or ``"raw"``).
        payload: 4-byte or 8-byte decoded SCS payload bytes.
        subbus: Sub-bus interface address (``0`` for local bus, ``1..15`` for
            ``#4#<subbus>`` F422 sub-bus interfaces).
    """

    digits: int
    opcode: str
    payload: bytes
    subbus: int = 0


def xor_checksum(payload: bytes) -> int:
    """Compute the bitwise XOR checksum over an SCS payload byte sequence."""
    acc = 0
    for b in payload:
        acc ^= b
    return acc


def wrap_raw_scs(payload: bytes) -> bytes:
    """Wrap a 4-byte or 8-byte SCS payload in ``A8 ... <xor> A3`` delimiters."""
    if len(payload) not in (4, 8):
        raise ValueError(f"SCS payload must be 4 or 8 bytes, got {len(payload)}")
    return bytes([0xA8, *payload, xor_checksum(payload), 0xA3])


def unwrap_raw_scs(frame: bytes) -> bytes | None:
    """Validate and unwrap a raw ``A8 ... <xor> A3`` binary SCS telegram."""
    if len(frame) not in (7, 11):
        return None
    if frame[0] != 0xA8 or frame[-1] != 0xA3:
        return None
    payload = frame[1:-2]
    if xor_checksum(payload) != frame[-2]:
        return None
    return payload


def decode_gateway_frame(frame: bytes) -> DecodedPicFrame | None:
    """Decode an outbound gateway PIC or raw SCS frame into a DecodedPicFrame."""
    raw = unwrap_raw_scs(frame)
    if raw is not None:
        subbus = raw[1] if len(raw) == 8 and raw[0] == 0xEC else 0
        return DecodedPicFrame(digits=0, opcode="raw", payload=raw, subbus=subbus)

    stripped = frame.strip(b"\r\n")
    if not stripped.startswith(b"$"):
        return None

    for prefix, digits, opcode, expected_len in (
        (b"$003", 3, "03", 4),
        (b"$004", 3, "04", 4),
        (b"$006", 3, "06", 8),
        (b"$03", 2, "03", 4),
        (b"$04", 2, "04", 4),
        (b"$06", 2, "06", 8),
    ):
        if stripped.startswith(prefix):
            hex_part = stripped[len(prefix) :]
            if len(hex_part) != expected_len * 2:
                return None
            try:
                payload = bytes.fromhex(hex_part.decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                return None
            subbus = 0
            if len(payload) == 8 and payload[0] == 0xEC:
                subbus = payload[1]
            elif len(payload) == 8 and payload[0] == 0xD1:
                subbus = (payload[2] >> 4) & 0x0F
            return DecodedPicFrame(
                digits=digits, opcode=opcode, payload=payload, subbus=subbus
            )
    return None


def encode_short_inbound(
    b0: int,
    b1: int,
    b2: int,
    b3: int,
    *,
    digits: int = 3,
    subbus: int = 0,
) -> bytes:
    """Encode a 4-byte inbound device telegram (or 8-byte E4 sub-bus wrapper)."""
    for val in (b0, b1, b2, b3, subbus):
        if not 0 <= val <= 0xFF:
            raise ValueError(f"byte value out of range: {val}")
    if subbus > 0:
        payload8 = bytes([0xE4, subbus, 0x00, 0x00, b0, b1, b2, b3])
        return encode_extended_inbound(payload8, digits=digits)
    if digits == 0:
        return wrap_raw_scs(bytes([b0, b1, b2, b3]))
    if digits == 2:
        return f"${17:02d}{b0:02X}{b1:02X}{b2:02X}{b3:02X}\r".encode("ascii")
    if digits == 3:
        return f"${17:03d}{b0:02X}{b1:02X}{b2:02X}{b3:02X}\r".encode("ascii")
    raise ValueError(f"unsupported PIC digit width: {digits}")


def encode_extended_inbound(payload8: bytes, *, digits: int = 3) -> bytes:
    """Encode an 8-byte extended inbound device telegram (e.g. D2 or E4)."""
    if len(payload8) != 8:
        raise ValueError(f"extended SCS payload must be 8 bytes, got {len(payload8)}")
    if digits == 0:
        return wrap_raw_scs(payload8)
    hex_str = payload8.hex().upper()
    if digits == 2:
        return f"$18{hex_str}\r".encode("ascii")
    if digits == 3:
        return f"$018{hex_str}\r".encode("ascii")
    raise ValueError(f"unsupported PIC digit width: {digits}")


def parse_where_subbus(where: str) -> tuple[str, int]:
    """Split an OpenWebNet WHERE string into (base_where, subbus_id)."""
    if "#4#" in where:
        base, sub = where.split("#4#", 1)
        try:
            return base, int(sub)
        except ValueError:
            return base, 0
    return where, 0


def where_to_bus_addr(where: str, *, who: int = 1) -> tuple[int, int]:
    """Convert an OpenWebNet WHERE address into (bus_addr_byte, subbus_id)."""
    base, subbus = parse_where_subbus(where)
    if not base or base == "0":
        return 0x00, subbus
    if who == 18 and base.startswith("5") and len(base) == 2 and base[1].isdigit():
        return 0xA0 | int(base[1]), subbus
    if (
        base.startswith("7")
        and base.endswith("#0")
        and len(base) == 4
        and base[1].isdigit()
    ):
        return 0xB0 | int(base[1]), subbus
    try:
        addr_obj: SCSAddress = parse_own_where(where)
        return addr_obj.to_scs_byte(), addr_obj.interface or 0
    except ValueError:
        pass
    if len(base) <= 2 and all(c in "0123456789ABCDEFabcdef" for c in base):
        return int(base, 16) & 0xFF, subbus
    raise ValueError(f"cannot map WHERE={where!r} to SCS bus byte")
