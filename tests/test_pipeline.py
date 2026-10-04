"""Unit tests on tiny *synthetic* fixtures -- no vendor firmware involved.

These run in pr.yml on every PR (forks included), with no secrets and no
image. They prove the layer logic and the guard without touching anything
proprietary.
"""

import gzip
import hashlib
import io
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import fwfetch
import guard
import plan
import schema
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
    assert unpack.cpu_of(elf) == "ELF/ARM/exec/32le"  # too short for e_flags


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
    unpack.walk("top.zip", outer, [], Path(), rows)
    paths = {r["path"] for r in rows}
    assert "top.zip!inner.zip!prog" in paths
    assert any(p.endswith("~gunzip") for p in paths)


def test_password_scheme_substitutes_product():
    entry = {
        "product": "MH200N",
        "password_scheme": {"candidates": ["SCHEDULER", "$PRODUCT"]},
    }
    assert unpack.passwords(entry) == [b"SCHEDULER", b"MH200N"]


def test_guard_flags_binary(tmp_path, monkeypatch):
    bad = tmp_path / "results" / "leak.bin"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"\x7fELFsomething")
    assert guard.is_binary(bad) is not None


def test_guard_flags_a_blob_renamed_to_tsv(tmp_path):
    bad = tmp_path / "rows.tsv"
    bad.write_bytes(b"path\ttype\n" + b"\x00\x01\x02 opaque")
    assert guard.is_binary(bad) == "NUL byte (binary content)"


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


def test_cpu_of_handles_empty_and_tiny_files():
    # Real rootfs trees contain empty and very short files; none may crash.
    assert unpack.cpu_of(b"") == "empty"
    assert unpack.cpu_of(b"x") == "data"
    assert unpack.cpu_of(b"\x27\x05") == "data"
    assert unpack.cpu_of(b"\x7fELF\x01") == "ELF/truncated"


def test_walk_records_fake_containers_without_crashing():
    rows: list[dict] = []
    unpack.walk("notzip", b"PK but not a zip archive", [], Path(), rows)
    unpack.walk("notgz", b"\x1f\x8b garbage", [], Path(), rows)
    assert [r["type"] for r in rows] == ["zip/unreadable", "gzip/unreadable"]


def test_sibling_ext_layers_get_separate_work_dirs(tmp_path, monkeypatch):
    # Two filesystems at the same depth must not share an rdump target.
    seen: list[Path] = []
    monkeypatch.setattr(unpack, "_ext_tree", lambda img, sub: seen.append(sub) or [])
    fs = b"\x00" * 0x438 + struct.pack("<H", unpack.EXT_MAGIC) + b"\x00" * 16
    outer = _zip({"rootfs.img": fs, "recovery.img": fs + b"\x01"})
    unpack.walk("fw.zip", outer, [], tmp_path, [])
    assert len(seen) == 2
    assert seen[0] != seen[1]


def test_unzip_moves_on_when_wrong_password_passes_check_byte(monkeypatch):
    # A wrong ZipCrypto password can pass the 1-byte check and then fail in
    # inflate; _unzip must treat that as "wrong password" and try the next.
    real_infolist = zipfile.ZipFile.infolist

    def infolist(self):
        infos = real_infolist(self)
        for i in infos:
            i.flag_bits |= 0x1
        return infos

    def read(self, info, pwd=None):
        if self.pwd != b"right":
            raise zlib.error("Error -3 while decompressing data")
        return b"payload"

    monkeypatch.setattr(zipfile.ZipFile, "infolist", infolist)
    monkeypatch.setattr(zipfile.ZipFile, "read", read)
    raw = _zip({"member": b"payload"})
    assert unpack._unzip(raw, [b"wrong", b"right"]) == {"member": b"payload"}


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


SHA_A = "a" * 64
SHA_B = "b" * 64


def _catalog(tmp_path, image_sha=SHA_A, wrapper=None, sources=""):
    """Write a minimal valid catalog entry; `sources` is spliced under wrapper."""
    wrapper = wrapper or {"filename": "FW.zip", "size": 1, "sha256": SHA_B}
    cat = tmp_path / "catalog" / "MH200N" / "010108.yaml"
    cat.parent.mkdir(parents=True, exist_ok=True)
    cat.write_text(
        "product: MH200N\nversion: '010108'\n"
        f"wrapper:\n  filename: {wrapper['filename']}\n"
        f"  size: {wrapper['size']}\n  sha256: '{wrapper['sha256']}'\n"
        + sources
        + f"image:\n  filename: fw.fwz\n  size: 1\n  sha256: '{image_sha}'\n"
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
    man.write_text(f"# image_sha256={SHA_A}\n# tool_version=0\npath\n")
    assert plan.is_stale(cat, results) is True

    # both keys current -> fresh
    man.write_text(
        f"# image_sha256={SHA_A}\n# tool_version={unpack.TOOL_VERSION}\npath\n"
    )
    assert plan.is_stale(cat, results) is False


# --- catalog validation -------------------------------------------------------


def test_catalog_accepts_the_real_mh200n_entry():
    entry = schema.load(ROOT / "catalog" / "MH200N" / "010108.yaml")
    assert entry["wrapper"]["filename"] == "FW_MH200N_vers_010108.zip"


@pytest.mark.parametrize(
    "source",
    [
        "vendor: 'http://www.bticino.be/fw.zip'",
        "vendor: 'https://evil.example/fw.zip'",
        "vendor: 'file:///etc/passwd'",
        "r2: 'firmware/../../other'",
        "ftp: 'x'",
    ],
)
def test_catalog_rejects_bad_sources(tmp_path, source):
    cat = _catalog(tmp_path, sources=f"  sources:\n    - {source}\n")
    with pytest.raises(SystemExit):
        schema.load(cat)


@pytest.mark.parametrize(
    "filename",
    [
        '"x.zip\\nBASH_ENV=/tmp/x"',  # newline -> $GITHUB_ENV injection
        "'$(id).zip'",
        "../x.zip",
    ],
)
def test_catalog_rejects_unsafe_filenames(tmp_path, filename):
    cat = _catalog(tmp_path, wrapper={"filename": filename, "size": 1, "sha256": SHA_B})
    with pytest.raises(SystemExit):
        schema.load(cat)


def test_catalog_rejects_mismatched_path_and_bad_hash(tmp_path):
    cat = _catalog(tmp_path)
    schema.load(cat)  # the fixture itself is valid
    moved = tmp_path / "catalog" / "OTHER" / "010108.yaml"
    moved.parent.mkdir()
    moved.write_text(cat.read_text())
    with pytest.raises(SystemExit):
        schema.load(moved)
    with pytest.raises(SystemExit):
        schema.load(_catalog(tmp_path / "x", image_sha="abc123"))


def test_fwfetch_refuses_redirect_off_https_or_allowlist():
    handler = fwfetch._VendorRedirects()
    for url in ("http://www.bticino.be/fw.zip", "https://evil.example/fw.zip"):
        with pytest.raises(ValueError, match=r"https|allow-listed"):
            handler.redirect_request(None, None, 302, "", {}, url)


# --- unpack: the image must be the catalog image -----------------------------


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def test_unpack_refuses_an_image_that_is_not_the_catalog_wrapper():
    good = _zip({"fw.fwz": b"inner"})
    entry = {"wrapper": {"filename": "FW.zip", "size": len(good), "sha256": _sha(good)}}
    unpack.verify_wrapper(entry, good)
    with pytest.raises(SystemExit):
        unpack.verify_wrapper(entry, good + b"x")


def test_unpack_requires_the_catalog_image_inside():
    entry = {"image": {"filename": "fw.fwz", "sha256": SHA_A}}
    unpack.require_image(entry, [{"sha256": SHA_A}])
    with pytest.raises(SystemExit):
        unpack.require_image(entry, [{"sha256": SHA_B}])


def test_unpack_roots_paths_at_wrapper_filename(tmp_path, monkeypatch):
    inner = b"not a container"
    wrapper = _zip({"fw.fwz": inner})
    cat = _catalog(
        tmp_path,
        image_sha=_sha(inner),
        wrapper={"filename": "FW.zip", "size": len(wrapper), "sha256": _sha(wrapper)},
    )
    # The fwfetch cache names files <sha256>.zip; the manifest must not care.
    on_disk = tmp_path / f"{_sha(wrapper)}.zip"
    on_disk.write_bytes(wrapper)
    out = tmp_path / "manifest.tsv"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "unpack",
            str(cat),
            str(on_disk),
            "-o",
            str(out),
            "--work",
            str(tmp_path / "w"),
        ],
    )
    unpack.main()
    rows = [ln.split("\t")[0] for ln in out.read_text().splitlines()[4:]]
    assert rows == ["FW.zip", "FW.zip!fw.fwz"]


def test_unpack_accepts_a_download_that_is_the_image_itself(tmp_path, monkeypatch):
    # MyHOMEServer1: the vendor serves SMARTGW_<ver>.fwz directly, with no
    # outer zip. wrapper == image, and the root row carries the image hash.
    fwz = _zip({"fwz.xml": b"<fwz/>"})
    cat = _catalog(
        tmp_path,
        image_sha=_sha(fwz),
        wrapper={"filename": "SMARTGW.fwz", "size": len(fwz), "sha256": _sha(fwz)},
    )
    img = tmp_path / "SMARTGW.fwz"
    img.write_bytes(fwz)
    out = tmp_path / "manifest.tsv"
    monkeypatch.setattr(sys, "argv", ["unpack", str(cat), str(img), "-o", str(out)])
    unpack.main()
    lines = out.read_text().splitlines()
    assert f"# image_sha256={_sha(fwz)}" in lines
    assert [ln.split("\t")[0] for ln in lines[4:]] == [
        "SMARTGW.fwz",
        "SMARTGW.fwz!fwz.xml",
    ]


def test_unpack_deletes_its_temp_work_dir(tmp_path, monkeypatch):
    # An ext layer makes walk() create work dirs; without --work they must go.
    fs = b"\x00" * 0x438 + struct.pack("<H", unpack.EXT_MAGIC) + b"\x00" * 16
    wrapper = _zip({"rootfs.img": fs})
    cat = _catalog(
        tmp_path,
        image_sha=_sha(fs),
        wrapper={"filename": "FW.zip", "size": len(wrapper), "sha256": _sha(wrapper)},
    )
    img = tmp_path / "FW.zip"
    img.write_bytes(wrapper)
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    monkeypatch.setattr(unpack.tempfile, "tempdir", str(temp_root))
    seen: list[Path] = []
    monkeypatch.setattr(unpack, "_ext_tree", lambda img, sub: seen.append(sub) or [])
    monkeypatch.setattr(
        sys, "argv", ["unpack", str(cat), str(img), "-o", str(tmp_path / "m.tsv")]
    )
    unpack.main()
    assert seen
    assert seen[0].is_relative_to(temp_root)
    assert list(temp_root.iterdir()) == []


# --- unpack: bounded, injection-free, one row per line -----------------------


def test_unzip_refuses_archives_declaring_more_than_the_cap():
    raw = _zip({"a": b"x" * 600, "b": b"y" * 600})
    with pytest.raises(SystemExit):
        unpack._unzip(raw, [], limit=1000)
    assert unpack._unzip(raw, [], limit=1200) == {"a": b"x" * 600, "b": b"y" * 600}


@pytest.mark.parametrize("work", ["/tmp/with space", "/tmp/line\nbreak", '/tmp/"q"'])
def test_ext_tree_refuses_work_dirs_that_are_not_plain_paths(work):
    with pytest.raises(SystemExit):
        unpack._ext_tree(b"", Path(work))


def test_tsv_field_escapes_tabs_newlines_and_backslashes():
    assert unpack.tsv_field("bin/ls") == "bin/ls"
    assert unpack.tsv_field("a\tb\nc\\d\x7f") == "a\\x09b\\x0ac\\x5cd\\x7f"


def test_manifest_rows_stay_one_line_for_hostile_member_names(tmp_path, monkeypatch):
    inner = b"payload"
    wrapper = _zip({"evil\tname\nFAKE\tROW": inner})
    cat = _catalog(
        tmp_path,
        image_sha=_sha(inner),
        wrapper={"filename": "FW.zip", "size": len(wrapper), "sha256": _sha(wrapper)},
    )
    img = tmp_path / "FW.zip"
    img.write_bytes(wrapper)
    out = tmp_path / "m.tsv"
    monkeypatch.setattr(sys, "argv", ["unpack", str(cat), str(img), "-o", str(out)])
    unpack.main()
    body = out.read_text().splitlines()[4:]
    assert len(body) == 2
    assert all(len(ln.split("\t")) == 4 for ln in body)


# --- unpack: symlinks are recorded, never followed ---------------------------


def test_tree_entries_records_symlinks_without_following(tmp_path):
    host = tmp_path / "host-secret"
    host.write_bytes(b"runner file")
    (tmp_path / "host-dir").mkdir()
    (tmp_path / "host-dir" / "f").write_bytes(b"x")

    root = tmp_path / "tree"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "busybox").write_bytes(b"\x7fELF")
    (root / "bin" / "ls").symlink_to("busybox")  # relative, in-tree
    (root / "bin" / "abs").symlink_to(host)  # absolute -> host file
    (root / "d").symlink_to(tmp_path / "host-dir")  # dir link -> host dir
    (root / "dangling").symlink_to("/nonexistent")

    entries = {p: (blob, tgt) for p, blob, tgt in unpack._tree_entries(root)}
    assert entries["bin/busybox"] == (b"\x7fELF", None)
    assert entries["bin/ls"] == (None, "busybox")
    assert entries["bin/abs"] == (None, str(host))
    assert entries["d"] == (None, str(tmp_path / "host-dir"))
    assert entries["dangling"] == (None, "/nonexistent")
    assert "d/f" not in entries  # never descended into the linked directory


def test_walk_writes_symlink_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(
        unpack, "_ext_tree", lambda img, sub: [("bin/ls", None, "busybox")]
    )
    fs = b"\x00" * 0x438 + struct.pack("<H", unpack.EXT_MAGIC) + b"\x00" * 16
    rows: list[dict] = []
    unpack.walk("rootfs", fs, [], tmp_path, rows)
    assert rows[-1] == {
        "path": "rootfs:/bin/ls",
        "type": "symlink",
        "size": 7,
        "sha256": _sha(b"busybox"),
    }


# --- guard: untracked files count too ----------------------------------------


def test_guard_sees_untracked_but_not_ignored_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    Path(".gitignore").write_text("*.zip\n")
    Path("results").mkdir()
    Path("results/manifest.tsv").write_text("path\n")
    Path("results/bt_luci").write_bytes(b"\x7fELF")  # extensionless ELF
    Path("fw.zip").write_bytes(b"PK\x03\x04")  # ignored: never committed
    files = {p.as_posix() for p in guard.repo_files()}
    assert {"results/manifest.tsv", "results/bt_luci"} <= files
    assert "fw.zip" not in files
    assert guard.main() == 1


def test_guard_flags_short_uimage(tmp_path):
    short = tmp_path / "kernel.txt"
    short.write_bytes(struct.pack(">I", unpack.UIMAGE_MAGIC) + b"\x00" * 60)
    assert guard.is_binary(short) == "uImage header"
