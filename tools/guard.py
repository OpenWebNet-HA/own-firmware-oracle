#!/usr/bin/env python3
"""guard -- refuse to let a binary or an oversized file into the repo.

Runs in CI (pr.yml) and as a pre-commit hook. It is the automated backstop for
the ground rule "never commit binaries, disassembly or decompiled code": even
if someone points a result at the wrong path, this fails the build.

Checks every tracked/staged file outside allow-listed text areas:
  * no known binary magic (ELF / zip / gzip / squashfs / ext / uImage);
  * nothing larger than MAX_BYTES (results are TSV/markdown, never images).
"""
from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024

# magic bytes we never want committed
BIN_MAGIC = [b"\x7fELF", b"PK\x03\x04", b"\x1f\x8b", b"hsqs", b"sqsh"]

# extensions that are binary by definition
BIN_EXT = {".fwz", ".zip", ".gz", ".img", ".bin", ".squashfs", ".elf", ".so", ".o"}


def is_binary(path: Path) -> str | None:
    if path.suffix.lower() in BIN_EXT:
        return f"binary extension {path.suffix}"
    head = path.read_bytes()[:0x43A]
    for magic in BIN_MAGIC:
        if head.startswith(magic):
            return f"binary magic {magic.hex()}"
    if len(head) > 0x43A - 1 and struct.unpack_from(">I", head, 0)[0] == 0x27051956:
        return "uImage header"
    if len(head) > 0x439 and struct.unpack_from("<H", head, 0x438)[0] == 0xEF53:
        return "ext filesystem"
    return None


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout.split("\n")
    return [Path(p) for p in out if p and Path(p).is_file()]


def main() -> int:
    bad: list[str] = []
    for path in tracked_files():
        if path.parts and path.parts[0] in {".git"}:
            continue
        size = path.stat().st_size
        if size > MAX_BYTES:
            bad.append(f"{path}: {size} bytes > {MAX_BYTES}")
        reason = is_binary(path)
        if reason:
            bad.append(f"{path}: {reason}")
    if bad:
        print("guard: refusing these files:", file=sys.stderr)
        for b in bad:
            print("  " + b, file=sys.stderr)
        return 1
    print(f"guard: ok ({len(tracked_files())} files clean)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
