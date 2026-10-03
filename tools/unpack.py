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
  ext2/3/4 (53 ef @ 0x438)  file tree listed via debugfs (read-only)

Nothing about this script is image-specific: point it at any catalog entry.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import struct
import subprocess
import tempfile
import zipfile
import zlib
from pathlib import Path

import yaml

# Manifest key component. Bump when a change to this tool would alter the
# manifest for an unchanged image; plan.py treats a mismatch as stale.
TOOL_VERSION = "1"

UIMAGE_MAGIC = 0x27051956
EXT_MAGIC = 0xEF53
MAX_DECOMPRESS = 256 * 1024 * 1024  # cap a single gzip layer (bomb guard)

# ELF e_machine -> human CPU name (enough to answer "is this ARM Linux?")
ELF_MACHINE = {0x28: "ARM", 0x3E: "x86-64", 0x03: "x86", 0xB7: "AArch64", 0x08: "MIPS"}


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def cpu_of(b: bytes) -> str:
    """Return a short type/CPU tag for a blob (facts for the manifest)."""
    if b[:4] == b"\x7fELF":
        machine = struct.unpack_from("<H", b, 18)[0]
        kind = {1: "reloc", 2: "exec", 3: "dyn", 4: "core"}.get(b[16], "elf")
        return f"ELF/{ELF_MACHINE.get(machine, hex(machine))}/{kind}"
    if b[:2] == b"PK":
        return "zip"
    if b[:2] == b"\x1f\x8b":
        return "gzip"
    if len(b) > 0x43A and struct.unpack_from("<H", b, 0x438)[0] == EXT_MAGIC:
        return "ext-fs"
    if struct.unpack_from(">I", b, 0)[0] == UIMAGE_MAGIC:
        return "uImage"
    return "data"


def passwords(entry: dict) -> list[bytes]:
    out: list[bytes] = []
    for cand in entry.get("password_scheme", {}).get("candidates", []):
        cand = cand.replace("$PRODUCT", str(entry.get("product", "")))
        if cand:
            out.append(cand.encode())
    return out


def _unzip(data: bytes, pwds: list[bytes]) -> dict[str, bytes]:
    """Return {name: bytes} for a (possibly ZipCrypto) archive."""
    zf = zipfile.ZipFile(io.BytesIO(data))
    encrypted = any(i.flag_bits & 0x1 for i in zf.infolist())
    out: dict[str, bytes] = {}
    for info in zf.infolist():
        if info.is_dir():
            continue
        if not encrypted:
            out[info.filename] = zf.read(info)
            continue
        for pw in pwds:
            try:
                zf.setpassword(pw)
                out[info.filename] = zf.read(info)
                break
            except (RuntimeError, zipfile.BadZipFile):
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


def _ext_tree(img: bytes, work: Path) -> list[tuple[str, bytes]]:
    """List regular files in an ext2/3/4 image via read-only debugfs.

    debugfs never mounts the image, so this works unprivileged in CI. One
    `rdump` writes the whole tree to a work dir; we then read it back. Chown
    lines on rdump are expected (we are not root) and harmless -- contents and
    paths are intact, which is all the manifest needs.
    """
    tmp = work / "fs.img"
    tmp.write_bytes(img)
    root = work / "tree"
    root.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(
        ["debugfs", "-R", f"rdump / {root}", str(tmp)],
        capture_output=True, text=True, check=False,
    )
    # rdump exits non-zero purely because it can't chown/chmod as non-root;
    # those lines are expected. Any OTHER stderr line is a real failure
    # (bad image, I/O error) that would otherwise yield a silent empty tree.
    real = [
        ln for ln in res.stderr.splitlines()
        if ln.strip() and "Operation not permitted" not in ln
    ]
    if real:
        raise SystemExit("debugfs rdump failed:\n  " + "\n  ".join(real[:10]))
    return [
        (str(p.relative_to(root)).replace("\\", "/"), p.read_bytes())
        for p in sorted(root.rglob("*"))
        if p.is_file()
    ]


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
    if tag == "zip":
        for child, blob in _unzip(data, pwds).items():
            walk(f"{name}!{child}", blob, pwds, work, rows, depth + 1)
    elif tag == "uImage":
        walk(f"{name}~payload", _strip_uimage(data), pwds, work, rows, depth + 1)
    elif tag == "gzip":
        walk(f"{name}~gunzip", _gunzip(data), pwds, work, rows, depth + 1)
    elif tag == "ext-fs":
        sub = work / f"ext{depth}"
        sub.mkdir(parents=True, exist_ok=True)
        for fpath, blob in _ext_tree(data, sub):
            walk(f"{name}:/{fpath}", blob, pwds, work, rows, depth + 1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("catalog")
    ap.add_argument("image", help="path from fwfetch (the wrapper on disk)")
    ap.add_argument("-o", "--out", required=True, help="manifest.tsv to write")
    ap.add_argument("--work", help="work dir for extracted files (temp if unset)")
    args = ap.parse_args()

    entry = yaml.safe_load(Path(args.catalog).read_text())
    pwds = passwords(entry)
    data = Path(args.image).read_bytes()

    work = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix="own-fw-"))
    rows: list[dict] = []
    walk(Path(args.image).name, data, pwds, work, rows)

    # Deterministic: sorted by path, no timestamps -> zero diff on a clean re-run.
    rows.sort(key=lambda r: r["path"])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="\n") as fh:
        fh.write(f"# product={entry['product']} version={entry['version']}\n")
        fh.write(f"# image_sha256={entry['image']['sha256']}\n")
        fh.write(f"# tool_version={TOOL_VERSION}\n")
        fh.write("path\ttype\tsize\tsha256\n")
        for r in rows:
            fh.write(f"{r['path']}\t{r['type']}\t{r['size']}\t{r['sha256']}\n")
    print(f"{len(rows)} entries -> {out}")


if __name__ == "__main__":
    main()
