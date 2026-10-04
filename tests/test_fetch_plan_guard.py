"""fwfetch / plan / guard paths that need no network and no vendor image.

Every source is faked: the vendor opener, boto3, the cache dir. What is real is
the logic around them -- verification, fall-through, cache reuse, matrices.
"""

import hashlib
import io
import subprocess
import sys
import types
import urllib.request
from http.client import HTTPMessage
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import fwfetch
import guard
import plan
import schema
import unpack

BLOB = b"synthetic firmware wrapper"
VENDOR = "https://www.bticino.be/fw/FW.zip"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _entry(sources: list[dict[str, str]]) -> dict:
    return {
        "wrapper": {
            "filename": "FW.zip",
            "size": len(BLOB),
            "sha256": _sha(BLOB),
            "sources": sources,
        }
    }


def _catalog(
    root: Path, sources: str = f"    - vendor: '{VENDOR}'\n", extra: str = ""
) -> Path:
    cat = root / "catalog" / "MH200N" / "010108.yaml"
    cat.parent.mkdir(parents=True, exist_ok=True)
    cat.write_text(
        "product: MH200N\nversion: '010108'\n"
        f"wrapper:\n  filename: FW.zip\n  size: {len(BLOB)}\n"
        f"  sha256: '{_sha(BLOB)}'\n  sources:\n{sources}"
        f"image:\n  filename: fw.fwz\n  size: 1\n  sha256: '{'a' * 64}'\n"
        f"{extra}"
    )
    return cat


def _write_manifest(root: Path, tool_version: str = unpack.TOOL_VERSION) -> None:
    man = root / "results" / "MH200N" / "010108" / "manifest.tsv"
    man.parent.mkdir(parents=True, exist_ok=True)
    man.write_text(f"# image_sha256={'a' * 64}\n# tool_version={tool_version}\n")


@pytest.fixture
def cache(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    monkeypatch.setattr(fwfetch, "CACHE", root)
    return root


def _cached_files(cache: Path) -> list[Path]:
    return [p for p in cache.rglob("*") if p.is_file()]


# --- fwfetch: every byte is verified -----------------------------------------


def test_from_file_is_verified_then_cached(tmp_path, cache):
    src = tmp_path / "FW.zip"
    src.write_bytes(BLOB)
    out = fwfetch.fetch(_entry([]), str(src))
    assert out == fwfetch.cache_path(_sha(BLOB), ".zip")
    assert out.read_bytes() == BLOB


@pytest.mark.parametrize(
    ("data", "reason"),
    [(BLOB + b"x", "size mismatch"), (BLOB[::-1], "sha256 mismatch")],
)
def test_from_file_refuses_a_wrong_file_and_caches_nothing(
    tmp_path, cache, data, reason
):
    src = tmp_path / "FW.zip"
    src.write_bytes(data)
    with pytest.raises(SystemExit, match=reason):
        fwfetch.fetch(_entry([]), str(src))
    assert _cached_files(cache) == []


def test_a_valid_cache_entry_is_reused_without_fetching(cache, monkeypatch):
    dest = fwfetch.cache_path(_sha(BLOB), ".zip")
    dest.parent.mkdir(parents=True)
    dest.write_bytes(BLOB)
    monkeypatch.setattr(
        fwfetch, "_fetch_vendor", lambda *_: pytest.fail("fetched despite cache")
    )
    assert fwfetch.fetch(_entry([{"vendor": VENDOR}]), None) == dest


def test_a_bad_source_falls_through_to_the_next(cache, monkeypatch, capsys):
    monkeypatch.setattr(
        fwfetch,
        "_fetch_vendor",
        lambda _url, dest, _limit: dest.write_bytes(b"tampered"),
    )
    monkeypatch.setattr(fwfetch, "_fetch_r2", lambda _key, dest: dest.write_bytes(BLOB))
    sources = [{"vendor": VENDOR}, {"r2": "firmware/sha256/x.zip"}]
    out = fwfetch.fetch(_entry(sources), None)
    assert out.read_bytes() == BLOB
    assert "source vendor failed: size mismatch" in capsys.readouterr().err
    assert _cached_files(cache) == [out]  # the rejected .part is gone


def test_no_valid_source_is_fatal(cache, monkeypatch):
    def offline(_url: str, _dest: Path, _limit: int) -> None:
        raise OSError("network unreachable")

    monkeypatch.setattr(fwfetch, "_fetch_vendor", offline)
    sources = [{"mirror": "ignored"}, {"vendor": VENDOR}]
    with pytest.raises(SystemExit, match="no source produced a valid image"):
        fwfetch.fetch(_entry(sources), None)
    assert _cached_files(cache) == []


def test_fetch_vendor_streams_through_the_allowlisting_opener(tmp_path, monkeypatch):
    seen: dict = {}

    class Opener:
        def open(self, req, timeout):
            seen.update(url=req.full_url, ua=req.get_header("User-agent"))
            seen["timeout"] = timeout
            return io.BytesIO(BLOB)

    def build_opener(*handlers):
        seen["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(fwfetch.urllib.request, "build_opener", build_opener)
    dest = tmp_path / "out"
    fwfetch._fetch_vendor(VENDOR, dest)
    assert dest.read_bytes() == BLOB
    assert seen["handlers"] == (fwfetch._VendorRedirects,)
    assert seen["url"] == VENDOR
    assert seen["ua"].startswith("own-firmware-oracle/")


def test_fetch_vendor_refuses_a_non_vendor_url(tmp_path):
    with pytest.raises(ValueError, match="not allow-listed"):
        fwfetch._fetch_vendor("https://evil.example/fw.zip", tmp_path / "out")
    assert not (tmp_path / "out").exists()


class _Chunked(io.BytesIO):
    """A response body with no Content-Length, read in small chunks."""

    def read(self, _size: int | None = -1) -> bytes:
        return super().read(4)


def _chunked_opener(monkeypatch, body: bytes) -> None:
    class Opener:
        def open(self, _req, timeout):
            return _Chunked(body)

    monkeypatch.setattr(
        fwfetch.urllib.request, "build_opener", lambda *_handlers: Opener()
    )


def test_fetch_vendor_stops_a_body_longer_than_the_catalog_size(tmp_path, monkeypatch):
    # Legrand checkout links send no Content-Length: the catalog size is the
    # only bound, and it must stop the download, not just fail the hash after.
    _chunked_opener(monkeypatch, BLOB * 1000)
    dest = tmp_path / "out"
    with pytest.raises(fwfetch.Mismatch, match=f"more than {len(BLOB)} bytes"):
        fwfetch._fetch_vendor(VENDOR, dest, len(BLOB))
    assert dest.stat().st_size <= len(BLOB)


def test_fetch_vendor_accepts_a_chunked_body_of_exactly_the_catalog_size(
    tmp_path, monkeypatch
):
    _chunked_opener(monkeypatch, BLOB)
    dest = tmp_path / "out"
    fwfetch._fetch_vendor(VENDOR, dest, len(BLOB))
    assert dest.read_bytes() == BLOB


def test_fetch_passes_the_catalog_size_as_the_download_cap(cache, monkeypatch):
    seen: list[int] = []

    def fake(_url: str, dest: Path, limit: int) -> None:
        seen.append(limit)
        dest.write_bytes(BLOB)

    monkeypatch.setattr(fwfetch, "_fetch_vendor", fake)
    fwfetch.fetch(_entry([{"vendor": VENDOR}]), None)
    assert seen == [len(BLOB)]


def test_a_redirect_to_another_vendor_url_is_followed():
    req = urllib.request.Request(VENDOR)
    new = fwfetch._VendorRedirects().redirect_request(
        req, io.BytesIO(), 302, "Found", HTTPMessage(), VENDOR + "?mirror=2"
    )
    assert new is not None
    assert new.full_url == VENDOR + "?mirror=2"


def test_fetch_r2_reads_the_bucket_from_the_environment(tmp_path, monkeypatch):
    calls: dict = {}

    class S3:
        def download_file(self, bucket, key, dest):
            calls.update(bucket=bucket, key=key)
            Path(dest).write_bytes(BLOB)

    def client(service, **kwargs):
        calls.update(service=service, **kwargs)
        return S3()

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=client))
    monkeypatch.setenv("R2_ENDPOINT", "https://r2.example")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "id")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_BUCKET", "own-fw")
    fwfetch._fetch_r2("firmware/sha256/x.zip", tmp_path / "out")
    assert (tmp_path / "out").read_bytes() == BLOB
    # "firmware" is a key prefix, never the bucket
    assert calls["bucket"] == "own-fw"
    assert calls["key"] == "firmware/sha256/x.zip"
    assert calls["service"] == "s3"
    assert calls["endpoint_url"] == "https://r2.example"


def test_fwfetch_main_prints_the_cache_path(tmp_path, cache, monkeypatch, capsys):
    cat = _catalog(tmp_path)
    src = tmp_path / "local-copy.zip"
    src.write_bytes(BLOB)
    monkeypatch.setattr(sys, "argv", ["fwfetch", str(cat), "--from-file", str(src)])
    fwfetch.main()
    printed = Path(capsys.readouterr().out.strip())
    assert printed.read_bytes() == BLOB
    assert printed.is_relative_to(cache)


# --- plan: what gets rebuilt, what gets re-checked ---------------------------


def test_reproducible_means_fresh_and_publicly_fetchable(tmp_path):
    results = tmp_path / "results"
    cat = _catalog(tmp_path)
    assert plan.is_reproducible(cat, results) is False  # no manifest: stale
    _write_manifest(tmp_path, tool_version="0")
    assert plan.is_reproducible(cat, results) is False  # old tool: stale
    _write_manifest(tmp_path)
    assert plan.is_reproducible(cat, results) is True

    r2_only = _catalog(tmp_path, sources="    - r2: 'firmware/sha256/x.zip'\n")
    assert plan.is_reproducible(r2_only, results) is False  # needs secrets


def _plan(monkeypatch, capsys, *args: str) -> str:
    monkeypatch.setattr(sys, "argv", ["plan", *args])
    plan.main()
    return capsys.readouterr().out.strip()


def test_plan_matrices_split_stale_from_reproducible(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(plan, "ROOT", tmp_path)
    _catalog(tmp_path)
    entry = '["catalog/MH200N/010108.yaml"]'

    assert _plan(monkeypatch, capsys, "--emit-matrix") == f"matrix={entry}"
    assert _plan(monkeypatch, capsys, "--emit-reproduce-matrix") == "matrix=[]"
    assert _plan(monkeypatch, capsys).startswith("STALE ")

    _write_manifest(tmp_path)
    assert _plan(monkeypatch, capsys, "--emit-matrix") == "matrix=[]"
    assert _plan(monkeypatch, capsys, "--emit-reproduce-matrix") == f"matrix={entry}"
    assert _plan(monkeypatch, capsys).startswith("ok ")


def test_plan_result_path(tmp_path, monkeypatch, capsys):
    cat = _catalog(tmp_path)
    out = _plan(monkeypatch, capsys, str(cat), "--result-path")
    assert out == "MH200N/010108/manifest.tsv"


def test_plan_keeps_a_blocked_entry_out_of_both_matrices(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(plan, "ROOT", tmp_path)
    cat = _catalog(tmp_path, extra="status: blocked\nblocked_reason: 'no password'\n")
    results = tmp_path / "results"
    # No manifest (would be stale) and a manifest (would be reproducible):
    # neither may send a blocked image to a CI job that can only fail on it.
    assert plan.is_stale(cat, results) is False
    assert plan.is_reproducible(cat, results) is False
    _write_manifest(tmp_path)
    assert plan.is_reproducible(cat, results) is False
    assert _plan(monkeypatch, capsys, "--emit-matrix") == "matrix=[]"
    assert _plan(monkeypatch, capsys, "--emit-reproduce-matrix") == "matrix=[]"
    assert _plan(monkeypatch, capsys).startswith("BLOCK ")


# --- schema: every rejection path --------------------------------------------

CAT_PATH = Path("catalog/MH200N/010108.yaml")


def _valid() -> dict:
    blob = {"filename": "FW.zip", "size": 1, "sha256": "a" * 64}
    return {
        "product": "MH200N",
        "version": "010108",
        "wrapper": {**blob, "sources": [{"vendor": VENDOR}]},
        "image": dict(blob, filename="fw.fwz"),
    }


def _with(**changes: object) -> dict:
    entry = _valid()
    for dotted, value in changes.items():
        *parents, key = dotted.split("__")
        node = entry
        for p in parents:
            node = node[p]
        node[key] = value
    return entry


@pytest.mark.parametrize(
    ("entry", "match"),
    [
        (["not", "a", "mapping"], "must be a mapping"),
        (_with(wrapper="FW.zip"), "wrapper must be a mapping"),
        (_with(image__size=0), "image.size must be a positive integer"),
        (_with(image__size=True), "image.size must be a positive integer"),
        (_with(wrapper__sources="https://x"), "sources must be a list"),
        (_with(wrapper__sources=[{"vendor": VENDOR, "r2": "k"}]), "one-key"),
        (_with(wrapper__sources=[{"vendor": 42}]), "must be a string"),
        (_with(password_scheme={"candidates": "bticino"}), "list of strings"),
        (_with(password_scheme={"candidates": [1]}), "list of strings"),
        (_with(password_scheme="bticino"), "list of strings"),
        (_with(status="encrypted"), "status must be one of"),
        (_with(status="blocked"), "needs a blocked_reason"),
        (_with(status="blocked", blocked_reason="  "), "needs a blocked_reason"),
        (_with(blocked_reason="stray"), "only allowed with status: blocked"),
    ],
)
def test_schema_rejects(entry, match):
    with pytest.raises((ValueError, TypeError), match=match):
        schema.validate(entry, CAT_PATH)


def test_schema_accepts_the_valid_fixture():
    assert schema.validate(_valid(), CAT_PATH)["product"] == "MH200N"


def test_schema_accepts_a_blocked_entry_with_a_reason():
    entry = schema.validate(
        _with(status="blocked", blocked_reason="password unknown"), CAT_PATH
    )
    assert schema.is_blocked(entry) is True
    assert schema.is_blocked(_valid()) is False
    assert schema.is_blocked(_with(status="unpackable")) is False


def test_schema_accepts_a_bare_image_download():
    # A vendor download that IS the .fwz: wrapper and image are the same file.
    entry = _valid()
    entry["image"] = {k: entry["wrapper"][k] for k in ("filename", "size", "sha256")}
    assert schema.validate(entry, CAT_PATH)["image"]["filename"] == "FW.zip"


# --- guard: whole-tree checks ------------------------------------------------


@pytest.fixture
def git_tree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    return tmp_path


def test_guard_passes_a_clean_tree(git_tree, capsys):
    Path("README.md").write_text("# notes\n")
    Path("results").mkdir()
    Path("results/manifest.tsv").write_text("path\ttype\tsize\tsha256\n")
    assert guard.main() == 0
    assert "guard: ok (2 files clean)" in capsys.readouterr().out


def test_guard_refuses_oversized_files(git_tree, monkeypatch, capsys):
    monkeypatch.setattr(guard, "MAX_BYTES", 10)
    Path("big.tsv").write_text("x" * 11)
    assert guard.main() == 1
    assert "big.tsv: 11 bytes > 10" in capsys.readouterr().err


def test_guard_flags_an_ext_filesystem(tmp_path):
    fs = tmp_path / "rootfs"
    fs.write_bytes(b"\x00" * 0x438 + b"\x53\xef" + b"\x00" * 8)
    assert guard.is_binary(fs) == "ext filesystem"
