"""bus -- the simulated SCS bus the firmware talks to.

The bus adapter hands us raw bytes (a pty master in production, a queue in
tests). A Framer cuts them into frames, a Responder plays the devices on the
bus, and Bus keeps an ordered log. Raw bytes are the evidence: decoding SCS
fields is a separate layer, so a wrong framing guess loses no data.

Time comes from an injected Clock, so the settle logic is tested without
sleeping.
"""

from __future__ import annotations

import time
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

    def settle(self, quiet_ms: float, max_ms: float) -> bool:
        """Pump until the firmware has been silent for quiet_ms (True) or max_ms
        has passed (False: the step timed out)."""
        start = last = self.clock.now_ms()
        while True:
            if self.pump():
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
