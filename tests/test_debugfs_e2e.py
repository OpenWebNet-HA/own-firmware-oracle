"""Real debugfs, real layers: a synthetic firmware built with mke2fs.

The other tests fake _ext_tree. These build a genuine ext2 image (mke2fs -d,
no root needed), wrap it the way the MH200N is wrapped -- zip > zip > uImage >
gzip > ext2 -- and run unpack end to end, twice: the manifest must come out
byte for byte the same, which is the property reproduce.yml checks on the real
image every week.

Skipped when e2fsprogs is missing (e.g. macOS); CI sets ORACLE_REQUIRE_DEBUGFS=1
so a runner without it fails instead of skipping silently.
"""

import gzip
import hashlib
import io
import os
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import unpack


def _find(tool: str) -> str | None:
    found = shutil.which(tool)
    if found:
        return found
    for d in ("/usr/sbin", "/sbin"):  # not on every user's PATH
        if (Path(d) / tool).exists():
            return str(Path(d) / tool)
    return None


MKE2FS, DEBUGFS = _find("mke2fs"), _find("debugfs")
MISSING = not (MKE2FS and DEBUGFS)

if MISSING and os.environ.get("ORACLE_REQUIRE_DEBUGFS") == "1":
    raise RuntimeError("ORACLE_REQUIRE_DEBUGFS=1 but mke2fs / debugfs are missing")

pytestmark = [
    pytest.mark.debugfs,
    pytest.mark.skipif(MISSING, reason="needs e2fsprogs (mke2fs + debugfs)"),
]

ARM_ELF = b"\x7fELF\x01\x01\x01" + b"\x00" * 9 + struct.pack("<HH", 2, 0x28)
ARM_ELF += b"\x00" * 44


@pytest.fixture(autouse=True)
def _debugfs_on_path(monkeypatch):
    # These tests are about the readers, not the jail (test_unpack_formats covers
    # that), and hosted runners ship no bubblewrap: run the tools directly.
    monkeypatch.setattr(unpack, "SANDBOX", False)
    if DEBUGFS:
        path = os.environ.get("PATH", "")
        monkeypatch.setenv("PATH", f"{Path(DEBUGFS).parent}{os.pathsep}{path}")


def _ext2(tmp_path: Path) -> bytes:
    src = tmp_path / "rootfs-src"
    (src / "bin").mkdir(parents=True)
    (src / "etc").mkdir()
    (src / "bin" / "busybox").write_bytes(ARM_ELF)
    (src / "bin" / "ls").symlink_to("busybox")
    (src / "etc" / "mtab").symlink_to("/proc/mounts")  # absolute: never followed
    (src / "etc" / "version").write_text("1.1.8\n")
    (src / "empty").write_bytes(b"")
    img = tmp_path / "rootfs.ext2"
    subprocess.run(
        [MKE2FS, "-q", "-F", "-t", "ext2", "-d", str(src), str(img), "1024"],
        check=True,
        capture_output=True,
    )
    return img.read_bytes()


def _uimage(payload: bytes) -> bytes:
    return struct.pack(">I", unpack.UIMAGE_MAGIC) + b"\x00" * 60 + payload


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def work(tmp_path: Path) -> Path:
    # walk() hands _ext_tree a fresh mkdtemp dir; do the same here
    w = tmp_path / "w"
    w.mkdir()
    return w


def test_ext_tree_lists_files_and_records_symlinks(tmp_path, work):
    entries = {
        path: (blob, target)
        for path, blob, target in unpack._ext_tree(_ext2(tmp_path), work)
    }
    assert entries["bin/busybox"] == (ARM_ELF, None)
    assert entries["bin/ls"] == (None, "busybox")
    assert entries["etc/mtab"] == (None, "/proc/mounts")
    assert entries["etc/version"] == (b"1.1.8\n", None)
    assert entries["empty"] == (b"", None)


def test_ext_tree_fails_loudly_on_a_corrupt_filesystem(work):
    with pytest.raises(SystemExit, match="no usable tree"):
        unpack._ext_tree(b"\x00" * 4096, work)


def test_layered_firmware_unpacks_to_the_same_manifest_twice(tmp_path, monkeypatch):
    rootfs = _uimage(gzip.compress(_ext2(tmp_path), mtime=0))
    fwz = _zip({"Info.txt": b"fw 010108\n", "ubtweb_only.gz": rootfs})
    wrapper = _zip({"scheduler_010108.fwz": fwz})
    sha = hashlib.sha256
    cat = tmp_path / "catalog" / "MH200N" / "010108.yaml"
    cat.parent.mkdir(parents=True)
    cat.write_text(
        "product: MH200N\nversion: '010108'\n"
        f"wrapper:\n  filename: FW.zip\n  size: {len(wrapper)}\n"
        f"  sha256: '{sha(wrapper).hexdigest()}'\n"
        f"image:\n  filename: scheduler_010108.fwz\n  size: {len(fwz)}\n"
        f"  sha256: '{sha(fwz).hexdigest()}'\n"
    )
    img = tmp_path / "cache-name.zip"
    img.write_bytes(wrapper)

    manifests = []
    for run in ("a", "b"):
        out = tmp_path / run / "manifest.tsv"
        monkeypatch.setattr(sys, "argv", ["unpack", str(cat), str(img), "-o", str(out)])
        unpack.main()
        manifests.append(out.read_bytes())
    assert manifests[0] == manifests[1]

    rows = {
        ln.split("\t")[0]: ln.split("\t")[1]
        for ln in manifests[0].decode().splitlines()[4:]
    }
    fs = "FW.zip!scheduler_010108.fwz!ubtweb_only.gz~payload~gunzip"
    assert rows["FW.zip!scheduler_010108.fwz!ubtweb_only.gz"] == "uImage"
    assert rows[fs] == "ext-fs"
    assert rows[f"{fs}:/bin/busybox"] == "ELF/ARM/exec/32le/oabi"
    assert rows[f"{fs}:/bin/ls"] == "symlink"
    assert rows[f"{fs}:/etc/mtab"] == "symlink"
    assert rows[f"{fs}:/empty"] == "empty"
