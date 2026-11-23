# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""見本と位置合わせできたページで、範囲が隣の行に置かれている見込みの高い欄を挙げる

用紙の一部の行がちょうど1行分ずれると、範囲を置いた位置にも隣の行の同じ形の枠の線が来る。枠の線は4辺とも
見つかり、罫線の対応で置いた位置との差も小さいので、ほかの確かめでは要確認が付かないまま、範囲が1行ずれる。
そこで、欄の周りの見本の印刷を、位置合わせの変換で見本の画像の画素に写したページの同じ所と照合し、置いた位置より、
縦に半行以上離れた位置のほうがよく合う欄を挙げる。範囲は動かさない（見誤ったときに、範囲が隣の行の字を隠すため）。

座標の呼び方は align.py と同じ。照合は、見本の画像の画素で行う。
"""

from collections.abc import Sequence

import cv2
import numpy as np

from . import align
from .formstore import TEMPLATE_SCALE, Field

# 照合する所：見本の画像の、欄とその上下（欄の高さの MARGIN_V 倍で、MARGIN_V_MIN 画素以上）・左右（MARGIN_H 画素。
# 作業画像では60画素）
MARGIN_V = 0.35
MARGIN_V_MIN = 4.0
MARGIN_H = 30.0
# 照合から外す所：その欄とほかの欄の、枠の線から GROW 画素外までの内側。欄の中の記入と、隣の行にも同じ形で
# 印刷された枠の線は、どの行に置いたかを見分ける手がかりにならないため（欄の中も照合する形と、欄の中だけを外す形
# では、1行ずらした合成画像24ページのうち、ずらしていないページの値の最大を上回ったのは2〜23ページだった）
GROW = 2
BLUR = 1.0  # 濃さを反転した画像をぼかす量（σ。見本の画像の画素）
MIN_PIXELS = 50  # 照合する画素がこれより少ない欄は調べない
# 探す範囲：置いた位置を中心に、縦に ±SEARCH 行（1行は欄の高さ）。横は ±SHIFT_X 画素に、置いた位置の横のずれを足す
SEARCH = 1.6
SHIFT_X = 4
NEAR = 0.15  # 置いた位置とみなす縦のずれ（行。NEAR_MIN 画素以上）
NEAR_MIN = 2.0
FAR = 0.5  # 離れた位置とみなす縦のずれの下限（行）
# 離れた位置での照合の値（正規化相互相関）の最大が、置いた位置での最大をこの値以上上回る欄を挙げる。1行ずらした
# 合成画像24ページのずらした欄は0.543以上、要確認のないページの欄は −0.129以下だった
PICK = 0.4


def shifted(
    work: np.ndarray, tpl: align.Template, H: np.ndarray, fields: Sequence[Field]
) -> list[str]:
    """見本 tpl を変換 H で作業画像 work に移して置いた範囲 fields（作業画像の座標。tpl.fields と同じ順）のうち、
    範囲が隣の行に置かれている見込みの高い欄の名前（tpl.fields の順）"""
    template = _ink(tpl.image)
    page = _ink(_page_on_template(work, H, tpl.image.shape))
    back = np.linalg.inv(np.asarray(H, dtype=np.float64))
    names = []
    for f, placed in zip(tpl.fields, fields, strict=True):
        others = [g for g in tpl.fields if g is not f]
        gain = _gain(template, page, f, others, _offset(back, f, placed))
        if gain is not None and gain >= PICK:
            names.append(f.name)
    return names


def _ink(image: np.ndarray) -> np.ndarray:
    """濃さを反転して（墨が大きい値）、ぼかした画像"""
    return cv2.GaussianBlur(255.0 - image.astype(np.float32), (0, 0), BLUR)


def _page_on_template(
    work: np.ndarray, H: np.ndarray, shape: tuple[int, ...]
) -> np.ndarray:
    """作業画像を、見本の画像の画素に写した画像（大きさ shape）"""
    s = TEMPLATE_SCALE
    small = cv2.resize(work, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    scale = np.diag([s, s, 1.0])
    M = scale @ np.asarray(H, dtype=np.float64) @ np.linalg.inv(scale)
    return cv2.warpPerspective(
        small,
        M,
        (shape[1], shape[0]),
        flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
        borderValue=255,
    )


def _offset(back: np.ndarray, f: Field, placed: Field) -> tuple[float, float]:
    """置いた範囲 placed の中心を、逆の変換 back で見本の座標に戻したときの、欄 f の中心からのずれ（見本の画像の
    画素）。枠の線に合わせた範囲や、曲がりの補正で移した範囲は、変換で移した位置から動いているので、置いた位置を
    中心に探す"""
    p = back @ np.array([placed.x + placed.w / 2, placed.y + placed.h / 2, 1.0])
    s = TEMPLATE_SCALE
    return (
        (p[0] / p[2] - (f.x + f.w / 2)) * s,
        (p[1] / p[2] - (f.y + f.h / 2)) * s,
    )


def _gain(
    template: np.ndarray,
    page: np.ndarray,
    f: Field,
    others: Sequence[Field],
    offset: tuple[float, float],
) -> float | None:
    """欄 f の周りの照合で、置いた位置から離れた位置での値の最大から、置いた位置での値の最大を引いた値。照合できない
    ときは None"""
    s = TEMPLATE_SCALE
    x, y, w, h = f.x * s, f.y * s, f.w * s, f.h * s
    mv = max(MARGIN_V * h, MARGIN_V_MIN)
    th, tw = template.shape
    x0, y0 = max(round(x - MARGIN_H), 0), max(round(y - mv), 0)
    x1, y1 = min(round(x + w + MARGIN_H), tw), min(round(y + h + mv), th)
    patch = template[y0:y1, x0:x1]
    mask = np.ones_like(patch)
    for g in (f, *others):
        gx0, gy0 = round(g.x * s - GROW) - x0, round(g.y * s - GROW) - y0
        gx1 = round((g.x + g.w) * s + GROW) - x0
        gy1 = round((g.y + g.h) * s + GROW) - y0
        if gx1 <= 0 or gy1 <= 0 or gx0 >= patch.shape[1] or gy0 >= patch.shape[0]:
            continue
        mask[max(gy0, 0) : gy1, max(gx0, 0) : gx1] = 0
    if mask.sum() < MIN_PIXELS or patch.std() < 1e-3:
        return None
    sv = round(SEARCH * h)
    sx = SHIFT_X + round(abs(offset[0]))
    cy = round(offset[1])
    ys0, ys1 = y0 + cy - sv, y1 + cy + sv
    xs0, xs1 = x0 - sx, x1 + sx
    region = page[max(ys0, 0) : min(ys1, th), max(xs0, 0) : min(xs1, tw)]
    # ページの外は墨のない所として照合する
    region = cv2.copyMakeBorder(
        region,
        max(-ys0, 0),
        max(ys1 - th, 0),
        max(-xs0, 0),
        max(xs1 - tw, 0),
        cv2.BORDER_CONSTANT,
        value=0,
    )
    r = cv2.matchTemplate(region, patch, cv2.TM_CCOEFF_NORMED, mask=mask)
    r = np.nan_to_num(r, nan=-1.0, posinf=-1.0, neginf=-1.0)
    best = r.max(axis=1)  # 縦のずれごとの最大（横は ±sx の中）
    apart = np.abs(np.arange(len(best)) - sv + cy - offset[1])
    near = apart <= max(NEAR_MIN, NEAR * h)
    far = apart >= FAR * h
    if not near.any():
        return None
    s1 = best[far].max() if far.any() else -1.0
    return float(s1 - best[near].max())
