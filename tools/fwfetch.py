#!/usr/bin/env python3
"""fwfetch -- materialize a firmware image locally, verified by SHA-256.

Public artifact: this script and the catalog. The firmware image is NEVER
committed; it is fetched to a cache outside the repo tree.

Sources are tried in catalog order:
  vendor:  plain HTTPS download (public BTicino / Legrand files)
  r2:      S3-compatible object, key = firmware/sha256/<hash>.<ext>
           (needs R2_ENDPOINT / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY)
  --from-file: a local copy from your own device (no upload required)

Every fetched blob is checked against the catalog size and sha256; any
mismatch is a hard failure. Output: the cache path on stdout.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

import yaml

CACHE = Path(os.environ.get("OWN_FW_CACHE", Path.home() / ".cache" / "own-fw"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_path(sha256: str, ext: str) -> Path:
    return CACHE / "sha256" / f"{sha256}.{ext.lstrip('.')}"


def _verify(path: Path, size: int, sha256: str) -> None:
    actual_size = path.stat().st_size
    if actual_size != size:
        raise SystemExit(f"size mismatch: got {actual_size}, want {size}")
    actual = sha256_file(path)
    if actual != sha256:
        raise SystemExit(f"sha256 mismatch:\n  got  {actual}\n  want {sha256}")


def _fetch_vendor(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "own-firmware-oracle/1"})
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)


def _fetch_r2(key: str, dest: Path) -> None:
    import boto3  # imported lazily so the vendor path needs no deps

    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    )
    # The bucket is configured out of band (R2_BUCKET); the catalog's r2 source
    # is the full object key within it, e.g. "firmware/sha256/<hash>.zip".
    # Do NOT derive the bucket from the key -- "firmware" there is a prefix.
    s3.download_file(os.environ["R2_BUCKET"], key, str(dest))


def fetch(entry: dict, from_file: str | None) -> Path:
    w = entry["wrapper"]
    sha256, size = w["sha256"], w["size"]
    ext = Path(w["filename"]).suffix or ".bin"
    dest = cache_path(sha256, ext)
    if dest.exists() and dest.stat().st_size == size and sha256_file(dest) == sha256:
        return dest  # already cached and valid

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    if from_file:
        src = Path(from_file)
        _verify(src, size, sha256)  # verify in place; do not copy a bad file
        tmp.write_bytes(src.read_bytes())
    else:
        last_err: Exception | None = None
        for source in w.get("sources", []):
            kind, loc = next(iter(source.items()))
            try:
                if kind == "vendor":
                    _fetch_vendor(loc, tmp)
                elif kind == "r2":
                    _fetch_r2(loc, tmp)
                else:
                    continue
                _verify(tmp, size, sha256)
                break
            except Exception as err:  # noqa: BLE001 -- try the next source
                last_err = err
                tmp.unlink(missing_ok=True)
                print(f"  source {kind} failed: {err}", file=sys.stderr)
        else:
            raise SystemExit(f"no source produced a valid image: {last_err}")

    tmp.replace(dest)
    return dest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("catalog", help="path to catalog/<product>/<version>.yaml")
    ap.add_argument("--from-file", help="use a local image instead of fetching")
    args = ap.parse_args()

    entry = yaml.safe_load(Path(args.catalog).read_text())
    path = fetch(entry, args.from_file)
    print(path)


if __name__ == "__main__":
    main()
