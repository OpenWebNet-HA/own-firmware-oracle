#!/usr/bin/env python3
"""live_probe -- read-only live OpenWebNet gateway probe producing oracle-shaped TSVs.

Connects to a physical OpenWebNet gateway on TCP port 20000, enforces a strict
read-only frame allow-list (`*#<WHO>*<WHERE>##` and `*#<WHO>*<WHERE>*<DIM>...##`
status/dimension reads only) BEFORE opening any socket, and writes an
oracle-compatible TSV (`direction\tinput\treply\tverdict\toutput`) that
`tools/check.py --live-tsv` can compare directly against emulated firmware.
"""

from __future__ import annotations

import argparse
import re
import socket
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from oracle import ORACLE_VERSION, cases  # noqa: E402

ACK = "*#*1##"
NACK = "*#*0##"
COMMAND_SESSION = "*99*0##"
NONCE_RE = re.compile(r"^\*#(?P<nonce>\d+)##$")

# Strictly status requests (*#WHO*WHERE##) and dimension reads
# (*#WHO*WHERE*DIM[#P...]##). Rejects commands (*WHO*WHAT*WHERE##), dimension
# writes (*#WHO*WHERE*#DIM*...##), and session/handshake frames (*#*1##, *99*0##).
READONLY_FRAME_RE = re.compile(
    r"^\*#(?P<who>\d+)\*(?P<where>(?:\*|[#0-9]+)?)(?:\*(?P<dim>\d+(?:#\d+)*))?##$"
)

MODEL_MAP: dict[str, str] = {
    "2": "MH200",
    "4": "F452",
    "6": "F452V",
    "11": "MH200N",
    "12": "F453AV",
    "13": "H4684",
    "16": "F454",
    "17": "F455",
    "19": "L4684",
    "22": "MH202",
    "23": "F459",
    "27": "F460",
    "28": "MyHomeServer1",
    "29": "F461",
    "200": "F450",
}

SocketFactory = Callable[[tuple[str, int], float], socket.socket]


def is_readonly_frame(frame: str) -> bool:
    """Return True if and only if frame is a safe read-only OpenWebNet query."""
    return READONLY_FRAME_RE.fullmatch(frame.strip()) is not None


def load_probe_steps(
    suite_path: Path, *, skip_writes: bool = False
) -> tuple[cases.Suite, list[cases.Step]]:
    """Load a suite and enforce the read-only frame gate before opening any socket."""
    suite = cases.load(suite_path)
    safe_steps: list[cases.Step] = []
    rejected: list[str] = []

    for step in suite.steps:
        if step.direction == "down" and is_readonly_frame(step.input):
            safe_steps.append(step)
        elif not skip_writes:
            rejected.append(f"{step.direction} {step.input}")

    if rejected:
        bad_list = ", ".join(rejected[:5])
        raise ValueError(
            f"{suite_path.name}: refused non-read-only step(s): {bad_list} "
            "(pass --skip-writes to filter them out)"
        )
    if not safe_steps:
        raise ValueError(f"{suite_path.name}: no read-only steps to probe")
    return suite, safe_steps


def compute_open_hash(password: str, nonce: str) -> str:
    """Compute the standard BTicino OpenWebNet numeric challenge-response hash."""
    if not password.isdigit() or not nonce.isdigit():
        raise ValueError("OpenWebNet password and nonce must be numeric strings")

    m_mask = 0xFFFFFFFF
    num1 = int(password) & m_mask
    flag = True

    for ch in nonce:
        if ch != "0":
            if flag:
                num2 = num1
                flag = False
            else:
                num2 = num1 & m_mask
        if ch == "1":
            num1 = ((num2 & 0xFFFFFF80) >> 7) | ((num2 << 25) & m_mask)
        elif ch == "2":
            num1 = ((num2 & 0xFFFFFFF0) >> 4) | ((num2 << 28) & m_mask)
        elif ch == "3":
            num1 = ((num2 & 0xFFFFFFF8) >> 3) | ((num2 << 29) & m_mask)
        elif ch == "4":
            num1 = ((num2 << 1) & m_mask) | ((num2 & 0x80000000) >> 31)
        elif ch == "5":
            num1 = ((num2 << 5) & m_mask) | ((num2 & 0xF8000000) >> 27)
        elif ch == "6":
            num1 = ((num2 << 12) & m_mask) | ((num2 & 0xFFF00000) >> 20)
        elif ch == "7":
            num1 = (
                ((num2 & 0x0000FF00) << 16)
                | (num2 & 0x000000FF)
                | ((num2 & 0x00FF0000) >> 16)
                | (num2 & 0xFF000000)
            ) & m_mask
        elif ch == "8":
            num1 = (
                ((num2 & 0x0000FFFF) << 16) | (num2 >> 24) | ((num2 & 0x00FF0000) >> 8)
            ) & m_mask
        elif ch == "9":
            num1 = (~num2) & m_mask
        else:
            num1 = num2 if not flag else num1

    return str(num1 & m_mask)


def _default_socket_factory(
    address: tuple[str, int], timeout_s: float
) -> socket.socket:
    return socket.create_connection(address, timeout=timeout_s)


def _parse_model_reply(frames: list[str]) -> str | None:
    for f in frames:
        m = re.match(r"^\*#13\*\*15\*(\d+)##$", f)
        if m:
            code = m.group(1)
            return MODEL_MAP.get(code, f"MODEL_{code}")
    return None


def _parse_version_reply(frames: list[str]) -> str | None:
    for f in frames:
        m = re.match(r"^\*#13\*\*16\*(\d+)\*(\d+)\*(\d+)##$", f)
        if m:
            return f"{int(m.group(1)):02d}{int(m.group(2)):02d}{int(m.group(3)):02d}"
    return None


class LiveProbeClient:
    """Synchronous read-only OpenWebNet TCP client for physical gateway probing."""

    def __init__(
        self,
        host: str,
        port: int = 20000,
        password: str | None = None,
        timeout_ms: int = 2000,
        socket_factory: SocketFactory = _default_socket_factory,
    ) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.timeout_s = max(0.05, timeout_ms / 1000.0)
        self.socket_factory = socket_factory
        self._buf = ""

    def _recv_until_delimiter(self, sock: socket.socket) -> str | None:
        while "##" not in self._buf:
            try:
                chunk = sock.recv(4096)
            except TimeoutError:
                return None
            if not chunk:
                return None
            self._buf += chunk.decode("ascii", errors="replace")
        idx = self._buf.index("##") + 2
        frame = self._buf[:idx].strip()
        self._buf = self._buf[idx:]
        return frame

    def _handshake(self, sock: socket.socket) -> None:
        self._buf = ""
        greeting = self._recv_until_delimiter(sock)
        if greeting != ACK:
            raise ConnectionError(f"unexpected gateway greeting: {greeting!r}")

        sock.sendall(COMMAND_SESSION.encode("ascii"))
        resp = self._recv_until_delimiter(sock)
        if resp == ACK:
            return

        m = NONCE_RE.match(resp or "")
        if m:
            if not self.password:
                raise PermissionError(
                    "gateway requested OpenWebNet password challenge "
                    "but --password was not provided"
                )
            digest = compute_open_hash(self.password, m.group("nonce"))
            sock.sendall(f"*#{digest}##".encode("ascii"))
            final = self._recv_until_delimiter(sock)
            if final != ACK:
                raise PermissionError(f"gateway rejected password: {final!r}")
            return

        raise ConnectionError(f"handshake failed with response: {resp!r}")

    def query_frame(self, frame: str) -> tuple[str, str, list[str]]:
        """Execute a single read-only frame and return (reply, verdict, own_frames)."""
        if not is_readonly_frame(frame):
            raise ValueError(f"refused non-read-only frame: {frame!r}")

        with self.socket_factory((self.host, self.port), self.timeout_s) as sock:
            self._handshake(sock)
            sock.sendall(frame.encode("ascii"))
            emitted: list[str] = []
            reply = "-"
            while True:
                msg = self._recv_until_delimiter(sock)
                if msg is None:
                    break
                if msg == ACK:
                    reply = "ack"
                    break
                if msg == NACK:
                    reply = "nack"
                    break
                emitted.append(msg)

        verdict = "out" if emitted else "silent"
        return reply, verdict, emitted

    def detect_identity(self) -> tuple[str, str]:
        """Auto-detect gateway product and 6-digit firmware version via WHO=13."""
        product = "UNKNOWN"
        version = "000000"
        try:
            _r1, _v1, f_model = self.query_frame("*#13**15##")
            parsed_m = _parse_model_reply(f_model)
            if parsed_m:
                product = parsed_m
            _r2, _v2, f_ver = self.query_frame("*#13**16##")
            parsed_v = _parse_version_reply(f_ver)
            if parsed_v:
                version = parsed_v
        except OSError:
            pass
        return product, version


def run_probe_tsv(
    client: LiveProbeClient,
    suite_path: Path,
    *,
    skip_writes: bool = False,
    product: str | None = None,
    version: str | None = None,
) -> str:
    """Run all read-only steps of suite_path against client and render an oracle TSV."""
    suite, steps = load_probe_steps(suite_path, skip_writes=skip_writes)
    det_prod, det_ver = (
        (product, version) if (product and version) else client.detect_identity()
    )
    final_prod = product or det_prod
    final_ver = version or det_ver

    header_lines = [
        f"# product={final_prod}",
        f"# version={final_ver}",
        "# harness=live-probe",
        "# adapter=tcp-live",
        f"# suite={suite.name}",
        f"# suite_sha256={suite.sha256}",
        f"# oracle_version={ORACLE_VERSION}",
    ]
    rows: list[str] = []
    for step in steps:
        reply, verdict, emitted = client.query_frame(step.input)
        output_cell = " | ".join(f"own:{f}" for f in emitted) if emitted else "-"
        rows.append(
            "\t".join([step.direction, step.input, reply, verdict, output_cell])
        )

    return (
        "\n".join([*header_lines, "direction\tinput\treply\tverdict\toutput", *rows])
        + "\n"
    )


def main(
    argv: list[str] | None = None,
    socket_factory: SocketFactory = _default_socket_factory,
) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", help="gateway IP or hostname")
    ap.add_argument("--port", type=int, default=20000, help="TCP port (default: 20000)")
    ap.add_argument("--password", help="numeric OpenWebNet password")
    ap.add_argument(
        "--suite",
        type=Path,
        default=ROOT / "oracle" / "canary" / "canary-live.cases",
        help="suite file to probe (default: oracle/canary/canary-live.cases)",
    )
    ap.add_argument(
        "--skip-writes",
        action="store_true",
        help="skip non-read-only steps instead of failing",
    )
    ap.add_argument(
        "--validate-only",
        action="store_true",
        help=(
            "validate that suite contains only read-only steps "
            "and exit without connecting"
        ),
    )
    ap.add_argument("--product", help="override gateway product name")
    ap.add_argument("--version", help="override gateway firmware version")
    ap.add_argument(
        "--timeout-ms",
        type=int,
        default=2000,
        help="socket timeout in milliseconds (default: 2000)",
    )
    ap.add_argument("-o", "--out", type=Path, help="output TSV file (default: stdout)")
    args = ap.parse_args(argv)

    try:
        suite, steps = load_probe_steps(args.suite, skip_writes=args.skip_writes)
    except (ValueError, cases.CaseError) as exc:
        print(f"live_probe: ERROR - {exc}", file=sys.stderr)
        return 1

    if args.validate_only:
        print(f"live_probe: ok ({suite.name}: {len(steps)} read-only steps verified)")
        return 0

    if not args.host:
        ap.error("--host is required unless --validate-only is set")

    client = LiveProbeClient(
        host=args.host,
        port=args.port,
        password=args.password,
        timeout_ms=args.timeout_ms,
        socket_factory=socket_factory,
    )
    tsv_text = run_probe_tsv(
        client,
        args.suite,
        skip_writes=args.skip_writes,
        product=args.product,
        version=args.version,
    )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(tsv_text, encoding="ascii", newline="\n")
    else:
        sys.stdout.write(tsv_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
