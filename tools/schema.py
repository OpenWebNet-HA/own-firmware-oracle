"""schema -- load and validate one catalog/<product>/<version>.yaml entry.

Every tool reads a catalog file through `load()`. Catalog values end up in file
paths, shell variables and workflow matrices, so they are checked against a
fixed schema here instead of being trusted as free text:

  * product / version / filenames: plain names ([A-Za-z0-9._-], no slashes);
  * the file must live at catalog/<product>/<version>.yaml;
  * sha256 values are 64 lower-case hex digits, sizes are positive integers;
  * vendor sources are https:// on an allow-listed vendor host;
  * r2 sources are plain object keys (no "..", no control characters).
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
R2_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")

# Hosts the vendor firmware is actually published on (BTicino / Legrand).
VENDOR_HOSTS = {
    "www.bticino.be",
    "assets.legrand.com",
    "homesystems-legrandgroup.com",
    "www.homesystems-legrandgroup.com",
}


def check_vendor_url(url: str) -> None:
    """Raise ValueError unless `url` is https:// on an allow-listed vendor host."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise ValueError(f"vendor URL must be https://, got {url!r}")
    if parts.hostname not in VENDOR_HOSTS:
        raise ValueError(f"vendor host {parts.hostname!r} is not allow-listed")


def _name(value: object, field: str) -> str:
    if not isinstance(value, str) or not NAME.match(value):
        raise ValueError(f"{field} must match {NAME.pattern}, got {value!r}")
    return value


def _blob(section: object, field: str) -> None:
    """Validate a {filename, size, sha256} block."""
    if not isinstance(section, dict):
        raise TypeError(f"{field} must be a mapping")
    _name(section.get("filename"), f"{field}.filename")
    size = section.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError(f"{field}.size must be a positive integer, got {size!r}")
    sha = section.get("sha256")
    if not isinstance(sha, str) or not SHA256.match(sha):
        raise ValueError(f"{field}.sha256 must be 64 lower-case hex digits")


def _sources(sources: object) -> None:
    if not isinstance(sources, list):
        raise TypeError("wrapper.sources must be a list")
    for source in sources:
        if not isinstance(source, dict) or len(source) != 1:
            raise ValueError(f"each source must be a one-key mapping, got {source!r}")
        kind, loc = next(iter(source.items()))
        if not isinstance(loc, str):
            raise TypeError(f"source {kind!r} must be a string")
        if kind == "vendor":
            check_vendor_url(loc)
        elif kind == "r2":
            if not R2_KEY.match(loc) or ".." in loc:
                raise ValueError(f"r2 key {loc!r} is not a plain object key")
        else:
            raise ValueError(f"unknown source kind {kind!r}")


def validate(entry: object, path: Path) -> dict:
    """Return `entry` if it satisfies the schema, else raise ValueError or TypeError."""
    if not isinstance(entry, dict):
        raise TypeError("catalog entry must be a mapping")
    product = _name(entry.get("product"), "product")
    version = _name(entry.get("version"), "version")
    if (path.parent.parent.name, path.parent.name, path.name) != (
        "catalog", product, f"{version}.yaml"
    ):
        raise ValueError(f"must live at catalog/{product}/{version}.yaml")
    _blob(entry.get("wrapper"), "wrapper")
    _blob(entry.get("image"), "image")
    _sources(entry["wrapper"].get("sources", []))
    scheme = entry.get("password_scheme") or {}
    cands = scheme.get("candidates", []) if isinstance(scheme, dict) else None
    if not isinstance(cands, list) or not all(isinstance(c, str) for c in cands):
        raise ValueError("password_scheme.candidates must be a list of strings")
    return entry


def load(path: str | Path) -> dict:
    """Load and validate a catalog file; exit with a clear message if invalid."""
    path = Path(path)
    try:
        return validate(yaml.safe_load(path.read_text()), path)
    except (ValueError, TypeError) as err:
        raise SystemExit(f"{path}: invalid catalog entry: {err}") from None
