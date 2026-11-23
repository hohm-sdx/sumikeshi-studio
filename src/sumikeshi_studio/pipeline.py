# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""1ページの解析：向きの判定（見本との位置合わせか OCR）→ 傾きの補正 → 帳票の判別と、見本との位置合わせ →（様式不明なら）
罫線の並びで、様式のバリエーションかを調べる
"""

import dataclasses
import threading
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from . import align, formcheck, forms, imaging, ocr, orientation, variants
from .boxes import Box, Source
from .formstore import UNKNOWN, Field, FormSpec, spec_key


@dataclass(eq=False)
class PageResult:
    quarter_turns: int  # 反時計回りの90度回転の回数
    skew: float  # 傾きの補正の角度（度）
    orientation_confident: bool  # False のときは向きを画面で確かめてもらう
    # 補正後の作業画像（グレー、WORK_DPI）。作業画像を手放した結果（without_work）では None
    work: np.ndarray | None
    boxes: list[Box]
    # 判別した様式の名前（forms.Located）。どの様式にも決まらなければ formstore.UNKNOWN（様式不明）
    form: str
    # 画面で確かめてほしいこと（様式不明、類似した様式を決められない、枠の線、写真のほかの用紙など）。
    # 空なら、様式どおりに墨消しできる
    note: str = ""
    engine: ocr.Engine = ocr.Engine.RAPIDOCR  # 題名を読んだ OCR
    # 墨消しする範囲を使った様式（forms.Located。作業ログの「使った様式」に書く）
    used: tuple[FormSpec, ...] = ()
    # 印刷された枠の線が見つからない辺がある範囲の名前（forms.Located）
    unframed: tuple[str, ...] = ()
    # 枠の線を越えて書かれた記入がある範囲の名前（forms.Located）
    crossed: tuple[str, ...] = ()
    # 1枚目の用紙の部分で見本に類似した様式（点の多い順）
    similar: tuple[FormSpec, ...] = ()
    # 作業画像を手放した後も持つ、作業画像の高さ・幅（出力で範囲を元の解像度に換算するため）
    shape: tuple[int, int] | None = None
    # 位置合わせの1段目の整合した点の数（位置合わせできた用紙ごと。forms.Located）、バリエーションとして範囲を置いたか、
    # その罫線の一致度（見本の側の割合, ページの側の割合。variants。作業ログに書く）
    points: tuple[int, ...] = ()
    variant: bool = False
    line_match: tuple[float, float] | None = None
    # 様式不明のままになったページで、罫線の一致度が下限に届かなかった候補（forms.BelowLimit。作業ログに書く）
    line_below: forms.BelowLimit | None = None

    @property
    def work_shape(self) -> tuple[int, int]:
        """作業画像の高さ・幅"""
        if self.shape is not None:
            return self.shape
        h, w = self.work.shape[:2]
        return (h, w)

    def sharing(self, specs: Sequence[FormSpec]) -> PageResult:
        """used・similar の様式を、specs の中の同じ版の様式（formstore.spec_key）のオブジェクトに置き換えた結果

        解析のプロセスから受け取った結果は、ページごとに様式の写しを持つ。様式は見本の罫線を持ち、1つ0.1〜0.2MB に
        なるので、ページの多いファイルで、画面のプロセスのメモリが増えないようにする。
        """
        same = {spec_key(s): s for s in specs}

        def pick(s: FormSpec) -> FormSpec:
            return same.get(spec_key(s), s)

        return dataclasses.replace(
            self,
            used=tuple(pick(s) for s in self.used),
            similar=tuple(pick(s) for s in self.similar),
        )

    def without_work(self) -> PageResult:
        """作業画像を持たない結果（範囲・向き・傾き・様式と、作業画像の高さ・幅だけを持つ）"""
        return dataclasses.replace(self, work=None, shape=self.work_shape)


def analyze(
    page: imaging.SourcePage,
    quarter_turns: int | None = None,
    engine: ocr.Engine = ocr.Engine.RAPIDOCR,
    *,
    formset: Sequence[FormSpec],
) -> PageResult:
    """ページを解析する。quarter_turns を指定すると、向きの判定をせずにその回転を使う

    向きは、まず見本との位置合わせで決め（帳票のページ）、成り立たなければ OCR で判定する。見本と位置合わせできずに
    様式不明になったページは、罫線の並びで、様式のバリエーションかを調べ、決まればその様式の欄に範囲を置く（variants）。
    見本と位置合わせできたページも、枠の線が見つからない欄があれば、罫線の並びで範囲を置いてみて、見つからない欄が
    減るときは置き換える（_by_lines）。
    engine は題名を読む OCR。90度単位の向きの判定は、常に RapidOCR で行う。
    formset は使う様式（この PC で使わない様式を除いたもの）。空なら、どのページも様式不明になる。
    """
    gray = imaging.render(page, dpi=imaging.WORK_DPI, gray=True)
    # 回さないかもしれないページは、回さないときの傾きを、向きを決めるあいだに別のスレッドで求めておく（傾きの推定は
    # 1ページの解析の時間の半分ほどかかり、OpenCV の処理のあいだは GIL を手放すので、位置合わせと同時に進む）
    may_stay = quarter_turns is None or quarter_turns % 4 == 0
    upright_skew = _Background(imaging.estimate_skew, gray) if may_stay else None
    features = align.page_features(gray) if may_stay else None
    hint = None  # 向きを決めるときに位置合わせが成り立った様式（範囲を求めるときに先に試す）
    hint_found = None  # hint の (変換, 点)（作業画像が gray と同じときだけ、範囲を求めるときに使い回す）
    group = ()  # hint と同じ用紙の部分に成り立った様式と点（2つ以上なら題名で決める）
    no_form = False  # 向きを決めるときに、どの様式とも位置合わせが成り立たなかった
    if quarter_turns is not None:  # 画面で回転を指定した
        confident = True
    elif (found := align.upright_group(gray, formset, features)) is not None:
        # 見本と位置合わせできたページは、その変換から向きを決め、OCR で向きを判定しない
        quarter_turns, best, group = found
        hint, hint_found, confident = best[0].spec, best[1:], True
    else:
        no_form = True
        o = orientation.detect(gray)
        confident = o.confident
        # 自信がないときは回さずに、画面で確かめてもらう
        quarter_turns = o.quarter_turns if confident else 0
    turned = imaging.correct(gray, quarter_turns, 0)
    skew = (
        upright_skew.result()
        if upright_skew is not None and quarter_turns % 4 == 0
        else imaging.estimate_skew(turned)
    )
    work = imaging.correct(turned, 0, skew)  # work_image と同じ手順
    # 回さず、傾きも直さない（imaging.correct が回さない）ページは、作業画像が gray と同じ画素なので、向きを決める
    # ときの特徴点と位置合わせを使い回す
    same = quarter_turns % 4 == 0 and abs(skew) < imaging.SKEW_MIN_DEG
    if no_form:
        # 回して傾きを直した画像で全部の様式と位置合わせし直しても、特徴点（ORB）は回転によらないので、ほとんどの場合は
        # 向きを決めるときと同じく成り立たないと見込む（回した画像では見つかる特徴点が少し変わるので、成り立つかどうかの
        # 境目の様式では成り立つこともあり得る）。位置合わせし直さずに様式不明にする
        loc = forms.unknown()
    else:
        loc = forms.locate(
            work,
            engine,
            formset,
            hint,
            group,
            features if same else None,
            hint_found if same else None,
        )
    if loc.form == UNKNOWN and formset:
        # 見本と位置合わせできないページは、様式のバリエーション（行の高さ・列の幅の違う申告書）かを、罫線の並びで調べる
        variant, below = variants.judge(work, formset, features if same else None)
        if variant is not None:
            loc = forms.from_variant(variant)
        elif below is not None:
            loc = forms.below_limit(loc, below)
    else:
        loc = _by_lines(loc, work)
    boxes = [
        Box(f.x, f.y, f.w, f.h, f.name, Source.AUTO, corners=f.corners)
        for f in loc.fields
    ]
    return PageResult(
        quarter_turns=quarter_turns,
        skew=skew,
        orientation_confident=confident,
        work=work,
        boxes=boxes,
        form=loc.form,
        note=loc.note,
        engine=engine,
        used=loc.used,
        unframed=loc.unframed,
        crossed=loc.crossed,
        similar=loc.similar,
        points=loc.points,
        variant=loc.variant,
        line_match=loc.line_match,
        line_below=loc.line_below,
    )


# 枠の線がすべて見つかったページで、罫線の対応で置いた位置が、位置合わせの結果からこの画素を超えてずれていたら、
# 罫線の対応での置き直しを試す。行の高さを帯ごとに変えた試験画像で、欄が隣の行の線に合ったページは13〜65画素、
# 見本と同じ作りのスキャン・行と列を変えて正しく置けたページ（163ページ）は、154ページが6画素以内だった
LINES_SHIFT = 6.0


def _by_lines(loc: forms.Located, work: np.ndarray) -> forms.Located:
    """見本と位置合わせできたページの結果 loc を、その様式のバリエーションとして罫線の対応で置いた結果に置き換える
    （置き換えるのはページの全部の欄。置き換えないときは loc のまま）

    行の高さ・列の幅だけが見本と違う用紙は、字の形が見本と同じだと位置合わせが成り立ち、欄が枠の線からずれたまま
    残るため。試すのは、次のすべてに合うページ。
    - 位置合わせできた用紙が1枚で、用紙についての注記がない（罫線の対応は、ページ全体を1枚の表として読む。類似した
      様式を題名で決められないページは、様式が決まっていない）。注記が、様式を判別できない用紙も写っていること
      （forms.OTHER_PAPER_NOTE）だけのページは試す（行の高さ・列の幅が見本と違う用紙では、位置合わせの変換で移した
      見本の四隅がページの一部しか覆わず、残りをほかの用紙とみて、この注記が付くことがある。置き換えたときは、ページ全体を
      1枚の表として置けたので、この注記も外れる）
    - 様式が、罫線の並びを判別に使う様式で、見本の罫線を持つ
    - 用紙が歪んで写っていない（罫線の対応は、線が平行に並んだページを前提にする。歪みの判定は、ほかの条件に合う
      ページでだけ行う）

    枠の線が見つからない欄があるページは、罫線の対応で置くと見つからない欄が減るときに置き換える。線の薄いスキャンや
    白黒2値では、罫線の対応で置いても見つからない欄が減らず、見本と同じ作りの用紙の範囲を動かさずに済む。

    枠の線がすべて見つかったページも、欄が、高さの近い隣の行の線に合っていることがある（要確認が付かないまま、
    記入が墨消しの外に出る）。そこで、罫線だけを読み、見本の罫線との対応で置いた位置が、位置合わせの結果から
    LINES_SHIFT を超えてずれているページでだけ、罫線の対応で置き、線がすべて見つかって、枠の線に合わせた後の範囲も
    LINES_SHIFT を超えて違うときに置き換える。写真と判定したページでは、罫線を読まない（歪みの判定で外れるため）。
    """
    if len(loc.points) != 1 or (loc.paper_notes and not loc.other_paper_only):
        return loc
    spec = loc.used[0]
    if not variants.usable(spec):
        return loc
    if loc.unframed:
        if formcheck.distorted(work):
            return loc
        variant = variants.relocate(work, spec)
        if variant is None or len(variant.unframed) >= len(loc.unframed):
            return loc
    else:
        if loc.photo:
            return loc
        lines = variants.page_model(work)
        if (
            lines is None
            or variants.placed_shift(spec, lines, loc.fields) <= LINES_SHIFT
        ):
            return loc
        if formcheck.distorted(work):
            return loc
        variant = variants.relocate(work, spec, lines)
        if variant is None or variant.unframed:
            return loc
        if _edge_shift(variant.fields, loc.fields) <= LINES_SHIFT:
            return loc
    return dataclasses.replace(
        forms.from_variant(variant), points=loc.points, similar=loc.similar
    )


def _edge_shift(a: Sequence[Field], b: Sequence[Field]) -> float:
    """同じ順の範囲 a・b の、辺の位置の差の最大（画素）"""
    return max(
        max(
            abs(f.x - g.x),
            abs(f.y - g.y),
            abs(f.x + f.w - g.x - g.w),
            abs(f.y + f.h - g.y - g.h),
        )
        for f, g in zip(a, b, strict=True)
    )


def work_image(page: imaging.SourcePage, quarter_turns: int, skew: float) -> np.ndarray:
    """解析の結果の向きと傾きで、作業画像を元のファイルから描き直す（analyze が作る作業画像と画素まで同じ）

    墨消しの画面が、手放したページの作業画像を、表示するときに解析のプロセスに頼んで描き直す。
    """
    gray = imaging.render(page, dpi=imaging.WORK_DPI, gray=True)
    return imaging.correct(imaging.correct(gray, quarter_turns, 0), 0, skew)


class _Background:
    """関数を別のスレッドで呼び、result で結果を返す（例外は result で出す）。結果を使わないときは待たない"""

    def __init__(self, fn, *args):
        self._value = None
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, args=(fn, args), daemon=True)
        self._thread.start()

    def _run(self, fn, args):
        try:
            self._value = fn(*args)
        except BaseException as e:  # noqa: BLE001  result で呼んだ側に出す
            self._error = e

    def result(self):
        self._thread.join()
        if self._error is not None:
            raise self._error
        return self._value
