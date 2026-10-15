from __future__ import annotations

import hashlib
import struct
from collections.abc import Callable
from pathlib import Path

import httpx

from . import config

Reader = Callable[[int, int], bytes]  # (offset, length) -> bytes


def http_reader(url: str, size: int | None = None) -> tuple[Reader, int]:
    """Range-read helper; returns (reader, total size)."""
    if size is None:
        r = httpx.head(url, timeout=60, follow_redirects=True)
        r.raise_for_status()
        size = int(r.headers["Content-Length"])

    def read(off: int, n: int) -> bytes:
        r = httpx.get(url, headers={"Range": f"bytes={off}-{off + n - 1}"}, timeout=300, follow_redirects=True)
        if r.status_code != 206:
            raise RuntimeError(f"server ignored Range request (status {r.status_code})")
        return r.content

    return read, size


def _find_eocd(tail: bytes) -> int:
    i = tail.rfind(b"PK\x05\x06")
    if i < 0:
        raise ValueError("EOCD not found")
    return i


def extract_stored_member(read: Reader, size: int, name: str) -> bytes:
    """Pull one *stored* (method 0) member out of a remote zip using range reads only."""
    tail_len = min(size, 65536 + 22)
    tail = read(size - tail_len, tail_len)
    i = _find_eocd(tail)
    cd_size, cd_off = struct.unpack("<II", tail[i + 12 : i + 20])
    if cd_off == 0xFFFFFFFF:
        raise ValueError("zip64 not supported")
    cd = read(cd_off, cd_size)
    pos = 0
    while pos < len(cd):
        if cd[pos : pos + 4] != b"PK\x01\x02":
            break
        method, = struct.unpack("<H", cd[pos + 10 : pos + 12])
        comp, _uncomp = struct.unpack("<II", cd[pos + 20 : pos + 28])
        nlen, elen, cmt_len = struct.unpack("<HHH", cd[pos + 28 : pos + 34])
        lho, = struct.unpack("<I", cd[pos + 42 : pos + 46])
        fname = cd[pos + 46 : pos + 46 + nlen].decode()
        if fname == name or fname.endswith("/" + name):
            if method != 0:
                raise ValueError(f"{name} is compressed (method {method}); expected stored")
            lh = read(lho, 30)
            ln, le = struct.unpack("<HH", lh[26:30])
            return read(lho + 30 + ln + le, comp)
        pos += 46 + nlen + elen + cmt_len
    raise FileNotFoundError(name)


def verify_dem(path: Path, lat: float, lon: float) -> None:
    import rasterio

    with rasterio.open(path) as ds:
        b = ds.bounds
        if not (b.left <= lon <= b.right and b.bottom <= lat <= b.top):
            raise ValueError(f"DEM {path} bounds {tuple(b)} do not contain {lat},{lon}")


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r, open(tmp, "wb") as f:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        got = 0
        for chunk in r.iter_bytes(1 << 20):
            f.write(chunk)
            got += len(chunk)
            if total:
                print(f"\r{dest.name}: {got * 100 // total}%", end="", flush=True)
    print()
    tmp.replace(dest)


def md5_of(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def fetch_osm(force: bool = False) -> Path:
    config.OSM_DIR.mkdir(parents=True, exist_ok=True)
    pbf = config.OSM_DIR / "lithuania-latest.osm.pbf"
    if pbf.exists() and not force:
        print(f"OSM present: {pbf}")
        return pbf
    _download(config.PBF_URL + ".md5", config.OSM_DIR / "lithuania-latest.osm.pbf.md5")
    _download(config.PBF_URL, pbf)
    expected = (config.OSM_DIR / "lithuania-latest.osm.pbf.md5").read_text().split()[0]
    if md5_of(pbf) != expected:
        pbf.unlink()
        raise RuntimeError("PBF md5 mismatch; deleted")
    return pbf


def fetch_dem(source: str = "fabdem", force: bool = False) -> Path:
    config.DEM_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.DEM_DIR / (config.FABDEM_MEMBER if source == "fabdem" else "copernicus_N54E025.tif")
    if dest.exists() and not force:
        print(f"DEM present: {dest}")
        return dest
    if source == "fabdem":
        read, size = http_reader(config.FABDEM_ZIP_URL)
        data = extract_stored_member(read, size, config.FABDEM_MEMBER)
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(dest)
    elif source == "copernicus":
        _download(config.COPERNICUS_URL, dest)
    else:
        raise ValueError(source)
    try:
        verify_dem(dest, *config.HOME)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return dest


def find_dem() -> tuple[Path, str]:
    f = config.DEM_DIR / config.FABDEM_MEMBER
    if f.exists():
        return f, "fabdem"
    c = config.DEM_DIR / "copernicus_N54E025.tif"
    if c.exists():
        return c, "copernicus"
    raise FileNotFoundError("no DEM in data/dem; run `takas fetch`")
