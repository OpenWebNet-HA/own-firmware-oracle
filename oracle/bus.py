"""bus -- the simulated SCS bus the firmware talks to.

The bus adapter hands us raw bytes (a pty master in production, a queue in
tests). A Framer cuts them into frames, a Responder plays the devices on the
bus, and Bus keeps an ordered log. Raw bytes are the evidence: decoding SCS
fields is a separate layer, so a wrong framing guess loses no data.

Time comes from an injected Clock, so the settle logic is tested without
sleeping.
"""

from __future__ import annotations

import contextlib
import os
import select
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

# Who put a frame on the bus.
GATEWAY = "gateway"  # the firmware under test
DRIVER = "driver"  # a step injected it
DEVICE = "device"  # a Responder answered


class Port(Protocol):
    def read(self) -> bytes:
        """Non-blocking: whatever the firmware wrote since the last read, or b''."""

    def write(self, data: bytes) -> None: ...


class PtyPort:
    """A Port reading from and writing to a PTY master file descriptor."""

    def __init__(self, master: int) -> None:
        self.master = master

    def read(self) -> bytes:
        if self.master < 0:
            return b""
        try:
            r, _, _ = select.select([self.master], [], [], 0)
            if not r:
                return b""
            return os.read(self.master, 4096)
        except (OSError, ValueError):
            return b""

    def write(self, data: bytes) -> None:
        if not data or self.master < 0:
            return
        with contextlib.suppress(OSError, ValueError):
            os.write(self.master, data)


class Clock(Protocol):
    def now_ms(self) -> float: ...

    def sleep_ms(self, ms: float) -> None: ...


class MonotonicClock:
    def now_ms(self) -> float:
        return time.monotonic() * 1000.0

    def sleep_ms(self, ms: float) -> None:
        time.sleep(ms / 1000.0)


class Framer(Protocol):
    name: str

    def feed(self, data: bytes, now_ms: float) -> list[bytes]: ...

    def flush(self, now_ms: float) -> list[bytes]:
        """Frames that are complete once the line has been quiet until now_ms."""


class DelimitedFramer:
    """Start byte .. end byte. The community SCS framing (A8 .. A3) is the first
    hypothesis; it is a parameter because discovery may refute it. Bytes outside
    a frame are kept as their own 'frame' rather than dropped."""

    def __init__(self, start: int = 0xA8, end: int = 0xA3, max_len: int = 64) -> None:
        self.start, self.end, self.max_len = start, end, max_len
        self.name = f"delimited:{start:02x}:{end:02x}"
        self._buf = bytearray()
        self._in_frame = False

    def feed(self, data: bytes, now_ms: float) -> list[bytes]:
        out: list[bytes] = []
        for b in data:
            if not self._in_frame and b == self.start:
                if self._buf:  # stray bytes before this start
                    out.append(bytes(self._buf))
                    self._buf.clear()
                self._in_frame = True
            self._buf.append(b)
            if self._in_frame and (b == self.end or len(self._buf) >= self.max_len):
                out.append(bytes(self._buf))
                self._buf.clear()
                self._in_frame = False
        return out

    def flush(self, now_ms: float) -> list[bytes]:
        if not self._buf:
            return []
        frame = bytes(self._buf)
        self._buf.clear()
        self._in_frame = False
        return [frame]


class LineFramer:
    """A frame ends at CR or LF; NUL padding before a frame is dropped.

    The MH200N's scsserver talks to its bus interface (a PIC) in short ASCII
    commands -- `$24` CR, NUL-padded to 8 bytes -- so CR is the frame end, and
    unlike an idle gap it does not depend on scheduling: the same bytes give
    the same frames however the reads were chunked."""

    name = "line"
    ENDS = b"\r\n"

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes, now_ms: float) -> list[bytes]:
        out: list[bytes] = []
        for b in data:
            if b == 0 and not self._buf:
                continue  # padding between frames
            self._buf.append(b)
            if b in self.ENDS:
                out.append(bytes(self._buf))
                self._buf.clear()
        return out

    def flush(self, now_ms: float) -> list[bytes]:
        frame, self._buf = bytes(self._buf), bytearray()
        return [frame] if frame else []


class IdleGapFramer:
    """A frame is a burst of bytes followed by at least gap_ms of silence. For
    discovery, when the framing is not known yet."""

    def __init__(self, gap_ms: int = 20) -> None:
        self.gap_ms = gap_ms
        self.name = f"idle:{gap_ms}"
        self._buf = bytearray()
        self._last_ms = 0.0

    def feed(self, data: bytes, now_ms: float) -> list[bytes]:
        if not data:
            return []
        out = self.flush(now_ms)
        self._buf += data
        self._last_ms = now_ms
        return out

    def flush(self, now_ms: float) -> list[bytes]:
        if self._buf and now_ms - self._last_ms >= self.gap_ms:
            frame = bytes(self._buf)
            self._buf.clear()
            return [frame]
        return []


class Responder(Protocol):
    name: str

    def respond(self, frame: bytes) -> list[bytes]: ...


class Silent:
    """No device on the bus. Firmware that waits for an answer will see none."""

    name = "silent"

    def respond(self, frame: bytes) -> list[bytes]:
        return []


class AckAll:
    """Every gateway frame is acknowledged with one byte (A5 is the community
    guess for the SCS ACK; a parameter for the same reason as the framing)."""

    def __init__(self, ack: int = 0xA5) -> None:
        self.ack = bytes([ack])
        self.name = f"ack:{ack:02x}"

    def respond(self, frame: bytes) -> list[bytes]:
        return [self.ack]


class Scripted:
    """Exact-match table: gateway frame -> device answers. Named, because the
    responder is part of every record header."""

    def __init__(self, name: str, table: dict[bytes, list[bytes]]) -> None:
        self.name = f"script:{name}"
        self.table = table

    def respond(self, frame: bytes) -> list[bytes]:
        return list(self.table.get(frame, []))


class SoundSourceResponder:
    """Simulated SCS Sound Source endpoint (L4561N / F500 Tuner).

    Answers SCS polling telegrams on the bus:
    - Status inquiry: answers with source power state
    - Frequency inquiry: answers with 6-digit frequency payload
    - Stored preset inquiry: answers with active preset number
    - RDS inquiry: answers with 8-byte ASCII codes
    """

    name = "sound_source"

    def __init__(self, source_id: int = 2) -> None:
        self.source_id = source_id
        self.power_on = False
        self.frequency_khz = 107000
        self.preset = 1
        self.rds_text = b"RADIO  1"  # 8 ASCII bytes

    def respond(self, frame: bytes) -> list[bytes]:
        # PicResponder already sends $19\r for $03 and $00\r for $06.
        # This responder returns ONLY device-generated bus telegrams.
        if not (frame.startswith(b"$03") or frame.startswith(b"$06")):
            return []

        payload = frame[3:].strip(b"\r\n")
        src_byte = bytes(f"{self.source_id:02X}", "ascii")

        # Opcode 90/91: Power control write on SCS bus
        if payload.startswith(b"0490") and src_byte in payload:
            self.power_on = True
            return []
        if payload.startswith(b"0491") and src_byte in payload:
            self.power_on = False
            return []

        # Opcode 95: Status Query (WHO 16 DIM 5 query)
        if payload.startswith(b"0495") and src_byte in payload:
            status_byte = b"C1" if self.power_on else b"C0"
            resp_payload = f"0490018F{self.source_id:02X}".encode("ascii") + status_byte
            return [b"$" + resp_payload + b"\r"]

        # Tuner Frequency Query (DIM 6)
        if payload.startswith(b"0496") and src_byte in payload:
            freq_hex = f"{self.frequency_khz:06X}".encode("ascii")
            return [b"$06D1" + freq_hex + b"\r"]

        # RDS Query (DIM 8)
        if payload.startswith(b"0498") and src_byte in payload:
            rds_hex = self.rds_text.hex().upper().encode("ascii")
            return [b"$0AD1" + rds_hex + b"\r"]

        return []


def pic_v8_mode(version: str) -> bool:
    """True when 2-digit scsserver runs this PIC version in v8 mode.

    `Configure_MCU` parses `$25<hwrev><fwrev><fwrel>` and switches to v8 mode when
    `fwrev > 27` and `fwrel > 0` (MyHomeServer1 028206); `Write()` then counts only
    `$08\\r` as ACK PIC (findings/pic-inbound-protocol.md, section 2).
    """
    if len(version) == 6 and version.isdigit():
        return int(version[2:4]) > 27 and int(version[4:6]) > 0
    return False


def needs_device_txack(bus_device: str, version: str) -> bool:
    """True when scsserver reports ACK PIC only after a device acknowledged the frame.

    3-digit RPMsg gateways (F460/F461 on /dev/ttyRPMSG*) need `$00800\\r`, 2-digit
    gateways in v8 PIC mode need `$08\\r`. A `pic` responder with nothing behind it
    sends neither, so on these targets every bus write ends in NAK PIC and openserver
    answers NACK although the frame went out (findings/empty-bus-reply-semantics.md).
    """
    return bus_device.rsplit("/", 1)[-1].startswith("ttyRPMSG") or pic_v8_mode(version)


class PicResponder:
    """Simulated PIC microcontroller answering firmware UART protocol commands.

    Answers status requests ($24), configurators ($26), configuration echo
    ($27, $02), and frame write acknowledgements ($03 -> $19 for standard SCS,
    $00 for extended/special SCS frames). Other frames are passed to the inner
    responder.

    Protocol opcodes reverse-engineered from MH200N/H4684 scsserver binary
    (2-digit UART protocol) and F460/F461 scsserver binary (3-digit RPMsg protocol):
    - 3-digit RPMsg protocol (F460, F461 Cortex-M4):
      - $024: requests PIC status and firmware version; answered with $025<version>\r.
      - $026: requests hardware configurators; answered with $026000000\r.
      - $028: requests hardware configurators ext; answered with $0280000\r.
      - $002: keepalive / status query; acknowledged with $000\r.
      - $003: write standard SCS frame to bus; acknowledged with $019\r.
      - $004 / $005 / $006 / $015 / $016 / $051 / $052 / $053: acknowledged with $000\r.
    - 2-digit UART protocol (legacy ARM32 gateways):
      - $24: requests PIC status and firmware version string;
        answered with $25<version>\\r.
      - $26: requests hardware configurators;
        answered with $26000\\r (virtual configuration).
      - $27 / $02 / $28: configuration echo and handshake frames during bus init.
      - $03: write standard SCS frame to bus; acknowledged by PIC with $19\\r.
      - $04 / $05 / $06: write 4/5/extended SCS frame to bus; acknowledged with $00\\r.
      - $15 / $16: bus configuration/diagnostics; acknowledged with $00\\r.
      - $51 / $52 / $53: auxiliary/energy/special frames; acknowledged with $00\\r.
    """

    def __init__(
        self,
        version: str = "010108",
        inner: Responder | None = None,
        pace_2digit_ms: float = 25.0,
    ) -> None:
        self.version = version
        self.inner = inner or Silent()
        self.pace_2digit_ms = pace_2digit_ms
        self._deferred: list[bytes] = []
        self._next_emit_ms: float = 0.0
        self.name = (
            "pic" if isinstance(self.inner, Silent) else f"pic:{self.inner.name}"
        )

    def _is_2digit_v8(self) -> bool:
        return pic_v8_mode(self.version)

    def _respond_3digit(self, frame: bytes) -> list[bytes] | None:
        is_plant = self.inner.name.startswith("plant:")
        if frame.startswith(b"$024"):
            return [f"$025{self.version.zfill(8)}\r".encode("ascii")]
        if frame.startswith(b"$026"):
            return [b"$026000000\r"]
        if frame.startswith(b"$028"):
            return [b"$0280000\r"]
        if frame.startswith(b"$002"):
            return [b"$000\r"]
        if frame.startswith(b"$003"):
            if not is_plant:
                answers = [b"$019\r"]
                if isinstance(self.inner, Silent) and frame.startswith(b"$00374001500"):
                    answers.extend([b"$017B874120E\r", b"$018D274010489FBFFF7\r"])
                return answers + self.inner.respond(frame)
            inner_ans = self.inner.respond(frame)
            has_dev_fn = getattr(self.inner, "has_p2p_device", None)
            has_dev = bool(has_dev_fn(frame)) if callable(has_dev_fn) else False
            if inner_ans or has_dev:
                return [b"$00800\r", *inner_ans]
            return [b"$019\r"]
        if any(
            frame.startswith(p)
            for p in (
                b"$004",
                b"$005",
                b"$006",
                b"$015",
                b"$016",
                b"$051",
                b"$052",
                b"$053",
            )
        ):
            if not is_plant:
                return [b"$000\r", *self.inner.respond(frame)]
            inner_ans = self.inner.respond(frame)
            stripped = frame.rstrip(b"\r\n")
            is_tx_ack_only = (
                frame.startswith(b"$004")
                or frame.startswith((b"$006D1A", b"$006D1B"))
                or (
                    frame.startswith(b"$006EC")
                    and stripped.endswith((b"001000", b"001100"))
                )
            )
            if inner_ans or is_tx_ack_only:
                return [b"$00800\r", *inner_ans]
            return [b"$000\r"]
        return None

    def _queue_2digit(self, ack: bytes, inner_ans: list[bytes]) -> list[bytes]:
        if not inner_ans:
            return [ack]
        if self.pace_2digit_ms <= 0 or not self.inner.name.startswith("plant:"):
            return [ack, *inner_ans]
        self._deferred.extend(inner_ans)
        return [ack]

    def _respond_2digit(self, frame: bytes) -> list[bytes] | None:
        is_plant = self.inner.name.startswith("plant:")
        if frame.startswith(b"$24"):
            return [f"$25{self.version}\r".encode("ascii")]
        if frame.startswith(b"$26"):
            return [b"$26000\r"]
        if any(frame.startswith(p) for p in (b"$27", b"$02", b"$28")):
            return [frame]
        if any(
            frame.startswith(p)
            for p in (
                b"$15",
                b"$16",
                b"$04",
                b"$05",
                b"$06",
                b"$51",
                b"$52",
                b"$53",
            )
        ):
            if not is_plant:
                return [b"$00\r", *self.inner.respond(frame)]
            inner_ans = self.inner.respond(frame)
            ack = (
                b"$08\r"
                if (inner_ans or frame.startswith(b"$04")) and self._is_2digit_v8()
                else b"$00\r"
            )
            return self._queue_2digit(ack, inner_ans)
        if frame.startswith(b"$03"):
            if not is_plant:
                answers = [b"$19\r"]
                if isinstance(self.inner, Silent) and frame.startswith(b"$0374001500"):
                    answers.extend([b"$17B874120E\r", b"$18D274010489FBFFF7\r"])
                return answers + self.inner.respond(frame)
            inner_ans = self.inner.respond(frame)
            has_dev_fn = getattr(self.inner, "has_p2p_device", None)
            has_dev = bool(has_dev_fn(frame)) if callable(has_dev_fn) else False
            ack = (
                b"$08\r"
                if (inner_ans or has_dev) and self._is_2digit_v8()
                else b"$19\r"
            )
            return self._queue_2digit(ack, inner_ans)
        return None

    def reset(self) -> None:
        self._deferred.clear()
        self._next_emit_ms = 0.0
        reset_fn = getattr(self.inner, "reset", None)
        if callable(reset_fn):
            reset_fn()

    def tick(self, now_ms: float) -> list[bytes]:
        tick_fn = getattr(self.inner, "tick", None)
        if callable(tick_fn):
            inner_tick: list[bytes] = tick_fn(now_ms)
            if inner_tick:
                if self.pace_2digit_ms > 0 and self._is_2digit_v8():
                    self._deferred.extend(inner_tick)
                else:
                    return inner_tick
        if self._deferred:
            if self._next_emit_ms == 0.0:
                self._next_emit_ms = now_ms + self.pace_2digit_ms
                return []
            if now_ms >= self._next_emit_ms:
                frame = self._deferred.pop(0)
                self._next_emit_ms = (
                    now_ms + self.pace_2digit_ms if self._deferred else 0.0
                )
                return [frame]
        return []

    def respond(self, frame: bytes) -> list[bytes]:
        res_3d = self._respond_3digit(frame)
        if res_3d is not None:
            return res_3d
        res_2d = self._respond_2digit(frame)
        if res_2d is not None:
            return res_2d
        return self.inner.respond(frame)


@dataclass(frozen=True)
class BusFrame:
    seq: int
    source: str  # GATEWAY | DRIVER | DEVICE
    data: bytes


@dataclass
class Bus:
    port: Port
    framer: Framer
    responder: Responder
    clock: Clock = field(default_factory=MonotonicClock)
    poll_ms: float = 5.0
    log: list[BusFrame] = field(default_factory=list)

    def _append(self, source: str, data: bytes) -> None:
        self.log.append(BusFrame(len(self.log), source, data))

    def mark(self) -> int:
        """Sequence number of the next frame; pass to gateway_frames() later."""
        return len(self.log)

    def inject(self, data: bytes) -> None:
        self._append(DRIVER, data)
        self.port.write(data)

    def _accept(self, frames: list[bytes]) -> None:
        for frame in frames:
            self._append(GATEWAY, frame)
            for answer in self.responder.respond(frame):
                self._append(DEVICE, answer)
                self.port.write(answer)

    def _tick(self, now_ms: float) -> bool:
        tick_fn = getattr(self.responder, "tick", None)
        tick_frames: list[bytes] = tick_fn(now_ms) if callable(tick_fn) else []
        for answer in tick_frames:
            self._append(DEVICE, answer)
            self.port.write(answer)
        return bool(tick_frames)

    def pump(self) -> bool:
        """Move whatever is waiting; True if the firmware wrote anything."""
        data = self.port.read()
        now_ms = self.clock.now_ms()
        self._accept(self.framer.feed(data, now_ms))
        ticked = self._tick(now_ms)
        return bool(data) or ticked

    def settle(
        self,
        quiet_ms: float,
        max_ms: float,
        on_poll: Callable[[], bool] | None = None,
    ) -> bool:
        """Pump until the firmware has been silent for quiet_ms (True) or max_ms
        has passed (False: the step timed out). on_poll may poll additional channels
        (e.g. OpenWebNet event socket) and return True if activity occurred."""
        start = last = self.clock.now_ms()
        while True:
            bus_active = self.pump()
            extra_active = on_poll() if on_poll is not None else False
            if bus_active or extra_active:
                last = self.clock.now_ms()
            now = self.clock.now_ms()
            if now - last >= quiet_ms:
                self._accept(self.framer.flush(now))
                return True
            if now - start >= max_ms:
                # force out a half-seen frame so it is not blamed on the next step
                self._accept(self.framer.flush(float("inf")))
                return False
            self.clock.sleep_ms(self.poll_ms)

    def gateway_frames(self, since: int) -> list[bytes]:
        return [f.data for f in self.log[since:] if f.source == GATEWAY]

    def device_frames(self, since: int) -> list[bytes]:
        return [f.data for f in self.log[since:] if f.source == DEVICE]


def get_framer(name: str) -> Framer:
    """Look up a framer by descriptor, e.g. 'line', 'idle:20', 'delimited:a8:a3'."""
    if name == "line":
        return LineFramer()
    if name.startswith("delimited:"):
        parts = name.split(":")
        if len(parts) == 3:
            try:
                return DelimitedFramer(int(parts[1], 16), int(parts[2], 16))
            except ValueError:
                pass
    elif name.startswith("idle:"):
        parts = name.split(":")
        if len(parts) == 2:
            try:
                return IdleGapFramer(int(parts[1]))
            except ValueError:
                pass
    raise ValueError(f"unknown framer: {name!r}")


def get_responder(
    name: str, version: str = "010108", inner: Responder | None = None
) -> Responder:
    """Look up a responder by descriptor, e.g. 'pic', 'silent', 'ack:a5'."""
    if name == "silent":
        return Silent()
    if name == "sound_source":
        return SoundSourceResponder(source_id=2)
    if name == "pic:sound_source":
        return PicResponder(version=version, inner=SoundSourceResponder(source_id=2))
    if name.startswith("plant:"):
        from oracle.plant.responder import PlantResponder  # noqa: PLC0415

        fixture = name.split(":", 1)[1]
        return PlantResponder.from_fixture(fixture, version=version)
    if name.startswith("pic:plant:"):
        from oracle.plant.responder import PlantResponder  # noqa: PLC0415

        fixture = name.split(":", 2)[2]
        return PicResponder(
            version=version,
            inner=PlantResponder.from_fixture(fixture, version=version),
        )
    if name.startswith("ack:"):
        parts = name.split(":")
        if len(parts) == 2:
            try:
                return AckAll(int(parts[1], 16))
            except ValueError:
                pass
    elif name in ("pic", "pic-mh200n"):
        return PicResponder(version=version, inner=inner)
    raise ValueError(f"unknown responder: {name!r}")
