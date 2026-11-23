# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""判別・位置合わせ・向きの判定・出力のテスト（1ページの解析に数秒かかる）

試験画像と解析の結果は、conftest.py の scan・analyzed で、同じ指定ならテスト全体で使い回す。
"""

import dataclasses
import os
import sys
import threading
import tomllib
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pdfcheck
import pypdf
import pypdfium2 as pdfium
import pytest
import scan
from conftest import cached_analyze, needs_sample_scan
from field_coverage import (
    assert_covered,
    beyond_line,
    jpeg_gray,
    left_digits,
    number_left_cells,
)
from pypdf.generic import ContentStream
from scan import (
    BILEVEL_DPI,
    DATA,
    GRAY_DPI,
    JPEG_DPI,
    SAMPLE_SCAN,
    SIMPLE_FIELD,
    _image_pdf,
    _scan,
    _scan_bilevel,
    _scan_jpeg,
    make_certificate_scan,
    page_image,
    scan_transform,
    simple_form,
    written_page,
)
from userforms import bundled, bundled_forms, install_bundled, write_form

from sumikeshi_studio import (
    align,
    analysis,
    export,
    formcheck,
    forms,
    formsave,
    formstore,
    i18n,
    imaging,
    ocr,
    orientation,
    paper,
    pipeline,
    titles,
    variants,
)
from sumikeshi_studio import frames as frames_mod
from sumikeshi_studio.boxes import Box, Source

# 見本のある申告書（公開様式）ごとの、墨消しする個人番号欄の数
EXPECTED = {
    "2027bun_01": (forms.FUYOU, 10),  # 扶養控除等申告書 令和9年分
    "2026bun_04": (forms.HOKEN, 1),  # 保険料控除申告書 令和8年分
    "2026bun_06": (forms.KIHAI, 4),  # 基礎控除申告書 兼 … 令和8年分
}


@pytest.mark.parametrize("form", EXPECTED)
def test_public_forms_are_classified_and_located(form, analyzed):
    """3度傾けた公開様式のスキャンを、見本の様式に判別し、見本の欄の数の範囲を求める。要確認にしない"""
    _, res = analyzed(form, angle=3)
    kind, n = EXPECTED[form]
    assert (res.form, res.note, len(res.boxes)) == (kind, "", n)


@needs_sample_scan
def test_sample_pages_get_all_fields_fitted_to_frames(analyzed):
    """実物のスキャン（見本のスキャン）でも、公開様式から作った見本で判別し、見本のすべての欄を墨消しして、どの欄も
    印刷された枠の線に合わせる（見本の位置のままにする欄がない）"""
    pages = imaging.open_pages(SAMPLE_SCAN)
    expected = [forms.FUYOU, forms.HOKEN, forms.KIHAI, formstore.UNKNOWN]
    for pg, kind in zip(pages, expected, strict=True):
        res = pipeline.analyze(pg, formset=bundled_forms())
        assert res.form == kind
        if kind == formstore.UNKNOWN:
            # 住宅借入金等特別控除申告書の記入例は、どの様式の見本にも類似しないので、墨消しせず要確認にする
            assert res.boxes == [] and res.note == forms.UNKNOWN_NOTE
            continue
        assert res.note == ""
        (spec,) = [s for s in bundled_forms() if s.name == kind]
        assert [b.kind for b in res.boxes] == [f.name for f in spec.fields]
        assert res.unframed == () and res.crossed == ()


@pytest.mark.parametrize("form", ["2025bun_01", "2026bun_01"])
def test_other_years_use_same_template(form, analyzed):
    """年分は確かめず、扶養控除等申告書の令和7・8年分も令和9年分の見本で墨消しする"""
    _, res = analyzed(form)
    assert res.form == forms.FUYOU and res.note == ""
    assert len(res.boxes) == 10


@pytest.mark.parametrize("form", EXPECTED)
@pytest.mark.parametrize(
    ("angle", "turns"), [(-1.5, 0), (8, 0), (0.8, 1), (0.8, 2), (0.8, 3)]
)
def test_fields_in_scan_are_covered(form, angle, turns, scan, reference):
    """傾けたスキャン・回したスキャンで、向きを確信して直し、墨消しする範囲が個人番号欄を覆う（field_coverage.py）"""
    filled = form == "2027bun_01"
    img, ref_boxes = reference(form, filled)
    spec = {"angle": angle, "quarter_turns": turns} | (
        {"filled": True} if filled else {}
    )
    page = imaging.open_pages(scan(form, **spec))[0]
    res = cached_analyze(page)
    kind, fields = EXPECTED[form]
    assert (res.form, res.quarter_turns, len(res.boxes)) == (
        kind,
        (4 - turns) % 4,
        fields,
    )
    assert res.orientation_confident
    M, shape = scan_transform(img.shape, angle, turns)
    assert_covered(ref_boxes, M, shape, page, res)


# 試験画像の種類：(解像度, 作り方)。白黒2値は、細い枠の線が残るしきい値160にする（128 では線が消える）
SCANS = {
    "gray": (GRAY_DPI, lambda img, out, angle: _scan(img, out, angle, 0)),
    "bilevel": (
        BILEVEL_DPI,
        lambda img, out, angle: _scan_bilevel(img, out, angle, 0, threshold=160),
    ),
    "jpeg": (
        JPEG_DPI,
        lambda img, out, angle: _scan_jpeg(np.dstack([img] * 3), out, angle, 0),
    ),
}
# 公開様式（傾けない 200dpi）の上の、扶養控除等申告書の源泉控除対象配偶者の欄の下の線の外側の縁と、欄の内側の左右。
# 画像で確かめた値。下の段は生年月日の欄
HAIGUUSHA_BOTTOM = 501
HAIGUUSHA_SPAN = (561, 987)


def _written_scans(form, kind, angle, boxes, tmp_path, make=None, place="center"):
    """番号を書かない用紙と、boxes（ページの画像の座標の欄）に番号を書いた用紙の試験画像のページと、
    ページの画像（傾けない 200dpi）の座標から試験画像の座標への変換と、試験画像の大きさ。place は数字の置き方
    （scan.written_page）"""
    dpi, scan_fn = SCANS[kind]
    scan_fn = make or scan_fn
    pages = []
    for name, b in (("blank", ()), ("filled", boxes)):
        img = written_page(form, dpi, b, place)
        pages.append(
            imaging.open_pages(scan_fn(img, tmp_path / f"{name}.pdf", angle))[0]
        )
    k = dpi / GRAY_DPI
    M, shape = scan_transform(img.shape[:2], angle, 0)
    return pages, M @ np.diag([k, k, 1.0]), shape


def test_page_without_result_is_exported_without_redaction(tmp_path):
    """解析の結果のないページ（解析が失敗したページ）は、向きと傾きを直さず、墨消しせずに出力する"""
    page = imaging.open_pages(DATA / "公開様式_2026bun_01_200dpi_グレー.png")[0]
    job = export.PageJob.of(page, None)
    assert (job.quarter_turns, job.skew, job.rects) == (0, 0.0, ())
    image = export.mask_page(job)
    rendered = imaging.render(page)
    assert (image.height, image.width, image.masked) == (*rendered.shape[:2], 0)
    out = tmp_path / "out.pdf"
    report = export.export([(page, None)], out)
    assert (report.pages, report.masked) == (1, 0)
    assert len(pypdf.PdfReader(out).pages) == 1


@pytest.mark.parametrize(
    ("form", "kind", "angle", "place"),
    [
        ("2026bun_04", "gray", 0.75, "center"),
        ("2026bun_04", "bilevel", 1.5, "center"),
        ("2026bun_04", "jpeg", 0.75, "center"),
        ("2027bun_01", "gray", 1.5, "center"),
        ("2027bun_01", "bilevel", -0.75, "center"),
        # 左に寄せた数字「1」の縦の画を、左の線とみなさないことを確かめる（frames.INNER_TIE）
        ("2027bun_01", "gray", 2, "left"),
        ("2027bun_01", "jpeg", 1.5, "right"),
        # 数字を書いた用紙だけ位置合わせが左右に約7画素ずれ、左に寄せた「1」の縦の画のほうが本当の左の線より期待する
        # 位置に近くなる。縦の線の候補が欄の上下の端まで続くかを見るので、線に選ばない（frames.END_BAND）
        ("2026bun_06", "jpeg", 0, "left"),
    ],
)
def test_numbers_inside_frame_are_masked(form, kind, angle, place, reference, tmp_path):
    """印刷された枠の線が見える試験画像で、墨消しが枠の線に合い、枠の内側に書いた番号が出力に残らない

    墨消しは広げず、枠の線の中心に合わせる。番号は、
    番号を書いた用紙の出力と、番号を書かない用紙を同じ範囲で墨消しした出力の差で確かめる。
    """
    _, ref_boxes = reference(form)
    (blank, filled), M, shape = _written_scans(
        form, kind, angle, ref_boxes, tmp_path, place=place
    )
    res = cached_analyze(blank)
    assert (res.form, len(res.boxes), res.unframed) == (*EXPECTED[form], ())
    assert res.note == ""  # 枠の線を越えた記入の要確認も付かない
    # スキャンと判定したページの範囲は、線が見つかっても長方形（角を持たない）
    assert all(b.corners is None for b in res.boxes)
    assert_covered(ref_boxes, M, shape, blank, res)

    res = cached_analyze(filled)
    assert (res.form, len(res.boxes), res.unframed) == (*EXPECTED[form], ())
    assert res.note == ""
    left, digits, _ = left_digits(res, filled, blank)
    assert digits > 0
    assert left.sum() == 0


@pytest.mark.parametrize("shift", [2, 4])
def test_ranges_inside_frame_do_not_take_digit_strokes(
    shift, reference, tmp_path, monkeypatch
):
    """範囲が枠の線より内側にある様式（範囲を描く画面で線の中心に合わせていないもの）でも、欄の右に寄せた「4」、
    左右に寄せた「1」の縦の画を線に選ばず、数字が墨消しの外に残らない（解析だけで確かめる。縦の線の候補は、欄の
    上下の端まで続くものだけ。frames.END_BAND）"""
    form = "2027bun_01"
    _, ref_boxes = reference(form)
    inside = _ranges_inside("nta-fuyou-r9", shift)
    for digits, place in (
        ("444444444444", "right"),
        ("111111111111", "left"),
        ("111111111111", "right"),
    ):
        monkeypatch.setattr(scan, "DUMMY", digits)
        out = tmp_path / f"{digits[0]}-{place}"
        out.mkdir()
        (blank, filled), _, _ = _written_scans(
            form, "jpeg", 1.5, ref_boxes, out, place=place
        )
        res = pipeline.analyze(filled, formset=(inside,))
        left, written, _ = left_digits(res, filled, blank)
        assert written > 0 and left.sum() == 0, (digits, place)


def _ranges_inside(form_id, shift):
    """配布用パッケージの様式の範囲を左右とも shift 画素内側に狭め、上下・左右の線を探す様式（範囲を描く画面で線の中心に
    合わせていないもの）"""
    both = (formstore.TOP_BOTTOM, formstore.LEFT_RIGHT)
    spec = bundled(form_id)
    return dataclasses.replace(
        spec,
        fields=tuple(
            formstore.Field(f.name, f.x + shift, f.y, f.w - 2 * shift, f.h, both)
            for f in spec.fields
        ),
    )


def _tall_digits_at_edge(place):
    """scan._digits の代わり：欄の高さいっぱい（上下の線の内側の縁に接する）の数字を、ます目の左か右に寄せる"""
    original = scan._digits

    def digits(b, _place):
        k = 72 / GRAY_DPI
        cell = b.w / len(scan.DUMMY)
        out = []
        for i, (_, base, size, sx, d) in enumerate(original(b, "touch")):
            width = size * scan.DIGIT_WIDTH * sx
            if place == "left":
                left = (b.x + 1 + i * cell) * k + 0.2
            else:
                left = (b.x - 1 + (i + 1) * cell) * k - 0.2 - width
            out.append((left, base, size, sx, d))
        return out

    return digits


def test_tall_digits_at_edge_are_not_taken_as_lines(reference, tmp_path, monkeypatch):
    """欄の高さいっぱいに書いて欄の端に寄せた数字は、縦の画が欄の上下の端まで届くが、範囲が枠の線より内側にある様式でも
    （カラー JPEG、傾き1.5度）線に選ばれず、範囲は記入のない用紙と同じ位置になる（縦の線の候補を欄の上下の端まで続くかで
    選ぶ決まりの、弱いところになりうる形。傾き0で選ばれる件は test_tall_digits_at_right_edge_keep_ranges）"""
    form = "2027bun_01"
    _, ref_boxes = reference(form)
    inside = _ranges_inside("nta-fuyou-r9", 4)
    dpi, scan_fn = SCANS["jpeg"]
    blank = imaging.open_pages(
        scan_fn(written_page(form, dpi), tmp_path / "blank.pdf", 1.5)
    )[0]
    expected = [b.rect() for b in pipeline.analyze(blank, formset=(inside,)).boxes]
    for digits, place in (("444444444444", "right"), ("111111111111", "left")):
        monkeypatch.setattr(scan, "DUMMY", digits)
        monkeypatch.setattr(scan, "_digits", _tall_digits_at_edge(place))
        img = written_page(form, dpi, ref_boxes, "touch")
        monkeypatch.undo()
        page = imaging.open_pages(scan_fn(img, tmp_path / f"{place}.pdf", 1.5))[0]
        res = pipeline.analyze(page, formset=(inside,))
        for got, want in zip((b.rect() for b in res.boxes), expected):
            assert max(abs(u - v) for u, v in zip(got, want)) <= 0.6, (digits, place)


@pytest.mark.parametrize(
    ("form", "form_id", "digits"),
    [
        ("2027bun_01", "nta-fuyou-r9", "123412341234"),
        ("2026bun_06", "nta-kihai-r8", "444444444444"),
    ],
)
def test_tall_digits_at_right_edge_keep_ranges(
    form, form_id, digits, reference, tmp_path, monkeypatch
):
    """欄の高さいっぱいに書いて欄の右の端に寄せた数字があっても、範囲が枠の線より4画素内側にある様式（カラー JPEG、
    傾き0）で、範囲は記入のない用紙と同じ位置になる

    欄の高さいっぱいの数字の縦の画は、欄の上下の端の近くの帯に届くので、左右の線の候補から外れない。範囲の右の辺が
    縦の画の手前まで内側に入るかは、見本の画像しだいで変わる（範囲の中を枠の線ごと塗った見本の画像では、この
    2件で10.5・10.0画素内側に入り、縦の画が墨消しの外に残る）
    """
    _, ref_boxes = reference(form)
    inside = _ranges_inside(form_id, 4)
    dpi, scan_fn = SCANS["jpeg"]
    blank = imaging.open_pages(
        scan_fn(written_page(form, dpi), tmp_path / "blank.pdf", 0)
    )[0]
    expected = [b.rect() for b in pipeline.analyze(blank, formset=(inside,)).boxes]
    monkeypatch.setattr(scan, "DUMMY", digits)
    monkeypatch.setattr(scan, "_digits", _tall_digits_at_edge("right"))
    img = written_page(form, dpi, ref_boxes, "touch")
    monkeypatch.undo()
    page = imaging.open_pages(scan_fn(img, tmp_path / "filled.pdf", 0))[0]
    res = pipeline.analyze(page, formset=(inside,))
    for b, want in zip(res.boxes, expected):
        got = b.rect()
        assert max(abs(u - v) for u, v in zip(got, want)) <= 0.6, b.kind


def test_form_registered_with_ranges_inside_frame(reference, tmp_path, monkeypatch):
    """範囲を枠の線より内側に描いて登録した様式でも、登録で範囲を線の中心に合わせる（forms.fit_to_frame）ので、
    欄の左右に寄せて書いた「4」「1」が墨消しの外に残らない（内側に描いたまま保存すると、数字の縦の画を線に選んだり、
    左右の線の組を記録できずに線を探さなかったりして、数字の一部が残った）"""
    form = "2027bun_01"
    shift = 4  # 範囲を枠の線より内側に描いた量（画素）
    work, ref_boxes = reference(form)
    spec = bundled("nta-fuyou-r9")
    fields = []
    for f in spec.fields:
        rect, found = forms.fit_to_frame(
            work, True, (f.x + shift, f.y, f.w - 2 * shift, f.h)
        )
        assert found == (formstore.TOP_BOTTOM, formstore.LEFT_RIGHT)
        fields.append(formstore.Field(f.name, *rect, found))
    registered = dataclasses.replace(spec, fields=tuple(fields))
    for digits, place in (
        ("444444444444", "right"),
        ("111111111111", "left"),
        ("111111111111", "right"),
    ):
        monkeypatch.setattr(scan, "DUMMY", digits)
        out = tmp_path / f"{digits[0]}-{place}"
        out.mkdir()
        (blank, filled), _, _ = _written_scans(
            form, "jpeg", 1.5, ref_boxes, out, place=place
        )
        res = pipeline.analyze(filled, formset=(registered,))
        left, written, _ = left_digits(res, filled, blank)
        assert written > 0 and left.sum() == 0, (digits, place)


def _exif_jpeg(img: np.ndarray, out: Path, orientation: int) -> Path:
    """画素を回して保存し、EXIF の向き（6か8）を付けた JPEG（向きを直して表示すると img の向きになる）"""
    from PIL import Image

    stored = np.rot90(img, {6: 1, 8: -1}[orientation])
    exif = Image.Exif()
    exif[imaging.EXIF_ORIENTATION] = orientation
    Image.fromarray(np.ascontiguousarray(stored)).save(
        out, exif=exif, dpi=(GRAY_DPI, GRAY_DPI), quality=92
    )
    return out


@pytest.mark.parametrize("case", ["turn1", "turn2", "turn3", "exif6", "exif8", "given"])
def test_rotated_pages_are_masked_in_output(case, reference, tmp_path):
    """90・180・270度に回したページ、EXIF の向きのある写真、画面で向きを指定したページでも、出力で番号が消え、
    出力の検査（pdfcheck）に通る

    番号は、test_numbers_inside_frame_are_masked と同じく、番号を書いた用紙と書かない用紙の出力の差で確かめる。
    """
    form = "2027bun_01"
    _, ref_boxes = reference(form)
    if case.startswith("exif"):
        orientation, turns, given = int(case[4:]), 0, None

        def make(img, out, angle):
            return _exif_jpeg(img, out.with_suffix(".jpg"), orientation)
    else:
        turns = 2 if case == "given" else int(case[4:])
        given = (4 - turns) % 4 if case == "given" else None  # 画面で回した向き

        def make(img, out, angle):
            return _scan(img, out, angle, turns)

    (blank, filled), _, _ = _written_scans(
        form, "gray", 0.5, ref_boxes, tmp_path, make=make
    )
    res = cached_analyze(filled, quarter_turns=given)
    assert (res.form, len(res.boxes)) == EXPECTED[form]
    assert res.quarter_turns == (4 - turns) % 4
    left, digits, _ = left_digits(res, filled, blank)
    assert digits > 0
    assert left.sum() == 0
    out = tmp_path / "out.pdf"
    export.export([(filled, res)], out)
    assert pdfcheck.problems(out, _jobs([(filled, res)])) == []


@pytest.mark.parametrize("angle", [0.75, 1.5])
def test_bilevel_scan_without_lines_keeps_template_position(angle, reference, tmp_path):
    """枠の線が消えた白黒2値（しきい値128）では、見本の位置のまま墨消しし、線が見つからない欄として示す

    線が見つからない欄では、墨消しの外に残ってよいのは、先頭か最後の桁のます目にある、1桁分以下の画素だけとする。
    この2つの角度では、位置合わせの1段目の変換だけだと範囲が欄から外れる（2段目を確かめる角度）。
    """
    form = "2026bun_04"
    _, ref_boxes = reference(form)
    (blank, filled), M, shape = _written_scans(
        form,
        "bilevel",
        angle,
        ref_boxes,
        tmp_path,
        make=lambda img, out, a: _scan_bilevel(img, out, a, 0),
    )
    res = cached_analyze(filled)
    assert (res.form, len(res.boxes)) == EXPECTED[form]
    assert res.unframed == (ref_boxes[0].kind,)
    assert res.note == (
        f"「{ref_boxes[0].kind}」の欄の範囲が、欄に合っていないおそれがあります"
    )
    left, digits, job = left_digits(res, filled, blank)
    per_digit = digits / (12 * len(ref_boxes))
    cells = number_left_cells(left, job.work_shape, ref_boxes, M, shape, filled, res)
    assert {c for _, c in cells} <= {0, 11}
    assert left.sum() <= per_digit


@pytest.mark.parametrize(
    ("kind", "angle"),
    [("gray", -1.5), ("gray", 3), ("bilevel", 0.75), ("jpeg", 1.5)],
)
def test_mask_does_not_cover_birthdate_row(kind, angle, tmp_path):
    """扶養控除等申告書の源泉控除対象配偶者の欄の墨消しが、下の線（その下は生年月日の欄）の外側の縁を、
    出力の画像の画素で1.5画素（作業画像の画素）を超えて越えない

    1.5画素の許しは、正解の線の位置そのものの誤差（公開様式の画像の画素の境目で求め、試験画像の変換で移したもの）と、
    出力の画像の画素にそろえる丸めによる。
    """
    form = "2027bun_01"
    dpi, scan_fn = SCANS[kind]
    img = written_page(form, dpi)
    page = imaging.open_pages(scan_fn(img, tmp_path / "blank.pdf", angle))[0]
    k = dpi / GRAY_DPI
    M, shape = scan_transform(img.shape[:2], angle, 0)
    M = M @ np.diag([k, k, 1.0])
    res = cached_analyze(page)
    assert (res.form, res.unframed) == (forms.FUYOU, ())
    [box] = [b for b in res.boxes if b.kind == "A 源泉控除対象配偶者"]
    job = export.PageJob.of(page, res)
    masked = jpeg_gray(export.mask_page(dataclasses.replace(job, rects=(box.rect(),))))
    plain = jpeg_gray(export.mask_page(dataclasses.replace(job, rects=())))
    changed = plain.astype(int) - masked > 64  # 墨消しで黒くなった画素
    over = beyond_line(
        changed, job.work_shape, M, shape, page, res,
        HAIGUUSHA_BOTTOM, "bottom", HAIGUUSHA_SPAN,
    )  # fmt: skip
    assert over <= 1.5


@pytest.mark.parametrize(
    ("place", "kind", "angle", "crossing"),
    [
        ("over1", "gray", 0.75, True),
        ("over2", "jpeg", -0.6, True),
        ("over1", "bilevel", 1.5, True),
        ("touch", "gray", 0.75, False),
        ("below", "gray", -1.5, False),
        ("below", "bilevel", 0.75, False),
    ],
)
def test_writing_beyond_frame_needs_review(
    place, kind, angle, crossing, reference, tmp_path
):
    """枠の線を1mm・2mm 越えて書いた数字があれば、その欄の名前を入れて「⚠要確認」とする。線に内から接して
    書いた数字と、下の段に線に外から接して書いた記入（生年月日に見立てる）では付けない

    数字は、扶養控除等申告書の枠の線の中心に合わせた欄（傾けずに画像にしたページを解析したもの）を基準に書く。
    線に内から接して書いた数字では、線が見つからない欄が出ることがあるので、越えた記入だけを見る。
    """
    form = "2027bun_01"
    dpi, scan_fn = SCANS[kind]
    _, ref_boxes = reference(form)
    img = written_page(form, dpi, ref_boxes, place)
    page = imaging.open_pages(scan_fn(img, tmp_path / "page.pdf", angle))[0]
    res = cached_analyze(page)
    assert (res.form, len(res.boxes)) == EXPECTED[form]
    if crossing:
        assert "本人の個人番号" in res.crossed
        # 10欄とも越えた記入になる組では、欄の名前を並べずに「すべての欄」の文になる
        assert "「本人の個人番号」" in res.note or res.note == (
            "すべての欄の範囲が、欄に合っていないおそれがあります"
        )
    else:
        assert res.crossed == ()


def test_review_note_names_fields():
    """欄の要確認の文では、欄の名前を「」で囲んで並べる。ページの全部の欄（2つ以上）なら「すべての欄」とする"""
    every = {"本人", "配偶者", "親族"}
    assert forms.field_note(["本人", "親族"], every) == (
        "「本人」「親族」の欄の範囲が、欄に合っていないおそれがあります"
    )
    assert forms.field_note(["本人", "配偶者", "親族"], every) == (
        "すべての欄の範囲が、欄に合っていないおそれがあります"
    )
    # 欄が1つだけのページでは、「すべての欄」としない
    assert forms.field_note(["本人"], {"本人"}) == (
        "「本人」の欄の範囲が、欄に合っていないおそれがあります"
    )


def test_field_notes_merge_names():
    """枠の線を越えた記入・隣の行・線が見つからない・写真の枠のない欄・線が傾いた欄の名前を、1つの文に、確かめる
    必要の高い順に重ねずに並べる。「すべての欄」かは、まとめた名前で決める"""

    def fields(*names):
        return [forms.Field(name=n, x=0, y=0, w=1, h=1, frame=()) for n in names]

    every = fields("甲", "乙", "丙", "丁")
    notes = forms._field_notes(
        every,
        unframed=["丙", "乙"],
        crossed=["乙"],
        frameless=[],
        tilted=["丙", "甲"],
        shifted=["丙"],
    )
    assert notes == ["「乙」「丙」「甲」の欄の範囲が、欄に合っていないおそれがあります"]
    # 名前のどれかの一覧だけではページの全部の欄にならなくても、まとめると全部になれば「すべての欄」
    assert forms._field_notes(
        every,
        unframed=["甲", "乙"],
        crossed=[],
        frameless=["丙"],
        tilted=["丁"],
        shifted=[],
    ) == ["すべての欄の範囲が、欄に合っていないおそれがあります"]
    assert forms._field_notes(every, [], [], [], [], []) == []
    # 同じ様式が2枚写っていると同じ名前の欄が2つあり、1枚の欄だけでは「すべての欄」としない
    twice = fields("甲", "乙", "甲", "乙")
    assert forms._field_notes(twice, ["甲", "乙"], [], [], [], []) == [
        "「甲」「乙」の欄の範囲が、欄に合っていないおそれがあります"
    ]
    assert forms._field_notes(
        twice, ["甲", "乙"], [], [], ["甲", "乙", "甲", "乙"], []
    ) == ["すべての欄の範囲が、欄に合っていないおそれがあります"]


def test_review_notes_in_english():
    """英語の画面では、解析の注記を英語にし、欄の名前は "" で囲んで並べる。数で語の形を変える"""
    i18n.set_language(i18n.EN)
    every = {"本人", "配偶者", "親族"}
    assert forms.field_note(["本人"], every) == (
        'The redaction area of the field "本人" may not match the field'
    )
    assert forms.field_note(["本人", "親族"], every) == (
        'The redaction areas of the fields "本人", "親族" may not match the fields'
    )
    assert forms.field_note(["本人", "配偶者", "親族"], every) == (
        "The redaction areas of all fields may not match the fields"
    )
    assert titles.similar_note(["甲", "乙"]) == (
        'The form could not be determined between "甲" and "乙", so the page was redacted with "甲"'
    )
    assert titles.similar_note(["甲", "乙", "丙"]) == (
        'The form could not be determined among "甲", "乙", "丙", so the page was redacted with "甲"'
    )
    assert i18n.tr(forms.UNKNOWN_NOTE) == (
        "The form could not be identified, so this page is not redacted"
    )
    assert i18n.tr(forms.OTHER_PAPER_NOTE) == (
        "Another sheet whose form could not be identified is also in the image, and that sheet is not redacted"
    )
    assert i18n.tr(forms.VARIANT_NOTE, name="甲") == (
        'This sheet is a variation of the form "甲", so the redaction areas may be misaligned'
    )


def test_orientation_of_form_comes_from_template(scan, monkeypatch):
    """申告書のページは、見本との位置合わせで向きを決め、OCR で向きを判定しない。
    見本と合わないページは、OCR で向きを判定する"""
    detect, calls = orientation.detect, []
    monkeypatch.setattr(
        orientation, "detect", lambda gray: calls.append(gray.shape) or detect(gray)
    )
    page = imaging.open_pages(scan("2026bun_04", quarter_turns=3))[0]
    res = pipeline.analyze(page, formset=bundled_forms())
    assert (res.form, res.quarter_turns, res.orientation_confident) == (
        forms.HOKEN,
        1,
        True,
    )
    assert calls == []
    pipeline.analyze(
        imaging.open_pages(scan("2026bun_08"))[0], formset=bundled_forms()
    )  # 従たる給与の申告書
    assert len(calls) == 1


def test_large_skew_is_corrected(analyzed):
    _, res = analyzed("2027bun_01", angle=12)
    assert abs(res.skew + 12) < 0.3
    assert len(res.boxes) == 10


def test_export_masks_and_drops_text(analyzed, tmp_path, monkeypatch):
    # 出力では OCR を使わない
    def no_ocr(*a, **k):
        raise AssertionError("出力で OCR を呼んだ")

    monkeypatch.setattr(ocr, "read", no_ocr)
    page, res = analyzed("2027bun_01", filled=True)
    out = tmp_path / "out.pdf"
    report = export.export([(page, res)], out)
    assert pdfcheck.problems(out, _jobs([(page, res)])) == []
    assert report.masked == 10

    # 本人の欄を解除すると、その欄は墨消ししない
    for b in res.boxes:
        if b.kind == "本人の個人番号":
            b.active = False
    report = export.export([(page, res)], tmp_path / "out2.pdf")
    assert pdfcheck.problems(tmp_path / "out2.pdf", _jobs([(page, res)])) == []
    assert report.masked == 9


def _jobs(pages) -> list[export.PageJob]:
    """出力した各ページの墨消しの依頼（pdfcheck.problems に渡す）"""
    return [export.PageJob.of(src, res) for src, res in pages]


def test_export_in_processes_gives_same_pdf(analyzed, tmp_path):
    """ページを墨消しする処理を2つのプロセスで並べて行っても、順に行ったときと同じ PDF になる"""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    pages = [analyzed("2027bun_01", filled=True), analyzed("2026bun_04", angle=3)] * 2
    one = export.export(pages, tmp_path / "one.pdf")
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(2, mp_context=ctx) as ex:
        two = export.export(
            pages,
            tmp_path / "two.pdf",
            mask_pages=lambda jobs: ex.map(analysis.mask_page, jobs),
        )
    assert (one.pages, one.masked) == (two.pages, two.masked) == (4, 22)
    assert pdfcheck.problems(tmp_path / "one.pdf", _jobs(pages)) == []
    assert pdfcheck.problems(tmp_path / "two.pdf", _jobs(pages)) == []
    a, b = pypdf.PdfReader(tmp_path / "one.pdf"), pypdf.PdfReader(tmp_path / "two.pdf")
    assert [list(p.mediabox) for p in a.pages] == [list(p.mediabox) for p in b.pages]
    for pa, pb in zip(a.pages, b.pages):
        assert _page_images(pa) == _page_images(pb)  # 1ページに画像1枚


@pytest.mark.parametrize(
    "form, spec",
    [
        ("2027bun_01", {"filled": True}),  # グレーのスキャン（色のないページ）
        ("2026bun_04", {"angle": 3}),  # 傾きの大きいもの
        ("2026bun_06", {"quarter_turns": 1}),  # 90度回したもの
        ("2027bun_01", {"kind": "jpeg"}),  # カラーの JPEG（色のあるページ）
        ("2026bun_04", {"kind": "bilevel"}),  # 白黒2値
    ],
)
def test_work_image_is_drawn_again_with_same_pixels(analyzed, form, spec):
    """解析の結果の向きと傾きで描き直した作業画像は、解析のときの作業画像と画素まで同じ（墨消しの画面が、手放した
    ページを表示するときに描き直す）"""
    page, res = analyzed(form, **spec)
    again = pipeline.work_image(page, res.quarter_turns, res.skew)
    assert again.shape == res.work.shape == res.work_shape
    assert np.array_equal(again, res.work)
    assert np.array_equal(analysis.redraw(page, res.quarter_turns, res.skew), res.work)


def test_compact_converts_in_place_and_finds_color_in_any_strip():
    """出力の JPEG にする前の変換：色のあるページは、その場で BGR に並べ替え（新しい画像を作らない）、色のないページは
    グレーにする。色は帯ごとに調べ、最後の帯にだけ色があっても見つける"""
    import cv2

    rng = np.random.default_rng(0)
    gray = rng.integers(0, 256, (export.imaging.STRIP_ROWS * 3 + 7, 50), np.uint8)
    rgb = np.dstack([gray, gray, gray])
    assert np.array_equal(export._compact(rgb.copy()), gray)
    colored = rgb.copy()
    colored[-1, -1] = (200, 0, 0)  # 最後の帯の1画素だけ赤
    want = cv2.cvtColor(colored, cv2.COLOR_RGB2BGR)
    work = colored.copy()
    got = export._compact(work)
    assert np.array_equal(got, want)
    assert np.shares_memory(got, work)  # その場で並べ替えた
    assert export._compact(gray) is gray


def test_page_without_correction_is_masked_from_copied_pixels():
    """回さず傾きも直さない PDF のページも墨消しできる（PDFium が描いた画像は閉じるので、閉じる前にコピーする）"""
    import cv2

    page = imaging.open_pages(DATA / "試験_画像1枚.pdf")[0]
    h, w = imaging.render(page, imaging.WORK_DPI, gray=True).shape
    rect = (100.0, 100.0, 300.0, 200.0)
    image = export.mask_page(export.PageJob(page, 0, 0.0, (h, w), (rect,)))
    out = cv2.imdecode(np.frombuffer(image.jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    full = imaging.render(page, gray=True)
    assert out.shape == full.shape
    x0, y0, x1, y1 = export._scaled(rect, out.shape[1] / w, out.shape[0] / h, out.shape)
    assert out[y0:y1, x0:x1].mean() < 40
    assert (
        abs(float(out[:y0].mean()) - float(full[:y0].mean())) < 3
    )  # 範囲の外は描いたまま


def test_box_with_corners_is_masked_as_polygon():
    """角のある範囲（長方形でない範囲）は、角を結んだ形の中を黒くし、形の外（囲む長方形の隅）は描いたままにする。
    長方形の範囲と同じ数え方で、墨消しした範囲の数に入る"""
    page = imaging.open_pages(DATA / "試験_画像1枚.pdf")[0]
    h, w = imaging.render(page, imaging.WORK_DPI, gray=True).shape
    corners = ((100.0, 140.0), (400.0, 100.0), (400.0, 260.0), (100.0, 300.0))
    job = export.PageJob(page, 0, 0.0, (h, w), (), (corners,))
    image = export.mask_page(job)
    assert image.masked == 1
    out = cv2.imdecode(np.frombuffer(image.jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    full = imaging.render(page, gray=True)
    sx, sy = out.shape[1] / w, out.shape[0] / h
    inside = np.float32([[250, 200], [120, 160], [380, 120], [380, 240], [120, 280]])
    for x, y in inside:
        assert out[int(y * sy), int(x * sx)] < 40, (x, y)
    # 囲む長方形の左上の隅（形の外）は、描いたまま
    y0, y1, x0, x1 = int(100 * sy), int(115 * sy), int(100 * sx), int(130 * sx)
    assert abs(float(out[y0:y1, x0:x1].mean()) - float(full[y0:y1, x0:x1].mean())) < 3
    assert pdfcheck_polygon_black(out, job)


def pdfcheck_polygon_black(out, job) -> bool:
    """出力の画像で、長方形でない範囲の塗る画素（export.polygon_pixels）が黒い（tests/pdfcheck.py の検査と同じ見方）"""
    sx, sy = out.shape[1] / job.work_shape[1], out.shape[0] / job.work_shape[0]
    for points in job.polygons:
        x0, y0, mask = export.polygon_pixels(points, sx, sy, out.shape)
        if out[y0 : y0 + mask.shape[0], x0 : x0 + mask.shape[1]][mask].mean() > 40:
            return False
    return True


def test_polygon_pixels_of_upright_rectangle_match_rectangle():
    """角が長方形に並んだ範囲を塗る画素は、長方形の範囲を塗る画素（外向きに丸めたもの）と同じ"""
    rect = (100.25, 50.5, 300.0, 120.0)
    x, y, w, h = rect
    corners = ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
    for sx, sy in ((1.0, 1.0), (1.5, 1.5)):
        shape = (1000, 1000)
        x0, y0, x1, y1 = export._scaled(rect, sx, sy, shape)
        px, py, mask = export.polygon_pixels(corners, sx, sy, shape)
        assert (px, py, mask.shape) == (x0, y0, (y1 - y0, x1 - x0))
        assert mask.all()


def test_page_job_splits_rectangles_and_polygons_and_checks_corners():
    """墨消しの依頼は、オンの長方形の範囲と角のある範囲を分けて持つ。角が3つ未満か、有限の数でない角があれば止める"""
    corners = ((10.0, 20.0), (110.0, 15.0), (112.0, 45.0), (12.0, 50.0))
    res = SimpleNamespace(
        boxes=[
            Box(1, 2, 30, 40, "手で追加した範囲", Source.MANUAL),
            Box(0, 0, 0, 0, "本人の個人番号", Source.AUTO, corners=corners),
            Box(0, 0, 0, 0, "配偶者", Source.AUTO, active=False, corners=corners),
        ],
        quarter_turns=0,
        skew=0.0,
        work_shape=(100, 200),
    )
    job = export.PageJob.of(None, res)
    assert job.rects == ((1, 2, 30, 40),) and job.polygons == (corners,)
    for bad in (((0, 0), (5, 5)), ((0, 0), (5, float("nan")), (5, 9))):
        res.boxes = [Box(0, 0, 9, 9, "本人の個人番号", Source.AUTO)]
        res.boxes[0].corners = tuple(bad)
        with pytest.raises(ValueError):
            export.PageJob.of(None, res)


def _pdfium_reference(pages, path: Path) -> None:
    """前の出力の書き方（PDFium の文書に全ページを足してから保存する）で、同じ墨消しのページを書く（比べるため）"""
    import io

    pdf = pdfium.PdfDocument.new()
    for src, res in pages:
        image = export.mask_page(export.PageJob.of(src, res))
        width, height = image.width * 72 / src.dpi, image.height * 72 / src.dpi
        page = pdf.new_page(width, height)
        obj = pdfium.PdfImage.new(pdf)
        obj.load_jpeg(io.BytesIO(image.jpeg), inline=True)
        obj.set_matrix(pdfium.PdfMatrix().scale(width, height))
        page.insert_obj(obj)
        page.gen_content()
        page.close()
    pdf.save(path)
    pdf.close()


def test_output_written_page_by_page_matches_previous_layout(analyzed, tmp_path):
    """1ページずつ書き足した出力は、前の書き方（PDFium）と、ページの数・大きさ・向き・貼った画像（JPEG のまま）・
    画像の画素の数と色が同じ。PDFium と pypdf のどちらでも読める"""
    color = imaging.open_pages(DATA / "公開様式_2026bun_04_232dpi_カラー.png")[0]
    pages = [
        analyzed("2027bun_01", filled=True),
        analyzed("2026bun_04", angle=3),
        (color, pipeline.analyze(color, formset=bundled_forms())),
    ]
    export.export(pages, tmp_path / "new.pdf")
    _pdfium_reference(pages, tmp_path / "old.pdf")
    new, old = (
        pypdf.PdfReader(tmp_path / "new.pdf"),
        pypdf.PdfReader(tmp_path / "old.pdf"),
    )
    assert len(new.pages) == len(old.pages) == 3
    for pn, po in zip(new.pages, old.pages):
        assert [float(v) for v in pn.mediabox] == pytest.approx(
            [float(v) for v in po.mediabox], abs=0.001
        )
        assert pn.rotation == po.rotation == 0
        assert _page_images(pn) == _page_images(po)
        (xn,) = pn["/Resources"]["/XObject"].values()
        (xo,) = po["/Resources"]["/XObject"].values()
        for key in ("/Width", "/Height", "/ColorSpace", "/BitsPerComponent", "/Filter"):
            assert xn.get_object()[key] == xo.get_object()[key], key
    sizes = []
    for name in ("new.pdf", "old.pdf"):
        with imaging.PDF_LOCK, imaging.open_pdf(tmp_path / name) as pdf:
            sizes.append([v for k in range(len(pdf)) for v in pdf[k].get_size()])
    assert sizes[0] == pytest.approx(sizes[1], abs=0.001)
    assert pdfcheck.problems(tmp_path / "new.pdf", _jobs(pages)) == []


def _page_images(page) -> list[bytes]:
    """pypdf で読んだページに貼られた画像の XObject の中身（JPEG のまま）"""
    xobjects = page["/Resources"].get("/XObject", {})
    return [
        v.get_object().get_data()
        for v in xobjects.values()
        if v.get_object().get("/Subtype") == "/Image"
    ]


def test_export_saves_under_temporary_name_then_renames(
    analyzed, tmp_path, monkeypatch
):
    """出力先と同じフォルダに一時的な名前で保存し、保存が終わってから正式な名前にする。
    進み具合の数はページの数で、出力後の検査はしない"""
    page, res = analyzed("2027bun_01", filled=True)
    renamed = []
    real = os.replace
    monkeypatch.setattr(
        os, "replace", lambda a, b: renamed.append((Path(a), Path(b))) or real(a, b)
    )
    steps = []
    out = tmp_path / "a_redacted.pdf"
    export.export([(page, res)], out, lambda d, n, m: steps.append((d, n, m)))
    ((src, dst),) = renamed
    assert src.parent == tmp_path and dst == out
    assert src.name.startswith(".a_redacted.pdf.") and src.suffix == ".tmp"
    assert [p.name for p in tmp_path.iterdir()] == [out.name]
    assert steps[-1] == (1, 1, "出力しました")
    assert not any("検査" in m for _, _, m in steps)


@pytest.mark.parametrize("where", ["save", "before_replace", "replace"])
def test_failed_export_leaves_no_file_and_keeps_previous(
    where, analyzed, tmp_path, monkeypatch
):
    """保存の途中・正式な名前にする前・置き換えで失敗すると、一時的な名前のファイルを消し、前からある同じ名前の
    ファイルは変えない"""
    page, res = analyzed("2027bun_01", filled=True)
    out = tmp_path / "a_redacted.pdf"
    out.write_bytes(b"previous")
    before = None
    expected = export.ExportError
    if where == "save":

        def fail(self, *a, **k):
            raise OSError("ディスクがいっぱい")

        monkeypatch.setattr(export.PdfWriter, "close", fail)
        expected = OSError
    elif where == "before_replace":

        def before():
            raise export.ExportError(export.CHANGED)
    else:

        def deny(a, b):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(os, "replace", deny)
    with pytest.raises(expected) as e:
        export.export([(page, res)], out, before_replace=before)
    assert out.read_bytes() == b"previous"
    assert [p.name for p in tmp_path.iterdir()] == [out.name]
    if where == "replace":
        assert str(e.value).startswith(
            "出力先の同じ名前のファイルを、ほかのソフトが開いている可能性があります"
        )


def test_field_outside_image_keeps_moved_position(tmp_path):
    """帳票の下が切れて写り、欄が画像の外に出ても、ページの解析は失敗せず、写っていない欄は移した位置のまま
    要確認で名前を挙げる"""
    from PIL import Image

    img = page_image("2027bun_01")
    cut = img[: round(img.shape[0] * 0.7)]  # 下の30%が写っていない
    path = tmp_path / "cut.png"
    Image.fromarray(cut).save(path, dpi=(200, 200))
    res = pipeline.analyze(imaging.open_pages(path)[0], formset=bundled_forms())
    assert (res.form, len(res.boxes)) == EXPECTED["2027bun_01"]
    outside = [b.kind for b in res.boxes if b.y >= cut.shape[0]]
    assert outside and set(outside) <= set(res.unframed)
    assert all(b.w > 0 and b.h > 0 for b in res.boxes)
    assert all(f"「{k}」" in res.note for k in outside)


def test_failure_in_one_field_keeps_moved_position(analyzed, monkeypatch):
    """1つの欄で線を探す処理が失敗しても、その欄は移した位置のまま墨消しして要確認にし、ほかの欄は今までどおり。
    このページは、枠の線が見つからない欄があるページとして罫線の対応に進む。罫線の対応で置いて線が見つかれば
    置き直され、置けなければ、移した位置のまま残る"""
    page, expected = analyzed("2026bun_04")
    real = forms.framed

    def fail_once(gray, f, H, pairs, template=None, **kwargs):
        if f.name == expected.boxes[0].kind:
            raise IndexError("線を探す処理の失敗")
        return real(gray, f, H, pairs, template, **kwargs)

    monkeypatch.setattr(forms, "framed", fail_once)
    replaced = pipeline.analyze(page, formset=bundled_forms())
    assert replaced.variant and replaced.unframed == ()
    assert len(replaced.boxes) == len(expected.boxes)
    monkeypatch.setattr(variants, "relocate", lambda gray, spec: None)
    res = pipeline.analyze(page, formset=bundled_forms())
    assert not res.variant
    assert len(res.boxes) == len(expected.boxes)
    assert res.unframed == (expected.boxes[0].kind,)
    assert res.boxes[0].w > 0 and res.boxes[0].h > 0


def test_collapsed_pair_falls_back_to_moved_position(monkeypatch):
    """線に合わせた欄の幅か高さが1画素未満になった組は、線が見つからなかったものとし、移した位置に戻す"""
    gray = np.full((300, 600), 255, np.uint8)
    f = formstore.Field("小さな欄", 100, 100, 200, 10, (formstore.TOP_BOTTOM,))
    collapsed = frames_mod.Frame((105, 103), (103, 105), None, None, ("top_bottom",))
    monkeypatch.setattr(frames_mod, "find", lambda *a, **k: collapsed)
    field, frame, *_ = forms.framed(gray, f, np.eye(3), f.frame)
    assert (field.y, field.h) == (100, 10)
    assert frame.missing == ("top", "bottom") and field.frame == ()


def test_export_refuses_range_without_size(analyzed, tmp_path):
    """幅か高さが0以下の範囲は、黙って塗らずに出力を失敗にする"""
    page, res = analyzed("2026bun_04")
    res.boxes[0].h = 0
    with pytest.raises(ValueError, match="墨消しする範囲の大きさが正しくありません"):
        export.export([(page, res)], tmp_path / "out.pdf")
    assert list(tmp_path.iterdir()) == []


def test_ocr_uses_bundled_models_without_downloading(monkeypatch, tmp_path):
    """RapidOCR には同梱のモデルの場所を渡し、モデルを取り直す処理（インターネットに接続する）を通らない。モデルの
    ファイルがなければ、取り直さずにエラーにする"""
    from rapidocr.utils import download_file

    def no_download(*a, **k):
        raise AssertionError("モデルを取り直そうとした")

    monkeypatch.setattr(download_file.DownloadFile, "run", no_download)
    ocr._engine.cache_clear()
    gray = page_image("2026bun_06")[:400]
    text = "".join(t.text for t in ocr.read(gray, upright=True).texts)
    assert "基礎控除申告書" in text
    with pytest.raises(ocr.ModelError, match="OCR のモデルのファイルがありません"):
        ocr.model_params(tmp_path)


def test_template_image_is_remembered_with_revision(tmp_path):
    """見本の画像は、パスに加えて版と保存した日時・保存した人で覚える。同じ名前で書き直された画像を、前の画像のまま
    使わない"""
    import shutil

    write_form(tmp_path, "u-a", "様式A", base="nta-hoken-r8")
    spec = formstore.read_spec(tmp_path / "u-a.json")
    before = align._template_image(spec)[0].copy()
    kihai = bundled("nta-kihai-r8")
    shutil.copy(kihai.image_path(), tmp_path / spec.image)  # 同じ名前で別の画像を書く
    assert np.array_equal(
        align._template_image(spec)[0], before
    )  # 同じ様式の版なら覚えたまま
    newer = dataclasses.replace(spec, revision=2, updated_at="2026-11-08T12:00:00")
    assert not np.array_equal(align._template_image(newer)[0], before)


def test_check_output_tool_accepts_output_of_sample(tmp_path):
    """tools/check_output.py は、試験用の画像を何も変えずに出力した PDF を「問題なし」とし、墨消しのない PDF は
    「墨消しできていない範囲」とする（Windows で出力した PDF を確かめるため）"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import check_output

    (page,) = imaging.open_pages(check_output.SAMPLE)
    res = pipeline.analyze(page, formset=bundled_forms())
    # 傾きの補正も回転もない画像（傾けずに描いた試験用の画像）を出力できる
    assert (res.quarter_turns, abs(res.skew) < 0.05) == (0, True)
    export.export([(page, res)], tmp_path / "ok.pdf")
    assert check_output.check(tmp_path / "ok.pdf") == []
    for b in res.boxes:
        b.active = False
    export.export([(page, res)], tmp_path / "none.pdf")
    assert "墨消しできていない範囲（1ページ）" in check_output.check(
        tmp_path / "none.pdf"
    )


@pytest.mark.parametrize("kind", ["jpeg_72dpi", "pdf_page_in_pixels"])
def test_implausible_resolution_is_treated_as_a4(kind, scan, tmp_path):
    """画像を保存するソフトが既定で書き込む 72dpi などは、A4 とみなして扱う"""
    import zlib

    from PIL import Image

    rgb = imaging.render(imaging.open_pages(scan("2027bun_01"))[0], dpi=200)
    if kind == "jpeg_72dpi":
        path = tmp_path / "scan.jpg"
        Image.fromarray(rgb).save(path, dpi=(72, 72), quality=90)
    else:  # ページの大きさが画素数そのまま（1画素を1ポイント）の PDF
        path = tmp_path / "pixels.pdf"
        h, w = rgb.shape[:2]
        entries = "/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode"
        _image_pdf(path, w, h, 72, entries, zlib.compress(rgb.tobytes()))
    page = imaging.open_pages(path)[0]
    assert 190 < page.dpi < 210
    res = pipeline.analyze(page, formset=bundled_forms())
    assert max(res.work.shape) < 2600
    assert len(res.boxes) == 10


MARK = "SECRET1234"  # 検査用の PDF（tests/data/試験_文字のデータ9種類.pdf）の各所に入れた目印の文字列


def test_no_text_data_is_left_in_output(tmp_path):
    """入力のどこに文字のデータがあっても、出力は画像だけのPDFになる"""
    src = DATA / "試験_文字のデータ9種類.pdf"
    # 入力には9種類とも入っている（文字を書いたので、画像のほかの部品もある）
    assert pdfcheck.text_left_in(src) == pdfcheck.KINDS[:9] + ["画像のほかの部品"]
    page = imaging.open_pages(src)[0]
    res = pipeline.analyze(page, formset=bundled_forms())
    export.export([(page, res)], tmp_path / "out.pdf")
    assert pdfcheck.problems(tmp_path / "out.pdf", _jobs([(page, res)])) == []
    raw = (tmp_path / "out.pdf").read_bytes()
    # 出力は、作ったライブラリ（PDFium）と別のライブラリ（pypdf）でも確かめる
    reader = pypdf.PdfReader(tmp_path / "out.pdf")
    (out_page,) = reader.pages
    xobjects = out_page["/Resources"]["/XObject"]
    assert (
        len(xobjects) == 1 and len(_page_images(out_page)) == 1
    )  # 画像の XObject 1枚だけ
    ops = [op for _, op in ContentStream(out_page.get_contents(), reader).operations]
    assert b"Do" in ops and b"INLINE IMAGE" not in ops  # インライン画像ではない
    assert out_page.extract_text() == "" and "/Annots" not in out_page
    assert not reader.get_fields() and not reader.outline and not reader.attachments
    # 目録に XMP・OpenAction・AcroForm などがない
    assert sorted(reader.trailer["/Root"].keys()) == ["/Pages", "/Type"]
    # 文書の情報（作ったソフトや日時）を書かない
    assert reader.metadata is None and "/Info" not in reader.trailer
    streams = b"".join(
        obj.get_data()
        for i in range(1, int(reader.trailer["/Size"]))
        if isinstance(obj := reader.get_object(i), pypdf.generic.StreamObject)
    )
    # 目印の文字列は、ファイルにも、圧縮を戻した中身にも残らない
    assert MARK.encode() not in raw and MARK.encode() not in streams


def no_title(*args):
    raise AssertionError("題名を読んだ")


def test_title_is_not_read_when_a_template_matches(monkeypatch):
    """配布用パッケージの3様式だけなら、同じ用紙の部分に2つ以上の様式は成り立たない（類似した様式は1つ）ので、見本と位置合わせ
    が成り立つページは、題名を読まず（見本の上の帯も読まない）に見本の種類で墨消しし、要確認にしない"""
    monkeypatch.setattr(titles, "read_title", no_title)
    monkeypatch.setattr(titles, "_band_title", no_title)
    for form, (kind, n) in EXPECTED.items():
        res = forms.locate(page_image(form), ocr.Engine.RAPIDOCR, bundled_forms())
        assert (res.form, len(res.fields), res.note, len(res.similar)) == (
            kind,
            n,
            "",
            1,
        ), form


# どの様式とも位置合わせできない、題名のあるページ。見本のスキャンの4ページ目（住宅借入金等特別控除申告書の
# 記入例）のほかに、見本がないリポジトリでも確かめられるように、従たる給与についての扶養控除等申告書を使う
UNMATCHED = [pytest.param("sample-page4", marks=needs_sample_scan), "2026bun_08"]


def unmatched_page(which: str, scan) -> tuple[imaging.SourcePage, str]:
    """どの様式とも位置合わせできないページと、その題名にあって OCR で読める語"""
    if which == "sample-page4":
        return imaging.open_pages(SAMPLE_SCAN)[3], "住宅借入金等特別控除申告書"
    return imaging.open_pages(scan(which))[0], "従たる給与についての扶養控除等"


@pytest.mark.parametrize("which", UNMATCHED)
def test_title_is_not_read_when_no_template_matches(which, scan, tmp_path, monkeypatch):
    """どの見本とも位置合わせが成り立たないページは、題名を読まずに様式不明とし、墨消しせずに要確認とする。その
    ページの題名にある語を題名の語に持つ利用者の様式があっても、同じ"""
    page, title = unmatched_page(which, scan)
    write_form(tmp_path, "u-0005", title, base="nta-kihai-r8", title_words=[title])
    loaded = formstore.load(formstore.FolderChoice(tmp_path))
    monkeypatch.setattr(titles, "read_title", no_title)
    monkeypatch.setattr(titles, "_band_title", no_title)
    res = pipeline.analyze(page, formset=(*bundled_forms(), *loaded.active))
    assert res.form == formstore.UNKNOWN and res.boxes == []
    assert res.note == forms.UNKNOWN_NOTE


@pytest.mark.parametrize("bilevel", [False, True], ids=["gray", "bilevel"])
@pytest.mark.parametrize("blank_rows", [0, 56], ids=["as-is", "title-first"])
def test_long_title_is_read(bilevel, blank_rows):
    """基・配・特・所の申告書の長い題名を読む（範囲を描く画面の「題名を読む」で使う）

    RapidOCR の行ごとの向きの判定をすると、長い題名の行を180度と取り違えて逆さに読むことがあるので、向きの判定を
    せずに題名の語を読めることを確かめる。
    blank_rows は、題名より上の印字（右上の「年末調整用」の枠）を消す行の数。56 なら、題名の文字の上端が
    印字の上端になる（令和7年分の実際のスキャンと同じ形）。
    """
    gray = page_image("2026bun_06", bilevel=bilevel)
    gray[:blank_rows] = 255
    text = titles.read_title(gray, ocr.Engine.RAPIDOCR)
    assert titles.title_choice(text, bundled_forms()).name == forms.KIHAI
    assert "令和8年分" in formstore.title_key(text)


def test_read_title_narrows_width_only_in_photos(monkeypatch):
    """題名を読むとき、読む範囲の横を印字のある範囲に狭めるのは、用紙の周りが写っている写真だけ。スキャンは
    ページの幅のまま OCR に渡す（スキャンでも狭める誤りがあっても、ほかのテストは失敗しないため）。
    画像はメモリの中だけで作る"""
    widths = []

    def read(image, engine, **kw):
        widths.append(image.shape[1])
        return SimpleNamespace(texts=[])

    monkeypatch.setattr(ocr, "read", read)
    scan = page_image("2027bun_01")
    assert paper.paper_region(scan) is None
    titles.read_title(scan, ocr.Engine.RAPIDOCR)
    # 机に見立てた暗い地の上に、同じページを置いた写真
    h, w = scan.shape
    photo = np.full((int(h * 1.3), int(w * 1.6)), 70, np.uint8)
    y0, x0 = (photo.shape[0] - h) // 2, (photo.shape[1] - w) // 2
    photo[y0 : y0 + h, x0 : x0 + w] = scan
    assert paper.paper_region(photo) is not None
    titles.read_title(photo, ocr.Engine.RAPIDOCR)
    assert widths[0] == w
    assert widths[1] < photo.shape[1] and widths[1] <= w


def test_edge_shadow_of_scan_is_not_other_paper(tmp_path):
    """用紙1枚のスキャンで、画像の端に沿った細い線（用紙の縁の影）を、ほかの用紙の印字とみなさない（様式を判別
    できない用紙も写っているという要確認を付けない。paper.EDGE_IGNORE）"""
    from PIL import Image

    form = page_image("2026bun_06")
    h, w = form.shape
    m = 140  # 白い余白。申告書が画像の9割より狭くなり、ほかの用紙が写っていないかを調べる
    img = np.full((h + 2 * m, w + 2 * m), 255, np.uint8)
    img[m : m + h, m : m + w] = form
    img[40:-40, 18:24] = 70  # 左の端に沿った縦の影
    img[-26:-20, 200:-200] = 70  # 下の端に沿った横の影
    path = tmp_path / "scan.png"
    Image.fromarray(img).save(path, dpi=(GRAY_DPI, GRAY_DPI))
    res = pipeline.analyze(imaging.open_pages(path)[0], formset=bundled_forms())
    assert res.form == forms.KIHAI
    assert "様式を判別できない用紙" not in res.note


def test_certificates_are_not_forms(tmp_path):
    """控除証明書（架空の内容）は申告書と判別せず、墨消ししない。どの様式の見本にも類似しないので、様式不明として
    要確認にする

    生命保険料の控除証明書には「保険料控除」の語があるが、保険料控除申告書と判別しない（地震保険料・国民年金
    保険料の控除証明書も、向きを決めるときにどの様式とも位置合わせできず、同じく様式不明になる）。
    """
    src = make_certificate_scan("生命保険", tmp_path / "cert.pdf")
    res = pipeline.analyze(imaging.open_pages(src)[0], formset=bundled_forms())
    assert res.form == formstore.UNKNOWN
    assert res.boxes == [] and res.note == forms.UNKNOWN_NOTE


@pytest.mark.parametrize("angle", [16, 20])
def test_scan_tilted_beyond_skew_limit_needs_review(angle, scan):
    """大きく傾いた（16度・20度）スキャンは、見つけた線分がどれも MAX_SKEW_DEG（15度）以上の角度なので傾きを直さず、
    傾いたまま位置合わせするので、欄の枠の線が見つからない。どの欄も要確認で名指しし、記入した番号を、要確認なしで
    墨消しの外に残さない（記入のある扶養控除等申告書）"""
    blank = imaging.open_pages(scan("2027bun_01", angle=angle))[0]
    filled = imaging.open_pages(scan("2027bun_01", angle=angle, filled=True))[0]
    res = cached_analyze(filled)
    assert (res.form, res.skew) == (forms.FUYOU, 0)
    assert res.note and set(res.unframed) == {b.kind for b in res.boxes}
    left, digits, _ = left_digits(res, filled, blank)
    assert digits > 0 and left.sum() == 0


def test_blank_image_has_no_text_lines():
    """文字の行がない画像（白紙）では、向きを決めるのに使う行がない（OCR が行の枠を返さない）"""
    blank = np.full((800, 600), 255, np.uint8)
    assert orientation._line_shapes(blank) == ([], 0, 0)


def test_pymupdf_is_not_a_dependency():
    """ツールは PDF を pypdfium2 で扱い、PyMuPDF（GNU AGPL 3.0）は、ツール本体にも開発用の依存にも入れない。uv.lock に
    PyMuPDF の項目（pymupdf・fitz など）がない"""
    lock = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "uv.lock").read_text("utf-8")
    )
    names = {p["name"] for p in lock["package"]}
    assert not {n for n in names if n.startswith(("pymupdf", "fitz"))}
    assert "pypdfium2" in names


def test_unreadable_structure_is_reported():
    """相互参照ストリームの PDF は、ファイルの中の辞書を調べられないので「検査できなかった構造」と示す"""
    assert "検査できなかった構造" in pdfcheck.text_left_in(
        DATA / "試験_相互参照ストリーム.pdf"
    )


@pytest.mark.parametrize("call", ["open_pages", "render", "export"])
def test_pdf_calls_wait_for_pdf_lock(call, analyzed, tmp_path):
    """PDF のライブラリを使う処理は imaging.PDF_LOCK の中で1つずつ動く（PDFium は同時に呼べないため）"""
    page, res = analyzed("2027bun_01")
    calls = {
        "open_pages": lambda: imaging.open_pages(page.path),
        "render": lambda: imaging.render(page, dpi=100),
        "export": lambda: export.export([(page, res)], tmp_path / "out.pdf"),
    }
    done = threading.Event()

    def run():
        calls[call]()
        done.set()

    with imaging.PDF_LOCK:
        th = threading.Thread(target=run)
        th.start()
        # ロックを持っているあいだは終わらない
        assert not done.wait(1.0)
    th.join(120)
    assert done.is_set()


@needs_sample_scan
def test_sample_pdf_resolution():
    """見本のスキャンの4ページの解像度（貼られた画像から求める）"""
    pages = imaging.open_pages(SAMPLE_SCAN)
    assert [round(p.dpi) for p in pages] == [232] * 4


# ---------------------------------------------------------------- 利用者の様式


def test_edited_and_registered_forms_are_used(scan, tmp_path):
    """様式のフォルダの様式で判別し、その範囲を墨消しする。編集した様式は、編集した範囲を使う"""
    page = imaging.open_pages(scan("2027bun_01", angle=3))[0]
    install_bundled(tmp_path)
    # 配布用パッケージの扶養控除等申告書を編集したもの（範囲を3つにした）
    write_form(tmp_path, "nta-fuyou-r9", forms.FUYOU, fields=3, revision=2, frame=True)
    loaded = formstore.load(formstore.FolderChoice(tmp_path))
    res = pipeline.analyze(page, formset=loaded.active)
    assert res.form == forms.FUYOU and len(res.boxes) == 3 and res.note == ""
    assert [(s.id, s.revision, s.folder) for s in res.used] == [
        ("nta-fuyou-r9", 2, str(tmp_path))
    ]
    # 同じ見本の画像の、画面から登録した様式（扶養控除等申告書は削除した）
    (tmp_path / "nta-fuyou-r9.json").unlink()
    write_form(tmp_path, "u-0001", "登録した扶養控除等申告書", fields=2)
    loaded = formstore.load(formstore.FolderChoice(tmp_path))
    res = pipeline.analyze(page, formset=loaded.active)
    assert res.form == "登録した扶養控除等申告書" and res.note == ""
    assert [b.kind for b in res.boxes] == ["支払者の法人（個人）番号", "本人の個人番号"]
    assert [s.id for s in res.used] == ["u-0001"]


def test_unreadable_template_image_raises_form_error(scan, tmp_path):
    """解析の途中で見本の画像を読めなくなったら、ほかの様式だけで続けずにエラーにする"""
    page = imaging.open_pages(scan("2026bun_04", angle=3))[0]
    write_form(tmp_path, "u-0002", "消えた様式")
    (spec,) = formstore.load(formstore.FolderChoice(tmp_path)).active
    (tmp_path / spec.image).unlink()
    with pytest.raises(
        formstore.FormError, match="様式「消えた様式」の見本の画像を読めません"
    ):
        pipeline.analyze(page, formset=(*bundled_forms(), spec))


def test_unused_form_is_not_used_for_alignment(scan, tmp_path):
    """この端末で使わない様式は、見本との位置合わせに使わない。その帳票のページは様式不明になり、
    要確認にする"""
    page = imaging.open_pages(scan("2026bun_04", angle=3))[0]
    install_bundled(tmp_path)
    loaded = formstore.load(formstore.FolderChoice(tmp_path), {"nta-hoken-r8"})
    res = pipeline.analyze(page, formset=loaded.active)
    assert res.form == formstore.UNKNOWN and res.boxes == []
    assert res.note == forms.UNKNOWN_NOTE


def test_form_aligned_for_orientation_is_tried_first(scan, monkeypatch):
    """向きを決めるときに位置合わせが成り立った様式を、範囲を求めるときに先に試し、成り立てば、ほかの様式とは
    位置合わせしない。全部の様式と位置合わせしたときと同じ結果になる"""
    page = imaging.open_pages(scan("2026bun_04", angle=3))[0]
    calls = []
    real = align.register
    monkeypatch.setattr(
        align, "register", lambda tpl, f: calls.append(tpl.name) or real(tpl, f)
    )
    res = pipeline.analyze(page, formset=bundled_forms())
    # 向きを決めるときに全部の様式と位置合わせした後、範囲を求めるときは保険料控除申告書だけ
    assert calls[len(bundled_forms()) :] == [forms.HOKEN]
    full = forms.locate(
        res.work, ocr.Engine.RAPIDOCR, bundled_forms()
    )  # 先に試す様式を指定しない
    assert full.form == res.form
    assert [(f.x, f.y, f.w, f.h) for f in full.fields] == [b.rect() for b in res.boxes]


def test_frame_record_decides_which_lines_to_fit(scan):
    """範囲の枠の線の記録（frame）の組だけ、印刷された枠の線に合わせる。記録のない範囲（前のフォーマット）は、
    配布用パッケージの様式の欄と同じ名前でも、線に合わせない"""
    tb, lr = formstore.TOP_BOTTOM, formstore.LEFT_RIGHT
    page = imaging.open_pages(scan("2027bun_01"))[0]
    # 配布用パッケージの扶養控除等申告書の「A 源泉控除対象配偶者」（上下・左右の線に合わせる）
    ref = cached_analyze(page).boxes[2]
    base = bundled("nta-fuyou-r9")
    f = base.fields[2]
    wide = (f.x - 4, f.y - 4, f.w + 8, f.h + 8)  # 枠の線にかかる、外へ4画素広い範囲
    spec = dataclasses.replace(
        base,
        fields=(
            formstore.Field("枠なし", *wide, ()),
            formstore.Field("上下だけ", *wide, (tb,)),
            formstore.Field(
                f.name, *wide, None
            ),  # 記録なしで、配布用パッケージの様式の欄と同じ名前
            formstore.Field("両方", *wide, (tb, lr)),
        ),
    )
    res = pipeline.analyze(page, formset=[spec])
    assert res.unframed == ()
    none, top_bottom, same, both = res.boxes

    def edges(b):
        return (b.x, b.y, b.x + b.w, b.y + b.h)

    want = edges(ref)
    # 線の縁は、線を探す帯（欄の幅・高さの両端を除いた所）の濃さで決めるので、範囲の大きさで1画素違うことがある
    assert np.allclose(edges(both), want, atol=1.0)  # 上下・左右とも線に合わせる
    for b in (
        none,
        same,
    ):  # 線に合わせず、範囲のまま（線に合わせた範囲より、4辺とも外）
        left, top, right, bottom = edges(b)
        assert left < want[0] - 2 and top < want[1] - 2
        assert right > want[2] + 2 and bottom > want[3] + 2
    left, top, right, bottom = edges(top_bottom)  # 上下だけ線に合わせる
    assert np.allclose((top, bottom), (want[1], want[3]), atol=1.0)
    assert left < want[0] - 2 and right > want[2] + 2


def test_received_result_shares_form_objects():
    """解析のプロセスから受け取った結果（様式の写しを持つ）の used・similar を、使う様式の組の同じ版の様式の
    オブジェクトに置き換える（見本の罫線を持つ様式の写しを、ページごとに持たないため）。組にない様式はそのまま"""
    import pickle

    specs = bundled_forms()
    hoken, fuyou = bundled("nta-hoken-r8"), bundled("nta-fuyou-r9")
    other = dataclasses.replace(fuyou, id="u-other", updated_by="試験")
    res = pipeline.PageResult(
        quarter_turns=0,
        skew=0.0,
        orientation_confident=True,
        work=None,
        boxes=[],
        form=hoken.name,
        used=(hoken, other),
        similar=(fuyou,),
        shape=(10, 10),
    )
    copy = pickle.loads(pickle.dumps(res))
    assert copy.used[0] is not hoken
    shared = copy.sharing(specs)
    assert shared.used[0] is next(s for s in specs if s.id == "nta-hoken-r8")
    assert shared.similar[0] is next(s for s in specs if s.id == "nta-fuyou-r9")
    assert shared.used[1] == other and shared.used[1] is copy.used[1]


def test_collapsed_transform_is_not_accepted(tmp_path):
    """手がかりの少ない様式で、整合した点が題名のあたりに集まると、見本の四隅を移した形が四角形にならない変換が
    求まることがある（面積の比は範囲の中に入る）。その変換では位置合わせが成り立たないとし、範囲を違う大きさ・違う
    場所に置かない。白黒2値（しきい値160）のスキャンに見立てたページで確かめる"""
    work = simple_form()
    small = formcheck.make_template(work, True, [SIMPLE_FIELD])
    _, png = cv2.imencode(".png", small)
    field = formstore.Field("範囲1", *SIMPLE_FIELD, formstore.FRAME_PAIRS)
    spec = formstore.FormSpec(
        "u-simple", "線と題名だけの様式", "", (1653, 2339), (field,)
    )
    formsave.save(tmp_path, spec, png.tobytes(), formsave.OP_REGISTER, "試験")
    specs = formstore.load(formstore.FolderChoice(tmp_path)).active
    big = cv2.resize(work, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    page = imaging.open_pages(_scan_bilevel(big, tmp_path / "page.pdf", 0.8, 0, 160))[0]
    res = pipeline.analyze(page, formset=specs)
    # 判別できるなら範囲は1つで、大きさは欄の大きさ（判別できなければ様式不明で、範囲はない）
    assert len(res.boxes) <= 1
    for b in res.boxes:
        assert abs(b.w - SIMPLE_FIELD[2]) <= 40 and abs(b.h - SIMPLE_FIELD[3]) <= 20
    # つぶれた変換そのもの：見本の四隅を移した形（この様式で求まった値）は、用紙として成り立たない
    tpl = align.templates(specs)[0]
    feats = align.page_features(res.work)
    H, _ = align.register(tpl, feats)
    assert H is None or align.plausible(tpl, H)


def test_plausible_transform_keeps_convex_quad_and_orientation():
    """見本の四隅を移した形で、位置合わせの変換が用紙として成り立つかを決める。回転・縮小・台形（写真）は成り立ち、
    裏返し・四隅が交差する形・消失線をまたぐ変換・小さすぎる形は成り立たない"""
    tpl = align.templates([bundled("nta-fuyou-r9")])[0]
    th, tw = tpl.image.shape
    w, h = tw / formstore.TEMPLATE_SCALE, th / formstore.TEMPLATE_SCALE
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])

    def to(dst):
        return cv2.getPerspectiveTransform(src, np.float32(dst))

    assert align.plausible(tpl, np.eye(3))
    assert align.plausible(tpl, to([[w, 0], [w, h], [0, h], [0, 0]]))  # 90度回した形
    assert align.plausible(
        tpl, to([[0.1 * w, 0], [0.9 * w, 0], [w, h], [0, h]])
    )  # 台形
    assert align.plausible(
        tpl, to([[0, 0], [0.5 * w, 0], [0.5 * w, 0.5 * h], [0, 0.5 * h]])
    )
    assert not align.plausible(tpl, to([[w, 0], [0, 0], [0, h], [w, h]]))  # 裏返し
    assert not align.plausible(
        tpl, to([[0, 0], [w, 0], [0, h], [w, h]])
    )  # 辺が交差する
    assert not align.plausible(
        tpl, to([[0, 0], [0.3 * w, 0], [0.3 * w, 0.3 * h], [0, 0.3 * h]])
    )  # 面積の比が下限より小さい
    vanishing = np.float64([[1, 0, 0], [0, 1, 0], [-2 / w, 0, 1]])  # x = w/2 が消失線
    assert not align.plausible(tpl, vanishing)
