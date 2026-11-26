# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""様式の登録・編集のときの確認：見本の画像を作り、保存の前に、位置合わせと題名で確かめる。登録と置き換えに使う
ページの歪みと記入も確かめる

解析のプロセスで動く：範囲を描く画面を開く前（analysis.check_page）、「保存」を押したとき（analysis.build_form）、
新しい用紙に置き換えるとき（analysis.move_ranges。今の見本と位置合わせして、墨消しする範囲を新しい用紙の上に移す）。
歪みの判定（distorted）は、解析（pipeline）が、見本と位置合わせできたページを罫線の並びで置き直すかを決めるときにも
使う。
"""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

import cv2
import numpy as np

from . import align, forms, ocr, paper, rulings, titles
from .formstore import TEMPLATE_SCALE, Field, FormSpec, frame_pairs, title_key
from .i18n import tr

# 型の画像で範囲の中を塗るときの値（作業画像の画素。型の画像に塗るときは TEMPLATE_SCALE 倍にする）。
# 残すのは欄を囲む枠の線だけで、欄の中の区切りの線（12ますの区切りなど）は残さない。区切りの線のすぐ左に書いた
# 数字の右の縦の画が、位置と長さで区切りの線と見分けられず、記入が型の画像に残るため
FILL_MARGIN = 4  # 範囲の外側へ塗る幅。枠の線にかかった記入の端も塗る（線は残す）
LINE_NEAR = 8  # 範囲の辺から、この画素以内にある線を、欄を囲む線とみなす
LINE_COVER = (
    0.6  # 辺の長さのこの割合以上続く線だけを残す（数字の画は欄の高さ・幅より短い）
)
LINE_MIN = 10  # 残す線の長さの下限（小さな範囲で、短い画を線とみなさないため）
# 縦の線は上と下の枠の線まで、横の線は左と右の枠の線まで、この画素以内に端が届いていれば残す。枠の線の近くに
# 大きく書いた数字の縦の画（「1」など）は、辺の長さの6割以上あっても、上と下の線までは届かない
LINE_REACH = 3
INK_BLOCK = 31  # 線を取り出す2値化で、明るさを比べる周りの大きさ
INK_OFFSET = 15  # 周りの明るさの平均より、これだけ暗い画素を墨とする
# 記入の見分け（written_ranges）：範囲の中の、字に見える画のまとまりがこの数以上なら、記入があるとみる。記入のない
# 用紙の範囲は、スキャン・白黒2値・JPEG で0、印刷の文字の上に置いた範囲で最大3、個人番号を書いた範囲は9以上だった。
# 警告だけで登録は続けられるので、短い記入も拾える数にする
WRITTEN_MIN = 2
WRITTEN_EDGE = 3  # 範囲の上か下の辺からこの画素以内に端があるまとまり（枠につながる区切りの線）は数えない
# 字に見える画のまとまりの大きさ。高さは範囲の高さの WRITTEN_H 倍のあいだ、幅は WRITTEN_W_MIN 画素以上かつ
# まとまりの高さの WRITTEN_W_SLIM 倍以上で、範囲の高さの WRITTEN_W_MAX 倍以下、画素は WRITTEN_AREA 以上
WRITTEN_H = (0.3, 1.1)
WRITTEN_W_MIN = 3
WRITTEN_W_SLIM = 0.2
WRITTEN_W_MAX = 1.2
WRITTEN_AREA = 30
# 歪んだ画像の見分け（distorted）：長い罫線の傾きのばらつき（度）がこれ以上か、用紙の割合（paper.paper_region）が
# DISTORT_PAPER 未満なら、写真などで歪んだ画像とみる。スキャンと PDF は0.87度以下、写真と写真から作った PDF は
# 1.37度以上だった。用紙の割合はスキャンで0.81以上、用紙を小さく写した写真（長い縦の線が見つからない）で0.29
DISTORT_SPREAD = 1.2
DISTORT_PAPER = 0.6
DISTORT_LONG = (800, 400)  # 傾きを測る横の線・縦の線の長さの下限（作業画像の画素）
DISTORT_MAX_T = 25  # 傾きを測る線の太さの上限（太いものは塗りや影）
DISTORT_RUN = 80  # 傾きを測る線を取り出すときの、墨が続く長さの下限（作業画像の画素。字の画を除く）


@dataclass(frozen=True)
class TemplateCheck:
    """保存の前に確かめた結果のうち、位置合わせを使うもの"""

    inliers: int  # 新しい型の画像と、元の画像の整合した特徴点の数
    aligned: bool  # 元の画像と位置合わせできた（MIN_INLIERS 点以上）
    # 互いに位置合わせが成り立った、ほかの様式（名前, 整合した特徴点の数）
    conflicts: tuple[tuple[str, int], ...]
    needed: int = align.MIN_INLIERS  # 位置合わせが成り立つのに要る点の数
    # 互いに位置合わせが成り立った様式のうち、2つの見本の題名で見分けられないもの：
    # (様式の名前, 題名の語に入れる1行の案, 消す行)。案を作れなければ、案は ""、消す行は ()
    unclear: tuple[tuple[str, str, tuple[str, ...]], ...] = ()
    # 塗る前の作業画像から読んだ見本の罫線（rulings.read。登録と新しい用紙への置き換え）。編集では None
    lines: tuple | None = None
    # 記入があるとみた墨消しする範囲の名前（written_ranges。登録と新しい用紙への置き換え）
    written: tuple[str, ...] = ()


@dataclass(frozen=True)
class PageCheck:
    """登録と置き換えに使うページを、範囲を描く画面を開く前に確かめた結果"""

    distorted: bool  # 写真などで用紙が歪んでいるようだ（distorted）
    # 初めの範囲のうち、記入があるとみた範囲の名前（written_ranges）
    written: tuple[str, ...]


def make_template(
    base: np.ndarray, base_is_work: bool, rects: Sequence[tuple]
) -> np.ndarray:
    """型の画像（TEMPLATE_SCALE の解像度）を作る。rects（作業画像の座標の左上・幅・高さ）の中を、
    紙の地の色（画像の明るさの中央値）で塗る（fill_ranges。欄を囲む枠の線は残す）

    base_is_work なら base は作業画像で、塗ってから縮める。そうでなければ base は今の型の画像で、縮め直さずに
    そのまま塗る（保存し直すたびに画像がぼけないように）。tools/make_templates.py の3様式も同じく作る。
    """
    image = fill_ranges(base, rects, 1.0 if base_is_work else TEMPLATE_SCALE)
    if not base_is_work:
        return image
    s = TEMPLATE_SCALE
    return cv2.resize(image, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)


def fill_ranges(image: np.ndarray, rects: Sequence[tuple], scale: float) -> np.ndarray:
    """image の、rects（作業画像の座標の左上・幅・高さ）を scale 倍した範囲の中を、外側へ FILL_MARGIN まで
    紙の地の色（画像の明るさの中央値）で塗った画像を返す。範囲の上下左右の辺に沿った枠の線は残す

    残す線は、すべての範囲で先に元の画像から求める（ほかの範囲を塗る所に入った線も残すため）。
    """
    h, w = image.shape
    margin = FILL_MARGIN * scale
    region = np.zeros((h, w), bool)
    keep = np.zeros((h, w), np.uint8)
    for x, y, rw, rh in rects:
        rect = (x * scale, y * scale, rw * scale, rh * scale)
        x0 = max(math.floor(rect[0] - margin), 0)
        y0 = max(math.floor(rect[1] - margin), 0)
        x1 = min(math.ceil(rect[0] + rect[2] + margin), w)
        y1 = min(math.ceil(rect[1] + rect[3] + margin), h)
        if x1 <= x0 or y1 <= y0:
            continue
        region[y0:y1, x0:x1] = True
        _mark_frame_lines(image, rect, scale, keep)
    keep = cv2.dilate(keep, np.ones((3, 3), np.uint8))  # 線の縁のぼけも残す
    out = image.copy()
    out[region & (keep == 0)] = int(np.median(image))
    return out


def _mark_frame_lines(
    image: np.ndarray, rect: tuple, scale: float, keep: np.ndarray
) -> None:
    """rect（image の座標の左上・幅・高さ）の辺に沿った枠の線の画素を、keep に 255 で印す

    横の線は、上か下の辺から LINE_NEAR 以内で、範囲の幅の LINE_COVER 以上続き、左と右の枠の線まで届くもの。
    縦の線は、左か右の辺から LINE_NEAR 以内で、範囲の高さの LINE_COVER 以上続き、上と下の枠の線まで届くもの。
    枠の線の位置は、辺の近くでいちばん長く続く線の位置（見つからなければ範囲の辺）。とぎれとぎれに写った線は
    届かないので塗る。墨は、周りより暗い画素（地の色が場所で違うスキャンや、色の付いた欄でも線を取り出すため）。
    """
    h, w = image.shape
    x, y, rw, rh = rect
    near = LINE_NEAR * scale
    pad = math.ceil((FILL_MARGIN + LINE_NEAR) * scale) + 2
    X0, Y0 = max(math.floor(x) - pad, 0), max(math.floor(y) - pad, 0)
    X1, Y1 = min(math.ceil(x + rw) + pad, w), min(math.ceil(y + rh) + pad, h)
    if X1 <= X0 or Y1 <= Y0:
        return
    block = max(round(INK_BLOCK * scale) | 1, 3)
    ink = cv2.adaptiveThreshold(
        image[Y0:Y1, X0:X1],
        255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY_INV,
        block,
        INK_OFFSET,
    )
    shortest = max(round(LINE_MIN * scale), 1)
    across = max(shortest, int(LINE_COVER * rw))
    down = max(shortest, int(LINE_COVER * rh))
    lines_h = cv2.morphologyEx(
        ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (across, 1))
    )
    lines_v = cv2.morphologyEx(
        ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, down))
    )
    rows = np.arange(Y0, Y1)
    cols = np.arange(X0, X1)
    top, bottom = np.abs(rows - y) <= near, np.abs(rows - (y + rh)) <= near
    left, right = np.abs(cols - x) <= near, np.abs(cols - (x + rw)) <= near
    lines_h = np.where((top | bottom)[:, None], lines_h, 0).astype(np.uint8)
    lines_v = np.where((left | right)[None, :], lines_v, 0).astype(np.uint8)
    # 枠の線の位置（image の座標）：辺の近くで、線の画素がいちばん多い行・列
    row_ink = (lines_h > 0).sum(axis=1)
    col_ink = (lines_v > 0).sum(axis=0)

    def line_at(ink, band, coords, edge):
        counts = np.where(band, ink, 0)
        return float(coords[int(np.argmax(counts))]) if counts.max() > 0 else edge

    y_top, y_bottom = (
        line_at(row_ink, top, rows, y),
        line_at(row_ink, bottom, rows, y + rh),
    )
    x_left, x_right = (
        line_at(col_ink, left, cols, x),
        line_at(col_ink, right, cols, x + rw),
    )
    reach = max(LINE_REACH * scale, 1.5)
    part = keep[Y0:Y1, X0:X1]
    for lines, start, end, horizontal in (
        (lines_h, x_left, x_right, True),
        (lines_v, y_top, y_bottom, False),
    ):
        n, labels, stats, _ = cv2.connectedComponentsWithStats(lines, connectivity=8)
        for i in range(1, n):
            sx, sy, sw, sh, _ = stats[i]
            a, b = (
                (X0 + sx, X0 + sx + sw - 1)
                if horizontal
                else (Y0 + sy, Y0 + sy + sh - 1)
            )
            if a <= start + reach and b >= end - reach:
                part[labels == i] = 255


def written_ranges(work: np.ndarray, fields: Sequence[Field]) -> tuple[str, ...]:
    """墨消しする範囲（作業画像の座標）のうち、記入があるとみた範囲の名前（ページから様式を登録するときの警告）

    範囲（外側 FILL_MARGIN 画素まで）の墨から、範囲の幅・高さの6割以上続く線（枠の線と、欄の高さいっぱいの区切り）を
    除き、上か下の辺から WRITTEN_EDGE 以内に端があるまとまり（枠につながる区切りの線）も除いて、高さが範囲の高さの
    3割以上・1.1倍以下で、幅が3画素以上かつまとまりの高さの2割以上・範囲の高さの1.2倍以下、画素が30以上のまとまりを
    数える。幅と画素の下限は、点線の区切りと、枠から離れて読まれた区切りの線を数えないため（縦の線だけの字「1」も
    数えない）。範囲の外の記入は見ない。
    """
    height, width = work.shape
    out = []
    for f in fields:
        x, y, w, h = f.x, f.y, f.w, f.h
        x0 = max(math.floor(x) - FILL_MARGIN, 0)
        y0 = max(math.floor(y) - FILL_MARGIN, 0)
        x1 = min(math.ceil(x + w) + FILL_MARGIN + 1, width)
        y1 = min(math.ceil(y + h) + FILL_MARGIN + 1, height)
        if x1 <= x0 or y1 <= y0 or w < 1 or h < 1:
            continue
        ink = cv2.adaptiveThreshold(
            work[y0:y1, x0:x1],
            255,
            cv2.ADAPTIVE_THRESH_MEAN_C,
            cv2.THRESH_BINARY_INV,
            INK_BLOCK,
            INK_OFFSET,
        )
        rect = cv2.MORPH_RECT
        across = max(LINE_MIN, int(LINE_COVER * w))
        down = max(LINE_MIN, int(LINE_COVER * h))
        lines = cv2.morphologyEx(
            ink, cv2.MORPH_OPEN, cv2.getStructuringElement(rect, (across, 1))
        ) | cv2.morphologyEx(
            ink, cv2.MORPH_OPEN, cv2.getStructuringElement(rect, (1, down))
        )
        rest = ink & ~cv2.dilate(lines, np.ones((3, 3), np.uint8))
        top, bottom = y - y0, y + h - y0
        inside = np.zeros_like(rest)
        inside[
            max(int(top) - 2, 0) : int(bottom) + 3,
            int(x - x0) + 2 : int(x + w - x0) - 1,
        ] = 255
        n, _, stats, _ = cv2.connectedComponentsWithStats(rest & inside, connectivity=8)
        count = 0
        for i in range(1, n):
            _, cy, cw, ch, area = stats[i]
            if cy <= top + WRITTEN_EDGE or cy + ch >= bottom - WRITTEN_EDGE:
                continue
            if (
                WRITTEN_H[0] * h <= ch <= WRITTEN_H[1] * h
                and max(WRITTEN_W_MIN, WRITTEN_W_SLIM * ch) <= cw <= WRITTEN_W_MAX * h
                and area >= WRITTEN_AREA
            ):
                count += 1
        if count >= WRITTEN_MIN:
            out.append(f.name)
    return tuple(out)


def line_angle_spread(work: np.ndarray) -> float:
    """向きと傾きを直した作業画像の、長い横の線（DISTORT_LONG）どうしと長い縦の線どうしの傾き（度）の、最大と
    最小の差のうち大きいほう。スキャンでは印刷された罫線はどれも平行で、写真では遠近と用紙の曲がりで線ごとに
    傾きが変わる。線が2本未満の向きは0とする"""
    ink = cv2.adaptiveThreshold(
        work,
        255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY_INV,
        INK_BLOCK,
        INK_OFFSET,
    )
    spread = 0.0
    for horizontal, minlen in ((True, DISTORT_LONG[0]), (False, DISTORT_LONG[1])):
        k = (DISTORT_RUN, 1) if horizontal else (1, DISTORT_RUN)
        m = cv2.morphologyEx(
            ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, k)
        )
        # 番号の画像は16ビットで求める（写真のページで、32ビットの番号の画像が解析の中の最大のメモリを押し上げるため）
        labels, stats = rulings.components(m)
        del m
        angles = []
        for i in range(1, len(stats)):
            x, y, w, h, _ = stats[i]
            length, thick = (w, h) if horizontal else (h, w)
            if length < minlen or thick > DISTORT_MAX_T:
                continue
            ys, xs = np.nonzero(labels[y : y + h, x : x + w] == i)
            slope = np.polyfit(xs, ys, 1)[0] if horizontal else np.polyfit(ys, xs, 1)[0]
            angles.append(math.degrees(math.atan(slope)))
        if len(angles) >= 2:
            spread = max(spread, max(angles) - min(angles))
    return spread


def distorted(work: np.ndarray) -> bool:
    """写真などで用紙が歪んでいるようか（DISTORT_SPREAD・DISTORT_PAPER）"""
    region = paper.paper_region(work)
    # 用紙の割合を先に数にして、線の傾きを求めるあいだ、用紙の部分の配列を持たない（解析の中の最大のメモリを増やさない）
    share = float(region.mean()) if region is not None else None
    del region
    if share is not None and share < DISTORT_PAPER:
        return True
    return line_angle_spread(work) >= DISTORT_SPREAD


def check_page(work: np.ndarray, fields: Sequence[Field]) -> PageCheck:
    """登録と置き換えに使うページ（作業画像）を、範囲を描く画面を開く前に確かめる"""
    return PageCheck(distorted(work), written_ranges(work, fields))


def check_template(
    small: np.ndarray,
    base: np.ndarray,
    base_is_work: bool,
    others: Sequence[FormSpec],
    saving: FormSpec | None = None,
    engine: ocr.Engine = ocr.Engine.RAPIDOCR,
) -> TemplateCheck:
    """新しい型の画像（small）を、元の画像（base）と、ほかの様式の型の画像と位置合わせする

    元の画像と位置合わせできなければ、範囲を消しすぎて手がかりが足りない。ほかの様式とは互いに（新しい
    型からほかの型へ、ほかの型から新しい型へ）位置合わせし、どちらかが成り立てば、類似した様式とする。
    saving（保存しようとしている様式。題名の語と除く語を使う）があれば、類似した様式ごとに、2つの見本の上の帯で
    題名を読み、題名で見分けられるかを確かめる（解析では、同じ用紙の部分に類似した様式が2つ以上あれば、
    題名で決めるため）。
    """
    kp, desc = align.orb().detectAndCompute(small, None)
    h, w = small.shape
    spec = FormSpec(
        "new",
        tr("新しい様式"),
        "",
        (round(w / TEMPLATE_SCALE), round(h / TEMPLATE_SCALE)),
        (),
    )
    tpl = align.Template(spec, small, kp, desc)
    page = (
        align.page_features(base)
        if base_is_work
        else align.orb().detectAndCompute(base, None)
    )
    H, inliers = align.register(tpl, page)
    conflicts = []
    similar = []  # 互いに位置合わせが成り立った型
    for other in align.templates(others):
        h1, n1 = align.register(other, (kp, desc))
        h2, n2 = align.register(tpl, (other.keypoints, other.descriptors))
        found = [n for H_, n in ((h1, n1), (h2, n2)) if H_ is not None]
        if found:
            conflicts.append((other.name, max(found)))
            similar.append(other)
    unclear = []
    if saving is not None and similar:
        new_title = template_title(small, engine)
        for other in similar:
            advice = title_advice(
                saving, new_title, other.spec, template_title(other.image, engine)
            )
            if advice is not None:
                unclear.append((other.name, *advice))
    return TemplateCheck(
        inliers, H is not None, tuple(conflicts), unclear=tuple(unclear)
    )


def template_title(image: np.ndarray, engine: ocr.Engine) -> str:
    """型の画像（TEMPLATE_SCALE の解像度）の上の帯（高さの TITLE_BAND）を、作業画像の解像度に拡大して OCR で
    読んだ文（保存の直前の確認）"""
    band = image[: max(round(image.shape[0] * titles.TITLE_BAND), 1)]
    factor = 1 / TEMPLATE_SCALE
    band = cv2.resize(band, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
    return "".join(t.text for t in ocr.read(band, engine, upright=True).texts)


def _distinguished(
    new: FormSpec, new_title: str, other: FormSpec, other_title: str
) -> bool:
    """2つの様式だけを候補にして、新しい見本の題名が新しい様式に、相手の見本の題名が相手の様式に決まるか"""
    pair = [new, other]
    a, b = titles.title_choice(new_title, pair), titles.title_choice(other_title, pair)
    return a is not None and a.id == new.id and b is not None and b.id == other.id


def title_advice(
    new: FormSpec, new_title: str, other: FormSpec, other_title: str
) -> tuple[str, tuple[str, ...]] | None:
    """類似した2つの様式を、見本の題名で見分けられるか。見分けられれば None。見分けられなければ、
    新しい様式の題名の語に入れる1行の案と、消す行を返す。案を作れなければ ("", ())

    案は、新しい見本の題名の「令和〇年分」と、相手の様式の行のうち新しい見本の題名に合う最も長い行を、全角の
    スペースで区切った1行。消す行は、新しい様式の行のうち、相手の見本の題名に合い、相手の様式の合った行と同じか
    長い行（相手の帳票のページで、並ぶか新しい様式に決まってしまう）。案を足して消す行を除いても見分けられなければ、
    案を作れないとする。
    """
    if _distinguished(new, new_title, other, other_title):
        return None
    nt, ot = title_key(new_title), title_key(other_title)
    year = re.search(r"令和\d+年分", nt)
    bases = [
        (n, w.strip())
        for w in other.title_words
        if (n := titles.line_length(nt, w)) is not None
    ]
    if year is None or not bases:
        return "", ()
    line = f"{year[0]}\u3000{max(bases)[1]}"
    other_length = titles.title_score(ot, other.title_words, other.title_excludes)
    deletions = tuple(
        w
        for w in new.title_words
        if (n := titles.line_length(ot, w)) is not None
        and (other_length is None or n >= other_length)
    )
    words = (*(w for w in new.title_words if w not in deletions), line)
    if not _distinguished(
        replace(new, title_words=words), new_title, other, other_title
    ):
        return "", ()
    return line, deletions


def move_ranges(
    work: np.ndarray, spec: FormSpec
) -> tuple[list[Field], list[Field]] | None:
    """新しい型にするページ（作業画像）を今の型と位置合わせし、成り立てば、墨消しする範囲と消す範囲を
    ページの上に移したものを返す（型の画像を置き換えるとき）。成り立たなければ None

    墨消しする範囲は、新しいページの印刷された枠の線（の中心）に合わせる。新しいページは記入のない用紙なので、
    登録と同じく、線より内側に描いた範囲の線も探す（forms.framed の inside）。範囲の frame（枠の線の記録）は、
    新しいページで線が見つかった組にする。消す範囲は移すだけ。
    """
    (tpl,) = align.templates([spec])
    features = align.page_features(work)
    H, inliers = align.register(tpl, features)
    if H is None:
        return None
    _, H, _ = align.refined(
        (tpl, H, inliers), features
    )  # 解析と同じ2段の位置合わせにする
    moved = [
        forms.framed(work, f, H, frame_pairs(f), inside=True).field for f in spec.fields
    ]
    return moved, [forms.move_field(f, H) for f in spec.erase]
