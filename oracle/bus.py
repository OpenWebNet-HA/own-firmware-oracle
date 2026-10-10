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


class PicResponder:
    """Simulated PIC microcontroller answering firmware UART protocol commands.

    Answers status requests ($24), configurators ($26), configuration echo
    ($27, $02), and frame write acknowledgements ($03 -> $19 for standard SCS,
    $06 -> $00 for extended SCS). Other frames are passed to the inner responder.

    Protocol opcodes reverse-engineered from MH200N scsserver binary:
    - $24: requests PIC status and firmware version string;
      answered with $25<version>\\r.
    - $26: requests hardware configurators;
      answered with $26000\\r (virtual configuration).
    - $27 / $02: configuration echo and handshake frames during bus init.
    - $03: write standard SCS frame to bus; acknowledged by PIC with $19\\r.
    - $04: write 4-byte SCS frame to bus; acknowledged by PIC with $00\\r.
    - $06: write extended SCS frame to bus; acknowledged by PIC with $00\\r.
    """

    def __init__(
        self,
        version: str = "010108",
        inner: Responder | None = None,
    ) -> None:
        self.version = version
        self.inner = inner or Silent()
        self.name = (
            "pic" if isinstance(self.inner, Silent) else f"pic:{self.inner.name}"
        )

    def respond(self, frame: bytes) -> list[bytes]:
        if frame.startswith(b"$24"):
            return [f"$25{self.version}\r".encode("ascii")]
        if frame.startswith(b"$26"):
            return [b"$26000\r"]
        if (
            frame.startswith(b"$27")
            or frame.startswith(b"$02")
            or frame.startswith(b"$28")
        ):
            return [frame]
        if frame.startswith(b"$15") or frame.startswith(b"$16"):
            return [b"$00\r"]
        if frame.startswith(b"$03"):
            answers = [b"$19\r"]
            if not isinstance(self.inner, Silent):
                answers.extend(self.inner.respond(frame))
            return answers
        if frame.startswith(b"$04"):
            answers = [b"$00\r"]
            if not isinstance(self.inner, Silent):
                answers.extend(self.inner.respond(frame))
            return answers
        if frame.startswith(b"$06"):
            answers = [b"$00\r"]
            if not isinstance(self.inner, Silent):
                answers.extend(self.inner.respond(frame))
            return answers
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

    def pump(self) -> bool:
        """Move whatever is waiting; True if the firmware wrote anything."""
        data = self.port.read()
        self._accept(self.framer.feed(data, self.clock.now_ms()))
        return bool(data)

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
