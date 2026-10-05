"""Phase 2a: target runtime, sandbox command lines, trace reduction, staging
and the discovery command line.

No firmware, no qemu, no bwrap. Where a process is needed, the jail is swapped
for a small Python stand-in that writes to the device pty like a program would.
"""

import dataclasses
import io
import os
import stat
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import unpack
from oracle import bus, cases, discover, driver, record, run, sandbox, stage, target
from test_oracle import _write_target

OK = f'  a: {{path: bin/a, sha256: "{"b" * 64}"}}'


# --- target: runtime, programs, layout ----------------------------------------


def _load(tmp_path, programs=OK, boundary="status: pending", extra=""):
    spec, results = _write_target(tmp_path, programs, boundary, extra)
    return target.load(spec, results)


def test_target_reads_runtime_arch_and_image(tmp_path):
    extra = textwrap.dedent("""\
        runtime:
          cwd: /home/x
          env: {LD_LIBRARY_PATH: /home/x/lib}
          tmpfs: [/tmp]
          dirs: [/var/run]
          devices: {/dev/ttyPIC: pty}
        """)
    programs = OK + f'\n  f: {{path: bin/f, sha256: "{"f" * 64}", args: ["-d", "1"]}}'
    spec = _load(tmp_path, programs, extra=extra)
    assert spec.arch == "arm"
    assert spec.image_sha256 == "e" * 64
    assert spec.programs["f"].layer == "w.zip!fs:"  # fs layers root at '/'
    assert spec.programs["f"].args == ("-d", "1")
    assert spec.runtime == target.Runtime(
        cwd="/home/x",
        env={"LD_LIBRARY_PATH": "/home/x/lib"},
        tmpfs=("/tmp",),
        dirs=("/var/run",),
        devices={"/dev/ttyPIC": "pty"},
    )
    assert _load(tmp_path / "d").runtime == target.Runtime()  # all defaults


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ("runtime: [cwd]\n", "runtime must be a mapping"),
        ("runtime: {shell: sh}\n", "unknown keys"),
        ("runtime: {env: [A]}\n", "must be mappings"),
        ("runtime: {devices: [x]}\n", "must be mappings"),
        ("runtime: {cwd: home}\n", "not a plain absolute guest path"),
        ("runtime: {tmpfs: /tmp}\n", "list of strings"),
        ("runtime: {dirs: [/a/../b]}\n", "not a plain absolute guest path"),
        ("runtime: {devices: {/dev/x: file}}\n", "kind 'file'"),
        ("runtime: {devices: {dev/x: pty}}\n", "not a plain absolute guest path"),
        ("runtime: {env: {lower: x}}\n", "bad entry"),
        ("runtime: {env: {A: 1}}\n", "bad entry"),
        ("runtime: {env: {QEMU_STRACE: '1'}}\n", "emulator's"),
    ],
)
def test_target_rejects_bad_runtime(tmp_path, extra, message):
    with pytest.raises(target.TargetError, match=message):
        _load(tmp_path, extra=extra)


@pytest.mark.parametrize(
    ("programs", "message"),
    [
        (OK + f'\n  m: {{path: bin/m, sha256: "{"c" * 64}"}}', "different emulators"),
        (f'  z: {{path: bin/z, sha256: "{"d" * 64}"}}', "no emulator"),
        (f'  a: {{path: bin/a, sha256: "{"b" * 64}", args: [""]}}', "args"),
        (f'  a: {{path: bin/a, sha256: "{"b" * 64}", args: "-x"}}', "args"),
        ("  bad name!: {path: bin/a}", "bad program entry"),
        ("  a: bin/a", "bad program entry"),
        ("  {}", "programs must be a non-empty mapping"),
    ],
)
def test_target_rejects_bad_programs(tmp_path, programs, message):
    with pytest.raises(target.TargetError, match=message):
        _load(tmp_path, programs)


@pytest.mark.parametrize(
    ("boundary", "message"),
    [
        ("status: maybe", "pending or discovered"),
        ("status: discovered\n  bus: pty\n  own: half", "boundary.own"),
    ],
)
def test_target_rejects_bad_boundaries(tmp_path, boundary, message):
    with pytest.raises(target.TargetError, match=message):
        _load(tmp_path, boundary=boundary)


def test_target_rejects_a_boundary_that_is_not_a_mapping(tmp_path):
    spec, results = _write_target(tmp_path, OK)
    spec.write_text(
        spec.read_text().replace("boundary:\n  status: pending", "boundary: x")
    )
    with pytest.raises(target.TargetError, match="boundary must be a mapping"):
        target.load(spec, results)


def test_target_rejects_bad_files_and_layouts(tmp_path):
    spec, results = _write_target(tmp_path, OK)
    text = spec.read_text()
    cases = [
        ("- a list\n", "not a mapping"),
        (text.replace("product: P", "product: ../P"), "not a plain name"),
        (text.replace('version: "1"', 'version: "2"'), "must live at"),
        (
            text.replace('sysroot: ["w.zip!fs:", "w.zip!app.zip!"]', "sysroot: []"),
            "sysroot",
        ),
    ]
    for body, message in cases:
        spec.write_text(body)
        with pytest.raises(target.TargetError, match=message):
            target.load(spec, results)
    spec.write_text(text)
    manifest = results / "P" / "1" / "manifest.tsv"
    manifest.write_text(manifest.read_text().replace("# image_sha256", "# other"))
    with pytest.raises(target.TargetError, match="no image_sha256"):
        target.load(spec, results)
    manifest.unlink()
    with pytest.raises(target.TargetError, match="run phase 1 first"):
        target.load(spec, results)


def test_target_qemu_arch_needs_a_full_elf_tag():
    assert target.qemu_arch("ELF/MIPS/exec/32le") == "mipsel"
    for tag in ("ELF/ARM/exec", "data", "ELF/ARM/exec/16le"):
        with pytest.raises(target.TargetError):
            target.qemu_arch(tag)


def test_manifest_key_escapes_like_unpack():
    """stage.verify looks members up by manifest_key: it must be unpack's
    tsv_field, character for character."""
    hostile = "a\\b\tc\nd\x7fe\x01f"
    assert target.manifest_key(hostile) == unpack.tsv_field(hostile)


def test_manifest_rows_skip_header_and_junk(tmp_path):
    p = tmp_path / "m.tsv"
    p.write_text("# x\npath\ttype\tsize\tsha256\na\tdata\t1\tff\nshort\tline\n")
    assert target.read_rows(p) == {"a": ("data", "ff")}
    assert target.read_manifest(p) == {"a": "ff"}


# --- sandbox -------------------------------------------------------------------


def test_sandbox_wraps_without_network_or_host_writes(tmp_path):
    argv = sandbox.wrap(["python3", "-m", "oracle.run"], tmp_path.resolve())
    assert argv[0] == "unshare"
    assert "-r" in argv
    assert "-n" in argv
    assert "bwrap" in argv
    assert "--clearenv" in argv
    binds = [argv[i + 1] for i, a in enumerate(argv) if a == "--bind"]
    assert binds == [str(tmp_path.resolve())]  # the only writable host path
    assert argv[-3:] == ["python3", "-m", "oracle.run"]
    with pytest.raises(sandbox.SandboxError, match="absolute"):
        sandbox.wrap(["x"], Path("relative"))


@pytest.mark.parametrize("bad", ["relative", "/", "/a/../b", "/a b", "/a/./b", "/a//b"])
def test_sandbox_guest_paths_are_plain_and_absolute(bad):
    with pytest.raises(sandbox.SandboxError):
        sandbox.guest_path(bad)
    assert sandbox.guest_path("/home/bticino/bin") == "/home/bticino/bin"


def _opt(argv, flag):
    """Every value following `flag`, as tuples of its arguments."""
    width = {"--setenv": 2, "--bind": 2, "--dev-bind": 2, "--symlink": 2}.get(flag, 1)
    return [tuple(argv[i + 1 : i + 1 + width]) for i, a in enumerate(argv) if a == flag]


def test_sandbox_jail_makes_the_sysroot_root(tmp_path):
    trace = tmp_path / "trace"
    argv = sandbox.jail(
        tmp_path / "sysroot",
        ["/home/bticino/bin/scsserver", "-v"],
        cwd="/home/bticino",
        env={"LD_LIBRARY_PATH": "/home/bticino/lib"},
        devices={"/dev/ttyPIC": "/dev/pts/7"},
        tmpfs=("/tmp",),
        dirs=("/var/run",),
        links={"/var/link": "/home/target"},
        trace_dir=trace,
    )
    assert argv[:4] == ["bwrap", "--unshare-all", "--die-with-parent", "--new-session"]
    env = dict(_opt(argv, "--setenv"))
    assert env["QEMU_UNAME"] == "2.4.19"
    assert env["QEMU_STRACE"] == "1"
    assert env["QEMU_LOG_FILENAME"] == "/.oracle/trace/strace.%d"
    assert env["TZ"] == "UTC"
    assert env["LD_LIBRARY_PATH"] == "/home/bticino/lib"
    assert _opt(argv, "--bind") == [
        (str(tmp_path / "sysroot"), "/"),
        (str(trace), "/.oracle/trace"),
    ]
    assert _opt(argv, "--dev-bind") == [("/dev/pts/7", "/dev/ttyPIC")]
    assert _opt(argv, "--tmpfs") == [("/tmp",)]
    assert _opt(argv, "--dir") == [("/var/run",)]
    assert _opt(argv, "--symlink") == [("/home/target", "/var/link")]
    assert _opt(argv, "--chdir") == [("/home/bticino",)]
    assert argv[argv.index("--") + 1 :] == ["/home/bticino/bin/scsserver", "-v"]
    # without a trace dir, no trace and no extra bind
    plain = sandbox.jail(tmp_path, ["/bin/sh"])
    assert "QEMU_STRACE" not in dict(_opt(plain, "--setenv"))
    assert _opt(plain, "--bind") == [(str(tmp_path), "/")]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"sysroot": Path("rel")}, "sysroot must be absolute"),
        ({"argv": []}, "empty command"),
        ({"argv": ["bin/sh"]}, "guest path"),
        ({"cwd": "home"}, "guest path"),
        ({"argv": ["/bin/sh", "a\0b"]}, "NUL"),
        ({"trace_dir": Path("rel")}, "trace_dir must be absolute"),
        ({"env": {"bad-name": "x"}}, "bad environment entry"),
        ({"env": {"A": "x\0y"}}, "bad environment entry"),
        ({"devices": {"dev/x": "/dev/pts/1"}}, "guest path"),
    ],
)
def test_sandbox_jail_refuses_bad_input(tmp_path, kwargs, message):
    args = {"sysroot": tmp_path, "argv": ["/bin/sh"], **kwargs}
    sysroot, argv = args.pop("sysroot"), args.pop("argv")
    with pytest.raises(sandbox.SandboxError, match=message):
        sandbox.jail(sysroot, argv, **args)


def _binfmt(tmp_path, body):
    d = tmp_path / "binfmt"
    d.mkdir(exist_ok=True)
    (d / "qemu-arm").write_text(body)
    return d


def test_sandbox_binfmt_needs_an_enabled_entry_with_the_f_flag(tmp_path):
    with pytest.raises(sandbox.SandboxError, match="install qemu-user-static"):
        sandbox.binfmt_interpreter("arm", tmp_path)
    d = _binfmt(tmp_path, "disabled\ninterpreter /x\nflags: F\n")
    with pytest.raises(sandbox.SandboxError, match="disabled"):
        sandbox.binfmt_interpreter("arm", d)
    d = _binfmt(tmp_path, "enabled\ninterpreter /x\nflags: PO\noffset 0\n")
    with pytest.raises(sandbox.SandboxError, match="F flag"):
        sandbox.binfmt_interpreter("arm", d)
    d = _binfmt(
        tmp_path, "enabled\ninterpreter /usr/bin/qemu-arm-static\nflags: POCF\n"
    )
    assert sandbox.binfmt_interpreter("arm", d) == Path("/usr/bin/qemu-arm-static")


def test_sandbox_emulator_version_reads_the_real_binary(tmp_path):
    exe = tmp_path / "qemu-arm-static"
    exe.write_text("#!/bin/sh\necho 'qemu-arm version 8.2.2 (Debian 1:8.2.2)'\n")
    exe.chmod(0o755)
    link = tmp_path / "qemu-arm"  # binfmt names a link; --version needs the target
    link.symlink_to(exe)
    d = _binfmt(tmp_path, f"enabled\ninterpreter {link}\nflags: F\n")
    assert sandbox.emulator_version("arm", d) == "qemu-arm-8.2.2"
    exe.write_text("#!/bin/sh\necho nothing useful\n")
    with pytest.raises(sandbox.SandboxError, match="cannot read the version"):
        sandbox.emulator_version("arm", d)


# --- discover ------------------------------------------------------------------

TRACE = """\
101 open("/dev/ttyS1",O_RDWR|O_NOCTTY) = 3
101 ioctl(3,21505,0x7fff0000) = 0
101 close(3) = 0
101 ioctl(3,21505,0x7fff0000) = -1 errno=9 (Bad file descriptor)
101 open("/lib/libc.so.6",O_RDONLY) = 4
101 open("/home/bticino/cfg/stack_open.xml",O_RDONLY) = 5
101 bind(6,{sun_family=AF_UNIX,sun_path=/tmp/scs},110) = 0
101 connect(7,{sin_family=AF_INET,sin_port=htons(20000),\
sin_addr=inet_addr("127.0.0.1")},16) = -1 errno=111 (Connection refused)
102 open("/dev/ttyS1",O_RDWR|O_NOCTTY) = 8
garbage line
"""


def test_discover_reduces_a_trace_to_sorted_facts():
    facts = discover.parse(TRACE)
    assert facts == sorted(set(facts))
    assert [(f.kind, f.detail, f.result) for f in facts] == [
        ("bind", "unix:/tmp/scs", "ok"),
        ("connect", "inet:127.0.0.1:20000", "ECONNREFUSED"),
        ("ioctl", "/dev/ttyS1 0x5401", "ok"),
        ("ioctl", "fd 0x5401", "EBADF"),
        ("open", "/dev/ttyS1 O_RDWR|O_NOCTTY", "ok"),
        ("open", "/home/bticino/cfg/stack_open.xml O_RDONLY", "ok"),
    ]
    text = discover.render({"product": "MH200N"}, facts)
    assert text.startswith("# product=MH200N\nkind\tdetail\tresult\n")


QEMU_TRACE = """\
7 execve("/bin/sh",{"/bin/sh","-c","x",NULL})7 uname(0x4000) = 0
7 open("/dev/ttyPIC",O_RDWR|O_NOCTTY|O_NONBLOCK) = 5
7 ioctl(5,TCGETS,0xbe) = 0
7 ioctl(5,TCSETS,{c_iflag = IGNPAR|IXON|IXOFF,c_oflag = 0,\
c_cflag = B38400,CS8,CREAD|CLOCAL,c_lflag = 0}) = 0
7 ioctl(5,TIOCMGET,0xbe) = -1 errno=25 (Inappropriate ioctl for device)
7 ioctl(x,TCGETS) = 0
7 ioctl(5) = 0
7 socket(PF_INET,SOCK_STREAM,IPPROTO_IP) = 6
7 BIND(6,{sin_family=AF_INET,sin_port=htons(20001),\
sin_addr=inet_addr("0.0.0.0")}, 16,) = 0
7 LISTEN(6,5,) = 0
7 listen(9,5) = 0
7 listen(x,5) = 0
7 connect(8,{sin_family=AF_INET,sin_port=htons(40001)},16) = 0
7 connect(8,{sin_family=AF_INET6},28) = -1 errno=97 (Address family not supported)
7 connect(9,{sun_family=AF_UNIX,sun_path="/var/run/.nscd_socket"},110) = -1 errno=22
7 open("/home/bticino/lib/libcommon.so.0",O_RDONLY) = 10
7 open("/etc/passwd") = 11
7 open(0x0,O_RDONLY) = -1 errno=14 (Bad address)
7 close(x) = 0
7 execve(0x1234) = -1 errno=2
7 brk(NULL) = 0x00021000
7 mmap(NULL,4096) = ?
7 read(3,0x1,1) = -1 errno=4095
"""


def test_discover_handles_qemu_user_output():
    facts = {(f.kind, f.detail, f.result) for f in discover.parse(QEMU_TRACE)}
    assert facts == {
        ("execve", "/bin/sh", "ok"),  # never returned: it worked
        ("open", "/dev/ttyPIC O_RDWR|O_NOCTTY|O_NONBLOCK", "ok"),
        ("ioctl", "/dev/ttyPIC TCGETS", "ok"),
        (
            "ioctl",
            "/dev/ttyPIC TCSETS iflag=IGNPAR|IXON|IXOFF cflag=B38400,CS8,CREAD|CLOCAL",
            "ok",
        ),
        ("ioctl", "/dev/ttyPIC TIOCMGET", "ENOTTY"),
        ("bind", "inet:0.0.0.0:20001", "ok"),
        ("listen", "inet:0.0.0.0:20001", "ok"),
        ("connect", "inet:?:40001", "ok"),
        ("connect", "unix:/var/run/.nscd_socket", "EINVAL"),
        ("open", "/etc/passwd -", "ok"),
    }


def test_discover_keeps_fds_per_process_file(tmp_path):
    (tmp_path / "strace.1").write_text('1 open("/dev/a",O_RDWR) = 3\n')
    (tmp_path / "strace.2").write_text("2 ioctl(3,TCGETS,0x1) = 0\n")
    facts = discover.parse_files(sorted(tmp_path.glob("strace.*")))
    assert discover.Fact("ioctl", "fd TCGETS", "ok") in facts  # not /dev/a


def test_discover_exit_facts():
    assert discover.exit_fact(None) == discover.Fact("exit", "running", "timeout")
    assert discover.exit_fact(-11) == discover.Fact("exit", "signal", "SIGSEGV")
    assert discover.exit_fact(-200) == discover.Fact("exit", "signal", "SIG200")
    assert discover.exit_fact(139) == discover.Fact("exit", "status", "139")


def test_discover_render_escapes_device_bytes():
    text = discover.render({}, [discover.Fact("write", "/dev/ttyPIC", "$24\r\0")])
    assert text.splitlines()[-1] == "write\t/dev/ttyPIC\t$24\\x0d\\x00"


# --- stage ---------------------------------------------------------------------

L1, L2 = "w.zip!fs:", "w.zip!app.zip!"


@pytest.fixture
def members():
    return [
        stage.Member(L1, f"{L1}/bin/sh", "/bin/sh", b"\x7fELF...", None),
        stage.Member(L1, f"{L1}/etc/conf", "/etc/conf", b"a=1", None),
        stage.Member(L1, f"{L1}/lib/libc.so", "/lib/libc.so", None, "/lib/libc.so.6"),
        stage.Member(L1, f"{L1}/var/tmp", "/var/tmp", None, None),
        stage.Member(L1, f"{L1}/usr/up", "/usr/up", None, "../../../../etc/conf"),
        stage.Member(L2, f"{L2}etc/conf", "etc/conf", b"a=2", None),
        stage.Member(L2, f"{L2}run.sh", "run.sh", b"#!/bin/sh\n", None),
        stage.Member(L2, f"{L2}var", "var", None, None),  # already a dir: fine
    ]


def _manifest(members):
    out = {}
    for m in members:
        data = m.blob if m.blob is not None else os.fsencode(m.target or "")
        out[target.manifest_key(m.row)] = unpack.sha256(data)
    return out


def test_stage_writes_layers_in_order_and_contains_links(tmp_path, members):
    root = tmp_path / "sysroot"
    staged = stage.write(root, members, (L1, L2))
    assert (staged.files, staged.links, staged.dirs) == (4, 2, 2)
    assert (root / "etc/conf").read_bytes() == b"a=2"  # the later layer wins
    assert (root / "var/tmp").is_dir()
    assert (
        root / "lib/libc.so"
    ).readlink().as_posix() == "libc.so.6"  # absolute -> relative
    assert (
        root / "usr/up"
    ).readlink().as_posix() == "../etc/conf"  # '..' stops at the root
    assert stat.S_IMODE((root / "bin/sh").stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "run.sh").stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "etc/conf").stat().st_mode) == 0o600


def test_stage_contained_link_edge_cases():
    assert stage.contained_link("a", "/") == "."
    assert stage.contained_link("x/y", "./z") == "z"
    assert stage.contained_link("x/y", "/x/./y2") == "y2"


@pytest.mark.parametrize("name", ["", "/", "a//b", "a/./b", "a/../b", "a\0b"])
def test_stage_refuses_unsafe_member_paths(name):
    with pytest.raises(stage.StageError, match="unsafe member path"):
        stage.member_path(name)


@pytest.mark.parametrize(
    ("later", "message"),
    [
        (stage.Member(L2, f"{L2}etc", "etc", b"x", None), "cannot replace a directory"),
        (stage.Member(L2, f"{L2}etc/conf", "etc/conf", None, None), "directory cannot"),
        (stage.Member(L2, f"{L2}lib/libc.so/x", "lib/libc.so/x", b"", None), "symlink"),
        (stage.Member(L2, f"{L2}etc/conf/x", "etc/conf/x", b"", None), "is a file"),
    ],
)
def test_stage_never_writes_through_links_or_over_dirs(
    tmp_path, members, later, message
):
    with pytest.raises(stage.StageError, match=message):
        stage.write(tmp_path / "s", [*members[:3], later], (L1, L2))


def test_stage_root_must_be_absolute_and_new(tmp_path, members):
    with pytest.raises(stage.StageError, match="absolute"):
        stage.write(Path("rel"), members, (L1, L2))
    with pytest.raises(FileExistsError):
        stage.write(tmp_path, members, (L1, L2))


def test_stage_verify_matches_the_manifest_row_by_row(members):
    manifest = _manifest(members)
    # a row derived from a member (a payload inside it) is not a member
    manifest[f"{L1}/etc/conf~gunzip"] = "0" * 64
    manifest["elsewhere!x"] = "0" * 64  # not under a staged layer
    stage.verify(members, (L1, L2), manifest)
    with pytest.raises(stage.StageError, match="manifest is stale"):
        stage.verify(members, (L1, L2), {**manifest, f"{L2}run.sh": "0" * 64})
    with pytest.raises(stage.StageError, match="not in the image"):
        stage.verify(members, (L1, L2), {**manifest, f"{L2}gone": "0" * 64})


def test_stage_collects_only_the_sysroot_layers_through_unpacks_sink(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bin/a", b"\x7fELF")
        zf.writestr("var/run/", b"")  # an empty directory member
        link = zipfile.ZipInfo("lib/l.so")
        link.create_system = unpack.ZIP_UNIX
        link.external_attr = (unpack.S_IFLNK | 0o777) << 16
        zf.writestr(link, "l.so.0")
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as zf:
        zf.writestr("app.zip", buf.getvalue())
        zf.writestr("readme", b"not a layer")
    rows: list = []

    def walk(sink):
        unpack.walk("w.zip", outer.getvalue(), [], tmp_path / "u", rows, sink=sink)

    spec = target.TargetSpec("P", "1", ("w.zip!app.zip!",), {}, {"status": "pending"})
    walk(lambda *a: None)  # fills rows: the manifest phase 1 would write
    manifest = {unpack.tsv_field(r["path"]): r["sha256"] for r in rows}
    staged = stage.stage(spec, manifest, tmp_path / "root", walk)
    assert (staged.files, staged.links, staged.dirs) == (1, 1, 1)
    assert {r["type"] for r in rows if r["path"].endswith("var/run")} == {"dir"}
    assert not (tmp_path / "root" / "readme").exists()


# --- run: the discovery command line --------------------------------------------


def test_run_stage_target_verifies_the_wrapper_and_stages(tmp_path, monkeypatch):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("bin/a", b"\x7fELF")
        zf.writestr("tmp/", b"")
    data = inner.getvalue()
    entry = {"wrapper": {"filename": "w.zip", "size": len(data)}}
    entry["wrapper"]["sha256"] = unpack.sha256(data)
    rows: list = []
    unpack.walk("w.zip", data, [], tmp_path / "u0", rows)
    manifest = tmp_path / "results" / "P" / "1" / "manifest.tsv"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "# x\npath\ttype\tsize\tsha256\n"
        + "".join(
            f"{r['path']}\t{r['type']}\t{r['size']}\t{r['sha256']}\n" for r in rows
        )
    )
    monkeypatch.setattr(run, "ROOT", tmp_path)
    monkeypatch.setattr(run.schema, "load", lambda p: entry)
    monkeypatch.setattr(run.unpack, "passwords", lambda e: [])
    monkeypatch.setattr(run.unpack, "SANDBOX", True)
    image = tmp_path / "w.zip"
    image.write_bytes(data)
    spec = target.TargetSpec("P", "1", ("w.zip!",), {}, {"status": "pending"})
    work = tmp_path / "work"
    staged = run.stage_target(spec, image, work, sandboxed=False)
    assert unpack.SANDBOX is False
    assert (staged.files, staged.dirs) == (1, 1)
    assert (work / "sysroot" / "tmp").is_dir()
    image.write_bytes(data + b"x")
    with pytest.raises(SystemExit, match=r"is not w\.zip"):
        run.stage_target(spec, image, tmp_path / "work2")


# A stand-in for the firmware: writes NUL-padded CR-terminated commands to the
# device, leaves a trace file, then exits or keeps running.
FAKE_PROGRAM = """
import os, sys, time
dev, trace, linger = sys.argv[1], sys.argv[2], float(sys.argv[3])
open(os.path.join(trace, "strace.42"), "w").write(
    '42 open("/dev/ttyPIC",O_RDWR|O_NOCTTY) = 5\\n')
fd = os.open(dev, os.O_RDWR | os.O_NOCTTY)
os.write(fd, b"$24\\r\\0\\0\\0\\0$2603\\r$24\\r$unterminated")
print("console noise")
time.sleep(linger)
"""


def _fake_jail(linger):
    def jail(sysroot, argv, *, devices, trace_dir, **kw):
        assert argv[0] == "/home/bticino/bin/scsserver"
        (dev,) = devices.values()
        return [sys.executable, "-c", FAKE_PROGRAM, dev, str(trace_dir), str(linger)]

    return jail


def _spec():
    return target.load(ROOT / "oracle/targets/MH200N/010108.yaml", ROOT / "results")


@pytest.mark.parametrize(("linger", "code"), [(0, 0), (30, None)])
def test_run_discovery_listens_on_the_device_pty(tmp_path, monkeypatch, linger, code):
    monkeypatch.setattr(run.sandbox, "jail", _fake_jail(linger))
    console = tmp_path / "console.log"
    with console.open("wb") as fh:
        got, devices = run.run_discovery(
            _spec(), "scsserver", tmp_path, 3 if linger == 0 else 1.5, fh
        )
    assert got == code
    assert console.read_bytes() == b"console noise\n" or code is None
    facts = run.device_facts(devices)
    assert facts == [
        discover.Fact("write", "/dev/ttyPIC", "$24\r"),
        discover.Fact("write", "/dev/ttyPIC", "$2603\r"),
    ]  # each distinct burst once; the cut-off tail is not a frame
    assert discover.parse_files([tmp_path / "trace" / "strace.42"])


def test_run_cmd_discover_writes_the_boundary_record(tmp_path, monkeypatch, capsys):
    calls = {}

    def stage_target(spec, image, work, *, sandboxed):
        calls["sandboxed"] = sandboxed
        (work / "sysroot").mkdir()
        return stage.Staged(work / "sysroot", 3, 2, 1)

    def run_discovery(spec, program, work, seconds, console):
        (work / "trace").mkdir()
        (work / "trace" / "strace.1").write_text(
            '1 open("/dev/ttyPIC",O_RDWR) = 3\n1 LISTEN(3,1,) = 0\n'
        )
        dev = run.Device("/dev/ttyPIC", -1, -1, "/dev/pts/0", bursts=[b"$24\r"])
        return None, [dev]

    monkeypatch.setattr(run.sandbox, "emulator_version", lambda arch: "qemu-arm-9.9")
    monkeypatch.setattr(run, "stage_target", stage_target)
    monkeypatch.setattr(run, "run_discovery", run_discovery)
    target_yaml = str(ROOT / "oracle/targets/MH200N/010108.yaml")
    out = tmp_path / "out" / "scsserver.tsv"
    base = [target_yaml, "--image", "img.zip", "--program", "scsserver"]
    assert run.main(["discover", *base, "-o", str(out), "--seconds", "2"]) == 0
    text = out.read_text()
    assert text.splitlines()[:9] == [
        "# product=MH200N",
        "# version=010108",
        f"# image_sha256={_spec().image_sha256}",
        "# program=scsserver",
        f"# target_sha256={_spec().programs['scsserver'].sha256}",
        "# emulator=qemu-arm-9.9",
        "# kernel_release=2.4.19",
        "# window_s=2",
        "# oracle_version=1",
    ]
    assert text.splitlines()[9:] == [
        "kind\tdetail\tresult",
        "exit\trunning\ttimeout",
        "open\t/dev/ttyPIC O_RDWR\tok",
        "write\t/dev/ttyPIC\t$24\\x0d",
    ]
    assert calls["sandboxed"] is True
    assert "staged 3 files, 2 links, 1 empty dirs" in capsys.readouterr().err
    keep = tmp_path / "keep"
    orig_load = run.target.load

    def fake_load(target_file, results_dir):
        sp = orig_load(target_file, results_dir)
        new_rt = dataclasses.replace(
            sp.runtime, files={"/sys/devicetree/model": "Linda"}
        )
        return dataclasses.replace(sp, runtime=new_rt)

    monkeypatch.setattr(run.target, "load", fake_load)
    run.main(["discover", *base, "-o", str(out), "--keep", str(keep), "--no-sandbox"])
    assert (keep / "console.log").exists()
    assert (keep / "sysroot/sys/devicetree/model").read_text() == "Linda"
    assert calls["sandboxed"] is False
    with pytest.raises(FileExistsError):  # --keep never reuses a directory
        run.main(["discover", *base, "-o", str(out), "--keep", str(keep)])
    with pytest.raises(SystemExit, match="not a program"):
        run.main(["discover", target_yaml, "--image", "x", "--program", "bt_nope"])


def test_run_boundary_path_is_under_results():
    assert run.boundary_path(_spec(), "bt_luci") == (
        ROOT / "results/MH200N/010108/oracle/boundary/bt_luci.tsv"
    )


# --- coverage of the remaining small paths ------------------------------------


def test_pump_drains_what_an_exited_program_left_behind():
    devices = run.open_devices(target.Runtime(devices={"/dev/ttyPIC": "pty"}))
    (dev,) = devices
    try:
        os.write(dev.slave, b"$24\r$2702\r")

        class Exited:
            def poll(self):
                return 0

        run.pump(devices, until=0.0, proc=Exited())
        assert dev.bursts == [b"$24\r", b"$2702\r"]
    finally:
        os.close(dev.master)
        os.close(dev.slave)


def test_line_framer_cuts_at_cr_and_drops_padding():
    f = bus.LineFramer()
    assert f.feed(b"\0\0$24\r\0\0$26", 0) == [b"$24\r"]
    assert f.flush(0) == [b"$26"]
    assert f.flush(0) == []
    assert f.feed(b"a\nb\r", 0) == [b"a\n", b"b\r"]


def test_small_bus_pieces():
    clock = bus.MonotonicClock()
    t0 = clock.now_ms()
    clock.sleep_ms(1)
    assert clock.now_ms() > t0
    assert bus.IdleGapFramer().feed(b"", 5) == []
    r = bus.Scripted("status-74", {b"\x01": [b"\x02", b"\x03"]})
    assert r.name == "script:status-74"
    assert r.respond(b"\x01") == [b"\x02", b"\x03"]
    assert r.respond(b"\x09") == []
    b = bus.Bus(_NullPort(), bus.LineFramer(), bus.Silent())
    assert b.mark() == 0
    b.inject(b"x")
    assert b.mark() == 1


class _NullPort:
    def read(self):
        return b""

    def write(self, data):
        pass


def test_cases_reject_a_bare_direction_and_non_ascii(tmp_path):
    p = tmp_path / "s.cases"
    p.write_text("down\n")
    with pytest.raises(cases.CaseError, match="expected"):
        cases.load(p)
    p.write_bytes("down *1*1*31##\n; caf\u00e9\n".encode())
    with pytest.raises(cases.CaseError, match="ASCII"):
        cases.load(p)


def test_driver_marks_a_step_that_never_settles(tmp_path):
    class Busy:
        def send_own(self, frame):
            pass

        def inject_bus(self, data):
            pass

        def settle(self):
            return False

        def take_reply(self):
            return "-"

        def take_bus(self):
            return []

        def take_own(self):
            return []

        def alive(self):
            return True

        def restart(self):
            pass

    p = tmp_path / "s.cases"
    p.write_text("down *1*1*31##\n")
    (row,) = driver.run_suite(Busy(), cases.load(p))
    assert row.verdict == "timeout"


def test_header_only_files(tmp_path):
    p = tmp_path / "r.tsv"
    p.write_text("# product=P\n# version=1\n")
    assert record.read_header(p) == {"product": "P", "version": "1"}
    with pytest.raises(target.TargetError, match="no image_sha256"):
        target.manifest_image(p)
    spec = target.TargetSpec("P", "1", ("w!",), {}, {"status": "pending"})
    with pytest.raises(target.TargetError, match="not discovered"):
        spec.require_ready()


def test_failed_opens_name_no_fd():
    facts = discover.parse(
        '1 open("/dev/nvram",O_RDONLY) = -1 errno=2\n1 ioctl(-1,TCGETS,0x1) = 0\n'
    )
    assert discover.Fact("open", "/dev/nvram O_RDONLY", "ENOENT") in facts
    assert discover.Fact("ioctl", "fd TCGETS", "ok") in facts
