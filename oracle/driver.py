"""driver -- run a suite against a Target and turn each step into a record Row.

Target is a protocol so the loop is tested with a fake; QemuTarget implements
it in Phase 2b. Outputs are tagged by side so one row can show both what reached
the bus and what the gateway emitted to the OpenWebNet client (on its event side
or as status response frames on the command side before ACK):

    bus:a8 31 00 12 01 22 a3
    own:*1*0*31##
"""

from __future__ import annotations

from typing import Protocol

from oracle.cases import Step, Suite
from oracle.record import Row


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
    return [f"bus:{f.hex(' ')}" for f in target.take_bus()] + [
        f"own:{f}" for f in target.take_own()
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
