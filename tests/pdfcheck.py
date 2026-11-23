# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""出力した PDF の検査（テストの補助）

ツールは出力のたびには検査しない。出力の中身はコードと uv.lock で固定したライブラリだけで決まるので、コードや
ライブラリを変えたときに、テストと tools/check_output.py（Windows で出力した試験用の PDF を確かめる）でかける。

- problems：4つの検査をまとめてかけ、見つかったものの一覧を返す（空なら問題なし）
  - 墨消しした範囲が黒いか（black）
  - 元の文字のデータ・注釈・入力欄・しおり・添付ファイル・XMP・JavaScript が残っていないか（text_left_in）
  - 各ページの部品が画像1つだけか（text_left_in の「画像のほかの部品」）
  - 文書の情報がないか（text_left_in の「文書の情報」）
"""

import re
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from sumikeshi_studio import export, imaging


def problems(path: Path, jobs: list[export.PageJob]) -> list[str]:
    """出力した PDF に4つの検査をかけ、見つかったものの一覧を返す。jobs は、出力した各ページの墨消しの依頼"""
    found = text_left_in(path)
    with imaging.PDF_LOCK, imaging.open_pdf(path) as pdf:
        count = len(pdf)
    if count != len(jobs):
        found.append(f"ページの数が{count}（{len(jobs)}のはず）")
    for i, job in enumerate(jobs[:count]):
        if not black(path, i, job):
            found.append(f"墨消しできていない範囲（{i + 1}ページ）")
    return found


# 出力したPDFのファイルの中の辞書にあってはいけない名前と、その種類（text_left_in）
FORBIDDEN_NAMES = {
    "Font": "フォント",
    "Annots": "注釈",
    "Outlines": "しおり",
    "EmbeddedFiles": "添付ファイル",
    "EmbeddedFile": "添付ファイル",
    "Metadata": "XMP",
    "AcroForm": "JavaScript・フォーム",
    "XFA": "JavaScript・フォーム",
    "JavaScript": "JavaScript・フォーム",
    "JS": "JavaScript・フォーム",
    "OpenAction": "JavaScript・フォーム",
    "AA": "JavaScript・フォーム",
}
NAME_RE = re.compile(
    rb"/(%s)(?=[\s/<>\[\]()%%{}]|$)" % "|".join(FORBIDDEN_NAMES).encode()
)
KINDS = [
    "ページの文字",
    "フォント",
    "注釈",
    "入力欄",
    "しおり",
    "添付ファイル",
    "文書の情報",
    "XMP",
    "JavaScript・フォーム",
    "画像のほかの部品",
    "検査できなかった構造",
]


def text_left_in(path: Path) -> list[str]:
    """PDFに残っている文字のデータの種類を返す。画像だけのPDFなら空

    pypdfium2 で読める項目（ページの文字・注釈・入力欄・しおり・添付ファイル・文書の情報・JavaScript）は
    pypdfium2 で調べる。XMP・OpenAction などは pypdfium2 で読めないので、ファイルの中の辞書（ストリームの中身を
    除いた部分）を相互参照表からたどって、名前があるかを調べる（_dictionaries）。各ページの部品が画像1つだけかも
    調べる。
    """
    left = set()
    with imaging.PDF_LOCK, imaging.open_pdf(path) as pdf:
        for i in range(len(pdf)):
            page = pdf[i]
            try:
                left |= _page_left(page)
            finally:
                page.close()
        if pdf.get_formtype() != pdfium_c.FORMTYPE_NONE:
            left.add("入力欄")
        if any(True for _ in pdf.get_toc()):
            left.add("しおり")
        if pdf.count_attachments():
            left.add("添付ファイル")
        if pdfium_c.FPDFDoc_GetJavaScriptActionCount(pdf.raw) > 0:
            left.add("JavaScript・フォーム")
        if pdf.get_metadata_dict(skip_empty=True):
            left.add("文書の情報")
    dictionaries = _dictionaries(path.read_bytes())
    if dictionaries is None:
        left.add("検査できなかった構造")
    else:
        for d in dictionaries:
            left |= {FORBIDDEN_NAMES[m.decode()] for m in NAME_RE.findall(d)}
    return [k for k in KINDS if k in left]


def _page_left(page: pdfium.PdfPage) -> set[str]:
    """1ページに残っている文字のデータの種類（PDF_LOCK を持って呼ぶ）"""
    left = set()
    textpage = page.get_textpage()
    try:
        if textpage.count_chars() > 0:
            left.add("ページの文字")
    finally:
        textpage.close()
    if pdfium_c.FPDFPage_GetAnnotCount(page.raw) > 0:
        left.add("注釈")
    count = pdfium_c.FPDFPage_CountObjects(page.raw)
    kinds = [
        pdfium_c.FPDFPageObj_GetType(pdfium_c.FPDFPage_GetObject(page.raw, i))
        for i in range(count)
    ]
    if pdfium_c.FPDF_PAGEOBJ_TEXT in kinds:
        left.add("フォント")
    if kinds != [pdfium_c.FPDF_PAGEOBJ_IMAGE]:
        left.add("画像のほかの部品")
    return left


def _dictionaries(data: bytes) -> list[bytes] | None:
    """PDF のファイルの中の間接オブジェクトの、ストリームの中身を除いた部分の一覧

    従来の形の相互参照表（xref）だけを読む。ツールは出力をこの形で書く（オブジェクトストリームを使わない。export.PdfWriter）。
    読めないとき（相互参照ストリームなど）は None。
    """
    start = data.rfind(b"startxref")
    m = re.match(rb"startxref\s+(\d+)", data[start:]) if start >= 0 else None
    if not m:
        return None
    pos = int(m[1])
    if not data.startswith(b"xref", pos):
        return None
    offsets = []
    lines = iter(data[pos + 4 : data.find(b"trailer", pos)].split())
    try:
        for first in lines:
            count = int(next(lines))
            int(first)
            for _ in range(count):
                offset, _gen, kind = next(lines), next(lines), next(lines)
                if kind == b"n":
                    offsets.append(int(offset))
    except StopIteration, ValueError:
        return None
    out = []
    for offset in offsets:
        end = data.find(b"endobj", offset)
        body = data[offset : end if end >= 0 else len(data)]
        s = re.search(rb">>\s*stream\r?\n", body)
        out.append(body[: s.start() + 2] if s else body)
    trailer = data[data.find(b"trailer", pos) : start]
    return out + [trailer]


def black(out_path: Path, index: int, job: export.PageJob) -> bool:
    """出力したPDFの1ページ（index は0始まり）を画像にし、墨消しの依頼 job の範囲（長方形と、長方形でない範囲）が
    すべて黒いかを返す（もとは export.check_page）。長方形でない範囲は、出力が塗るのと同じ画素（export.polygon_pixels）を見る"""
    gray = imaging.render_pdf_page(out_path, index, imaging.WORK_DPI / 72, gray=True)
    sx = gray.shape[1] / job.work_shape[1]
    sy = gray.shape[0] / job.work_shape[0]
    for rect in job.rects:
        x0, y0, x1, y1 = export._scaled(rect, sx, sy, gray.shape)
        if gray[y0:y1, x0:x1].size and gray[y0:y1, x0:x1].mean() > 40:
            return False
    for points in job.polygons:
        x0, y0, mask = export.polygon_pixels(points, sx, sy, gray.shape)
        inside = gray[y0 : y0 + mask.shape[0], x0 : x0 + mask.shape[1]][mask]
        if inside.size and inside.mean() > 40:
            return False
    return True
