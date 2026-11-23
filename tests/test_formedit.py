# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""様式の登録・編集で、解析のプロセスで動かす処理のテスト

見本の画像を作る処理、保存の前に確かめる処理、見本の画像を置き換えるときに範囲を移す処理と、登録した様式での判別。
画面の操作は test_forms_screens.py で確かめる。
"""

import dataclasses

import cv2
import numpy as np
import pytest
from field_coverage import assert_covered
from PIL import Image
from scan import (
    SIMPLE_FIELD,
    data_image,
    page_image,
    scan_transform,
    simple_form,
)
from userforms import bundled, bundled_forms

from sumikeshi_studio import (
    align,
    analysis,
    formcheck,
    forms,
    formsave,
    formstext,
    formstore,
    imaging,
    pipeline,
    rulings,
    variants,
)
from sumikeshi_studio.boxes import Box, Source

NAME = "従たる給与の申告書（独自）"
FIELD = (1074, 183, 410, 59)  # 登録する墨消しする範囲（作業画像の座標）
ERASE = (300, 300, 500, 120)  # 見本の画像から消す範囲


@pytest.fixture(scope="module")
def juutaru_page(tmp_path_factory):
    """傾けずに画像にした従たる給与の申告書（作業画像の座標が、ページの画像の座標と同じになる）"""
    img = page_image("2026bun_08")
    path = tmp_path_factory.mktemp("juutaru") / "page.png"
    Image.fromarray(img).save(path, dpi=(200, 200))
    res = pipeline.analyze(imaging.open_pages(path)[0], formset=bundled_forms())
    assert res.form == formstore.UNKNOWN and res.quarter_turns == 0
    assert abs(res.skew) < 0.05
    return img, res.work


def test_lines_read_for_default_limit_are_reused_when_saving(juutaru_page, monkeypatch):
    """登録で、罫線の一致度の下限の初めの値を求めるために読んだ罫線を、保存の前の確認に渡すと、同じ画像から罫線を
    読み直さない（確認の結果の罫線は、読み直したときと同じ）"""
    _, work = juutaru_page
    others = list(bundled_forms())
    limit, lines = analysis.line_match_default(work, others)
    assert lines == rulings.read(work) and lines[0] and lines[1]
    assert limit == variants.default_limit(lines, others)
    _, read_again = analysis.build_form(work, True, [FIELD, ERASE], [])

    def no_read(gray):
        raise AssertionError("罫線を読み直した")

    monkeypatch.setattr(rulings, "read", no_read)
    _, check = analysis.build_form(work, True, [FIELD, ERASE], [], None, None, lines)
    assert check.lines == lines == read_again.lines
    with pytest.raises(AssertionError, match="読み直した"):
        analysis.build_form(work, True, [FIELD, ERASE], [])


def test_template_image_is_erased_inside_ranges(juutaru_page):
    """見本の画像は、墨消しする範囲と、見本から消す範囲の中を紙の地の色で塗ってから、半分の大きさに縮める（範囲の辺に
    沿った枠の線は残すので、辺の近くは除いて確かめる）"""
    _, work = juutaru_page
    png, check = analysis.build_form(work, True, [FIELD, ERASE], [])
    small = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    h, w = work.shape
    assert small.shape == (round(h / 2), round(w / 2))
    edge = 6  # 見本の画像の画素。残す線は範囲の辺から LINE_NEAR（作業画像で8画素）と、線の太さの内
    for x, y, rw, rh in (FIELD, ERASE):
        inside = small[
            y // 2 + edge : (y + rh) // 2 - edge, x // 2 + edge : (x + rw) // 2 - edge
        ]
        assert inside.min() == inside.max() == int(np.median(work))
        before = work[y : y + rh, x : x + rw]
        assert before.min() < 128  # 消す前は、範囲の中に印字があった
    assert check.aligned and check.conflicts == ()
    # 登録では、塗る前の作業画像から見本の罫線を読む（範囲を描く画面が保存する様式に入れる）
    assert check.lines == rulings.read(work) and len(check.lines[0]) > 30
    # 今の見本の画像から作るとき（編集）は、縮め直さずに、範囲を半分の座標で消す
    again = formcheck.make_template(small, False, [(0, 0, 100, 100)])
    assert again.shape == small.shape and (again[:50, :50] == again[0, 0]).all()
    assert (again[60:, 60:] == small[60:, 60:]).all()


def _boxed_number():
    """個人番号の欄に見立てた試験用の画像（作業画像の画素）と、その欄の範囲（枠の線の中心）

    枠の線は幅3画素で、横の線は左右の表へ続く。中に、12ますの区切り（4ますごとは欄の高さいっぱい、ほかは下から
    15画素の灰色の線）と、ますごとに数字の縦の画（ますの右寄り。右のますの画は右の辺から11画素）を描く。左のますの
    縦の画は左の辺から12画素、横の画（「7」の上の画など）は上の辺から6画素の所にある。右のますには、右の辺から
    7画素の所に、欄の高さの6割以上の縦の画（枠の線に寄せて大きく書いた「1」）も描き、下の辺から6画素の所には、
    欄の幅の6割以上で左右の線に届かない横の画（下線）を描く。欄の外には、文字に見立てた画を置く。
    """
    img = np.full((300, 600), 255, np.uint8)
    rect = (120, 100, 360, 60)
    img[99:102, 20:580] = 0
    img[159:162, 20:580] = 0
    img[60:200, 119:122] = 0
    img[60:200, 479:482] = 0
    for k in range(1, 12):
        x = 120 + 30 * k
        if k % 4 == 0:
            img[100:161, x : x + 2] = 0
        else:
            img[146:161, x] = 100
    for k in range(12):
        x = 120 + 30 * k + 18
        img[112:150, x : x + 2] = 0
    img[112:150, 132:134] = 0
    img[106:108, 125:143] = 0
    img[112:150, 473:475] = 0  # 右の辺から6〜7画素
    img[154:156, 150:400] = 0  # 下の辺から4〜6画素、幅250画素
    img[120:150, 50:52] = 0  # 欄の外
    return img, rect


@pytest.mark.parametrize("scale", [1.0, formstore.TEMPLATE_SCALE])
def test_fill_ranges_keeps_only_frame_lines(scale):
    """範囲の中を、外側へ FILL_MARGIN まで紙の地の色で塗り、範囲の辺に沿った枠の線だけを残す。欄の中の区切りの線と、
    数字の画（左の辺の近くの縦の画、上の辺の近くの短い横の画、右の辺の近くの欄の高さの6割以上の縦の画、下の辺の
    近くの欄の幅の6割以上の横の画を含む。どれも上下・左右の枠の線までは届かない）は残さない。欄の外は変えない。
    見本の画像に塗るとき（scale が TEMPLATE_SCALE。編集）も、範囲は作業画像の座標で渡す"""
    work, rect = _boxed_number()
    img = (
        work
        if scale == 1.0
        else cv2.resize(work, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    )
    out = formcheck.fill_ranges(img, [rect], scale)
    x, y, w, h = (v * scale for v in rect)
    m = formcheck.FILL_MARGIN * scale
    rows = np.arange(img.shape[0])[:, None]
    cols = np.arange(img.shape[1])[None, :]
    region = (rows >= y - m) & (rows < y + h + m) & (cols >= x - m) & (cols < x + w + m)
    band = 2 + scale  # 線の太さの半分と、残す線の縁（1画素）
    on_line = (
        (np.abs(rows - y) <= band)
        | (np.abs(rows - (y + h)) <= band)
        | (np.abs(cols - x) <= band)
        | (np.abs(cols - (x + w)) <= band)
    )
    assert (out[region & ~on_line] == 255).all()
    assert (out[~region] == img[~region]).all()
    # 4辺の線は、範囲の辺に沿って全部残る
    xs, xe, ys, ye = (
        round(x + 3 * band),
        round(x + w - 3 * band),
        round(y + 3 * band),
        round(y + h - 3 * band),
    )
    top, bottom = round(y), round(y + h)
    left, right = round(x), round(x + w)
    assert (out[top - 1 : top + 2, xs:xe].min(axis=0) < 128).all()
    assert (out[bottom - 1 : bottom + 2, xs:xe].min(axis=0) < 128).all()
    assert (out[ys:ye, left - 1 : left + 2].min(axis=1) < 128).all()
    assert (out[ys:ye, right - 1 : right + 2].min(axis=1) < 128).all()


def test_fill_ranges_leaves_no_written_number():
    """架空の記入のある扶養控除等申告書を、配布用パッケージの範囲で塗ると、範囲（外側 FILL_MARGIN まで）の中に記入の
    画素が残らない。記入のない公開様式を同じく塗った画像の、線から1画素より離れた暗い画素を、残った記入とする"""
    blank, filled = page_image("2027bun_01"), page_image("2027bun_01", filled=True)
    rects = [(f.x, f.y, f.w, f.h) for f in bundled("nta-fuyou-r9").fields]
    after_blank = formcheck.fill_ranges(blank, rects, 1.0)
    after_filled = formcheck.fill_ranges(filled, rects, 1.0)
    m = formcheck.FILL_MARGIN
    near = np.ones((3, 3), np.uint8)
    written = left = 0
    for x, y, w, h in rects:
        sl = (
            slice(int(y - m), int(y + h + m) + 1),
            slice(int(x - m), int(x + w + m) + 1),
        )
        printed = cv2.dilate((blank[sl] < 160).astype(np.uint8), near) > 0
        written += int(((filled[sl] < 160) & ~printed).sum())
        kept = cv2.dilate((after_blank[sl] < 160).astype(np.uint8), near) > 0
        left += int(((after_filled[sl] < 160) & ~kept).sum())
    assert written > 5000  # 塗る前は、範囲の中に記入があった
    assert left == 0


def test_registered_form_classifies_other_scans(juutaru_page, scan, tmp_path):
    """登録した様式で、そのページと別の傾き・回転の試験画像を判別し、範囲を墨消しする（消す範囲は墨消ししない）"""
    img, work = juutaru_page
    png, check = analysis.build_form(work, True, [FIELD, ERASE], list(bundled_forms()))
    assert check.aligned and check.conflicts == ()
    h, w = work.shape
    spec = formstore.FormSpec(
        formsave.new_user_id(), NAME, "", (w, h),
        (formstore.Field("本人の個人番号", *FIELD),),
        erase=(formstore.Field("消す範囲1", *ERASE),),
    )  # fmt: skip
    # 罫線が上限を超えて読まれた用紙（ディザのスキャンなど）から登録しても、罫線を持たない様式として保存され、
    # 見本との位置合わせでの判別と墨消しは同じに動く
    too_many = tuple(
        formstore.Line(10.0 + 0.25 * i, 100, 400, 1)
        for i in range(formstore.MAX_LINES + 1)
    )
    spec = dataclasses.replace(spec, lines=(too_many, check.lines[1]))
    formsave.save(tmp_path, spec, png, formsave.OP_REGISTER, "yamada")
    loaded = formstore.load(formstore.FolderChoice(tmp_path))
    assert [f.lines for f in loaded.active if f.name == NAME] == [None]
    ref = [Box(*FIELD, "本人の個人番号", Source.AUTO)]
    for angle, turns in [(3, 0), (-1.5, 1)]:
        page = imaging.open_pages(scan("2026bun_08", angle=angle, quarter_turns=turns))[
            0
        ]
        res = pipeline.analyze(page, formset=loaded.active)
        assert (res.form, res.note, res.quarter_turns) == (NAME, "", (4 - turns) % 4)
        assert len(res.boxes) == 1  # 消す範囲は墨消ししない
        M, shape = scan_transform(img.shape, angle, turns)
        assert (
            res.unframed == ()
        )  # 枠のない範囲（記録のない、画面から登録した様式）は、線を探さない
        assert_covered(ref, M, shape, page, res)


def test_check_finds_too_few_features_and_other_forms(juutaru_page, analyzed):
    """消しすぎて手がかりが足りない画像と、ほかの様式と取り違える画像を見つける"""
    _, work = juutaru_page
    h, w = work.shape
    _, check = analysis.build_form(work, True, [(0, 0, w, h - 80)], [])
    assert not check.aligned and check.inliers < check.needed == align.MIN_INLIERS
    # 扶養控除等申告書のページから登録すると、配布用パッケージの扶養控除等申告書と取り違えるおそれがある
    _, fuyou = analyzed("2027bun_01", filled=True)
    _, check = analysis.build_form(fuyou.work, True, [FIELD], list(bundled_forms()))
    assert check.aligned
    [(name, inliers)] = check.conflicts
    assert name == forms.FUYOU and inliers >= align.MIN_INLIERS


def test_check_reports_size_mismatch_without_saying_too_few_features(
    juutaru_page, monkeypatch
):
    """整合した特徴点が足りていても、移した大きさが合わずに位置合わせが成り立たないとき、保存の前の確認の文は、点が
    足りないとは言わない（大きさの範囲 AREA_RATIO を成り立たない値に置き換えて、その経路を通す）"""
    monkeypatch.setattr(align, "AREA_RATIO", (10.0, 20.0))
    _, work = juutaru_page
    _, check = analysis.build_form(work, True, [FIELD], [])
    assert not check.aligned and check.inliers >= check.needed
    text = formstext.not_aligned_text(check.inliers, check.needed)
    assert text.startswith("この画像では、元の画像と位置合わせした大きさが合いません")
    assert "手がかりが足りません" not in text


def test_check_page_finds_distorted_images():
    """登録と置き換えに使うページの歪み：スキャンに見立てたページ（傾けたものも、作業画像では傾きを直してある）は
    歪んでいない。用紙を小さく写した写真（用紙の割合が DISTORT_PAPER 未満）と、遠近で罫線ごとに傾きが変わる写真
    （傾きのばらつきが DISTORT_SPREAD 以上）は歪んでいる。画像はメモリの中だけで作る"""
    page = page_image("2026bun_04")
    h, w = page.shape
    assert formcheck.line_angle_spread(page) < 0.2 and not formcheck.distorted(page)
    small = np.full(
        (h, w), 70, np.uint8
    )  # 机に見立てた暗い地の上に、半分の大きさの用紙を置く
    small[h // 4 : h // 4 + h // 2, w // 4 : w // 4 + w // 2] = cv2.resize(
        page, (w // 2, h // 2)
    )
    assert formcheck.distorted(small)
    # 台形に写した用紙：縦の線は、左右の端で傾きが逆になる
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[0.04 * w, 0], [0.96 * w, 0], [w, h], [0, h]])
    keystone = cv2.warpPerspective(
        page, cv2.getPerspectiveTransform(src, dst), (w, h), borderValue=255
    )
    spread = formcheck.line_angle_spread(keystone)
    assert spread >= formcheck.DISTORT_SPREAD and formcheck.distorted(keystone)


def test_written_ranges_find_entries_inside_ranges():
    """記入の見分け：扶養控除等申告書の個人番号欄に架空の番号を書いたページでは、番号を書いた範囲を、記入のある範囲と
    みる。記入のない公開様式と、白黒2値の公開様式では、どの範囲も記入とみない。範囲を描く画面を開く前の確かめ
    （check_page）は、歪みと合わせて返す"""
    spec = bundled("nta-fuyou-r9")
    filled = page_image("2027bun_01", filled=True)
    written = formcheck.written_ranges(filled, spec.fields)
    # 番号を書いた7欄（tests/data/README.md）のすべてで、書いていない3欄は入らない
    assert written == (
        "支払者の法人（個人）番号",
        "本人の個人番号",
        "A 源泉控除対象配偶者",
        "B 源泉控除対象親族 1",
        "B 源泉控除対象親族 2",
        "B 源泉控除対象親族 3",
        "住民税 16歳未満の扶養親族 1",
    )
    for blank in (page_image("2027bun_01"), page_image("2027bun_01", bilevel=True)):
        assert formcheck.written_ranges(blank, spec.fields) == ()
    check = analysis.check_page(filled, list(spec.fields))
    assert check == formcheck.PageCheck(False, written)
    # 範囲の中の墨のまとまりが WRITTEN_MIN 未満なら記入とみない（1つの数字だけを書いた範囲）
    one = page_image("2027bun_01")
    f = spec.fields[0]
    cv2.putText(
        one, "8", (int(f.x + f.w / 3), int(f.y + 0.8 * f.h)), cv2.FONT_HERSHEY_SIMPLEX,
        f.h / 30, 0, 3,
    )  # fmt: skip
    assert formcheck.written_ranges(one, [f]) == ()
    # 画像の外にある範囲と、幅か高さが1画素に満たない範囲は、見ない
    h, w = filled.shape
    outside = dataclasses.replace(f, x=w + 50.0, y=h + 50.0)
    thin = dataclasses.replace(f, h=0.5)
    assert formcheck.written_ranges(filled, [outside, thin]) == ()


def test_form_with_few_cues_is_warned_at_saving():
    """保存の前の確認の点（見本の画像と元のページの整合した特徴点）は、表の線と英字1語の題名だけの様式では
    位置合わせが成り立つ下限以上・1,500点未満で、手がかりが少ないことの警告が出る。文字のある様式（架空の控除証明書
    と、そこから説明の文や題名を消したもの）と、公開様式から登録する様式は1,500点以上で、警告は出ない"""
    _, check = analysis.build_form(simple_form(), True, [SIMPLE_FIELD], [])
    assert check.aligned and align.MIN_INLIERS <= check.inliers < formstext.FEW_POINTS
    assert len(formstext.save_warnings((), check.inliers)) == 1
    cert = data_image("架空_控除証明書_生命保険_200dpi_グレー.png")
    no_notes = cert.copy()
    no_notes[240:600] = 255  # 説明の文3行と「契約者」の行を消す
    table_only = no_notes.copy()
    table_only[:240] = 255  # 題名も消す
    works = [cert, no_notes, table_only, page_image("2026bun_08")]
    for work, rect in zip(works, [SIMPLE_FIELD] * 3 + [FIELD], strict=True):
        _, check = analysis.build_form(work, True, [rect], [])
        assert check.inliers >= formstext.FEW_POINTS
        assert formstext.save_warnings((), check.inliers) == []


def test_ranges_move_to_replacing_page(analyzed):
    """見本の画像を置き換えるときは、新しいページを今の見本と位置合わせし、範囲を新しいページの上に移す"""
    _, res = analyzed("2027bun_01", angle=3, filled=True)
    fuyou = bundled("nta-fuyou-r9")
    spec = dataclasses.replace(
        fuyou, erase=(formstore.Field("氏名", 540, 240, 600, 70),)
    )
    fields, erase = analysis.move_ranges(res.work, spec)
    assert [f.name for f in fields] == [b.kind for b in res.boxes]
    for f, b in zip(fields, res.boxes):  # 解析で求めた範囲と同じ位置
        assert abs(f.x - b.x) < 1 and abs(f.y - b.y) < 1
    # 消す範囲も同じ変換で移る（見本の上での、1つ目の墨消しする範囲との位置の差が保たれる）
    first = fuyou.fields[0]
    assert [e.name for e in erase] == ["氏名"]
    assert abs((erase[0].x - fields[0].x) - (540 - first.x)) < 5
    assert abs((erase[0].y - fields[0].y) - (240 - first.y)) < 5
    # 位置合わせできないページ（ほかの様式）なら None
    _, hoken = analyzed("2026bun_04")
    assert analysis.move_ranges(hoken.work, fuyou) is None


def test_template_crop_is_in_work_coordinates():
    """見本の画像（作業画像の半分の解像度）の一部を、見本の作業画像の座標の画像にする（枠の線を越えた記入を探すとき、
    印刷の部分を求めるのに使う）"""
    image = np.full((300, 400), 255, np.uint8)
    image[50, 100:300] = 0  # 作業画像の座標では、行100〜101・列200〜599
    to_crop = np.array([[1.0, 0, -150], [0, 1.0, -80], [0, 0, 1.0]])
    crop = forms._template_crop(image, to_crop, (60, 500))
    rows = np.flatnonzero((crop < 128).any(axis=1))
    cols = np.flatnonzero((crop < 128).any(axis=0))
    assert (rows.min(), rows.max()) == (20, 21)
    assert (cols.min(), cols.max()) == (50, 449)


def test_fit_to_frame_moves_range_to_frame_centers(reference):
    """範囲を描いた元の画像で枠の線を探し、線が見つかった組の辺を線の中心に合わせる。左右は探す範囲（12画素）まで、
    上下は探す範囲（8画素）より少し内側まで、線より内側に描いた範囲も合わせる（登録のときだけ、枠の内側が範囲より
    大きい組も許す）。線のない所に描いた範囲は動かさない"""
    tb, lr = formstore.TOP_BOTTOM, formstore.LEFT_RIGHT
    work, boxes = reference("2027bun_01")  # 範囲は、解析で求めた枠の線の中心
    for b in boxes:
        for s in (0, 2, 4, 6, 8, 12):
            rect, found = forms.fit_to_frame(
                work, True, (b.x + s, b.y, b.w - 2 * s, b.h)
            )
            assert found == (tb, lr), (b.kind, s)
            assert max(abs(u - v) for u, v in zip(rect, b.rect())) <= 0.5, (b.kind, s)
        rect, found = forms.fit_to_frame(
            work, True, (b.x + 4, b.y + 4, b.w - 8, b.h - 8)
        )
        assert (
            found == (tb, lr) and max(abs(u - v) for u, v in zip(rect, b.rect())) <= 0.5
        )
        # 探す組を渡すと、その組だけを合わせる
        rect, found = forms.fit_to_frame(
            work, True, (b.x + 4, b.y + 4, b.w - 8, b.h - 8), (lr,)
        )
        assert found == (lr,) and rect == (b.x, b.y + 4, b.w, b.h - 8)
    blank = np.full((600, 1200), 255, np.uint8)
    assert forms.fit_to_frame(blank, True, (300, 200, 400, 50)) == (
        (300, 200, 400, 50),
        (),
    )
    # 見本の画像（範囲の中を塗ったもの）：範囲の辺に沿った枠の線は残すので、線より内側に描いた範囲も、線にかかる
    # 範囲も合う
    [b] = [b for b in boxes if b.kind == "A 源泉控除対象配偶者"]
    inside = (b.x + 4, b.y + 3, b.w - 8, b.h - 6)
    rect, found = forms.fit_to_frame(
        formcheck.make_template(work, True, [inside]), False, inside
    )
    assert found == (tb, lr) and max(abs(u - v) for u, v in zip(rect, b.rect())) <= 1.5
    over = (b.x - 2, b.y - 2, b.w + 4, b.h + 4)
    rect, found = forms.fit_to_frame(
        formcheck.make_template(work, True, [over]), False, over
    )
    assert found == (tb, lr) and max(abs(u - v) for u, v in zip(rect, b.rect())) <= 1.5
    # 枠の線ごと塗った見本の画像（前のバージョンで、範囲の中だけを塗って保存したもの）では、線にかかる範囲の線は
    # 見つからず、動かさない
    old = work.copy()
    x0, y0, x1, y1 = (
        round(v) for v in (over[0], over[1], over[0] + over[2], over[1] + over[3])
    )
    old[y0:y1, x0:x1] = int(np.median(work))
    old = cv2.resize(old, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    assert forms.fit_to_frame(old, False, over) == (over, ())
