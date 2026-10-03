"""target -- load oracle/targets/<product>/<version>.yaml and pin it to the manifest.

A target spec names the firmware programs to run. Each program is a path
inside the staged sysroot plus its SHA-256, and must match a row of the
committed results/<product>/<version>/manifest.tsv, so a record can never
claim a binary the manifest does not hold. `sysroot` lists manifest path
prefixes overlaid in order (later layers win), e.g. rootfs then app zip.

The emulator CPU is not configured: it follows from the programs' ELF tags in
the manifest (all programs of a target must agree). `runtime` describes what
the device's own boot sets up before the programs start -- working directory,
environment, empty mounts, device nodes -- each value taken from the image's
own configuration and cited in the spec.

`boundary` is filled from discovery (oracle/discover.py). Until its status is
'discovered', the spec loads but cannot run cases (`require_ready`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from oracle.sandbox import ENV_NAME, SandboxError, guest_path

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
SHA256 = re.compile(r"[0-9a-f]{64}")
REL_PATH = re.compile(r"[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*")
BUS_CUTS = {"pty", "socket"}  # B1 hardware cut, B2 socket cut
OWN_CUTS = {"full", "unit"}  # O1 full stack, O2 one translator
DEVICE_KINDS = {"pty"}  # what a device node in runtime.devices can be
# ELF/<cpu>/<kind>/<bits><order>[/abi] -> qemu-user / binfmt_misc CPU name
QEMU_ARCH = {
    ("ARM", "32le"): "arm",
    ("ARM", "32be"): "armeb",
    ("AArch64", "64le"): "aarch64",
    ("MIPS", "32be"): "mips",
    ("MIPS", "32le"): "mipsel",
    ("PowerPC", "32be"): "ppc",
    ("x86", "32le"): "i386",
    ("x86-64", "64le"): "x86_64",
}


class TargetError(ValueError):
    pass


@dataclass(frozen=True)
class Program:
    name: str
    path: str  # inside the sysroot, no leading slash
    sha256: str
    layer: str  # the manifest prefix it resolved in
    args: tuple[str, ...] = ()  # argv[1:], when the program wants any


@dataclass(frozen=True)
class Runtime:
    cwd: str = "/"
    env: dict[str, str] = field(default_factory=dict)
    tmpfs: tuple[str, ...] = ()
    dirs: tuple[str, ...] = ()
    devices: dict[str, str] = field(default_factory=dict)  # guest path -> kind


@dataclass(frozen=True)
class TargetSpec:
    product: str
    version: str
    sysroot: tuple[str, ...]
    programs: dict[str, Program]
    boundary: dict[str, str]
    image_sha256: str = ""  # from the manifest header
    arch: str = ""  # qemu CPU name, from the programs' ELF tags
    runtime: Runtime = field(default_factory=Runtime)

    @property
    def ready(self) -> bool:
        return self.boundary.get("status") == "discovered"

    def require_ready(self) -> None:
        if not self.ready:
            raise TargetError(
                f"{self.product}/{self.version}: boundary not discovered yet; "
                "run discovery and fill the boundary block first"
            )


def check_rel_path(path: str) -> str:
    if not REL_PATH.fullmatch(path) or any(
        part in {".", ".."} for part in path.split("/")
    ):
        raise TargetError(f"program path must be a plain relative path: {path!r}")
    return path


# tools/unpack.py writes manifest paths with backslash and control characters
# escaped as \xNN (its TSV_UNSAFE). A key built from a raw member name is
# escaped the same way before lookup; a test pins the two together.
MANIFEST_UNSAFE = re.compile(r"[\\\x00-\x1f\x7f]")


def manifest_key(path: str) -> str:
    return MANIFEST_UNSAFE.sub(lambda m: f"\\x{ord(m.group()):02x}", path)


def manifest_image(path: Path) -> str:
    """image_sha256 from a manifest header."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("#"):
            break
        key, _, value = line[2:].partition("=")
        if key == "image_sha256" and SHA256.fullmatch(value):
            return value
    raise TargetError(f"{path}: no image_sha256 in the manifest header")


def read_rows(path: Path) -> dict[str, tuple[str, str]]:
    """manifest path -> (type, sha256); header and column line skipped."""
    rows: dict[str, tuple[str, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(("#", "path\t")):
            continue
        cols = line.split("\t")
        if len(cols) == 4:
            rows[cols[0]] = (cols[1], cols[3])
    return rows


def read_manifest(path: Path) -> dict[str, str]:
    """manifest path -> sha256."""
    return {k: sha for k, (_, sha) in read_rows(path).items()}


def qemu_arch(elf_tag: str) -> str:
    parts = elf_tag.split("/")
    if len(parts) < 4 or parts[0] != "ELF" or (parts[1], parts[3]) not in QEMU_ARCH:
        raise TargetError(f"no emulator for manifest type {elf_tag!r}")
    return QEMU_ARCH[(parts[1], parts[3])]


def resolve(
    path: str, layers: tuple[str, ...], manifest: dict[str, str]
) -> tuple[str, str]:
    """(layer, sha256) of the LAST layer holding path -- overlay order.
    Filesystem layers root their paths at '/', zip layers do not."""
    found: tuple[str, str] | None = None
    for layer in layers:
        for key in (layer + path, layer + "/" + path):
            if key in manifest:
                found = (layer, manifest[key])
    if found is None:
        raise TargetError(f"{path}: not in any sysroot layer of the manifest")
    return found


def _check_boundary(boundary: object) -> dict[str, str]:
    if not isinstance(boundary, dict):
        raise TargetError("boundary must be a mapping")
    status = boundary.get("status")
    if status == "pending":
        return boundary
    if status != "discovered":
        raise TargetError(
            f"boundary.status must be pending or discovered, got {status!r}"
        )
    if boundary.get("bus") not in BUS_CUTS:
        raise TargetError(f"boundary.bus must be one of {sorted(BUS_CUTS)}")
    if boundary.get("own") not in OWN_CUTS:
        raise TargetError(f"boundary.own must be one of {sorted(OWN_CUTS)}")
    return boundary


def _str_list(raw: object, label: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise TargetError(f"{label} must be a list of strings")
    return tuple(raw)


def _runtime(raw: object) -> Runtime:
    if not isinstance(raw, dict):
        raise TargetError("runtime must be a mapping")
    unknown = set(raw) - {"cwd", "env", "tmpfs", "dirs", "devices"}
    if unknown:
        raise TargetError(f"runtime: unknown keys {sorted(unknown)}")
    env = raw.get("env", {})
    devices = raw.get("devices", {})
    if not isinstance(env, dict) or not isinstance(devices, dict):
        raise TargetError("runtime.env and runtime.devices must be mappings")
    try:
        cwd = str(raw.get("cwd", "/"))
        if cwd != "/":
            guest_path(cwd)
        tmpfs = tuple(guest_path(p) for p in _str_list(raw.get("tmpfs", []), "tmpfs"))
        dirs = tuple(guest_path(p) for p in _str_list(raw.get("dirs", []), "dirs"))
        for path, kind in devices.items():
            guest_path(str(path))
            if kind not in DEVICE_KINDS:
                raise TargetError(f"runtime.devices[{path}]: kind {kind!r}")
    except SandboxError as exc:
        raise TargetError(f"runtime: {exc}") from None
    for key, value in env.items():
        if not ENV_NAME.fullmatch(str(key)) or not isinstance(value, str):
            raise TargetError(f"runtime.env: bad entry {key!r}")
        if str(key).startswith("QEMU_"):
            raise TargetError(f"runtime.env: {key} is the emulator's, not the guest's")
    return Runtime(
        cwd=cwd,
        env={str(k): v for k, v in env.items()},
        tmpfs=tmpfs,
        dirs=dirs,
        devices={str(k): str(v) for k, v in devices.items()},
    )


def _layers(raw: object, path: Path) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw or not all(isinstance(x, str) for x in raw):
        raise TargetError(
            f"{path}: sysroot must be a non-empty list of manifest prefixes"
        )
    return tuple(raw)


def _program(
    path: Path,
    name: object,
    spec: object,
    layers: tuple[str, ...],
    rows: dict[str, tuple[str, str]],
) -> tuple[Program, str]:
    """(program, its qemu arch)."""
    if not NAME.fullmatch(str(name)) or not isinstance(spec, dict):
        raise TargetError(f"{path}: bad program entry {name!r}")
    rel = check_rel_path(str(spec.get("path", "")))
    want = str(spec.get("sha256", ""))
    if not SHA256.fullmatch(want):
        raise TargetError(f"{path}: {name}: sha256 must be 64 lower-case hex digits")
    shas = {k: sha for k, (_, sha) in rows.items()}
    layer, have = resolve(rel, layers, shas)
    if have != want:
        raise TargetError(f"{path}: {name}: sha256 {want} != manifest {have}")
    key = layer + rel if layer + rel in rows else layer + "/" + rel
    args = spec.get("args", [])
    if not isinstance(args, list) or not all(
        isinstance(a, str) and a and "\0" not in a for a in args
    ):
        raise TargetError(f"{path}: {name}: args must be a list of strings")
    program = Program(str(name), rel, want, layer, tuple(args))
    return program, qemu_arch(rows[key][0])


def load(path: Path, results_root: Path) -> TargetSpec:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TargetError(f"{path}: not a mapping")
    product, version = str(data.get("product", "")), str(data.get("version", ""))
    for label, value in (("product", product), ("version", version)):
        if not NAME.fullmatch(value):
            raise TargetError(f"{path}: {label} {value!r} is not a plain name")
    if (path.parent.name, path.stem) != (product, version):
        raise TargetError(
            f"{path}: must live at oracle/targets/{product}/{version}.yaml"
        )
    layers = _layers(data.get("sysroot"), path)

    manifest_path = results_root / product / version / "manifest.tsv"
    if not manifest_path.exists():
        raise TargetError(f"{path}: no manifest at {manifest_path}; run phase 1 first")
    rows = read_rows(manifest_path)

    raw_programs = data.get("programs")
    if not isinstance(raw_programs, dict) or not raw_programs:
        raise TargetError(f"{path}: programs must be a non-empty mapping")
    programs: dict[str, Program] = {}
    arches: set[str] = set()
    for name, spec in raw_programs.items():
        program, arch = _program(path, name, spec, layers, rows)
        programs[program.name] = program
        arches.add(arch)
    if len(arches) != 1:
        raise TargetError(f"{path}: programs need different emulators {sorted(arches)}")

    return TargetSpec(
        product=product,
        version=version,
        sysroot=layers,
        programs=programs,
        boundary=_check_boundary(data.get("boundary", {"status": "pending"})),
        image_sha256=manifest_image(manifest_path),
        arch=arches.pop(),
        runtime=_runtime(data.get("runtime", {})),
    )
