"""record -- the oracle TSV: one row per step, every answer-changing key in the header.

    # product=MH200N
    # ...
    direction	input	reply	verdict	output
    down	*1*0*31##	ack	out	a8 31 00 12 01 22 a3

Rows of a `.cases` suite are sorted by (direction, input); a `.seq` keeps its
order. Within a row, outputs keep emission order, joined with OUTPUT_SEP.
No timestamps, PIDs or ports: a re-run must give a byte-identical file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

COLUMNS = ("direction", "input", "reply", "verdict", "output")
DIRECTIONS = ("down", "up")
REPLIES = ("ack", "nack", "-")
VERDICTS = ("out", "silent", "crash", "timeout", "skipped")
OUTPUT_SEP = " | "
EMPTY = "-"

# Header keys, in the order they are written. A record missing one is invalid:
# each can change an answer, so each is part of the staleness key.
HEADER_KEYS = (
    "product", "version", "image_sha256",
    "harness", "target_sha256",
    "bus", "framer", "responder", "settle_ms",
    "suite", "suite_sha256",
    "oracle_version",
)
HEADER_VALUE = re.compile(r"[\x21-\x7e]+")
HARNESS = re.compile(r"full|unit:[A-Za-z0-9][A-Za-z0-9._-]*")

# Like manifest.tsv, plus non-ASCII: one row stays one ASCII line whatever the
# firmware emits.
TSV_UNSAFE = re.compile(r"[^\x20-\x5b\x5d-\x7e]")


def _escape(m: re.Match[str]) -> str:
    c = ord(m.group())
    return f"\\x{c:02x}" if c < 0x100 else f"\\u{c:04x}"


def tsv_field(s: str) -> str:
    return TSV_UNSAFE.sub(_escape, s)


@dataclass(frozen=True)
class Row:
    direction: str
    input: str
    reply: str
    verdict: str
    outputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.direction not in DIRECTIONS:
            raise ValueError(f"direction {self.direction!r}")
        if self.reply not in REPLIES:
            raise ValueError(f"reply {self.reply!r}")
        if self.verdict not in VERDICTS:
            raise ValueError(f"verdict {self.verdict!r}")
        # crash / timeout keep whatever was emitted before the process failed
        if self.verdict == "out" and not self.outputs:
            raise ValueError("verdict 'out' needs at least one output")
        if self.verdict in ("silent", "skipped") and self.outputs:
            raise ValueError(f"verdict {self.verdict!r} cannot have outputs")

    def cells(self) -> list[str]:
        # '|' escaped inside an output so OUTPUT_SEP stays unambiguous
        output = OUTPUT_SEP.join(
            tsv_field(o).replace("|", "\\x7c") for o in self.outputs
        ) or EMPTY
        return [self.direction, tsv_field(self.input), self.reply, self.verdict, output]


def harness_dir(harness: str) -> str:
    if not HARNESS.fullmatch(harness):
        raise ValueError(f"harness must be 'full' or 'unit:<program>', got {harness!r}")
    return harness.replace(":", "-")


def result_path(product: str, version: str, harness: str, suite: str) -> str:
    return f"{product}/{version}/oracle/{harness_dir(harness)}/{suite}.tsv"


def render(header: dict[str, str], rows: list[Row], *, ordered: bool) -> str:
    missing = [k for k in HEADER_KEYS if k not in header]
    extra = sorted(set(header) - set(HEADER_KEYS))
    if missing or extra:
        raise ValueError(f"header keys: missing {missing}, unexpected {extra}")
    for key in HEADER_KEYS:
        if not HEADER_VALUE.fullmatch(header[key]):
            raise ValueError(f"header {key}={header[key]!r} is not a plain token")
    harness_dir(header["harness"])
    if not ordered:
        keys = [(r.direction, r.input) for r in rows]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate (direction, input) in an unordered suite")
        rows = sorted(rows, key=lambda r: (r.direction, r.input))
    lines = [f"# {k}={header[k]}" for k in HEADER_KEYS]
    lines.append("\t".join(COLUMNS))
    lines += ["\t".join(r.cells()) for r in rows]
    return "\n".join(lines) + "\n"


def write(path: Path, header: dict[str, str], rows: list[Row], *, ordered: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(header, rows, ordered=ordered), encoding="ascii", newline="\n")


def read_header(path: Path) -> dict[str, str]:
    """Header only -- enough for plan.py to decide staleness."""
    header: dict[str, str] = {}
    for line in path.read_text(encoding="ascii").splitlines():
        if not line.startswith("# "):
            break
        key, _, value = line[2:].partition("=")
        header[key] = value
    return header
