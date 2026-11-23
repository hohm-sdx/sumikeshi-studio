# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""印刷された罫線を線分として読む処理（rulings.py）のテスト"""

import cv2
import numpy as np
import pytest
from scan import page_image, table

from sumikeshi_studio import rulings


def test_lines_are_read_without_text_strokes():
    """表の罫線を線分として読み、文字の画は線にしない。線は (位置, 端, 端, 太さ) で、位置・端の順に並ぶ"""
    rows, cols = [200, 300, 400, 520], [200, 600, 1000, 1400]
    hs, vs = rulings.read(table(rows, cols))
    assert [round(s[0]) for s in hs] == rows
    assert [round(s[0]) for s in vs] == cols
    assert all(abs(s[1] - 200) <= 3 and abs(s[2] - 1400) <= 3 for s in hs)
    assert all(1 <= s[3] <= rulings.MAX_T for s in (*hs, *vs))


def test_lines_not_in_rulings_are_returned_separately():
    """read_all は、罫線（read と同じ）と、線として読めたが罫線に入れなかった線を返す。罫線に入れなかった線は、端が
    ほかの向きの線につながらない短い線（縦の短い線と、横の短い線）"""
    rows, cols = [200, 400, 600], [200, 800, 1400]
    g = table(rows, cols)
    cv2.line(g, (1000, 260), (1000, 340), 0, 2)  # 上下の横の線から離れた、縦の短い線
    cv2.line(g, (300, 500), (420, 500), 0, 2)  # 左右の縦の線から離れた、横の短い線
    hs, vs, other_h, other_v = rulings.read_all(g)
    assert (hs, vs) == rulings.read(g)
    assert [round(s[0]) for s in hs] == rows and [round(s[0]) for s in vs] == cols
    assert [(round(s[0]), s[1], s[2]) for s in other_v] == [(1000, 260, 343)]
    assert [(round(s[0]), s[1], s[2]) for s in other_h] == [(500, 300, 423)]


def test_thin_light_lines_and_unconnected_short_lines():
    """2値にすると落ちる細く薄い線も読む。端がほかの向きの線につながらない短い線（文字の画）は除き、長い線は残す"""
    rows, cols = [200, 520], [200, 600, 1000, 1400]
    g = table(rows, cols)
    g[360, 200:1400] = 225  # 1画素の薄い横の線（左右の縦の線につながる）
    cv2.line(g, (300, 700), (420, 700), 0, 2)  # どの線にもつながらない短い横の画
    cv2.line(g, (200, 900), (1400, 900), 0, 2)  # どの線にもつながらない長い横の線
    hs, _ = rulings.read(g)
    assert sorted({round(s[0]) for s in hs}) == [200, 360, 520, 900]
    # 薄い線は、縦の線と交わる所の前後（約17画素）で切れて、ますごとの線分になる
    thin = [s for s in hs if round(s[0]) == 360]
    assert len(thin) == 3
    assert min(s[1] for s in thin) <= 220 and max(s[2] for s in thin) >= 1380


def test_text_strokes_are_not_read_as_lines():
    """同じ高さに並ぶ字の画（すき間が9画素未満）は、つながって線にならない。罫線に触れた字も、罫線から離して消すので、
    罫線の位置と端は変わらない。点線（点が小さく、すき間が9画素未満）は線として読む"""
    rows, cols = [200, 520, 840], [200, 800, 1400]
    plain = table(rows, cols)
    for y in rows[:-1]:  # table が書いた「AB」を消して、線だけの表にする
        for x in cols[:-1]:
            plain[y + 5 : y + 40, x + 5 : x + 60] = 255
    g = plain.copy()
    for k in range(40):  # 高さ12画素・幅8画素の字に見立てた四角を、5画素おきに並べた行
        x = 230 + 13 * k
        cv2.rectangle(g, (x, 300), (x + 7, 311), 0, 1)
    for k in range(20):  # 表の横の線（y=520）に上から触れた字
        x = 240 + 13 * k
        cv2.rectangle(g, (x, 508), (x + 7, 520), 0, 1)
    for x in range(820, 1380, 6):  # 縦の線につながる点線（点は3×2画素、すき間は3画素）
        g[660:662, x : x + 3] = 0
    hs, vs = rulings.read(g)
    plain_h, plain_v = rulings.read(plain)
    assert vs == plain_v
    assert tuple(s for s in hs if round(s.pos) != 661) == plain_h
    dotted = [s for s in hs if round(s.pos) == 661]
    assert len(dotted) == 1 and dotted[0].a <= 825 and dotted[0].b >= 1375


def test_line_end_cut_at_image_margin_counts_as_connected():
    """画像の端から除く所（3%）で切られた縦の線の端は、ほかの向きの線につながっているとみなす。端の先の外枠は
    読めないので、つながりを問うと、表の下の端まである短い縦の線が落ちる"""
    g = np.full((1200, 1600), 255, np.uint8)
    cv2.rectangle(
        g, (200, 200), (1400, 1190), 0, 2
    )  # 下の辺（y=1190）は、除く所（y>=1164）の中
    cv2.line(g, (200, 1000), (1400, 1000), 0, 2)
    cv2.line(
        g, (800, 1000), (800, 1190), 0, 2
    )  # 横の線から下の辺までの、250画素未満の縦の線
    cv2.line(g, (500, 1040), (500, 1120), 0, 2)  # どちらの端もつながらない縦の線
    _, vs = rulings.read(g)
    xs = sorted({round(s.pos) for s in vs})
    assert xs == [200, 800, 1400]
    inner = next(s for s in vs if round(s.pos) == 800)
    assert inner.a <= 1003 and inner.b >= 1160 and inner.b - inner.a < rulings.LONG


# 薄い横の線（y=360〜361）の、濃い所の中にある薄い所（x の範囲）。縦の線（x=200・600・1000・1400）からは離す
FAINT_GAPS = [
    (290, 320),
    (470, 500),
    (700, 730),
    (860, 890),
    (1110, 1140),
    (1270, 1300),
]


def broken_line_table(paper: int, noise: float) -> np.ndarray:
    """紙の地の濃さが paper の表に、所どころが薄い横の線を引いた画像。線は、紙の地より40暗い所（今のしきい値で読める）と
    11暗い所（今のしきい値では読めず、低いしきい値で読める）が交互に続く。noise は足す雑音の標準偏差"""
    g = table([200, 520], [200, 600, 1000, 1400]).astype(np.float32) * paper / 255
    g[360:362, 200:1400] = paper - 40
    for a, b in FAINT_GAPS:
        g[360:362, a:b] = paper - 11
    for x in (200, 600, 1000, 1400):  # 薄い線で上書きした縦の線を引き直す
        cv2.line(g, (x, 200), (x, 520), 0, 2)
    if noise:
        g = g + np.random.default_rng(0).normal(0, noise, g.shape)
    return np.clip(g, 0, 255).astype(np.uint8)


@pytest.mark.parametrize(
    ("paper", "noise", "quiet"), [(255, 0.0, True), (230, 2.5, False)]
)
def test_fragments_of_faint_line_are_joined(monkeypatch, paper, noise, quiet):
    """所どころがしきい値を下回る薄い線は、切れ端をつないで、端から端までの1本の線として読む。雑音の小さいページでは
    低いしきい値で読んだ線に沿って、雑音の大きいページでは線の向きに平均した画像の線に沿ってつなぐ。つながないと、
    切れ端は、端がほかの向きの線につながらないので落ちる"""
    g = broken_line_table(paper, noise)
    assert (rulings._noise(g) <= rulings.QUIET) == quiet
    hs, vs = rulings.read(g)
    faint = [s for s in hs if abs(s.pos - 361) <= 2]
    assert len(faint) == 1 and faint[0].a <= 205 and faint[0].b >= 1395
    assert sorted(round(s.pos) for s in hs if abs(s.pos - 361) > 2) == [200, 520]
    assert sorted({round(s.pos) for s in vs}) == [200, 600, 1000, 1400]
    monkeypatch.setattr(rulings, "_joined", lambda parts, low, to_ends: (parts, []))
    pieces = [s for s in rulings.read(g)[0] if abs(s.pos - 361) <= 2]
    assert sum(s.b - s.a for s in pieces) < 600


def faint_scan(img: np.ndarray) -> np.ndarray:
    """薄いスキャンに見立てた画像：ぼかし（σ1.3）、濃さを紙の地200・墨110にし、雑音（標準偏差2.5）を足して、JPEG
    （品質70）にする"""
    x = cv2.GaussianBlur(img, (0, 0), 1.3)
    x = 110 + x.astype(np.float32) / 255 * (200 - 110)
    x = x + np.random.default_rng(0).normal(0, 2.5, x.shape)
    x = np.clip(x, 0, 255).astype(np.uint8)
    return cv2.imdecode(cv2.imencode(".jpg", x, [cv2.IMWRITE_JPEG_QUALITY, 70])[1], 0)


def test_lines_of_faint_scan_are_read():
    """薄いスキャンに見立てた保険料控除申告書で、元の画像で読める罫線のうち、長さの半分以上を読みこぼす線の数が、
    横は12本以下、縦は45本以下（切れ端をつながない読み取りでは、横32本・縦64本を読みこぼした。元の画像の線は
    横67本・縦87本）"""
    img = page_image("2026bun_04")
    ref_h, ref_v = rulings.read(img)
    assert (len(ref_h), len(ref_v)) == (67, 87)
    hs, vs = rulings.read(faint_scan(img))

    def missed(ref, got):
        n = 0
        for s in ref:
            near = [t for t in got if abs(t.pos - s.pos) <= 4]
            read = sum(max(0, min(s.b, t.b) - max(s.a, t.a)) for t in near)
            n += read < 0.5 * (s.b - s.a)
        return n

    assert missed(ref_h, hs) <= 12 and missed(ref_v, vs) <= 45


def _random_lines(rng, n: int, span: int) -> list[rulings.Line]:
    """位置が 0.5 刻み・端が整数の、でたらめな線（位置の同じ線、端のそろった線を含む）"""
    out = []
    for _ in range(n):
        a = int(rng.integers(0, span - 40))
        out.append(
            rulings.Line(
                float(rng.integers(0, 2 * span)) / 2,
                a,
                a + int(rng.integers(5, 200)),
                1,
            )
        )
    return out


def test_touching_and_merge_match_their_definitions_on_random_lines():
    """線の端がほかの向きの線の上にあるか（_touching）と、重なる線を除いて足す処理（_merge）は、位置の近い組だけを
    調べる。全部の組を1つずつ調べた結果と同じになる（境の TOUCH・SAME ちょうどの組を含む）"""
    rng = np.random.default_rng(3)
    for n in (1, 7, 300):
        own, others = _random_lines(rng, n, 400), _random_lines(rng, n, 400)
        x = np.array(own, float)
        want = [
            any(
                abs(o[0] - s[1]) <= rulings.TOUCH
                and o[1] - rulings.TOUCH <= s[0] <= o[2] + rulings.TOUCH
                for o in others
            )
            for s in own
        ]
        assert rulings._touching(x[:, 1], x[:, 0], others).tolist() == want
        kept = [
            b
            for b in others
            if not any(
                abs(b[0] - a[0]) <= rulings.SAME
                and min(b[2], a[2]) - max(b[1], a[1])
                >= 0.5 * min(b[2] - b[1], a[2] - a[1])
                for a in own
            )
        ]
        assert rulings._merge(own, others) == sorted(
            own + kept, key=lambda s: (s[0], s[1])
        )
        if (
            n == 300
        ):  # どちらの結果も混ざっている（片方だけの組では、確かめたことにならない）
            assert 0 < sum(want) < n and 0 < len(kept) < n
    exact = [
        rulings.Line(100.0, 10, 50, 1),
        rulings.Line(100.0 + rulings.TOUCH, 10, 50, 1),
    ]
    ends = np.array([100.0, 100.0 + 2 * rulings.TOUCH + 0.5])
    assert rulings._touching(ends, np.array([20.0, 20.0]), exact).tolist() == [
        True,
        False,
    ]
    assert rulings._touching(ends, np.array([20.0, 20.0]), []).tolist() == [
        False,
        False,
    ]
    assert rulings._merge([], exact) == exact and rulings._merge(exact, []) == exact


def whole_image_segments(mask: np.ndarray, horizontal: bool) -> list:
    """rulings._segments と同じ線を、行（列）を詰めずに、画像全体の墨のかたまりから求める形"""
    _, st = rulings._components(mask)
    out = []
    for x, y, w, h, area in st[1:]:
        if horizontal and w >= 3 * h:
            out.append(
                rulings.Line(
                    float(y + h / 2), int(x), int(x + w), max(1, round(area / w))
                )
            )
        elif not horizontal and h >= 3 * w:
            out.append(
                rulings.Line(
                    float(x + w / 2), int(y), int(y + h), max(1, round(area / h))
                )
            )
    return sorted(out, key=lambda s: (s[0], s[1]))


@pytest.mark.parametrize("horizontal", [True, False])
def test_segments_match_components_of_whole_image(horizontal):
    """線の画像の墨のかたまりを、墨のない行（縦の線は列）を詰めてから求めても、画像全体から求めたときと同じ線に
    なる。1画素だけ離れた線・斜めに接する線・画像の端の線・同じ位置で入れ子になる線を含む、乱数の線の画像で"""
    rng = np.random.default_rng(3)
    for case in range(60):
        mask = np.zeros((240, 320), np.uint8)
        for _ in range(int(rng.integers(1, 40))):
            y, t = int(rng.integers(0, 236)), int(rng.integers(1, 5))
            x, length = int(rng.integers(0, 280)), int(rng.integers(3, 200))
            mask[y : y + t, x : x + length] = 255
        if case % 3 == 0:  # 画像の最初と最後の行にも線を置く
            mask[0, 10:200] = 255
            mask[-1, 50:300] = 255
        if case % 4 == 0:
            # コの字の線の内側に、つながらない線：2つのかたまりの位置と始まりが同じになる
            mask[100:105, 20:300] = 0
            mask[98:107, 18:302] = 0
            mask[100, 20:300] = mask[104, 20:300] = 255
            mask[100:105, 280:300] = 255
            mask[102, 20:270] = 255
        if not horizontal:
            mask = np.ascontiguousarray(mask.T)
        got = rulings._segments(mask, horizontal)
        assert got == whole_image_segments(mask, horizontal)
        assert got or case % 3  # 線が1本もない画像だけを比べていない


def test_noise_matches_median_of_all_pixels(monkeypatch):
    """雑音の大きさは、隣の画素との差の値ごとの数から求めても、全部の差を並べて中央値と平均を求めたときと同じ値に
    なる（画素の数が奇数・偶数、値ごとの数を分けて数える大きさを含む）"""

    def sorted_form(gray):
        d = np.abs(gray[:, 1:].astype(np.int16) - gray[:, :-1])
        cap = 3 * (float(np.median(d)) + 1)
        return float(d[d <= cap].mean()) * np.sqrt(np.pi) / 2

    rng = np.random.default_rng(5)
    images = [np.full((40, 50), 230, np.uint8), np.full((1, 2), 7, np.uint8)]
    for sigma, shape in (
        (0.5, (60, 81)),
        (2.0, (61, 80)),
        (8.0, (33, 33)),
        (40.0, (64, 64)),
    ):
        g = np.clip(200 + rng.normal(0, sigma, shape), 0, 255).astype(np.uint8)
        g[10:14, 5:30] = 0  # 線や字のふちの大きい差
        images.append(g)
    images.append(rng.integers(0, 256, (37, 41)).astype(np.uint8))
    for pixels in (rulings._HIST_PIXELS, 500):
        monkeypatch.setattr(rulings, "_HIST_PIXELS", pixels)
        for g in images:
            assert rulings._noise(g) == sorted_form(g)
    assert np.isnan(rulings._noise(np.full((30, 1), 200, np.uint8)))


def test_text_sized_and_thresholds_match_plain_forms():
    """字の大きさの墨のかたまりの画像と、値がしきい値以上の画素の画像は、画素ごとに判定した形と同じになる"""
    rng = np.random.default_rng(7)
    ink = np.zeros((300, 400), np.uint8)
    for _ in range(120):
        y, x = int(rng.integers(0, 290)), int(rng.integers(0, 390))
        h, w = int(rng.integers(1, 70)), int(rng.integers(1, 70))
        ink[y : y + h, x : x + w] = 255
    ink[::7] = 0
    ink[:, ::9] = 0
    labels, stats = rulings._components(ink)
    w, h = stats[:, 2], stats[:, 3]
    text = (np.minimum(w, h) >= rulings.TEXT_MIN) & (
        np.maximum(w, h) < rulings.TEXT_MAX
    )
    text[0] = False
    want = np.where(text[labels], 255, 0).astype(np.uint8)
    assert 0 < np.count_nonzero(want) < np.count_nonzero(ink)
    assert np.array_equal(rulings._text_sized(ink), want)
    gray = rng.integers(0, 256, (50, 60)).astype(np.uint8)
    for level in (1, 5, 8, 12, 255):
        want = np.where(gray >= level, 255, 0).astype(np.uint8)
        assert np.array_equal(rulings._at_least(gray, level), want)


def test_components_fall_back_when_labels_do_not_fit_in_16_bits():
    """墨のかたまりが16ビットに収まらない数あるページでも、32ビットで求めたときと同じ結果になる。墨のない画像からは、
    線を読まない"""
    dots = np.zeros((600, 600), np.uint8)
    dots[::2, ::2] = 255  # 離れた点が 90,000 個
    dots[300:303, 100:400] = 255
    labels, stats = rulings._components(dots)
    _, labels32, stats32, _ = cv2.connectedComponentsWithStats(dots, connectivity=8)
    assert len(stats) == len(stats32) > 2**16
    assert np.array_equal(labels, labels32) and np.array_equal(stats, stats32)
    few = np.zeros((600, 600), np.uint8)
    few[300:303, 100:400] = 255
    labels, stats = rulings._components(few)
    assert labels.dtype == np.uint16 and stats.tolist()[1] == [100, 300, 300, 3, 900]
    assert rulings._segments(few, True) == [rulings.Line(301.5, 100, 400, 3)]
    assert rulings._segments(np.zeros((600, 600), np.uint8), True) == []
    assert rulings.read(np.full((1654, 2339), 255, np.uint8)) == ((), ())


def test_horizontal_line_cut_at_image_margin_counts_as_connected():
    """画像の端から除く所で切られた横の線の端も、つながっているとみなす（左の端・右の端）。どちらの端も
    つながらない、同じ長さの横の線は読まない"""
    g = np.full((1200, 1600), 255, np.uint8)
    cv2.line(g, (0, 400), (200, 400), 0, 2)  # 左の端（除く所の中）から始まる
    cv2.line(g, (1400, 500), (1599, 500), 0, 2)  # 右の端まで続く
    cv2.line(g, (700, 600), (860, 600), 0, 2)  # 画像の中ほどの、どこにもつながらない線
    hs, _ = rulings.read(g)
    assert [(round(s.pos), s.a <= 50, s.b >= 1550) for s in hs] == [
        (400, True, False),
        (500, False, True),
    ]
    assert all(s.b - s.a < rulings.LONG for s in hs)


def faint_line_table(a: int, b: int) -> np.ndarray:
    """表（縦の線は x=200・600・1000・1400）の y=360〜361 の x=a〜b に、紙の地より10だけ暗い横の線を引いた、雑音の
    ない画像。この線は、今のしきい値では読めず、低いしきい値でだけ読める"""
    g = table([200, 520], [200, 600, 1000, 1400])
    g[360:362, a:b] = 245
    for x in (200, 600, 1000, 1400):
        cv2.line(g, (x, 200), (x, 520), 0, 2)
    return g


def test_line_read_only_at_low_threshold_is_added_when_both_ends_connect():
    """低いしきい値でだけ読めた線（切れ端のない線）は、NEW_H 以上の長さで、両端がほかの向きの線につながるものだけを
    足す。片端だけがつながる線と、短い線は足さない"""

    def faint(img):
        return [s for s in rulings.read(img)[0] if abs(s.pos - 361) <= 2]

    whole = faint_line_table(200, 1400)
    assert rulings._raw(whole, rulings.INK_DIFF, rulings.INK_HAT)[0] == [
        s for s in rulings.read(whole)[0] if abs(s.pos - 361) > 2
    ]  # 今のしきい値では読めていない
    (line,) = faint(whole)
    assert line.a <= 205 and line.b >= 1395
    assert faint(faint_line_table(200, 500)) == []  # 右の端がつながらない
    short = rulings.NEW_H - 10
    assert faint(faint_line_table(200, 200 + short)) == []


def test_double_line_stays_two_lines_when_joined_along_one_wide_line():
    """3画素離れた二重線は、線の向きに平均した画像では1本の太い線として読まれる。その線に沿ってつなぐときも、
    位置の差が NEAR を超える切れ端は別の線のままにする"""
    g = table([200, 520], [200, 1400])
    g[360, 200:1400] = 215
    g[363, 200:1400] = 215
    g[361:363, 200:1400] = 244  # 2本のあいだの薄い所
    for x in (200, 1400):
        cv2.line(g, (x, 200), (x, 520), 0, 2)
    along, _ = rulings._lines(*rulings._along(g), g.shape)
    assert len([s for s in along if abs(s.pos - 362) <= 3]) == 1
    double = [s.pos for s in rulings.read(g)[0] if abs(s.pos - 362) <= 3]
    assert double == [360.5, 363.5]
    assert double[1] - double[0] > rulings.NEAR


def test_noisy_page_does_not_gain_lines_from_low_threshold():
    """雑音の大きいページ（雑音の見込みが QUIET を超える）では、画素ごとの低いしきい値の線を使わないので、雑音から
    読まれる線が増えない。保険料控除申告書に標準偏差2.5の雑音を足したページで、横72本・縦88本以下（元の画像は
    横67本・縦87本。低いしきい値を使い続けると、横78本・縦92本になった）"""
    img = page_image("2026bun_04")
    noisy = np.clip(
        img.astype(np.float32) + np.random.default_rng(0).normal(0, 2.5, img.shape),
        0,
        255,
    ).astype(np.uint8)
    assert rulings._noise(noisy) > rulings.QUIET
    hs, vs = rulings.read(noisy)
    assert len(hs) <= 72 and len(vs) <= 88


def test_short_solid_line_touched_by_text_is_kept():
    """字が触れた短い縦の線（高さ45画素。字と合わせると字の大きさのかたまりになる）は、SOLID 以上続く実線として
    先に取り出すので、字と一緒に消さない"""
    g = np.full((1200, 1600), 255, np.uint8)
    for y in (300, 345):
        cv2.line(g, (200, y), (1400, y), 0, 2)
    for x in (200, 800, 1400):
        cv2.line(g, (x, 300), (x, 345), 0, 2)
    cv2.putText(
        g, "8", (801, 337), cv2.FONT_HERSHEY_SIMPLEX, 0.9, 0, 2
    )  # x=800 の線に触れる
    assert rulings.SOLID <= 45 < rulings.TEXT_MAX
    _, vs = rulings.read(g)
    assert [round(s.pos) for s in vs] == [200, 800, 1400]
