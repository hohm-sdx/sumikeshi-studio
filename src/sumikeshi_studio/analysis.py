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
import sys
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
    # ほかの依頼と同時に動かさない（解析のプロセスが途中で終わったときに、このページが原因かを確かめるため）
    alone: bool = False


# Windows の例外で解析のプロセスを終わらせるときの終了の番号
NATIVE_EXCEPTION_EXIT = 3


def call_in(language: str, fn, *args):
    """画面の言語を language にしてから fn(*args) を呼ぶ

    解析のプロセスで作る文（ページの注記、様式を保存する前の確認の文など）を、画面のプロセスと同じ言語にするため、
    workers.Analyzer は解析のプロセスへの依頼をすべてこれで包み、依頼を出す時の言語を渡す。
    fn が、Windows の例外から作られた OSError（native_exception）を出したら、異常終了の記録に書いて、このプロセスを
    終わらせる（呼んだ側には、解析のプロセスが途中で終わったことが届き、ほかの異常終了と同じに扱う）。
    """
    from . import i18n

    i18n.set_language(language)
    try:
        return fn(*args)
    except OSError as e:
        if native_exception(e):
            _end_by_native_exception(e)
        raise


def native_exception(e: BaseException) -> bool:
    """e が、Windows の ctypes が、C の関数の中で起きた例外（C++ の例外 0xE06D7363・アクセス違反など）を受け止めて作った
    OSError か

    ctypes は、決まった種類（アクセス違反など）を「exception: …」の文の OSError（winerror なし）に、それ以外を、例外の
    番号を winerror にした OSError（番号は負の数になる）にする。ふつうの OSError（ファイルを読めない、アクセスが拒否された
    など）は、winerror が正の数か None で、文が「exception: 」で始まらない。
    """
    if not isinstance(e, OSError):
        return False
    winerror = getattr(e, "winerror", None)
    if winerror is not None:
        return winerror < 0
    return str(e).startswith("exception: ")


def _end_by_native_exception(e: OSError) -> None:
    """Windows の例外を受けた解析のプロセスを終わらせる。例外が PDFium などの C の関数の中を戻るとき、途中で取った
    メモリや状態は後始末されずに残り、同じプロセスでの後の処理が、メモリの上限に届いて失敗することがあるので、同じ
    プロセスで続けない"""
    from . import crashlog

    crashlog.write_exception(
        "Windows exception in a native call (ending the analysis process)",
        type(e),
        e,
        e.__traceback__,
    )
    crashlog.flush()
    os._exit(NATIVE_EXCEPTION_EXIT)


def configure(threads: int) -> None:
    """解析のプロセスを立ち上げるときの設定：異常終了の記録、確保できるメモリの上限（Windows のジョブ）と、OCR の
    スレッドの数（解析のプロセスが複数のときは分ける）"""
    from . import crashlog, ocr, winjob

    crashlog.setup(
        crashlog.ROLE_ANALYSIS
    )  # pythonw.exe から起動するので、エラーの出力がない
    # 確保できるメモリを限る（Windows）。細工した PDF で PDFium が部品を展開し続けても、PC 全体の空きを使い切らない
    if (reason := winjob.limit_memory()) and sys.platform == "win32":
        crashlog.write(f"memory limit: {reason}")
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
