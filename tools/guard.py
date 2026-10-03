#!/usr/bin/env python3
"""guard -- refuse to let a binary or an oversized file into the repo.

Runs in CI (pr.yml) and as a pre-commit hook. It is the automated backstop for
the ground rule "never commit binaries, disassembly or decompiled code": even
if someone points a result at the wrong path, this fails the build.

Checks every file git would commit -- tracked files AND untracked files that
are not gitignored. The untracked half matters: in oracle.yml a freshly written
manifest is untracked when guard runs, and create-pull-request commits it next.
  * no known binary magic (ELF / zip / gzip / xz / 7z / squashfs / cramfs /
    UBI / ext / uImage);
  * no NUL byte anywhere (a renamed blob without known magic);
  * nothing larger than MAX_BYTES (results are TSV/markdown, never images),
    except a results/<product>/<version>/manifest.tsv, which may reach
    MANIFEST_MAX_BYTES: a modern rootfs has tens of thousands of files and
    each row carries the full layer path.
"""

from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
MANIFEST_MAX_BYTES = 50 * 1024 * 1024  # GitHub refuses files over 100 MB

# magic bytes we never want committed
BIN_MAGIC = [
    b"\x7fELF",
    b"PK\x03\x04",
    b"\x1f\x8b",
    b"hsqs",
    b"sqsh",
    b"\xfd7zXZ\x00",
    b"7z\xbc\xaf\x27\x1c",
    b"UBI#",
    b"\x45\x3d\xcd\x28",
    b"\x28\xcd\x3d\x45",  # cramfs, both byte orders
]

# extensions that are binary by definition
BIN_EXT = {
    ".fwz",
    ".zip",
    ".gz",
    ".img",
    ".bin",
    ".squashfs",
    ".elf",
    ".so",
    ".o",
    ".xz",
    ".bz2",
    ".lzma",
    ".7z",
    ".tar",
    ".cpio",
    ".cramfs",
    ".jffs2",
    ".ubi",
    ".ubifs",
    ".dtb",
    ".itb",
}


def is_binary(path: Path) -> str | None:
    if path.suffix.lower() in BIN_EXT:
        return f"binary extension {path.suffix}"
    head = path.read_bytes()[:0x43A]
    for magic in BIN_MAGIC:
        if head.startswith(magic):
            return f"binary magic {magic.hex()}"
    if len(head) >= 4 and struct.unpack_from(">I", head, 0)[0] == 0x27051956:
        return "uImage header"
    if len(head) > 0x439 and struct.unpack_from("<H", head, 0x438)[0] == 0xEF53:
        return "ext filesystem"
    # Everything we commit is text; a NUL anywhere means a blob, e.g. one
    # renamed to .tsv or .md that no magic above recognises.
    if b"\x00" in path.read_bytes():
        return "NUL byte (binary content)"
    return None


def max_bytes(path: Path) -> int:
    parts = path.parts
    if len(parts) == 4 and parts[0] == "results" and parts[3] == "manifest.tsv":
        return MANIFEST_MAX_BYTES
    return MAX_BYTES


def repo_files() -> list[Path]:
    """Tracked files plus untracked, non-ignored ones: everything `git add -A` takes."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    return [Path(p) for p in sorted(set(out)) if p and Path(p).is_file()]


def main() -> int:
    bad: list[str] = []
    files = repo_files()
    for path in files:
        size, limit = path.stat().st_size, max_bytes(path)
        if size > limit:
            bad.append(f"{path}: {size} bytes > {limit}")
        reason = is_binary(path)
        if reason:
            bad.append(f"{path}: {reason}")
    if bad:
        print("guard: refusing these files:", file=sys.stderr)
        for b in bad:
            print("  " + b, file=sys.stderr)
        return 1
    print(f"guard: ok ({len(files)} files clean)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
