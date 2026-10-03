"""sandbox -- command lines for running firmware: bwrap around the run, qemu-arm per program.

Builds argv lists only; nothing here executes. One bwrap wraps the WHOLE run
(driver + firmware), so the driver can reach the firmware's loopback sockets
while neither has a network: --unshare-all leaves a private loopback only.
The host is visible read-only for the interpreter and qemu; the per-run work
dir (holding the throwaway sysroot copy) is the only writable host path.
"""
from __future__ import annotations

from pathlib import Path

from oracle.target import check_rel_path

QEMU = "qemu-arm"
# uname release the MH200N kernel reports; old userlands check it.
KERNEL_RELEASE = "2.4.19"
# Read-only host paths; --ro-bind-try skips the ones a distro lacks.
HOST_RO = ("/usr", "/bin", "/lib", "/lib64", "/etc/alternatives", "/etc/ld.so.cache")


def wrap(cmd: list[str], workdir: Path) -> list[str]:
    if not workdir.is_absolute():
        raise ValueError(f"workdir must be absolute: {workdir}")
    ro: list[str] = []
    for p in HOST_RO:
        ro += ["--ro-bind-try", p, p]
    return [
        "bwrap",
        "--unshare-all", "--die-with-parent", "--new-session",
        "--clearenv",
        "--setenv", "TZ", "UTC",
        "--setenv", "LANG", "C",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--proc", "/proc",
        "--dev", "/dev",          # private /dev with its own /dev/pts for the bus pty
        "--tmpfs", "/tmp",
        *ro,
        "--bind", str(workdir), str(workdir),
        "--chdir", str(workdir),
        "--",
        *cmd,
    ]


def qemu(sysroot: Path, program: str, args: tuple[str, ...] = (), *,
         strace: bool = False, release: str = KERNEL_RELEASE) -> list[str]:
    """qemu-arm user mode with -L: the guest's absolute paths resolve inside the
    sysroot first, which is how a device node becomes our pty (a symlink placed
    in <sysroot>/dev by the stage step)."""
    check_rel_path(program)
    if any("\0" in a for a in args):
        raise ValueError("NUL in a program argument")
    return [
        QEMU, "-L", str(sysroot), "-r", release,
        *(["-strace"] if strace else []),
        str(sysroot / program), *args,
    ]
