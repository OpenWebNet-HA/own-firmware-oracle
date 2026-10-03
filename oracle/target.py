"""target -- load oracle/targets/<product>/<version>.yaml and pin it to the manifest.

A target spec names the firmware programs to run. Each program is a path
inside the staged sysroot plus its SHA-256, and must match a row of the
committed results/<product>/<version>/manifest.tsv, so a record can never
claim a binary the manifest does not hold. `sysroot` lists manifest path
prefixes overlaid in order (later layers win), e.g. rootfs then app zip.

`boundary` is filled from discovery (oracle/discover.py). Until its status is
'discovered', the spec loads but cannot run cases (`require_ready`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
SHA256 = re.compile(r"[0-9a-f]{64}")
REL_PATH = re.compile(r"[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*")
BUS_CUTS = {"pty", "socket"}   # B1 hardware cut, B2 socket cut
OWN_CUTS = {"full", "unit"}    # O1 full stack, O2 one translator


class TargetError(ValueError):
    pass


@dataclass(frozen=True)
class Program:
    name: str
    path: str     # inside the sysroot, no leading slash
    sha256: str
    layer: str    # the manifest prefix it resolved in


@dataclass(frozen=True)
class TargetSpec:
    product: str
    version: str
    sysroot: tuple[str, ...]
    programs: dict[str, Program]
    boundary: dict

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
    if not REL_PATH.fullmatch(path) or any(part in {".", ".."} for part in path.split("/")):
        raise TargetError(f"program path must be a plain relative path: {path!r}")
    return path


def read_manifest(path: Path) -> dict[str, str]:
    """manifest path -> sha256 (rows only; header and column line skipped)."""
    rows: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(("#", "path\t")):
            continue
        cols = line.split("\t")
        if len(cols) == 4:
            rows[cols[0]] = cols[3]
    return rows


def resolve(path: str, layers: tuple[str, ...], manifest: dict[str, str]) -> tuple[str, str]:
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


def _check_boundary(boundary: object) -> dict:
    if not isinstance(boundary, dict):
        raise TargetError("boundary must be a mapping")
    status = boundary.get("status")
    if status == "pending":
        return boundary
    if status != "discovered":
        raise TargetError(f"boundary.status must be pending or discovered, got {status!r}")
    if boundary.get("bus") not in BUS_CUTS:
        raise TargetError(f"boundary.bus must be one of {sorted(BUS_CUTS)}")
    if boundary.get("own") not in OWN_CUTS:
        raise TargetError(f"boundary.own must be one of {sorted(OWN_CUTS)}")
    return boundary


def load(path: Path, results_root: Path) -> TargetSpec:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TargetError(f"{path}: not a mapping")
    product, version = str(data.get("product", "")), str(data.get("version", ""))
    for label, value in (("product", product), ("version", version)):
        if not NAME.fullmatch(value):
            raise TargetError(f"{path}: {label} {value!r} is not a plain name")
    if (path.parent.name, path.stem) != (product, version):
        raise TargetError(f"{path}: must live at oracle/targets/{product}/{version}.yaml")

    layers = data.get("sysroot")
    if not isinstance(layers, list) or not layers or not all(isinstance(x, str) for x in layers):
        raise TargetError(f"{path}: sysroot must be a non-empty list of manifest prefixes")

    manifest_path = results_root / product / version / "manifest.tsv"
    if not manifest_path.exists():
        raise TargetError(f"{path}: no manifest at {manifest_path}; run phase 1 first")
    manifest = read_manifest(manifest_path)

    raw_programs = data.get("programs")
    if not isinstance(raw_programs, dict) or not raw_programs:
        raise TargetError(f"{path}: programs must be a non-empty mapping")
    programs: dict[str, Program] = {}
    for name, spec in raw_programs.items():
        if not NAME.fullmatch(str(name)) or not isinstance(spec, dict):
            raise TargetError(f"{path}: bad program entry {name!r}")
        rel = check_rel_path(str(spec.get("path", "")))
        want = str(spec.get("sha256", ""))
        if not SHA256.fullmatch(want):
            raise TargetError(f"{path}: {name}: sha256 must be 64 lower-case hex digits")
        layer, have = resolve(rel, tuple(layers), manifest)
        if have != want:
            raise TargetError(f"{path}: {name}: sha256 {want} != manifest {have}")
        programs[str(name)] = Program(str(name), rel, want, layer)

    return TargetSpec(
        product=product,
        version=version,
        sysroot=tuple(layers),
        programs=programs,
        boundary=_check_boundary(data.get("boundary", {"status": "pending"})),
    )
