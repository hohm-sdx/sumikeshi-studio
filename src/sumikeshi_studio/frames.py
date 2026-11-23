# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""墨消しする欄の、印刷された枠の線を探す処理と、枠の線を越えた記入を見つける処理

見本との位置合わせで欄をページに移した後（バリエーションは、罫線の対応で欄を置いた後）、欄ごとに上下左右の枠の線を
探し、墨消しの端を線の中心に合わせる（find・fit）。
枠の線を越えて書かれた記入は、線の外側の墨のうち、見本の画像の印刷にないものが、線をはさんで内側の記入と
つながっているかで見つける（crossings）。座標は、渡された画像（作業画像か、欄の周りを見本の座標に移した画像を
切り出したもの。どちらも WORK_DPI 相当）の画素の値。
"""

from dataclasses import dataclass

import cv2
import numpy as np

SIDES = ("top", "bottom", "left", "right")
# 枠の線の組（formstore.FRAME_PAIRS と同じ名前）と、その辺
PAIRS = {"top_bottom": ("top", "bottom"), "left_right": ("left", "right")}

# ---- 枠の線を探す
# 探す範囲：欄の辺から上下の辺は ±DEPTH_H、左右の辺は ±DEPTH_V 画素。上下を広げると、保険料控除申告書の上の辺で
# 約16画素上の太い線を選んだ。左右は、ます目の区切りの線が欄の端から約34画素離れているので広くできる。
# 見本と実物の用紙で印刷の位置が数画素違うことがあるので、左右は広くとる
DEPTH_H = 8
DEPTH_V = 12
LOOSE = 25  # 候補を探す墨：帯の紙の地より、この量だけ暗い画素（見本のスキャンの線は紙の地より32〜39暗い）
RUN = 9  # 線に沿ってこの画素以上続く墨だけを残す（文字の画を除く）
COVER_H = 0.30  # 横の線：欄の幅（両端の5%を除く）のこの割合以上に墨がある行を候補にする（白黒2値で途切れるため低め）
COVER_V = 0.50  # 縦の線：欄の高さ（上下の10%を除く）のこの割合以上
# 候補の中で、墨の割合が最大のこの割合未満の行で分ける（二重線を別の線にする）
SPLIT = 0.3
MAX_WIDTH = 8  # これより太い候補は線とみなさない
COL_MIN = 0.2  # 線の縁を求める列（墨がある列）が、帯の幅のこの割合に満たなければ、線とみなさない
WEAK = 0.5  # 同じ辺の候補のうち、墨の割合が最大の候補のこの割合に満たないもの（文字の並び）は除く
# 欄の外まで続くかを見る所（欄の端からの画素。位置合わせのずれを見込んで端から離す）と、続くとみなす墨の割合。
# 横の線は、続かなくても欄の幅の SOLID 以上に墨があれば線とみなす（扶養控除等申告書の配偶者の欄の下の線）
OUTSIDE = (14, 30)
OUTSIDE_COVER = 0.5
SOLID = 0.85
SIZE_TOL = 8  # 上と下（左と右）の組の、枠の内側の大きさと様式の欄の大きさの差の許し
# 枠の内側が様式の欄より小さいときは、さらにこの画素まで許す。範囲が枠の線より外まで描かれていることがあるため。
# 大きいときは広げない（保険料控除申告書の上の辺で、約16画素上の太い線を選ばないため）
OVERDRAWN = 8
# 縦の線の候補は、欄の上の端と下の端の近く（欄の高さの上下 END_BAND の帯。横の線の縁にかかる END_EDGE 画素は除く）
# にも墨があるものだけにする。欄の端に寄せて書いた数字の縦の画は、欄の高さの約7割で中央にあるので帯に届かず、
# 位置合わせが数画素ずれたときや、範囲が線より内側にあるときに、線に選ばれない。印刷の線は角まで続く。白黒2値では
# 細い線が途切れるので、帯の行の END_COVER 以上に墨があればよいとし、列も候補の縁から END_PAD 画素広げて見る
# （帯の行の5割にすると、白黒2値で本当の線も外れた）
END_BAND = 0.15
END_EDGE = 3
END_COVER = 0.2
END_PAD = 3
# 期待する位置からのずれの和が最も小さい組より、この画素以内の組があれば、内側の組にする。範囲が外まで描かれて
# いると、二重線の外の線（支払者の欄の右の線の6画素外）のほうが期待する位置に近くなるため。ただし、枠の内側が
# 最も小さい組より OVERDRAWN を超えて小さい組は選ばない（外まで描いた分では説明できない。選ぶと、左に寄せて
# 書いた数字「1」の縦の画を、左の線とみなすことがあるため）
INNER_TIE = 4


@dataclass(frozen=True)
class Frame:
    """欄の枠の線。辺ごとの (内側の縁, 外側の縁)（find に渡した画像の、画素の境目の座標）。見つからない辺は None"""

    top: tuple[int, int] | None
    bottom: tuple[int, int] | None
    left: tuple[int, int] | None
    right: tuple[int, int] | None
    pairs: tuple[str, ...] = tuple(PAIRS)  # 探した組（探さない組の辺は None）

    def side(self, name: str) -> tuple[int, int] | None:
        return getattr(self, name)

    @property
    def missing(self) -> tuple[str, ...]:
        """探した組のうち、線が見つからない辺"""
        return tuple(s for p in self.pairs for s in PAIRS[p] if self.side(s) is None)

    @property
    def found(self) -> tuple[str, ...]:
        """線が見つかった組"""
        return tuple(
            p for p in self.pairs if all(self.side(s) is not None for s in PAIRS[p])
        )


def find(
    gray: np.ndarray,
    rect: tuple[float, float, float, float],
    pairs: tuple[str, ...] = tuple(PAIRS),
    inside: bool = False,
    blurred: bool = False,
    depth_h: int = DEPTH_H,
) -> Frame:
    """rect（様式の欄をページに移したもの。x, y, 幅, 高さ）の近くで、枠の線を探す。pairs は探す組（PAIRS の名前）。
    depth_h は、上と下の線を探す範囲（欄の辺から ± この画素）

    上と下（左と右）の候補を組で選ぶ。枠の内側の大きさと rect の大きさの差が SIZE_TOL 以下（枠の内側が小さいときは
    SIZE_TOL + OVERDRAWN 以下）の組のうち、期待する位置からのずれの和が最も小さい組にする。ずれの和がそれより
    INNER_TIE 以内で、枠の内側の小ささが OVERDRAWN 以内の組があれば、その中で内側の組（枠の内側が小さいほう）に
    する（二重線の内側の線を選ぶため）。
    組にならなければ、どちらの辺も使わない（線が消えた白黒2値で、片方の辺だけの候補を採ると、ます目の端に書いた数字の
    画を線とみなすことがあるため）。
    inside は様式の登録のとき（範囲を描いた記入のない用紙）に使う。枠の内側が rect より大きい組も、探す範囲（depth）の
    2倍まで許す（範囲を左右の線より内側に描くと、枠の内側が 2 × ずれの分だけ大きくなり、SIZE_TOL では組にならないため）。
    記入のない用紙なので、数字の画を線に選ぶおそれはない。解析では使わない（記入のある用紙で、許しを広げると関係のない
    線や数字の画を選びやすくなるため）。
    blurred は、拡大してぼけた画像（写真を見本の座標に移したもの）で探すとき。線の位置（縁）を、探す帯の端まで墨が
    続く列を除いて求める（_mode_edges）。線とみなすかの判定のうち、線の太さ（MAX_WIDTH 以下）と期待する位置からの
    ずれ（depth 以内）は全部の列で求めた縁で、欄の外まで続くか（_extends）と、縦の線が欄の上下の端の近くまで届くか
    （_reaches_ends）は位置にする縁で調べる。2組の縁が違うのは blurred のときだけ。
    枠の内側の大きさは、下（右）の候補の内側の縁から上（左）の候補の内側の縁を引いて測り、0以下の組（同じ線や、
    上下が逆の組）は使わない（絶対値で測ると、高さ約12画素以下の欄を横線1本が通ったとき、その線を上と下の両方に
    選んで、墨消しの高さが0になるため）。
    """
    _, _, w, h = rect
    found: dict[str, tuple[int, int] | None] = dict.fromkeys(SIDES)
    for pair, size, depth in (("top_bottom", h, depth_h), ("left_right", w, DEPTH_V)):
        if pair not in pairs:
            continue
        a, b = PAIRS[pair]
        larger = SIZE_TOL + (2 * depth if inside else 0)
        valid = []  # (枠の内側の大きさ, ずれの和, a の候補, b の候補)
        for p in _candidates(gray, rect, a, depth, blurred):
            for q in _candidates(gray, rect, b, depth, blurred):
                inner = q[0] - p[0]
                if inner > 0 and -(SIZE_TOL + OVERDRAWN) <= inner - size <= larger:
                    valid.append((inner, abs(p[2]) + abs(q[2]), p, q))
        best = None
        if valid:
            least = min(valid, key=lambda v: v[1])
            best = min(
                (
                    v
                    for v in valid
                    if v[1] <= least[1] + INNER_TIE and least[0] - v[0] <= OVERDRAWN
                ),
                key=lambda v: v[:2],
            )
        found[a] = best[2][:2] if best else None
        found[b] = best[3][:2] if best else None
    return Frame(**found, pairs=tuple(p for p in PAIRS if p in pairs))


def fit(
    rect: tuple[float, float, float, float], frame: Frame
) -> tuple[float, float, float, float]:
    """墨消しする範囲：線が見つかった辺は線の中心、見つからない辺は rect のまま（広げない）"""
    x, y, w, h = rect
    edge = {"top": y, "bottom": y + h, "left": x, "right": x + w}
    for s in SIDES:
        if (line := frame.side(s)) is not None:
            edge[s] = (line[0] + line[1]) / 2
    return (
        edge["left"],
        edge["top"],
        edge["right"] - edge["left"],
        edge["bottom"] - edge["top"],
    )


def _candidates(
    gray: np.ndarray,
    rect: tuple[float, float, float, float],
    side: str,
    depth: int,
    blurred: bool = False,
) -> list[tuple[int, int, float]]:
    """辺 side の線の候補 [(内側の縁, 外側の縁, 期待する位置からのずれ)]。blurred は find の blurred"""
    x, y, w, h = rect
    height, width = gray.shape
    horiz = side in ("top", "bottom")
    # 欄が画像の端からはみ出すときに負の添字で反対側を切り出さないよう、どの端も画像の中に収める
    if horiz:
        e = y if side == "top" else y + h
        a0, a1 = max(round(x + 0.05 * w), 0), min(round(x + 0.95 * w), width)
        b0 = max(int(np.floor(e - depth)) - 1, 0)
        b1 = min(int(np.ceil(e + depth)) + 2, height)
        if a1 <= a0 or b1 <= b0:
            return []
        crop = gray[b0:b1, a0:a1].astype(np.float32)  # 行が線を横切る向き
        cover = COVER_H
    else:
        e = x if side == "left" else x + w
        a0, a1 = max(round(y + 0.10 * h), 0), min(round(y + 0.90 * h), height)
        b0 = max(int(np.floor(e - depth)) - 1, 0)
        b1 = min(int(np.ceil(e + depth)) + 2, width)
        if a1 <= a0 or b1 <= b0:
            return []
        crop = gray[a0:a1, b0:b1].astype(np.float32).T
        cover = COVER_V
    paper = np.percentile(crop, 80)
    loose = crop < paper - LOOSE
    kept = (
        cv2.morphologyEx(
            loose.astype(np.uint8), cv2.MORPH_OPEN, np.ones((1, RUN), np.uint8)
        )
        > 0
    )
    tilted = kept.copy()  # 上下1画素の傾きを許す
    tilted[1:] |= kept[:-1]
    tilted[:-1] |= kept[1:]
    share = tilted.mean(axis=1)
    exact = kept.mean(axis=1)
    out = []
    for s, t in _runs(share >= cover):
        peak = exact[s : t + 1].max()
        for u, v in _runs(exact[s : t + 1] >= SPLIT * peak):
            row = s + u + int(np.argmax(exact[s + u : s + v + 1]))
            # 縁は、その行の濃さの中央値（線の濃さ）と紙の地の中間をしきい値にして決める。線のぼけと、
            # 隣の数字の画を縁に含めない。帯全体の最も濃い部分（濃い数字）を基準にすると、薄い線を落とした
            line = float(np.median(crop[row]))
            thr = (paper + line) / 2 if paper - line >= 20 else paper - LOOSE
            edges = _mode_edges(crop < thr, row, blurred)
            if edges is None:
                continue
            # 線とみなすかの太さと位置のずれを決める、全部の列の縁（full_lo, full_hi）と、位置にする縁（lo, hi）。
            # 欄の外まで続くか・上下の端まで届くかは、位置にする縁で調べる。2組が違うのは blurred のときだけ
            (full_lo, full_hi), (lo, hi) = ((b0 + a, b0 + b) for a, b in edges)
            if (
                full_hi - full_lo > MAX_WIDTH
                or abs((full_lo + full_hi) / 2 - e) > depth
            ):
                continue
            center = (lo + hi) / 2
            solid = float(exact[s + u : s + v + 1].max())
            if not (horiz and solid >= SOLID) and not _extends(
                gray, rect, horiz, lo, hi, thr
            ):
                continue  # 欄の外まで続かない（数字の並びや画を線とみなさない）
            if not horiz and not _reaches_ends(gray, rect, lo, hi, thr):
                continue  # 欄の上下の端の近くまで続かない（欄の端に寄せた数字の縦の画を線とみなさない）
            inner, outer = (hi, lo) if side in ("top", "left") else (lo, hi)
            out.append((inner, outer, center - e, solid))
    if out:
        strongest = max(o[3] for o in out)
        out = [o for o in out if o[3] >= WEAK * strongest]
    return [o[:3] for o in out]


def _mode_edges(
    ink: np.ndarray, row: int, blurred: bool = False
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """行 row（の上下1行）に墨がある列ごとに、その墨の線を横切る向きの連なりの端を求め、最も多い値を返す
    （[始まり, 終わり+1)）。数字が線に接したり越えたりする列があっても、多くの列は線だけなので、引きずられない。
    返すのは、全部の列の縁と、線の位置にする縁の2組。blurred なら、線の位置にする縁は、連なりが ink の
    端（探す帯の端）まで続く列を除いて求める（除くと COL_MIN 未満なら除かない）。写真を見本の座標に移した画像では、
    線に接した数字の画が線とつながるので、その列が多いと、除かなければ縁の最頻値が帯の端になり、線の中心が欄の内側に
    ずれる（高さいっぱいに書いた数字で、墨消しの辺が線の中心より最大約2画素内側に入り、数字の端が墨消しの外に出る）。
    線の太さと期待する位置からのずれは、全部の列の縁で調べる（除いた縁で調べると、太すぎて線とみなさない帯も線になり、
    覆える数字の端を墨消しの外に出す欄がある）"""
    n, w = ink.shape
    start = np.full(w, -1)
    for d in (0, -1, 1):
        r = row + d
        if 0 <= r < n:
            start = np.where((start < 0) & ink[r], r, start)
    cols = np.flatnonzero(start >= 0)
    if not len(cols) or len(cols) < COL_MIN * w:
        return None
    tops, bottoms = [], []
    for c in cols:
        r0 = r1 = start[c]
        while r0 > 0 and ink[r0 - 1, c]:
            r0 -= 1
        while r1 < n - 1 and ink[r1 + 1, c]:
            r1 += 1
        tops.append(r0)
        bottoms.append(r1)
    tops, bottoms = np.array(tops), np.array(bottoms)
    edges = _modes(tops, bottoms)
    inside = (tops > 0) & (bottoms < n - 1)
    if not blurred or inside.sum() < COL_MIN * w:
        return edges, edges
    return edges, _modes(tops[inside], bottoms[inside])


def _modes(tops: np.ndarray, bottoms: np.ndarray) -> tuple[int, int]:
    """列ごとの連なりの始まりと終わりの、それぞれ最も多い値（[始まり, 終わり+1)）"""
    return int(np.bincount(tops).argmax()), int(np.bincount(bottoms).argmax()) + 1


def _extends(gray, rect, horiz, lo, hi, thr) -> bool:
    """線（[lo, hi) の行か列）が、欄の外まで続くか。横の線は欄の左右の外、縦の線は上下の外の、欄の端から
    OUTSIDE の所のどちらかに、OUTSIDE_COVER 以上の割合で墨がある"""
    x, y, w, h = rect
    height, width = gray.shape
    near, far = OUTSIDE
    if horiz:
        spans = [(x - far, x - near), (x + w + near, x + w + far)]
        limit = width
    else:
        spans = [(y - far, y - near), (y + h + near, y + h + far)]
        limit = height
    for a, b in spans:
        a, b = max(int(a), 0), min(int(b), limit)
        if b <= a:
            continue
        if horiz:
            band = gray[max(lo - 1, 0) : hi + 1, a:b]
        else:
            band = gray[a:b, max(lo - 1, 0) : hi + 1].T
        if band.size == 0:  # 線が画像の端の外
            continue
        darkest = band.min(axis=0)
        if (darkest < thr).mean() >= OUTSIDE_COVER:
            return True
    return False


def _reaches_ends(gray, rect, lo, hi, thr) -> bool:
    """縦の線の候補（[lo, hi) の列）が、欄の上の端と下の端の近く（END_BAND の帯）にも墨があるか"""
    _, y, _, h = rect
    height = gray.shape[0]
    for a, b in (
        (y + END_EDGE, y + END_BAND * h),
        (y + (1 - END_BAND) * h, y + h - END_EDGE),
    ):
        a, b = max(round(float(a)), 0), min(round(float(b)), height)
        if b <= a:
            return False
        band = gray[a:b, max(lo - END_PAD, 0) : hi + END_PAD]
        if band.size == 0 or (band.min(axis=1) < thr).mean() < END_COVER:
            return False
    return True


def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """真が続く区間 [(始まり, 終わり)]"""
    out, start = [], None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        if not f and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(flags) - 1))
    return out


# ---- 枠の線を越えた記入
BAND = 32  # 線の外側の帯の幅（4mm）。帯の端まで続くまとまりは、印刷の線か隣の欄の記入とみなして除く
INSIDE = 16  # 線の内側の帯の幅
CROSS_MIN = 3  # 印刷にない画素が、線の外側の縁からこの画素以上外まで続くものを、越えた記入の候補にする
AREA_MIN = 6  # まとまり（の印刷にない画素）の数の下限
CORNER = 8  # 欄の角から除く幅（角で直交する線の端を拾わない）
LINE_ROWS = 4  # 見つけた線の縁の外の、墨が5割以上の行は、この行数まで線に含める
FRINGE = 2  # 縁のぼけ（墨が3割以上の行）として線に含める行数
TICK_WIDTH = 8  # ます目の区切りの線とみなす幅の上限
TICK_SNAP = 3  # 区切りの線の位置（欄の幅を12か13で割った間隔）からのずれの許し
PRINT_SHIFT = 4  # 見本の印刷をページに合わせるときに試すずらしの範囲
PRINT_DILATE = 2  # 合わせた印刷を広げる量
# 線の内側の縁から、外側の帯の外の端までの画素の上限（線の太さ、線に含める行、帯）。この分の画像を調べる
REACH = MAX_WIDTH + LINE_ROWS + FRINGE + BAND


def crossings(
    gray: np.ndarray,
    rect: tuple[float, float, float, float],
    frame: Frame,
    template: np.ndarray | None,
) -> tuple[str, ...]:
    """枠の線を越えた記入がある辺。線が見つからない辺は調べない

    template は、見本の画像を gray と同じ座標・解像度に移したもの（forms._template_crop）。線の外側の墨のうち
    見本の印刷にないものが、線の外側の縁から CROSS_MIN 画素以上外まで続き、線をはさんで内側の記入（線から内へ
    続く画）と同じ列にあれば、越えた記入とみなす。ます目の区切りの線（線を共有する隣の欄のものを含む）は除く。
    """
    x, y, w, h = rect
    left = frame.left[0] if frame.left else x
    right = frame.right[0] if frame.right else x + w
    top = frame.top[0] if frame.top else y
    bottom = frame.bottom[0] if frame.bottom else y + h
    height, width = gray.shape
    x0, x1 = max(int(left) - REACH, 0), min(int(right) + REACH, width)
    y0, y1 = max(int(top) - REACH, 0), min(int(bottom) + REACH, height)
    ink = _ink(gray[y0:y1, x0:x1])
    printed = (
        _printed(ink, template[y0:y1, x0:x1])
        if template is not None
        else np.zeros_like(ink)
    )
    hits = []
    for side in SIDES:
        line = frame.side(side)
        if line is None:
            continue
        horiz = side in ("top", "bottom")
        # 直交する線が見つからない角は、線を探した範囲の分も除く（線の位置が分からないため）
        if horiz:
            skip = (
                CORNER + (0 if frame.left else DEPTH_V),
                CORNER + (0 if frame.right else DEPTH_V),
            )
            a0, a1 = round(left - x0) + skip[0], round(right - x0) - skip[1]
            base, span = y0, right - left
            v, p = ink[:, a0:a1], printed[:, a0:a1]
        else:
            skip = (
                CORNER + (0 if frame.top else DEPTH_H),
                CORNER + (0 if frame.bottom else DEPTH_H),
            )
            a0, a1 = round(top - y0) + skip[0], round(bottom - y0) - skip[1]
            base, span = x0, bottom - top
            v, p = ink[a0:a1, :].T, printed[a0:a1, :].T
        if v.size == 0:  # 辺の調べる所がない（小さな欄・画像の端）
            continue
        # 行が欄の内 → 外の順になるようにそろえる
        lo, hi = round(line[0]) - base, round(line[1]) - base
        if side in ("top", "left"):
            v, p = v[::-1], p[::-1]
            n = v.shape[0]
            lo, hi = n - lo, n - hi
        lo, hi = _widen(v.mean(axis=1), lo, hi)
        out = v[hi : hi + BAND].copy()
        out[out.mean(axis=1) >= 0.5] = False  # 線に平行な長い線
        ins = v[max(lo - INSIDE, 0) : lo][::-1].copy()
        ins[ins.mean(axis=1) >= 0.5] = False
        outside = _parts(out, CROSS_MIN, p[hi : hi + BAND])
        inside = _parts(ins, 3)
        ticks_in = _ticks(inside, span, skip[0]) if horiz else set()
        ticks_out = _ticks(outside, span, skip[0]) if horiz else set()
        inward = np.zeros(v.shape[1], bool)
        for i, part in enumerate(inside):
            if i not in ticks_in:
                inward[part["deep"]] = True  # 線から内へ続く画がある列
        for j, part in enumerate(outside):
            if part["reach"] >= BAND - 1 or j in ticks_out:
                continue
            near = np.zeros_like(inward)
            for c in part["cols"]:
                near[max(c - 1, 0) : c + 2] = True
            if (near & inward).any():
                hits.append(side)
                break
    return tuple(hits)


def _ink(crop: np.ndarray) -> np.ndarray:
    """記入の墨：紙の地より「40」か「紙の地と最も濃い部分の差の4分の1」の大きいほうだけ暗い画素"""
    if crop.size == 0:  # 欄が画像の外
        return np.zeros(crop.shape, bool)
    c = crop.astype(np.float32)
    paper = np.percentile(c, 80)
    dark = np.percentile(c, 0.5)
    return c < paper - max(40, 0.25 * (paper - dark))


def _printed(ink: np.ndarray, template_crop: np.ndarray) -> np.ndarray:
    """欄の周りの印刷の部分。見本の墨を ±PRINT_SHIFT 画素ずらしてページの墨と最もよく重なる位置に合わせ、
    PRINT_DILATE 画素広げる（見本とページで、印刷の位置が数画素違うため）"""
    if (
        ink.size == 0 or template_crop.shape != ink.shape
    ):  # 空の配列を OpenCV に渡さない
        return np.zeros(ink.shape, bool)
    t = template_crop.astype(np.float32)
    tink = (t < np.percentile(t, 80) - 40).astype(np.float32)
    s = PRINT_SHIFT
    padded = cv2.copyMakeBorder(
        ink.astype(np.float32), s, s, s, s, cv2.BORDER_CONSTANT, value=0
    )
    score = cv2.matchTemplate(padded, tink, cv2.TM_CCORR)
    dy, dx = np.unravel_index(int(np.argmax(score)), score.shape)
    M = np.float32([[1, 0, dx - s], [0, 1, dy - s]])
    shifted = cv2.warpAffine(
        tink, M, (tink.shape[1], tink.shape[0]), flags=cv2.INTER_NEAREST
    )
    k = 2 * PRINT_DILATE + 1
    return cv2.dilate(shifted.astype(np.uint8), np.ones((k, k), np.uint8)) > 0


def _widen(share: np.ndarray, lo: int, hi: int) -> tuple[int, int]:
    """見つけた線の範囲 [lo, hi) に、外の墨が5割以上の行（LINE_ROWS 行まで）と、縁のぼけ（FRINGE 行まで）を足す"""
    for limit, rows in ((0.5, LINE_ROWS), (0.3, FRINGE)):
        for _ in range(rows):
            if lo > 0 and share[lo - 1] >= limit:
                lo -= 1
            if hi < len(share) and share[hi] >= limit:
                hi += 1
    return lo, hi


def _parts(region: np.ndarray, reach_min: int, printed: np.ndarray | None = None):
    """region（行が線に近い順）の中の、線に接する（0行目か1行目にかかる）まとまり。printed を渡すと、その外の画素が
    reach_min 行以上・AREA_MIN 個以上あるものだけにする"""
    if region.size == 0:
        # connectedComponentsWithStats は、縦か横が0の配列を渡すと、例外を出さずにプロセスごと強制終了する
        # （OpenCV 5.0.0。小さな欄で、線が切り抜いた画像の端に接すると起きる）
        return []
    n, labels, stats, _ = cv2.connectedComponentsWithStats(
        region.astype(np.uint8), connectivity=8
    )
    out = []
    for k in range(1, n):
        top = stats[k, cv2.CC_STAT_TOP]
        reach = top + stats[k, cv2.CC_STAT_HEIGHT]
        if top > 1 or stats[k, cv2.CC_STAT_AREA] < AREA_MIN or reach < reach_min:
            continue
        part = labels == k
        if printed is not None:
            own = part & ~printed
            rows = np.flatnonzero(own.any(axis=1))
            if own.sum() < AREA_MIN or not len(rows) or rows.max() + 1 < reach_min:
                continue
        out.append(
            {
                "cols": np.flatnonzero(part[:2].any(axis=0)),  # 線に接する列
                "deep": np.flatnonzero(
                    part[2:6].any(axis=0)
                ),  # 線から2〜5行離れた所にもある列
                "reach": int(reach),
                "width": int(stats[k, cv2.CC_STAT_WIDTH]),
            }
        )
    return out


def _ticks(parts: list[dict], span: float, start: int) -> set[int]:
    """まとまりのうち、ます目の区切りの線とみなすものの番号。幅 TICK_WIDTH 以下で、欄の幅を12か13で割った間隔の
    位置（±TICK_SNAP）にあるもの。12と13で、合うものが多いほうにする（同じ数なら12）。start は、まとまりの列の
    0番目の、枠の内側の縁からの位置"""
    narrow = [i for i, part in enumerate(parts) if part["width"] <= TICK_WIDTH]
    best: list[int] = []
    for cells in (12, 13):
        step = span / cells
        hit = []
        for i in narrow:
            pos = parts[i]["cols"].mean() + start  # 枠の内側の縁からの位置
            if abs(pos / step - round(pos / step)) * step <= TICK_SNAP:
                hit.append(i)
        if len(hit) > len(best):
            best = hit
    return set(best)
