"""stage -- rebuild a target's sysroot from the firmware image, checked row by row.

Phase 1 already walks every layer; staging reuses that walk (tools/unpack.py's
`sink`) and writes the members of the target's `sysroot` layers into a fresh
directory, in overlay order. Every member must match its row in the committed
manifest.tsv, and every manifest row under those layers must turn up, so the
sysroot the oracle runs is exactly the one the manifest describes. A mismatch
means the manifest is stale (re-run phase 1), never "close enough".

The staged tree is vendor code: it lives under the run's work dir, which is
throwaway and never committed (tools/guard.py refuses it anyway).

Untrusted paths. Member names come from the image, so:
  * a name with an empty, '.' or '..' component, or a NUL, is refused;
  * nothing is ever written through a symlink: a parent that is a link from an
    earlier layer is refused rather than followed;
  * links are kept, but re-targeted inside the sysroot: an absolute target
    becomes relative to the link, and '..' stops at the sysroot root, the way
    the guest would resolve it under chroot. Without this, the HOST kernel
    would resolve /lib/libc.so.6 against the host's /lib.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from oracle.target import TargetSpec, manifest_key

if TYPE_CHECKING:
    from collections.abc import Callable

ELF_MAGIC = b"\x7fELF"
# How unpack names what it found inside a member: <member>~gunzip, <member>!x,
# <member>:/etc/x. Rows past such a separator are derived, not members.
DERIVED_AT = re.compile(r"~|!|:/")
EXEC_MODE, FILE_MODE, DIR_MODE = 0o755, 0o644, 0o755


class StageError(ValueError):
    pass


@dataclass(frozen=True)
class Member:
    layer: str
    row: str  # the member's manifest path
    path: str  # as the archive names it; member_path() makes it sysroot-relative
    blob: bytes | None
    target: str | None  # link text when the member is a symlink


@dataclass(frozen=True)
class Staged:
    root: Path
    files: int
    links: int
    dirs: int = 0  # empty directories


def member_path(name: str) -> str:
    """The path a member takes inside the sysroot; refuses anything unsafe."""
    rel = name.lstrip("/")
    parts = rel.split("/")
    if "\0" in rel or not rel or any(p in {"", ".", ".."} for p in parts):
        raise StageError(f"unsafe member path {name!r}")
    return rel


def contained_link(link: str, target: str) -> str:
    """`target` rewritten relative to `link`'s directory, resolved as the guest
    would see it with the sysroot as '/': '..' cannot climb above the root."""
    base = posixpath.dirname(link)
    joined = target if target.startswith("/") else posixpath.join("/", base, target)
    parts: list[str] = []
    for part in joined.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    resolved = "/".join(parts) or "."
    return posixpath.relpath(resolved, base or ".")


def collect(
    layers: tuple[str, ...],
) -> tuple[list[Member], Callable[[str, str, str, bytes | None, str | None], None]]:
    """A member list and the unpack sink that fills it with every member of
    one of `layers` (a layer is a container row path plus '!' or ':')."""
    wanted = set(layers)
    members: list[Member] = []

    def sink(
        container: str, sep: str, member: str, blob: bytes | None, target: str | None
    ) -> None:
        layer = container + sep[0]
        if layer in wanted:
            members.append(
                Member(layer, container + sep + member, member, blob, target)
            )

    return members, sink


def _derived(key: str, seen: set[str]) -> bool:
    """True if key extends a seen member at an unpack separator."""
    return any(key[: m.start()] in seen for m in DERIVED_AT.finditer(key))


def verify(
    members: list[Member], layers: tuple[str, ...], manifest: dict[str, str]
) -> None:
    """Every member matches its manifest row; every row of a layer was seen."""
    seen: set[str] = set()
    for m in members:
        key = manifest_key(m.row)
        want = manifest.get(key)
        data = m.blob if m.blob is not None else os.fsencode(m.target or "")
        have = hashlib.sha256(data).hexdigest()
        if want != have:
            raise StageError(
                f"{key}: image gives {have}, manifest has {want}; "
                "the manifest is stale -- re-run phase 1"
            )
        seen.add(key)
    # Rows derived from a member (the payload of a .gz inside the rootfs, say)
    # extend a member's path; they are not members themselves.
    missing = sorted(
        k
        for k in manifest
        if k.startswith(layers) and k not in seen and not _derived(k, seen)
    )
    if missing:
        raise StageError(f"manifest rows not in the image: {missing[:5]}")


def _safe_parent(root: Path, rel: str) -> Path:
    """Create rel's parent dirs under root; refuse to pass through a link."""
    cur = root
    for part in rel.split("/")[:-1]:
        cur = cur / part
        if cur.is_symlink():
            raise StageError(f"{rel}: parent {cur.relative_to(root)} is a symlink")
        if not cur.exists():
            cur.mkdir(mode=DIR_MODE)
        elif not cur.is_dir():
            raise StageError(f"{rel}: parent {cur.relative_to(root)} is a file")
    return cur


def write(root: Path, members: list[Member], layers: tuple[str, ...]) -> Staged:
    """Write members into root, layer by layer: a later layer replaces a file
    or link of an earlier one, never a directory. A member with neither bytes
    nor a link target is an empty directory (a manifest `dir` row)."""
    if not root.is_absolute():
        raise StageError(f"stage root must be absolute: {root}")
    root.mkdir(parents=True, exist_ok=False)
    order = {layer: i for i, layer in enumerate(layers)}
    files = links = dirs = 0
    for m in sorted(members, key=lambda m: (order[m.layer], m.path)):
        rel = member_path(m.path)
        parent = _safe_parent(root, rel)
        dest = parent / rel.rsplit("/", 1)[-1]
        if m.blob is None and m.target is None:
            if dest.is_symlink() or (dest.exists() and not dest.is_dir()):
                raise StageError(f"{rel}: a directory cannot replace a file or link")
            dest.mkdir(mode=DIR_MODE, exist_ok=True)
            dirs += 1
            continue
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        elif dest.exists():
            raise StageError(f"{rel}: a later layer cannot replace a directory")
        if m.target is not None:
            dest.symlink_to(contained_link(rel, m.target))
            links += 1
            continue
        blob = m.blob or b""
        executable = blob.startswith((ELF_MAGIC, b"#!"))
        fd = os.open(
            dest,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            EXEC_MODE if executable else FILE_MODE,
        )
        with os.fdopen(fd, "wb") as fh:
            fh.write(blob)
        files += 1
    return Staged(root, files, links, dirs)


def stage(
    spec: TargetSpec,
    manifest: dict[str, str],
    root: Path,
    walk: Callable[[Callable[[str, str, str, bytes | None, str | None], None]], None],
) -> Staged:
    """`walk(sink)` runs phase 1's walk over the verified wrapper (run.py
    supplies it); this module never opens the image itself."""
    members, sink = collect(spec.sysroot)
    walk(sink)
    verify(members, spec.sysroot, manifest)
    return write(root, members, spec.sysroot)
