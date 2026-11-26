# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""tests/data/ の検査用の PDF のテスト：どの PDF にも ICC プロファイルが入っていないこと"""

import io
from pathlib import Path

import pypdf
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests" / "data"
PASSWORDS = {"試験_パスワード付き.pdf": "a"}


def jpeg_markers(data: bytes) -> list[int]:
    """JPEG の、画像のデータ（SOS）までのマーカーの番号"""
    found, i = [], 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        found.append(marker)
        if marker == 0xDA:
            break
        i += 2 + int.from_bytes(data[i + 2 : i + 4], "big")
    return found


@pytest.mark.parametrize("path", sorted(DATA.glob("*.pdf")), ids=lambda p: p.name)
def test_pdfs_have_no_icc_profile(path):
    """検査用の PDF には、ICC プロファイル（ICCBased の色空間と、MuPDF のプロファイルの「Artifex」の文字列）も、
    JPEG の中の ICC のデータ（APP2）もない。パスワード付きの PDF は、暗号を外したコピーで確かめる"""
    data = path.read_bytes()
    if path.name in PASSWORDS:
        doc = pdfium.PdfDocument(path, password=PASSWORDS[path.name])
        buf = io.BytesIO()
        doc.save(buf, flags=pdfium_c.FPDF_REMOVE_SECURITY)
        doc.close()
        data = buf.getvalue()
    reader = pypdf.PdfReader(io.BytesIO(data))
    found = data
    jpegs = []
    for i in range(1, int(reader.trailer["/Size"])):
        try:
            obj = reader.get_object(i)
        except pypdf.errors.PdfReadError:
            continue
        if isinstance(obj, pypdf.generic.StreamObject):
            if obj.get("/Filter") == "/DCTDecode":
                jpegs.append(obj._data)
            else:
                found += obj.get_data()
        if "/ICCBased" in repr(obj):
            found += b"/ICCBased"
    assert b"Artifex" not in found and b"/ICCBased" not in found
    assert all(0xE2 not in jpeg_markers(j) for j in jpegs)
