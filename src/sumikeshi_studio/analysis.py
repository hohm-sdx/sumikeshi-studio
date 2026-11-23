# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""解析のプロセスで動かす関数と、解析の依頼

墨消しの画面は、ページの解析を別のプロセス（解析のプロセス）に頼む（workers.Analyzer）。
画面のプロセスが重いライブラリを読み込まずに関数を指定できるよう、このモジュールは上部で
標準ライブラリだけを読み込む。重いモジュールは、解析のプロセスで関数が呼ばれたときに読み込む。
上部で標準ライブラリだけを読み込むことは、tests/test_structure.py が確かめる。

解析のプロセスで動く関数は、このモジュールの configure・prepare・analyze・mask_page・redraw・build_form・
move_ranges・line_match_default・check_page・read_title だけ（出力の墨消しの export.mask_page も、ここの mask_page を通す）。依頼は call_in で包み、
画面のプロセスの言語にしてから動かす（workers.Analyzer）。
"""

import os
import signal
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

    from . import export, formcheck, imaging, ocr, pipeline
    from .formstore import Field, FormSpec


@dataclass(frozen=True)
class AnalysisJob:
    """1ページの解析の依頼（解析のプロセスに pickle して渡す）"""

    index: int  # 全ファイルを通したページの番号
    source: imaging.SourcePage | imaging.MemoryPage
    quarter_turns: int | None  # 画面で指定した回転。None なら自動で判定する
    engine: ocr.Engine
    # 使う様式（この PC で使わない様式を除いたもの）。1回の「開く」の中では、同じ組で解析する
    formset: tuple[FormSpec, ...]
    retried: bool = False  # 解析のプロセスが途中で終わったため、やり直している


def call_in(language: str, fn, *args):
    """画面の言語を language にしてから fn(*args) を呼ぶ

    解析のプロセスで作る文（ページの注記、様式を保存する前の確認の文など）を、画面のプロセスと同じ言語にするため、
    workers.Analyzer は解析のプロセスへの依頼をすべてこれで包み、依頼を出す時の言語を渡す。
    """
    from . import i18n

    i18n.set_language(language)
    return fn(*args)


def configure(threads: int) -> None:
    """解析のプロセスを立ち上げるときの設定：異常終了の記録と、OCR のスレッドの数（解析のプロセスが複数の
    ときは分ける）"""
    from . import crashlog, ocr

    crashlog.setup(
        crashlog.ROLE_ANALYSIS
    )  # pythonw.exe から起動するので、エラーの出力がない
    # コマンドラインから出力するとき、コンソールの Ctrl+C は同じコンソールの解析のプロセスにも届く。止めるかどうかは
    # 呼んだプロセスが決める（解析のプロセスが先に終わると、解析中のページが「途中で終了しました」になるため）
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    ocr.THREADS = threads


def prepare() -> int:
    """解析の準備：OCR を用意する。解析のプロセスの番号（pid）を返す

    様式の見本は、その様式を使う依頼を受けたときに読む（align.templates）。
    """
    from . import ocr

    ocr._engine()
    return os.getpid()


def analyze(job: AnalysisJob) -> pipeline.PageResult:
    """1ページを解析する（pipeline.analyze をそのつど引くので、テストで置き換えたものが使われる）"""
    from . import pipeline

    return pipeline.analyze(
        job.source, job.quarter_turns, job.engine, formset=job.formset
    )


def mask_page(job: export.PageJob) -> export.PageImage:
    """出力のとき、1ページを墨消しして JPEG にする（export.export に、並べて行う関数として渡す）"""
    from . import export

    return export.mask_page(job)


def redraw(page: imaging.SourcePage, quarter_turns: int, skew: float) -> np.ndarray:
    """手放したページの作業画像を、元のファイルから描き直す（墨消しの画面が表示するときに頼む）"""
    from . import pipeline

    return pipeline.work_image(page, quarter_turns, skew)


# 様式の登録・編集の画面から頼む処理


def build_form(
    base: np.ndarray,
    base_is_work: bool,
    rects: list[tuple],
    others: list[FormSpec],
    spec: FormSpec | None = None,
    engine: ocr.Engine | None = None,
    lines: tuple | None = None,
) -> tuple[bytes, formcheck.TemplateCheck]:
    """見本の画像を作り（rects の中を消す）、PNG のデータと、保存の前に確かめた結果を返す。spec は保存しようとしている
    様式で、engine は題名を読む OCR（類似した様式と題名で見分けられるかを確かめる）。base_is_work（登録と新しい用紙への
    置き換え）なら、塗る前の base から見本の罫線を読み、結果の lines に入れ、spec の墨消しする範囲のうち記入がある
    とみた範囲の名前を、結果の written に入れる。lines は、同じ base から先に読んだ罫線（line_match_default の結果）で、
    渡すと読み直さない"""
    from dataclasses import replace

    import cv2

    from . import formcheck, ocr, rulings

    small = formcheck.make_template(base, base_is_work, rects)
    ok, buf = cv2.imencode(".png", small)
    if not ok:
        from .i18n import tr

        raise ValueError(tr("見本の画像を PNG にできませんでした"))
    check = formcheck.check_template(
        small, base, base_is_work, others, spec, engine or ocr.Engine.RAPIDOCR
    )
    if base_is_work:
        written = formcheck.written_ranges(base, spec.fields) if spec else ()
        if lines is None:
            lines = rulings.read(base)
        check = replace(check, lines=lines, written=written)
    return buf.tobytes(), check


def move_ranges(
    work: np.ndarray, spec: FormSpec
) -> tuple[list[Field], list[Field]] | None:
    from . import formcheck

    return formcheck.move_ranges(work, spec)


def line_match_default(
    work: np.ndarray, others: list[FormSpec]
) -> tuple[tuple[bool, float], tuple]:
    """新しく登録する様式の、(罫線の並びを判別に使うか, 罫線の一致度の下限) の初めの値と、そのために work から読んだ
    罫線（保存のときに build_form に渡す）。work は登録するページの作業画像、others は使うほかの様式
    （variants.default_limit）"""
    from . import rulings, variants

    lines = rulings.read(work)
    return variants.default_limit(lines, others), lines


def check_page(work: np.ndarray, fields: list[Field]) -> formcheck.PageCheck:
    """登録と置き換えに使うページを、範囲を描く画面を開く前に確かめる（歪みと、初めの範囲の記入）"""
    from . import formcheck

    return formcheck.check_page(work, fields)


def read_title(gray: np.ndarray, engine: ocr.Engine) -> str:
    from . import titles

    return titles.read_title(gray, engine)
