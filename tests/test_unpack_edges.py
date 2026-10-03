"""Error paths and size caps of the container readers: every malformed or
oversized layer must fail closed (an exception the walk turns into
<kind>/unreadable, or a SystemExit), never yield a partial layer."""

from __future__ import annotations

import gzip
import io
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import jail
import schema
import unpack
from test_unpack_formats import _newc, _tar

S_REG, S_LNK = 0o100644, 0o120777


def _odc(entries: list[tuple[str, int, bytes]]) -> bytes:
    out = b""
    for name, mode, body in [*entries, ("TRAILER!!!", 0, b"")]:
        nb = name.encode() + b"\0"
        hdr = b"070707" + b"0" * 12 + b"%06o" % mode + b"0" * 35
        hdr += b"%06o" % len(nb) + b"%011o" % len(body)
        out += hdr + nb + body
    return out


# --- cpio ------------------------------------------------------------------------


def test_odc_cpio_files_and_symlinks_are_read():
    blob = _odc([("./etc/a", S_REG, b"hello"), ("bin/sh", S_LNK, b"busybox")])
    # entries come back sorted by path, not in archive order
    assert unpack._cpio_entries(blob) == [
        ("bin/sh", None, "busybox"),
        ("etc/a", b"hello", None),
    ]


def test_newc_symlinks_are_read():
    blob = _newc([("sh", S_LNK, b"busybox")])
    assert unpack._cpio_entries(blob) == [("sh", None, "busybox")]


@pytest.mark.parametrize(
    "blob",
    [
        b"070701" + b"0" * 20,  # newc header cut short
        b"070707" + b"0" * 20,  # odc header cut short
        b"garbage!" * 4,  # no magic at all
        _newc([("f", S_REG, b"x" * 64)])[:-200],  # member body cut off
    ],
    ids=["newc-header", "odc-header", "bad-magic", "member-body"],
)
def test_malformed_cpio_is_rejected(blob):
    with pytest.raises(ValueError, match="cpio"):
        unpack._cpio_entries(blob)


def test_cpio_total_is_capped():
    blob = _newc([("a", S_REG, b"x" * 600), ("b", S_REG, b"y" * 600)])
    with pytest.raises(SystemExit, match="refusing to expand"):
        unpack._cpio_entries(blob, limit=1000)


def test_extracted_tree_total_is_capped():
    entries = [("a", b"x" * 600, None), ("b", b"y" * 600, None)]
    with pytest.raises(SystemExit, match="refusing to expand"):
        unpack._check_total("squashfs", entries, 1000)


def test_short_or_non_hex_cpio_look_alikes_stay_data():
    assert not unpack._is_cpio(b"070701" + b"0" * 10)
    assert not unpack._is_cpio(b"070707" + b"9" * 70)  # not octal
    assert unpack.cpu_of(b"7z\xbc\xaf\x27\x1c" + b"\0" * 32) == "7z"


# --- walk ------------------------------------------------------------------------


def _walk(blob: bytes, tmp_path: Path) -> dict[str, str]:
    rows: list[unpack.Row] = []
    unpack.walk("fw", blob, [], tmp_path, rows)
    return {r["path"]: r["type"] for r in rows}


def test_corrupt_tar_is_unreadable_not_fatal(tmp_path):
    fake = bytearray(1024)
    fake[257:262] = b"ustar"
    assert _walk(bytes(fake), tmp_path)["fw"] == "tar/unreadable"


def test_corrupt_cpio_is_unreadable_not_fatal(tmp_path):
    blob = b"070701" + b"0" * 40
    # Not detected as cpio (header too short), so it stays plain data.
    assert _walk(blob, tmp_path)["fw"] == "data"


def test_nesting_stops_at_depth_eight(tmp_path):
    blob = b"leaf" * 8
    for _ in range(12):
        blob = gzip.compress(blob)
    types = _walk(blob, tmp_path)
    assert len(types) == 10  # fw plus nine ~gunzip layers, then the cut-off
    assert set(types.values()) == {"gzip"}


def test_tar_symlinks_and_files_are_both_recorded(tmp_path):
    blob = _tar({"a": b"1"}, {"l": "a"})
    types = _walk(blob, tmp_path)
    assert types["fw!a"] == "data"
    assert types["fw!l"] == "symlink"


# --- filesystem tools --------------------------------------------------------------


def _fail(returncode: int, stderr: str = ""):
    def run(argv, work):
        return subprocess.CompletedProcess(argv, returncode, "", stderr)

    return run


def test_unsquashfs_failure_names_the_tool_error(tmp_path, monkeypatch):
    monkeypatch.setattr(unpack, "_run_tool", _fail(1, "FATAL ERROR: bad superblock"))
    with pytest.raises(ValueError, match=r"(?s)unsquashfs failed.*bad superblock"):
        unpack._squashfs_tree(b"hsqs", tmp_path)


def test_cramfs_failure_names_the_tool_error(tmp_path, monkeypatch):
    monkeypatch.setattr(unpack, "_run_tool", _fail(8, "fsck.cramfs: bad crc"))
    with pytest.raises(ValueError, match=r"(?s)fsck\.cramfs failed.*bad crc"):
        unpack._cramfs_tree(b"\x45\x3d\xcd\x28", tmp_path)


def test_tool_failure_without_output_still_explains_itself(tmp_path, monkeypatch):
    monkeypatch.setattr(unpack, "_run_tool", _fail(1))
    with pytest.raises(ValueError, match=r"\(no output\)"):
        unpack._cramfs_tree(b"x", tmp_path)


# --- jail / schema -------------------------------------------------------------------


def test_jail_needs_an_absolute_work_dir():
    with pytest.raises(ValueError, match="absolute"):
        jail.command(["debugfs"], Path("relative/work"))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("limits", ["max_expand_mib"], "limits must be a mapping"),
        ("undecoded_ok", {"path": "x"}, "undecoded_ok must be a list"),
    ],
)
def test_schema_rejects_wrong_container_types(field, value, message):
    with pytest.raises(TypeError, match=message):
        {"limits": schema._limits, "undecoded_ok": schema._undecoded_ok}[field](value)


# --- remaining branches --------------------------------------------------------------


def test_blank_password_candidates_are_dropped():
    entry = {"product": "MH200N", "password_scheme": {"candidates": ["", "$PRODUCT"]}}
    assert unpack.passwords(entry) == [b"MH200N"]


def test_encrypted_zip_nobody_can_open_names_the_member(monkeypatch):
    real_infolist = zipfile.ZipFile.infolist

    def infolist(self):
        infos = real_infolist(self)
        for i in infos:
            i.flag_bits |= 0x1
        return infos

    def read(self, info, pwd=None):
        raise RuntimeError("Bad password for file")

    monkeypatch.setattr(zipfile.ZipFile, "infolist", infolist)
    monkeypatch.setattr(zipfile.ZipFile, "read", read)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("secret.bin", b"x")
    with pytest.raises(SystemExit, match=r"no catalog password opened 'secret\.bin'"):
        unpack._unzip(buf.getvalue(), [b"nope"])


def test_tar_directories_and_cpio_directories_are_skipped():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        d = tarfile.TarInfo("etc")
        d.type = tarfile.DIRTYPE
        tf.addfile(d)
        f = tarfile.TarInfo("etc/a")
        f.size = 1
        tf.addfile(f, io.BytesIO(b"1"))
    assert unpack._tar_entries(buf.getvalue()) == [("etc/a", b"1", None)]
    cpio = _newc([("etc", 0o040755, b""), ("etc/a", S_REG, b"1")])
    assert unpack._cpio_entries(cpio) == [("etc/a", b"1", None)]


def test_cpio_without_a_trailer_ends_at_the_data():
    blob = _newc([("a", S_REG, b"1")])
    blob = blob[: blob.index(b"TRAILER") - 110]  # drop the trailer record
    assert unpack._cpio_entries(blob) == [("a", b"1", None)]


def test_cpio_member_cut_inside_its_body_is_rejected():
    blob = _newc([("f", S_REG, b"x" * 64)])
    with pytest.raises(ValueError, match="truncated cpio member"):
        unpack._cpio_entries(blob[:130])


def test_cramfs_tree_returns_what_fsck_extracted(tmp_path, monkeypatch):
    def run(argv, work):
        root = Path(argv[1].removeprefix("--extract="))
        (root / "bin").mkdir(parents=True)
        (root / "bin" / "sh").write_bytes(b"sh")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(unpack, "_run_tool", run)
    assert unpack._cramfs_tree(b"img", tmp_path) == [("bin/sh", b"sh", None)]
