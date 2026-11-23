# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""解析を速くした4つの計算のテスト

- 傾きの推定（imaging.estimate_skew）：直線の近くの画素を、全部の画素からではなく、直線の両端の行の範囲から選ぶ。
  描いた線の角度との差と、MAX_SKEW_DEG 以上傾いた線を使わないことを確かめる。
- 位置合わせの、比べてよい組の行列（align._nearby_mask）：全部の組ではなく、升に分けて周りの升の点だけを調べる。
  参照の式（全部の組を調べる）をここに置き、行列のすべての要素が一致することを確かめる。
- 傾きの推定を、向きを決めるあいだに別のスレッドで求める（pipeline._Background）：結果と例外を、呼んだ側に返す
  ことを確かめる。
- 特徴点の照合（align.good_matches）：同じページの特徴点と見本の組の照合を、向きを決めるときとバリエーションの候補を
  絞るときで使い回す。使い回した結果が、毎回照合した結果と同じことと、ページかスレッドが変われば照合し直すことを
  確かめる。
"""

import threading
from pathlib import Path

import cv2
import numpy as np
import pytest
from userforms import bundled_forms

from sumikeshi_studio import align, imaging, pipeline
from sumikeshi_studio.imaging import MAX_SKEW_DEG


def reference_mask(proj: np.ndarray, pp: np.ndarray, radius: float) -> np.ndarray:
    """align._nearby_mask の参照の式（全部の組を調べて、比べてよい組の行列を作る）"""
    allowed = np.zeros((len(proj), len(pp)), np.uint8)
    for i in range(0, len(proj), 1000):
        near_x = np.abs(proj[i : i + 1000, None, 0] - pp[None, :, 0]) <= radius
        near_y = np.abs(proj[i : i + 1000, None, 1] - pp[None, :, 1]) <= radius
        allowed[i : i + 1000] = near_x & near_y
    return allowed


def ruled(angles: list[float], width: int, size=(1700, 1300)) -> np.ndarray:
    """白地に、長さ約1100画素の黒い直線を、上から順に angles の角度（度。時計回りが正）で描いた灰色の画像"""
    h, w = size
    img = np.full((h, w), 255, np.uint8)
    for i, deg in enumerate(angles):
        cy = 200 + i * (h - 400) // max(len(angles) - 1, 1)
        dx = 550 * np.cos(np.radians(deg))
        dy = 550 * np.sin(np.radians(deg))
        p1 = (round(w / 2 - dx), round(cy - dy))
        p2 = (round(w / 2 + dx), round(cy + dy))
        cv2.line(img, p1, p2, 0, width)
    return img


SKEW_CASES = {
    "水平": ([0.0] * 4, 2),
    "わずかに右下がり": ([0.7] * 4, 3),
    "左下がり": ([-2.3] * 5, 1),
    "太い線": ([4.0] * 3, 7),
    "角度の混ざった線": ([1.0, 1.5, -0.5, 2.0, 1.2], 2),
    "上限の手前": ([14.0] * 3, 2),
    "上限を越える線": ([20.0] * 3, 2),
    "上限の内と外の線": ([3.0, 25.0, 3.0, -40.0], 2),
}


# 描いた線の角度と、求めた傾きの差の上限（度）。長さ約1100画素の線では、端の1画素のずれが約0.05度になる
# （2026-11-18 に測った差は、太さ7画素の線で最大0.08度）
SKEW_TOLERANCE = 0.15


@pytest.mark.parametrize("name", SKEW_CASES)
def test_estimate_skew_follows_drawn_lines(name):
    """線の角度・太さ・本数を変えた画像で、傾きは描いた線の角度（MAX_SKEW_DEG 以上傾いた線を除いた中央値）に
    近い。使える線がなければ 0"""
    angles, width = SKEW_CASES[name]
    used = [a for a in angles if abs(a) < MAX_SKEW_DEG]
    got = imaging.estimate_skew(ruled(angles, width))
    if used:
        assert abs(got - np.median(used)) < SKEW_TOLERANCE
    else:
        assert got == 0.0


def test_estimate_skew_without_lines():
    """線のない画像と、短い線だけの画像は 0"""
    blank = np.full((1200, 900), 255, np.uint8)
    short = blank.copy()
    cv2.line(short, (100, 300), (300, 310), 0, 2)
    for gray in (blank, short):
        assert imaging.estimate_skew(gray) == 0.0


def mask_points(radius: float) -> tuple[np.ndarray, np.ndarray]:
    """移した型の点とページの点（float32）。乱数の点（種を固定）に、ちょうど radius の距離の点、その少し外の点、
    数でない点（NaN・無限大）、ページの外の遠い点、負の座標の点を加える"""
    rng = np.random.default_rng(10)
    pp = np.round(rng.uniform(-60, 1500, (700, 2))).astype(np.float32)
    proj = rng.uniform(-120, 1600, (900, 2)).astype(np.float32)
    pp[3] = [-40, -30]  # 負の座標のページの点
    special = np.array(
        [
            pp[0] + [radius, 0],  # x がちょうど radius
            pp[1] + [-radius, radius],  # x・y ともちょうど radius
            pp[2] + [radius + 0.5, 0],  # 少し外
            [np.nan, 100],
            [100, np.inf],
            [-np.inf, -np.inf],
            [1e6, 1e6],  # 遠い点
            [-5000, 200],
            pp[3] + [0, -radius],  # 負の座標のページの点から、y がちょうど radius
        ],
        np.float32,
    )
    return np.concatenate([proj, special]), pp


@pytest.mark.parametrize("radius", [24, 10])
def test_nearby_mask_matches_reference(radius):
    """比べてよい組の行列が、前の式と、要素・形・型とも同じになる"""
    proj, pp = mask_points(radius)
    expected = reference_mask(proj, pp, radius)
    got = align._nearby_mask(proj, pp, radius)
    assert got.dtype == np.uint8 and got.shape == expected.shape
    assert np.array_equal(got, expected)
    # ちょうど radius の距離の組は1、少し外の組は0（端の扱いを確かめていること）
    n = len(proj) - 9
    assert expected[n, 0] == 1 and expected[n + 1, 1] == 1 and expected[n + 2, 2] == 0
    assert expected[n + 8, 3] == 1
    assert not expected[n + 3 : n + 8].any()  # 数でない点と遠い点は、どの組も0
    assert 0 < expected.sum() < expected.size / 10


def test_nearby_mask_with_no_points():
    """点が0個の側があるときと、移した点がどれもページの点の範囲の外か数でないときは、その形の0の行列（前の式と
    同じ）"""
    pts = np.float32([[1, 2], [3, 4]])
    none = np.zeros((0, 2), np.float32)
    outside = np.float32([[500, 2], [np.nan, 3], [-30, np.inf]])
    for proj, pp in ((none, pts), (pts, none), (none, none), (outside, pts)):
        got = align._nearby_mask(proj, pp, 24)
        assert np.array_equal(got, reference_mask(proj, pp, 24))
        assert got.shape == (len(proj), len(pp)) and got.dtype == np.uint8


def direct_matches(
    tpl: align.Template, features: tuple
) -> list[tuple[int, int, float]]:
    """毎回照合したときの、よい対応（見本の点の番号, ページの点の番号, 距離）"""
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(tpl.descriptors, features[1], k=2)
    return [
        (p[0].queryIdx, p[0].trainIdx, p[0].distance)
        for p in pairs
        if len(p) == 2 and p[0].distance < align.MATCH_RATIO * p[1].distance
    ]


def test_good_matches_are_reused_per_page_and_thread(monkeypatch):
    """同じページの特徴点と見本の組は1回だけ照合し、結果は毎回照合したものと同じ。ページの特徴点のオブジェクトが
    変わるか、別のスレッドから呼べば照合し直す"""
    tpls = align.templates(bundled_forms())
    path = Path(__file__).parent / "data" / "公開様式_2027bun_01_200dpi_グレー.png"
    page = align.page_features(
        cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_GRAYSCALE)
    )
    expected = [direct_matches(t, page) for t in tpls]
    assert (
        max(len(e) for e in expected) >= align.MIN_INLIERS
    )  # 扶養控除等申告書の見本とは対応が多い
    calls = []
    real = cv2.BFMatcher
    monkeypatch.setattr(cv2, "BFMatcher", lambda *a: calls.append(1) or real(*a))
    key = lambda good: [(g.queryIdx, g.trainIdx, g.distance) for g in good]
    for _ in range(2):
        assert [key(align.good_matches(t, page)) for t in tpls] == expected
    assert len(calls) == len(tpls)
    same_points = (page[0], page[1])  # 中身は同じでも別のオブジェクト
    assert key(align.good_matches(tpls[0], same_points)) == expected[0]
    assert len(calls) == len(tpls) + 1
    other = []
    th = threading.Thread(
        target=lambda: other.append(key(align.good_matches(tpls[0], same_points)))
    )
    th.start()
    th.join()
    assert other == [expected[0]] and len(calls) == len(tpls) + 2


def test_background_returns_value_and_raises_error_of_thread():
    """別のスレッドで呼んだ関数の戻り値を result で返し、関数の例外は result を呼んだ側で出す"""
    assert pipeline._Background(lambda a, b: a + b, 2, 3).result() == 5
    seen = []

    def fail():
        seen.append(threading.current_thread() is not threading.main_thread())
        raise ValueError("傾きを求められない")

    job = pipeline._Background(fail)
    with pytest.raises(ValueError, match="傾きを求められない"):
        job.result()
    assert seen == [True]
