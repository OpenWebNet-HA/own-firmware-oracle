"""driver -- run a suite against a Target and turn each step into a record Row.

Target is a protocol so the loop is tested with a fake; QemuTarget implements
it in Phase 2b. Outputs are tagged by side so one row can show both what reached
the bus and what the gateway emitted to the OpenWebNet client (on its event side
or as status response frames on the command side before ACK):

    bus:a8 31 00 12 01 22 a3
    own:*1*0*31##

The gateway clock is the emulated host's wall clock, which no two runs share
(the jail cannot set it, so a clock write never changes it either). Its
values are recorded as placeholders: in a WHO 13 time / date answer
(*#13**0*{H}*{M}*{S}*001##), and as 'x' in the clock telegrams a 2-digit PIC
gateway puts on the bus after a clock write ($06D2000906xxxxxxFF\\r). The
time-zone field T or byte, an empty or non-numeric value and the field count
stay verbatim: those are the firmware's facts.
"""

from __future__ import annotations

import re
from typing import Protocol

from oracle.cases import Step, Suite
from oracle.record import Row

CLOCK_ANSWER = re.compile(r"\*#13\*\*(0|1|22)\*(.*)##")
# Per DIMENSION, the placeholder of each value field; None keeps it (T).
CLOCK_FIELDS: dict[str, tuple[str | None, ...]] = {
    "0": ("{H}", "{M}", "{S}", None),
    "1": ("{W}", "{D}", "{MO}", "{Y}"),
    "22": ("{H}", "{M}", "{S}", None, "{W}", "{D}", "{MO}", "{Y}"),
}
# PIC clock telegrams: time hhmmss (BCD) + zone byte, and date ddWMyyyy.
CLOCK_TELEGRAMS = (
    re.compile(rb"(\$0?06D2000906)([0-9]{6})([0-9A-F]{2}\r)"),
    re.compile(rb"(\$0?06D2000907)([0-9A-F]{8})(\r)"),
)


def redact_clock(frame: str) -> str:
    """Replace the wall-clock digits of a WHO 13 DIMENSION 0 / 1 / 22 answer."""
    m = CLOCK_ANSWER.fullmatch(frame)
    if m is None:
        return frame
    names = CLOCK_FIELDS[m.group(1)]
    values = m.group(2).split("*")
    for i, value in enumerate(values[: len(names)]):
        name = names[i]
        if name is not None and value.isdigit():
            values[i] = name
    return f"*#13**{m.group(1)}*{'*'.join(values)}##"


def redact_clock_bus(frame: bytes) -> bytes:
    """Replace the wall-clock digits of a PIC clock telegram with 'x'."""
    for pattern in CLOCK_TELEGRAMS:
        m = pattern.fullmatch(frame)
        if m is not None:
            return m.group(1) + b"x" * len(m.group(2)) + m.group(3)
    return frame


class Target(Protocol):
    def send_own(self, frame: str) -> None: ...

    def inject_bus(self, data: bytes) -> None: ...

    def settle(self) -> bool:
        """True once the firmware is quiet, False if it was still busy at the limit."""

    def take_reply(self) -> str:
        """'ack', 'nack' or '-' for the last OpenWebNet command."""

    def take_bus(self) -> list[bytes]:
        """Frames the gateway put on the bus since the last call."""

    def take_own(self) -> list[str]:
        """Frames the gateway emitted on its event side since the last call."""

    def alive(self) -> bool: ...

    def restart(self) -> None: ...


def _drain(target: Target) -> list[str]:
    harness = getattr(target, "harness", "full")
    if isinstance(harness, str) and harness.startswith("plant:"):
        take_dev = getattr(target, "take_device_bus", None)
        take_cmd = getattr(target, "take_cmd_events", None)
        take_ev = getattr(target, "take_ev_events", None)
        dev_frames: list[bytes] = take_dev() if callable(take_dev) else []
        cmd_frames: list[str] = take_cmd() if callable(take_cmd) else []
        ev_frames: list[str] = take_ev() if callable(take_ev) else target.take_own()
        return (
            [f"bus:{redact_clock_bus(f).hex(' ')}" for f in target.take_bus()]
            + [f"bus_rx:{f.hex(' ')}" for f in dev_frames]
            + [f"cmd:{redact_clock(f)}" for f in cmd_frames]
            + [f"evt:{redact_clock(f)}" for f in ev_frames]
        )
    return [f"bus:{redact_clock_bus(f).hex(' ')}" for f in target.take_bus()] + [
        f"own:{redact_clock(f)}" for f in target.take_own()
    ]


def run_step(target: Target, step: Step) -> Row:
    if step.direction == "down":
        target.send_own(step.input)
    else:
        target.inject_bus(bytes.fromhex(step.input))
    settled = target.settle()
    reply = target.take_reply() if step.direction == "down" else "-"
    outputs = tuple(_drain(target))
    if not target.alive():
        verdict = "crash"
    elif not settled:
        verdict = "timeout"
    else:
        verdict = "out" if outputs else "silent"
    return Row(step.direction, step.input, reply, verdict, outputs)


def run_suite(target: Target, suite: Suite, *, restart_every: int = 1) -> list[Row]:
    """Independent steps get a fresh process each by default (reset=each: no
    step can see state left by another). restart_every=N shares a process across
    N steps (reset=batch-N) and is only for when the reset-each TSV is stable
    and identical; 0 never restarts except after a crash. A sequence starts from
    a fresh process and stops at the first crash: later steps are recorded as
    'skipped', since their answer would come from a different process state."""
    target.restart()
    _drain(target)  # boot noise belongs to no step
    rows: list[Row] = []
    for i, step in enumerate(suite.steps):
        if suite.ordered and rows and rows[-1].verdict in ("crash", "skipped"):
            rows.append(Row(step.direction, step.input, "-", "skipped"))
            continue
        if not suite.ordered and restart_every and i and i % restart_every == 0:
            target.restart()
            _drain(target)
        row = run_step(target, step)
        rows.append(row)
        if row.verdict == "crash" and not suite.ordered:
            target.restart()
            _drain(target)
    return rows
