"""Unit tests on tiny *synthetic* fixtures -- no vendor firmware involved.

These run in pr.yml on every PR (forks included), with no secrets and no
image. They prove the layer logic and the guard without touching anything
proprietary.
"""
import gzip
import io
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import guard  # noqa: E402
import unpack  # noqa: E402


def _zip(members: dict[str, bytes], password: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    raw = buf.getvalue()
    if password is None:
        return raw
    # pyzipper would give real ZipCrypto; for a unit test an unencrypted zip
    # exercises the same walk() path, so keep the dependency surface at zero.
    return raw


def test_cpu_of_detects_elf_arm():
    elf = b"\x7fELF" + b"\x01\x01\x01" + b"\x00" * 9
    elf += struct.pack("<HH", 2, 0x28)  # e_type=exec, e_machine=ARM
    assert unpack.cpu_of(elf) == "ELF/ARM/exec"


def test_cpu_of_detects_containers():
    assert unpack.cpu_of(b"PK\x03\x04rest") == "zip"
    assert unpack.cpu_of(gzip.compress(b"hi")) == "gzip"
    uimg = struct.pack(">I", unpack.UIMAGE_MAGIC) + b"\x00" * 60
    assert unpack.cpu_of(uimg) == "uImage"


def test_walk_recurses_zip_and_gzip():
    payload = b"\x7fELF" + b"\x00" * 60
    inner = _zip({"prog": payload})
    outer = _zip({"inner.zip": inner, "readme": gzip.compress(b"notes")})
    rows: list[dict] = []
    unpack.walk("top.zip", outer, [], Path("."), rows)
    paths = {r["path"] for r in rows}
    assert "top.zip!inner.zip!prog" in paths
    assert any(p.endswith("~gunzip") for p in paths)


def test_password_scheme_substitutes_product():
    entry = {"product": "MH200N",
             "password_scheme": {"candidates": ["SCHEDULER", "$PRODUCT"]}}
    assert unpack.passwords(entry) == [b"SCHEDULER", b"MH200N"]


def test_guard_flags_binary(tmp_path, monkeypatch):
    bad = tmp_path / "results" / "leak.bin"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"\x7fELFsomething")
    assert guard.is_binary(bad) is not None


def test_guard_passes_tsv(tmp_path):
    good = tmp_path / "manifest.tsv"
    good.write_text("path\ttype\tsize\tsha256\n")
    assert guard.is_binary(good) is None
