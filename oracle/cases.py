"""cases -- load a suite: one `down` (OpenWebNet text) or `up` (bus hex) step per line.

    ; comment (OpenWebNet uses '#', so comments use ';')
    down  *1*1*31##
    up    a8 31 00 12 01 22 a3

`<name>.cases` holds independent steps: duplicates are an error and the record
sorts them, so editing one line never moves another. `<name>.seq` is an
ordered sequence recorded as one unit; there order is the point.
Malformed OpenWebNet text is allowed on purpose -- what the firmware does with
it is a fact too -- but every value must be printable, TSV-safe ASCII.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

SUITE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")
OWN_TEXT = re.compile(r"[\x21-\x7e]+")
HEX_BYTE = re.compile(r"[0-9a-f]{2}")
SUFFIXES = {".cases": False, ".seq": True}  # suffix -> ordered


class CaseError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class Step:
    direction: str  # "down" | "up"
    input: str  # OpenWebNet text, or normalised lower-case hex bytes


@dataclass(frozen=True)
class Suite:
    name: str
    sha256: str
    ordered: bool
    steps: tuple[Step, ...]


def normalise_hex(value: str) -> str:
    tokens = value.lower().split()
    if not tokens or not all(HEX_BYTE.fullmatch(t) for t in tokens):
        raise CaseError(f"not a list of hex bytes: {value!r}")
    return " ".join(tokens)


def parse_line(line: str) -> Step | None:
    text = line.strip()
    if not text or text.startswith(";"):
        return None
    parts = text.split(None, 1)
    if len(parts) != 2:
        raise CaseError(f"expected '<direction> <input>': {text!r}")
    direction, value = parts[0], parts[1].strip()
    if direction == "down":
        if not OWN_TEXT.fullmatch(value):
            raise CaseError(
                f"OpenWebNet input must be printable ASCII without spaces: {value!r}"
            )
        return Step("down", value)
    if direction == "up":
        return Step("up", normalise_hex(value))
    raise CaseError(f"unknown direction {direction!r} (want down / up)")


def load(path: Path) -> Suite:
    if path.suffix not in SUFFIXES:
        raise CaseError(f"{path.name}: suite files end in .cases or .seq")
    if not SUITE_NAME.fullmatch(path.stem):
        raise CaseError(f"{path.name}: suite name must match {SUITE_NAME.pattern}")
    ordered = SUFFIXES[path.suffix]
    # Hash the LF-normalised text: a Windows checkout must give the same key.
    try:
        text = path.read_bytes().decode("ascii").replace("\r\n", "\n")
    except UnicodeDecodeError:
        raise CaseError(f"{path.name}: suite files are ASCII") from None
    steps: list[Step] = []
    for lineno, line in enumerate(text.split("\n"), 1):
        try:
            step = parse_line(line)
        except CaseError as exc:
            raise CaseError(f"{path.name}:{lineno}: {exc}") from None
        if step is not None:
            steps.append(step)
    if not steps:
        raise CaseError(f"{path.name}: no steps")
    if not ordered:
        seen: set[Step] = set()
        for step in steps:
            if step in seen:
                raise CaseError(
                    f"{path.name}: duplicate step {step.direction} {step.input}"
                )
            seen.add(step)
        steps.sort()
    return Suite(
        name=path.stem,
        sha256=hashlib.sha256(text.encode("ascii")).hexdigest(),
        ordered=ordered,
        steps=tuple(steps),
    )
