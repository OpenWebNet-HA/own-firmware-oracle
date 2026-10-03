"""jail -- run an extraction tool on untrusted firmware inside bubblewrap.

debugfs, unsquashfs and fsck.cramfs parse attacker-shaped filesystem images.
Each call runs with no network, no IPC, its own PID namespace, a cleared
environment, the host's system dirs read-only and exactly one writable host
path: the work dir the tool reads its image from and writes its tree to. The
work dir is bound at the same path inside, so the tool's arguments need no
rewriting.

`--no-sandbox` on unpack.py turns this off explicitly (e.g. a machine without
bubblewrap); it is never the silent default.
"""
from __future__ import annotations

import shutil
from pathlib import Path

# Read-only host paths the tools and their libraries live in; --ro-bind-try
# skips the ones a distro does not have.
HOST_RO = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc/alternatives", "/etc/ld.so.cache")


def command(argv: list[str], rw: Path) -> list[str]:
    """bwrap argv that runs `argv` with `rw` as the only writable host path."""
    if not rw.is_absolute():
        raise ValueError(f"work dir must be absolute: {rw}")
    if shutil.which("bwrap") is None:
        raise SystemExit(
            "bwrap not found: install bubblewrap (apt install bubblewrap), "
            "or pass --no-sandbox to run extraction tools unconfined"
        )
    ro: list[str] = []
    for p in HOST_RO:
        ro += ["--ro-bind-try", p, p]
    return [
        "bwrap",
        "--unshare-all", "--die-with-parent", "--new-session",
        "--clearenv",
        "--setenv", "PATH", "/usr/sbin:/usr/bin:/sbin:/bin",
        "--setenv", "LANG", "C",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        *ro,
        "--bind", str(rw), str(rw),
        "--chdir", str(rw),
        "--",
        *argv,
    ]
