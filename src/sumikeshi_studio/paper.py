# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""写真の中の用紙：用紙の部分と、位置合わせできた帳票のほかに写っている用紙

スマートフォンで撮った写真では、用紙の周りに机などが写る。用紙の部分（明るい部分のまとまり）を求め、
位置合わせできた帳票の外に、印字のある用紙が写っていないかを調べる。
"""

import cv2
import numpy as np

from . import align
from .formstore import TEMPLATE_SCALE

# 印字とみなす濃さ（見本のスキャンの紙の地は濃さ約203）。題名を読む範囲（titles）とほかの用紙の印字（ここ）の両方が使う。
# titles が paper を読み込むので、循環しないよう paper に置く（titles に移すと、paper → titles と titles → paper の
# 循環になり、tests/test_structure.py が失敗する）
TITLE_INK = 160
# 用紙の部分（明るい部分のまとまり）は、作業画像をこの倍率に縮めて求める。
# 明るい部分は、大津の方法で2値にしてから、PAPER_CLOSE 画素の正方形で閉じて中の印字を埋め、
# 縁を PAPER_ERODE 画素削る。縁を削らないと、用紙や白い余白と机の境目の画素が印字とみなされ、
# わずかに傾いた境目では1行に20画素を超えて、写真の上端から印字があるとされた
PAPER_SCALE = 0.25
PAPER_CLOSE = 5
PAPER_ERODE = 3
PAPER_MAX_SHARE = 0.9  # 用紙の部分が画像のこの割合以上なら、周りは写っていないとみなす
QUAD_MARGIN = 0.03  # 位置合わせできた帳票の範囲を、この割合だけ広げてから除く
# 位置合わせできた帳票の外の用紙の部分に、印字（濃さ TITLE_INK 未満）がこの割合以上あれば、
# ほかの用紙が写っているとみなす。割合は、位置合わせできた帳票の面積に対するもの（縮めた画像の上）。
# 写真に見立てた画像では、帳票1枚だけで0.01%未満、はがき大の控除証明書で0.35%、
# もう1枚の帳票で7〜9%だった
OTHER_INK_MIN = 0.002
# 画像の端からこの画素（縮めた画像の上。作業画像では32画素）の中の印字は数えない。スキャンでは、用紙の縁の影が画像の端に
# 沿った細い線になり、閉じる処理で用紙の部分に埋まって、縁を削る処理も画像の端には効かないので、ほかの用紙の印字と
# みなされた（用紙1枚のスキャンで、しきい値の約1.5倍）
EDGE_IGNORE = 8


def _quad_mask(quads: list[np.ndarray], shape: tuple, scale: float) -> np.ndarray:
    """位置合わせできた帳票の範囲（QUAD_MARGIN だけ広げたもの）を 255 にした画像。
    shape は作業画像を scale の倍率に縮めた画像の大きさ"""
    mask = np.zeros(shape, np.uint8)
    for q in quads:
        c = q.mean(axis=0)
        big = (c + (q - c) * (1 + QUAD_MARGIN)) * scale
        cv2.fillPoly(mask, [np.round(big).astype(np.int32)], 255)
    return mask


def features_outside(features: tuple, quads: list[np.ndarray], shape: tuple):
    """特徴点のうち、位置合わせできた帳票の範囲の外にあるものだけを返す"""
    kp, desc = features
    h, w = (round(v * TEMPLATE_SCALE) for v in shape)
    mask = _quad_mask(quads, (h, w), TEMPLATE_SCALE)
    keep = [
        i
        for i, k in enumerate(kp)
        if not mask[min(int(k.pt[1]), h - 1), min(int(k.pt[0]), w - 1)]
    ]
    if not keep:
        return (), None
    return tuple(kp[i] for i in keep), desc[keep]


def _paper(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """縮めた画像と、その上の用紙の部分（明るい部分を閉じて中の印字を埋め、縁を削ったもの。255）を返す"""
    s = PAPER_SCALE
    small = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    _, bright = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = np.ones((PAPER_CLOSE, PAPER_CLOSE), np.uint8)
    paper = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, kernel)
    return small, cv2.erode(paper, np.ones((2 * PAPER_ERODE + 1,) * 2, np.uint8))


def paper_region(gray: np.ndarray) -> np.ndarray | None:
    """写真などで用紙の周りが写っているとき、用紙の部分（作業画像と同じ大きさの真偽の配列）を返す。
    用紙の部分が画像の PAPER_MAX_SHARE 以上なら（スキャンなど）None"""
    _, paper = _paper(gray)
    if np.count_nonzero(paper) >= PAPER_MAX_SHARE * paper.size:
        return None
    h, w = gray.shape
    return cv2.resize(paper, (w, h), interpolation=cv2.INTER_NEAREST) > 0


def other_paper(gray: np.ndarray, quads: list[np.ndarray]) -> bool:
    """位置合わせできた帳票の外に、印字のある用紙が写っているか"""
    small, paper = _paper(gray)
    paper[_quad_mask(quads, paper.shape, PAPER_SCALE) > 0] = 0
    e = EDGE_IGNORE
    paper[:e], paper[-e:], paper[:, :e], paper[:, -e:] = 0, 0, 0, 0
    ink = int(np.count_nonzero((small < TITLE_INK) & (paper > 0)))
    return ink >= OTHER_INK_MIN * align.quad_area(quads[0]) * PAPER_SCALE**2
