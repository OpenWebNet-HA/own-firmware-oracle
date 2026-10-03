"""discover -- reduce a `qemu-arm -strace` trace to boundary facts.

Which device nodes a program opens, which ioctl requests it sends to them,
which sockets it binds or connects: observed behaviour, not code. The facts
decide the bus cut (pty vs socket) and the OpenWebNet cut (full vs unit) per
target (docs/oracle-architecture.md section 5).

The output is deterministic: fd numbers, PIDs and pointer values are dropped,
results become 'ok' or an errno name, and facts are de-duplicated and sorted.
The parser is tolerant on purpose -- lines it does not recognise are skipped --
and will be tightened against the first real trace.
"""
from __future__ import annotations

import errno
import re
from dataclasses import dataclass

from oracle.record import tsv_field

# "1234 open("/dev/ttyS1",O_RDWR|O_NOCTTY) = 3"
CALL = re.compile(r"^(?:\d+ )?(?P<name>[a-z_0-9]+)\((?P<args>.*)\) = (?P<ret>.+)$")
ERRNO = re.compile(r"-1 errno=(\d+)")
FIRST_STRING = re.compile(r'^"((?:[^"\\]|\\.)*)"')
SUN_PATH = re.compile(r"sun_path=([^},]*)")
SIN_PORT = re.compile(r"sin_port=htons\((\d+)\)")
SIN_ADDR = re.compile(r'sin_addr=inet_addr\("([0-9.]+)"\)')
OPEN_FLAGS = re.compile(r"O_[A-Z]+(?:\|O_[A-Z]+)*")

OPENS = {"open", "openat"}
SOCKADDR_CALLS = {"bind", "connect"}
INTERESTING_PREFIXES = ("/dev/", "/proc/", "/sys/", "/var/", "/tmp/", "/home/", "/etc/")


@dataclass(frozen=True, order=True)
class Fact:
    kind: str
    detail: str
    result: str


def _result(ret: str) -> tuple[str, int | None]:
    """('ok', value) or (errno name, None)."""
    m = ERRNO.match(ret.strip())
    if m:
        code = int(m.group(1))
        return errno.errorcode.get(code, f"errno{code}"), None
    try:
        return "ok", int(ret.split()[0], 0)
    except ValueError:
        return "ok", None


def _sockaddr(args: str) -> str | None:
    if m := SUN_PATH.search(args):
        return f"unix:{m.group(1)}"
    port = SIN_PORT.search(args)
    if port:
        addr = SIN_ADDR.search(args)
        return f"inet:{addr.group(1) if addr else '?'}:{port.group(1)}"
    return None


def parse(trace: str) -> list[Fact]:
    facts: set[Fact] = set()
    fd_path: dict[int, str] = {}
    for raw in trace.splitlines():
        m = CALL.match(raw.strip())
        if not m:
            continue
        name, args, ret = m.group("name"), m.group("args"), m.group("ret")
        result, value = _result(ret)
        if name in OPENS:
            # openat's first argument is the dirfd; the path is the first string
            path_m = FIRST_STRING.search(args[args.find('"'):]) if '"' in args else None
            if not path_m:
                continue
            path = path_m.group(1)
            if not path.startswith(INTERESTING_PREFIXES):
                continue
            flags = OPEN_FLAGS.search(args)
            facts.add(Fact("open", f"{path} {flags.group(0) if flags else '-'}", result))
            if value is not None and result == "ok":
                fd_path[value] = path
        elif name == "ioctl":
            parts = args.split(",")
            try:
                fd, req = int(parts[0]), int(parts[1], 0)
            except (IndexError, ValueError):
                continue
            target = fd_path.get(fd, "fd")
            facts.add(Fact("ioctl", f"{target} req=0x{req:04x}", result))
        elif name in SOCKADDR_CALLS:
            addr = _sockaddr(args)
            if addr:
                facts.add(Fact(name, addr, result))
        elif name == "close":
            try:
                fd_path.pop(int(args.split(",")[0]), None)  # fds get reused
            except ValueError:
                continue
        elif name == "execve":
            path_m = FIRST_STRING.search(args)
            if path_m:
                facts.add(Fact("execve", path_m.group(1), result))
    return sorted(facts)


def render(header: dict[str, str], facts: list[Fact]) -> str:
    lines = [f"# {k}={v}" for k, v in header.items()]
    lines.append("kind\tdetail\tresult")
    lines += [f"{f.kind}\t{tsv_field(f.detail)}\t{f.result}" for f in facts]
    return "\n".join(lines) + "\n"
