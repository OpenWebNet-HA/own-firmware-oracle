"""schema -- load and validate one catalog/<product>/<version>.yaml entry.

Every tool reads a catalog file through `load()`. Catalog values end up in file
paths, shell variables and workflow matrices, so they are checked against a
fixed schema here instead of being trusted as free text:

  * product / version / filenames: plain names ([A-Za-z0-9._-], no slashes);
  * the file must live at catalog/<product>/<version>.yaml;
  * sha256 values are 64 lower-case hex digits, sizes are positive integers;
  * vendor sources are https:// on an allow-listed vendor host;
  * r2 sources are plain object keys (no "..", no control characters);
  * optional limits.max_expand_mib raises unpack's bomb guard (1..8192 MiB);
  * optional undecoded_ok: [{path, reason}] acknowledges layers unpack.py's
    coverage gate would otherwise refuse;
  * optional status: unpackable (default) | blocked, and blocked needs a
    blocked_reason. A blocked entry is catalogued (fetch + hash) but kept out
    of the CI matrices.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
R2_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")

# A catalog entry after validate(): the checked fields have the shapes above;
# the rest (flash map, notes) is free-form YAML.
CatalogEntry = dict[str, Any]

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


def validate(entry: object, path: Path) -> CatalogEntry:
    """Return `entry` if it satisfies the schema, else raise ValueError or TypeError."""
    if not isinstance(entry, dict):
        raise TypeError("catalog entry must be a mapping")
    product = _name(entry.get("product"), "product")
    version = _name(entry.get("version"), "version")
    if (path.parent.parent.name, path.parent.name, path.name) != (
        "catalog",
        product,
        f"{version}.yaml",
    ):
        raise ValueError(f"must live at catalog/{product}/{version}.yaml")
    _blob(entry.get("wrapper"), "wrapper")
    _blob(entry.get("image"), "image")
    _sources(entry["wrapper"].get("sources", []))
    scheme = entry.get("password_scheme") or {}
    cands = scheme.get("candidates", []) if isinstance(scheme, dict) else None
    if not isinstance(cands, list) or not all(isinstance(c, str) for c in cands):
        raise ValueError("password_scheme.candidates must be a list of strings")
    _limits(entry.get("limits", {}))
    _undecoded_ok(entry.get("undecoded_ok", []))
    _status(entry)
    return entry


# unpackable: every layer opens; plan.py builds and re-checks the manifest.
# blocked:    fetch + hash are verified, but a layer cannot be opened yet (for
#             example its packaging password is not a known vendor string).
#             plan.py leaves it out of both CI matrices until it is unblocked.
STATUSES = ("unpackable", "blocked")


def _status(entry: CatalogEntry) -> None:
    status = entry.get("status", "unpackable")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {list(STATUSES)}, got {status!r}")
    reason = entry.get("blocked_reason")
    if status == "blocked":
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("status: blocked needs a blocked_reason")
    elif reason is not None:
        raise ValueError("blocked_reason is only allowed with status: blocked")


def is_blocked(entry: CatalogEntry) -> bool:
    """True if the entry is catalogued but cannot be unpacked yet."""
    return bool(entry.get("status", "unpackable") == "blocked")


MAX_EXPAND_MIB_CEILING = 8192  # 8 GiB: beyond this a runner runs out of memory anyway


def _limits(limits: object) -> None:
    """Optional per-image unpack bounds: the bomb guard can be raised for a big
    image, never switched off."""
    if not isinstance(limits, dict):
        raise TypeError("limits must be a mapping")
    unknown = set(limits) - {"max_expand_mib"}
    if unknown:
        raise ValueError(f"unknown limits keys: {sorted(unknown)}")
    mib = limits.get("max_expand_mib")
    if mib is not None and (
        isinstance(mib, bool)
        or not isinstance(mib, int)
        or not 1 <= mib <= MAX_EXPAND_MIB_CEILING
    ):
        raise ValueError(
            f"limits.max_expand_mib must be 1..{MAX_EXPAND_MIB_CEILING}, got {mib!r}"
        )


def _undecoded_ok(acks: object) -> None:
    """Manifest paths the coverage gate may let through, each with a reason."""
    if not isinstance(acks, list):
        raise TypeError("undecoded_ok must be a list")
    seen: set[str] = set()
    for ack in acks:
        if not isinstance(ack, dict) or set(ack) != {"path", "reason"}:
            raise ValueError(f"undecoded_ok entries are {{path, reason}}, got {ack!r}")
        path, reason = ack["path"], ack["reason"]
        if not isinstance(path, str) or not path or any(ord(c) < 0x20 for c in path):
            raise ValueError(
                f"undecoded_ok path must be a plain manifest path, got {path!r}"
            )
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"undecoded_ok {path!r} needs a reason")
        if path in seen:
            raise ValueError(f"undecoded_ok lists {path!r} twice")
        seen.add(path)


def load(path: str | Path) -> CatalogEntry:
    """Load and validate a catalog file; exit with a clear message if invalid."""
    path = Path(path)
    try:
        return validate(yaml.safe_load(path.read_text()), path)
    except (ValueError, TypeError) as err:
        raise SystemExit(f"{path}: invalid catalog entry: {err}") from None
