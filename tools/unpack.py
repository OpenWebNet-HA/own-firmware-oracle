#!/usr/bin/env python3
"""unpack -- turn one firmware image into a manifest of what's inside it.

Walks the BTicino/Legrand packaging layers and records, for every extracted
program, its path / type / CPU / SHA-256. That TSV is the only thing published;
the extracted files land under a local work dir (default: a temp dir) and are
never committed.

Layers handled (auto-detected by magic, not hard-coded per image):
  zip                       wrapper and inner archives (ZipCrypto via the
                            catalog's documented password_scheme -- known
                            vendor strings, tried in order; no brute force);
                            Unix symlink members recorded as symlinks
  uImage (0x27051956)       64-byte U-Boot header stripped, payload recursed
  gzip / bzip2 / xz / lzma  decompressed in memory, payload recursed
  tar / cpio (newc, odc)    members recursed in memory; symlinks recorded
  ext2/3/4 (53 ef @ 0x438)  file tree via debugfs (read-only)
  squashfs (hsqs / sqsh)    file tree via unsquashfs
  cramfs (0x28cd3d45)       file tree via fsck.cramfs --extract
Filesystem symlinks are recorded, never followed. External tools run inside
bubblewrap (tools/jail.py) unless --no-sandbox is given.

Recognised but not unpacked yet: jffs2, UBI, FIT, 7z. They, unreadable
containers and any `data` blob of LARGE_DATA bytes or more fail the run unless
the catalog acknowledges them under `undecoded_ok`, so a filesystem cannot go
missing from a manifest without anyone noticing.

The image on disk must be the catalog's wrapper (size + SHA-256), and the
catalog's inner image must turn up inside it; anything else is refused, so a
manifest header always describes the bytes that were actually walked.
"""

from __future__ import annotations

import argparse
import bz2
import contextlib
import hashlib
import io
import lzma
import os
import re
import struct
import subprocess
import tarfile
import tempfile
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

import jail
import schema
from schema import CatalogEntry

# Manifest key component. Bump when a change to this tool would alter the
# manifest for an unchanged image; plan.py treats a mismatch as stale.
# 2: paths rooted at wrapper.filename; symlinks recorded instead of followed.
# 3: ELF tags carry word size, byte order and ARM ABI; more container formats;
#    ARM zImage recognised.
# 4: zip members stored as Unix symlinks are typed `symlink`, not `data`
#    (size and sha256 were already those of the link text); empty
#    directories of an archive or filesystem get a `dir` row.
TOOL_VERSION = "4"
# Row type of an empty directory; size 0, sha256 of no bytes.
DIR_TYPE = "dir"

SAFE_DEBUGFS_PATH = re.compile(r"[A-Za-z0-9_./+-]+")
# Manifest fields are TSV: a tab or newline in a member name would add a
# column or a row, so backslash and control characters are escaped as \xNN.
TSV_UNSAFE = re.compile(r"[\\\x00-\x1f\x7f]")

UIMAGE_MAGIC = 0x27051956
EXT_MAGIC = 0xEF53
CRAMFS_MAGIC = 0x28CD3D45
ZIMAGE_MAGIC = 0x016F2818  # ARM zImage, little-endian u32 at 0x24
# Bomb guard: default cap on what one decompressed layer, all members of one
# archive together, or one extracted filesystem tree may expand to. A catalog
# entry can raise it with limits.max_expand_mib.
MAX_DECOMPRESS = 256 * 1024 * 1024
# A `data` blob this large is more likely an unrecognised layer than a leaf.
LARGE_DATA = 1024 * 1024

# Containers we recognise but cannot open yet: the coverage gate names them.
UNSUPPORTED = {"jffs2", "ubi", "fit", "7z"}

# ELF e_machine -> CPU name (enough to pick an emulator)
ELF_MACHINE = {
    0x02: "SPARC",
    0x03: "x86",
    0x04: "m68k",
    0x08: "MIPS",
    0x14: "PowerPC",
    0x15: "PowerPC64",
    0x28: "ARM",
    0x2A: "SuperH",
    0x3E: "x86-64",
    0x5E: "Xtensa",
    0x71: "NiosII",
    0xB7: "AArch64",
    0xBD: "MicroBlaze",
    0xF3: "RISC-V",
}
EF_ARM_ABI_FLOAT_HARD = 0x400

TAR_MAGIC_OFFSET = 257
CPIO_NEWC = (b"070701", b"070702")
CPIO_ODC = b"070707"
S_IFMT, S_IFREG, S_IFLNK, S_IFDIR = 0o170000, 0o100000, 0o120000, 0o040000
ZIP_UNIX = 3  # ZipInfo.create_system: external_attr's high word is a Unix mode


class Row(TypedDict):
    """One manifest line."""

    path: str
    type: str
    size: int
    sha256: str


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def tsv_field(s: str) -> str:
    """Escape backslash and control characters so one row stays one TSV line."""
    return TSV_UNSAFE.sub(lambda m: f"\\x{ord(m.group()):02x}", s)


def _elf_tag(b: bytes) -> str:
    """ELF/<cpu>/<kind>/<bits><endian>[/<arm abi>], e.g. ELF/ARM/exec/32le/oabi.

    Byte order comes from EI_DATA: reading e_machine little-endian regardless
    would mislabel every big-endian MIPS / PowerPC / ARM program."""
    if len(b) < 20:
        return "ELF/truncated"
    bits = {1: 32, 2: 64}.get(b[4])
    order = {1: "<", 2: ">"}.get(b[5])
    if bits is None or order is None:
        return "ELF/bad-ident"
    etype, machine = struct.unpack_from(order + "HH", b, 16)
    kind = {1: "reloc", 2: "exec", 3: "dyn", 4: "core"}.get(etype, "elf")
    tag = (
        f"ELF/{ELF_MACHINE.get(machine, hex(machine))}/{kind}/"
        f"{bits}{'le' if order == '<' else 'be'}"
    )
    if machine == 0x28:
        # e_flags: EABI version in the top byte; 0 = the legacy (OABI) ABI
        off = 36 if bits == 32 else 48
        if len(b) >= off + 4:
            flags = struct.unpack_from(order + "I", b, off)[0]
            version = flags >> 24
            tag += f"/eabi{version}" if version else "/oabi"
            if version and flags & EF_ARM_ABI_FLOAT_HARD:
                tag += "-hf"
    return tag


def _is_lzma_alone(b: bytes) -> bool:
    """Legacy .lzma has no magic; accept only the usual header shape: props 0x5d,
    a power-of-two dictionary of 64 KiB..64 MiB, and an unknown or sane size."""
    if len(b) < 13 or b[0] != 0x5D:
        return False
    dict_size, out_size = struct.unpack_from("<IQ", b, 1)
    if dict_size & (dict_size - 1) or not (1 << 16) <= dict_size <= (1 << 26):
        return False
    return bool(out_size == 0xFFFFFFFFFFFFFFFF or out_size < (1 << 32))


# jffs2 node types (dirent, inode, cleanmarker, padding, summary, xattr, xref)
JFFS2_NODETYPES = {0xE001, 0xE002, 0x2003, 0x2004, 0x2006, 0xE008, 0xE009}
HEX = frozenset(b"0123456789abcdefABCDEF")
OCT = frozenset(b"01234567")


def _is_jffs2(b: bytes) -> bool:
    """Two magic bytes alone would match 1 in 65536 random files; the node type
    after them must be a real one, in the same byte order."""
    if len(b) < 12:
        return False
    for order in "<>":
        magic, nodetype = struct.unpack_from(order + "HH", b)
        if magic == 0x1985 and nodetype in JFFS2_NODETYPES:
            return True
    return False


def _is_cpio(b: bytes) -> bool:
    """The magic is six ASCII digits; require the whole header to be hex (newc)
    or octal (odc) so a text file starting with '070701' stays data."""
    if b[:6] in CPIO_NEWC:
        return len(b) >= 110 and set(b[6:110]) <= HEX
    if b[:6] == CPIO_ODC:
        return len(b) >= 76 and set(b[6:76]) <= OCT
    return False


def cpu_of(b: bytes) -> str:
    """Return a short type/CPU tag for a blob (facts for the manifest).

    Every header read is length-checked: real filesystems contain empty and
    tiny files, and a truncated header must classify, not crash.
    """
    if not b:
        return "empty"
    if b[:4] == b"\x7fELF":
        return _elf_tag(b)
    if b[:2] == b"PK":
        return "zip"
    if b[:2] == b"\x1f\x8b":
        return "gzip"
    if b[:3] == b"BZh" and b[4:10] == b"1AY&SY":
        return "bzip2"
    if b[:6] == b"\xfd7zXZ\x00":
        return "xz"
    if b[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    if b[:4] in (b"hsqs", b"sqsh"):
        return "squashfs"
    if len(b) >= 4 and CRAMFS_MAGIC in struct.unpack_from("<I", b) + struct.unpack_from(
        ">I", b
    ):
        return "cramfs"
    if b[:4] == b"UBI#":
        return "ubi"
    if _is_jffs2(b):
        return "jffs2"
    if b[:4] == b"\xd0\x0d\xfe\xed":
        # A FIT image is a device tree carrying kernels / ramdisks; a plain .dtb
        # is a small leaf. Size is the cheap tell without parsing the tree.
        return "fit" if len(b) >= LARGE_DATA else "dtb"
    if _is_cpio(b):
        return "cpio"
    if b[TAR_MAGIC_OFFSET : TAR_MAGIC_OFFSET + 5] == b"ustar":
        return "tar"
    if len(b) > 0x43A and struct.unpack_from("<H", b, 0x438)[0] == EXT_MAGIC:
        return "ext-fs"
    if len(b) >= 64 and struct.unpack_from(">I", b, 0)[0] == UIMAGE_MAGIC:
        return "uImage"  # a uImage needs its full 64-byte header
    if len(b) >= 0x28 and struct.unpack_from("<I", b, 0x24)[0] == ZIMAGE_MAGIC:
        return "zImage/ARM"
    if _is_lzma_alone(b):
        return "lzma"
    return "data"


def passwords(entry: CatalogEntry) -> list[bytes]:
    out: list[bytes] = []
    for cand in entry.get("password_scheme", {}).get("candidates", []):
        pw = cand.replace("$PRODUCT", str(entry.get("product", "")))
        if pw:
            out.append(pw.encode())
    return out


def _unzip(
    data: bytes, pwds: list[bytes], limit: int = MAX_DECOMPRESS
) -> dict[str, bytes]:
    """Return {name: bytes} for a (possibly ZipCrypto) archive.

    Members are read into memory, so the archive's declared sizes must fit in
    `limit` together. zipfile never returns more than a member's declared
    file_size (and fails the CRC if the data disagrees), so checking the
    declarations up front bounds the real output too.
    """
    zf = zipfile.ZipFile(io.BytesIO(data))
    members = [i for i in zf.infolist() if not i.is_dir()]
    declared = sum(i.file_size for i in members)
    if declared > limit:
        raise SystemExit(
            f"zip members declare {declared} bytes > {limit}; "
            "refusing to expand (raise limits.max_expand_mib "
            "in the catalog if this image really is that large)"
        )
    encrypted = any(i.flag_bits & 0x1 for i in members)
    out: dict[str, bytes] = {}
    for info in members:
        if not encrypted:
            out[info.filename] = zf.read(info)
            continue
        for pw in pwds:
            try:
                zf.setpassword(pw)
                out[info.filename] = zf.read(info)
                break
            # ZipCrypto checks a password against ONE byte, so ~1 in 256 wrong
            # candidates passes it and only fails later: in inflate (zlib.error)
            # or at the CRC check (BadZipFile). All of these mean "try the next".
            except (RuntimeError, zipfile.BadZipFile, zlib.error, EOFError):
                continue
        else:
            raise SystemExit(
                f"no catalog password opened {info.filename!r}; "
                "add the vendor string to password_scheme.candidates"
            )
    return out


def _zip_kinds(data: bytes) -> tuple[set[str], list[str]]:
    """(symlink members, directory members). A symlink is a member whose Unix
    mode says so; its content is the link text, and zipfile reads it as an
    ordinary file."""
    infos = zipfile.ZipFile(io.BytesIO(data)).infolist()
    links = {
        i.filename
        for i in infos
        if i.create_system == ZIP_UNIX and (i.external_attr >> 16) & S_IFMT == S_IFLNK
    }
    return links, [i.filename for i in infos if i.is_dir()]


def _strip_uimage(data: bytes) -> bytes:
    return data[64:]  # 64-byte legacy U-Boot header, then the payload


def _too_big(kind: str, limit: int) -> SystemExit:
    return SystemExit(
        f"{kind} payload exceeds {limit} bytes; refusing to expand "
        "(raise limits.max_expand_mib in the catalog if needed)"
    )


def _gunzip(data: bytes, limit: int = MAX_DECOMPRESS) -> bytes:
    """Decompress one gzip member, refusing to expand past `limit` (bomb guard)."""
    dec = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    out = dec.decompress(data, limit + 1)
    if len(out) > limit or dec.unconsumed_tail:
        raise _too_big("gzip", limit)
    return _finish("gzip", out + dec.flush(), dec.eof, limit)


def _finish(kind: str, out: bytes, eof: bool, limit: int) -> bytes:
    if len(out) > limit:
        raise _too_big(kind, limit)
    if not eof:  # truncated stream: a partial payload would look like a real layer
        raise EOFError(f"{kind} stream ends early")
    return out


def _bunzip2(data: bytes, limit: int = MAX_DECOMPRESS) -> bytes:
    dec = bz2.BZ2Decompressor()
    out = dec.decompress(data, max_length=limit + 1)
    return _finish("bzip2", out, dec.eof, limit)


def _unlzma(data: bytes, fmt: int, limit: int = MAX_DECOMPRESS) -> bytes:
    dec = lzma.LZMADecompressor(format=fmt)
    out = dec.decompress(data, max_length=limit + 1)
    return _finish("xz/lzma", out, dec.eof, limit)


# (relative path, file bytes, link target): exactly one of the last two is set
Entry = tuple[str, bytes | None, str | None]
# Called once per member of an archive or filesystem tree, as (container row
# path, separator, member path, file bytes, link target); container + separator
# + member is the member's manifest path. oracle/stage.py uses it to rebuild a
# sysroot from exactly the bytes the manifest describes, without a second
# extractor.
Sink = Callable[[str, str, str, bytes | None, str | None], None]


def _check_total(kind: str, entries: list[Entry], limit: int) -> list[Entry]:
    total = sum(len(e[1]) for e in entries if e[1] is not None)
    if total > limit:
        raise _too_big(kind, limit)
    return entries


def _tar_entries(data: bytes, limit: int = MAX_DECOMPRESS) -> list[Entry]:
    """Members of an uncompressed tar, in memory (compression is its own layer)."""
    out: list[Entry] = []
    dirs: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tf:
        members = tf.getmembers()
        if sum(m.size for m in members if m.isfile()) > limit:
            raise _too_big("tar", limit)
        for m in members:
            name = m.name.removeprefix("./")
            if m.issym():
                out.append((name, None, m.linkname))
            elif m.isfile() or m.islnk():
                fh = tf.extractfile(m)
                out.append((name, fh.read() if fh else b"", None))
            elif m.isdir():
                dirs.append(name)
    return with_empty_dirs(out, dirs)


def _cpio_entries(data: bytes, limit: int = MAX_DECOMPRESS) -> list[Entry]:
    """Regular files, symlinks and empty directories of a newc/crc or odc cpio
    archive."""
    out: list[Entry] = []
    dirs: list[str] = []
    pos, total = 0, 0
    while pos < len(data):
        magic = data[pos : pos + 6]
        if magic in CPIO_NEWC:
            hdr = data[pos + 6 : pos + 110]
            if len(hdr) < 104:
                raise ValueError("truncated cpio header")
            fields = [int(hdr[i : i + 8], 16) for i in range(0, 104, 8)]
            mode, filesize, namesize = fields[1], fields[6], fields[11]
            name_at = pos + 110
            data_at = (name_at + namesize + 3) & ~3
            next_at = (data_at + filesize + 3) & ~3
        elif magic == CPIO_ODC:
            hdr = data[pos : pos + 76]
            if len(hdr) < 76:
                raise ValueError("truncated cpio header")
            mode = int(hdr[18:24], 8)
            namesize, filesize = int(hdr[59:65], 8), int(hdr[65:76], 8)
            name_at = pos + 76
            data_at = name_at + namesize
            next_at = data_at + filesize
        else:
            raise ValueError(f"bad cpio magic at {pos}")
        name = (
            data[name_at : name_at + namesize].rstrip(b"\0").decode("utf-8", "replace")
        )
        if name == "TRAILER!!!":
            break
        body = data[data_at : data_at + filesize]
        if len(body) != filesize:
            raise ValueError("truncated cpio member")
        total += filesize
        if total > limit:
            raise _too_big("cpio", limit)
        name = name.removeprefix("./")
        if mode & S_IFMT == S_IFLNK:
            out.append((name, None, body.decode("utf-8", "replace")))
        elif mode & S_IFMT == S_IFREG:
            out.append((name, body, None))
        elif mode & S_IFMT == S_IFDIR:
            dirs.append(name)
        pos = next_at
    return with_empty_dirs(out, dirs)


def _tree_entries(root: Path) -> list[Entry]:
    """Regular files and symlinks under `root`, sorted by relative path.

    Symlinks are recorded with their target and never followed: rdump recreates
    them as real links, and an absolute target (/etc/mtab, /bin/busybox) would
    otherwise resolve on the HOST and hash a runner file as firmware. os.walk
    does not descend into symlinked directories either.
    """
    out: list[Entry] = []
    dirs: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                # Not Path.readlink(): a Path normalises the target text
                # ("a//b/" -> "a/b"), and the manifest hashes it verbatim.
                out.append((rel, None, os.readlink(p)))  # noqa: PTH115
            elif name in filenames and p.is_file():
                out.append((rel, p.read_bytes(), None))
            elif name in dirnames:
                dirs.append(rel)
    return with_empty_dirs(out, dirs)


def with_empty_dirs(entries: list[Entry], dirs: list[str]) -> list[Entry]:
    """Sorted entries plus (dir, None, None) for every directory nothing else
    lives under.

    Directories that hold an entry are implied by its path; an EMPTY one (a
    rootfs's /var, /tmp, /mnt) would otherwise vanish from the manifest, and a
    sysroot staged from it would lack directories the firmware writes into.
    """
    have = {e[0].strip("/") for e in entries}
    wanted = {d.strip("/") for d in dirs} - {""}
    occupied: set[str] = set()
    for name in have | wanted:
        parts = name.split("/")
        occupied.update("/".join(parts[:i]) for i in range(1, len(parts)))
    empty: list[Entry] = [(d, None, None) for d in wanted - occupied - have]
    return sorted([*entries, *empty], key=lambda e: e[0])


# Extraction tools run unconfined only when main() is told --no-sandbox.
SANDBOX = True


def _run_tool(argv: list[str], work: Path) -> subprocess.CompletedProcess[str]:
    if SANDBOX:
        argv = jail.command(argv, work.resolve())
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def _debugfs_real_errors(stderr: str) -> list[str]:
    """Keep only genuine rdump failures from debugfs stderr.

    Two kinds of noise are expected and harmless:
      * the version banner debugfs prints on every run ("debugfs 1.47.0 ...");
      * failing to give a dumped file its owner, mode or times as non-root:
        "Operation not permitted" outside a sandbox, "Invalid argument while
        changing ownership" inside bubblewrap's user namespace (the image's
        uids are not mapped there). Ownership is not part of the manifest.
    Anything else (bad image, I/O error) is a real failure that would otherwise
    leave a silent empty tree.
    """
    return [
        ln
        for ln in stderr.splitlines()
        if ln.strip()
        and not ln.startswith("debugfs ")
        and "Operation not permitted" not in ln
        and "while changing ownership of" not in ln
    ]


def _unsquashfs_real_errors(stderr: str) -> list[str]:
    """unsquashfs cannot create device nodes, fifos or sockets without root and
    says so per node (exit code 2, 'non-fatal'). Those nodes are not files, so
    the manifest loses nothing; every other line is a real error."""
    return [
        ln
        for ln in stderr.splitlines()
        if ln.strip() and "because you're not superuser" not in ln
    ]


def _fs_dirs(img: bytes, work: Path) -> tuple[Path, Path]:
    root = work / "tree"
    if not SAFE_DEBUGFS_PATH.fullmatch(str(root)):
        raise SystemExit(
            f"work dir {str(root)!r} is not a plain path; "
            "pass --work without spaces, quotes or control characters"
        )
    tmp = work / "fs.img"
    tmp.write_bytes(img)
    return root, tmp


def _ext_tree(img: bytes, work: Path) -> list[Entry]:
    """List regular files and symlinks in an ext2/3/4 image via read-only debugfs.

    debugfs never mounts the image, so this works unprivileged in CI. One
    `rdump` writes the whole tree to a work dir; we then read it back.
    """
    # `root` is spliced into debugfs's own command language (-R), which splits
    # on whitespace and runs one request per line; only plain paths may go in.
    root, tmp = _fs_dirs(img, work)
    root.mkdir(parents=True, exist_ok=True)
    res = _run_tool(["debugfs", "-R", f"rdump / {root}", str(tmp)], work)
    real = _debugfs_real_errors(res.stderr)
    # A real debugfs error means this is not a readable ext filesystem. An empty
    # tree without one is valid (e.g. a filesystem of only device nodes).
    if real:
        raise ValueError("debugfs rdump failed:\n  " + "\n  ".join(real))
    return _tree_entries(root)


def _squashfs_tree(img: bytes, work: Path) -> list[Entry]:
    root, tmp = _fs_dirs(img, work)
    res = _run_tool(["unsquashfs", "-no-xattrs", "-n", "-d", str(root), str(tmp)], work)
    real = _unsquashfs_real_errors(res.stderr)
    # exit 0 = clean, 2 = non-fatal (device nodes as non-root), 1 = fatal
    entries = _tree_entries(root) if root.exists() else []
    if res.returncode not in (0, 2) or real:
        detail = "\n  ".join(real or res.stderr.splitlines()[:10] or ["(no output)"])
        raise ValueError(f"unsquashfs failed (exit {res.returncode}):\n  {detail}")
    return entries


def _cramfs_tree(img: bytes, work: Path) -> list[Entry]:
    root, tmp = _fs_dirs(img, work)
    res = _run_tool(["fsck.cramfs", f"--extract={root}", str(tmp)], work)
    entries = _tree_entries(root) if root.exists() else []
    if res.returncode != 0:
        detail = "\n  ".join(res.stderr.splitlines()[:10] or ["(no output)"])
        raise ValueError(f"fsck.cramfs failed (exit {res.returncode}):\n  {detail}")
    return entries


# Filesystem tag -> tree reader (looked up by name so tests can patch it). A
# filesystem its tool rejects becomes <tag>/unreadable and is left to the
# coverage gate; an empty tree from a tool that succeeded is a valid filesystem.
FS_READERS = {
    "ext-fs": "_ext_tree",
    "squashfs": "_squashfs_tree",
    "cramfs": "_cramfs_tree",
}

# Tags _walk opens. Meeting one past MAX_DEPTH means a layer was left unread.
CONTAINERS = frozenset(
    {"zip", "uImage", "gzip", "bzip2", "xz", "lzma", "tar", "cpio", *FS_READERS}
)
MAX_DEPTH = 8


@dataclass
class Walk:
    """Per-run state, so recursion does not thread five parameters."""

    pwds: list[bytes]
    work: Path
    limit: int = MAX_DECOMPRESS
    rows: list[Row] = field(default_factory=list)
    sink: Sink | None = None


def _record_tree(
    w: Walk, name: str, entries: list[Entry], depth: int, sep: str
) -> None:
    for fpath, blob, target in entries:
        if blob is None and target is None:
            # An empty directory (with_empty_dirs): a row so the sysroot
            # staged from the manifest has it, hashed like an empty file
            # since it has no content; never walked.
            w.rows.append(
                {
                    "path": f"{name}{sep}{fpath}",
                    "type": DIR_TYPE,
                    "size": 0,
                    "sha256": sha256(b""),
                }
            )
            if w.sink:
                w.sink(name, sep, fpath, None, None)
        elif target is not None:
            # The target is recorded by hash, like file contents, so the
            # TSV stays one line per entry whatever the link text holds.
            link = os.fsencode(target)
            path = f"{name}{sep}{fpath}"
            w.rows.append(
                {
                    "path": path,
                    "type": "symlink",
                    "size": len(link),
                    "sha256": sha256(link),
                }
            )
            if w.sink:
                w.sink(name, sep, fpath, None, target)
        else:
            if w.sink:
                w.sink(name, sep, fpath, blob or b"", None)
            _walk(w, f"{name}{sep}{fpath}", blob or b"", depth + 1)


def _fresh_dir(w: Walk, depth: int) -> Path:
    # One fresh dir per filesystem: an image can hold several filesystem layers
    # at the same depth (MH200N: rootfs + recovery rootfs), and sharing a dir
    # would make the second extraction collide with -- or mix into -- the first.
    w.work.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"fs{depth}-", dir=w.work))


def _walk(w: Walk, name: str, data: bytes, depth: int) -> None:
    tag = cpu_of(data)
    w.rows.append(
        {"path": name, "type": tag, "size": len(data), "sha256": sha256(data)}
    )
    row = w.rows[-1]
    if depth > MAX_DEPTH:
        # Not descended into: say so, or the manifest looks complete when it is not.
        if tag in CONTAINERS:
            row["type"] = f"{tag}/unreadable"
        return
    # A file can carry container magic without being a valid container (e.g. a
    # data file starting with "PK"). Record it as unreadable instead of aborting
    # the whole run; the row stays, it just isn't descended into, and the
    # coverage gate reports it.
    if tag == "zip":
        try:
            children = _unzip(data, w.pwds, w.limit)
            links, dirs = _zip_kinds(data)
        except (zipfile.BadZipFile, zlib.error, EOFError, ValueError):
            row["type"] = "zip/unreadable"
            return
        # fsdecode: the link text round-trips to the exact member bytes, so
        # the row keeps the hash it had when the member was typed `data`.
        entries: list[Entry] = [
            (n, None, os.fsdecode(b)) if n in links else (n, b, None)
            for n, b in children.items()
        ]
        _record_tree(w, name, with_empty_dirs(entries, dirs), depth, "!")
    elif tag == "uImage":
        _walk(w, f"{name}~payload", _strip_uimage(data), depth + 1)
    elif tag in ("gzip", "bzip2", "xz", "lzma"):
        try:
            if tag == "gzip":
                payload = _gunzip(data, w.limit)
            elif tag == "bzip2":
                payload = _bunzip2(data, w.limit)
            else:
                fmt = lzma.FORMAT_XZ if tag == "xz" else lzma.FORMAT_ALONE
                payload = _unlzma(data, fmt, w.limit)
        except (zlib.error, EOFError, OSError, lzma.LZMAError):
            # legacy lzma is a header heuristic: a miss is plain data, not a fault
            row["type"] = "data" if tag == "lzma" else f"{tag}/unreadable"
            return
        suffix = {"gzip": "gunzip", "bzip2": "bunzip2", "xz": "unxz", "lzma": "unlzma"}[
            tag
        ]
        _walk(w, f"{name}~{suffix}", payload, depth + 1)
    elif tag in ("tar", "cpio"):
        try:
            entries = (
                _tar_entries(data, w.limit)
                if tag == "tar"
                else _cpio_entries(data, w.limit)
            )
        except (tarfile.TarError, ValueError, EOFError):
            row["type"] = f"{tag}/unreadable"
            return
        _record_tree(w, name, entries, depth, "!")
    elif tag in FS_READERS:
        reader = globals()[FS_READERS[tag]]
        try:
            entries = reader(data, _fresh_dir(w, depth))
        except ValueError:
            row["type"] = f"{tag}/unreadable"
            return
        _record_tree(w, name, _check_total(tag, entries, w.limit), depth, ":/")


def walk(
    name: str,
    data: bytes,
    pwds: list[bytes],
    work: Path,
    rows: list[Row],
    depth: int = 0,
    *,
    limit: int = MAX_DECOMPRESS,
    sink: Sink | None = None,
) -> None:
    """Recurse through container layers, recording a manifest row per file."""
    w = Walk(pwds, work, limit, rows, sink)
    _walk(w, name, data, depth)


def undecoded(rows: list[Row]) -> list[Row]:
    """Rows that may hide an unread layer: unsupported or unreadable containers
    anywhere, and large opaque blobs outside a filesystem tree. Inside a
    filesystem (':/' in the path) a big data file is a leaf -- a web bundle, a
    database -- not a packaging layer, so size alone does not flag it there."""
    return [
        r
        for r in rows
        if r["type"] in UNSUPPORTED
        or r["type"].endswith("/unreadable")
        or (r["type"] == "data" and r["size"] >= LARGE_DATA and ":/" not in r["path"])
    ]


def coverage_gate(entry: CatalogEntry, rows: list[Row]) -> None:
    """Fail unless every undecoded row is acknowledged in the catalog, and every
    acknowledgement still matches a row (a stale one hides the next surprise)."""
    acked = {a["path"]: a for a in entry.get("undecoded_ok", [])}
    found = {r["path"]: r for r in undecoded(rows)}
    new = sorted(set(found) - set(acked))
    stale = sorted(set(acked) - set(found))
    if new or stale:
        lines = [f"  {found[p]['type']}\t{found[p]['size']}\t{p}" for p in new]
        lines += [
            f"  stale undecoded_ok entry (no such undecoded row): {p}" for p in stale
        ]
        raise SystemExit(
            "coverage gate: layers that were not unpacked:\n"
            + "\n".join(lines)
            + "\nadd a handler to unpack.py, or acknowledge each path under "
            "undecoded_ok in the catalog with a reason"
        )


def verify_wrapper(entry: CatalogEntry, data: bytes) -> None:
    """Refuse to walk anything but the catalog's wrapper."""
    w = entry["wrapper"]
    if len(data) != w["size"] or sha256(data) != w["sha256"]:
        raise SystemExit(
            f"image is not {w['filename']} from the catalog: got "
            f"{len(data)} bytes / {sha256(data)}, want {w['size']} / {w['sha256']}"
        )


def require_image(entry: CatalogEntry, rows: list[Row]) -> None:
    """The manifest header names image.sha256, so that image must be inside."""
    want = entry["image"]["sha256"]
    if not any(r["sha256"] == want for r in rows):
        raise SystemExit(
            f"catalog image {entry['image']['filename']} ({want}) "
            "was not found inside the wrapper"
        )


def expand_limit(entry: CatalogEntry) -> int:
    mib = entry.get("limits", {}).get("max_expand_mib")
    return MAX_DECOMPRESS if mib is None else mib * 1024 * 1024


def main() -> None:
    global SANDBOX
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("catalog")
    ap.add_argument("image", help="path from fwfetch (the wrapper on disk)")
    ap.add_argument("-o", "--out", required=True, help="manifest.tsv to write")
    ap.add_argument("--work", help="work dir for extracted files (temp if unset)")
    ap.add_argument(
        "--no-sandbox",
        action="store_true",
        help="run debugfs / unsquashfs / fsck.cramfs without bubblewrap",
    )
    args = ap.parse_args()
    SANDBOX = not args.no_sandbox

    entry = schema.load(args.catalog)
    pwds = passwords(entry)
    data = Path(args.image).read_bytes()
    verify_wrapper(entry, data)

    rows: list[Row] = []
    with contextlib.ExitStack() as stack:
        if args.work:
            work = Path(args.work)  # kept: the caller asked for the files
        else:
            # Extracted vendor files must not outlive the run. The tree is gone
            # before the manifest is written; rows already hold every fact.
            work = Path(
                stack.enter_context(
                    tempfile.TemporaryDirectory(
                        prefix="own-fw-", ignore_cleanup_errors=True
                    )
                )
            )
        # Root every path at the catalog filename, not the on-disk name: the
        # fwfetch cache stores files as <sha256>.zip, a local copy keeps its
        # own name, and both must produce the same manifest.
        walk(
            entry["wrapper"]["filename"],
            data,
            pwds,
            work,
            rows,
            limit=expand_limit(entry),
        )
    require_image(entry, rows)
    coverage_gate(entry, rows)

    # Deterministic: sorted by path, no timestamps -> zero diff on a clean re-run.
    rows.sort(key=lambda r: r["path"])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="\n", encoding="utf-8") as fh:
        fh.write(f"# product={entry['product']} version={entry['version']}\n")
        fh.write(f"# image_sha256={entry['image']['sha256']}\n")
        fh.write(f"# tool_version={TOOL_VERSION}\n")
        fh.write("path\ttype\tsize\tsha256\n")
        for r in rows:
            fh.write(
                f"{tsv_field(r['path'])}\t{tsv_field(r['type'])}"
                f"\t{r['size']}\t{r['sha256']}\n"
            )
    print(f"{len(rows)} entries -> {out}")


if __name__ == "__main__":
    main()
