"""Tool versions 3 and 4: more layer formats, precise ELF tags, the coverage gate,
per-image limits and the extraction sandbox. Synthetic fixtures only."""

import bz2
import io
import lzma
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import guard
import jail
import schema
import unpack


def _elf(
    machine: int, *, bits: int = 32, order: str = "<", etype: int = 2, flags: int = 0
) -> bytes:
    ident = b"\x7fELF" + bytes([1 if bits == 32 else 2, 1 if order == "<" else 2, 1])
    ident += b"\x00" * 9
    head = ident + struct.pack(order + "HHI", etype, machine, 1)
    if bits == 32:  # e_entry, e_phoff, e_shoff, then e_flags at 36
        head += struct.pack(order + "IIII", 0, 0, 0, flags)
    else:  # 8-byte e_entry, e_phoff, e_shoff, then e_flags at 48
        head += struct.pack(order + "QQQI", 0, 0, 0, flags)
    return head + b"\x00" * 16


# --- ELF tags ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("blob", "tag"),
    [
        (_elf(0x28), "ELF/ARM/exec/32le/oabi"),
        (_elf(0x28, flags=0x05000000), "ELF/ARM/exec/32le/eabi5"),
        (_elf(0x28, flags=0x05000400, etype=3), "ELF/ARM/dyn/32le/eabi5-hf"),
        (_elf(0x28, order=">", flags=0x04000000), "ELF/ARM/exec/32be/eabi4"),
        (_elf(0x08, order=">"), "ELF/MIPS/exec/32be"),
        (_elf(0x14, order=">"), "ELF/PowerPC/exec/32be"),
        (_elf(0xB7, bits=64), "ELF/AArch64/exec/64le"),
        (b"\x7fELF\x03\x01" + b"\x00" * 30, "ELF/bad-ident"),
    ],
)
def test_elf_tags_carry_word_size_byte_order_and_arm_abi(blob, tag):
    assert unpack.cpu_of(blob) == tag


def test_big_endian_machine_is_not_misread():
    # Read little-endian, PowerPC (0x0014) would come out as 0x1400.
    assert "0x1400" not in unpack.cpu_of(_elf(0x14, order=">"))


# --- detection: new magics, and look-alikes that must stay data ---------------


def _tar(members: dict[str, bytes], links: dict[str, str] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            tf.addfile(info)
    return buf.getvalue()


def _newc(entries: list[tuple[str, int, bytes]]) -> bytes:
    out = b""
    for name, mode, body in [*entries, ("TRAILER!!!", 0, b"")]:
        nb = name.encode() + b"\0"
        fields = [0, mode, 0, 0, 1, 0, len(body), 0, 0, 0, 0, len(nb), 0]
        hdr = b"070701" + b"".join(b"%08x" % f for f in fields) + nb
        hdr += b"\0" * (-len(hdr) % 4)
        out += hdr + body + b"\0" * (-len(body) % 4)
    return out


def test_detects_new_container_magics():
    assert unpack.cpu_of(bz2.compress(b"x")) == "bzip2"
    assert unpack.cpu_of(lzma.compress(b"x")) == "xz"
    assert unpack.cpu_of(lzma.compress(b"x", format=lzma.FORMAT_ALONE)) == "lzma"
    assert unpack.cpu_of(b"hsqs" + b"\x00" * 60) == "squashfs"
    assert (
        unpack.cpu_of(struct.pack("<I", unpack.CRAMFS_MAGIC) + b"\x00" * 60) == "cramfs"
    )
    assert unpack.cpu_of(b"UBI#" + b"\x00" * 60) == "ubi"
    assert (
        unpack.cpu_of(struct.pack("<HHI", 0x1985, 0xE001, 0) + b"\x00" * 8) == "jffs2"
    )
    assert unpack.cpu_of(_tar({"a": b"1"})) == "tar"
    assert unpack.cpu_of(_newc([("a", 0o100644, b"1")])) == "cpio"
    zimage = b"\x00" * 0x24 + struct.pack("<I", unpack.ZIMAGE_MAGIC) + b"\x00" * 8
    assert unpack.cpu_of(zimage) == "zImage/ARM"
    dtb = b"\xd0\x0d\xfe\xed" + b"\x00" * 100
    assert unpack.cpu_of(dtb) == "dtb"
    assert unpack.cpu_of(dtb + b"\x00" * unpack.LARGE_DATA) == "fit"


@pytest.mark.parametrize(
    "blob",
    [
        b"\x85\x19" + b"\x00" * 30,  # jffs2 magic, but no real node type
        b"070701 is how this text starts" + b" " * 100,
        b"]" + b"\x00" * 40,  # 0x5d but not an lzma header
    ],
)
def test_look_alikes_stay_data(blob):
    assert unpack.cpu_of(blob) == "data"


# --- walking the new layers ---------------------------------------------------


def _walk(name: str, blob: bytes, tmp_path: Path, **kw) -> list[dict]:
    rows: list[dict] = []
    unpack.walk(name, blob, [], tmp_path, rows, **kw)
    return rows


def test_walk_recurses_compression_and_archives(tmp_path):
    tar = _tar({"./bin/prog": _elf(0x28), "etc/conf": b"k=v"}, {"bin/sh": "busybox"})
    cpio = _newc([("init", 0o100755, b"#!/bin/sh"), ("lib", 0o120777, b"/usr/lib")])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("root.tar.xz", lzma.compress(tar))
        zf.writestr("initrd.cpio.bz2", bz2.compress(cpio))
    rows = {r["path"]: r for r in _walk("fw.zip", buf.getvalue(), tmp_path)}
    assert rows["fw.zip!root.tar.xz~unxz!bin/prog"]["type"] == "ELF/ARM/exec/32le/oabi"
    assert rows["fw.zip!root.tar.xz~unxz!etc/conf"]["type"] == "data"
    assert rows["fw.zip!root.tar.xz~unxz!bin/sh"]["type"] == "symlink"
    assert rows["fw.zip!initrd.cpio.bz2~bunzip2!init"]["size"] == 9
    assert rows["fw.zip!initrd.cpio.bz2~bunzip2!lib"]["type"] == "symlink"


def test_truncated_compression_is_unreadable_not_a_partial_layer(tmp_path):
    rows = _walk("x.xz", lzma.compress(b"a" * 5000)[:-20], tmp_path)
    assert rows[-1]["type"] == "xz/unreadable"
    rows = _walk("x.bz2", bz2.compress(b"a" * 5000)[:-10], tmp_path)
    assert rows[-1]["type"] == "bzip2/unreadable"


def test_decompression_respects_the_limit(tmp_path):
    with pytest.raises(SystemExit):
        _walk("big.xz", lzma.compress(b"\x00" * 10_000), tmp_path, limit=1000)
    with pytest.raises(SystemExit):
        _walk("big.tar", _tar({"a": b"x" * 4000}), tmp_path, limit=1000)


def test_filesystem_readers_failures_become_unreadable(tmp_path, monkeypatch):
    def broken(img, work):
        raise ValueError("unsquashfs failed")

    monkeypatch.setattr(unpack, "_squashfs_tree", broken)
    rows = _walk("rootfs.sqsh", b"hsqs" + b"\x00" * 60, tmp_path)
    assert rows[-1]["type"] == "squashfs/unreadable"


def test_ext_failure_becomes_unreadable_not_an_aborted_run(tmp_path, monkeypatch):
    def broken(img, work):
        raise ValueError("debugfs rdump failed")
    monkeypatch.setattr(unpack, "_ext_tree", broken)
    fs = b"\x00" * 0x438 + struct.pack("<H", unpack.EXT_MAGIC) + b"\x00" * 16
    rows = _walk("rootfs.img", fs, tmp_path)
    assert rows[-1]["type"] == "ext-fs/unreadable"
    assert unpack.undecoded(rows) == rows[-1:]


def test_empty_filesystem_is_valid_not_unreadable(tmp_path, monkeypatch):
    monkeypatch.setattr(unpack, "_ext_tree", lambda img, work: [])
    fs = b"\x00" * 0x438 + struct.pack("<H", unpack.EXT_MAGIC) + b"\x00" * 16
    assert [r["type"] for r in _walk("rootfs.img", fs, tmp_path)] == ["ext-fs"]


def test_depth_cutoff_marks_unopened_containers_unreadable(tmp_path):
    blob = _tar({"leaf": b"x" * 10})
    for _ in range(unpack.MAX_DEPTH + 1):
        blob = _tar({"inner.tar": blob})
    rows = _walk("outer.tar", blob, tmp_path)
    cut = [r for r in rows if r["type"] == "tar/unreadable"]
    assert len(cut) == 1
    assert cut[0]["path"].count("!") == unpack.MAX_DEPTH + 1
    assert unpack.undecoded(rows) == cut


def test_depth_cutoff_leaves_leaves_alone(tmp_path):
    blob = b"plain data"
    for _ in range(unpack.MAX_DEPTH):
        blob = _tar({"inner.tar": blob})
    rows = _walk("outer.tar", blob, tmp_path)
    assert unpack.undecoded(rows) == []


def test_filesystem_trees_are_recorded_under_colon_slash(tmp_path, monkeypatch):
    monkeypatch.setattr(
        unpack,
        "_cramfs_tree",
        lambda img, work: [
            ("bin/prog", _elf(0x28, flags=0x05000000), None),
            ("bin/ls", None, "prog"),
        ],
    )
    rows = {
        r["path"]: r["type"]
        for r in _walk(
            "fs.cram", struct.pack("<I", unpack.CRAMFS_MAGIC) + b"\x00" * 60, tmp_path
        )
    }
    assert rows["fs.cram:/bin/prog"] == "ELF/ARM/exec/32le/eabi5"
    assert rows["fs.cram:/bin/ls"] == "symlink"


@pytest.mark.skipif(
    shutil.which("mksquashfs") is None, reason="squashfs-tools not installed"
)
def test_squashfs_tree_reads_a_real_image_without_sandbox(tmp_path, monkeypatch):
    src = tmp_path / "src"
    (src / "bin").mkdir(parents=True)
    (src / "bin" / "a").write_bytes(b"hello")
    (src / "bin" / "l").symlink_to("/bin/a")
    img = tmp_path / "fs.sqsh"
    subprocess.run(
        [
            "mksquashfs",
            str(src),
            str(img),
            "-quiet",
            "-noappend",
            "-p",
            "dev/null c 666 0 0 1 3",
        ],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(unpack, "SANDBOX", False)
    work = tmp_path / "work"
    work.mkdir()
    entries = {p: (b, t) for p, b, t in unpack._squashfs_tree(img.read_bytes(), work)}
    # the device node it cannot create without root is noise, not a failure
    assert entries == {"bin/a": (b"hello", None), "bin/l": (None, "/bin/a")}


# --- coverage gate ------------------------------------------------------------


def _rows(*rows):
    return [{"path": p, "type": t, "size": s, "sha256": "0" * 64} for p, t, s in rows]


def test_gate_flags_unsupported_unreadable_and_big_opaque_blobs():
    rows = _rows(
        ("fw!rootfs.ubi", "ubi", 10),
        ("fw!x.gz", "gzip/unreadable", 10),
        ("fw!blob.bin", "data", unpack.LARGE_DATA),
        ("fw!small.bin", "data", unpack.LARGE_DATA - 1),
        ("fw!fs:/usr/share/big.db", "data", 5 * unpack.LARGE_DATA),  # leaf in a fs
    )
    assert [r["path"] for r in unpack.undecoded(rows)] == [
        "fw!rootfs.ubi",
        "fw!x.gz",
        "fw!blob.bin",
    ]
    with pytest.raises(SystemExit, match=r"fw!rootfs\.ubi"):
        unpack.coverage_gate({}, rows)


def test_gate_accepts_acknowledged_rows_and_rejects_stale_acks():
    rows = _rows(("fw!blob.bin", "data", unpack.LARGE_DATA))
    ack = {"undecoded_ok": [{"path": "fw!blob.bin", "reason": "MCU firmware blob"}]}
    unpack.coverage_gate(ack, rows)
    stale = {"undecoded_ok": [*ack["undecoded_ok"], {"path": "fw!gone", "reason": "x"}]}
    with pytest.raises(SystemExit, match="stale"):
        unpack.coverage_gate(stale, rows)


def test_mh200n_manifest_would_pass_the_gate():
    # The v2 manifest's types are a lower bound for v3 (v3 only adds detail),
    # so nothing in it may trip the gate without an acknowledgement.
    manifest = ROOT / "results" / "MH200N" / "010108" / "manifest.tsv"
    rows = []
    for line in manifest.read_text().splitlines():
        cols = line.split("\t")
        if line.startswith("#") or cols[0] == "path":
            continue
        rows.append({"path": cols[0], "type": cols[1], "size": int(cols[2])})
    assert unpack.undecoded(rows) == []


# --- catalog: limits and acknowledgements -------------------------------------


def _entry(**extra):
    base = {
        "product": "P",
        "version": "1",
        "wrapper": {"filename": "w.zip", "size": 1, "sha256": "a" * 64},
        "image": {"filename": "i.fwz", "size": 1, "sha256": "b" * 64},
    }
    return {**base, **extra}


CAT = Path("catalog/P/1.yaml")


def test_schema_accepts_limits_and_acks():
    e = _entry(
        limits={"max_expand_mib": 1024},
        undecoded_ok=[{"path": "w.zip!x", "reason": "bootloader"}],
    )
    assert schema.validate(e, CAT) is e
    assert unpack.expand_limit(e) == 1024 * 1024 * 1024
    assert unpack.expand_limit(_entry()) == unpack.MAX_DECOMPRESS


@pytest.mark.parametrize(
    "extra",
    [
        {"limits": {"max_expand_mib": 0}},
        {"limits": {"max_expand_mib": 10**6}},
        {"limits": {"max_expand_mib": True}},
        {"limits": {"off": 1}},
        {"undecoded_ok": [{"path": "w.zip!x"}]},  # no reason
        {"undecoded_ok": [{"path": "w.zip!x", "reason": " "}]},
        {"undecoded_ok": [{"path": "a\nb", "reason": "r"}]},
        {"undecoded_ok": [{"path": "p", "reason": "r"}, {"path": "p", "reason": "r"}]},
    ],
)
def test_schema_rejects_bad_limits_and_acks(extra):
    with pytest.raises((ValueError, TypeError)):
        schema.validate(_entry(**extra), CAT)


# --- sandbox -------------------------------------------------------------------


def test_jail_binds_only_the_work_dir_writable(tmp_path, monkeypatch):
    monkeypatch.setattr(jail.shutil, "which", lambda name: "/usr/bin/bwrap")
    argv = jail.command(["debugfs", "-R", "rdump / x", "img"], tmp_path.resolve())
    assert argv[0] == "bwrap"
    assert "--unshare-all" in argv
    assert "--clearenv" in argv
    binds = [argv[i + 1] for i, a in enumerate(argv) if a == "--bind"]
    assert binds == [str(tmp_path.resolve())]
    assert argv[argv.index("--") + 1 :] == ["debugfs", "-R", "rdump / x", "img"]


def test_jail_fails_closed_without_bwrap(tmp_path, monkeypatch):
    monkeypatch.setattr(jail.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="--no-sandbox"):
        jail.command(["debugfs"], tmp_path.resolve())


def test_run_tool_goes_through_the_jail_unless_disabled(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        unpack.subprocess,
        "run",
        lambda argv, **kw: (
            calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", "")
        ),
    )
    monkeypatch.setattr(jail.shutil, "which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(unpack, "SANDBOX", True)
    unpack._run_tool(["unsquashfs", "x"], tmp_path)
    monkeypatch.setattr(unpack, "SANDBOX", False)
    unpack._run_tool(["unsquashfs", "x"], tmp_path)
    assert calls[0][0] == "bwrap"
    assert calls[1] == ["unsquashfs", "x"]


def test_debugfs_chown_noise_inside_the_sandbox_is_not_an_error():
    # In bwrap's user namespace the image's uids are unmapped, so chown fails
    # with EINVAL instead of EPERM; observed on the first sandboxed MH200N run.
    stderr = (
        "debugfs 1.47.0 (5-Feb-2023)\n"
        "dump_file: Invalid argument while changing ownership of /w/tree//bin/ls\n"
        "rdump: Invalid argument while changing ownership of /w/tree/\n"
    )
    assert unpack._debugfs_real_errors(stderr) == []
    assert unpack._debugfs_real_errors(
        "rdump: Invalid argument while reading block 7\n"
    )


# --- guard ---------------------------------------------------------------------


def test_guard_allows_big_manifests_but_nothing_else(tmp_path):
    assert (
        guard.max_bytes(Path("results/F460/020012/manifest.tsv"))
        == guard.MANIFEST_MAX_BYTES
    )
    assert guard.max_bytes(Path("results/F460/020012/notes.tsv")) == guard.MAX_BYTES
    assert guard.max_bytes(Path("findings/manifest.tsv")) == guard.MAX_BYTES


def test_guard_knows_the_new_container_magics(tmp_path):
    for i, head in enumerate(
        [b"\xfd7zXZ\x00", b"UBI#", struct.pack("<I", unpack.CRAMFS_MAGIC)]
    ):
        p = tmp_path / f"f{i}.txt"
        p.write_bytes(head + b"\x00" * 16)
        assert guard.is_binary(p) is not None


# --- tool version 4: zip symlinks, and the sink oracle/stage.py uses -----------


def _zip_with_link() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("lib/libx.so.0.0", _elf(0x28, etype=3))
        link = zipfile.ZipInfo("lib/libx.so")
        link.create_system = unpack.ZIP_UNIX
        link.external_attr = (unpack.S_IFLNK | 0o777) << 16
        zf.writestr(link, "libx.so.0.0")
        # a Windows-made member whose attribute bits happen to look like a link
        odd = zipfile.ZipInfo("notes.txt")
        odd.create_system = 0
        odd.external_attr = (unpack.S_IFLNK | 0o777) << 16
        zf.writestr(odd, "plain text")
    return buf.getvalue()


def test_zip_symlink_members_are_symlink_rows(tmp_path):
    rows = {r["path"]: r for r in _walk("app.zip", _zip_with_link(), tmp_path)}
    link = rows["app.zip!lib/libx.so"]
    assert link["type"] == "symlink"
    # same size and hash as the old `data` row: only the type changed
    assert link["size"] == len(b"libx.so.0.0")
    assert link["sha256"] == unpack.sha256(b"libx.so.0.0")
    assert rows["app.zip!notes.txt"]["type"] == "data"  # only Unix modes count
    assert rows["app.zip!lib/libx.so.0.0"]["type"].startswith("ELF/ARM/dyn")


def test_sink_sees_every_member_with_its_bytes(tmp_path):
    seen: dict[str, tuple[bytes | None, str | None]] = {}

    def sink(container, sep, member, blob, target):
        seen[f"{container}{sep}{member}"] = (blob, target)

    rows = _walk("app.zip", _zip_with_link(), tmp_path, sink=sink)
    members = [r for r in rows if r["path"] != "app.zip"]
    assert sorted(seen) == sorted(r["path"] for r in members)
    assert seen["app.zip!lib/libx.so"] == (None, "libx.so.0.0")
    assert seen["app.zip!notes.txt"] == (b"plain text", None)
    for r in members:
        blob, target = seen[r["path"]]
        data = blob if blob is not None else (target or "").encode()
        assert unpack.sha256(data) == r["sha256"]


def test_empty_directories_get_dir_rows_and_other_node_types_are_skipped(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for name, kind in (
            ("var/run", tarfile.DIRTYPE),
            ("dev/fifo", tarfile.FIFOTYPE),
        ):
            info = tarfile.TarInfo(name)
            info.type = kind
            tf.addfile(info)
    assert unpack._tar_entries(buf.getvalue()) == [("var/run", None, None)]
    cpio = _newc([("var/tmp", 0o040755, b""), ("dev/null", 0o020666, b"")])
    assert unpack._cpio_entries(cpio) == [("var/tmp", None, None)]
    tree = tmp_path / "tree"
    (tree / "var" / "tmp").mkdir(parents=True)
    (tree / "etc").mkdir()
    (tree / "etc" / "conf").write_bytes(b"x")
    os.mkfifo(tree / "etc" / "pipe")
    assert unpack._tree_entries(tree) == [
        ("etc/conf", b"x", None),
        ("var/tmp", None, None),  # var is implied by var/tmp
    ]


def test_dir_rows_reach_the_manifest_and_the_sink(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("etc/applications/", b"")
        zf.writestr("etc/conf", b"k=v")
    seen = []
    rows = _walk("a.zip", buf.getvalue(), tmp_path, sink=lambda *a: seen.append(a))
    row = next(r for r in rows if r["path"] == "a.zip!etc/applications")
    assert row == {
        "path": "a.zip!etc/applications",
        "type": unpack.DIR_TYPE,
        "size": 0,
        "sha256": unpack.sha256(b""),
    }
    assert ("a.zip", "!", "etc/applications", None, None) in seen
    assert not unpack.undecoded(rows)  # a dir row never trips the coverage gate
