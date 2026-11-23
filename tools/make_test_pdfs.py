# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""テストの検査用の PDF（tests/data/試験_*.pdf のうち5つ）を、PDF の構文を直接書いて作る

作るもの（どれも ICC プロファイルを入れない。画像の色空間は DeviceRGB）:
- 試験_画像1枚.pdf：A4 のページに、1000×1400画素の JPEG を縦横の比を保って貼った PDF
- 試験_FormXObject縮小.pdf：同じ画像を描く Form XObject を、別の Form XObject で半分の大きさにして貼った PDF
- 試験_回転90.pdf：試験_画像1枚.pdf のページに /Rotate 90 を付けた PDF
- 試験_相互参照ストリーム.pdf：試験_画像1枚.pdf と同じ中身を、オブジェクトストリームと相互参照ストリームで書いた PDF
- 試験_文字のデータ9種類.pdf：小さな画像を貼ったページに、目印の文字列を、見えない文字・注釈・入力欄・文書の情報・
  XMP・しおり・添付ファイル・OpenAction（JavaScript）に入れた PDF

日時や識別子を書かないので、作るたびに同じバイト列になる（JPEG は uv.lock でバージョンを固定した OpenCV で作る）。
tests/test_make_test_pdfs.py が、作り直したものとコミットされているものが同じことを確かめる。
試験_入力欄.pdf・試験_パスワード付き.pdf は、ここでは作らない（tests/data/README.md）。

使い方（リポジトリの直下で）:
    uv run python tools/make_test_pdfs.py
"""

import sys
import zlib
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "data"
MARK = "SECRET1234"  # 試験_文字のデータ9種類.pdf の各所に入れる目印の文字列
A4 = (595, 842)


def jpeg(w: int, h: int) -> bytes:
    """灰色の地に黒い長方形を1つ描いた JPEG（ICC のデータ（APP2）は入らない）"""
    img = np.full((h, w, 3), 200, np.uint8)
    img[h // 4 : h // 2, w // 4 : w // 2] = 0
    return cv2.imencode(".jpg", img)[1].tobytes()


def text(s: str) -> bytes:
    """PDF の文字列（ASCII ならリテラル、ほかは UTF-16BE の16進）"""
    if s.isascii():
        return (
            b"("
            + s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode()
            + b")"
        )
    return b"<FEFF" + s.encode("utf-16-be").hex().upper().encode() + b">"


def stream(entries: bytes, data: bytes, compress: bool = False) -> bytes:
    """ストリームのオブジェクトの中身（entries は辞書の中の項目。/Length は足す）"""
    if compress:
        data = zlib.compress(data, 9)
        entries += b" /Filter /FlateDecode"
    return (
        b"<< %s /Length %d >>\nstream\n" % (entries, len(data)) + data + b"\nendstream"
    )


def write_pdf(objects: list[bytes], root: int, info: int | None = None) -> bytes:
    """オブジェクト（1番から順）を、従来の形の相互参照表で PDF にする"""
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    trailer = b"/Size %d /Root %d 0 R" % (len(objects) + 1, root)
    if info is not None:
        trailer += b" /Info %d 0 R" % info
    out += b"trailer\n<< %s >>\nstartxref\n%d\n%%%%EOF\n" % (trailer, xref)
    return bytes(out)


def write_pdf_with_xref_stream(objects: list[bytes], root: int) -> bytes:
    """ストリームでないオブジェクトをオブジェクトストリームに入れ、相互参照ストリームで PDF にする"""
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    packed = [n for n, body in enumerate(objects, 1) if b"stream\n" not in body]
    objstm = len(objects) + 1
    xref_n = objstm + 1
    entries: dict[int, tuple[int, int, int]] = {0: (0, 0, 65535)}
    for n, body in enumerate(objects, 1):
        if n not in packed:
            entries[n] = (1, len(out), 0)
            out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    head, data = [], bytearray()
    for i, n in enumerate(packed):
        head.append(b"%d %d" % (n, len(data)))
        data += objects[n - 1] + b"\n"
        entries[n] = (2, objstm, i)
    first = b" ".join(head) + b"\n"
    entries[objstm] = (1, len(out), 0)
    body = stream(
        b"/Type /ObjStm /N %d /First %d" % (len(packed), len(first)),
        first + bytes(data),
        True,
    )
    out += b"%d 0 obj\n" % objstm + body + b"\nendobj\n"
    entries[xref_n] = (1, len(out), 0)
    rows = b"".join(
        bytes([kind]) + field2.to_bytes(4, "big") + field3.to_bytes(2, "big")
        for kind, field2, field3 in (entries[n] for n in range(xref_n + 1))
    )
    xref = stream(
        b"/Type /XRef /Size %d /W [1 4 2] /Root %d 0 R" % (xref_n + 1, root), rows, True
    )
    start = len(out)
    out += b"%d 0 obj\n" % xref_n + xref + b"\nendobj\n"
    out += b"startxref\n%d\n%%%%EOF\n" % start
    return bytes(out)


def image_xobject(data: bytes, w: int, h: int) -> bytes:
    return stream(
        b"/Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB /BitsPerComponent 8 "
        b"/Filter /DCTDecode" % (w, h),
        data,
    )


def fit(w: int, h: int) -> bytes:
    """w×h画素の画像を、縦横の比を保って A4 の幅いっぱいに、縦の真ん中に貼る変換"""
    height = A4[0] * h / w
    return b"%d 0 0 %s 0 %s cm" % (A4[0], _num(height), _num((A4[1] - height) / 2))


def _num(v: float) -> bytes:
    return (b"%.4f" % v).rstrip(b"0").rstrip(b".")


def plain_objects(rotate: int = 0) -> list[bytes]:
    """試験_画像1枚.pdf のオブジェクト（rotate を付けると、そのページの /Rotate）"""
    rotation = b" /Rotate %d" % rotate if rotate else b""
    return [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842]%s /Resources << /XObject << /Im0 4 0 R >> >> "
        b"/Contents 5 0 R >>" % rotation,
        image_xobject(jpeg(1000, 1400), 1000, 1400),
        stream(b"", b"q\n" + fit(1000, 1400) + b"\n/Im0 Do\nQ\n"),
    ]


def form_xobject_objects() -> list[bytes]:
    """試験_FormXObject縮小.pdf のオブジェクト：画像を描く Form XObject（6）を、半分にする Form XObject（5）で貼る"""
    return [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /XObject << /Fm0 5 0 R >> >> "
            b"/Contents 4 0 R >>"
        ),
        stream(b"", b"q /Fm0 Do Q\n"),
        stream(
            b"/Type /XObject /Subtype /Form /BBox [0 0 595 842] /Matrix [0.5 0 0 0.5 100 321] "
            b"/Resources << /XObject << /Fm1 6 0 R >> >>",
            b"/Fm1 Do\n",
        ),
        stream(
            b"/Type /XObject /Subtype /Form /BBox [0 0 595 842] /Resources << /XObject << /Im0 7 0 R >> >>",
            b"q\n" + fit(1000, 1400) + b"\n/Im0 Do\nQ\n",
        ),
        image_xobject(jpeg(1000, 1400), 1000, 1400),
    ]


def marked_objects() -> tuple[list[bytes], int]:
    """試験_文字のデータ9種類.pdf のオブジェクトと、文書の情報のオブジェクトの番号"""
    hidden = f"{MARK} 個人番号 123412341234"
    objects = [
        # 1 目録：入力欄・XMP・しおり・添付ファイル・OpenAction（JavaScript）
        b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [13 0 R] /NeedAppearances false >> "
        b"/Metadata 15 0 R /Outlines 16 0 R /Names << /EmbeddedFiles << /Names [(memo.txt) 18 0 R] >> >> "
        b"/OpenAction << /S /JavaScript /JS " + text(MARK) + b" >> >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /XObject << /Im0 4 0 R >> /Font << /F1 6 0 R >> >> /Contents 5 0 R "
            b"/Annots [9 0 R 11 0 R] >>"
        ),
        image_xobject(jpeg(400, 566), 400, 566),
        # 5 ページの内容：画像と、見えない文字（描き方 3 Tr）
        stream(
            b"",
            b"q\n" + fit(400, 566) + b"\n/Im0 Do\nQ\n"
            b"BT\n1 0 0 1 100 742 Tm\n/F1 11 Tf 3 Tr\n<"
            + hidden.encode("utf-16-be").hex().encode()
            + b"> Tj\nET\n",
            True,
        ),
        # 6〜8 日本語の文字を書くフォント（埋め込まない。PDFium が代わりのフォントで扱う）
        b"<< /Type /Font /Subtype /Type0 /BaseFont /Gothic /Encoding /UniJIS-UTF16-H /DescendantFonts [7 0 R] >>",
        (
            b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /Gothic "
            b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Japan1) /Supplement 6 >> /FontDescriptor 8 0 R >>"
        ),
        (
            b"<< /Type /FontDescriptor /FontName /Gothic /Flags 4 /FontBBox [-200 -200 1200 1200] /ItalicAngle 0 "
            b"/Ascent 1000 /Descent -200 /CapHeight 700 /StemV 80 >>"
        ),
        # 9〜10 注釈（メモ）
        b"<< /Type /Annot /Subtype /Text /Rect [200 626 216 642] /Contents "
        + text(f"{MARK} 注釈")
        + b" /Name /Note /F 28 /C [1 1 0] /P 3 0 R /AP << /N 10 0 R >> >>",
        stream(
            b"/Type /XObject /Subtype /Form /BBox [0 0 16 16]",
            b"1 1 0 rg\n0.5 0.5 15 15 re\nb\n",
        ),
        # 11〜14 入力欄（値と見た目の両方に目印）
        (
            b"<< /Type /Annot /Subtype /Widget /Rect [300 522 400 542] /F 4 /P 3 0 R /Parent 13 0 R "
            b"/AP << /N 12 0 R >> >>"
        ),
        stream(
            b"/Type /XObject /Subtype /Form /BBox [0 0 100 20] /Resources << /Font << /Helv 14 0 R >> >>",
            b"/Tx BMC\nq\nBT\n0 g\n/Helv 15 Tf\n2 5 Td\n"
            + text(MARK)
            + b" Tj\nET\nQ\nEMC\n",
        ),
        b"<< /FT /Tx /T (f1) /V "
        + text(MARK)
        + b" /DA (0 g /Helv 0 Tf) /Kids [11 0 R] >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        # 15 XMP
        stream(
            b"/Type /Metadata /Subtype /XML",
            f"<x:xmpmeta xmlns:x='adobe:ns:meta/'><s>{MARK}</s></x:xmpmeta>".encode(),
        ),
        # 16〜17 しおり
        b"<< /Type /Outlines /First 17 0 R /Last 17 0 R /Count 1 >>",
        b"<< /Title "
        + text(f"{MARK} しおり")
        + b" /Parent 16 0 R /Dest [3 0 R /XYZ 72 806 0] >>",
        # 18〜19 添付ファイル
        b"<< /Type /Filespec /F (memo.txt) /UF (memo.txt) /EF << /F 19 0 R >> >>",
        stream(
            b"/Type /EmbeddedFile /Params << /Size %d >>"
            % len(f"{MARK} 添付".encode()),
            f"{MARK} 添付".encode(),
        ),
        # 20 文書の情報
        b"<< "
        + b" ".join(
            b"/%s %s" % (key, text(MARK))
            for key in (
                b"Title",
                b"Author",
                b"Subject",
                b"Keywords",
                b"Creator",
                b"Producer",
            )
        )
        + b" >>",
    ]
    return objects, 20


def build() -> dict[str, bytes]:
    """作る PDF（ファイルの名前 → バイト列）"""
    marked, info = marked_objects()
    return {
        "試験_画像1枚.pdf": write_pdf(plain_objects(), 1),
        "試験_FormXObject縮小.pdf": write_pdf(form_xobject_objects(), 1),
        "試験_回転90.pdf": write_pdf(plain_objects(rotate=90), 1),
        "試験_相互参照ストリーム.pdf": write_pdf_with_xref_stream(plain_objects(), 1),
        "試験_文字のデータ9種類.pdf": write_pdf(marked, 1, info),
    }


def main() -> None:
    for name, data in build().items():
        (OUT / name).write_bytes(data)
        print(f"{name}\t{len(data):,}")


if __name__ == "__main__":
    sys.exit(main())
