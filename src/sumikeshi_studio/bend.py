# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""用紙の曲がりの補正：位置合わせの変換（射影変換1つ）で合わせきれないページで、欄ごとの変換を求める

写真では、用紙の端が浮いていたりレンズのゆがみがあったりして、変換で移した位置とページの中身の位置のずれが、場所に
よって変わる（上の端が浮いた用紙では、上の端ほど上と左に数十画素）。見本の画像を格子の区画に分け、変換で見本の座標に
移したページの中で区画ごとに照合してずれを測り、ずれ全体にページの位置の3次の多項式を当てはめる。欄の四隅をその
ずれの分だけ見本の座標で動かしてから変換で移すものを、その欄の変換にする。
"""

import hashlib
from collections.abc import Callable, Sequence

import cv2
import numpy as np

from .formstore import TEMPLATE_SCALE, Field

# 区画の大きさ・間隔・探す範囲（見本の画像の画素。作業画像では2倍）
PATCH = 70
STEP = 35
SEARCH = 40
# 正規化相互相関がこれ未満の区画は使わない（ずれを測れていないとみなす）
MIN_SCORE = 0.5
# 見本の画像の区画で、濃さの標準偏差が MIN_STD 未満か、墨（濃さ INK 未満）の画素が MIN_INK の割合未満なら使わない。
# 印刷のほとんどない区画は、相関の最大の位置が決まらないため
MIN_STD = 12
INK = 200
MIN_INK = 0.02
# 当てはめで、外れた区画の重みを0にする（Tukey の重み。定数は、外れのない正規分布で効率が95%になる値）。くり返しの形
# （等間隔の線や文字）で別の所に合った区画があるため。ずれ全体に多項式を当てはめるのは、近くの区画のずれの中央値で
# 四隅を動かすと、近くの区画のずれが割れた欄が約20画素動きすぎ、覆える数字の端を範囲の外に出すため
TUKEY = 4.685
ROUNDS = 10  # 重みを求め直す回数
MIN_FIT = 20  # 当てはめの重みの合計がこれより小さければ、補わない（照合できた区画が少なく、多項式が決まらない）
MIN_SHIFT = 2.0  # 欄の四隅のずれ（見本の座標の画素）がどれもこれ未満なら、補わない


def transforms(
    gray: np.ndarray, image: np.ndarray, H: np.ndarray, fields: Sequence[Field]
) -> list[np.ndarray | None]:
    """fields（見本の座標の欄）ごとの、用紙の曲がりを補った変換（見本の座標 → 作業画像の座標）

    gray はページの作業画像、image は見本の画像（TEMPLATE_SCALE の解像度）、H は位置合わせの変換。補わない欄は None
    （照合できた区画が足りないとき、欄の四隅のずれがどれも MIN_SHIFT 未満のとき）。
    """
    h, w = image.shape
    shift = _displacement(
        _offsets(gray, image, H), (w / TEMPLATE_SCALE, h / TEMPLATE_SCALE)
    )
    if shift is None:
        return [None] * len(fields)
    return [_field_transform(f, H, shift) for f in fields]


def _offsets(gray: np.ndarray, image: np.ndarray, H: np.ndarray) -> np.ndarray:
    """見本の画像の格子の区画を、変換 H で見本の座標に移したページの中で照合し、使える区画の
    (中心x, 中心y, ずれx, ずれy) の並び（N×4）を返す。見本の座標と、その画素で、ずれは、ページの中身の位置 −
    変換で移した位置"""
    s = TEMPLATE_SCALE
    th, tw = image.shape
    scale = np.diag([s, s, 1.0])
    small = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    warped = cv2.warpPerspective(
        small, scale @ H @ np.linalg.inv(scale), (tw, th),
        flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderValue=255,
    )  # fmt: skip
    B, M = PATCH, SEARCH
    out = []
    for x, y in _usable_patches(image):
        patch = image[y : y + B, x : x + B]
        area = warped[y - M : y + B + M, x - M : x + B + M]
        if area.std() < 1:  # 一様な所（用紙の外の白など）では相関を求められない
            continue
        r = cv2.matchTemplate(area, patch, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(r)
        if score >= MIN_SCORE:
            c = ((x + B / 2) / s, (y + B / 2) / s)
            out.append((*c, (loc[0] - M) / s, (loc[1] - M) / s))
    return np.array(out, np.float64).reshape(-1, 4)


# 見本の画像の中身 → 使える区画の左上（_usable_patches）
_PATCHES: dict[bytes, list[tuple[int, int]]] = {}


def _usable_patches(image: np.ndarray) -> list[tuple[int, int]]:
    """見本の画像の格子の区画のうち、照合に使えるもの（印刷のある区画）の左上 (x, y) の並び（見本の画像の画素。上の
    行から順）。見本の画像だけで決まるので、画像の中身ごとに1回だけ求める（1ページの照合のたびに求めると、区画の
    標準偏差の計算が約550区画で数十ミリ秒かかるため）"""
    key = (
        hashlib.blake2b(image.tobytes(), digest_size=16).digest()
        + repr(image.shape).encode()
    )
    found = _PATCHES.get(key)
    if found is None:
        th, tw = image.shape
        B, M = PATCH, SEARCH
        found = []
        for y in range(M, th - B - M, STEP):
            for x in range(M, tw - B - M, STEP):
                patch = image[y : y + B, x : x + B]
                if patch.std() >= MIN_STD and (patch < INK).mean() >= MIN_INK:
                    found.append((x, y))
        # 様式を編集して見本の画像が変わり続けても、際限なく増やさない
        if len(_PATCHES) >= 64:
            _PATCHES.clear()
        _PATCHES[key] = found
    return found


def _basis(p: np.ndarray, size: tuple[float, float]) -> np.ndarray:
    """点の並び p（N×2）の、3次の多項式の項（座標は見本の大きさ size で割って0〜1にする）"""
    x, y = p[:, 0] / size[0], p[:, 1] / size[1]
    one = np.ones_like(x)
    return np.stack(
        [one, x, y, x * x, x * y, y * y, x**3, x * x * y, x * y * y, y**3], axis=1
    )


def _displacement(
    offsets: np.ndarray, size: tuple[float, float]
) -> Callable[[np.ndarray], np.ndarray] | None:
    """区画のずれに3次の多項式を当てはめた、点の並び（N×2）→ ずれの並び（N×2）の関数。求められなければ None。
    size は見本の座標での (幅, 高さ)"""
    if len(offsets) < MIN_FIT:
        return None
    A = _basis(offsets[:, :2], size)
    coefs = []
    for k in (2, 3):
        b = offsets[:, k]
        weight = np.ones(len(b))
        for _ in range(ROUNDS):
            root = np.sqrt(weight)
            c, *_ = np.linalg.lstsq(A * root[:, None], b * root, rcond=None)
            r = b - A @ c
            sigma = (
                1.4826 * np.median(np.abs(r)) + 1e-6
            )  # 残差の中央値から標準偏差を見積もる（正規分布の係数）
            u = r / (TUKEY * sigma)
            weight = np.where(np.abs(u) < 1, (1 - u * u) ** 2, 0.0)
            if weight.sum() < MIN_FIT:
                return None
        coefs.append(c)
    cx, cy = coefs
    return lambda p: np.stack([_basis(p, size) @ cx, _basis(p, size) @ cy], axis=1)


def _field_transform(
    f: Field, H: np.ndarray, shift: Callable[[np.ndarray], np.ndarray]
) -> np.ndarray | None:
    """欄 f の四隅を、ずれ（shift）の分だけ見本の座標で動かしてから H で移す変換。ずれがどれも MIN_SHIFT 未満なら None"""
    corners = np.float32(
        [[f.x, f.y], [f.x + f.w, f.y], [f.x + f.w, f.y + f.h], [f.x, f.y + f.h]]
    )
    shifts = np.float32(shift(corners.astype(np.float64)))
    if np.abs(shifts).max() < MIN_SHIFT:
        return None
    return H @ cv2.getPerspectiveTransform(corners, corners + shifts)
