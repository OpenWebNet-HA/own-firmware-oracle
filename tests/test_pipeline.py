"""Unit tests on tiny *synthetic* fixtures -- no vendor firmware involved.

These run in pr.yml on every PR (forks included), with no secrets and no
image. They prove the layer logic and the guard without touching anything
proprietary.
"""
import gzip
import io
import struct
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import guard
import plan
import unpack


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


def test_gunzip_rejects_bomb():
    # 2 MiB of zeros compresses tiny; a 1 KiB limit must refuse to expand it.
    bomb = gzip.compress(b"\x00" * (2 * 1024 * 1024))
    try:
        unpack._gunzip(bomb, limit=1024)
    except SystemExit:
        return
    raise AssertionError("gunzip did not enforce the limit")


def test_gunzip_roundtrip_under_limit():
    assert unpack._gunzip(gzip.compress(b"hello"), limit=1024) == b"hello"


def test_debugfs_banner_and_chown_are_not_errors():
    # The version banner and unprivileged chown lines must NOT count as errors.
    noise = (
        "debugfs 1.47.0 (5-Feb-2023)\n"
        "rdump: Operation not permitted while changing ownership of /tree/etc\n"
    )
    assert unpack._debugfs_real_errors(noise) == []


def test_debugfs_real_error_is_kept():
    bad = "debugfs 1.47.0 (5-Feb-2023)\nrdump: Bad magic number in super-block\n"
    assert unpack._debugfs_real_errors(bad) == [
        "rdump: Bad magic number in super-block"
    ]


def _catalog(tmp_path):
    cat = tmp_path / "catalog" / "MH200N" / "010108.yaml"
    cat.parent.mkdir(parents=True)
    cat.write_text(
        "product: MH200N\nversion: '010108'\nimage:\n  sha256: 'abc123'\n"
    )
    return cat


def test_is_stale_on_missing_and_mismatched_keys(tmp_path):
    cat = _catalog(tmp_path)
    results = tmp_path / "results"
    # no manifest yet -> stale
    assert plan.is_stale(cat, results) is True

    man = results / "MH200N" / "010108" / "manifest.tsv"
    man.parent.mkdir(parents=True)

    # right image, wrong (old) tool version -> still stale
    man.write_text("# image_sha256=abc123\n# tool_version=0\npath\n")
    assert plan.is_stale(cat, results) is True

    # both keys current -> fresh
    man.write_text(
        f"# image_sha256=abc123\n# tool_version={unpack.TOOL_VERSION}\npath\n"
    )
    assert plan.is_stale(cat, results) is False
