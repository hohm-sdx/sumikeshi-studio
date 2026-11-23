# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""類似した様式（令和8年分と令和9年分の扶養控除等申告書など）の解析のテスト

類似した様式は、公開様式の扶養控除等申告書 令和8年分（samples/2026bun_01.pdf）から、配布用パッケージの扶養控除等申告書の範囲を移して
テストの中で作る（リポジトリには入れない）。
"""

import dataclasses

import pytest
from conftest import cached_analyze, needs_sample_scan
from scan import PHOTO_LONG, PHOTO_SHORT, SAMPLE_SCAN, make_photo, page_image
from userforms import R8_NAME, bundled, bundled_forms, make_r8

from sumikeshi_studio import analysis, formcheck, forms, imaging, titles


@pytest.fixture(scope="module")
def year_forms(tmp_path_factory):
    """令和8年分の様式の題名の語を、年分と帳票の名前をスペースで区切った1行にした組（画面から登録するときの書き方）"""
    return make_r8(tmp_path_factory.mktemp("year"), ("令和8年分　扶養控除等",))


@pytest.fixture(scope="module")
def no_year_forms(tmp_path_factory):
    """令和8年分の様式の題名の語に年分の語がない組（配布用パッケージの扶養控除等申告書の「扶養控除等」と同じ語だけ）"""
    return make_r8(tmp_path_factory.mktemp("noyear"), ("扶養控除等",))


def analyze(path, formset):
    return cached_analyze(imaging.open_pages(path)[0], formset=formset)


@pytest.mark.parametrize(
    ("form", "expected"),
    [("2026bun_01", R8_NAME), ("2027bun_01", forms.FUYOU), ("2025bun_01", forms.FUYOU)],
)
def test_title_decides_among_similar_forms(form, expected, year_forms, scan):
    """題名の語を「令和8年分　扶養控除等」の1行にすれば、令和8年分のページは令和8年分の様式（合った行が10文字で、
    配布用パッケージの扶養控除等申告書の「扶養控除等」より長い）、令和9年分・令和7年分のページは配布用パッケージの様式（「令和8年分」がないので
    その行は合わない）に決まり、「⚠要確認」は付かない"""
    res = analyze(scan(form), year_forms)
    assert [s.name for s in res.used] == [expected]
    assert res.note == ""
    assert {s.name for s in res.similar} == {R8_NAME, forms.FUYOU}
    assert len(res.boxes) == 10


@needs_sample_scan
def test_sample_scan_is_decided_by_band_title(year_forms):
    """見本のスキャン（令和9年分。題名の上に注意書きがある）も、見本の上の帯で題名を読んで配布用パッケージの様式に決まる"""
    res = cached_analyze(imaging.open_pages(SAMPLE_SCAN)[0], formset=year_forms)
    assert [s.name for s in res.used] == [forms.FUYOU]
    assert res.note == ""


def test_same_length_words_cannot_decide(no_year_forms, scan):
    """合った行の長さが同じ（どちらも「扶養控除等」）なら決められず、より類似している（点の多い）様式の範囲で
    墨消しし、「⚠要確認」を付ける"""
    res = analyze(scan("2026bun_01"), no_year_forms)
    first, second = (s.name for s in res.similar)
    assert [s.name for s in res.used] == [first]
    assert res.note == titles.similar_note([first, second])
    assert res.note.endswith(f"『{first}』で墨消ししました") and len(res.boxes) == 10


def test_photo_with_two_papers_keeps_separate_forms(year_forms, tmp_path):
    """写真に別々の帳票が2枚写っていれば、今までどおり別の帳票として墨消しする。類似した様式がある帳票は、その用紙の
    題名で決める"""
    photo = tmp_path / "photo.jpg"
    # test_photos.py の2枚の写真と同じ置き方（縦長の写真の上と下）
    make_photo(
        [
            (
                page_image("2027bun_01"),
                PHOTO_SHORT / 2,
                PHOTO_LONG * 0.26,
                2750,
                1,
                0.05,
            ),
            (
                page_image("2026bun_04"),
                PHOTO_SHORT / 2,
                PHOTO_LONG * 0.75,
                2750,
                -1.5,
                0.05,
            ),
        ],
        photo,
        portrait=True,
    )
    res = cached_analyze(imaging.open_pages(photo)[0], formset=year_forms)
    assert {s.name for s in res.used} == {forms.FUYOU, forms.HOKEN}
    assert "帳票が2枚写っています" in res.note
    assert "決められませんでした" not in res.note


def _spec(name, words=(), excludes=()):
    base = bundled("nta-fuyou-r9")
    return dataclasses.replace(
        base, id=f"u-{len(name)}", name=name, title_words=words, title_excludes=excludes
    )


TITLE8 = "令和8年分 給与所得者の扶養控除等（異動）申告書"
TITLE9 = "令和9年分 給与所得者の扶養控除等（異動）申告書"


def test_title_choice_rules():
    """同じ用紙の部分の候補の判別：スペースで区切った行は、区切ったすべての語が題名にあるときだけ合い、合った行が
    最も長い様式に決める。並んだら、どれも合わなければ、決めない。年分の特別な扱いはしない"""
    fuyou = bundled("nta-fuyou-r9")
    r8 = _spec("令和8年分", ("令和8年分　扶養控除等",))
    assert titles.title_choice(TITLE8, [fuyou, r8]) == r8  # 10文字と5文字
    assert titles.title_choice(TITLE9, [fuyou, r8]) == fuyou  # 「令和8年分」がない
    # 半角と全角は区別しない（NFKC）。区切りは半角のスペースでもよく、区切った語の順は問わない
    assert titles.title_choice("令和８年分給与所得者の扶養控除等", [fuyou, r8]) == r8
    for line in ("令和8年分 扶養控除等", "扶養控除等　令和8年分"):
        assert titles.title_choice(TITLE8, [fuyou, _spec("別", (line,))]).name == "別"
    # 「令和8年分」と「扶養控除等」を別々の行にすると、どちらの年分でも配布用パッケージの様式と同じ5文字で並び、決めない
    separate = _spec("別々の行", ("扶養控除等", "令和8年分"))
    assert titles.title_choice(TITLE8, [fuyou, separate]) is None
    assert titles.title_choice(TITLE9, [fuyou, separate]) is None
    # 「令和8年分　扶養控除等」の行があっても、「扶養控除等」だけの行も置くと、令和9年分では並ぶ
    both = _spec("両方の行", ("令和8年分　扶養控除等", "扶養控除等"))
    assert titles.title_choice(TITLE8, [fuyou, both]) == both
    assert titles.title_choice(TITLE9, [fuyou, both]) is None
    # どれも合わない。空白とかっこだけの行は合わない
    assert titles.title_choice("給与所得者の保険料控除申告書", [fuyou, r8]) is None
    assert titles.title_choice(TITLE8, [_spec("かっこだけ", ("（ ）",))]) is None
    # 限界：年分を読めないと、令和8年分の様式の行は合わず、配布用パッケージの様式に決まる
    assert (
        titles.title_choice("給与所得者の扶養控除等（異動）申告書", [fuyou, r8])
        == fuyou
    )
    # 除く語のある様式は外す。除く語も、区切ったすべての語が題名にあるときだけ当たる
    excluded = _spec("除く語", ("令和8年分　扶養控除等",), ("扶養控除等",))
    assert titles.title_choice(TITLE8, [fuyou, excluded]) == fuyou
    split = _spec("区切った除く語", ("令和8年分　扶養控除等",), ("令和8年分　保険料",))
    assert titles.title_choice(TITLE8, [fuyou, split]) == split  # 「保険料」がない
    split = _spec("区切った除く語", ("令和8年分　扶養控除等",), ("申告書　令和8年分",))
    assert titles.title_choice(TITLE8, [fuyou, split]) == fuyou


def test_title_advice_rules():
    """保存の直前の確認：2つの見本の題名で見分けられなければ、題名の語に入れる1行の案と、消す行を
    示す。案を足しても見分けられないとき（年分が同じ、年分を読めない）は、案を作らない"""
    fuyou = bundled("nta-fuyou-r9")
    new = _spec("扶養控除等申告書（令和8年分）", ("扶養控除等",))
    assert formcheck.title_advice(new, TITLE8, fuyou, TITLE9) == (
        "令和8年分　扶養控除等",
        # 配布用パッケージの様式の見本の題名にも、その様式の行と同じ長さで合う
        ("扶養控除等",),
    )
    # 案のとおりの1行なら見分けられる
    ok = _spec("扶養控除等申告書（令和8年分）", ("令和8年分　扶養控除等",))
    assert formcheck.title_advice(ok, TITLE8, fuyou, TITLE9) is None
    # 年分のない長い行は、配布用パッケージの様式の見本の題名に、その様式の行より長く合う（令和9年分のページが新しい様式に決まる）
    long = _spec("長い行", ("給与所得者の扶養控除等",))
    assert formcheck.title_advice(long, TITLE8, fuyou, TITLE9) == (
        "令和8年分　扶養控除等",
        ("給与所得者の扶養控除等",),
    )
    # 2つの見本の年分が同じ、新しい見本の題名から年分を読めない：案を作れない
    assert formcheck.title_advice(new, TITLE9, fuyou, TITLE9) == ("", ())
    unread = "給与所得者の扶養控除等（異動）申告書"
    assert formcheck.title_advice(new, unread, fuyou, TITLE9) == ("", ())


def test_save_check_reads_titles_of_similar_templates():
    """保存の前の確認で、見本が類似した様式があれば、2つの見本の上の帯で題名を読み、見分けられないものだけを返す"""
    work = page_image("2026bun_01")  # 令和8年分
    fields, _ = formcheck.move_ranges(work, bundled("nta-fuyou-r9"))
    rects = [(f.x, f.y, f.w, f.h) for f in fields]
    others = list(bundled_forms())
    new = dataclasses.replace(
        bundled("nta-fuyou-r9"), id="u-new", name="扶養控除等申告書（令和8年分）"
    )  # 題名の語は、配布用パッケージの様式と同じ「扶養控除等」だけ
    _, check = analysis.build_form(work, True, rects, others, new)
    assert [name for name, _ in check.conflicts] == [forms.FUYOU]
    assert check.unclear == ((forms.FUYOU, "令和8年分　扶養控除等", ("扶養控除等",)),)
    ok = dataclasses.replace(new, title_words=("令和8年分　扶養控除等",))
    _, check = analysis.build_form(work, True, rects, others, ok)
    assert check.conflicts and check.unclear == ()
