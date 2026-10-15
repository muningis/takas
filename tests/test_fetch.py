import io
import zipfile

import pytest

from takas.fetch import extract_stored_member


def _zip(entries, method=zipfile.ZIP_STORED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        for n, d in entries.items():
            z.writestr(n, d)
    return buf.getvalue()


def _reader(blob):
    return lambda off, n: blob[off : off + n]


def test_extract_stored_member():
    payload = bytes(range(256)) * 50
    blob = _zip({"a.txt": b"x" * 10, "N54E025_FABDEM_V1-2.tif": payload, "z.txt": b"yy"})
    got = extract_stored_member(_reader(blob), len(blob), "N54E025_FABDEM_V1-2.tif")
    assert got == payload


def test_compressed_member_rejected():
    blob = _zip({"a.tif": b"0" * 1000}, zipfile.ZIP_DEFLATED)
    with pytest.raises(ValueError):
        extract_stored_member(_reader(blob), len(blob), "a.tif")


def test_missing_member():
    blob = _zip({"a": b"1"})
    with pytest.raises(FileNotFoundError):
        extract_stored_member(_reader(blob), len(blob), "b")
