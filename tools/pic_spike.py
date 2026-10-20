#!/usr/bin/env python3
"""pic_spike -- verify 2-digit and 3-digit inbound PIC serial framing under QEMU.

Probes the four core subsystem families (WHO=1 relay + dimmer, WHO=2 cover,
WHO=4 thermoregulation, WHO=18 energy management) against F461/020011 (3-digit
PIC protocol) and MyHomeServer1/028206 / MH200/020102 (2-digit PIC protocol).
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from oracle import bus, cases, driver, qemu_target, record, target

ROOT = Path(__file__).resolve().parents[1]

SPIKE_FRAMES: tuple[str, ...] = (
    "*1*1*06##",
    "*#1*06##",
    "*#1*08*#1*150*0##",
    "*#1*08*1##",
    "*2*1*05##",
    "*#4*2*0##",
    "*#18*51*113##",
)

# Suffix rules mapping outbound SCS payload -> inbound PIC body ($17/$017 or $18/$018)
SPIKE_RULES: dict[str, tuple[str, str]] = {
    "0306001200": ("17", "B8061200"),
    "0306001500": ("17", "B8061200"),
    "06D10801420D0D3200": ("18", "D20801440D0D3200"),
    "06D10801430D0D0000": ("18", "D20801440D0D3200"),
    "06D10801430D000000": ("18", "D20801440D0D3200"),
    "030500120A": ("17", "B805120A"),
    "0305001208": ("17", "B8051208"),
    "0499023000": ("17", "B40230F9"),
    "06D1A10233001D0000": ("18", "D2A10234001D01C2"),
}


@dataclass
class SpikeResponder:
    """Deterministic scripted responder for the Phase 0 PIC inbound spike."""

    delay_ms: float = 0.0
    name: str = "plant:spike"
    _pending: list[tuple[float | None, bytes]] = field(default_factory=list)

    def reset(self) -> None:
        self._pending.clear()

    def respond(self, frame: bytes) -> list[bytes]:
        text = frame.decode("latin-1", errors="ignore").strip().upper()
        if not text.startswith("$"):
            return []
        three_digit = text.startswith(("$003", "$004", "$005", "$006"))
        norm = "$" + text[2:] if three_digit else text
        body = norm[1:]
        rule = SPIKE_RULES.get(body)
        if rule is None:
            return []
        kind, payload = rule
        prefix = f"$0{kind}" if three_digit else f"${kind}"
        reply = f"{prefix}{payload}\r".encode("ascii")
        if self.delay_ms <= 0:
            return [reply]
        self._pending.append((None, reply))
        return []

    def tick(self, now_ms: float) -> list[bytes]:
        if not self._pending:
            return []
        due: list[bytes] = []
        remaining: list[tuple[float | None, bytes]] = []
        for deadline, payload in self._pending:
            if deadline is None:
                remaining.append((now_ms + self.delay_ms, payload))
            elif now_ms >= deadline and not due:
                due.append(payload)
            else:
                remaining.append((deadline, payload))
        self._pending = remaining
        return due


def build_spike_suite() -> cases.Suite:
    steps = tuple(cases.Step("down", f) for f in SPIKE_FRAMES)
    return cases.Suite("pic-spike", "0" * 64, ordered=True, steps=steps)


def run_spike(
    target_yaml: Path,
    image_path: Path,
    sysroot_path: Path | None = None,
    delay_ms: float = 0.0,
) -> list[record.Row]:
    spec = target.load(target_yaml, ROOT / "results")
    suite = build_spike_suite()
    responder = bus.PicResponder(
        version=spec.version, inner=SpikeResponder(delay_ms=delay_ms)
    )
    with tempfile.TemporaryDirectory(prefix="own-pic-spike-") as td:
        tgt = qemu_target.QemuTarget(
            spec,
            image_path,
            Path(td),
            harness="plant:spike",
            responder=responder,
            staged_sysroot=sysroot_path,
            sandboxed=False,
        )
        try:
            return driver.run_suite(tgt, suite, restart_every=0)
        finally:
            tgt.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run PIC inbound protocol spike.")
    parser.add_argument("target", help="Path to target YAML")
    parser.add_argument("--image", required=True, help="Path to firmware image")
    parser.add_argument("--sysroot", help="Optional pre-staged sysroot directory")
    parser.add_argument("--delay-ms", type=float, default=0.0, help="Bus reply delay")
    args = parser.parse_args(argv)

    sysroot = Path(args.sysroot).resolve() if args.sysroot else None
    rows = run_spike(
        Path(args.target).resolve(),
        Path(args.image).resolve(),
        sysroot_path=sysroot,
        delay_ms=args.delay_ms,
    )
    for row in rows:
        out_str = " | ".join(row.outputs) if row.outputs else "-"
        sys.stdout.write(f"{row.input}\t{row.reply}\t{row.verdict}\t{out_str}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
