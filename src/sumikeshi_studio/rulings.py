# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""印刷された罫線を、線分として読む

様式のバリエーション（見本と作りの違う申告書）で、見本の罫線とページの罫線を対応させて欄を探すために使う。見本の
罫線は、様式を保存するときに、塗る前の作業画像から読んで様式の .json に持つ（formstore.FormSpec.lines）。

線は formstore.Line（位置, 一方の端, もう一方の端, 太さ）で、作業画像の画素。横の線は位置が y、端が左右の x。縦の線は
位置が x、端が上下の y。
"""

import math
from collections.abc import Sequence

import cv2
import numpy as np

from .formstore import Line

# 長さ・太さ・距離は、作業画像（200dpi）の画素
MARGIN = 0.03  # 画像の端から除く割合（紙のふちを罫線として拾わないため）
MIN_H = 50  # 横の線の長さの下限（約6mm）
MIN_V = 30  # 縦の線の長さの下限（約4mm）
MAX_T = 9  # 太さの上限
TOUCH = 7  # 線の端が、ほかの向きの線につながるとみなす距離
LONG = 250  # これより長い線（約32mm）は、つながりを問わずに残す
SAME = 4  # 2値と、細く薄い線を強めた画像の両方で読んだ線を、同じ線とみなす位置の差
SOLID = 40  # 字を消す前に取り出す、確かな実線の長さの下限（すき間をつながずに、これだけ墨が続くもの）
# 字とみなして消す墨のかたまりの大きさ：外接する四角の短い辺が TEXT_MIN 以上で、長い辺が TEXT_MAX 未満。点線の点と
# 細い線の切れ端（短い辺が TEXT_MIN 未満）は消さない
TEXT_MIN = 5
TEXT_MAX = 50
# 墨とみなす濃さ（256段階）。2値は、31画素四方の平均よりこれ以上暗い画素。細く薄い線を強めた画像（ブラックハット）は、
# 線をまたぐ向きの周りよりこれ以上暗い画素
INK_DIFF = 15
INK_HAT = 12
# 字を探すときの2値の濃さ。線を読むときより低くして、薄い字と字のふちも消す（消し残った字のふちが、低いしきい値で
# 読む線につながって、線になるため）
TEXT_DIFF = 10
# 薄く印刷された線は、所どころが INK_DIFF・INK_HAT を下回って切れ端になり、長さやつながりの条件で落ちる。切れ端を、
# 低いしきい値で読んだ線に沿ってつなぐ。しきい値そのものを下げないのは、下げると字の画が線として多く読まれるため
# （スキャンに見立てた国税庁の3様式で、字から読まれる線が約300本から約1,500本に増えた）
LOW_DIFF = 10
LOW_HAT = 8
# 線の向きに ALONG 画素の平均にした画像では、雑音が小さくなるので、さらに低いしきい値（ALONG_HAT）でも、長く続く薄い
# 線だけが残る。長い平均だけでは線が端の先まで伸びるので、ALONG_SHORT 画素の平均でもしきい値以上の画素に限る
ALONG = 31
ALONG_SHORT = 7
ALONG_HAT = 5
NEAR = 2.0  # 同じ低いしきい値の線に入る切れ端を、1本の線とみなす位置の差（これより離れた切れ端は、二重線の別の線）
# 雑音の大きさ（_noise）がこれを超えるページでは、画素ごとの低いしきい値（LOW_DIFF・LOW_HAT）の線を使わない。雑音を
# 拾って、線の端が伸びたり、余分な線が増えたりするため
QUIET = 1.5
# 低いしきい値でだけ読めた線（切れ端のない線）を足すときの長さの下限。両端がほかの向きの線につながるものだけ足す
NEW_H = 80
NEW_V = 60
ROUNDS = 8  # 足す線の候補を、つながりで絞り直す回数の上限


def read(gray: np.ndarray) -> tuple[tuple[Line, ...], tuple[Line, ...]]:
    """罫線を読む：(横の線, 縦の線)。どちらも位置・端の順に並べる（read_all の初めの2つ）"""
    hs, vs, _, _ = read_all(gray)
    return hs, vs


def read_all(
    gray: np.ndarray,
) -> tuple[tuple[Line, ...], tuple[Line, ...], tuple[Line, ...], tuple[Line, ...]]:
    """罫線と、線として読めたが罫線に入れなかった線を読む：(横の線, 縦の線, 罫線に入れなかった横の線, 同じく縦の
    線)。どれも位置・端の順に並べる

    罫線に入れなかった線は、端がほかの向きの線につながらないか、低いしきい値でだけ読めて短い線。字の画も含むので、
    罫線としては使わない。短く薄い罫線は、つながる先の線が読めないとこちらに入るので、見本の罫線と対応させるときに、
    見本の線が対応しなかった所の近くでだけ使う（variants.place）。

    先に字を消す（_without_text）。点線やかすれた線をつなぐ処理は、同じ高さに並ぶ隣の字の画もつないで線にするため。
    字を消した画像から、2値で読んだ線に、細く薄い線を強めてから読んだ線のうち、2値の線と重ならないものを足す
    （_raw）。スキャンで細い線が2値にすると落ちる用紙と、点線の区切りが多い用紙の両方で崩れにくかった。

    読んだ線は、薄い線の切れ端を含む。切れ端を、低いしきい値で読んだ線と、線の向きに平均した画像の線に沿って
    つなぐ（_joined）。つないだ線のうち、端がほかの向きの線につながる線（_connected）を1本でも含むものは、そのまま
    残す。含まないものと、低いしきい値でだけ読めた長い線は、端がほかの向きの線につながるものだけを足す
    （_supported）。
    """
    clean = _without_text(gray)
    hs, vs = _raw(clean, INK_DIFF, INK_HAT)
    kept_h, kept_v = (set(lines) for lines in _connected(hs, vs, gray.shape))
    parts_h = [(s, s in kept_h) for s in hs]
    parts_v = [(s, s in kept_v) for s in vs]
    new_h: list[Line] = []
    new_v: list[Line] = []
    if _noise(gray) <= QUIET:
        low_h, low_v = _raw(clean, LOW_DIFF, LOW_HAT)
        parts_h, new_h = _joined(parts_h, low_h, to_ends=True)
        parts_v, new_v = _joined(parts_v, low_v, to_ends=True)
    along_h, along_v = _lines(*_along(clean), clean.shape)
    parts_h, _ = _joined(parts_h, along_h, to_ends=False)
    parts_v, _ = _joined(parts_v, along_v, to_ends=False)
    hs = [s for s, kept in parts_h if kept]
    vs = [s for s, kept in parts_v if kept]
    # 足す線の候補と、つながっていなければならない端の数。切れ端だけをつないだ線は、_connected と同じく横は片端、
    # 縦は両端。低いしきい値でだけ読めた線は、横も縦も両端
    maybe_h = [(s, 1) for s, kept in parts_h if not kept]
    maybe_v = [(s, 2) for s, kept in parts_v if not kept]
    maybe_h += [(s, 2) for s in new_h if s[2] - s[1] >= NEW_H]
    maybe_v += [(s, 2) for s in new_v if s[2] - s[1] >= NEW_V]
    more_h, more_v = _supported(maybe_h, maybe_v, hs, vs)
    added_h, added_v = set(more_h), set(more_v)
    rest_h = [s for s, _ in maybe_h if s not in added_h]
    rest_v = [s for s, _ in maybe_v if s not in added_v]
    rest_h += [s for s in new_h if s[2] - s[1] < NEW_H]
    rest_v += [s for s in new_v if s[2] - s[1] < NEW_V]
    return (
        tuple(sorted(hs + more_h, key=_position)),
        tuple(sorted(vs + more_v, key=_position)),
        tuple(sorted(rest_h, key=_position)),
        tuple(sorted(rest_v, key=_position)),
    )


def _position(line: Line) -> tuple[float, int]:
    return line[0], line[1]


# 値ごとの数を1回で数える画素の数の上限（OpenCV は数を単精度の実数で返すので、整数のまま表せる範囲に収める）
_HIST_PIXELS = 1 << 23


def _noise(gray: np.ndarray) -> float:
    """ページの雑音の大きさ（濃さの標準偏差の見込み）。横の隣の画素との差の絶対値のうち、線や字のふちの大きい差を
    除いたものの平均から求める（2つの画素の雑音の差の絶対値の平均は、標準偏差の 2/√π 倍なので、√π/2 を掛ける）"""
    if gray.shape[1] < 2 or gray.shape[0] == 0:
        return math.nan
    d = cv2.absdiff(gray[:, 1:], gray[:, :-1])
    # 差は0〜255 の整数なので、値ごとの数から中央値と平均を求める（全部の画素を並べ替えるより速い。値は同じ）
    count = np.zeros(256, np.int64)
    rows = max(1, _HIST_PIXELS // d.shape[1])
    for top in range(0, d.shape[0], rows):
        part = cv2.calcHist([d[top : top + rows]], [0], None, [256], [0, 256])
        count += part.ravel().astype(np.int64)
    upto = np.cumsum(count)
    n = int(upto[-1])
    low = int(np.searchsorted(upto, (n - 1) // 2 + 1))
    high = int(np.searchsorted(upto, n // 2 + 1))
    cap = 3 * ((low + high) / 2 + 1)
    value = np.arange(256)
    inside = value <= cap
    mean = int((value * count)[inside].sum()) / int(count[inside].sum())
    return mean * math.sqrt(math.pi) / 2


def _binary(gray: np.ndarray, diff: int = INK_DIFF) -> np.ndarray:
    """局所的な2値（31画素四方の平均より diff 以上暗い画素を墨にする。紙の地の濃さのむらによらない）"""
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, diff
    )


def _components(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """墨のかたまり：(かたまりの番号の画像, かたまりごとの外接する四角の x・y・幅・高さと面積)。番号0は地

    番号は16ビットで求める（32ビットで求めるより、ページ全体で約半分の時間で済む）。かたまりが16ビットに収まらない数
    あるページ（OpenCV が例外を出す）だけ、32ビットで求め直す。
    """
    try:
        _, labels, stats, _ = cv2.connectedComponentsWithStats(
            mask, connectivity=8, ltype=cv2.CV_16U
        )
    except cv2.error:
        _, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    return labels, stats


def _text_sized(ink: np.ndarray) -> np.ndarray:
    """墨のかたまりのうち、字の大きさ（TEXT_MIN・TEXT_MAX）のものの画素"""
    labels, stats = _components(ink)
    w, h = stats[:, 2], stats[:, 3]
    text = (np.minimum(w, h) >= TEXT_MIN) & (np.maximum(w, h) < TEXT_MAX)
    text[0] = False  # 地
    return (text.astype(np.uint8) * 255)[labels]


def _without_text(gray: np.ndarray) -> np.ndarray:
    """字の大きさの墨のかたまりを、周りの地の色で塗った画像

    すき間をつながない2値から、SOLID 以上続く確かな実線を先に取り出す。字のかたまりは、元の2値と、実線を引いた
    残りの両方で探す（罫線に触れた字は、罫線ごと1つの大きなかたまりになっていて、実線を引くと離れるため）。
    実線の画素は塗らない。
    """
    ink = _binary(gray, TEXT_DIFF)
    rect = cv2.MORPH_RECT
    solid = cv2.morphologyEx(
        ink, cv2.MORPH_OPEN, cv2.getStructuringElement(rect, (SOLID, 1))
    ) | cv2.morphologyEx(
        ink, cv2.MORPH_OPEN, cv2.getStructuringElement(rect, (1, SOLID))
    )
    near = np.ones((3, 3), np.uint8)
    apart = ink & ~cv2.dilate(solid, near)
    text = cv2.dilate(_text_sized(ink) | _text_sized(apart), near) & ~solid
    # 周りの地の色：15画素四方より細い暗い部分（字の画と線）を、周りの明るさで埋めた画像
    ground = cv2.morphologyEx(
        gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(rect, (15, 15))
    )
    out = gray.copy()
    cv2.copyTo(ground, text, out)
    return out


def _raw(clean: np.ndarray, diff: int, hat: int) -> tuple[list[Line], list[Line]]:
    """字を消した画像から読んだ線：2値（濃さ diff）の線に、細く薄い線を強めた画像（濃さ hat）の線のうち、2値の線と
    重ならないものを足す"""
    ink = _binary(clean, diff)
    hb, vb = _lines(ink, ink, clean.shape)
    rect = cv2.MORPH_RECT
    # 線をまたぐ向きに9画素より細い暗い部分を強める（ブラックハット）
    hh = cv2.morphologyEx(
        clean, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(rect, (1, 9))
    )
    vv = cv2.morphologyEx(
        clean, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(rect, (9, 1))
    )
    ht, vt = _lines(_at_least(hh, hat), _at_least(vv, hat), clean.shape)
    return _merge(hb, ht), _merge(vb, vt)


def _at_least(image: np.ndarray, level: int) -> np.ndarray:
    """8ビットの画像 image の、値が level 以上の画素を255、ほかを0にした画像"""
    return cv2.threshold(image, level - 1, 255, cv2.THRESH_BINARY)[1]


def _along(clean: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """線の向きに平均した画像で、線をまたぐ向きの周りより ALONG_HAT 以上暗い画素（横の線の画像, 縦の線の画像）。
    ALONG 画素の平均と ALONG_SHORT 画素の平均の、両方で暗い画素だけにする"""
    rect = cv2.MORPH_RECT
    out = []
    for horizontal in (True, False):
        across = cv2.getStructuringElement(rect, (1, 9) if horizontal else (9, 1))
        dark = None
        for n in (ALONG, ALONG_SHORT):
            mean = cv2.blur(clean, (n, 1) if horizontal else (1, n))
            hat = _at_least(
                cv2.morphologyEx(mean, cv2.MORPH_BLACKHAT, across), ALONG_HAT
            )
            dark = hat if dark is None else cv2.bitwise_and(dark, hat)
        out.append(dark)
    return out[0], out[1]


def _lines(
    hb: np.ndarray, vb: np.ndarray, shape: tuple[int, int]
) -> tuple[list[Line], list[Line]]:
    """墨の画像（横の線を読む画像 hb, 縦の線を読む画像 vb）から読んだ線"""
    h, w = shape
    rect = cv2.MORPH_RECT
    # 途切れた線（点線・かすれ）を、線の向きの9画素未満のすき間でつないでから、線の向きに長いもの（横は30画素以上、
    # 縦は24画素以上）だけを残す
    hb = cv2.morphologyEx(hb, cv2.MORPH_CLOSE, cv2.getStructuringElement(rect, (9, 1)))
    vb = cv2.morphologyEx(vb, cv2.MORPH_CLOSE, cv2.getStructuringElement(rect, (1, 9)))
    hl = cv2.morphologyEx(hb, cv2.MORPH_OPEN, cv2.getStructuringElement(rect, (30, 1)))
    vl = cv2.morphologyEx(vb, cv2.MORPH_OPEN, cv2.getStructuringElement(rect, (1, 24)))
    my, mx = int(h * MARGIN), int(w * MARGIN)
    for m in (hl, vl):
        m[:my] = 0
        m[h - my :] = 0
        m[:, :mx] = 0
        m[:, w - mx :] = 0
    hs = [s for s in _segments(hl, True) if s[2] - s[1] >= MIN_H and s[3] <= MAX_T]
    vs = [s for s in _segments(vl, False) if s[2] - s[1] >= MIN_V and s[3] <= MAX_T]
    return hs, vs


def _joined(
    parts: list[tuple[Line, bool]], low: list[Line], to_ends: bool
) -> tuple[list[tuple[Line, bool]], list[Line]]:
    """線 parts を、低いしきい値で読んだ線 low に沿ってつないだ線と、parts のどれとも重ならない low の線

    parts は [(線, 残すと決まった線か)]。線は、位置が SAME 以内で重なる low の線のうち、位置がいちばん近いものの
    切れ端とみなす。同じ low の線の切れ端のうち、位置の差が NEAR 以内のものを1本にまとめる（位置と太さは、いちばん
    長い切れ端のもの。残すと決まった切れ端を1つでも含めば、残すと決まった線にする）。to_ends なら、low の線の端まで
    伸ばす。そうでなければ、切れ端のあいだをつなぐだけにする。
    """
    if not low:
        return list(parts), []
    lw = np.array(low, float).reshape(-1, 4)
    pieces: dict[int, list[tuple[Line, bool]]] = {}  # low の線の番号 → その切れ端
    out = []
    for part in parts:
        s = part[0]
        overlap = np.minimum(lw[:, 2], s[2]) - np.maximum(lw[:, 1], s[1])
        apart = np.abs(lw[:, 0] - s[0])
        on = (apart <= SAME) & (overlap > 0)
        if on.any():
            pieces.setdefault(int(np.argmin(np.where(on, apart, np.inf))), []).append(
                part
            )
        else:
            out.append(part)
    for j, members in pieces.items():
        members.sort(key=lambda part: part[0][1] - part[0][2])  # 長い順
        groups: list[list[tuple[Line, bool]]] = []
        for part in members:
            for group in groups:
                if abs(group[0][0][0] - part[0][0]) <= NEAR:
                    group.append(part)
                    break
            else:
                groups.append([part])
        for group in groups:
            starts = [s[1] for s, _ in group] + ([low[j][1]] if to_ends else [])
            ends = [s[2] for s, _ in group] + ([low[j][2]] if to_ends else [])
            longest = group[0][0]
            line = Line(longest[0], int(min(starts)), int(max(ends)), longest[3])
            out.append((line, any(kept for _, kept in group)))
    out.sort(key=lambda part: _position(part[0]))
    return out, [s for j, s in enumerate(low) if j not in pieces]


def _supported(
    maybe_h: list[tuple[Line, int]],
    maybe_v: list[tuple[Line, int]],
    hs: list[Line],
    vs: list[Line],
) -> tuple[list[Line], list[Line]]:
    """足す線の候補 [(線, つながっていなければならない端の数)] のうち、端が、ほかの向きの線（残すと決まった線 hs・vs
    か、残った候補）につながるもの

    画像の端で切られた端は、_connected と違って、つながっているとみなさない（紙のふちの影が、低いしきい値で、画像の
    端まで続く線として読まれるため）。
    """
    for _ in range(ROUNDS):
        all_h = hs + [s for s, _ in maybe_h]
        all_v = vs + [s for s, _ in maybe_v]
        left = []
        for maybe, others in ((maybe_h, all_v), (maybe_v, all_h)):
            x = np.array([s for s, _ in maybe], float).reshape(-1, 4)
            n = _touching(x[:, 1], x[:, 0], others).astype(int) + _touching(
                x[:, 2], x[:, 0], others
            ).astype(int)
            left.append([c for c, m in zip(maybe, n, strict=True) if m >= c[1]])
        if len(left[0]) == len(maybe_h) and len(left[1]) == len(maybe_v):
            break
        maybe_h, maybe_v = left
    return [s for s, _ in maybe_h], [s for s, _ in maybe_v]


def _segments(mask: np.ndarray, horizontal: bool) -> list[Line]:
    if not cv2.countNonZero(mask):
        return []
    # 線をまたぐ向きに、墨のない行（縦の線は列）が続く所を1行に詰めてから、かたまりを求める（線の画像は、ほとんどの
    # 行に墨がない。かたまりは隣の行としかつながらないので、詰めても、かたまりと、その大きさ・面積は変わらない）
    inked = mask.any(axis=1 if horizontal else 0)
    after = np.zeros(len(inked), bool)
    after[1:] = inked[:-1]
    kept = np.flatnonzero(inked | after)  # 墨のある行と、そのすぐ後の行
    small = np.take(mask, kept, axis=0 if horizontal else 1)
    _, st = _components(small)
    out = []
    for i in range(1, len(st)):
        x, y, w, h, area = st[i]
        if horizontal:
            y = kept[y]
        else:
            x = kept[x]
        if horizontal and w >= 3 * h:
            out.append(
                Line(float(y + h / 2), int(x), int(x + w), max(1, round(area / w)))
            )
        elif not horizontal and h >= 3 * w:
            out.append(
                Line(float(x + w / 2), int(y), int(y + h), max(1, round(area / h)))
            )
    out.sort(key=lambda s: (s[0], s[1]))
    return out


def _near_pairs(
    sorted_pos: np.ndarray, query: np.ndarray, radius: float
) -> tuple[np.ndarray, np.ndarray]:
    """query の値ごとに、小さい順に並んだ sorted_pos のうち、差が radius + 1 以内の値との組：(query の番号,
    sorted_pos の番号)。差が radius 以内かは、呼ぶ側で確かめる

    線の全部の組の表を作らないために使う（ディザのスキャンや雑音の大きいページでは線が数千本読まれ、全部の組の
    表は、1つで数百MBになる）。
    """
    first = np.searchsorted(sorted_pos, query - radius - 1, "left")
    last = np.searchsorted(sorted_pos, query + radius + 1, "right")
    counts = last - first
    rows = np.repeat(np.arange(len(query)), counts)
    within = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
    return rows, np.repeat(first, counts) + within


def _touching(ends: np.ndarray, pos: np.ndarray, others: Sequence[Line]) -> np.ndarray:
    """線ごとに、端（ends）が、ほかの向きの線（others）のどれかの上にあるか（線の位置は pos）"""
    out = np.zeros(len(ends), bool)
    if len(ends) == 0 or not others:
        return out
    o = np.array(others, float)
    o = o[np.argsort(o[:, 0], kind="stable")]
    i, j = _near_pairs(o[:, 0], ends, TOUCH)
    on = (
        (np.abs(o[j, 0] - ends[i]) <= TOUCH)
        & (o[j, 1] - TOUCH <= pos[i])
        & (pos[i] <= o[j, 2] + TOUCH)
    )
    out[i[on]] = True
    return out


def _connected(
    hs: list[Line], vs: list[Line], shape: tuple[int, int]
) -> tuple[tuple[Line, ...], tuple[Line, ...]]:
    """表の罫線として、端がほかの向きの線につながる線だけを残す（縦の線は両端、横の線は片端）。LONG 以上の長さの
    線は、つながりを問わずに残す（表の外枠や、ほかの線と離れて引かれた長い線）。

    画像の端から除く所（MARGIN）で切られた端は、つながっているとみなす（shape は画像の高さ・幅）。端の先の線は
    読めないので、表が画像の端の近くまである用紙では、外枠につながる線の端が、どの線にもつながらなくなるため。
    """
    height, width = shape
    my, mx = int(height * MARGIN), int(width * MARGIN)
    for _ in range(2):
        v = np.array(vs, float).reshape(-1, 4)
        keep_v = (v[:, 2] - v[:, 1] >= LONG) | (
            (_touching(v[:, 1], v[:, 0], hs) | (v[:, 1] <= my + TOUCH))
            & (_touching(v[:, 2], v[:, 0], hs) | (v[:, 2] >= height - my - TOUCH))
        )
        vs2 = [s for s, k in zip(vs, keep_v, strict=True) if k]
        h = np.array(hs, float).reshape(-1, 4)
        keep_h = (
            (h[:, 2] - h[:, 1] >= LONG)
            | _touching(h[:, 1], h[:, 0], vs2)
            | _touching(h[:, 2], h[:, 0], vs2)
            | (h[:, 1] <= mx + TOUCH)
            | (h[:, 2] >= width - mx - TOUCH)
        )
        hs2 = [s for s, k in zip(hs, keep_h, strict=True) if k]
        if len(vs2) == len(vs) and len(hs2) == len(hs):
            break
        hs, vs = hs2, vs2
    return tuple(hs), tuple(vs)


def _merge(a: list[Line], b: list[Line]) -> list[Line]:
    """a の線に、a のどの線とも重ならない b の線を足す（位置が SAME 以内で、長さの半分以上重なれば同じ線とする）"""
    out = list(a)
    if b and a:
        x = np.array(a, float)
        x = x[np.argsort(x[:, 0], kind="stable")]
        y = np.array(b, float)
        i, j = _near_pairs(x[:, 0], y[:, 0], SAME)
        overlap = np.minimum(y[i, 2], x[j, 2]) - np.maximum(y[i, 1], x[j, 1])
        shorter = np.minimum(y[i, 2] - y[i, 1], x[j, 2] - x[j, 1])
        same = (np.abs(y[i, 0] - x[j, 0]) <= SAME) & (overlap >= 0.5 * shorter)
        dup = np.zeros(len(b), bool)
        dup[i[same]] = True
        out += [s for s, d in zip(b, dup, strict=True) if not d]
    else:
        out += b
    out.sort(key=lambda s: (s[0], s[1]))
    return out
