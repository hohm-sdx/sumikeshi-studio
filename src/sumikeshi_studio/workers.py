# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""裏で動かす処理：起動の準備、ページの解析、ファイルを開く処理、ファイルの出力、様式のフォルダの読み込み

確認画面を先に出すため、重いライブラリを読み込むモジュール（imaging・export など）は、
このモジュールを読み込んだときではなく、裏で動く処理の中で読み込む。

ページの解析は、別のプロセス（解析のプロセス）で行う。同じプロセスのスレッドで解析すると、画面が止まることが
あるため（PDF のライブラリが PyMuPDF だったときに測ると、ページを画像にするあいだ GIL を持ったままになり、画面が
1ページにつき約0.15〜0.25秒止まった）。CPU とメモリに余裕があれば、解析のプロセスを2つにして、2ページを同時に
解析する。
"""

import dataclasses
import multiprocessing
import os
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable
from concurrent.futures import CancelledError, Executor, Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal

from . import analysis, folders, formstore, i18n, nativecheck, ocr, review
from .analysis import AnalysisJob
from .i18n import tr
from .review import Document

CRASHED = "解析のプロセスが途中で終了しました。"
NOT_STARTED = "解析のプロセスを起動できませんでした。"
# 解析のプロセスを2つにする条件。1つ増やすとメモリが約500MB増えるため、
# 空きメモリの少ない PC では1つにする。8コアの環境では、20ページの解析が約3割短くなった
TWO_PROCESSES_MIN_CPUS = 4
TWO_PROCESSES_MIN_AVAILABLE = 4 * 1024**3  # 起動したときの空きメモリ（バイト）
# 解析のプロセスの数を指定する環境変数（Windows で1つと2つを比べるためのもの）
ENV_PROCESSES = "SUMIKESHI_STUDIO_ANALYSIS_PROCESSES"


def process_count() -> int:
    """解析のプロセスの数：論理 CPU が4つ以上で、空きメモリが4GB以上なら2つ、そうでなければ1つ"""
    if value := os.environ.get(ENV_PROCESSES, "").strip():
        try:
            return max(1, int(value))
        except ValueError:
            pass
    import psutil

    cpus = os.cpu_count() or 1
    available = psutil.virtual_memory().available
    if cpus >= TWO_PROCESSES_MIN_CPUS and available >= TWO_PROCESSES_MIN_AVAILABLE:
        return 2
    return 1


def new_executor(processes: int) -> Executor:
    """解析のプロセスを processes 個持つ executor を作る（テストでは、スレッドで動くものに置き換える）

    OCR のスレッドの数（CPU の4分の3）は、解析のプロセスで分ける（8コアで2つなら3ずつ）。
    """
    return ProcessPoolExecutor(
        processes,
        mp_context=multiprocessing.get_context("spawn"),
        initializer=analysis.configure,
        initargs=(max(2, ocr.THREADS // processes),),
    )


class Analyzer(QObject):
    """ページの解析を、解析のプロセスに頼む（解析のプロセスの数だけ、同時に頼む）

    依頼は画面のプロセスの列に置き、解析のプロセスには空いた数だけ出す。まとめて出すと、回したページや
    開き直したファイルの解析が、先に出したすべての依頼の後になるため。新しい依頼は列の先頭に入れる。
    """

    page_done = Signal(object, int, object, str)  # 番号（token）, ページ, 結果, エラー
    # 解析が終わった依頼（executor の裏のスレッドから、画面のスレッドに渡す）
    _finished = Signal(
        object, object, object, object
    )  # 番号, AnalysisJob, Future, executor
    _called = Signal(object, object)  # call の callback, Future

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.executor: Executor | None = None
        self.processes = 1  # 解析のプロセスの数（start で決める）
        self.queue: deque[tuple[object, AnalysisJob]] = deque()
        self.running = 0  # 解析のプロセスに出して、まだ終わっていない依頼の数
        self.calling = 0  # call で出して、まだ callback を呼んでいない処理の数
        # 解析のプロセスを1つに減らす途中（limit_to_one）。出した依頼と処理が終わるまで、新しい依頼を出さない
        self.shrinking = False
        # 依頼がすぐに終わったとき（出した処理の中で終わりが知らされる）も、出した処理が終わってから受け取る
        self._finished.connect(self._done, Qt.ConnectionType.QueuedConnection)
        self._called.connect(self._call_done, Qt.ConnectionType.QueuedConnection)

    def start(self) -> Future:
        """解析のプロセスを立ち上げ、準備（OCR と型）を頼む

        準備は解析のプロセスの数だけ頼み、最初に頼んだものの Future（終わると pid が入る）を返す。
        """
        self.processes = process_count()
        self.executor = new_executor(self.processes)
        ready = [self._submit(analysis.prepare) for _ in range(self.processes)]
        return ready[0]

    def submit(self, token, jobs: list[AnalysisJob]):
        """依頼を列の先頭に入れる（回したページを、待っているページより先に解析する）"""
        self.queue.extendleft((token, job) for job in reversed(jobs))
        self._pump()

    def clear(self):
        """待っている依頼を取り消す。解析中の依頼の結果は届く（呼ぶ側が番号で見分けて捨てる）"""
        self.queue.clear()

    def prioritize(self, token, index: int):
        """表示したページ（index）とそれより後のページを、待っているほかの依頼より先に解析する

        先のページへ飛んだときに、解析が順に届くまで待たないようにする（順に待つと、100ページ中80ページ目で
        約127秒かかった）。そこから順にめくっても待たないよう、後のページもまとめて前に出す。
        それぞれの中では今の順を保つ。そのページを待っていなければ（解析中・解析済み）何もしない。
        """
        ahead = [(t, job) for t, job in self.queue if t == token and job.index >= index]
        if not any(job.index == index for _, job in ahead):
            return
        rest = [
            (t, job) for t, job in self.queue if not (t == token and job.index >= index)
        ]
        self.queue = deque(ahead + rest)

    def limit_to_one(self):
        """メモリが少ないとき：解析のプロセスを1つにする。出した依頼と処理（解析中のページ・描き直しなど）が終わるのを
        待ってから、今の解析のプロセスを終えて、1つで立ち上げ直す（そのあいだは新しい依頼を出さない）。プロセスの数は
        戻さない"""
        if self.processes <= 1 or self.executor is None:
            return
        self.processes = 1
        self.shrinking = True
        self._shrink()

    def _shrink(self):
        if not self.shrinking or self.running or self.calling or self.executor is None:
            return
        self.executor.shutdown(wait=False)
        self.executor = new_executor(1)
        self._submit(analysis.prepare)  # 準備（OCR）は、次の依頼より先に動く
        self.shrinking = False

    def shutdown(self):
        """待っている依頼を取り消し、解析中のページが終わったら解析のプロセスを終える"""
        self.queue.clear()
        if self.executor is not None:
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.executor = None

    def _pump(self):
        self._shrink()
        while (
            not self.shrinking
            and self.executor is not None
            and self.queue
            and self.running < self.processes
        ):
            token, job = self.queue.popleft()
            call = (analysis.analyze, job)
            try:
                future = self._submit(*call)
            except (
                BrokenProcessPool
            ):  # 待っているあいだに、解析のプロセスが終わっていた
                self._restart()
                future = self._submit(*call)
            self.running += 1
            executor = self.executor
            future.add_done_callback(
                lambda f, t=token, j=job, e=executor: self._finished.emit(t, j, f, e)
            )

    def call(self, fn: Callable, *args, callback: Callable) -> None:
        """解析のプロセスで fn(*args) を動かし、終わったら画面のスレッドで callback(結果, 例外) を呼ぶ

        様式の登録・編集の画面で、保存の前に確かめる処理や、題名を読む処理に使う（重い処理で画面を止めないため）。
        解析の依頼の列には入れず、すぐに出す（解析中のページが終わった後に動く）。
        """
        if self.executor is None:
            callback(None, RuntimeError(tr(NOT_STARTED)))
            return
        try:
            future = self._submit(fn, *args)
        except BrokenProcessPool:  # 待っているあいだに、解析のプロセスが終わっていた
            self._restart()
            future = self._submit(fn, *args)
        self.calling += 1
        future.add_done_callback(lambda f: self._called.emit(callback, f))

    def _call_done(self, callback: Callable, future: Future):
        self.calling -= 1
        if self.shrinking:
            self._pump()  # 1つに減らす途中なら、出した処理が終わったところで立ち上げ直して、待っている依頼を出す
        try:
            result, error = future.result(), None
        except BrokenProcessPool:
            result, error = None, RuntimeError(tr(CRASHED))
        except CancelledError:  # 閉じるときに取り消した
            return
        except Exception as e:  # noqa: BLE001  どんな失敗でも呼んだ側に知らせる
            traceback.print_exception(e)
            result, error = None, e
        callback(result, error)

    def _submit(self, fn: Callable, *args) -> Future:
        """解析のプロセスで fn(*args) を動かす。今の画面の言語を一緒に渡す（解析のプロセスで作る文を、同じ言語に
        するため）"""
        return self.executor.submit(analysis.call_in, i18n.language(), fn, *args)

    def _restart(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.executor = new_executor(self.processes)

    def _done(self, token, job: AnalysisJob, future: Future, executor: Executor):
        self.running -= 1
        if future.cancelled():  # 閉じるときに取り消した
            return
        result, error = None, ""
        try:
            result = future.result()
        except BrokenProcessPool:
            # 解析のプロセスが途中で終わった。作り直すのは、終わった組ごとに1回だけ
            if self.executor is not None and executor is self.executor:
                self._restart()
            # 解析のプロセスが2つだと、どのページが原因か分からないため、解析中だったページを
            # 1回だけやり直す。やり直しても終われば「処理できません」とする
            if not job.retried and self.executor is not None:
                self.queue.appendleft((token, dataclasses.replace(job, retried=True)))
                self._pump()
                return
            error = tr(CRASHED)
        except formstore.FormError as e:
            # 様式の型の画像を読めない（様式のフォルダにつながらないなど）。
            # 型の画像を読めないページは、ほかの様式だけで続けずに「処理できません」とする（その様式の欄を
            # 墨消ししないまま進めないため）
            error = str(e)
        except Exception as e:  # noqa: BLE001
            traceback.print_exception(e)  # 解析のプロセスでの経過も含まれる
            error = f"{type(e).__name__}: {e}"
        self.page_done.emit(token, job.index, result, error)
        self._pump()


class Background(QObject):
    """様式のフォルダの読み書きなど、短いが待たされることのある処理を裏のスレッドで行う

    共有フォルダにつながらないと、読み書きに数十秒かかることがあるため。画面を閉じるときに終わるのを
    待たないよう、デーモンのスレッドで行う。終わったら、画面のスレッドで callback(結果, 例外) を呼ぶ。
    """

    _done = Signal(object, object, object)  # callback, 結果, 例外

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.running = 0  # 途中の処理の数（様式を保存している途中は、ツールを終えない）
        self._done.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def run(self, fn: Callable, *args, callback: Callable) -> None:
        self.running += 1

        def work():
            try:
                result, error = fn(*args), None
            except Exception as e:  # noqa: BLE001  どんな失敗でも呼んだ側に知らせる
                traceback.print_exc()
                result, error = None, e
            try:
                self._done.emit(callback, result, error)
            except RuntimeError:  # 終わる前に画面を閉じ、受け取る部品がなくなった
                pass

        threading.Thread(target=work, daemon=True).start()

    def _deliver(self, callback: Callable, result, error):
        self.running -= 1
        callback(result, error)


def load_forms(choice: formstore.FolderChoice, off: frozenset[str]) -> formstore.Loaded:
    """様式のフォルダの様式を読む。どんな失敗でも、理由を Loaded.error に入れて返す（呼んだ側は blocking_text を見て、
    ファイルを開かない）。off は、この PC で使わない様式の識別子"""
    try:
        return formstore.load(choice, off)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return formstore.Loaded(
            choice.path,
            choice.origin,
            error=tr(
                "様式を読めないため、ファイルを開けません。\n{error}",
                error=f"{type(e).__name__}: {e}",
            ),
            off=off,
        )


class FormsLoader(QObject):
    """様式のフォルダを裏のスレッドで読む

    共有フォルダにつながらないと、フォルダの一覧を読むのに数十秒かかることがあり、画面のスレッドで読むと
    そのあいだ画面が止まるため。画面を閉じるときに読み終わるのを待たないよう、デーモンのスレッドで読む。
    """

    done = Signal(
        object, object
    )  # 読んだ様式のフォルダの決め方（FolderChoice）, Loaded

    def start(self, choice: formstore.FolderChoice, off: frozenset[str]) -> None:
        """様式を読み始める。off は、この端末で使わない様式の識別子"""
        threading.Thread(target=self._run, args=(choice, off), daemon=True).start()

    def _run(self, choice: formstore.FolderChoice, off: frozenset[str]) -> None:
        loaded = load_forms(choice, off)
        try:
            self.done.emit(choice, loaded)
        except RuntimeError:  # 読み終わる前に画面を閉じ、受け取る部品がなくなった
            pass


class FileOpener(QObject):
    """選ばれたファイルとフォルダを裏のスレッドで開く（フォルダをたどり、ファイルを開いてページを数える）

    サブフォルダまで開くとファイルが増え、共有フォルダやクラウドストレージのファイルは開くのに時間がかかることが
    あり、画面のスレッドで開くとそのあいだ画面が止まるため。取り消せる。
    """

    # 進み具合：（数, 全体の数）。全体の数が負なら、フォルダをたどっている途中で、数はそれまでに見つけたファイルの数
    progress = Signal(int, int)
    done = Signal(object)  # review.Opened。取り消したら None
    # 進み具合を知らせる間隔（秒）。ファイルが多いとき、1件ごとに画面に知らせないため
    PROGRESS_SECONDS = 0.05

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._cancel = threading.Event()
        self._told = 0.0

    def start(
        self,
        selection: folders.Selection,
        depth: int | None,
        engine: ocr.Engine,
        engine_label: str,
    ) -> None:
        threading.Thread(
            target=self._run,
            args=(selection, depth, engine, engine_label),
            daemon=True,
        ).start()

    def cancel(self) -> None:
        self._cancel.set()

    def _tell(self, count: int, total: int, last: bool = False) -> None:
        now = time.monotonic()
        if last or now - self._told >= self.PROGRESS_SECONDS:
            self._told = now
            self.progress.emit(count, total)

    def _run(self, selection, depth, engine, engine_label) -> None:
        cancelled = self._cancel.is_set
        try:
            opened = None
            if selection.walk_to(depth, cancelled, lambda n: self._tell(n, -1)):
                inputs = selection.inputs(depth)
                opened = review.open_inputs(
                    inputs,
                    engine,
                    engine_label,
                    lambda n, total: self._tell(n, total, n == total),
                    cancelled,
                )
        # どんな失敗でも、開けなかった理由として画面に知らせる（知らせないと、画面が開く途中のまま止まる）
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            opened = review.Opened(failed=[f"{type(e).__name__}: {e}"])
        try:
            self.done.emit(opened)
        except RuntimeError:  # 開き終わる前に画面を閉じ、受け取る部品がなくなった
            pass


class Preloader(QThread):
    """起動の準備：解析のプロセスの立ち上げ（OCR と型の準備）と、画面のプロセスで使う重いライブラリの読み込み"""

    done = Signal(str)  # 失敗したときはエラーの文、成功したときは空

    def __init__(self, analyzer: Analyzer):
        super().__init__()
        self.analyzer = analyzer

    def run(self):
        try:
            # 解析のプロセスの準備を先に始め、そのあいだに画面のプロセスの読み込みを行う
            ready = self.analyzer.start()
            from . import export, imaging  # noqa: F401  開く・出力に使う。読み込むこと自体が準備

            ready.result()
            self.done.emit("")
        except CancelledError:  # 準備の途中で画面を閉じた
            return
        except BrokenProcessPool:
            traceback.print_exc()
            self.done.emit(tr(NOT_STARTED))
        # どんな失敗でも画面に知らせる。スマート アプリ コントロールなどに部品を止められたときは、何が起きたか分かる文にする
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            error = f"{type(e).__name__}: {e}"
            blocked = nativecheck.policy_message(error)
            self.done.emit(
                tr("{blocked}\n（{error}）", blocked=blocked, error=error)
                if blocked
                else error
            )


class Exporter(QThread):
    """ファイルを順に出力する。1ファイルの失敗で残りを止めない

    ページを墨消しする処理は、解析のプロセスに頼んで並べて行う。
    executor_of は、そのときの解析のプロセスの executor を返す関数（なければこのスレッドで行う）。
    """

    progress = Signal(int, int, str)
    file_done = Signal(
        object, object, object, str
    )  # Document, 出力先, ExportReport or None, エラー
    all_done = Signal()

    def __init__(
        self,
        jobs: list[tuple[Document, Path]],
        executor_of: Callable[[], Executor | None] = lambda: None,
    ):
        super().__init__()
        self.jobs = [
            (
                doc,
                out,
                [(st.source, st.result) for st in doc.kept],
            )  # 削除したページは除く
            for doc, out in jobs
        ]
        self.executor_of = executor_of

    def run(self):
        from . import export

        total = sum(len(pages) for _, _, pages in self.jobs)
        done = 0
        for k, (doc, out, pages) in enumerate(self.jobs):
            prefix = tr(
                "{index}／{total}ファイル目（{name}）\n",
                index=k + 1,
                total=len(self.jobs),
                name=doc.label,
            )

            def progress(d, _n, message, base=done, prefix=prefix):
                self.progress.emit(base + d, total, prefix + message)

            executor = self.executor_of()
            mask_pages = (
                (lambda jobs, ex=executor: ex.map(analysis.mask_page, jobs))
                if executor is not None
                else None
            )

            def unchanged(doc=doc):
                """開いた後に入力ファイルが変わっていれば、出力しない"""
                export.check_unchanged(doc.path, doc.stamp)

            try:
                unchanged()  # 墨消しを始める前
                # 正式な名前にする直前にも照合する（出力の途中で変わったとき）
                report = export.export(
                    pages,
                    out,
                    progress,
                    mask_pages=mask_pages,
                    before_replace=unchanged,
                )
                self.file_done.emit(doc, out, report, "")
            except export.ExportError as e:  # 入力ファイルが変わった、置き換えられない
                self.file_done.emit(doc, out, None, str(e))
            except BrokenProcessPool:
                # 出力の途中で解析のプロセスが終わった。このファイルは出力できなかったとし、
                # 残りのファイルはこのスレッドで1ページずつ出力する
                traceback.print_exc()
                self.executor_of = lambda: None
                self.file_done.emit(doc, out, None, tr(CRASHED))
            # どんな失敗でも画面に知らせる
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                self.file_done.emit(doc, out, None, f"{type(e).__name__}: {e}")
            done += len(pages)
        self.all_done.emit()
