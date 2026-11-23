# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""スマートフォンで撮った写真に見立てた画像のテスト（1枚の作成と解析に数秒かかる）

写真の中の申告書の欄を墨消しする範囲が覆うかは、field_coverage.py で確かめる。
端が浮いて曲がった用紙の写真（scan.bent）では、欄に書いた数字が墨消しの外に残らないかを確かめる。
"""

import dataclasses

import numpy as np
import pytest
from field_coverage import assert_covered, left_digits
from scan import (
    GRAY_DPI,
    PHOTO_LONG,
    PHOTO_SHORT,
    bent,
    make_photo,
    make_scan,
    page_image,
    written_page,
)
from userforms import bundled_forms

from sumikeshi_studio import bend, forms, formstore, frames, imaging, pipeline

L, S = PHOTO_LONG, PHOTO_SHORT
KINDS = {
    "2027bun_01": forms.FUYOU,
    "2026bun_04": forms.HOKEN,
    "2026bun_06": forms.KIHAI,
}


def analyze_photo(path):
    page = imaging.open_pages(path)[0]
    return page, pipeline.analyze(page, formset=bundled_forms())


@pytest.fixture(scope="module")
def photos(tmp_path_factory):
    """photos(key, make) で、make(path) が書いた写真のファイルと、make の戻り値を返す。同じ key なら作り直さない
    （同じ指定の写真を使うテストは、写真の画像を共有する。ページは開き直し、解析の結果は共有しない）"""
    made = {}

    def get(key, make):
        if key not in made:
            path = tmp_path_factory.mktemp("photo") / "photo.jpg"
            made[key] = (path, make(path))
        return made[key]

    return get


def two_forms_photo(reference, photos):
    """扶養控除等申告書（記入あり）と保険料控除申告書を1枚に写した写真と、2枚それぞれの座標の変換"""
    fuyou, _ = reference("2027bun_01", True)
    hoken, _ = reference("2026bun_04")
    return photos(
        "two-forms",
        lambda path: make_photo(
            [
                (fuyou, S / 2, L * 0.26, 2750, 1, 0.05),
                (hoken, S / 2, L * 0.75, 2750, -1.5, 0.05),
            ],
            path,
            portrait=True,
        ),
    )


def only_unframed_note(res) -> bool:
    """要確認が、線が見つからない欄（res.unframed）のものだけか。写真では、線が見つからない欄があり、その欄を
    型の位置のまま墨消しして要確認とする"""
    if not res.unframed:
        return res.note == ""
    if res.crossed != ():
        return False
    if res.note == "すべての欄の範囲が、欄に合っていないおそれがあります":
        return True
    names = "".join(f"「{n}」" for n in res.unframed)
    return res.note.startswith(names) and res.note.endswith(
        "の欄の範囲が、欄に合っていないおそれがあります"
    )


@pytest.mark.parametrize(
    ("form", "filled", "ratio", "keystone", "angle", "portrait"),
    [
        # 横長の写真の長辺の50%に写り、台形のゆがみ15%、4度傾いた扶養控除等申告書（記入あり）
        ("2027bun_01", True, 0.5, 0.15, 4, False),
        ("2026bun_06", False, 0.75, 0.05, 2, False),
        # 縦長の写真に、90度回して写した保険料控除申告書
        ("2026bun_04", False, 0.75, 0.10, 92, True),
    ],
)
def test_fields_in_photo_are_covered(
    form, filled, ratio, keystone, angle, portrait, reference, tmp_path
):
    """写真の中に小さく写った申告書でも、型どおりに墨消しする"""
    img, ref_boxes = reference(form, filled)
    W, H = (S, L) if portrait else (L, S)
    path = tmp_path / "photo.jpg"
    [M] = make_photo([(img, W / 2, H / 2, ratio * L, angle, keystone)], path, portrait)
    page, res = analyze_photo(path)
    assert res.form == KINDS[form] and only_unframed_note(res)
    assert len(res.boxes) == len(ref_boxes)
    assert_covered(ref_boxes, M, (H, W), page, res, unframed=True)


def test_two_forms_in_one_photo_are_both_masked(reference, photos):
    """1枚の写真に申告書が2枚写っていれば、両方の欄を墨消しし、「⚠要確認」とする"""
    _, fuyou_boxes = reference("2027bun_01", True)
    _, hoken_boxes = reference("2026bun_04")
    path, (M1, M2) = two_forms_photo(reference, photos)
    page, res = analyze_photo(path)
    assert res.form == forms.FUYOU
    assert res.note.startswith(
        "帳票が2枚写っています（扶養控除等申告書、保険料控除申告書）"
    )
    assert len(res.boxes) == len(fuyou_boxes) + len(hoken_boxes)
    assert_covered(fuyou_boxes, M1, (L, S), page, res, unframed=True)
    assert_covered(
        hoken_boxes, M2, (L, S), page, res, start=len(fuyou_boxes), unframed=True
    )


def test_sheets_beyond_limit_are_reported_as_not_checked(
    reference, photos, monkeypatch
):
    """調べる帳票の数（MAX_FORMS）に達した後にも用紙が写っていれば、その用紙は墨消ししていないと知らせる（様式を
    判別できない用紙とは言わない）。上限を1枚にして、2枚の帳票の写真で確かめる"""
    monkeypatch.setattr(forms, "MAX_FORMS", 1)
    _, fuyou_boxes = reference("2027bun_01", True)
    path, _ = two_forms_photo(reference, photos)
    _, res = analyze_photo(path)
    assert res.form == forms.FUYOU and len(res.boxes) == len(fuyou_boxes)
    assert "用紙が多いため、1枚を超える分は墨消ししていません" in res.note
    assert "様式を判別できない用紙" not in res.note


def test_two_copies_with_lines_missing_on_one_are_not_all_fields(tmp_path):
    """同じ様式が2枚写った写真で、1枚目の欄だけ枠の線が見つからないとき、「すべての欄」とは言わず、欄の名前を並べる
    （欄の名前は2枚で同じなので、名前の集まりで比べると「すべての欄」になっていた）"""
    form = "2026bun_06"
    img = page_image(form)
    boxes = pipeline.analyze(
        imaging.MemoryPage(np.dstack([img] * 3), 200.0, "見本"), formset=bundled_forms()
    ).boxes
    erased = img.copy()
    for b in boxes:  # 1枚目の枠の線を消す
        x0, y0, x1, y1 = int(b.x), int(b.y), int(b.x + b.w), int(b.y + b.h)
        for y in (y0, y1):
            erased[y - 4 : y + 5, x0 - 6 : x1 + 7] = 255
        for x in (x0, x1):
            erased[y0 - 6 : y1 + 7, x - 4 : x + 5] = 255
    path = tmp_path / "photo.jpg"
    make_photo(
        [
            (erased, S / 2, L * 0.26, 2750, 1, 0.05),
            (img, S / 2, L * 0.75, 2750, -1.5, 0.05),
        ],
        path,
        portrait=True,
    )
    _, res = analyze_photo(path)
    assert len(res.boxes) == 2 * len(boxes) and len(res.unframed) == len(boxes)
    assert "すべての欄" not in res.note
    assert "「配偶者」" in res.note and res.note.endswith(
        "の欄の範囲が、欄に合っていないおそれがあります"
    )


def test_paper_without_template_in_photo_needs_review(reference, tmp_path):
    """どの様式の見本にも類似しない用紙（従たる給与の申告書）も写っていれば、「⚠要確認」とする"""
    fuyou, fuyou_boxes = reference("2027bun_01", True)
    path = tmp_path / "photo.jpg"
    make_photo(
        [
            (fuyou, S / 2, L * 0.26, 2750, 1, 0.05),
            (page_image("2026bun_08"), S / 2, L * 0.75, 2750, -1.5, 0.05),
        ],
        path,
        portrait=True,
    )
    _, res = analyze_photo(path)
    assert res.form == forms.FUYOU
    assert len(res.boxes) == len(fuyou_boxes)
    assert res.note.startswith(
        "様式を判別できない用紙も写っていて、その用紙は墨消ししていません"
    )


def test_paper_without_template_in_photo_is_unknown(tmp_path):
    """どの様式の見本にも類似しない申告書だけを写した写真は、様式不明として要確認にする"""
    path = tmp_path / "photo.jpg"
    make_photo([(page_image("2026bun_08"), L / 2, S / 2, 0.6 * L, 4, 0.15)], path)
    _, res = analyze_photo(path)
    assert res.form == formstore.UNKNOWN
    assert res.boxes == [] and res.note == forms.UNKNOWN_NOTE


# ---- 端が浮いて曲がった用紙の写真
UP, RIGHT, UP_RIGHT = (60, 0), (0, 40), (60, 40)  # scan.bent の (上の端, 右の端) の浮き
BENT_LONG = 3000  # 紙の長辺に写す画素（写真の長辺の75%）
LEFT_REVIEW = 100  # 墨消しの外にこの画素を超えて数字が残るページには、要確認が付いていないといけない
# 枠の線を「なし」にする、扶養控除等申告書の欄
FRAMELESS = ("支払者の法人（個人）番号", "本人の個人番号")


def bent_photos(form, bend, keystone, angle, place, boxes, photos, seed=0):
    """記入のない用紙と、boxes（conftest.py の reference の墨消しする範囲）に数字を書いた用紙（place は
    scan.written_page の置き方）を、同じように曲げて写真にしたページ。seed は写真の作り方の乱数"""
    pages = []
    for name, b in (("blank", ()), ("filled", boxes)):

        def make(path, b=b):
            img = bent(written_page(form, GRAY_DPI, b, place), *bend)
            make_photo(
                [(img, L / 2, S / 2, BENT_LONG, angle, keystone)], path, seed=seed
            )

        key = (form, bend, keystone, angle, place, name, repr(b), seed)
        path, _ = photos(key, make)
        pages.append(imaging.open_pages(path)[0])
    return pages


@pytest.mark.parametrize(
    ("form", "bend", "keystone", "angle", "place", "limit"),
    [
        # 欄の中央に書いた数字。上の端が浮いた扶養控除等申告書は、1つの射影変換では支払者・本人の欄の線が探す範囲の
        # 外にずれ、範囲が数字の行より下に残る形
        ("2027bun_01", UP, 0.05, -4.0, "center", 0),
        ("2027bun_01", UP_RIGHT, 0.3, -4.0, "center", 0),
        # 枠の内側の高さいっぱいに書いた数字。線が見つかった欄でも、写真のぼけで数字の下の端が線の中心より下まで
        # 広がり、墨消しのすぐ下に残ることがある。上限は、今の処理で測った値で、これより増えないことを確かめる。
        # この値は、見本の画像と写真の作り方の乱数のわずかな違いで変わる（上の端が浮いた扶養控除等申告書は、補正の
        # 後の位置と本当の線の差が、上下の線を探す範囲（frames.DEPTH_H）の端に近い。線を探す範囲を広げて探し直す
        # ので、乱数を8通りに変えて3〜57画素。探し直さないと9〜3,844画素になった）。基・配・特・所の申告書は、
        # 乱数を変えると340画素残る組がある（要確認が付くことは、下の
        # test_many_digits_left_outside_come_with_review が確かめる）。保険料控除申告書の10画素は、見本の画像を枠の線を
        # 残して塗る形に作り直した後の値で、写真のぼけで線の中に残る数字の端（要確認は付かない）。範囲を線の中心を
        # 結ぶ四隅にした後は、扶養控除等申告書が3→13画素（12画素は「B 源泉控除対象親族 4」の下の辺の外0.2〜0.3画素の、
        # 印刷の線の中）、保険料控除申告書が10→4画素、基・配・特・所の申告書は6画素のまま。上限は、四隅にした後の値
        ("2027bun_01", UP, 0.05, -4.0, "touch", 13),
        ("2026bun_06", UP_RIGHT, 0.05, 1.0, "touch", 6),
        ("2026bun_04", RIGHT, 0.05, -4.0, "touch", 4),
    ],
)
def test_numbers_in_bent_paper_photo_are_masked(
    form, bend, keystone, angle, place, limit, reference, photos
):
    """端が浮いて曲がり、1つの射影変換で合わせきれない用紙の写真でも、欄に書いた数字が墨消しの外に残らない
    （高さいっぱいに書いた数字は、残る画素が上限以下）"""
    _, boxes = reference(form)
    blank, filled = bent_photos(form, bend, keystone, angle, place, boxes, photos)
    res = pipeline.analyze(filled, formset=bundled_forms())
    assert res.form == KINDS[form]
    left, digits, _ = left_digits(res, filled, blank)
    assert digits > 0 and left.sum() <= limit


def test_many_digits_left_outside_come_with_review(reference, photos):
    """墨消しの外に LEFT_REVIEW 画素を超えて数字が残るページには、要確認が付いている。基・配・特・所の申告書の、
    上と右の端が浮いた用紙に高さいっぱいの数字を書いた写真（写真の作り方の乱数1）は、写真で傾いた線と範囲の辺の
    すき間に数字の端が残り（340画素）、その欄を名指しした要確認が付く。残る画素が LEFT_REVIEW 以下に減ったら、
    この確かめ方が働くように、多く残る別の組に替える"""
    form = "2026bun_06"
    _, boxes = reference(form)
    blank, filled = bent_photos(
        form, UP_RIGHT, 0.05, 1.0, "touch", boxes, photos, seed=1
    )
    res = pipeline.analyze(filled, formset=bundled_forms())
    left, digits, _ = left_digits(res, filled, blank)
    assert digits > 0 and left.sum() > LEFT_REVIEW
    assert "「特定親族 1」" in res.note and res.note.endswith(
        "の欄の範囲が、欄に合っていないおそれがあります"
    )


def _lowered(name: str, by: float):
    """配布用パッケージの3様式の、扶養控除等申告書の欄 name を by 画素下にずらしたもの"""
    return tuple(
        dataclasses.replace(
            spec,
            fields=tuple(
                dataclasses.replace(f, y=f.y + by) if f.name == name else f
                for f in spec.fields
            ),
        )
        if spec.id == "nta-fuyou-r9"
        else spec
        for spec in bundled_forms()
    )


@pytest.mark.parametrize("photo", [False, True])
def test_lines_are_searched_wider_after_bend_correction_only_in_photos(
    photo, tmp_path, monkeypatch
):
    """曲がりの補正の後も、欄の位置が上と下の線から線を探す範囲（frames.DEPTH_H）を超えてずれて残ったとき、写真と
    判定したページでは、範囲を狭いほうから順に広げて探し直して線に合わせ、線が見つからない要確認を付けない。スキャンのページでは
    探し直さず、要確認を残す（行の高さが見本と違う用紙で、隣の行の線に合わせないため）。ずれは、様式の欄を12画素
    下にずらし、曲がりの補正の結果を「動かさない」に置き換えて作る"""
    name = "住民税 16歳未満の扶養親族 1"
    image = page_image("2027bun_01")
    if photo:
        path = tmp_path / "photo.jpg"
        make_photo([(image, L / 2, S / 2, BENT_LONG, -4.0, 0.05)], path)
        page = imaging.open_pages(path)[0]
    else:
        page = imaging.MemoryPage(np.dstack([image] * 3), float(GRAY_DPI), "test")
    want = next(
        b
        for b in pipeline.analyze(page, formset=bundled_forms()).boxes
        if b.kind == name
    )
    depths = []
    find = frames.find

    def spy(*args, depth_h=frames.DEPTH_H, **kwargs):
        depths.append(depth_h)
        return find(*args, depth_h=depth_h, **kwargs)

    monkeypatch.setattr(frames, "find", spy)
    monkeypatch.setattr(
        bend,
        "transforms",
        lambda gray, image, H, fields: [H if f.name == name else None for f in fields],
    )
    res = pipeline.analyze(page, formset=_lowered(name, 12))
    got = next(b for b in res.boxes if b.kind == name)
    if photo:
        # 狭い範囲から順に試し、線が見つかった範囲（12画素のずれでは12）でやめる
        assert [d for d in depths if d != frames.DEPTH_H] == [10, 12]
        assert name not in res.unframed
        assert abs(got.y - want.y) <= 1 and abs(got.h - want.h) <= 1
    else:
        assert set(depths) == {frames.DEPTH_H}
        assert name in res.unframed and res.note
        assert got.y - want.y > frames.DEPTH_H


def test_field_without_bend_correction_is_searched_wider_in_photo(reference, tmp_path):
    """写真で、曲がりの補正の変換を求められなかった欄も、位置合わせの変換のまま、線を探す範囲を広げて探し直す。
    基・配・特・所の申告書を、遠近を強く付けて平らに写した写真では、「配偶者」の欄の上と下の線が、探す範囲8画素・
    10画素では見つからず、12画素で見つかる。この欄は補正の変換を求められないので、広げて探さないと、線が
    見つからない要確認が付く"""
    form = "2026bun_06"
    _, boxes = reference(form)
    path = tmp_path / "photo.jpg"
    img = bent(written_page(form, GRAY_DPI, boxes), 0, 0)
    make_photo([(img, L / 2, S / 2, BENT_LONG, -4.0, 0.3)], path)
    res = pipeline.analyze(imaging.open_pages(path)[0], formset=bundled_forms())
    assert res.form == KINDS[form]
    assert res.unframed == () and res.note == ""


@pytest.mark.parametrize("moved", [0.72, 0.75, 0.78])
def test_wider_search_does_not_reach_lines_of_same_size_neighbor(
    moved, tmp_path, monkeypatch
):
    """写真で、曲がりの補正が、欄を同じ大きさの隣の欄の向きに、隣までの距離の0.72〜0.78倍動かしたとき（補正を使わない
    上限の0.8倍には届かない）は、線を探す範囲を広げない。広げると、隣の欄の線がすべて見つかり、要確認が付かない
    まま、番号がすべて墨消しの外に出る（補正の結果を、隣の欄の向きへの移動に置き換えて確かめる）"""
    form = "2027bun_01"
    specs = bundled_forms()
    reference = imaging.MemoryPage(
        np.dstack([page_image(form)] * 3), float(GRAY_DPI), "test"
    )
    ref = pipeline.analyze(reference, formset=specs).boxes
    first = next(b for b in ref if b.kind == "住民税 16歳未満の扶養親族 1")
    second = next(b for b in ref if b.kind == "住民税 16歳未満の扶養親族 2")
    pages = []
    for name, boxes in (("blank", ()), ("filled", [first])):
        img = written_page(form, GRAY_DPI, boxes)
        y = round(first.y)  # 1つ目の欄の上の線を消し、補正の対象にする
        img[y - 3 : y + 4, round(first.x) + 2 : round(first.x + first.w) - 2] = 255
        path = tmp_path / f"{name}.jpg"
        make_photo([(img, L / 2, S / 2, BENT_LONG, -4.0, 0.05)], path)
        pages.append(imaging.open_pages(path)[0])
    blank, filled = pages
    down = np.array([[1, 0, 0], [0, 1, moved * (second.y - first.y)], [0, 0, 1.0]])
    monkeypatch.setattr(
        bend,
        "transforms",
        lambda gray, image, H, fields: [
            H @ down if f.name == first.kind else None for f in fields
        ],
    )
    depths = []
    find = frames.find

    def spy(*args, depth_h=frames.DEPTH_H, **kwargs):
        depths.append(depth_h)
        return find(*args, depth_h=depth_h, **kwargs)

    monkeypatch.setattr(frames, "find", spy)
    res = pipeline.analyze(filled, formset=specs)
    assert set(depths) == {frames.DEPTH_H}
    assert first.kind in res.unframed and f"「{first.kind}」" in res.note
    assert left_digits(res, filled, blank)[1] > 1000


@pytest.mark.parametrize(
    ("form", "bend", "keystone", "angle", "tolerance"),
    [("2027bun_01", RIGHT, 0.05, -4.0, 0.6), ("2026bun_06", UP, 0.05, -4.0, 0.95)],
)
def test_digits_joined_to_line_do_not_move_edge(
    form, bend, keystone, angle, tolerance, reference, photos
):
    """写真で、枠の内側の高さいっぱいに書いた数字の画が線とつながっても、線が見つかった欄の上と下の辺は、記入のない
    用紙と同じ所（線の中心）になる（この2件では、つながった帯の中心を線とみなすと、下の辺が1.6・2.8画素内側に入った）。
    差の許しは今の処理で測った値（基・配・特・所の申告書の上の端が浮いた写真は0.93画素。線につながった数字で、線の
    中心の推定が1画素未満動く）"""
    _, boxes = reference(form)
    blank, filled = bent_photos(form, bend, keystone, angle, "touch", boxes, photos)
    expected, got = (
        pipeline.analyze(p, formset=bundled_forms()) for p in (blank, filled)
    )
    lined = [
        (want, b)
        for want, b in zip(expected.boxes, got.boxes)
        if want.kind not in expected.unframed and b.kind not in got.unframed
    ]
    assert lined
    for want, b in lined:
        assert abs(_bottom(b) - _bottom(want)) <= tolerance, b.kind
        assert abs(_top(b) - _top(want)) <= tolerance, b.kind


def _top(box) -> float:
    """範囲の上の辺の位置。角のある範囲は、上の2つの角の高さの平均（線の両端の中心の平均で、長方形の範囲の辺と同じ値）"""
    if box.corners is None:
        return box.y
    return (box.corners[0][1] + box.corners[1][1]) / 2


def _bottom(box) -> float:
    """範囲の下の辺の位置。角のある範囲は、下の2つの角の高さの平均"""
    if box.corners is None:
        return box.y + box.h
    return (box.corners[2][1] + box.corners[3][1]) / 2


def test_fields_tilted_in_photo_need_review(tmp_path):
    """横から斜めに撮って欄の中の線が傾いた写真では、線が見つかった欄の範囲を、線の中心を結ぶ四隅（角のある範囲）に
    する。傾きの要確認は、四隅にした欄にも今までどおり付ける（曲がった用紙では、線で決めていない左右の辺の外に数字が
    残ることがあるため。test_many_digits_left_outside_come_with_review）。この写真では10欄とも、線の両端の高さの差の
    半分が3画素以上あるので、「すべての欄」の文になる"""
    # 帳票の画像を90度回してから台形にゆがめ、帳票の左右の辺の長さを変える（make_photo の台形のゆがみは、紙の画像の
    # 上の辺を縮める）
    img = np.ascontiguousarray(np.rot90(page_image("2027bun_01")))
    path = tmp_path / "photo.jpg"
    make_photo([(img, S / 2, L / 2, 0.75 * L, 0, 0.3)], path, portrait=True)
    _, res = analyze_photo(path)
    assert res.form == forms.FUYOU and res.unframed == ()
    assert res.note == "すべての欄の範囲が、欄に合っていないおそれがあります"
    for (
        box
    ) in res.boxes:  # 角は左上・右上・右下・左下の順で、上と下の辺が線に沿って傾く
        assert box.corners is not None, box.kind
        tl, tr, br, bl = box.corners
        assert abs(tr[1] - tl[1]) / 2 > forms.TILT_REVIEW, box.kind
        assert abs(br[1] - bl[1]) / 2 > forms.TILT_REVIEW, box.kind
        assert tl[0] < tr[0] and bl[0] < br[0] and tl[1] < bl[1] and tr[1] < br[1]


def frameless_forms():
    """配布用パッケージの3様式の、扶養控除等申告書の FRAMELESS の欄の枠の線を「なし」にしたもの"""
    out = []
    for spec in bundled_forms():
        if spec.id == "nta-fuyou-r9":
            fields = tuple(
                dataclasses.replace(f, frame=()) if f.name in FRAMELESS else f
                for f in spec.fields
            )
            spec = dataclasses.replace(spec, fields=fields)
        out.append(spec)
    return tuple(out)


def test_frameless_fields_in_photo_need_review(reference, photos):
    """枠の線を「なし」にした欄も、写真と判定したページでは曲がりを補った位置で墨消しして数字を残さず、欄の名前を
    入れた要確認を付ける（線で位置を確かめられないため）"""
    _, boxes = reference("2027bun_01")
    blank, filled = bent_photos("2027bun_01", UP, 0.05, -4.0, "center", boxes, photos)
    res = pipeline.analyze(filled, formset=frameless_forms())
    left, digits, _ = left_digits(res, filled, blank)
    assert digits > 0 and left.sum() == 0
    names = "".join(f"「{n}」" for n in FRAMELESS)
    assert f"{names}の欄の範囲が、欄に合っていないおそれがあります" in res.note


def test_frameless_fields_in_scan_need_no_review(tmp_path):
    """スキャン（欄の倍率が 1 ± 0.1 の中）では、枠の線を「なし」にした欄に要確認を付けない"""
    path = make_scan("2027bun_01", tmp_path / "scan.pdf", filled=True)
    res = pipeline.analyze(imaging.open_pages(path)[0], formset=frameless_forms())
    assert res.form == forms.FUYOU and res.note == ""
