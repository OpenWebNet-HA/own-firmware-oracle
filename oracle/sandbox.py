"""sandbox -- command lines for running firmware.

Builds argv lists and reads host facts; it never starts the firmware itself.

The firmware jail (`jail`) makes the staged sysroot the root directory, the
way the device sees it: every guest path -- absolute symlinks, the loader's
library search, /etc/ld.so.cache -- resolves inside the firmware tree and
nowhere else. (qemu's -L prefix falls back to the HOST path when a file is
missing from the prefix; the first trace read the host's ld.so.cache that way.)
Foreign binaries run through the kernel's binfmt_misc entry for their CPU,
registered with the F flag so the interpreter works inside the new root;
qemu-user reads QEMU_STRACE / QEMU_UNAME from the environment, so a program's
children are traced and see the same kernel release. Device nodes the target
names are bind-mounted from host ptys the driver holds. --unshare-all leaves
the jail a private loopback and no network.

`wrap` is the outer jail for the driver of a suite run (phase 2b): host
read-only, the work dir writable, the same private network the firmware jail
inherits when it is started inside it.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

BINFMT_DIR = Path("/proc/sys/fs/binfmt_misc")
# uname release the MH200N kernel reports; old userlands check it.
KERNEL_RELEASE = "2.4.19"
# Read-only host paths for the driver jail; --ro-bind-try skips missing ones.
HOST_RO = ("/usr", "/bin", "/lib", "/lib64", "/etc/alternatives", "/etc/ld.so.cache")
GUEST_PATH = re.compile(r"/[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*")
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")
QEMU_VERSION = re.compile(r"version (\d+(?:\.\d+)+)")
# Where the jail sees the host trace dir. Outside every firmware path, so a
# program cannot mistake it for its own state.
GUEST_TRACE_DIR = "/.oracle/trace"


class SandboxError(RuntimeError):
    pass


def guest_path(path: str) -> str:
    if not GUEST_PATH.fullmatch(path) or any(p in {".", ".."} for p in path.split("/")):
        raise SandboxError(f"not a plain absolute guest path: {path!r}")
    return path


def binfmt_interpreter(arch: str, binfmt_dir: Path = BINFMT_DIR) -> Path:
    """The interpreter registered for `qemu-<arch>`, which must carry the F
    flag (opened at registration, so it works inside the jail's root)."""
    entry = binfmt_dir / f"qemu-{arch}"
    try:
        lines = entry.read_text(encoding="ascii").splitlines()
    except OSError:
        raise SandboxError(
            f"no binfmt_misc entry {entry}; install qemu-user-static "
            "(it registers one per CPU)"
        ) from None
    if lines[:1] != ["enabled"]:
        raise SandboxError(f"{entry} is disabled")
    fields = dict(line.split(" ", 1) for line in lines[1:] if " " in line)
    if "F" not in fields.get("flags:", ""):
        raise SandboxError(f"{entry} lacks the F flag; the jail cannot reach it")
    return Path(fields["interpreter"])


def emulator_version(arch: str, binfmt_dir: Path = BINFMT_DIR) -> str:
    """`qemu-arm-8.2.2`: part of every header, since a qemu upgrade can change
    what a program sees. The binfmt interpreter is a link to qemu-<arch>-static,
    which refuses --version under its binfmt name, hence resolve()."""
    exe = binfmt_interpreter(arch, binfmt_dir).resolve()
    out = subprocess.run(  # noqa: S603 - the resolved interpreter, fixed args
        [str(exe), "--version"], capture_output=True, text=True, check=False
    ).stdout
    m = QEMU_VERSION.search(out)
    if not m:
        raise SandboxError(f"cannot read the version of {exe}: {out!r}")
    return f"qemu-{arch}-{m.group(1)}"


def jail(
    sysroot: Path,
    argv: list[str],
    *,
    cwd: str = "/",
    env: dict[str, str] | None = None,
    devices: dict[str, str] | None = None,
    tmpfs: tuple[str, ...] = (),
    dirs: tuple[str, ...] = (),
    trace_dir: Path | None = None,
    release: str = KERNEL_RELEASE,
    share_net: bool = False,
) -> list[str]:
    """bwrap argv running guest `argv` with `sysroot` as '/'.

    devices: guest path -> host path to bind there (a pty slave, usually).
    tmpfs / dirs: guest dirs to mount empty / to create after the mounts --
    what the device's boot scripts would have set up.
    trace_dir: turn on qemu's syscall trace, one file per process
    (strace.<pid>), so the calls of concurrent programs never interleave.
    share_net: keep the parent network namespace (loopback for IPC/OWN).
    """
    if not sysroot.is_absolute():
        raise SandboxError(f"sysroot must be absolute: {sysroot}")
    if not argv:
        raise SandboxError("empty command")
    guest_path(argv[0])
    if cwd != "/":
        guest_path(cwd)
    if any("\0" in a for a in argv):
        raise SandboxError("NUL in a program argument")
    full_env = {"PATH": "/bin:/usr/bin:/sbin:/usr/sbin", "TZ": "UTC", "LANG": "C"}
    full_env |= env or {}
    full_env["QEMU_UNAME"] = release
    binds: list[str] = []
    if trace_dir is not None:
        if not trace_dir.is_absolute():
            raise SandboxError(f"trace_dir must be absolute: {trace_dir}")
        full_env["QEMU_STRACE"] = "1"
        full_env["QEMU_LOG_FILENAME"] = f"{GUEST_TRACE_DIR}/strace.%d"
        binds = ["--bind", str(trace_dir), GUEST_TRACE_DIR]
    out = ["bwrap", "--unshare-all"]
    if share_net:
        out += ["--share-net"]
    out += ["--die-with-parent", "--new-session", "--clearenv"]
    for key in sorted(full_env):
        if not ENV_NAME.fullmatch(key) or "\0" in full_env[key]:
            raise SandboxError(f"bad environment entry {key!r}")
        out += ["--setenv", key, full_env[key]]
    out += ["--bind", str(sysroot), "/", "--dev", "/dev", "--proc", "/proc"]
    for guest, host in sorted((devices or {}).items()):
        out += ["--dev-bind", host, guest_path(guest)]
    for path in tmpfs:
        out += ["--tmpfs", guest_path(path)]
    for path in dirs:
        out += ["--dir", guest_path(path)]
    out += [*binds, "--chdir", cwd, "--", *argv]
    return out


def is_net_isolated() -> bool:
    """Check whether the process runs in an isolated network namespace.

    Returns True if /proc/net/route has no active routes (loopback only).
    """
    route_file = Path("/proc/net/route")
    if not route_file.exists():
        return False
    try:
        lines = [
            line.strip()
            for line in route_file.read_text(encoding="ascii").splitlines()
            if line.strip()
        ]
        return len(lines) <= 1
    except OSError:
        return False


def wrap(
    cmd: list[str],
    workdir: Path,
    *,
    ro_binds: tuple[Path, ...] = (),
    rw_binds: tuple[Path, ...] = (),
    env: dict[str, str] | None = None,
) -> list[str]:
    """The driver jail: host read-only, work dir writable, private network."""
    if not workdir.is_absolute():
        raise SandboxError(f"workdir must be absolute: {workdir}")
    ro: list[str] = []
    for p in HOST_RO:
        ro += ["--ro-bind-try", p, p]
    for p_ro in ro_binds:
        ro += ["--ro-bind-try", str(p_ro), str(p_ro)]
    extra_rw: list[str] = []
    for p_rw in rw_binds:
        extra_rw += ["--bind", str(p_rw), str(p_rw)]
    env_args: list[str] = [
        "--setenv",
        "TZ",
        "UTC",
        "--setenv",
        "LANG",
        "C",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
    ]
    if env:
        for k, v in sorted(env.items()):
            env_args += ["--setenv", k, v]
    return [
        "bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        *env_args,
        *("--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"),
        *ro,
        *("--bind", str(workdir), str(workdir)),
        *extra_rw,
        *("--chdir", str(workdir)),
        "--",
        *cmd,
    ]
