#!/usr/bin/env python3
"""unpack -- turn one firmware image into a manifest of what's inside it.

Walks the BTicino/Legrand packaging layers and records, for every extracted
program, its path / type / CPU / SHA-256. That TSV is the only thing published;
the extracted files land under a local work dir (default: a temp dir) and are
never committed.

Layer chain handled (auto-detected by magic, not hard-coded per image):
  zip                       wrapper and inner archives (ZipCrypto via the
                            catalog's documented password_scheme -- known
                            vendor strings, tried in order; no brute force)
  uImage (0x27051956)       64-byte U-Boot header stripped, payload recursed
  gzip  (1f 8b)             decompressed, payload recursed
  ext2/3/4 (53 ef @ 0x438)  file tree listed via debugfs (read-only);
                            symlinks are recorded, never followed

The image on disk must be the catalog's wrapper (size + SHA-256), and the
catalog's inner image must turn up inside it; anything else is refused, so a
manifest header always describes the bytes that were actually walked.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import os
import re
import struct
import subprocess
import tempfile
import zipfile
import zlib
from pathlib import Path

import schema

# Manifest key component. Bump when a change to this tool would alter the
# manifest for an unchanged image; plan.py treats a mismatch as stale.
# 2: paths rooted at wrapper.filename; symlinks recorded instead of followed.
TOOL_VERSION = "2"

SAFE_DEBUGFS_PATH = re.compile(r"[A-Za-z0-9_./+-]+")
# Manifest fields are TSV: a tab or newline in a member name would add a
# column or a row, so backslash and control characters are escaped as \xNN.
TSV_UNSAFE = re.compile(r"[\\\x00-\x1f\x7f]")

UIMAGE_MAGIC = 0x27051956
EXT_MAGIC = 0xEF53
# Bomb guard: cap on what one gzip layer, or all members of one zip archive
# together, may expand to.
MAX_DECOMPRESS = 256 * 1024 * 1024

# ELF e_machine -> human CPU name (enough to answer "is this ARM Linux?")
ELF_MACHINE = {0x28: "ARM", 0x3E: "x86-64", 0x03: "x86", 0xB7: "AArch64", 0x08: "MIPS"}


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def tsv_field(s: str) -> str:
    """Escape backslash and control characters so one row stays one TSV line."""
    return TSV_UNSAFE.sub(lambda m: f"\\x{ord(m.group()):02x}", s)


def cpu_of(b: bytes) -> str:
    """Return a short type/CPU tag for a blob (facts for the manifest).

    Every header read is length-checked: real filesystems contain empty and
    tiny files, and a truncated header must classify, not crash.
    """
    if not b:
        return "empty"
    if b[:4] == b"\x7fELF":
        if len(b) < 20:
            return "ELF/truncated"
        machine = struct.unpack_from("<H", b, 18)[0]
        kind = {1: "reloc", 2: "exec", 3: "dyn", 4: "core"}.get(b[16], "elf")
        return f"ELF/{ELF_MACHINE.get(machine, hex(machine))}/{kind}"
    if b[:2] == b"PK":
        return "zip"
    if b[:2] == b"\x1f\x8b":
        return "gzip"
    if len(b) > 0x43A and struct.unpack_from("<H", b, 0x438)[0] == EXT_MAGIC:
        return "ext-fs"
    if len(b) >= 64 and struct.unpack_from(">I", b, 0)[0] == UIMAGE_MAGIC:
        return "uImage"  # a uImage needs its full 64-byte header
    return "data"


def passwords(entry: dict) -> list[bytes]:
    out: list[bytes] = []
    for cand in entry.get("password_scheme", {}).get("candidates", []):
        cand = cand.replace("$PRODUCT", str(entry.get("product", "")))
        if cand:
            out.append(cand.encode())
    return out


def _unzip(data: bytes, pwds: list[bytes],
           limit: int = MAX_DECOMPRESS) -> dict[str, bytes]:
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
        raise SystemExit(f"zip members declare {declared} bytes > {limit}; "
                         "refusing to expand")
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


def _strip_uimage(data: bytes) -> bytes:
    return data[64:]  # 64-byte legacy U-Boot header, then the payload


def _gunzip(data: bytes, limit: int = MAX_DECOMPRESS) -> bytes:
    """Decompress one gzip member, refusing to expand past `limit` (bomb guard)."""
    dec = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    out = dec.decompress(data, limit + 1)
    if len(out) > limit or dec.unconsumed_tail:
        raise SystemExit(f"gzip payload exceeds {limit} bytes; refusing to expand")
    return out + dec.flush()


def _debugfs_real_errors(stderr: str) -> list[str]:
    """Keep only genuine rdump failures from debugfs stderr.

    Two kinds of noise are expected and harmless:
      * the version banner debugfs prints on every run ("debugfs 1.47.0 ...");
      * "Operation not permitted" while chown/chmod/utimes-ing as non-root.
    Anything else (bad image, I/O error) is a real failure that would otherwise
    leave a silent empty tree.
    """
    return [
        ln for ln in stderr.splitlines()
        if ln.strip()
        and not ln.startswith("debugfs ")
        and "Operation not permitted" not in ln
    ]


# (relative path, file bytes, link target): exactly one of the last two is set
Entry = tuple[str, bytes | None, str | None]


def _tree_entries(root: Path) -> list[Entry]:
    """Regular files and symlinks under `root`, sorted by relative path.

    Symlinks are recorded with their target and never followed: rdump recreates
    them as real links, and an absolute target (/etc/mtab, /bin/busybox) would
    otherwise resolve on the HOST and hash a runner file as firmware. os.walk
    does not descend into symlinked directories either.
    """
    out: list[Entry] = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                out.append((rel, None, os.readlink(p)))
            elif name in filenames and p.is_file():
                out.append((rel, p.read_bytes(), None))
    return sorted(out, key=lambda e: e[0])


def _ext_tree(img: bytes, work: Path) -> list[Entry]:
    """List regular files and symlinks in an ext2/3/4 image via read-only debugfs.

    debugfs never mounts the image, so this works unprivileged in CI. One
    `rdump` writes the whole tree to a work dir; we then read it back.
    """
    root = work / "tree"
    # `root` is spliced into debugfs's own command language (-R), which splits
    # on whitespace and runs one request per line; only plain paths may go in.
    if not SAFE_DEBUGFS_PATH.fullmatch(str(root)):
        raise SystemExit(f"work dir {str(root)!r} is not a plain path; "
                         "pass --work without spaces, quotes or control characters")
    tmp = work / "fs.img"
    tmp.write_bytes(img)
    root.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(
        ["debugfs", "-R", f"rdump / {root}", str(tmp)],
        capture_output=True, text=True, check=False,
    )
    entries = _tree_entries(root)
    real = _debugfs_real_errors(res.stderr)
    # Fail on a real error, or on an empty tree (which means rdump did nothing).
    if real or not entries:
        detail = "\n  ".join(real or res.stderr.splitlines()[:10] or ["(no output)"])
        raise SystemExit(f"debugfs rdump produced no usable tree:\n  {detail}")
    return entries


def walk(name: str, data: bytes, pwds: list[bytes], work: Path,
         rows: list[dict], depth: int = 0) -> None:
    """Recurse through container layers, recording a manifest row per file."""
    tag = cpu_of(data)
    rows.append({
        "path": name,
        "type": tag,
        "size": len(data),
        "sha256": sha256(data),
    })
    if depth > 8:
        return
    # A file can carry container magic without being a valid container (e.g. a
    # data file starting with "PK"). Record it as unreadable instead of aborting
    # the whole run; the row stays, it just isn't descended into.
    if tag == "zip":
        try:
            children = _unzip(data, pwds)
        except (zipfile.BadZipFile, zlib.error, EOFError, ValueError):
            rows[-1]["type"] = "zip/unreadable"
            return
        for child, blob in children.items():
            walk(f"{name}!{child}", blob, pwds, work, rows, depth + 1)
    elif tag == "uImage":
        walk(f"{name}~payload", _strip_uimage(data), pwds, work, rows, depth + 1)
    elif tag == "gzip":
        try:
            payload = _gunzip(data)
        except (zlib.error, EOFError):
            rows[-1]["type"] = "gzip/unreadable"
            return
        walk(f"{name}~gunzip", payload, pwds, work, rows, depth + 1)
    elif tag == "ext-fs":
        # One fresh dir per filesystem: an image can hold several ext layers
        # at the same depth (MH200N: rootfs + recovery rootfs), and sharing a
        # dir would make the second rdump collide with -- or mix into -- the first.
        work.mkdir(parents=True, exist_ok=True)
        sub = Path(tempfile.mkdtemp(prefix=f"ext{depth}-", dir=work))
        for fpath, blob, target in _ext_tree(data, sub):
            if target is not None:
                # The target is recorded by hash, like file contents, so the
                # TSV stays one line per entry whatever the link text holds.
                link = os.fsencode(target)
                rows.append({"path": f"{name}:/{fpath}", "type": "symlink",
                             "size": len(link), "sha256": sha256(link)})
            else:
                walk(f"{name}:/{fpath}", blob, pwds, work, rows, depth + 1)


def verify_wrapper(entry: dict, data: bytes) -> None:
    """Refuse to walk anything but the catalog's wrapper."""
    w = entry["wrapper"]
    if len(data) != w["size"] or sha256(data) != w["sha256"]:
        raise SystemExit(
            f"image is not {w['filename']} from the catalog: got "
            f"{len(data)} bytes / {sha256(data)}, want {w['size']} / {w['sha256']}"
        )


def require_image(entry: dict, rows: list[dict]) -> None:
    """The manifest header names image.sha256, so that image must be inside."""
    want = entry["image"]["sha256"]
    if not any(r["sha256"] == want for r in rows):
        raise SystemExit(
            f"catalog image {entry['image']['filename']} ({want}) "
            "was not found inside the wrapper"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("catalog")
    ap.add_argument("image", help="path from fwfetch (the wrapper on disk)")
    ap.add_argument("-o", "--out", required=True, help="manifest.tsv to write")
    ap.add_argument("--work", help="work dir for extracted files (temp if unset)")
    args = ap.parse_args()

    entry = schema.load(args.catalog)
    pwds = passwords(entry)
    data = Path(args.image).read_bytes()
    verify_wrapper(entry, data)

    rows: list[dict] = []
    with contextlib.ExitStack() as stack:
        if args.work:
            work = Path(args.work)  # kept: the caller asked for the files
        else:
            # Extracted vendor files must not outlive the run. The tree is gone
            # before the manifest is written; rows already hold every fact.
            work = Path(stack.enter_context(tempfile.TemporaryDirectory(
                prefix="own-fw-", ignore_cleanup_errors=True)))
        # Root every path at the catalog filename, not the on-disk name: the
        # fwfetch cache stores files as <sha256>.zip, a local copy keeps its
        # own name, and both must produce the same manifest.
        walk(entry["wrapper"]["filename"], data, pwds, work, rows)
    require_image(entry, rows)

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
            fh.write(f"{tsv_field(r['path'])}\t{tsv_field(r['type'])}"
                     f"\t{r['size']}\t{r['sha256']}\n")
    print(f"{len(rows)} entries -> {out}")


if __name__ == "__main__":
    main()
