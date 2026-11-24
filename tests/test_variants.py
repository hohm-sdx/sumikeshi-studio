# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""バリエーションの欄を、見本の罫線の並びで探す処理（variants.py）と、解析の流れへの組み込みのテスト"""

import dataclasses
import math
import random

import cv2
import numpy as np
import pytest
import scan
from PIL import Image
from scan import GRAY_DPI, page_image, table, written_page
from userforms import bundled, bundled_forms

from sumikeshi_studio import (
    align,
    formcheck,
    forms,
    formstore,
    frames,
    imaging,
    pipeline,
    rulings,
    variants,
)
from sumikeshi_studio.formstore import Line


def _loop_align(T, P, fmap, at_t, at_p, scale_t, scale_p, across):
    """align_lines と同じ重みを、組ごとのくり返しで求める形（numpy でまとめて計算する前の形）"""
    V = variants
    n, m = len(T), len(P)
    if n == 0 or m == 0:
        return [], n * V.GAP + m * V.GAP_P
    D = np.full((n, m), V.INF)
    prev = np.full((n, m, 2), -1, np.int32)
    local = np.zeros((n, m))
    for i in range(n):
        ft = fmap(T[i].pos)
        for k in range(m):
            local[i, k] = V._desc_cost(
                T[i], P[k], at_t, at_p, scale_t, scale_p
            ) + V.POS_W * min(abs(ft - P[k].pos) / V.POS_SIGMA, V.POS_CAP)
    for i in range(n):
        for k in range(m):
            best, arg = i * V.GAP + k * V.GAP_P, (-1, -1)
            for i2 in range(max(0, i - V.SKIP - 1), i):
                dt = T[i].pos - T[i2].pos
                for k2 in range(max(0, k - V.SKIP_P - 1), k):
                    dp = P[k].pos - P[k2].pos
                    if D[i2, k2] >= V.INF or dt <= 0 or dp <= 0:
                        continue
                    c = (
                        D[i2, k2]
                        + (i - i2 - 1) * V.GAP
                        + (k - k2 - 1) * V.GAP_P
                        + V.SPACE_W
                        * min(abs(math.log(dp / (across * dt))), V.SPACE_CAP)
                    )
                    if c < best:
                        best, arg = c, (i2, k2)
            D[i, k] = best + local[i, k]
            prev[i, k] = arg
    end = (
        D
        + (n - 1 - np.arange(n))[:, None] * V.GAP
        + (m - 1 - np.arange(m))[None, :] * V.GAP_P
    )
    i, k = np.unravel_index(int(np.argmin(end)), end.shape)
    cost = float(end[i, k])
    out = []
    while i >= 0 and k >= 0:
        out.append((int(i), int(k)))
        i, k = prev[i, k]
    return out[::-1], cost


def test_align_lines_matches_loop_form():
    """numpy でまとめて計算する対応づけは、組ごとのくり返しで求める形と同じ対応と重みを返す（乱数の線の列で）"""
    rng = random.Random(7)
    for _ in range(30):
        n, m = rng.randint(1, 18), rng.randint(1, 30)

        def segs(count):
            pos = sorted(rng.uniform(50, 1100) for _ in range(count))
            return [
                Line(
                    round(p, 1),
                    rng.randint(0, 600),
                    rng.randint(700, 1500),
                    rng.randint(1, 6),
                )
                for p in pos
            ]

        T, P = segs(n), segs(m)
        fmap = variants.Map1D([0, 1200], [0, 1200 * rng.uniform(0.8, 1.2)])
        args = (
            T,
            P,
            fmap,
            650.0,
            640.0,
            1000.0,
            rng.uniform(800, 1200),
            rng.uniform(0.7, 1.4),
        )
        got, want = variants.align_lines(*args), _loop_align(*args)
        assert got[0] == want[0] and abs(got[1] - want[1]) < 1e-6


def test_align_prefers_earlier_lines_among_equal_weights():
    """重みが同じ組は、1つ前の見本の線、ページの線の番号の小さいものを選ぶ（同じ位置に線が2本あって、どちらから
    つないでも重みが同じになる列で）"""

    def segs(ys):
        return [Line(float(y), 100, 900, 2) for y in ys]

    # 見本の線0と線1が同じ位置：線2の1つ前は、線0（線1を抜けにする）でも線1（線0を抜けにする）でも同じ重み
    T, P = segs([100, 100, 200]), segs([100, 200])
    desc = np.array([[0.0, 9.0], [0.0, 9.0], [9.0, 0.0]])
    ft = np.array([100.0, 100.0, 200.0])
    assert variants._align(T, P, desc, ft, 1.0) == ([(0, 0), (2, 1)], variants.GAP)
    # ページの線0と線1が同じ位置：線2の1つ前は、線0（線1を余分にする）でも線1（線0を余分にする）でも同じ重み
    T, P = segs([100, 200]), segs([100, 100, 200])
    desc = np.array([[0.0, 0.0, 9.0], [9.0, 9.0, 0.0]])
    ft = np.array([100.0, 200.0])
    assert variants._align(T, P, desc, ft, 1.0) == ([(0, 0), (1, 2)], variants.GAP_P)


def test_align_keeps_order_with_missing_and_extra_lines():
    """行の高さが違い、見本の線が1本抜け、ページに余分な線があっても、順番を保って正しく対応させる"""

    def segs(ys):
        return [Line(float(y), 100, 900, 2) for y in ys]

    T = segs([100, 200, 300, 400, 500, 600])
    # 見本の 300 がない。行の高さが 0.8 倍。370 に余分な線（短い）
    P = segs([90, 170, 330, 410, 490])
    P.insert(2, Line(370.0, 400, 520, 2))
    P.sort(key=lambda s: s.pos)
    fmap = variants.Map1D([100, 600], [90, 490])
    pairs, _ = variants.align_lines(T, P, fmap, 500, 500, 800, 800, 0.8)
    matched = {T[i].pos: P[k].pos for i, k in pairs}
    assert matched == {100: 90, 200: 170, 400: 330, 500: 410, 600: 490}


def test_field_is_placed_between_corresponding_lines():
    """行の高さと列の幅の違う表で、見本の欄（3行目・2列目のます）を、ページの対応するますに置く。罫線の並びが同じ
    表の点は、並びの違う表の点より高い"""
    t_rows, t_cols = [200, 300, 400, 520, 640], [200, 600, 1000, 1400]
    p_rows, p_cols = [180, 260, 380, 470, 600], [220, 560, 1040, 1380]
    tm = variants.model(rulings.read(table(t_rows, t_cols)))
    pm = variants.model(rulings.read(table(p_rows, p_cols)))
    x, y, w, h = variants.place(tm, pm, (600, 400, 400, 120))
    assert (x, y, x + w, y + h) == pytest.approx((560, 380, 1040, 470), abs=2)
    other = variants.model(rulings.read(table([200, 900], [200, 1400])))
    assert variants.score(tm, pm).template > variants.score(tm, other).template


def ruled(hs, vs, height: int = 800) -> np.ndarray:
    """横の線 [(y, 左の端, 右の端, 濃さ)] と縦の線 [(x, 上の端, 下の端, 濃さ)] を太さ2画素で引いた用紙（幅1600画素の
    グレー）。濃さを省くと黒"""
    g = np.full((height, 1600), 255, np.uint8)
    for y, x0, x1, *ink in hs:
        cv2.line(g, (x0, y), (x1, y), ink[0] if ink else 0, 2)
    for x, y0, y1, *ink in vs:
        cv2.line(g, (x, y0), (x, y1), ink[0] if ink else 0, 2)
    return g


# 見本：4行3列の表。欄は2行目・2列目のます。ページ：2列目の左の線が12画素左にあり、2列目の右の線は、欄の行の中に
# だけ、上下の横の線から離れた短く薄い線（端がほかの線につながらないので、罫線に入らない）。均した位置は、前後の
# 対応した線（588 と 1400）のあいだを均した 994
ROWS = [200, 300, 400, 520]
FIELD = (600, 300, 400, 100)
PAGE_COLS = [(200, 200, 520), (588, 200, 520), (1400, 200, 520)]


def template_table(cols=(200, 600, 1000, 1400), rows=ROWS, top=None, height=800):
    hs = [(y, 200, 1400) for y in rows]
    vs = [
        (x, top if top is not None and x == 1000 else rows[0], rows[-1]) for x in cols
    ]
    return variants.page_model(ruled(hs, vs, height))


def right_edge(tm, page) -> float:
    x, _, w, _ = variants.place(tm, variants.page_model(page), FIELD)
    return x + w


def test_edge_without_corresponding_line_follows_line_not_in_rulings():
    """欄の辺に近い見本の線がページの罫線と対応しないとき、ページで線として読めたが罫線に入れなかった線（短く薄く、
    端がほかの線につながらない線）が、均した位置の近くに1本だけあれば、辺をその線に合わせる。罫線に入れなかった線を
    持たないページの形では、均した位置のまま"""
    tm = template_table()
    page = ruled([(y, 200, 1400) for y in ROWS], [*PAGE_COLS, (1012, 312, 388, 160)])
    pm = variants.page_model(page)
    assert [round(s.pos) for s in pm.vs] == [200, 588, 1400]
    assert [round(s.pos) for s in pm.unruled_vs] == [1012]
    assert rulings.read(page) == (pm.hs, pm.vs)
    x, y, w, h = variants.place(tm, pm, FIELD)
    assert (x, y, x + w, y + h) == pytest.approx((588, 300, 1012, 400), abs=1)
    bare = variants.Model(pm.hs, pm.vs, pm.box)
    assert variants.place(tm, bare, FIELD)[0:3:2] == pytest.approx((588, 406), abs=1)
    # 罫線の一致度は、罫線に入れなかった線によらない
    assert variants.score(tm, pm) == variants.score(tm, bare)


def test_line_beyond_reach_is_not_used(monkeypatch):
    """罫線に入れなかった線が、均した位置から欄の幅の UNRULED_REACH_X より離れていれば、辺を合わせない（36画素。
    欄の幅406画素の5% は約20画素）"""
    tm = template_table()
    page = ruled([(y, 200, 1400) for y in ROWS], [*PAGE_COLS, (1030, 312, 388, 160)])
    assert right_edge(tm, page) == pytest.approx(994, abs=1)
    monkeypatch.setattr(variants, "UNRULED_REACH_X", 0.5)
    assert right_edge(tm, page) == pytest.approx(1030, abs=1)


def test_line_beyond_half_way_to_next_line_is_not_used(monkeypatch):
    """罫線に入れなかった線が、隣の見本の線を均して移した位置までの UNRULED_HALF より遠ければ、欄の幅の割合の上限の
    中でも、辺を合わせない（隣の列が30画素の狭い列。均した位置 995 から 1012 までは17画素で、隣の線 1026 までの半分
    より遠い）"""
    tm = template_table(cols=(200, 600, 1000, 1030, 1400))
    cols = [*PAGE_COLS, (1026, 200, 520), (1012, 312, 388, 160)]
    page = ruled([(y, 200, 1400) for y in ROWS], cols)
    assert right_edge(tm, page) == pytest.approx(995.4, abs=1)
    monkeypatch.setattr(variants, "UNRULED_HALF", 1.0)
    assert right_edge(tm, page) == pytest.approx(1012, abs=1)


def test_line_outside_template_line_is_not_used(monkeypatch):
    """罫線に入れなかった線の長さの UNRULED_OVERLAP 以上が、見本の線を移した区間に入らなければ、辺を合わせない
    （見本の線は2行目から下だけにあり、ページの線は1行目にある）"""
    tm = template_table(top=300)
    page = ruled([(y, 200, 1400) for y in ROWS], [*PAGE_COLS, (1012, 212, 288, 160)])
    assert right_edge(tm, page) == pytest.approx(994, abs=1)
    monkeypatch.setattr(variants, "UNRULED_OVERLAP", -10.0)
    assert right_edge(tm, page) == pytest.approx(1012, abs=1)


def test_line_close_to_corresponding_line_is_not_used(monkeypatch):
    """前後の対応した線から UNRULED_APART 画素以内の線には、辺を合わせない（対応から作った写像と、対応した線の位置が
    食い違う所を、作った対応で確かめる）"""
    T = [Line(600.0, 200, 520, 2), Line(1000.0, 200, 520, 2), Line(1400.0, 200, 520, 2)]
    P = [Line(990.0, 200, 520, 2), Line(1400.0, 200, 520, 2)]
    fmap = variants.Map1D([0, 2000], [0, 2000])
    scan_ = variants.Scan(T, P, [(0, 0), (2, 1)], 0.0, fmap, 1000.0, 1000.0)
    search = variants._Search([Line(994.0, 300, 390, 2)], P, fmap, (200.0, 500.0), 50.0)
    assert variants._unruled_line(scan_, 1, search) is None
    monkeypatch.setattr(variants, "UNRULED_APART", 3)
    assert variants._unruled_line(scan_, 1, search) == Line(994.0, 300, 390, 2)


@pytest.mark.parametrize(
    ("cols", "height", "rows"),
    [
        # 位置の違う線が2本（1006 と 1014。どちらも上限の中）
        ([(1006, 312, 388, 160), (1014, 310, 388, 160)], 800, ROWS),
        # 欄の近くにかからない線（見本の線は表の下まで続き、ページの線は欄の3行下にある）
        ([(1012, 712, 788, 160)], 1000, [200, 300, 400, 520, 700, 800]),
    ],
)
def test_ambiguous_or_distant_lines_are_not_used(cols, height, rows):
    """罫線に入れなかった線の候補の位置が同じでないとき（どれが辺の線か決められない）と、欄の近くにかかる候補が
    ないときは、辺を合わせない"""
    tm = template_table(rows=rows, height=height)
    page_cols = [(x, rows[0], rows[-1]) for x, _, _ in PAGE_COLS]
    page = ruled([(y, 200, 1400) for y in rows], [*page_cols, *cols], height)
    pm = variants.page_model(page)
    assert len(pm.unruled_vs) == len(cols) and len(pm.vs) == 3
    assert right_edge(tm, page) == pytest.approx(994, abs=1)


def test_line_not_in_rulings_is_not_used_when_rulings_fragment_is_at_spread_position(
    monkeypatch,
):
    """均した位置（994）に、罫線として読めた切れ端（1行目にだけある線）があるときは、罫線に入れなかった線を探さない。
    辺の線が所どころだけ読めたページで、近くの字の画を辺にしないため"""
    tm = template_table()
    page = ruled(
        [(y, 200, 1400) for y in ROWS],
        [*PAGE_COLS, (1012, 312, 388, 160), (994, 200, 300)],
    )
    pm = variants.page_model(page)
    assert 994 in [round(s.pos) for s in pm.vs] and len(pm.unruled_vs) == 1
    assert right_edge(tm, page) == pytest.approx(994, abs=1)
    monkeypatch.setattr(variants, "_ruled_at", lambda *a: False)
    assert right_edge(tm, page) == pytest.approx(1012, abs=1)


def _scan_of(t_pos, p_pos, pairs, cost: float) -> variants.Scan:
    """線の位置と対応を直接与えた Scan（_forced の条件のテスト用）"""
    T = [Line(float(v), 0, 100, 2) for v in t_pos]
    P = [Line(float(v), 0, 100, 2) for v in p_pos]
    f = variants.Map1D([T[i].pos for i, _ in pairs], [P[k].pos for _, k in pairs])
    return variants.Scan(T, P, list(pairs), cost, f, 100.0, 100.0)


# 見本の4本目（400。欄の辺の線）が対応せず、ページの4本目（410）が見本の3本目に対応した対応（SHIFTED）と、4本目を
# 対応させ直した対応（FIXED）
T4, P4 = (100, 200, 300, 400), (110, 210, 310, 410)
SHIFTED = [(0, 0), (1, 1), (2, 3)]
FIXED = [(0, 0), (1, 1), (2, 2), (3, 3)]


def test_edge_line_is_matched_again_when_its_page_line_went_to_next_line():
    """欄の辺にあたる見本の線が対応せず、その線を対応させて求め直した対応で、その線に対応させたページの線が、今の
    対応で隣の見本の線に対応していて、重みの合計が今以下（同じでもよい）なら、求め直した対応で辺を決める"""
    scan = _scan_of(T4, P4, SHIFTED, 20.0)
    alt = _scan_of(T4, P4, FIXED, 19.0)
    asked = []

    def again(i):
        asked.append(i)
        return alt

    assert variants._forced(scan, again, 405) is alt
    assert asked == [3]
    same = alt._replace(cost=20.0)
    assert variants._forced(scan, lambda i: same, 405) is same
    assert variants._edge(alt, 405) == 415


@pytest.mark.parametrize(
    ("p_pos", "pairs", "alt_pairs", "alt_cost"),
    [
        (P4, SHIFTED, FIXED, 20.5),  # 重みの合計が増える
        # 対応させたページの線（410）が、今の対応で2本離れた見本の線に対応していた
        (P4, [(0, 0), (1, 3)], [(0, 0), (1, 1), (3, 3)], 19.0),
        # 対応させたページの線（470）が、今の対応でどの見本の線にも対応していなかった（辺の見本の線を写した位置 410
        # から、隣の見本の線を写した位置 310 までの半分より遠いので、求め直しは止まらない）
        ((110, 210, 310, 470), [(0, 0), (1, 1), (2, 2)], FIXED, 19.0),
        # 求め直しても、その見本の線が対応しない
        (P4, SHIFTED, [(0, 0), (1, 1), (2, 3)], 19.0),
    ],
    ids=["heavier", "two-lines-apart", "page-line-unmatched", "still-unmatched"],
)
def test_edge_line_is_not_matched_again_otherwise(p_pos, pairs, alt_pairs, alt_cost):
    """条件に合わなければ、今の対応のまま"""
    scan = _scan_of(T4, p_pos, pairs, 20.0)
    alt = _scan_of(T4, p_pos, alt_pairs, alt_cost)
    assert variants._forced(scan, lambda i: alt, 405) is scan


def test_edge_line_is_not_matched_again_when_page_line_is_left_unmatched_near_it():
    """求め直す前の対応で、辺の見本の線を写した位置から、隣の見本の線を写した位置までの半分より手前に、どの見本の線にも
    対応しなかったページの線があれば、求め直さない。本当の辺の線が短く切れて対応しないまま残っているときに、辺の見本の
    線が次の行の線を取り、範囲が次の行まで広がるため"""
    t_pos = (100, 200, 300, 400, 500)
    pairs = [
        (0, 0),
        (1, 1),
        (2, 2),
        (4, 4),
    ]  # 見本の4本目（400。欄の辺の線）は対応しない
    # 辺の見本の線を写した位置は410で、隣の見本の線を写した位置（310・510）までの半分は50
    moved = [
        (0, 0),
        (1, 1),
        (2, 2),
        (3, 4),
        (4, 5),
    ]  # 辺の見本の線が、隣の見本の線のページの線（510）を取る

    def again(i):
        raise AssertionError("求め直さない")

    for left in (405, 455):  # 対応しなかったページの線
        scan = _scan_of(t_pos, (110, 210, 310, left, 510, 610), pairs, 20.0)
        assert variants._forced(scan, again, 405) is scan
    p_pos = (110, 210, 310, 465, 510, 610)  # 半分より遠ければ、求め直す
    scan = _scan_of(t_pos, p_pos, pairs, 20.0)
    alt = _scan_of(t_pos, p_pos, moved, 19.0)
    assert variants._forced(scan, lambda i: alt, 405) is alt


def test_matched_or_distant_edge_line_is_not_matched_again():
    """辺にあたる見本の線（SNAP 以内でいちばん近い線）が対応していれば、また SNAP 以内に見本の線がなければ、求め直さない"""

    def again(i):
        raise AssertionError("求め直さない")

    fixed = _scan_of(T4, P4, FIXED, 20.0)
    assert variants._forced(fixed, again, 405) is fixed
    shifted = _scan_of(T4, P4, SHIFTED, 20.0)
    assert variants._forced(shifted, again, 413) is shifted
    assert (
        variants._forced(shifted, again, 305) is shifted
    )  # いちばん近い線（300）が対応している


def test_forced_line_is_matched_with_its_own_weight():
    """必ず対応させる見本の線は、どれかのページの線に対応し、重みの合計は、その対応の重み（引いた分を足し戻す）"""
    T = [Line(100.0, 0, 400, 2), Line(200.0, 0, 40, 2), Line(300.0, 0, 400, 2)]
    P = [Line(100.0, 0, 400, 2), Line(260.0, 360, 400, 20), Line(300.0, 0, 400, 2)]
    desc = variants._desc_matrix(T, P, 200.0, 200.0, 400.0, 400.0)
    ft = np.array([t.pos for t in T])
    free, free_cost = variants._align(T, P, desc, ft, 1.0)
    assert 1 not in dict(free)
    forced, cost = variants._align(T, P, desc, ft, 1.0, force=1)
    assert 1 in dict(forced)
    assert free_cost < cost < free_cost + 50


def test_score_has_template_side_and_page_side():
    """罫線の一致度は、横切った見本の線のうち合った線の割合（見本の側）と、横切ったページの線のうち合った線の割合
    （ページの側）。同じ表ではどちらも1。見本の線の一部だけがページにあれば見本の側が下がり、ページに見本にない
    線が多ければページの側が下がる"""
    rows, cols = [200, 300, 400, 520, 640], [200, 600, 1000, 1400]
    full = variants.model(rulings.read(table(rows, cols)))
    part = variants.model(rulings.read(table([200, 640], [200, 1400])))  # 外枠だけ
    assert variants.score(full, full) == (1.0, 1.0)
    more_in_template = variants.score(full, part)
    assert more_in_template.page == 1.0 and more_in_template.template < 0.6
    more_in_page = variants.score(part, full)
    assert more_in_page.template == 1.0 and more_in_page.page < 0.6


def test_rows_are_placed_when_only_height_changes():
    """行の高さだけが見本の2倍の表でも、欄を正しい行に置く（間隔は、線をまたぐ向きの大きさの比で比べる。線に沿った
    向きの比で比べると、正しい組の重みが大きくなって行を飛ばした）"""
    t_rows = [150, 190, 300, 340, 450, 490, 600, 640, 750]
    cols = [200, 600, 1000, 1400]
    p_rows = [100 + (y - 150) * 2 for y in t_rows]
    tm = variants.model(rulings.read(table(t_rows, cols, height=1500)))
    pm = variants.model(rulings.read(table(p_rows, cols, height=1500)))
    for i in range(len(t_rows) - 1):
        _, y, _, h = variants.place(
            tm, pm, (600, t_rows[i], 400, t_rows[i + 1] - t_rows[i])
        )
        assert (y, y + h) == pytest.approx((p_rows[i], p_rows[i + 1]), abs=2)


def given_scores(monkeypatch, values: dict[str, tuple[float, float]]) -> None:
    """variants.score を、見本の様式ごとに決めた値（見本の側, ページの側）を返すものに置き換える"""
    models = {variants.template_model(s): s.id for s in bundled_forms()}
    monkeypatch.setattr(
        variants, "score", lambda tm, pm: variants.Match(*values[models[tm]])
    )


@pytest.mark.parametrize(
    ("template", "page", "decided"),
    [
        (0.45, 0.45, True),  # どちらもちょうど下限
        (0.449, 0.90, False),  # 見本の側だけが下限に届かない
        (0.90, 0.449, False),  # ページの側だけが下限に届かない
        (0.90, 0.90, True),
    ],
)
def test_form_is_decided_only_with_both_ratios(monkeypatch, template, page, decided):
    """見本の側の割合とページの側の割合が、どちらもその様式の下限（保険料控除申告書は 0.45）以上のときだけ、その
    様式のバリエーションと決める。一致度を決めた値に置き換えて、2つの割合のそれぞれの境目を確かめる"""
    assert bundled("nta-hoken-r8").line_match_min == 0.45
    img, _ = warped("2026bun_04", "nta-hoken-r8")
    low = (0.0, 0.0)
    given_scores(
        monkeypatch,
        {"nta-hoken-r8": (template, page), "nta-kihai-r8": low, "nta-fuyou-r9": low},
    )
    v = variants.locate(img, bundled_forms())
    if decided:
        assert v.spec.id == "nta-hoken-r8" and v.match == (template, page)
    else:
        assert v is None


def test_larger_page_ratio_wins_between_candidates(monkeypatch):
    """2つの候補が条件に合えば、ページの側の割合の大きいほうに決める（見本の側の大きいほうではない）。同じなら、
    候補の順（位置合わせの点の多いほう）"""
    img, _ = warped("2026bun_04", "nta-hoken-r8")
    specs = bundled_forms()
    first, second = (
        s.id for s, _ in variants.candidates(align.page_features(img), specs)
    )
    rest = {s.id: (0.0, 0.0) for s in specs}
    given_scores(monkeypatch, rest | {first: (0.9, 0.7), second: (0.7, 0.8)})
    assert variants.locate(img, specs).spec.id == second
    given_scores(monkeypatch, rest | {first: (0.7, 0.8), second: (0.9, 0.8)})
    assert variants.locate(img, specs).spec.id == first


def test_each_form_has_its_own_limit(monkeypatch):
    """下限は様式ごとに持つ：同じ一致度（0.50／0.50）でも、下限が 0.45 の保険料控除申告書には決まり、下限が 0.65 の
    扶養控除等申告書には決まらない。同梱の3様式の下限は、扶養 0.65・保険料 0.45・基配 0.45"""
    limits = {s.id: (s.line_match_use, s.line_match_min) for s in bundled_forms()}
    assert limits == {
        "nta-fuyou-r9": (True, 0.65),
        "nta-hoken-r8": (True, 0.45),
        "nta-kihai-r8": (True, 0.45),
    }
    img, _ = warped("2026bun_04", "nta-hoken-r8")
    low = (0.0, 0.0)
    for form_id, decided in (("nta-hoken-r8", True), ("nta-fuyou-r9", False)):
        given_scores(
            monkeypatch, {s.id: low for s in bundled_forms()} | {form_id: (0.5, 0.5)}
        )
        v = variants.locate(img, bundled_forms())
        assert (v is not None and v.spec.id == form_id) == decided


def test_form_not_used_for_lines_is_not_a_candidate(monkeypatch):
    """罫線の並びを判別に使わないと決めた様式は、一致度が高くても候補にせず、その様式のバリエーションに決めない。
    見本と位置合わせできたページの置き直しにも使わない"""
    img, _ = warped("2026bun_04", "nta-hoken-r8")
    specs = [
        dataclasses.replace(s, line_match_use=False, updated_by="使わない")
        if s.id == "nta-hoken-r8"
        else s
        for s in bundled_forms()
    ]
    assert variants.locate(img, bundled_forms()).spec.id == "nta-hoken-r8"
    assert variants.locate(img, specs) is None
    found = variants.candidates(align.page_features(img), specs)
    assert "nta-hoken-r8" not in [s.id for s, _ in found]
    off = next(s for s in specs if s.id == "nta-hoken-r8")
    assert variants.relocate(img, off) is None
    assert variants.relocate(img, bundled("nta-hoken-r8")) is not None
    assert not variants.usable(off) and variants.usable(bundled("nta-hoken-r8"))
    assert variants.locate(img, [off]) is None


def test_relocate_follows_limit_of_form():
    """見本と位置合わせできたページの置き直し（relocate）も、罫線の一致度が様式の下限に届かなければ置かない"""
    img, _ = warped("2026bun_06", "nta-kihai-r8")
    spec = bundled("nta-kihai-r8")
    full = variants.relocate(img, spec)
    assert full is not None and len(full.unframed) == 1
    limit = min(full.match) + 0.01
    strict = dataclasses.replace(spec, line_match_min=limit, updated_by="高い下限")
    assert not variants.decides(strict, full.match)
    assert variants.relocate(img, strict) is None


def test_similar_public_form_is_not_chosen():
    """表の作りが扶養控除等申告書に似た別の帳票（公的年金等の受給者の扶養親族等申告書）は、同梱の3様式だけを使う
    設定で、扶養控除等申告書のバリエーションに決めない（一致度は見本の側・ページの側とも 0.5 を超えるが、扶養控除等
    申告書の下限 0.65 に届かない）"""
    page = page_image("2027bun_09")
    m = variants.score(
        variants.template_model(bundled("nta-fuyou-r9")),
        variants.model(rulings.read(page)),
    )
    assert 0.5 < min(m) < bundled("nta-fuyou-r9").line_match_min
    assert variants.locate(page, bundled_forms()) is None


def test_candidate_below_limit_is_reported(monkeypatch):
    """バリエーションに決まらなかったページは、罫線の一致度が下限に届かなかった候補（様式と一致度）を返す。届かなかった
    候補が2つあれば、2つの割合の小さいほうが大きい候補。決まったページと、ページの罫線が読めないページは返さない"""
    page = page_image("2027bun_09")
    variant, below = variants.judge(page, bundled_forms())
    assert variant is None and below.spec.id == "nta-fuyou-r9"
    assert 0.5 < min(below.match) < below.spec.line_match_min
    img, _ = warped("2026bun_04", "nta-hoken-r8")
    variant, below = variants.judge(img, bundled_forms())
    assert variant.spec.id == "nta-hoken-r8" and below is None
    specs = bundled_forms()
    first, second = (
        s.id for s, _ in variants.candidates(align.page_features(img), specs)
    )
    rest = {s.id: (0.0, 0.0) for s in specs}
    given_scores(monkeypatch, rest | {first: (0.30, 0.10), second: (0.20, 0.25)})
    variant, below = variants.judge(img, specs)
    assert variant is None and below.spec.id == second
    assert below.match == (0.20, 0.25)
    blank = np.full((1654, 2339), 255, np.uint8)
    assert variants.judge(blank, specs) == (None, None)


def test_pipeline_keeps_candidate_below_limit(scan):
    """様式不明のままになったページの解析の結果は、罫線の一致度が下限に届かなかった候補の、様式の識別子・版・一致度・
    下限を持つ（作業ログに書く）。バリエーションに決まったページと、見本と位置合わせできたページは持たない"""
    res = pipeline.analyze(
        imaging.open_pages(scan("2027bun_09"))[0], formset=bundled_forms()
    )
    assert res.form == forms.UNKNOWN and res.boxes == [] and not res.variant
    below = res.line_below
    assert (below.form_id, below.revision, below.limit) == ("nta-fuyou-r9", 0, 0.65)
    assert 0.4 < min(below.template, below.page) < 0.65
    res = pipeline.analyze(
        imaging.open_pages(scan("2026bun_04"))[0], formset=bundled_forms()
    )
    assert res.form == forms.HOKEN and res.line_below is None


def _with_lines(form_id: str, name: str, lines) -> formstore.FormSpec:
    """同梱の様式の形を借りて、見本の罫線が lines の様式を作る"""
    return dataclasses.replace(
        bundled("nta-hoken-r8"),
        id=form_id,
        name=name,
        lines=lines,
        updated_by=name,
    )


def test_default_limit_follows_nearest_form():
    """新しく登録する様式の下限の初めの値は 0.60。使うほかの様式の見本の罫線を当てて、いちばん近い様式との一致度
    （2つの割合の小さいほう）に 0.10 を足して 0.05 刻みに切り上げた値が 0.60 より高ければ、その値にする（上限
    0.95）。一致度が 0.85 を超える様式（表の作りが同じ帳票）は、罫線の並びを判別に使わないにする"""
    nenkin = rulings.read(page_image("2027bun_09"))
    fuyou = bundled("nta-fuyou-r9")
    near = min(variants.score(variants.model(nenkin), variants.template_model(fuyou)))
    assert 0.55 < near < 0.70  # 公的年金等の申告書は、扶養控除等申告書に近い
    # 扶養控除等申告書との一致度が約0.63なので、0.10 を足した約0.73を 0.05 刻みに切り上げた 0.75
    assert variants.default_limit(nenkin, bundled_forms()) == (True, 0.75)
    # 近い様式がなければ 0.60
    others = [bundled("nta-hoken-r8"), bundled("nta-kihai-r8")]
    assert variants.default_limit(nenkin, others) == (True, 0.60)
    assert variants.default_limit(nenkin, []) == (True, 0.60)
    assert variants.default_limit(None, bundled_forms()) == (True, 0.60)
    # 表の作りが同じ帳票（同じ罫線）があれば、使わないにする
    same = _with_lines("u-same", "同じ表の帳票", nenkin)
    assert variants.default_limit(nenkin, [same]) == (False, 0.95)
    # 罫線の並びを判別に使わない様式も、見本の罫線を持てば比べる
    off = dataclasses.replace(same, line_match_use=False, updated_by="使わない")
    assert variants.default_limit(nenkin, [off]) == (False, 0.95)


@pytest.mark.parametrize(
    ("near", "want"),
    [
        (0.0, (True, 0.60)),
        (0.50, (True, 0.60)),  # 0.10 を足して 0.60 ちょうど
        (0.501, (True, 0.65)),  # 0.60 を少しでも超えたら、次の刻み
        (0.55, (True, 0.65)),
        (0.551, (True, 0.70)),
        (0.80, (True, 0.90)),
        (0.85, (True, 0.95)),  # 0.85 ちょうどまでは使う
        (0.851, (False, 0.95)),  # 0.85 を超えたら使わない。下限は上限の 0.95 で止まる
        (0.97, (False, 0.95)),
    ],
)
def test_default_limit_steps_and_bounds(monkeypatch, near, want):
    """下限の初めの値の境：いちばん近い様式との一致度に 0.10 を足して 0.05 刻みに切り上げ、0.60 以上・0.95 以下に
    する。一致度が 0.85 を超えたら、罫線の並びを判別に使わないにする"""
    lines = rulings.read(page_image("2026bun_04"))
    monkeypatch.setattr(
        variants, "score", lambda tm, om: variants.Match(near, min(near + 0.2, 1.0))
    )
    assert variants.default_limit(lines, [bundled("nta-fuyou-r9")]) == want


def test_default_limit_skips_forms_without_lines_and_relocate_needs_lines_on_page():
    """見本の罫線を持たない様式は、下限の初めの値を決めるときに比べない。罫線の読めないページ（白紙）は、置き直さない"""
    nenkin = rulings.read(page_image("2027bun_09"))
    no_lines = dataclasses.replace(
        bundled("nta-fuyou-r9"), lines=None, updated_by="罫線なし"
    )
    assert variants.default_limit(nenkin, [no_lines]) == (True, 0.60)
    assert variants.default_limit(nenkin, [no_lines, bundled("nta-fuyou-r9")]) == (
        True,
        0.75,
    )
    blank = np.full((1654, 2339), 255, np.uint8)
    assert variants.relocate(blank, bundled("nta-fuyou-r9")) is None


@pytest.mark.parametrize(
    ("form", "form_id"),
    [
        ("2027bun_01", "nta-fuyou-r9"),
        ("2026bun_04", "nta-hoken-r8"),
        ("2026bun_06", "nta-kihai-r8"),
    ],
)
def test_bundled_lines_stay_close_to_current_reading(form, form_id):
    """配布用パッケージの様式の罫線（.json の lines）は、罫線の読み取りを変えても読み直さない。同じ公開様式から今の
    読み取りで読んだ罫線との一致度（どちらを見本にしても、2つの割合の小さいほう）が 0.95 以上であることを確かめる
    （2026-11-21 は 0.98〜0.99）。下回ったら、tools/make_templates.py --reread-lines で読み直し、様式ごとの下限を
    測り直す"""
    kept = variants.template_model(bundled(form_id))
    now = variants.model(rulings.read(page_image(form)))
    assert min(variants.score(kept, now)) >= 0.95
    assert min(variants.score(now, kept)) >= 0.95


def test_sparse_form_is_not_chosen_for_dense_document():
    """罫線の少ない様式（控除証明書）を罫線を持つ形で使っても、罫線の多い別の帳票（従たる給与の申告書）を、その様式の
    バリエーションに決めない。見本の線はページのどこかの線と合うので見本の側の割合は高いが、ページの線の多くは
    見本にないので、ページの側の割合が下限に届かない"""
    g = scan.data_image("架空_控除証明書_生命保険_200dpi_グレー.png")
    h, w = g.shape
    cert = dataclasses.replace(
        bundled("nta-fuyou-r9"),
        id="u-cert",
        name="控除証明書",
        lines=rulings.read(g),
        size=(w, h),
        updated_by="試験",
        line_match_min=formstore.LINE_MATCH_MIN,
    )
    page = page_image("2026bun_08")
    m = variants.score(
        variants.template_model(cert), variants.model(rulings.read(page))
    )
    assert m.template >= cert.line_match_min and m.page < cert.line_match_min
    assert variants.locate(page, [*bundled_forms(), cert]) is None


def test_other_form_is_not_chosen_with_two_forms():
    """使う様式が保険料控除申告書と扶養控除等申告書の2つでも、作りの似た別の帳票（従たる給与の申告書）を、扶養控除等
    申告書のバリエーションに決めない（種類を決める条件は、使う様式の組み合わせによらない）"""
    two = [bundled("nta-hoken-r8"), bundled("nta-fuyou-r9")]
    assert variants.locate(page_image("2026bun_08"), two) is None


def warped(form: str, form_id: str, image: np.ndarray | None = None):
    """公開様式の試験画像（200dpi）の行の高さと列の幅を、帯ごとに変えた画像と、正しい欄の位置。image を渡すと、
    その画像（同じ様式に記入したものなど）を変える

    上の3割の行は1.0倍、次の3割は1.2倍、下の4割は0.85倍。左半分の列は0.9倍、右半分は1.1倍（全体の大きさは同じ）。
    """
    g = page_image(form) if image is None else image
    h, w = g.shape
    ky = np.array([0, 0.3 * h, 0.6 * h, h])
    my = np.cumsum([0, 0.3 * h * 1.0, 0.3 * h * 1.2, 0.4 * h * 0.85])
    my = my * h / my[-1]
    kx = np.array([0, 0.5 * w, w])
    mx = np.cumsum([0, 0.5 * w * 0.9, 0.5 * w * 1.1])
    mx = mx * w / mx[-1]
    gx = np.interp(np.arange(w), mx, kx).astype(np.float32)
    gy = np.interp(np.arange(h), my, ky).astype(np.float32)
    out = cv2.remap(
        g,
        np.tile(gx, (h, 1)),
        np.tile(gy[:, None], (1, w)),
        cv2.INTER_LINEAR,
        borderValue=255,
    )
    truth = []
    for f in bundled(form_id).fields:
        x0, x1 = np.interp([f.x, f.x + f.w], kx, mx)
        y0, y1 = np.interp([f.y, f.y + f.h], ky, my)
        truth.append((x0, y0, x1 - x0, y1 - y0))
    return out, truth


@pytest.mark.parametrize(
    ("form", "form_id", "unframed"),
    [
        ("2027bun_01", "nta-fuyou-r9", 0),
        ("2026bun_04", "nta-hoken-r8", 0),
        ("2026bun_06", "nta-kihai-r8", 1),
    ],
)
def test_warped_form_is_located_by_lines(form, form_id, unframed):
    """行の高さと列の幅の違う用紙を、見本の罫線で正しい様式に決め、欄を置く。扶養控除等申告書（10欄）と保険料控除
    申告書（1欄）は、どの欄も枠の線が見つかり、2画素以内に置ける。基・配・特・所の申告書（4欄）は、1欄だけ列が
    ずれて置かれ（約28画素）、その欄は枠の線が見つからない確かでない欄（unframed）になる。枠の線が見つかった欄は、
    どれも2画素以内"""
    img, truth = warped(form, form_id)
    v = variants.locate(img, bundled_forms())
    assert v is not None and v.spec.id == form_id
    assert variants.decides(v.spec, v.match)
    errors = [
        max(abs(a - b) for a, b in zip((f.x, f.y, f.w, f.h), t))
        for f, t in zip(v.fields, truth, strict=True)
    ]
    assert len(v.unframed) == unframed
    assert all(e <= 2 for f, e in zip(v.fields, errors) if f.name not in v.unframed)
    assert all(e <= 40 for e in errors)


def test_form_with_few_lines_is_not_chosen():
    """罫線が短い2本だけの見本は、どの横切る線にもかからず、一致度が (0, 0) になる。例外にならず、その見本には
    決めない"""
    fuyou = bundled("nta-fuyou-r9")
    short = (((100.0, 100, 160, 9),), ((2200.0, 1500, 1540, 9),))
    few = dataclasses.replace(fuyou, id="u-short", lines=short, updated_by="試験")
    assert variants.score(
        variants.template_model(few), variants.template_model(fuyou)
    ) == (0, 0)
    assert variants.locate(page_image("2026bun_08"), [few, fuyou]) is None


def test_forms_without_lines_are_not_candidates():
    """見本の罫線を持たない様式（前のバージョンで保存した様式）は、候補にしない"""
    img, _ = warped("2026bun_04", "nta-hoken-r8")
    plain = [
        dataclasses.replace(s, lines=None, updated_by="罫線なし")
        for s in bundled_forms()
    ]
    assert variants.locate(img, plain) is None


def test_blank_page_without_lines_or_features():
    """罫線も特徴点もないページ（白紙）は、例外にならずに決まらない"""
    blank = np.full((1654, 2339), 255, np.uint8)
    assert align.page_features(blank)[1] is None
    assert rulings.read(blank) == ((), ())
    assert variants.locate(blank, bundled_forms()) is None


def test_fields_without_frame_record_are_placed_without_frames():
    """枠の線の記録がない欄（frame が空）は、罫線の対応で置いた位置のまま範囲にし、線を探さないので、確かでない欄にも
    しない"""
    img, _ = warped("2026bun_04", "nta-hoken-r8")
    hoken = bundled("nta-hoken-r8")
    plain = dataclasses.replace(
        hoken,
        fields=tuple(dataclasses.replace(f, frame=()) for f in hoken.fields),
        updated_by="枠なし",
    )
    v = variants.locate(img, [plain])
    assert v is not None and v.unframed == ()
    assert all(f.frame == () for f in v.fields)
    tm = variants.template_model(plain)
    pm = variants.page_model(img)
    for f, s in zip(v.fields, plain.fields, strict=True):
        assert (f.x, f.y, f.w, f.h) == variants.place(tm, pm, (s.x, s.y, s.w, s.h))


def test_frame_error_keeps_placed_rect_as_unframed(monkeypatch):
    """1つの欄で枠の線を探す処理が例外になっても、ページ全体を失敗にせず、その欄は罫線の対応で置いた位置のまま、
    確かでない欄にする（見本と位置合わせしたページの欄と同じ扱い）。ほかの欄は変わらない"""
    img, _ = warped("2027bun_01", "nta-fuyou-r9")
    specs = bundled_forms()
    normal = variants.locate(img, specs)
    real = frames.find
    calls = []

    def fail_first(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("線を探せない")
        return real(*a, **k)

    monkeypatch.setattr(frames, "find", fail_first)
    v = variants.locate(img, specs)
    first = v.spec.fields[0]
    tm, pm = variants.template_model(v.spec), variants.page_model(img)
    placed = variants.place(tm, pm, (first.x, first.y, first.w, first.h))
    assert (v.fields[0].x, v.fields[0].y, v.fields[0].w, v.fields[0].h) == placed
    assert v.fields[0].frame == () and first.name in v.unframed
    assert v.fields[1:] == normal.fields[1:]


def test_pipeline_places_ranges_on_unknown_variant_page(tmp_path, monkeypatch):
    """見本との位置合わせで様式不明になったページを、見本の罫線でバリエーションと決めると、範囲を置き、要確認の文に
    バリエーションの文だけを出す。作業ログに書く値として、罫線の一致度を持ち、位置合わせの点は持たない"""
    img, truth = warped("2026bun_04", "nta-hoken-r8")
    path = tmp_path / "page.png"
    Image.fromarray(img).save(path, dpi=(200, 200))
    # 字の形が違って位置合わせが成り立たない用紙（様式のバリエーション）に見立てる。向きは OCR で決め（よく通る道）、
    # 見本と位置合わせし直さずに罫線を調べる
    monkeypatch.setattr(align, "upright_group", lambda *a, **k: None)
    res = pipeline.analyze(imaging.open_pages(path)[0], formset=bundled_forms())
    hoken = bundled("nta-hoken-r8")
    assert res.quarter_turns == 0 and res.orientation_confident
    assert res.variant and res.form == hoken.name and res.used == (hoken,)
    assert (
        res.note
        == f"『{hoken.name}』のバリエーションのため、範囲がずれているおそれがあります"
    )
    template, page = res.line_match  # 見本の側の割合, ページの側の割合
    assert min(template, page) >= hoken.line_match_min
    assert res.points == ()
    (box,) = res.boxes
    assert max(abs(a - b) for a, b in zip(box.rect(), truth[0])) <= 2


def test_aligned_page_with_changed_rows_is_placed_by_lines(monkeypatch):
    """行の高さと列の幅だけが見本と違う用紙は、字の形が見本と同じなので位置合わせが成り立つが、欄が枠の線からずれる。
    枠の線が見つからない欄があれば、罫線の対応で置いてみて、見つからない欄が減るので、全部の欄を置き換える。結果は
    バリエーションのページと同じ形で、位置合わせの点と罫線の一致度の両方を持つ。置き換えないと、同じページで
    欄が6画素を超えてずれ、枠の線が見つからない欄が残る"""
    img, truth = warped("2027bun_01", "nta-fuyou-r9")
    page = imaging.MemoryPage(np.dstack([img] * 3), 200.0, "test")

    def errors(res):
        return [
            max(abs(a - b) for a, b in zip(box.rect(), t))
            for box, t in zip(res.boxes, truth, strict=True)
        ]

    res = pipeline.analyze(page, formset=bundled_forms())
    fuyou = bundled("nta-fuyou-r9")
    assert res.variant and res.form == fuyou.name and res.used == (fuyou,)
    assert (
        res.note
        == f"『{fuyou.name}』のバリエーションのため、範囲がずれているおそれがあります"
    )
    (points,) = res.points
    assert points >= align.MIN_INLIERS and variants.decides(
        fuyou, variants.Match(*res.line_match)
    )
    assert res.unframed == () and res.crossed == () and max(errors(res)) <= 2
    monkeypatch.setattr(variants, "relocate", lambda gray, spec: None)
    kept = pipeline.analyze(page, formset=bundled_forms())
    assert not kept.variant and kept.line_match is None and kept.points == res.points
    assert kept.unframed and max(errors(kept)) > 6


def _located(**changes) -> forms.Located:
    """見本と位置合わせできた用紙が1枚で、枠の線が見つからない欄が2つあるページの結果"""
    spec = bundled("nta-fuyou-r9")
    fields = [formstore.Field(f.name, f.x, f.y, f.w, f.h) for f in spec.fields]
    base = forms.Located(
        form=spec.name,
        fields=fields,
        note="要確認",
        used=(spec,),
        unframed=(fields[0].name, fields[1].name),
        points=(500,),
    )
    return dataclasses.replace(base, **changes)


def _variant(unframed: int) -> variants.Variant:
    spec = bundled("nta-fuyou-r9")
    fields = tuple(formstore.Field(f.name, 1, 2, 3, 4) for f in spec.fields)
    names = tuple(f.name for f in fields[:unframed])
    return variants.Variant(spec, variants.Match(0.9, 0.9), fields, names)


@pytest.mark.parametrize(
    ("loc", "distorted", "placed", "asks_distorted", "asks_lines", "replaced"),
    [
        (_located(), False, _variant(1), True, True, True),
        # 置き直す条件：罫線の対応で置いても、枠の線が見つからない欄が減らない・種類を決める条件に合わない
        (_located(), False, _variant(2), True, True, False),
        (_located(), False, None, True, True, False),
        # 試す条件：歪んで写ったページ（写真など）では、罫線を読まない
        (_located(), True, _variant(0), True, False, False),
        # 試す条件：次のページでは、歪みの判定も罫線の読み取りもしない（解析の時間を延ばさない）
        (_located(unframed=()), False, _variant(0), False, False, False),
        (_located(points=(500, 400)), False, _variant(0), False, False, False),
        (_located(paper_notes=True), False, _variant(0), False, False, False),
        # 用紙についての注記が、様式を判別できない用紙も写っていることだけのページは、試す
        (
            _located(paper_notes=True, other_paper_only=True),
            False,
            _variant(1),
            True,
            True,
            True,
        ),
        (
            _located(paper_notes=True, other_paper_only=True),
            False,
            _variant(2),
            True,
            True,
            False,
        ),
        (
            _located(
                used=(
                    dataclasses.replace(
                        bundled("nta-fuyou-r9"), lines=None, updated_by="罫線なし"
                    ),
                )
            ),
            False,
            _variant(0),
            False,
            False,
            False,
        ),
        # 罫線の並びを判別に使わない様式（見本の罫線は持つ）
        (
            _located(
                used=(
                    dataclasses.replace(
                        bundled("nta-fuyou-r9"),
                        line_match_use=False,
                        updated_by="使わない",
                    ),
                )
            ),
            False,
            _variant(0),
            False,
            False,
            False,
        ),
    ],
)
def test_aligned_page_is_replaced_only_when_fewer_fields_lack_frames(
    monkeypatch, loc, distorted, placed, asks_distorted, asks_lines, replaced
):
    """見本と位置合わせできたページを罫線の対応で置き直すのは、位置合わせできた用紙が1枚で用紙についての注記がなく、
    枠の線が見つからない欄があり、様式が罫線の並びを判別に使う様式で見本の罫線を持ち、用紙が歪んで写っていない
    ページで、罫線の対応で置くと枠の線が見つからない欄が減るときだけ。置き直すときは全部の欄を置き換え、位置合わせの
    点は残す"""
    asked = []

    def is_distorted(work):
        asked.append("distorted")
        return distorted

    def relocate(gray, spec):
        asked.append("lines")
        return placed

    monkeypatch.setattr(formcheck, "distorted", is_distorted)
    monkeypatch.setattr(variants, "relocate", relocate)
    got = pipeline._by_lines(loc, np.zeros((10, 10), np.uint8))
    assert ("distorted" in asked, "lines" in asked) == (asks_distorted, asks_lines)
    if replaced:
        assert got.variant and got.fields == list(placed.fields)
        assert got.unframed == placed.unframed and got.line_match == (0.9, 0.9)
        assert got.points == loc.points and got.crossed == ()
    else:
        assert got is loc


def _moved_variant(by: float, unframed: int = 0) -> variants.Variant:
    """_located の範囲を、どの欄も by 画素下にずらして置いたバリエーション"""
    spec = bundled("nta-fuyou-r9")
    fields = tuple(
        formstore.Field(f.name, f.x, f.y + by, f.w, f.h) for f in spec.fields
    )
    names = tuple(f.name for f in fields[:unframed])
    return variants.Variant(spec, variants.Match(0.9, 0.9), fields, names)


@pytest.mark.parametrize(
    ("changes", "shift", "distorted", "placed", "asked_want", "replaced"),
    [
        # 罫線の対応で置いた位置が 6画素以内のページは、歪みの判定も置き直しもしない
        ({}, 6.0, False, _moved_variant(50), ["lines", "shift"], False),
        ({}, 0.0, False, _moved_variant(50), ["lines", "shift"], False),
        # 6画素を超えるページ：歪んでいなければ置き直し、線がすべて見つかって範囲も6画素を超えて違えば置き換える
        (
            {},
            6.5,
            False,
            _moved_variant(50),
            ["lines", "shift", "distorted", "relocate"],
            True,
        ),
        ({}, 6.5, True, _moved_variant(50), ["lines", "shift", "distorted"], False),
        ({}, 6.5, False, None, ["lines", "shift", "distorted", "relocate"], False),
        (
            {},
            6.5,
            False,
            _moved_variant(50, unframed=1),
            ["lines", "shift", "distorted", "relocate"],
            False,
        ),
        (
            {},
            6.5,
            False,
            _moved_variant(6),
            ["lines", "shift", "distorted", "relocate"],
            False,
        ),
        # 罫線を読まないページ：写真、用紙が2枚、用紙についての注記、判別に使わない様式
        ({"photo": True}, 50.0, False, _moved_variant(50), [], False),
        ({"points": (500, 400)}, 50.0, False, _moved_variant(50), [], False),
        ({"paper_notes": True}, 50.0, False, _moved_variant(50), [], False),
        (
            {
                "used": (
                    dataclasses.replace(
                        bundled("nta-fuyou-r9"),
                        line_match_use=False,
                        updated_by="使わない",
                    ),
                )
            },
            50.0,
            False,
            _moved_variant(50),
            [],
            False,
        ),
        # 罫線の読めないページ
        ({}, None, False, _moved_variant(50), ["lines"], False),
    ],
)
def test_page_with_all_frames_is_replaced_only_when_lines_disagree(
    monkeypatch, changes, shift, distorted, placed, asked_want, replaced
):
    """枠の線がすべて見つかったページは、罫線を読み、見本の罫線との対応で置いた位置が位置合わせの結果から
    LINES_SHIFT を超えてずれているときだけ、歪みの判定と罫線の対応での置き直しを行う。罫線の対応でも線がすべて
    見つかり、範囲が LINES_SHIFT を超えて違うときだけ、全部の欄を置き換える。読んだ罫線は、置き直しに渡す"""
    loc = _located(unframed=(), **changes)
    asked = []
    lines = object()

    def page_model(work):
        asked.append("lines")
        return None if shift is None else lines

    def placed_shift(spec, pm, fields):
        asked.append("shift")
        assert pm is lines and fields == loc.fields
        return shift

    def is_distorted(work):
        asked.append("distorted")
        return distorted

    def relocate(gray, spec, pm):
        asked.append("relocate")
        assert pm is lines
        return placed

    monkeypatch.setattr(variants, "page_model", page_model)
    monkeypatch.setattr(variants, "placed_shift", placed_shift)
    monkeypatch.setattr(formcheck, "distorted", is_distorted)
    monkeypatch.setattr(variants, "relocate", relocate)
    assert pipeline.LINES_SHIFT == 6.0
    got = pipeline._by_lines(loc, np.zeros((10, 10), np.uint8))
    assert asked == asked_want
    if replaced:
        assert got.variant and got.fields == list(placed.fields)
        assert got.points == loc.points and got.note.startswith("『")
    else:
        assert got is loc


def band_stretched(image: np.ndarray, middle: float):
    """用紙の高さの30〜60% の帯の行の高さを middle 倍、その下を0.95倍にした画像（全体の高さは同じ）と、
    元の y から移した後の y を求める関数"""
    h, w = image.shape
    parts, scales = np.array([0.3, 0.3, 0.4]), np.array([1.0, middle, 0.95])
    ky = np.concatenate([[0], np.cumsum(parts)]) * h
    my = np.concatenate([[0], np.cumsum(parts * scales)]) * h
    my = my * h / my[-1]
    gy = np.interp(np.arange(h), my, ky).astype(np.float32)
    gx = np.arange(w, dtype=np.float32)
    out = cv2.remap(
        image,
        np.tile(gx, (h, 1)),
        np.tile(gy[:, None], (1, w)),
        cv2.INTER_LINEAR,
        borderValue=255,
    )
    return out, lambda y: float(np.interp(y, ky, my))


def test_field_fitted_to_lines_of_next_row_is_placed_by_lines(reference, monkeypatch):
    """帯の行の高さだけが見本と違う用紙（記入のある扶養控除等申告書。帯の行が1.08倍）では、位置合わせが成り立ち、
    1欄が、高さの近い隣の行の線に合う（枠の線はすべて見つかる）。罫線の対応で置いた位置と比べて、置き直すので、
    どの欄も正しい位置から2画素以内になり、要確認が付き、番号が墨消しの外に出ない。比べないと、1欄が60画素を
    超えてずれ、要確認が付かないまま、その欄の番号がすべて外に出る"""
    form, form_id = "2027bun_01", "nta-fuyou-r9"
    _, boxes = reference(form)
    blank, _ = band_stretched(written_page(form, GRAY_DPI), 1.08)
    filled, moved = band_stretched(written_page(form, GRAY_DPI, boxes, "center"), 1.08)
    truth = [(b.x, moved(b.y), b.w, moved(b.y + b.h) - moved(b.y)) for b in boxes]

    def analyzed():
        page = imaging.MemoryPage(np.dstack([filled] * 3), 200.0, "test")
        res = pipeline.analyze(page, formset=bundled_forms())
        errors = [
            max(abs(a - b) for a, b in zip(box.rect(), t))
            for box, t in zip(res.boxes, truth, strict=True)
        ]
        digits = (blank.astype(int) - filled) > 40
        total = int(digits.sum())
        for box in res.boxes:
            digits[
                max(int(box.y), 0) : int(np.ceil(box.y + box.h)),
                max(int(box.x), 0) : int(np.ceil(box.x + box.w)),
            ] = False
        return res, max(errors), int(digits.sum()), total

    res, worst, left, total = analyzed()
    assert res.form == bundled(form_id).name and res.variant and len(res.points) == 1
    assert res.note.startswith(f"『{bundled(form_id).name}』のバリエーションのため")
    assert res.unframed == () and worst <= 2
    assert total > 10000 and left == 0
    # 比べない形（線がすべて見つかったページは、そのまま）では、要確認なしで番号が外に出る
    monkeypatch.setattr(pipeline, "LINES_SHIFT", 1000.0)
    res, worst, left, _ = analyzed()
    assert not res.variant and res.note == "" and res.unframed == ()
    assert worst > 60 and left > 1500


def test_page_with_false_other_paper_note_is_placed_by_lines(reference, monkeypatch):
    """行の高さと列の幅を変えた用紙に、線を越える大きさの数字を書いたページは、位置合わせの変換で移した見本の
    四隅がページの一部しか覆わず、「様式を判別できない用紙も写っていて、その用紙は墨消ししていません」の要確認が付く。用紙に
    ついての要確認がこの1つだけなら、置き直しを試し、置き換えたときは、この要確認を外して、バリエーションの文だけに
    する（置き直さないと、欄が正しい位置から200画素を超えてずれたまま残る）"""
    form, form_id = "2027bun_01", "nta-fuyou-r9"
    _, boxes = reference(form)
    img, truth = warped(form, form_id, written_page(form, GRAY_DPI, boxes, "over1"))
    page = imaging.MemoryPage(np.dstack([img] * 3), 200.0, "test")
    seen = []
    real = pipeline._by_lines

    def spy(loc, work):
        seen.append(loc)
        return real(loc, work)

    monkeypatch.setattr(pipeline, "_by_lines", spy)
    res = pipeline.analyze(page, formset=bundled_forms())
    (loc,) = seen
    assert loc.paper_notes and loc.other_paper_only and len(loc.points) == 1
    assert loc.note.startswith(
        "様式を判別できない用紙も写っていて、その用紙は墨消ししていません"
    )
    assert max(abs(f.y - t[1]) for f, t in zip(loc.fields, truth)) > 200
    fuyou = bundled(form_id)
    assert res.variant and res.points == loc.points
    assert res.note.startswith(
        f"『{fuyou.name}』のバリエーションのため、範囲がずれているおそれがあります"
    )
    assert "様式を判別できない用紙" not in res.note
    errors = [
        max(abs(a - b) for a, b in zip(b.rect(), t))
        for b, t in zip(res.boxes, truth, strict=True)
    ]
    assert res.unframed == () and max(errors) <= 2


@pytest.mark.parametrize(
    ("place", "crossing"), [("center", False), ("touch", False), ("over1", True)]
)
def test_variant_page_names_fields_with_entries_crossing_lines(
    place, crossing, reference
):
    """罫線の対応で置いたページでも、枠の線を越えて書かれた記入を調べ、バリエーションの文に続けて、その欄を名指し
    する。扶養控除等申告書の行と列を変えた用紙で、線を1mm 越えて書いた数字は、10欄とも名指しされる。欄の中央に
    書いた数字と、線の内側の縁に接するだけの数字は、名指しされない"""
    form, form_id = "2027bun_01", "nta-fuyou-r9"
    _, boxes = reference(form)
    img, _ = warped(form, form_id, written_page(form, GRAY_DPI, boxes, place))
    spec = bundled(form_id)
    v = variants.locate(img, bundled_forms())
    assert v is not None and v.spec.id == form_id and v.unframed == ()
    assert v.crossed == (tuple(f.name for f in spec.fields) if crossing else ())
    loc = forms.from_variant(v)
    head = f"『{spec.name}』のバリエーションのため、範囲がずれているおそれがあります"
    if crossing:
        assert loc.crossed == v.crossed
        assert loc.note == (
            head + "。すべての欄の範囲が、欄に合っていないおそれがあります"
        )
    else:
        assert loc.crossed == () and loc.note == head


def test_real_other_paper_keeps_its_note(monkeypatch):
    """ほかの用紙が実際に写っているスキャン（扶養控除等申告書の右に、どの様式の見本にも類似しない帳票を並べた
    1ページ）では、置き直しを試しても、罫線の一致度が下限に届かず、様式を判別できない用紙の要確認は残る。扶養控除等申告書の1欄は、上の線を消して、枠の線が見つからない欄にしてある（置き直しを試す条件）"""
    left = page_image("2027bun_01").copy()
    spec = bundled("nta-fuyou-r9")
    f = next(f for f in spec.fields if f.name == "住民税 16歳未満の扶養親族 1")
    y = round(f.y)
    left[y - 3 : y + 4, round(f.x) + 2 : round(f.x + f.w) - 2] = 255
    page_img = np.hstack([left, page_image("2026bun_08")])
    page = imaging.MemoryPage(np.dstack([page_img] * 3), 200.0, "test")
    tried = []
    real = variants.relocate

    def relocate(gray, spec):
        tried.append(real(gray, spec))
        return tried[-1]

    monkeypatch.setattr(variants, "relocate", relocate)
    res = pipeline.analyze(page, formset=bundled_forms())
    assert tried == [None]
    assert res.form == spec.name and not res.variant
    assert res.note.startswith(
        "様式を判別できない用紙も写っていて、その用紙は墨消ししていません"
    )
    assert f.name in res.unframed


def test_aligned_scan_without_lines_is_not_replaced(scan, monkeypatch):
    """見本と同じ作りの用紙の、線が消えた白黒2値のスキャンは、罫線の一致度が種類を決める条件に合っても、罫線の対応で
    置いたときに枠の線が見つからない欄が減らないので、範囲を動かさない（要確認の文も、線が見つからない欄を挙げた
    欄の文のまま）"""
    tried = []
    real = variants.relocate

    def relocate(gray, spec):
        tried.append(real(gray, spec))
        return tried[-1]

    monkeypatch.setattr(variants, "relocate", relocate)
    res = pipeline.analyze(
        imaging.open_pages(scan("2026bun_06", kind="bilevel"))[0],
        formset=bundled_forms(),
    )
    (placed,) = tried
    assert placed is not None and len(placed.unframed) == len(res.unframed) == 4
    assert res.form == forms.KIHAI and not res.variant and res.line_match is None
    assert res.note == forms.field_note(res.unframed, [b.kind for b in res.boxes])


def test_pipeline_records_first_stage_points(scan, monkeypatch):
    """見本との位置合わせで判別したページは、位置合わせの1段目の整合した点の数を持つ（成り立つ下限以上）。2段目の
    点の数を1にしても変わらない（2段目の数を持つ形では落ちる）"""
    real = align.refined
    monkeypatch.setattr(align, "refined", lambda found, f: (*real(found, f)[:2], 1))
    res = pipeline.analyze(
        imaging.open_pages(scan("2026bun_04"))[0], formset=bundled_forms()
    )
    (points,) = res.points
    assert points >= align.MIN_INLIERS and not res.variant and res.line_match is None


def test_unknown_page_stays_unknown(scan):
    """どの見本にも似ていない用紙（従たる給与の申告書）は、罫線を調べても様式不明のまま"""
    res = pipeline.analyze(
        imaging.open_pages(scan("2026bun_08"))[0], formset=bundled_forms()
    )
    assert res.form == forms.UNKNOWN and not res.variant and res.boxes == []
