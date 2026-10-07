"""run -- phase 2 command line.

    python -m oracle.run discover oracle/targets/MH200N/010108.yaml \\
        --image FW_MH200N_vers_010108.zip --program scsserver

`discover` stages the target's sysroot from the image (oracle/stage.py), runs
one program in the firmware jail (oracle/sandbox.py) for a fixed window with
qemu's syscall trace on, holds the master side of a pty for every device node
the target declares, and writes results/<product>/<version>/oracle/boundary/
<program>.tsv: the trace reduced to facts (oracle/discover.py), every distinct
burst the program wrote to a device, and how the run ended. Nothing is sent to
the program; discovery only listens.

The staged vendor files live in a temp work dir that is gone when the command
returns, unless --keep names a new directory for them (and the raw trace).
"""

from __future__ import annotations

import argparse
import contextlib
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
import tty
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from oracle import (
    ORACLE_VERSION,
    cases,
    discover,
    driver,
    qemu_target,
    record,
    sandbox,
    stage,
    target,
)
from oracle.bus import LineFramer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import schema  # noqa: E402 - tools/ is not a package
import unpack  # noqa: E402


def stage_target(
    spec: target.TargetSpec, image: Path, work: Path, *, sandboxed: bool = True
) -> stage.Staged:
    """Verify the wrapper, walk it with phase 1's walker, stage the sysroot."""
    entry = schema.load(ROOT / "catalog" / spec.product / f"{spec.version}.yaml")
    data = image.read_bytes()
    unpack.verify_wrapper(entry, data)
    unpack.SANDBOX = sandboxed
    manifest = target.read_manifest(
        ROOT / "results" / spec.product / spec.version / "manifest.tsv"
    )

    def walk(sink: unpack.Sink) -> None:
        unpack.walk(
            entry["wrapper"]["filename"],
            data,
            unpack.passwords(entry),
            work / "unpack",
            [],
            limit=unpack.expand_limit(entry),
            sink=sink,
        )

    return stage.stage(spec, manifest, work / "sysroot", walk)


@dataclass
class Device:
    """The driver's end of one device node: a pty whose slave the jail binds
    at the guest path."""

    guest: str
    master: int
    slave: int  # held open so the master never reads EIO between runs
    slave_path: str
    # Line framing, not an idle gap: discovery must not depend on scheduling.
    framer: LineFramer = field(default_factory=LineFramer)
    bursts: list[bytes] = field(default_factory=list)


def open_devices(runtime: target.Runtime) -> list[Device]:
    devices = []
    for guest in sorted(runtime.devices):  # kinds are validated: all ptys
        master, slave = os.openpty()
        tty.setraw(master)
        devices.append(Device(guest, master, slave, os.ttyname(slave)))
    return devices


def _read_ready(by_fd: dict[int, Device], timeout: float) -> bool:
    """Feed whatever is readable now; False if nothing was."""
    ready, _, _ = select.select(list(by_fd), [], [], timeout)
    now = time.monotonic() * 1000.0
    for fd in ready:
        dev = by_fd[fd]
        dev.bursts += dev.framer.feed(os.read(fd, 4096), now)
    return bool(ready)


def pump(devices: list[Device], until: float, proc: subprocess.Popen[bytes]) -> None:
    """Collect device output until the deadline or the program exits, then
    drain what is still buffered. A frame left unterminated at the end is
    dropped: where the window cut it is timing, not firmware behaviour."""
    by_fd = {d.master: d for d in devices}
    while time.monotonic() < until and proc.poll() is None:
        _read_ready(by_fd, 0.01)
    # Once the writer is gone the buffer is finite; a live writer would never
    # let this loop end, and what it adds after the deadline is not recorded.
    while proc.poll() is not None and by_fd and _read_ready(by_fd, 0):
        pass


def run_discovery(
    spec: target.TargetSpec,
    program: str,
    work: Path,
    seconds: float,
    console: IO[bytes],
) -> tuple[int | None, list[Device]]:
    """Return code (None if still running at the deadline) and device output.
    Syscall traces land in work/trace/strace.<pid>; the program's own stdout
    and stderr go to `console` (kept with --keep, never committed)."""
    prog = spec.programs[program]
    devices = open_devices(spec.runtime)
    (work / "trace").mkdir()
    argv = sandbox.jail(
        work / "sysroot",
        ["/" + prog.path, *prog.args],
        cwd=spec.runtime.cwd,
        env=spec.runtime.env,
        devices={d.guest: d.slave_path for d in devices},
        tmpfs=spec.runtime.tmpfs,
        dirs=spec.runtime.dirs,
        links=spec.runtime.links,
        trace_dir=work / "trace",
        release=spec.runtime.kernel_release or sandbox.KERNEL_RELEASE,
    )
    proc = subprocess.Popen(  # noqa: S603 - argv built by sandbox.jail
        argv, stdin=subprocess.DEVNULL, stdout=console, stderr=console
    )
    try:
        pump(devices, time.monotonic() + seconds, proc)
        code = proc.poll()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        for dev in devices:
            os.close(dev.master)
            os.close(dev.slave)
    return code, devices


def device_facts(devices: list[Device]) -> list[discover.Fact]:
    """Each distinct burst once: a retry loop is one fact, not N."""
    return [
        discover.Fact("write", dev.guest, burst.decode("latin-1"))
        for dev in devices
        for burst in dict.fromkeys(dev.bursts)
    ]


def boundary_header(
    spec: target.TargetSpec, program: str, emulator: str, seconds: float
) -> dict[str, str]:
    prog = spec.programs[program]
    return {
        "product": spec.product,
        "version": spec.version,
        "image_sha256": spec.image_sha256,
        "program": program,
        "target_sha256": prog.sha256,
        "emulator": emulator,
        "kernel_release": spec.runtime.kernel_release or sandbox.KERNEL_RELEASE,
        "window_s": f"{seconds:g}",
        "oracle_version": ORACLE_VERSION,
    }


def boundary_path(spec: target.TargetSpec, program: str) -> Path:
    return (
        ROOT
        / "results"
        / spec.product
        / spec.version
        / "oracle"
        / "boundary"
        / f"{program}.tsv"
    )


def cmd_discover(args: argparse.Namespace) -> int:
    spec = target.load(Path(args.target), ROOT / "results")
    if args.program not in spec.programs:
        raise SystemExit(f"{args.program!r} is not a program of {args.target}")
    emulator = sandbox.emulator_version(spec.arch)
    with contextlib.ExitStack() as stack:
        if args.keep:
            work = Path(args.keep).resolve()
            work.mkdir(parents=True, exist_ok=False)
        else:
            work = Path(
                stack.enter_context(
                    tempfile.TemporaryDirectory(
                        prefix="own-oracle-", ignore_cleanup_errors=True
                    )
                )
            ).resolve()
        staged = stage_target(
            spec, Path(args.image), work, sandboxed=not args.no_sandbox
        )
        print(
            f"staged {staged.files} files, {staged.links} links, "
            f"{staged.dirs} empty dirs",
            file=sys.stderr,
        )
        for f_path, content in spec.runtime.files.items():
            dest = work / "sysroot" / f_path.lstrip("/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="latin-1")
        with (work / "console.log").open("wb") as console:
            code, devices = run_discovery(
                spec, args.program, work, args.seconds, console
            )
        traced = discover.parse_files(sorted((work / "trace").glob("strace.*")))
    facts = sorted({*traced, *device_facts(devices), discover.exit_fact(code)})
    out = Path(args.out) if args.out else boundary_path(spec, args.program)
    out.parent.mkdir(parents=True, exist_ok=True)
    header = boundary_header(spec, args.program, emulator, args.seconds)
    out.write_text(discover.render(header, facts), encoding="ascii", newline="\n")
    print(f"{len(facts)} facts -> {out}", file=sys.stderr)
    return 0


def parse_reset(reset: str) -> int:
    if reset == "each":
        return 1
    if reset.startswith("batch-"):
        num = reset[len("batch-") :]
        if num.isdigit() and int(num) > 0:
            return int(num)
    if reset.isdigit():
        return int(reset)
    raise SystemExit(
        f"invalid reset policy {reset!r}: must be 'each', 'batch-N', or integer"
    )


def validate_harness(spec: target.TargetSpec, harness: str) -> str:
    if not record.HARNESS.fullmatch(harness):
        raise SystemExit(
            f"invalid harness {harness!r}: must be 'full' or 'unit:<program>'"
        )
    if harness == "full":
        own_servers = [p for p in spec.programs.values() if p.role == "own_server"]
        if not own_servers and "openserver" not in spec.programs:
            raise SystemExit("harness 'full' requires 'openserver' in target programs")
        return own_servers[0].name if own_servers else "openserver"
    prog = harness[len("unit:") :]
    if prog not in spec.programs:
        raise SystemExit(
            f"program {prog!r} for harness {harness!r} not in target programs: "
            f"{sorted(spec.programs)}"
        )
    return prog


def reexec_suite_in_sandbox(
    args: argparse.Namespace, spec: target.TargetSpec, suite: cases.Suite
) -> int:
    if shutil.which("bwrap") is None:
        raise SystemExit(
            "bwrap not found: install bubblewrap (apt install bubblewrap), "
            "or pass --no-sandbox to run unconfined"
        )
    out_target = (
        Path(args.out).resolve()
        if args.out
        else suite_path(spec, args.harness, suite.name).resolve()
    )
    out_target.parent.mkdir(parents=True, exist_ok=True)

    with contextlib.ExitStack() as stack:
        if args.keep:
            work = Path(args.keep).resolve()
            work.mkdir(parents=True, exist_ok=False)
        else:
            work = Path(
                stack.enter_context(
                    tempfile.TemporaryDirectory(
                        prefix="own-oracle-", ignore_cleanup_errors=True
                    )
                )
            ).resolve()

        py_bin = Path(sys.executable).resolve()
        ro_binds = (
            ROOT,
            py_bin.parent,
            py_bin.parent.parent,
            Path(sys.prefix).resolve(),
            Path(sys.base_prefix).resolve(),
            Path("/home"),
            Path(args.image).resolve().parent,
            Path(args.target).resolve().parent,
            Path(args.suite).resolve().parent,
        )
        rw_binds = (out_target.parent,)
        sub_cmd = [
            sys.executable,
            "-m",
            "oracle.run",
            "suite",
            str(Path(args.target).resolve()),
            str(Path(args.suite).resolve()),
            "--image",
            str(Path(args.image).resolve()),
            "--harness",
            args.harness,
            "--reset",
            args.reset,
            "-o",
            str(out_target),
            "--keep",
            str(work),
        ]
        extra_env = {
            "PATH": f"{py_bin.parent}:/usr/bin:/bin",
            "PYTHONPATH": str(ROOT),
            "ORACLE_IN_SANDBOX": "1",
        }
        bwrap_argv = sandbox.wrap(
            sub_cmd,
            work,
            ro_binds=ro_binds,
            rw_binds=rw_binds,
            env=extra_env,
        )
        res = subprocess.run(bwrap_argv, check=False)  # noqa: S603
        return res.returncode


def suite_header(
    spec: target.TargetSpec,
    harness: str,
    target_sha256: str,
    reset: str,
    framer: str,
    responder: str,
    settle_ms: float,
    suite: cases.Suite,
) -> dict[str, str]:
    return {
        "product": spec.product,
        "version": spec.version,
        "image_sha256": spec.image_sha256,
        "harness": harness,
        "target_sha256": target_sha256,
        "adapter": "pty-1",
        "reset": reset,
        "bus": spec.boundary["bus"],
        "framer": framer,
        "responder": responder,
        "settle_ms": f"{int(settle_ms)}",
        "suite": suite.name,
        "suite_sha256": suite.sha256,
        "oracle_version": ORACLE_VERSION,
    }


def suite_path(spec: target.TargetSpec, harness: str, suite_name: str) -> Path:
    return (
        ROOT
        / "results"
        / record.result_path(spec.product, spec.version, harness, suite_name)
    )


def cmd_suite(args: argparse.Namespace) -> int:
    spec = target.load(Path(args.target), ROOT / "results")
    spec.require_ready()
    suite = cases.load(Path(args.suite))
    main_prog = validate_harness(spec, args.harness)
    restart_every = parse_reset(args.reset)

    if not args.no_sandbox and not (
        sandbox.is_net_isolated() or os.environ.get("ORACLE_IN_SANDBOX")
    ):
        return reexec_suite_in_sandbox(args, spec, suite)

    with contextlib.ExitStack() as stack:
        if args.keep:
            work = Path(args.keep).resolve()
            work.mkdir(parents=True, exist_ok=True)
        else:
            work = Path(
                stack.enter_context(
                    tempfile.TemporaryDirectory(
                        prefix="own-oracle-", ignore_cleanup_errors=True
                    )
                )
            ).resolve()
        stage_work = work / "stage"
        staged = stage_target(
            spec, Path(args.image), stage_work, sandboxed=not args.no_sandbox
        )
        print(
            f"staged {staged.files} files, {staged.links} links, "
            f"{staged.dirs} empty dirs",
            file=sys.stderr,
        )
        with qemu_target.QemuTarget(
            spec,
            Path(args.image),
            work,
            harness=args.harness,
            staged_sysroot=staged.root,
            sandboxed=not args.no_sandbox,
        ) as tgt:
            rows = driver.run_suite(tgt, suite, restart_every=restart_every)
            header = suite_header(
                spec,
                args.harness,
                spec.programs[main_prog].sha256,
                args.reset,
                tgt.bus.framer.name,
                tgt.bus.responder.name,
                tgt.settle_ms,
                suite,
            )
    out = Path(args.out) if args.out else suite_path(spec, args.harness, suite.name)
    record.write(out, header, rows, ordered=suite.ordered)
    print(f"{len(rows)} rows -> {out}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m oracle.run", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover", help="stage a target and trace one program")
    d.add_argument("target", help="oracle/targets/<product>/<version>.yaml")
    d.add_argument("--image", required=True, help="the catalog wrapper on disk")
    d.add_argument("--program", required=True)
    d.add_argument("--seconds", type=float, default=10.0)
    d.add_argument("-o", "--out", help="default: results/.../boundary/<prog>.tsv")
    d.add_argument("--keep", help="stage into this NEW dir; keep it and the trace")
    d.add_argument(
        "--no-sandbox",
        action="store_true",
        help="phase 1 extraction tools without bubblewrap (the firmware "
        "itself always runs in the jail)",
    )

    s = sub.add_parser("suite", help="run a test suite against a target")
    s.add_argument("target", help="oracle/targets/<product>/<version>.yaml")
    s.add_argument("suite", help="oracle/cases/<suite>.cases or .seq")
    s.add_argument("--image", required=True, help="the catalog wrapper on disk")
    s.add_argument(
        "-o", "--out", help="default: results/.../oracle/<harness>/<suite>.tsv"
    )
    s.add_argument("--harness", default="full", help="default: full")
    s.add_argument(
        "--reset",
        default="each",
        help="reset policy: each (default), batch-N, or 0 (never)",
    )
    s.add_argument("--keep", help="stage into this NEW dir; keep it")
    s.add_argument(
        "--no-sandbox",
        action="store_true",
        help="phase 1 extraction tools without bubblewrap",
    )

    args = ap.parse_args(argv)
    if args.cmd == "discover":
        return cmd_discover(args)
    return cmd_suite(args)


if __name__ == "__main__":
    raise SystemExit(main())
