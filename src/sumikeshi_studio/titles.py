# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""題名を読み、題名で様式を決める

同じ用紙の部分に2つ以上の様式が成り立ったとき（類似した様式）だけ、見本の上の帯をページの上に移して題名を読み、
様式の題名の語と比べて決める。題名の語は、様式の編集の画面で登録する。
"""

import math
from collections.abc import Sequence

import cv2
import numpy as np

from . import align, i18n, ocr, paper
from .formstore import TEMPLATE_SCALE, FormSpec, title_key

# 印字のある範囲の上からこの分の1を、題名として読む。印字のある範囲は、濃さ TITLE_INK 未満の画素が
# TITLE_MIN_INK 個以上ある行の範囲とする。傾きを直すと画像の周りに白い余白が付き、12度の傾きでは
# 画像の上から8分の1の範囲に題名が入らなかったため、画像の上端ではなく印字の上端から測る
TITLE_PART = 8
TITLE_MIN_INK = 20
# 読む範囲の上に、読む範囲の高さのこの分の1の白い余白を足す（A4 の 200dpi の作業画像で約20行）。
# 題名の文字の上端が印字の上端のときも、読む範囲の上に余白ができるようにするため
TITLE_MARGIN_PART = 10
# 同じ用紙の部分に2つ以上の様式が成り立ったとき、見本の高さのこの割合の上の帯をページの上に移して、題名を読む
# （印字のある範囲の上から読む read_title の読み方では、見本のスキャンは題名の上の注意書きで題名が切れ、写真は
# 2枚の用紙の題名が混ざるため）
TITLE_BAND = 0.12


def read_title(gray: np.ndarray, engine: ocr.Engine) -> str:
    """ページの上部の題名を OCR で読んだ文（範囲を描く画面の「題名を読む」で使う）

    印字のある範囲の上から TITLE_PART 分の1を読む。写真などで用紙の周りが写っているときは、用紙の部分の
    印字だけで印字のある範囲を求め、読む範囲の横もその印字のある範囲に限る。用紙の部分に限らないと、机などの
    暗い部分も印字とみなされ、題名を探す範囲が写真の上端からになって、題名が入らないため。
    読む範囲の上には白い余白を足し、行ごとの向きの判定をせずに読む（作業画像は向きを直してあるため。
    向きの判定をすると、基・配・特・所の申告書の長い題名の行を逆さに読むことがある）。
    """
    ink = gray < paper.TITLE_INK
    if (region := paper.paper_region(gray)) is not None:
        ink &= region
    rows = np.flatnonzero(ink.sum(axis=1) >= TITLE_MIN_INK)
    first, last = (rows[0], rows[-1]) if rows.size else (0, gray.shape[0])
    height = max((last - first) // TITLE_PART, 1)
    end = first + height
    top = gray[first:end]
    if region is not None:
        # 横も用紙の上の印字のある範囲に限る。OCR は長い辺を縮めてから読むため、机を含めて幅が広いと
        # 題名の文字が小さくなり、写真の長辺の50%に写った従たる給与の申告書の題名を読めなかった
        cols = np.flatnonzero(ink[first:end].any(axis=0))
        if cols.size:
            top = top[:, cols[0] : cols[-1] + 1]
    top = np.pad(top, ((height // TITLE_MARGIN_PART, 0), (0, 0)), constant_values=255)
    return "".join(t.text for t in ocr.read(top, engine, upright=True).texts)


def line_length(title: str, line: str) -> int | None:
    """題名の語・除く語の1行が題名（title_key の形）に合えば、その行の長さを返す。合わなければ None

    行を空白（半角・全角のスペース）で区切ったときは、区切ったすべての語が題名にあるときだけ合う。区切った語の
    順は問わない。長さは、区切った語を title_key の形にした文字数の合計（空白とかっこは数えない）。
    """
    parts = [key for key in map(title_key, line.split()) if key]
    if not parts or not all(key in title for key in parts):
        return None
    return sum(map(len, parts))


def title_score(
    title: str, words: Sequence[str], excludes: Sequence[str]
) -> int | None:
    """題名（title_key の形）に対する様式の点：合った行のうち最も長い行の長さ。候補にならなければ None

    除く語のどれかの行が題名に合う、題名の語のどの行も題名に合わない、のどちらかなら候補にしない。
    """
    if any(line_length(title, w) is not None for w in excludes):
        return None
    lengths = [n for w in words if (n := line_length(title, w)) is not None]
    return max(lengths) if lengths else None


def title_choice(text: str, specs: Sequence[FormSpec]) -> FormSpec | None:
    """題名として読めた文から、類似した様式（specs）のどれかに決める。決められなければ None

    同じ用紙の部分で見本に類似した、同じ種類の帳票の様式を見分けるためのもの。題名の語は、解析ではここでだけ使う
    （様式を保存する前の確認でも、同じ規則で見分けられるかを確かめる。formcheck）。
    候補と長さを title_score で決め、合った行が最も長い候補に決める。最も長い行の長さが同じ候補が2つ以上ある
    とき、候補がないときは、決められない（名前の順では決めない）。
    """
    title = title_key(text)
    scored = [
        (length, spec)
        for spec in specs
        if (length := title_score(title, spec.title_words, spec.title_excludes))
        is not None
    ]
    if not scored:
        return None
    longest = max(length for length, _ in scored)
    winners = [spec for length, spec in scored if length == longest]
    return winners[0] if len(winners) == 1 else None


def _band_title(gray: np.ndarray, tpl: align.Template, H: np.ndarray, engine) -> str:
    """見本の上の帯（高さの TITLE_BAND）を変換 H でページの上に移した範囲を、OCR で読んだ文"""
    th, tw = tpl.image.shape
    s = TEMPLATE_SCALE
    band = np.float32(
        [
            [[0, 0]],
            [[tw / s, 0]],
            [[tw / s, th / s * TITLE_BAND]],
            [[0, th / s * TITLE_BAND]],
        ]
    )
    q = cv2.perspectiveTransform(band, H).reshape(-1, 2)
    height, width = gray.shape
    x0, y0 = (max(int(v), 0) for v in np.floor(q.min(axis=0)))
    x1 = min(math.ceil(q[:, 0].max()), width)
    y1 = min(math.ceil(q[:, 1].max()), height)
    if x1 <= x0 or y1 <= y0:
        return ""
    return "".join(
        t.text for t in ocr.read(gray[y0:y1, x0:x1], engine, upright=True).texts
    )


def similar_note(names: list[str]) -> str:
    """同じ用紙の部分で類似した様式を、題名で決められなかったときの「⚠要確認」の文（names の先頭がより類似）"""
    if len(names) == 2:
        return i18n.tr(
            "様式『{first}』か『{second}』か決められず、『{first}』で墨消ししました",
            first=names[0],
            second=names[1],
        )
    return i18n.tr(
        "様式{names}のどれか決められず、『{first}』で墨消ししました",
        names=i18n.quoted(names, "『』"),
        first=names[0],
    )


def choose(
    gray: np.ndarray, features: tuple, candidates: list[tuple], engine
) -> tuple[tuple, str, tuple[FormSpec, ...]]:
    """同じ用紙の部分で成り立った様式（candidates。先頭は作業画像の上の変換を持つ、点の最も多いもの）から、
    墨消しに使う様式を決める。(見本, 変換, 点)、「⚠要確認」の文（決められたときは空）、類似した様式の一覧を返す

    1つだけなら、それを使う。2つ以上なら、見本の上の帯で題名を読み、title_choice で決める。決められなければ、
    点の最も多い様式を使い、「⚠要確認」の文を返す。
    """
    top = candidates[0]
    similar = tuple(t.spec for t, _, _ in candidates)
    if len(candidates) < 2:
        return top, "", similar
    names = [t.name for t, _, _ in candidates]
    chosen = title_choice(_band_title(gray, top[0], top[1], engine), similar)
    if chosen is None:
        return top, similar_note(names), similar
    tpl, H, inliers = next(c for c in candidates if c[0].spec == chosen)
    # upright_group では成り立ったが、作業画像の上の変換をまだ求めていない
    if H is None:
        H, inliers = align.register(tpl, features)
        if H is None:
            return top, similar_note(names), similar
    return (tpl, H, inliers), "", similar
