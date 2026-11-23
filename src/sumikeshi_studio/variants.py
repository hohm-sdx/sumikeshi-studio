# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""様式のバリエーション（見本と欄の並びは同じで、行の高さ・列の幅・書体の違う申告書。給与ソフトが印字した用紙など）の欄を、
見本の罫線の並びで探す

見本との位置合わせで様式不明になったページ（judge）と、見本と位置合わせできたのに枠の線が見つからない欄のある
ページ（relocate。様式は位置合わせで決まっているので、下の 1. と、候補どうしを比べる所は通さない）に使う
（pipeline.analyze）。

1. 候補を絞る：使う様式のそれぞれと、位置合わせの1段目を、整合を判断する距離を広げて行い、整合した点の多い順に
   CANDIDATES 個を候補にする（点の数では切らない。書体が大きく違う用紙で点が少なくても落とさないため）
2. ページの罫線を読む（rulings.read_all。線として読めたが、罫線に入れなかった線も持つ）
3. 候補ごとに、罫線の一致度（score。見本の側の割合とページの側の割合）を求め、2つの割合がどちらもその様式の
   下限（FormSpec.line_match_min）以上の候補のうち、ページの側の大きいものに決める（decides）
4. 欄ごとに、見本の罫線とページの罫線を順番を保って対応させて位置を出し（place）、枠の線の中心に合わせる
   （frames.find・fit）。記録した組の線が見つからない欄を、確かでない欄とする。欄の辺に近い見本の線が対応しなかった
   辺は、その線を必ず対応させて求め直した対応が条件に合えば、それで決め（_forced）、合わなければ、条件に合うときに、
   ページの罫線に入れなかった線に合わせる（_unruled_line）

見本の罫線は、様式の .json の lines（formstore.FormSpec.lines）。持たない様式と、罫線の並びを判別に使わないと
決めた様式（FormSpec.line_match_use）は候補にしない。座標は、ページも見本も
作業画像（imaging.WORK_DPI。200dpi）の画素（見本の罫線は、様式を保存するときに塗る前の作業画像から読む）。
"""

import bisect
import functools
import math
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import NamedTuple

import cv2
import numpy as np

from . import align, frames, rulings
from .formstore import (
    LINE_MATCH_MAX,
    LINE_MATCH_MIN,
    LINE_MATCH_STEP,
    Field,
    FormSpec,
    Line,
    frame_pairs,
    spec_key,
)

# 種類の見分けをする候補の数（試作で、見本3つ・70枚のどれも、上位2つに正しい種類が入った）
CANDIDATES = 2
# 候補を絞るときの、整合を判断する距離（見本の画像の画素。作業画像では2倍）。位置合わせの align.RANSAC_PX より広げ、
# 書体の違う用紙でも整合した点を数える
PREFILTER_RANSAC = 12.0

# ---- 線の対応づけ（動的計画法）
# 重みはどれも、線の違い（_desc_cost。対数の比の和）と同じ尺度。値は、試作で給与ソフトが印字した申告書の欄の置き方を
# 見て決め、行の高さと列の幅を変えた合成画像では変えずに確かめた
GAP = 2.2  # 見本の線が対応しない（抜け）ときの重み
GAP_P = 0.4  # ページの線が対応しない（余分）ときの重み（ます目の点線の区切りなど、ページにだけある線は多い）
SKIP = 4  # 続けて抜けにしてよい見本の線の数
SKIP_P = 14  # 続けて余分にしてよいページの線の数
SPACE_W = 3.0  # 1つ前の対応からの間隔の比（対数）の重み
SPACE_CAP = 1.2  # 間隔の比（対数）の上限。これより違う間隔は同じ重みにする
POS_W = 0.25  # 写像で移した位置との差の重み。弱くする（行の高さが部分ごとに違い、位置に引かれて線を1本飛ばしたため）
POS_SIGMA = 60.0  # 写像で移した位置との差を、この長さ（作業画像の画素。約7.6mm）で割って重みにする
POS_CAP = 3.0  # その上限
# 端までの長さの比を求めるときに足す長さ（作業画像の画素。短い線の比が大きくなりすぎないため）
EXT_OFFSET = 10.0
EXT_CAP = 1.5  # 端までの長さの比（対数）の、片方の端ごとの上限
SNAP = 12  # 欄の辺に、見本の線が近いとみなす距離（作業画像の画素。約1.5mm）
ROUNDS_SCAN = 3  # 対応から作った写像で対応させ直す回数
ROUNDS_PLACE = 2  # 行と列を順に直す回数
# 欄の辺に近い見本の線がページの罫線と対応しなかったときに、その線に当たる線を、ページの罫線に入れなかった線
# （rulings.read_all）から探す（_unruled_line）。短く薄い辺の線は、つながる先の線が読めないと罫線に入らず、辺の位置が
# 前後の対応した線のあいだを均した位置になって、列の幅の変わり方が見本と違う用紙で、範囲が隣の欄にはみ出すため
# 均した位置から探す距離の上限：欄を均して置いた範囲の、辺をまたぐ向きの長さに対する割合（左右の辺は欄の幅、上下の
# 辺は欄の高さ）。画素の固定の値にしないのは、欄が横に長く、同じ画素でも、上下の辺では欄の高さに対する割合が大きい
# ため（個人番号の欄は、幅が約380〜450画素、高さが約40〜75画素。字の画を辺の線に選んだときに、左右の辺なら12桁の
# 端の1桁の一部が範囲の外に出るだけだが、上下の辺では12桁すべての上か下が外に出る）
UNRULED_REACH_X = 0.05  # 左右の辺
UNRULED_REACH_Y = 0.15  # 上下の辺
UNRULED_HALF = 0.5  # 隣の見本の線を均して移した位置までの距離の、この割合より手前だけを探す（隣の行・列の線を選ばない）
UNRULED_OVERLAP = 0.5  # 線の長さのうち、見本の線を移した区間に入る割合の下限
UNRULED_APART = 6  # 前後の対応した線から、この画素より近い線は使わない
INF = 1e18  # たどれない組の重み
# 見本の線を必ずページの線に対応させるときに、その線の組の重みから引いておく値（_align。対応の重みの合計がとりうる値
# より十分大きく、INF より十分小さい）
FORCE = 1e6

# ---- 種類の見分け
SCANS = 14  # 見本の表を、縦と横にそれぞれこの数の線で横切る
GOOD = 1.0  # 線の違い（_desc_cost）がこれ以下の対応を、よく合ったとみなす
SPACE_OK = 1.0  # 1つ前のよく合った対応からの間隔の比（対数。約2.7倍）がこれ以下
# 種類を決める条件は、様式ごとの下限（FormSpec.line_match_min）を、見本の側の割合（横切った見本の線のうち、ページの
# 線とよく合った線の割合）と、ページの側の割合（横切ったページの線のうち、見本の線とよく合った線の割合）の両方に
# 掛ける。片方だけでは決められない：
# - 線の少ない見本は、線の多い別の帳票のどこかの線と合い、見本の側だけが高くなる（控除証明書の見本を、扶養控除等の
#   罫線に当てると、見本の側は0.5を超える）
# - 線の少ないページ（控除証明書や、雑音のページ）は、線の多い見本のどれかの線と合い、ページの側だけが高くなる
# 全部の様式に共通の下限にしないのは、表の作りが似た別の帳票の一致度（扶養控除等申告書の見本に、公的年金等の
# 受給者の扶養親族等申告書で0.62）が、別の様式のバリエーションの一致度（基・配・特・所の申告書で0.60）より高く、
# 1組の値では両方を満たせないため
# 新しく登録する様式の下限を決めるときに、使うほかの様式の見本の罫線を当てる。いちばん近い様式との一致度に
# NEAR_MARGIN を足した値まで下限を上げ、一致度が NEAR_OFF を超える様式（表の作りが同じ帳票。下限をどこに置いても
# 分けられない）は、罫線の並びを判別に使わないにする。上げる下限は LINE_MATCH_MAX まで、LINE_MATCH_STEP 刻み
NEAR_MARGIN = 0.10
NEAR_OFF = 0.85
# 罫線のある範囲（Model.box）を決めるのに使う線の長さの下限（作業画像の画素。横の線は約25mm、縦の線は約15mm）。
# これより長い線がなければ、全部の線で決める
BOX_LONG_H = 200
BOX_LONG_V = 120
CROSS_REACH = 3  # 横切る位置が、線の端からこの画素だけ外でも、その線を横切ったとみなす
BOX_SLACK = 5  # 罫線のある範囲の端から、この画素だけ外の線までを、対応させる線にする（外枠の線を落とさないため）
EDGE_SKIP = 0.04  # 表を横切る線を置かない、罫線のある範囲の両端の幅（範囲の幅・高さに対する割合）


@dataclass(frozen=True)
class Model:
    hs: tuple[Line, ...]
    vs: tuple[Line, ...]
    box: tuple[float, float, float, float]  # 罫線のある範囲（x0, y0, x1, y1）
    # 線として読めたが、罫線に入れなかった線（rulings.read_all。位置の順。ページの側だけが持ち、見本の側は空）
    unruled_hs: tuple[Line, ...] = ()
    unruled_vs: tuple[Line, ...] = ()


class Match(NamedTuple):
    """罫線の一致度（score）。どちらも、見本と同じ画像で 1.0"""

    template: float  # 見本の側の割合：横切った見本の線のうち、ページの線とよく合った線
    page: float  # ページの側の割合：横切ったページの線のうち、見本の線とよく合った線


def decides(spec: FormSpec, m: Match) -> bool:
    """罫線の一致度 m で、様式 spec のバリエーションと決めるか"""
    return spec.line_match_use and min(m) >= spec.line_match_min


def default_limit(lines, others: Sequence[FormSpec]) -> tuple[bool, float]:
    """新しく登録する様式（見本の罫線が lines）の、(罫線の並びを判別に使うか, 罫線の一致度の下限) の初めの値

    others は使うほかの様式。その見本の罫線を、新しい様式の見本の罫線に当てた一致度（2つの割合の小さいほう）の
    最大を、いちばん近い様式との一致度とする。
    """
    tm = model(lines) if lines is not None else None
    near = 0.0
    if tm is not None:
        for other in others:
            om = template_model(other)
            if om is not None:
                near = max(near, min(score(tm, om)))
    steps = math.ceil(round((near + NEAR_MARGIN) / LINE_MATCH_STEP, 6))
    limit = min(max(LINE_MATCH_MIN, steps * LINE_MATCH_STEP), LINE_MATCH_MAX)
    return near <= NEAR_OFF, round(limit, 2)


@dataclass(frozen=True)
class Variant:
    """バリエーションと決めた結果"""

    spec: FormSpec  # 似ていると決めた見本の様式
    match: Match  # 罫線の一致度
    fields: tuple[Field, ...]  # 置いた範囲（作業画像の座標。frame は線が見つかった組）
    unframed: tuple[str, ...]  # 記録した組の線が見つからない欄の名前
    crossed: tuple[str, ...] = ()  # 枠の線を越えて書かれた記入がある欄の名前


def model(lines) -> Model | None:
    """罫線（rulings.read か rulings.read_all の形）と、罫線のある範囲。範囲は長い線の端で決める（文字の画の残りに
    引かれないため）。横か縦の線がなければ None"""
    hs = tuple(Line(*s) for s in lines[0])
    vs = tuple(Line(*s) for s in lines[1])
    if not hs or not vs:
        return None
    lh = [s for s in hs if s.length >= BOX_LONG_H] or hs
    lv = [s for s in vs if s.length >= BOX_LONG_V] or vs
    box = (
        min(min(s.a for s in lh), min(s.pos for s in lv)),
        min(min(s.pos for s in lh), min(s.a for s in lv)),
        max(max(s.b for s in lh), max(s.pos for s in lv)),
        max(max(s.pos for s in lh), max(s.b for s in lv)),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    unruled = (tuple(Line(*s) for s in part) for part in lines[2:4])
    return Model(hs, vs, box, *unruled)


class Map1D:
    """順番を変えない1次元の写像（対応した2点以上の点のあいだは1次、外は端の2点の傾きで伸ばす）"""

    def __init__(self, src, dst):
        pairs = sorted(zip(src, dst, strict=True))
        self.s = np.float64([p[0] for p in pairs])
        self.d = np.float64([p[1] for p in pairs])

    def __call__(self, v: float) -> float:
        s, d = self.s, self.d
        if v <= s[0]:
            return float(d[0] + (v - s[0]) * (d[1] - d[0]) / max(s[1] - s[0], 1e-6))
        if v >= s[-1]:
            return float(
                d[-1] + (v - s[-1]) * (d[-1] - d[-2]) / max(s[-1] - s[-2], 1e-6)
            )
        return float(np.interp(v, s, d))


def _linear(tbox, pbox, axis: int) -> Map1D:
    """見本の罫線のある範囲を、ページの範囲に移す1次の写像（axis 0 は x、1 は y）"""
    return Map1D([tbox[axis], tbox[axis + 2]], [pbox[axis], pbox[axis + 2]])


def _desc_cost(
    t: Line, p: Line, at_t: float, at_p: float, scale_t: float, scale_p: float
) -> float:
    """線の違い：太さの比と、横切る所から両端までの長さの比（どちらも対数。表の大きさの比で直してから比べる）。
    長さを表の大きさで割って差を比べると、ます目の4ますごとの区切り（1行分の短い線）と、見出しから下まで続く列の
    線の違いが小さくなり、取り違えたため、比にする"""
    c = abs(math.log((t.t + 1) / (p.t + 1)))
    s = scale_p / scale_t
    for et, ep in ((at_t - t.a, at_p - p.a), (t.b - at_t, p.b - at_p)):
        c += min(
            abs(math.log((max(ep, 0) + EXT_OFFSET) / (s * max(et, 0) + EXT_OFFSET))),
            EXT_CAP,
        )
    return c


def align_lines(
    T: Sequence[Line],
    P: Sequence[Line],
    fmap,
    at_t: float,
    at_p: float,
    scale_t: float,
    scale_p: float,
    across: float,
) -> tuple[list[tuple[int, int]], float]:
    """見本の線 T とページの線 P（どちらも位置の順）を、順番を保って対応させる。([(i, k)], 重みの合計)

    scale_t・scale_p は、線に沿った向きの表の大きさ（線の違いで、横切る所から両端までの長さを比べるのに使う）。across は、
    線をまたぐ向き（線の位置の向き）の、ページの表の大きさ ÷ 見本の表の大きさ（間隔を比べるのに使う。縦横の比が見本と
    違う用紙で、線に沿った向きの比で間隔を比べると、正しい組の重みが大きくなって行を飛ばすため）。
    重みは、線の違いと、1つ前の対応からの間隔の比（ページの間隔 ÷ 見本の間隔を across で割った値の対数）と、
    写像 fmap で移した位置との差（弱い）。行の高さが部分ごとに違っても、隣の対応との間隔の比は大きくは変わらない
    ので、写像で移した位置との差より崩れにくい。線が1本抜けても、その線が「抜け」になるだけで、先の対応はずれない。
    見本の線1本ごとに、ページの線と1つ前の対応のすべての組を numpy でまとめて求める。重みが同じ組は、1つ前の見本の
    線、ページの線の番号の小さいものを選ぶ。
    """
    n, m = len(T), len(P)
    if n == 0 or m == 0:
        return [], n * GAP + m * GAP_P
    desc = _desc_matrix(T, P, at_t, at_p, scale_t, scale_p)
    return _align(T, P, desc, np.array([fmap(t.pos) for t in T]), across)


def _desc_matrix(T, P, at_t: float, at_p: float, scale_t: float, scale_p: float):
    """見本の線とページの線の組ごとの、線の違いの重み（_desc_cost を numpy でまとめて求めたもの。[見本, ページ]）"""
    s = scale_p / scale_t
    tt = np.array([t.t for t in T], float)
    pt = np.array([p.t for p in P], float)
    ta = np.array([t.a for t in T], float)
    tb = np.array([t.b for t in T], float)
    pa = np.array([p.a for p in P], float)
    pb = np.array([p.b for p in P], float)
    desc = np.abs(np.log((tt[:, None] + 1) / (pt[None, :] + 1)))
    for et, ep in ((at_t - ta, at_p - pa), (tb - at_t, pb - at_p)):
        desc += np.minimum(
            np.abs(
                np.log(
                    (np.maximum(ep, 0)[None, :] + EXT_OFFSET)
                    / (s * np.maximum(et, 0)[:, None] + EXT_OFFSET)
                )
            ),
            EXT_CAP,
        )
    return desc


def _align(
    T, P, desc: np.ndarray, ft: np.ndarray, across: float, force: int | None = None
):
    """align_lines の本体。desc は _desc_matrix、ft は見本の線の位置を写像で移した位置。force は、必ずどれかの
    ページの線に対応させる見本の線の番号（その線の組の重みから FORCE を引いて求め、重みの合計には足し戻す）

    1つ前の対応に使えるページの線は、SKIP_P + 1 本前までなので、ページの線ごとにその本数の帯だけを持つ（ページの
    線の数の2乗の表を作らない。ディザや雑音で線が多いページで遅くなるため）。帯の中は、ページの線の番号の小さい順。
    """
    n, m = len(T), len(P)
    tp = np.array([t.pos for t in T])
    pp = np.array([p.pos for p in P])
    local = desc + POS_W * np.minimum(
        np.abs(ft[:, None] - pp[None, :]) / POS_SIGMA, POS_CAP
    )
    if force is not None:
        local[force] -= FORCE
    band = SKIP_P + 1
    kk = np.arange(m)
    k2 = (
        kk[:, None] - band + np.arange(band)[None, :]
    )  # [ページの線, 帯]。1つ前のページの線の番号
    inside = k2 >= 0
    k2 = np.where(inside, k2, 0)
    dp = pp[:, None] - pp[k2]
    valid = inside & (dp > 0)
    # 下の3つは [ページの線, 1つ前の見本の線, 帯] の形で使う（1つ前の見本の線の候補を、まとめて求めるため）
    cols = k2[:, None, :]
    usable_k = valid[:, None, :]
    logdp = np.log(np.where(valid, dp, 1.0))[:, None, :]
    gap_p = ((kk[:, None] - k2 - 1) * GAP_P)[:, None, :]
    D = np.full((n, m), INF)
    # 選んだ1つ前の対応：候補の見本の線（befores[i] の中の順）× band ＋ 帯の中の順。ここから始めるときは -1
    picked = np.full((n, m), -1, np.int32)
    befores = []
    for i in range(n):
        best = i * GAP + kk * GAP_P  # ここから始める（前の線はすべて抜け・余分）
        before_i = [i2 for i2 in range(max(0, i - SKIP - 1), i) if tp[i] - tp[i2] > 0]
        befores.append(before_i)
        if before_i:
            i2 = np.array(before_i)[None, :, None]
            before = D[i2, cols]
            logdt = np.array([math.log(across * (tp[i] - tp[a])) for a in before_i])
            c = (
                before
                + (i - i2 - 1) * GAP
                + gap_p
                + SPACE_W * np.minimum(np.abs(logdp - logdt[None, :, None]), SPACE_CAP)
            )
            c = np.where(usable_k & (before < INF), c, INF)
            # ページの線ごとに、1つ前の見本の線・ページの線の番号の小さい順に並んでいるので、最初の最小を選ぶ
            flat = c.reshape(m, -1)
            j = np.argmin(flat, axis=1)
            v = flat[kk, j]
            better = v < best
            best = np.where(better, v, best)
            picked[i] = np.where(better, j, -1)
        D[i] = best + local[i]
    end = D + (n - 1 - np.arange(n))[:, None] * GAP + (m - 1 - kk)[None, :] * GAP_P
    i, k = (int(v) for v in np.unravel_index(int(np.argmin(end)), end.shape))
    cost = float(end[i, k])
    out = [(i, k)]
    while (j := int(picked[i, k])) >= 0:
        i, k = befores[i][j // band], int(k2[k, j % band])
        out.append((i, k))
    if force is not None and any(i == force for i, _ in out):
        cost += FORCE
    return out[::-1], cost


class Scan(NamedTuple):
    """_scan の結果"""

    T: list[Line]  # 横切った見本の線（位置の順）
    P: list[Line]  # 横切ったページの線（位置の順）
    pairs: list[tuple[int, int]]  # 対応（T の番号, P の番号）
    cost: float  # 対応の重みの合計
    fmap: Map1D  # 対応から作った、見本の位置からページの位置への写像
    scale_t: float  # 線の向きの、見本の罫線のある範囲の大きさ（線の端の位置を比べるときの単位）
    scale_p: float  # 同じく、ページの罫線のある範囲の大きさ


def _crossing(segs: Sequence[Line], at: float, lo: float, hi: float) -> list[Line]:
    """at（横の線なら x、縦の線なら y）を横切り、位置が lo〜hi の線（位置の順）"""
    return [
        s
        for s in segs
        if s.a - CROSS_REACH <= at <= s.b + CROSS_REACH and lo <= s.pos <= hi
    ]


def _scan(
    tm: Model,
    pm: Model,
    horizontal: bool,
    at_t: float,
    at_p: float,
    fmap,
    force: int | None = None,
) -> Scan:
    """見本の at_t を横切る線と、ページの at_p を横切る線を対応させる。force は、必ず対応させる見本の線の番号（_align）"""
    if horizontal:  # 縦の線（x 一定）で横切り、横の罫線を対応させる
        T = _crossing(tm.hs, at_t, tm.box[1] - BOX_SLACK, tm.box[3] + BOX_SLACK)
        P = _crossing(pm.hs, at_p, pm.box[1] - BOX_SLACK, pm.box[3] + BOX_SLACK)
        st, sp = tm.box[2] - tm.box[0], pm.box[2] - pm.box[0]
        across = (pm.box[3] - pm.box[1]) / (tm.box[3] - tm.box[1])
    else:
        T = _crossing(tm.vs, at_t, tm.box[0] - BOX_SLACK, tm.box[2] + BOX_SLACK)
        P = _crossing(pm.vs, at_p, pm.box[0] - BOX_SLACK, pm.box[2] + BOX_SLACK)
        st, sp = tm.box[3] - tm.box[1], pm.box[3] - pm.box[1]
        across = (pm.box[2] - pm.box[0]) / (tm.box[2] - tm.box[0])
    pairs, cost, f = [], 0.0, fmap
    if not T or not P:
        return Scan(T, P, *align_lines(T, P, f, at_t, at_p, st, sp, across), f, st, sp)
    desc = _desc_matrix(T, P, at_t, at_p, st, sp)  # 写像によらないので1回だけ求める
    for _ in range(ROUNDS_SCAN):
        last = pairs
        pairs, cost = _align(T, P, desc, np.array([f(t.pos) for t in T]), across, force)
        if pairs == last:  # 写像が前の回と同じなので、くり返しても同じ対応になる
            break
        if len(pairs) >= 2:
            f = Map1D([T[i].pos for i, _ in pairs], [P[k].pos for _, k in pairs])
    return Scan(T, P, pairs, cost, f, st, sp)


class _Search(NamedTuple):
    """欄の辺の線を、ページの罫線に入れなかった線から探すときの手がかり（_unruled_line）"""

    unruled: Sequence[Line]  # ページの罫線に入れなかった線（辺と同じ向き。位置の順）
    ruled: Sequence[Line]  # ページの罫線（辺と同じ向き。位置の順）
    along: Callable[
        [float], float
    ]  # 線に沿った向きの、見本の位置からページの位置への写像
    beside: tuple[
        float, float
    ]  # 欄の近くとみなす、線に沿った向きの範囲（ページの位置）
    reach: float  # 均した位置から探す距離の上限（画素）


def _forced(scan: Scan, again: Callable[[int], Scan], v: float) -> Scan:
    """見本の辺の位置 v にあたる見本の線（SNAP 以内でいちばん近い見本の線）が、どのページの線にも対応していなければ、
    その線を必ず対応させて求め直した対応（again に線の番号を渡して求める）。その線を写した位置の近くに、どの見本の線にも
    対応しなかったページの線があれば求め直さず（_unmatched_near）、求め直しても、次の両方を満たすときだけ使う。それ
    以外は scan のまま：
    - 対応の重みの合計が、scan 以下
    - その線に対応させたページの線が、scan で、隣の見本の線に対応していた

    見本の線を移した位置が本当の線から大きくずれた用紙（見本の端の欄がなく、罫線のある範囲の両端を合わせると位置が
    ずれる用紙）では、最初の対応で見本の線がページの1本隣の線に対応し、写像を作り直して対応させ直しても、ずれた対応
    から作った写像を手がかりにするので、同じ対応のまま止まるため。隣の見本の線に対応していた線に限るのは、欄の中の
    字の画を線として読んだときに、それを辺の線に対応させないため（線に接する大きさの数字の「1」の縦の画など）。
    """
    near = min(
        ((abs(t.pos - v), i) for i, t in enumerate(scan.T) if abs(t.pos - v) <= SNAP),
        default=None,
    )
    if near is None:
        return scan
    i = near[1]
    if i in dict(scan.pairs) or _unmatched_near(scan, i):
        return scan
    alt = again(i)
    k = dict(alt.pairs).get(i)
    if k is None or alt.cost > scan.cost:
        return scan
    before = {kk: j for j, kk in scan.pairs}.get(k)
    if before is None or abs(before - i) != 1:
        return scan
    return alt


def _unmatched_near(scan: Scan, i: int) -> bool:
    """対応しなかった見本の線 scan.T[i] を写した位置から、隣の見本の線を写した位置までの UNRULED_HALF より手前に、
    どの見本の線にも対応しなかったページの線があるか

    本当の辺の線が、短く切れていて対応しないまま残っていることがあり、そのときに求め直すと、見本の辺の線が隣の見本の
    線のページの線（次の行の線）を取り、範囲が次の行まで広がるため。
    """
    at = scan.fmap(scan.T[i].pos)
    reach = min(
        (
            UNRULED_HALF * abs(scan.fmap(scan.T[j].pos) - at)
            for j in (i - 1, i + 1)
            if 0 <= j < len(scan.T)
        ),
        default=INF,
    )
    used = {k for _, k in scan.pairs}
    return any(abs(p.pos - at) < reach for k, p in enumerate(scan.P) if k not in used)


def _edge(scan: Scan, v: float, search: _Search | None = None) -> float:
    """見本の辺の位置 v に近い見本の線が対応していれば、その線のページの位置。対応していなければ、その線に当たる、
    ページの罫線に入れなかった線（_unruled_line。search で探す）の位置。どちらもなければ写像で移した位置"""
    matched = dict(scan.pairs)
    nearest = missing = None  # (見本の辺からの距離, 見本の線の番号)
    for i, t in enumerate(scan.T):
        d = abs(t.pos - v)
        if d > SNAP:
            continue
        if i in matched:
            if nearest is None or d < nearest[0]:
                nearest = (d, i)
        elif missing is None or d < missing[0]:
            missing = (d, i)
    if nearest is not None:
        i = nearest[1]
        return scan.P[matched[i]].pos + (v - scan.T[i].pos)
    if missing is not None and search is not None:
        i = missing[1]
        line = _unruled_line(scan, i, search)
        if line is not None:
            return line.pos + (v - scan.T[i].pos)
    return scan.fmap(v)


def _unruled_line(scan: Scan, i: int, search: _Search) -> Line | None:
    """対応しなかった見本の線 scan.T[i] に当たる、ページの罫線に入れなかった線（決められなければ None）

    写像で均して移した位置の近くに罫線があれば、探さない（_ruled_at）。候補は、位置が均した位置から search.reach
    以内（隣の見本の線を均して移した位置までの UNRULED_HALF より手前）で、前後の対応した線のあいだにあってどちらからも
    UNRULED_APART 画素より離れ、長さの UNRULED_OVERLAP 以上が、見本の線を写像 search.along で移した区間に入る線。
    候補の位置がどれも同じ（rulings.SAME 以内）で、欄の近く（search.beside）にかかる候補があるときだけ、その中の
    いちばん長い線にする。位置の違う候補があるときは、どれが辺の線か決められないので使わない（字の画や、見本にない
    線を選ばないため）。
    """
    t = scan.T[i]
    at = scan.fmap(t.pos)
    a, b = search.along(t.a), search.along(t.b)
    if _ruled_at(search.ruled, at, a, b):
        return None
    reach = search.reach
    for j in (i - 1, i + 1):
        if 0 <= j < len(scan.T):
            reach = min(reach, UNRULED_HALF * abs(scan.fmap(scan.T[j].pos) - at))
    lo = max((scan.P[k].pos for j, k in scan.pairs if j < i), default=-INF)
    hi = min((scan.P[k].pos for j, k in scan.pairs if j > i), default=INF)
    found = [
        s
        for s in _lines_near(search.unruled, at, reach)
        if lo + UNRULED_APART < s.pos < hi - UNRULED_APART and _inside(s, a, b)
    ]
    near = [s for s in found if s.b >= search.beside[0] and s.a <= search.beside[1]]
    if not near:
        return None
    best = max(near, key=lambda s: s.length)
    if any(abs(s.pos - best.pos) > rulings.SAME for s in found):
        return None
    return best


def _ruled_at(ruled: Sequence[Line], at: float, a: float, b: float) -> bool:
    """均した位置 at から rulings.SAME 以内に、長さの UNRULED_OVERLAP 以上が区間 a〜b に入る罫線があるか

    辺の線が所どころだけ読めたページでは、切れ端は短くて見本の線と対応しないが、均した位置はその線の上にある。
    そこから罫線に入れなかった線に動かすと、欄の中の字の画を辺にするため（線に接する大きさの数字の縦の画や、数字の
    上下の端が横に並んだもの）。
    """
    return any(_inside(s, a, b) for s in _lines_near(ruled, at, rulings.SAME))


def _inside(s: Line, a: float, b: float) -> bool:
    """線 s の長さの UNRULED_OVERLAP 以上が、区間 a〜b に入るか"""
    return min(s.b, b) - max(s.a, a) >= UNRULED_OVERLAP * s.length


def _lines_near(lines: Sequence[Line], at: float, reach: float) -> Sequence[Line]:
    """位置の順に並んだ lines のうち、位置が at から reach 以内の線"""
    first = bisect.bisect_left(lines, at - reach, key=_pos)
    last = bisect.bisect_right(lines, at + reach, key=_pos)
    return lines[first:last]


def _pos(line: Line) -> float:
    return line.pos


def place(tm: Model, pm: Model, rect) -> tuple[float, float, float, float]:
    """見本の欄 rect（x, y, 幅, 高さ。見本の座標）をページの上に置いた範囲

    横の線の対応で欄の行の位置を直し、直した行で縦の線を対応させて列の位置を直す、を順にくり返す（同時に直すと、
    まだ直していない行で縦の線を対応させ、1行下の線に引かれて欄が1行ずれた）。くり返して重みが増えたら前の対応に戻す。
    辺の位置は _edge で決める。辺にあたる見本の線が対応しなかったときは、その線を必ず対応させて求め直した対応を
    使うことがあり（_forced）、それも使わなければ、pm が罫線に入れなかった線を持つとき、辺をその線に合わせることがある
    """
    x, y, w, h = rect
    cx, cy = x + w / 2, y + h / 2
    fx, fy = _linear(tm.box, pm.box, 0), _linear(tm.box, pm.box, 1)
    px = fx(cx)
    best_h = best_v = None
    for _ in range(ROUNDS_PLACE):
        hres = _scan(tm, pm, True, cx, px, fy)
        if best_h is None or hres.cost <= best_h.cost:
            best_h = hres
            again_h = functools.partial(_scan, tm, pm, True, cx, px, fy)
        py = best_h.fmap(cy)
        vres = _scan(tm, pm, False, cy, py, fx)
        if best_v is None or vres.cost <= best_v.cost:
            best_v = vres
            again_v = functools.partial(_scan, tm, pm, False, cy, py, fx)
        px = best_v.fmap(cx)
    # 罫線に入れなかった線から辺の線を探すときに、欄の近くとみなす範囲は、欄を均して置いた範囲を、その短い辺の長さ
    # だけ広げたもの
    y0, y1 = best_h.fmap(y), best_h.fmap(y + h)
    x0, x1 = best_v.fmap(x), best_v.fmap(x + w)
    margin = min(abs(y1 - y0), abs(x1 - x0))
    rows = _Search(
        pm.unruled_hs,
        pm.hs,
        best_v.fmap,
        (x0 - margin, x1 + margin),
        UNRULED_REACH_Y * abs(y1 - y0),
    )
    cols = _Search(
        pm.unruled_vs,
        pm.vs,
        best_h.fmap,
        (y0 - margin, y1 + margin),
        UNRULED_REACH_X * abs(x1 - x0),
    )
    top, bottom = (_edge(_forced(best_h, again_h, v), v, rows) for v in (y, y + h))
    left, right = (_edge(_forced(best_v, again_v, v), v, cols) for v in (x, x + w))
    return (left, top, right - left, bottom - top)


def score(tm: Model, pm: Model) -> Match:
    """見本の表を縦・横に SCANS 本ずつの線で横切り、それぞれで罫線の列を対応させ、よく合った線の数を、横切った見本の
    線の数とページの線の数で割った割合"""
    tx0, ty0, tx1, ty1 = tm.box
    fx, fy = _linear(tm.box, pm.box, 0), _linear(tm.box, pm.box, 1)
    s_h = (pm.box[2] - pm.box[0]) / (tx1 - tx0)
    s_v = (pm.box[3] - pm.box[1]) / (ty1 - ty0)
    good = total = total_p = 0
    for horizontal in (True, False):
        for j in range(SCANS):
            r = EDGE_SKIP + (1 - 2 * EDGE_SKIP) * (j + 0.5) / SCANS
            if horizontal:
                at_t = tx0 + r * (tx1 - tx0)
                at_p = fx(at_t)
                scan, s = _scan(tm, pm, True, at_t, at_p, fy), s_v
            else:
                at_t = ty0 + r * (ty1 - ty0)
                at_p = fy(at_t)
                scan, s = _scan(tm, pm, False, at_t, at_p, fx), s_h
            T, P = scan.T, scan.P
            total += len(T)
            total_p += len(P)
            last = None
            for i, k in scan.pairs:
                cost = _desc_cost(T[i], P[k], at_t, at_p, scan.scale_t, scan.scale_p)
                if cost > GOOD:
                    continue
                if last is not None:
                    dt = T[i].pos - T[last[0]].pos
                    dp = P[k].pos - P[last[1]].pos
                    if dt <= 0 or dp <= 0 or abs(math.log(dp / (s * dt))) > SPACE_OK:
                        last = (i, k)
                        continue
                good += 1
                last = (i, k)
    return Match(good / total if total else 0.0, good / total_p if total_p else 0.0)


# 様式ごとの見本の罫線（解析のプロセスで1回だけ求める）。鍵は、見本の画像と同じく、様式のファイルの場所と版と
# 保存の記録（ほかの人が保存し直しても取り違えない）
_models: dict[tuple, Model | None] = {}


def template_model(spec: FormSpec) -> Model | None:
    """様式の見本の罫線（持たなければ None）"""
    key = spec_key(spec)
    if key not in _models:
        _models[key] = model(spec.lines) if spec.lines is not None else None
    return _models[key]


def usable(spec: FormSpec) -> bool:
    """罫線の並びを判別に使う様式で、見本の罫線を持つ"""
    return spec.line_match_use and template_model(spec) is not None


def candidates(
    features: tuple, specs: Sequence[FormSpec]
) -> list[tuple[FormSpec, int]]:
    """罫線の並びを判別に使う、見本の罫線を持つ様式のうち、位置合わせの1段目（整合を判断する距離 PREFILTER_RANSAC）で整合した点の多い順の
    CANDIDATES 個（様式, 点の数）"""
    kp, desc = features
    usable_specs = [s for s in specs if usable(s)]
    counts = []
    for tpl in align.templates(usable_specs):
        n = 0
        if desc is not None and tpl.descriptors is not None:
            good = align.good_matches(tpl, features)  # 向きを決めるときの照合を使い回す
            if len(good) >= 4:
                _, mask = cv2.findHomography(
                    np.float32([tpl.keypoints[g.queryIdx].pt for g in good]),
                    np.float32([kp[g.trainIdx].pt for g in good]),
                    cv2.RANSAC,
                    PREFILTER_RANSAC,
                )
                n = int(mask.sum()) if mask is not None else 0
        counts.append((tpl.spec, n))
    counts.sort(key=lambda c: -c[1])  # 点が同じなら、使う様式の順
    return counts[:CANDIDATES]


class Below(NamedTuple):
    """罫線の一致度が下限に届かなかった候補（解析の結果には、様式そのものを持たない forms.BelowLimit にして入れる）"""

    spec: FormSpec
    match: Match


def judge(
    gray: np.ndarray, specs: Sequence[FormSpec], features: tuple | None = None
) -> tuple[Variant | None, Below | None]:
    """向きと傾きを直したページ画像 gray を、見本の罫線の並びで、使う様式（specs）のどれかのバリエーションと決め、
    欄ごとに範囲を置く。features は gray の align.page_features（求めてあれば渡す）

    決まれば (結果, None)。決まらなければ (None, 下限に届かなかった候補)。届かなかった候補が2つあれば、2つの割合の
    小さいほうが大きい候補（下限を下げたときに、先に決まるほうに近い）。候補にできる様式がないとき、ページの罫線が
    読めないときは (None, None)。
    """
    if not any(usable(s) for s in specs):
        return None, None
    if features is None:
        features = align.page_features(gray)
    found = candidates(features, specs)  # 候補にできる様式があるので、空にならない
    pm = page_model(gray)
    if pm is None:
        return None, None
    spec, match = None, None  # 決めた候補と、その罫線の一致度
    below = None
    for cand, _ in found:
        m = score(template_model(cand), pm)
        if not decides(cand, m):
            if below is None or min(m) > min(below.match):
                below = Below(cand, m)
            continue
        if (
            match is None or m.page > match.page
        ):  # 同じなら、候補の順（位置合わせの点の多いほう）
            spec, match = cand, m
    if spec is None:
        return None, below
    return _placed(gray, spec, pm, match), None


def locate(
    gray: np.ndarray, specs: Sequence[FormSpec], features: tuple | None = None
) -> Variant | None:
    """judge の結果のうち、バリエーションと決めた結果（決まらなければ None）"""
    return judge(gray, specs, features)[0]


def page_model(gray: np.ndarray) -> Model | None:
    """向きと傾きを直したページ画像 gray から読んだ罫線と、罫線に入れなかった線（横か縦の罫線が読めなければ None）"""
    return model(rulings.read_all(gray))


def placed_shift(spec: FormSpec, pm: Model, fields: Sequence[Field]) -> float:
    """様式 spec の欄を、見本の罫線とページの罫線 pm の対応で置いた位置と、fields（同じ順の、ほかの方法で置いた範囲）
    の、辺の位置の差の最大（画素）。枠の線には合わせない（線を探さずに済む分、relocate より軽い）"""
    tm = template_model(spec)
    worst = 0.0
    for f, g in zip(spec.fields, fields, strict=True):
        x, y, w, h = place(tm, pm, (f.x, f.y, f.w, f.h))
        worst = max(
            worst,
            abs(x - g.x),
            abs(y - g.y),
            abs(x + w - g.x - g.w),
            abs(y + h - g.y - g.h),
        )
    return worst


def relocate(
    gray: np.ndarray, spec: FormSpec, pm: Model | None = None
) -> Variant | None:
    """向きと傾きを直したページ画像 gray を、様式 spec のバリエーションとして、見本の罫線の並びで欄ごとに範囲を置く。
    spec が見本の罫線を持たないか、罫線の並びを判別に使わない様式のとき（usable）、ページの罫線が読めないとき、
    罫線の一致度が種類を決める条件（decides）に合わないときは None。pm は、gray から先に読んだ罫線（page_model）で、
    渡すと読み直さない"""
    if not usable(spec):
        return None
    tm = template_model(spec)
    if pm is None:
        pm = page_model(gray)
    if pm is None:
        return None
    match = score(tm, pm)
    return _placed(gray, spec, pm, match) if decides(spec, match) else None


def _placed(gray: np.ndarray, spec: FormSpec, pm: Model, match: Match) -> Variant:
    """様式 spec の欄を、見本の罫線とページの罫線 pm の対応で置き、枠の線の中心に合わせた結果

    枠の線を越えた記入も調べる（frames.crossings）。見本の画像はページと画素で合っていないので、見本の印刷を引かずに
    調べる（線の外側の墨を、どれも記入の候補とみる）。
    """
    tm = template_model(spec)
    fields, unframed, crossed = [], [], []
    for f in spec.fields:
        rect = place(tm, pm, (f.x, f.y, f.w, f.h))
        pairs = frame_pairs(f)
        found_pairs = ()
        if pairs:
            try:
                frame = frames.find(gray, rect, pairs)
                over = frames.crossings(gray, rect, frame, None)
                rect = frames.fit(rect, frame)
                missing, found_pairs = frame.missing, frame.found
                if over:
                    crossed.append(f.name)
            except Exception:  # noqa: BLE001  1つの欄の失敗で、ページ全体を失敗にしない
                # 置いた位置のまま、確かでない欄にする（見本と位置合わせしたページの欄と同じ扱い）
                traceback.print_exc()
                missing = True
            if missing:
                unframed.append(f.name)
        fields.append(Field(f.name, *rect, found_pairs))
    return Variant(spec, match, tuple(fields), tuple(unframed), tuple(crossed))
