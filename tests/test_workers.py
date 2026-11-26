# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""ページの解析を解析のプロセスに頼む仕組み（workers.Analyzer）のテスト（表示なしで動かす）

依頼の順と取り消しは、解析の代わりの関数をスレッドで動かして確かめる。結果の受け渡し・エラーの届き方・
解析のプロセスが終わったときの続け方は、本物の解析のプロセスで確かめる。
"""

import dataclasses
import os
import pickle
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import nativecrash
import numpy as np
import pytest
from conftest import BreakingPool, cached_analyze
from userforms import bundled_forms, write_form

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip(
    "PySide6.QtWidgets", reason="画面の部品を読み込めない環境", exc_type=ImportError
)

from sumikeshi_studio import (
    analysis,
    crashlog,
    export,
    formstore,
    i18n,
    imaging,
    ocr,
    pipeline,
    workers,
)
from sumikeshi_studio.analysis import AnalysisJob

FIELDS = 10  # 扶養控除等申告書 令和9年分の型にある個人番号欄の数


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def wait_for(qapp, cond, sec=180):
    end = time.time() + sec
    while not cond():
        qapp.processEvents()
        time.sleep(0.01)
        assert time.time() < end, "時間内に終わらなかった"


def job(index, source=None, turns=None, engine=ocr.Engine.RAPIDOCR, formset=None):
    """解析の依頼（formset を省くと、配布用パッケージの3様式）"""
    formset = bundled_forms() if formset is None else formset
    return AnalysisJob(index, source or f"p{index}", turns, engine, formset)


def fake_analyzer(monkeypatch, processes, pool=ThreadPoolExecutor):
    """解析の代わりの関数をスレッドで動かす Analyzer（解析のプロセスの数は processes。executor は pool で作る）

    代わりの関数は、受け取ったページ（source）を calls に記録し、gate が開くまで終わらない。
    ページが "crash" なら毎回、"crash_once" なら1回目だけ、解析のプロセスが途中で終わったときと同じ
    BrokenProcessPool を出す。"crash_now" は、gate を待たずに毎回出す。届いた (番号, ページ, 結果, エラー) は
    received に入る。
    """
    monkeypatch.setenv(workers.ENV_PROCESSES, str(processes))
    monkeypatch.setattr(workers, "new_executor", pool)
    monkeypatch.setattr(analysis, "prepare", os.getpid)
    calls, gate = [], threading.Event()

    def analyze(source, quarter_turns, engine, formset=None):
        calls.append(source)
        if source == "crash_now":
            raise BrokenProcessPool("解析のプロセスが終わった")
        gate.wait(30)
        if source == "crash" or (source == "crash_once" and calls.count(source) == 1):
            raise BrokenProcessPool("解析のプロセスが終わった")
        if source == "broken":
            raise ValueError("壊れたページ")
        if source == "no_template":
            raise formstore.FormError("様式「住宅」の見本の画像を読めません。（パス）")
        return f"{source} の結果"

    monkeypatch.setattr(pipeline, "analyze", analyze)
    a = workers.Analyzer()
    a.start().result()
    # 解析のプロセスの数だけ頼んだ準備がすべて終わるのを待つ（準備も、途中で終わったときに同時に動いていた処理に数える）
    end = time.time() + 30
    while a.live:
        time.sleep(0.01)
        assert time.time() < end, "準備が終わらなかった"
    received = []
    a.page_done.connect(lambda *args: received.append(args))
    return a, calls, gate, received


@pytest.fixture
def fake(qapp, monkeypatch):
    """解析のプロセスが1つの、代わりの Analyzer（fake_analyzer）"""
    a, calls, gate, received = fake_analyzer(monkeypatch, 1)
    yield a, calls, gate, received
    gate.set()
    a.shutdown()


@pytest.fixture
def fake2(qapp, monkeypatch):
    """解析のプロセスが2つの、代わりの Analyzer（fake_analyzer）"""
    a, calls, gate, received = fake_analyzer(monkeypatch, 2)
    yield a, calls, gate, received
    gate.set()
    a.shutdown()


def test_preloader_tells_part_blocked_by_application_control(qapp):
    """起動の準備で、スマート アプリ コントロールなどに部品を止められたら、何が起きたか分かる文と元の例外の文を示す"""

    class Blocked:
        def start(self):
            raise ImportError(
                "DLL load failed while importing cv2: "
                "アプリケーション制御ポリシーによってこのファイルがブロックされました。"
            )

    got = []
    pre = workers.Preloader(Blocked())
    pre.done.connect(got.append)
    pre.run()  # 裏のスレッドを立てずに、このスレッドで動かす
    first, second = got[0].split("\n")
    assert first.startswith(
        "Windows のスマート アプリ コントロール（または会社のアプリケーション制御）が、ツールの部品（cv2）を止めました。"
    )
    assert second == (
        "（ImportError: DLL load failed while importing cv2: "
        "アプリケーション制御ポリシーによってこのファイルがブロックされました。）"
    )
    # 英語の画面では、画面に出す文も英語にする（元の例外の文は、Windows の言語のまま）
    i18n.set_language(i18n.EN)
    got.clear()
    pre.run()
    first, second = got[0].split("\n")
    assert first == (
        "Windows Smart App Control (or your organization's application control) blocked a component of the tool "
        "(cv2). Extract the ZIP of the package and run install.bat to see the names of the blocked components. "
        "A screenshot of this window may help investigate the cause."
    )
    assert second.startswith("(ImportError: DLL load failed while importing cv2: ")


def test_process_count(monkeypatch):
    """論理 CPU が4つ以上で、空きメモリが2GB以上なら2つ（2GB ちょうどで2つ、それより1バイトでも少なければ1つ）。
    環境変数で指定もできる"""
    import psutil

    monkeypatch.delenv(workers.ENV_PROCESSES, raising=False)
    memory = psutil.virtual_memory()
    for cpus, available, want in [(8, 2 << 30, 2), (8, (2 << 30) - 1, 1),
                                  (4, 2 << 30, 2), (3, 8 << 30, 1),
                                  (8, 1 << 30, 1), (8, 4 << 30, 2)]:  # fmt: skip
        monkeypatch.setattr(os, "cpu_count", lambda c=cpus: c)
        monkeypatch.setattr(
            psutil, "virtual_memory", lambda a=available: memory._replace(available=a)
        )
        assert workers.process_count() == want
    monkeypatch.setenv(workers.ENV_PROCESSES, "1")
    assert workers.process_count() == 1


def test_new_jobs_go_before_waiting_jobs(fake, qapp):
    """解析中に出した依頼（回したページ）は、待っている依頼より先に解析する"""
    a, calls, gate, received = fake
    a.submit(1, [job(0), job(1), job(2)])
    wait_for(qapp, lambda: calls == ["p0"])
    assert len(a.queue) == 2  # 解析のプロセスには1件ずつ出し、残りは画面の側で待たせる
    a.submit(1, [job(9, turns=1)])
    gate.set()
    wait_for(qapp, lambda: len(received) == 4)
    assert calls == ["p0", "p9", "p1", "p2"]
    assert received[1] == (1, 9, "p9 の結果", "")


def test_clear_drops_waiting_jobs(fake, qapp):
    """開き直したときは待っている依頼を取り消す。解析中だった1件は、前の番号のまま届く"""
    a, calls, gate, received = fake
    a.submit(1, [job(0), job(1), job(2)])
    wait_for(qapp, lambda: calls == ["p0"])
    a.clear()
    a.submit(2, [job(5)])
    gate.set()
    wait_for(qapp, lambda: len(received) == 2)
    assert calls == ["p0", "p5"]
    assert [(token, index) for token, index, _, _ in received] == [(1, 0), (2, 5)]


def test_shown_page_and_following_pages_go_first(fake, qapp):
    """まだ解析していないページを表示したら、そのページとそれより後のページを先に解析する"""
    a, calls, gate, received = fake
    a.submit(1, [job(i) for i in range(10)])
    wait_for(qapp, lambda: calls == ["p0"])
    a.prioritize(1, 7)
    a.prioritize(1, 0)  # 解析中のページ：何もしない
    a.prioritize(2, 3)  # 別の番号（前に開いていたファイル）：何もしない
    gate.set()
    wait_for(qapp, lambda: len(received) == 10)
    assert calls == ["p0", "p7", "p8", "p9", "p1", "p2", "p3", "p4", "p5", "p6"]


def test_two_pages_are_analyzed_at_once(fake2, qapp):
    """解析のプロセスが2つなら、2ページを同時に頼み、残りは画面の側で待たせる"""
    a, calls, gate, received = fake2
    a.submit(1, [job(0), job(1), job(2), job(3)])
    wait_for(qapp, lambda: sorted(calls) == ["p0", "p1"])
    assert len(a.queue) == 2
    gate.set()
    wait_for(qapp, lambda: len(received) == 4)
    assert sorted(index for _, index, _, _ in received) == [0, 1, 2, 3]


def test_limit_to_one_restarts_with_one_process_after_running_work(fake2, qapp):
    """メモリが少ないときは、解析中のページと出した処理が終わるのを待ってから、解析のプロセスを1つで立ち上げ直す。
    そのあいだは新しい依頼を出さず、立ち上げ直した後は1ページずつ解析する。依頼は落とさない"""
    a, calls, gate, received = fake2
    a.submit(1, [job(i) for i in range(5)])
    wait_for(qapp, lambda: sorted(calls) == ["p0", "p1"])
    old = a.executor
    drawn = []
    a.call(
        lambda: (gate.wait(30), "描き直し")[1], callback=lambda r, e: drawn.append(r)
    )
    a.limit_to_one()
    assert (a.processes, a.shrinking, a.executor) == (1, True, old)
    gate.set()
    wait_for(qapp, lambda: len(received) == 5 and drawn)
    assert drawn == ["描き直し"]
    assert a.executor is not old and not a.shrinking and a.processes == 1
    assert sorted(index for _, index, _, _ in received) == [0, 1, 2, 3, 4]
    a.limit_to_one()  # 1つのときは何もしない
    assert not a.shrinking


def test_page_is_retried_once_when_process_ends(fake, qapp):
    """解析のプロセスが途中で終わったページは、1回だけやり直す。やり直しても終われば処理できないとする"""
    a, calls, gate, received = fake
    gate.set()
    a.submit(1, [job(0, "crash_once"), job(1, "crash"), job(2)])
    wait_for(qapp, lambda: len(received) == 3)
    assert calls == ["crash_once", "crash_once", "crash", "crash", "p2"]
    assert received == [
        (1, 0, "crash_once の結果", ""),
        (1, 1, None, workers.CRASHED),
        (1, 2, "p2 の結果", ""),
    ]


def test_page_crashing_process_does_not_fail_page_analyzed_with_it(qapp, monkeypatch):
    """解析のプロセスが2つのとき、1ページ（crash_now）の解析で解析のプロセスが落ちると、同時に解析していたページ（p0）
    の依頼も失敗する（executor 全体が壊れる）。p0 は、落ちたページと同時にやり直さず、巻き込まれて「処理できません」に
    ならない。落ちたページだけが、1ページずつのやり直しでも2回落ちて「処理できません」になる。残りのページ（p2・p3）
    も解析する"""
    a, calls, gate, received = fake_analyzer(monkeypatch, 2, BreakingPool)
    try:
        # p0 が動き出してから crash_now を出す（本物と同じく、動き出す前に executor が壊れた依頼は動かないため）
        a.submit(1, [job(0)])
        wait_for(qapp, lambda: calls == ["p0"])
        a.submit(1, [job(1, "crash_now"), job(2), job(3)])
        # p0 が gate を待っているあいだに、crash_now が落ちる。巻き込まれた p0 をもう一度出すまで待ってから gate を開く
        wait_for(qapp, lambda: calls.count("p0") >= 2 or len(received) >= 1)
        gate.set()
        wait_for(qapp, lambda: len(received) == 4)
        got = {index: (result, error) for _, index, result, error in received}
        assert got == {
            0: ("p0 の結果", ""),
            1: (None, workers.CRASHED),
            2: ("p2 の結果", ""),
            3: ("p3 の結果", ""),
        }
        # crash_now は、p0 と同時の1回（数えない）と、1ページずつの2回。p0 は、巻き込まれた1回と1ページずつの1回
        assert (calls.count("crash_now"), calls.count("p0")) == (3, 2)
        assert not a.alone_running and not a.queue and not a.together
    finally:
        gate.set()
        a.shutdown()


def test_page_crashing_process_alone_is_counted_at_once(qapp, monkeypatch):
    """解析のプロセスが2つでも、ほかの依頼を出していないときに落ちたページは、そのページが原因なので、1回目から
    やり直しの数に入れる（1つのときと同じく、2回落ちたら「処理できません」。1ページずつのやり直しを足さない）"""
    a, calls, gate, received = fake_analyzer(monkeypatch, 2, BreakingPool)
    try:
        gate.set()
        a.submit(1, [job(0, "crash_now")])
        wait_for(qapp, lambda: len(received) == 1)
        assert received == [(1, 0, None, workers.CRASHED)]
        assert calls == ["crash_now", "crash_now"]
        assert not a.live and not a.ended and not a.together
    finally:
        a.shutdown()


class FailOnceAfterRestart(BreakingPool):
    """作り直した2つ目の executor では、最初の解析の依頼を出すときに OSError になる（解析のプロセスを起動できない
    ときの代わり）"""

    made = 0

    def __init__(self, n):
        super().__init__(n)
        FailOnceAfterRestart.made += 1
        self.fail = FailOnceAfterRestart.made == 2

    def submit(self, fn, *args):
        if self.fail and analysis.analyze in args:
            self.fail = False
            raise OSError("プロセスを起動できない")
        return super().submit(fn, *args)


def test_failure_to_submit_does_not_stop_analysis(qapp, monkeypatch):
    """依頼を解析のプロセスに出せなかった（解析のプロセスを起動できないなど）ページは、その理由で「処理できません」に
    なり、残りのページと後から出した依頼の解析は続く（1ページずつのやり直しを出せなかったときも、ほかの依頼を
    止めたままにしない）"""
    FailOnceAfterRestart.made = 0
    a, _, gate, received = fake_analyzer(monkeypatch, 1, FailOnceAfterRestart)
    try:
        gate.set()
        a.submit(1, [job(0, "crash_now"), job(1)])
        wait_for(qapp, lambda: len(received) == 2, sec=20)
        a.submit(2, [job(5)])
        wait_for(qapp, lambda: len(received) == 3, sec=20)
        assert received == [
            (1, 0, None, "OSError: プロセスを起動できない"),
            (1, 1, "p1 の結果", ""),
            (2, 5, "p5 の結果", ""),
        ]
        assert not a.alone_running and a.running == 0
    finally:
        a.shutdown()


def test_alone_job_waits_until_other_work_ends(fake2, qapp):
    """1ページずつの依頼（alone）は、解析中のページと call() で出した処理が終わるまで出さない"""
    a, calls, gate, received = fake2
    a.submit(1, [job(0)])
    a.submit(1, [dataclasses.replace(job(1), alone=True)])
    assert (a.running, [j.index for _, j in a.queue]) == (1, [1])
    gate.set()
    wait_for(qapp, lambda: len(received) == 2)
    gate.clear()
    drawn = []
    a.call(
        lambda: (gate.wait(30), "描き直し")[1], callback=lambda r, e: drawn.append(r)
    )
    a.submit(1, [dataclasses.replace(job(2), alone=True)])
    assert (a.running, [j.index for _, j in a.queue]) == (0, [2])
    gate.set()
    wait_for(qapp, lambda: len(received) == 3 and drawn)
    assert calls == ["p0", "p1", "p2"]


def test_nothing_else_is_submitted_while_alone_job_runs(fake2, qapp):
    """1ページずつの依頼（alone）を解析しているあいだは、解析のプロセスが空いていても、ほかの依頼を出さない"""
    a, calls, gate, received = fake2
    a.submit(1, [dataclasses.replace(job(0), alone=True)])
    a.submit(1, [job(1)])
    assert (a.running, [j.index for _, j in a.queue]) == (1, [1])
    gate.set()
    wait_for(qapp, lambda: len(received) == 2)
    assert calls == ["p0", "p1"]


def crash():
    raise BrokenProcessPool("解析のプロセスが終わった")


def test_crash_in_call_is_not_blamed_on_page(qapp, monkeypatch):
    """call() で出した処理（様式の画面の解析・描き直しなど）で解析のプロセスが落ちても、同時に解析していたページを原因と
    みなさず、1ページずつ解析し直す。そのあいだに頼んだ call は、そのページが済んでから出す（同時に動かさない）"""
    a, calls, gate, received = fake_analyzer(monkeypatch, 2, BreakingPool)
    errors = []
    try:
        a.submit(1, [job(0)])
        wait_for(qapp, lambda: calls == ["p0"])
        a.call(crash, callback=lambda r, e: errors.append(str(e)))
        wait_for(qapp, lambda: calls.count("p0") == 2 and errors)
        a.call(crash, callback=lambda r, e: errors.append(str(e)))
        time.sleep(0.3)  # 解析のプロセスは空いているが、出さない
        qapp.processEvents()
        assert (a.calling, len(a.waiting_calls), len(errors)) == (0, 1, 1)
        gate.set()
        wait_for(qapp, lambda: received and len(errors) == 2)
        assert received == [(1, 0, "p0 の結果", "")]
        assert errors == [workers.CRASHED] * 2
        assert calls.count("p0") == 2
    finally:
        gate.set()
        a.shutdown()


def test_crash_in_export_is_not_blamed_on_page(qapp, monkeypatch):
    """出力でページを墨消しする処理で解析のプロセスが落ちても、同時に解析していたページを原因とみなさない"""
    a, calls, gate, received = fake_analyzer(monkeypatch, 2, BreakingPool)

    def mask_page(job):
        raise BrokenProcessPool("解析のプロセスが終わった")

    monkeypatch.setattr(analysis, "mask_page", mask_page)
    try:
        a.submit(1, [job(0)])
        for n in (2, 3):
            wait_for(qapp, lambda n=n: calls.count("p0") == n - 1)
            with pytest.raises(BrokenProcessPool):
                list(a.masker()(["出力するページ"]))
            wait_for(qapp, lambda n=n: calls.count("p0") == n)
        gate.set()
        wait_for(qapp, lambda: received)
        assert received == [(1, 0, "p0 の結果", "")]
    finally:
        gate.set()
        a.shutdown()


def test_error_of_one_page_does_not_stop_the_rest(fake, qapp):
    """1ページのエラーは、そのページのエラーとして届き、残りのページの解析は続く。様式の型の画像を読めない
    ときは、文だけを届ける"""
    a, _, gate, received = fake
    gate.set()
    a.submit(1, [job(0, "broken"), job(1, "no_template"), job(2)])
    wait_for(qapp, lambda: len(received) == 3)
    assert received == [
        (1, 0, None, "ValueError: 壊れたページ"),
        (1, 1, None, "様式「住宅」の見本の画像を読めません。（パス）"),
        (1, 2, "p2 の結果", ""),
    ]


def test_input_error_is_told_without_class_name(fake, qapp, monkeypatch):
    """入力のファイルを読めない理由（imaging.InputError）は、例外のクラスの名前を付けずに、文だけを届ける"""
    a, _, _, received = fake

    def analyze(source, quarter_turns, engine, formset=None):
        raise imaging.InputError("PDF の1ページ目を読めません: bad.pdf")

    monkeypatch.setattr(pipeline, "analyze", analyze)
    a.submit(1, [job(0)])
    wait_for(qapp, lambda: received, sec=20)
    assert received == [(1, 0, None, "PDF の1ページ目を読めません: bad.pdf")]


def test_crashing_page_is_not_retried_forever_with_redraw(qapp, monkeypatch):
    """解析のプロセスが1つのとき、解析のプロセスを落とすページを解析しているあいだに描き直し（call）が出ていて、描き直しが
    失敗するたびに同じ描き直しを頼み直す呼び手（墨消しの画面の work_drawn → prefetch）がいても、そのページは2回目の
    1ページずつのやり直しで落ちたら「処理できません」になり、後ろのページも解析する。1ページずつで出した依頼が落ちたら、
    ほかの処理と同時でも、やり直しの数に入れる。1ページずつの依頼が動いているあいだは、call を出さずに待たせる"""
    a, calls, gate, received = fake_analyzer(monkeypatch, 1, BreakingPool)
    inner = pipeline.analyze

    def analyze(source, *args, **kwargs):
        if source == "slow_crash":
            calls.append(source)
            time.sleep(0.2)  # 細工したページは、落ちるまでに時間がかかる
            raise BrokenProcessPool("解析のプロセスが終わった")
        return inner(source, *args, **kwargs)

    monkeypatch.setattr(pipeline, "analyze", analyze)
    draws = []

    def callback(result, error):
        draws.append(error)
        if error is not None:
            a.call(lambda: "描き直し", callback=callback)

    gate.set()
    try:
        a.submit(1, [job(0, "slow_crash"), job(1)])
        wait_for(qapp, lambda: "slow_crash" in calls, sec=30)
        a.call(
            lambda: "描き直し", callback=callback
        )  # ページ0の解析中に、描き直しを頼む
        end = (
            time.time() + 15
        )  # 止まらないときに、テストが終わらなくならないよう打ち切る
        while time.time() < end and len(received) < 2:
            qapp.processEvents()
            time.sleep(0.01)
        got = {index: error for _, index, _, error in received}
        assert got == {0: workers.CRASHED, 1: ""}, (calls.count("slow_crash"), draws)
        assert calls.count("slow_crash") <= 3
    finally:
        a.shutdown()


def test_alone_job_crashing_with_export_counts_as_retry(qapp, monkeypatch):
    """1ページずつで出した依頼が、出力の墨消しと同時に落ちても、やり直しの数に入れる（2回目で「処理できません」）"""
    a, calls, gate, received = fake_analyzer(monkeypatch, 2, BreakingPool)
    masking, release = threading.Event(), threading.Event()

    def mask_page(job):
        masking.set()
        release.wait(30)
        return job

    monkeypatch.setattr(analysis, "mask_page", mask_page)
    exported = []

    def export(masker):
        try:
            exported.extend(masker(["出力するページ"]))
        except BrokenProcessPool as e:
            exported.append(e)

    try:
        a.submit(1, [dataclasses.replace(job(0, "crash"), alone=True)])
        wait_for(qapp, lambda: calls == ["crash"])
        threading.Thread(target=export, args=(a.masker(),), daemon=True).start()
        assert masking.wait(10)
        gate.set()  # 墨消しと同時に落ちる
        wait_for(qapp, lambda: received, sec=20)
        assert received == [(1, 0, None, workers.CRASHED)]
        assert calls.count("crash") == 2
        assert isinstance(exported[0], BrokenProcessPool)
    finally:
        release.set()
        gate.set()
        a.shutdown()


def test_job_from_before_clear_is_not_retried(fake, qapp, monkeypatch):
    """開き直して待っている依頼を取り消した（clear）後に、解析中だった前のファイルのページで解析のプロセスが落ちても、
    そのページをやり直さず、新しいファイルのページを先に解析する"""
    a, calls, gate, received = fake
    monkeypatch.setenv(workers.ENV_PROCESSES, "1")
    a.submit(1, [job(0, "crash"), job(1)])
    wait_for(qapp, lambda: calls == ["crash"])
    a.clear()
    a.submit(2, [job(5), job(6)])
    gate.set()
    wait_for(qapp, lambda: len([r for r in received if r[0] == 2]) == 2)
    assert calls == ["crash", "p5", "p6"]


def test_start_fails_when_second_preparation_cannot_be_submitted(qapp, monkeypatch):
    """解析のプロセスが2つのとき、2つ目の準備を出せなければ（解析のプロセスを起動できないなど）、起動の準備は、その
    失敗を返す（成功とみなさない）"""

    class FailSecond(BreakingPool):
        made = 0

        def submit(self, fn, *args):
            FailSecond.made += 1
            if FailSecond.made == 2:
                raise OSError("プロセスを起動できない")
            return super().submit(fn, *args)

    monkeypatch.setenv(workers.ENV_PROCESSES, "2")
    monkeypatch.setattr(workers, "new_executor", FailSecond)
    monkeypatch.setattr(analysis, "prepare", os.getpid)
    a = workers.Analyzer()
    try:
        with pytest.raises(OSError, match="プロセスを起動できない"):
            a.start().result(timeout=10)
    finally:
        a.shutdown()


def test_crash_in_call_restarts_process_before_export(qapp, monkeypatch):
    """call で出した処理で解析のプロセスが落ちたら、次の依頼を待たずに作り直す（その後に出力を始めても、落ちた解析の
    プロセスで墨消ししない）"""
    a, _calls, gate, _received = fake_analyzer(monkeypatch, 1, BreakingPool)
    monkeypatch.setattr(analysis, "mask_page", lambda job: f"{job} を墨消しした")
    errors = []
    try:
        old = a.executor
        a.call(crash, callback=lambda r, e: errors.append(e))
        wait_for(qapp, lambda: errors, sec=20)
        assert a.executor is not old
        assert list(a.masker()(["出力するページ"])) == ["出力するページ を墨消しした"]
    finally:
        gate.set()
        a.shutdown()


def test_crash_of_page_and_call_restarts_process_once(qapp, monkeypatch):
    """解析中のページと call で出した処理が、同じ解析のプロセスの組で一緒に失敗しても、作り直すのは1回だけ（先に届いた
    方が作り直し、後の方は、作り直した組を終えない）"""
    made = []

    def counting(n):
        made.append(n)
        return BreakingPool(n)

    a, calls, gate, received = fake_analyzer(monkeypatch, 2, counting)
    errors = []
    try:
        a.submit(1, [job(0)])
        wait_for(qapp, lambda: calls == ["p0"])
        a.call(crash, callback=lambda r, e: errors.append(str(e)))
        wait_for(qapp, lambda: errors and calls.count("p0") == 2)
        gate.set()
        wait_for(qapp, lambda: received)
        assert received == [(1, 0, "p0 の結果", "")]
        assert made == [2, 2]  # 最初の組と、作り直した1組
    finally:
        gate.set()
        a.shutdown()


def test_crash_in_export_restarts_process_before_next_export(qapp, monkeypatch):
    """出力の墨消しで解析のプロセスが落ちたら、解析の依頼や call を待たずに作り直す（次の出力の1つ目のファイルも、
    作り直した解析のプロセスで墨消しする）"""
    a, _calls, gate, _received = fake_analyzer(monkeypatch, 1, BreakingPool)

    def mask_page(job):
        if job == "落とすページ":
            raise BrokenProcessPool("解析のプロセスが終わった")
        return f"{job} を墨消しした"

    monkeypatch.setattr(analysis, "mask_page", mask_page)
    try:
        old = a.executor
        with pytest.raises(BrokenProcessPool):
            list(a.masker()(["落とすページ"]))
        end = time.time() + 2
        while time.time() < end and a.executor is old:
            qapp.processEvents()
            time.sleep(0.01)
        assert a.executor is not old
        assert list(a.masker()(["次のページ"])) == ["次のページ を墨消しした"]
    finally:
        gate.set()
        a.shutdown()


def test_export_masking_moves_to_new_process_after_limit_to_one(qapp, monkeypatch):
    """出力のスレッドが墨消しする関数（masker）を受け取った後、最初の墨消しを出す前に、解析のプロセスを1つに減らして
    作り直しても、そのファイルは、作り直した解析のプロセスで墨消しする"""
    a, _calls, gate, _received = fake_analyzer(monkeypatch, 2)
    monkeypatch.setattr(analysis, "mask_page", lambda job: f"{job} を墨消しした")
    try:
        masker = a.masker()
        old = a.executor
        a.limit_to_one()  # 解析中のページも call もないので、すぐ作り直す
        assert a.executor is not old
        assert list(masker(["ページ"])) == ["ページ を墨消しした"]
    finally:
        gate.set()
        a.shutdown()


def test_export_masking_uses_language_of_screen(qapp, monkeypatch):
    """出力でページを墨消しする処理も、ほかの依頼と同じく、画面の言語を解析のプロセスに渡す（analysis.call_in）"""
    a, _calls, gate, _received = fake_analyzer(monkeypatch, 1)
    monkeypatch.setattr(analysis, "mask_page", lambda job: i18n.language())
    try:
        i18n.set_language(i18n.JA)
        masker = a.masker()
        i18n.set_language(i18n.EN)
        assert list(masker(["ページ"])) == [i18n.JA]  # masker を作ったときの言語
        i18n.set_language(
            i18n.EN
        )  # 代わりの executor はスレッドで動くので、上の墨消しで日本語に戻っている
        assert list(a.masker()(["ページ"])) == [i18n.EN]
    finally:
        i18n.set_language(i18n.JA)
        gate.set()
        a.shutdown()


# ---------------------------------------------------------------- 本物の解析のプロセス


@pytest.fixture(scope="module")
def process(qapp, tmp_path_factory):
    """本物の解析のプロセスを1つ立ち上げた Analyzer"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(workers.ENV_PROCESSES, "1")
        a = workers.Analyzer()
        a.pid = a.start().result(timeout=300)
        yield a
        a.shutdown()


def run(qapp, a, jobs, before_wait=None):
    """jobs を頼み、届いた (ページ, 結果, エラー) を、届いた順に返す"""
    token, got = object(), []

    def receive(t, index, result, error):
        if t is token:
            got.append((index, result, error))

    a.page_done.connect(receive)
    try:
        a.submit(token, jobs)
        if before_wait:
            before_wait()
        wait_for(qapp, lambda: len(got) == len(jobs), sec=300)
    finally:
        a.page_done.disconnect(receive)
    return got


def test_process_writes_crash_log_header(process):
    """解析のプロセスは、起動したときに異常終了の記録に見出しの行を書く"""
    log = Path(os.environ["LOCALAPPDATA"]) / "SumikeshiStudio" / crashlog.LOG_NAME
    assert f"role: analysis  pid: {process.pid}" in log.read_text("utf-8")


def test_process_ignores_ctrl_c(process, qapp):
    """解析のプロセスは Ctrl+C（SIGINT）を無視する（止めるかどうかは、コマンドラインから呼んだプロセスが決める）"""
    got = []
    process.call(
        signal.getsignal,
        signal.SIGINT,
        callback=lambda result, error: got.append((result, error)),
    )
    wait_for(qapp, lambda: got)
    assert got == [(signal.SIG_IGN, None)]


def test_load_forms_puts_any_failure_into_error(tmp_path, monkeypatch):
    """様式を読む処理は、どんな失敗でも理由を Loaded.error に入れて返す（ファイルを開かない理由になる）"""
    choice = formstore.FolderChoice(tmp_path, formstore.ORIGIN_USER)
    write_form(tmp_path, "u-0001", "登録した様式")
    loaded = workers.load_forms(choice, frozenset())
    assert [f.id for f in loaded.active] == ["u-0001"] and loaded.blocking_text() == ""

    def broken(choice, off):
        raise RuntimeError("試験")

    monkeypatch.setattr(formstore, "load", broken)
    loaded = workers.load_forms(choice, frozenset({"u-0001"}))
    assert loaded.folder == tmp_path and loaded.off == frozenset({"u-0001"})
    assert loaded.blocking_text() == loaded.error
    assert loaded.error.endswith("RuntimeError: 試験")


def test_process_uses_language_of_screen(process, qapp):
    """解析のプロセスに出す依頼は、依頼を出した時の画面の言語で動く（解析のプロセスで作る文を、画面と同じ言語に
    するため）"""
    got = []

    def ask():
        process.call(
            i18n.language, callback=lambda result, error: got.append((result, error))
        )
        wait_for(qapp, lambda: got)
        return got.pop()

    i18n.set_language(i18n.EN)
    assert ask() == (i18n.EN, None)
    i18n.set_language(i18n.JA)
    assert ask() == (i18n.JA, None)


def test_process_gives_same_result_as_in_process(process, qapp, scan):
    page = imaging.open_pages(scan("2027bun_01", filled=True))[0]
    [(_, got, error)] = run(qapp, process, [job(0, page)])
    want = cached_analyze(page)  # このプロセスで解析した結果
    assert error == ""
    assert (got.quarter_turns, got.skew, got.form, got.note, got.engine) == (
        want.quarter_turns,
        want.skew,
        want.form,
        want.note,
        want.engine,
    )
    shape = [(b.x, b.y, b.w, b.h, b.kind, b.source, b.active) for b in got.boxes]
    assert shape == [
        (b.x, b.y, b.w, b.h, b.kind, b.source, b.active) for b in want.boxes
    ]
    assert len(got.boxes) == FIELDS
    assert np.array_equal(got.work, want.work)


def test_process_uses_user_forms(process, qapp, scan, tmp_path):
    """解析のプロセスは、依頼に入れた様式の型の画像を読んで使う

    一度読んだ型の画像は覚えておくので、その後に画像を読めなくなっても解析できる。まだ読んでいない様式の
    画像を読めなければ、そのページは「処理できません」になる。
    """
    page = imaging.open_pages(scan("2027bun_01", filled=True))[0]
    write_form(tmp_path, "u-0003", "登録した扶養控除等申告書", fields=2)
    formset = formstore.load(formstore.FolderChoice(tmp_path)).active
    [(_, got, error)] = run(qapp, process, [job(0, page, formset=formset)])
    assert error == "" and got.form == "登録した扶養控除等申告書"
    assert len(got.boxes) == 2 and [s.id for s in got.used] == ["u-0003"]
    (tmp_path / "u-0003-r1.png").unlink()  # 共有フォルダにつながらなくなった
    [(_, got, error)] = run(qapp, process, [job(0, page, formset=formset)])
    assert error == "" and got.form == "登録した扶養控除等申告書"
    write_form(tmp_path, "u-0004", "まだ読んでいない様式", base="nta-hoken-r8")
    late = formstore.load(formstore.FolderChoice(tmp_path)).active
    (tmp_path / "u-0004-r1.png").unlink()
    [(_, got, error)] = run(qapp, process, [job(0, page, formset=late)])
    assert got is None
    assert error.startswith("様式「まだ読んでいない様式」の見本の画像を読めません。")


def test_page_is_retried_after_process_ends(process, qapp, scan):
    """解析のプロセスが途中で終わると、作り直して、解析中だったページをやり直し、残りも続ける"""
    page = imaging.open_pages(scan("2027bun_01", filled=True))[0]
    pid = process.pid
    got = run(
        qapp,
        process,
        [job(0, page), job(1, page)],
        before_wait=lambda: os.kill(pid, signal.SIGTERM),
    )
    assert sorted(index for index, _, _ in got) == [0, 1]
    for _, result, error in got:
        assert error == "" and len(result.boxes) == FIELDS


def test_two_processes_retry_running_pages(qapp, scan, monkeypatch, tmp_path):
    """解析のプロセスが2つのとき、1つが途中で終わると、解析中だった2ページをやり直す"""
    monkeypatch.setenv(workers.ENV_PROCESSES, "2")
    a = workers.Analyzer()
    try:
        a.start().result(timeout=300)
        assert a.processes == 2
        pids = list(a.executor._processes)  # 立ち上がった解析のプロセス
        page = imaging.open_pages(scan("2027bun_01", filled=True))[0]
        got = run(
            qapp,
            a,
            [job(0, page), job(1, page)],
            before_wait=lambda: os.kill(pids[0], signal.SIGTERM),
        )
        assert sorted(index for index, _, _ in got) == [0, 1]
        for _, result, error in got:
            assert error == "" and len(result.boxes) == FIELDS
    finally:
        a.shutdown()


def test_native_exception_is_told_from_ordinary_os_errors():
    """Windows の例外から ctypes が作った OSError（winerror が負の数のもの、winerror がなく「exception: 」で始まる文のもの）を
    見分け、ふつうの OSError（ファイルを読めない・アクセスが拒否されたなど。winerror が正の数か None）を巻き込まない"""
    denied = PermissionError(13, "アクセスが拒否されました", "a.pdf")
    denied.winerror = 5
    ordinary = [
        FileNotFoundError(2, "見つかりません", "a.pdf"),
        denied,
        OSError("ディスクがいっぱいです"),
        MemoryError(),
        RuntimeError("exception: access violation reading 0x0"),
    ]
    native = [
        nativecrash.native_error(),
        OSError("exception: access violation reading 0x0000000000000000"),
        OSError("exception: stack overflow"),
    ]
    assert [analysis.native_exception(e) for e in ordinary] == [False] * 5
    assert [analysis.native_exception(e) for e in native] == [True] * 3


def test_windows_exception_ends_analysis_process(qapp, scan, monkeypatch):
    """解析のプロセスで、Windows の例外から作られた OSError が出たら（PDFium の中の C++ の例外を ctypes が受け止めたとき）、
    その解析のプロセスを終わらせ、ほかの異常終了と同じに扱う：ページの解析では、作り直して1ページずつやり直し、2回目で
    「解析のプロセスが途中で終了しました。」にする。call() と出力の墨消しも同じく失敗する。その後も、作り直した解析の
    プロセスで、ほかのページの解析・call()・出力の墨消しができる（同じプロセスで続けると、PDFium が取ったメモリが残り、
    後の墨消しがメモリの上限で失敗した）"""
    monkeypatch.setenv(workers.ENV_PROCESSES, "1")
    a = workers.Analyzer()
    try:
        first = a.start().result(timeout=300)
        page = imaging.open_pages(scan("2027bun_01", filled=True))[0]
        got = {
            index: (result, error)
            for index, result, error in run(
                qapp, a, [job(0, nativecrash.NativeErrorPage()), job(1, page)]
            )
        }
        assert got[0] == (None, workers.CRASHED)
        assert got[1][1] == "" and len(got[1][0].boxes) == FIELDS
        calls = []
        a.call(nativecrash.raise_native, callback=lambda r, e: calls.append(e))
        wait_for(qapp, lambda: calls, sec=120)
        assert str(calls[0]) == workers.CRASHED
        pids = []
        a.call(os.getpid, callback=lambda r, e: pids.append((r, e)))
        wait_for(qapp, lambda: pids, sec=120)
        assert pids[0][1] is None and pids[0][0] != first
        with pytest.raises(BrokenProcessPool):
            list(a.masker()([export.PageJob.of(nativecrash.NativeErrorPage(), None)]))
        old = a.executor
        wait_for(qapp, lambda: a.executor is not old, sec=30)
        (image,) = a.masker()([export.PageJob.of(page, None)])
        assert image.jpeg[:2] == b"\xff\xd8"  # JPEG
        log = Path(os.environ["LOCALAPPDATA"]) / "SumikeshiStudio" / crashlog.LOG_NAME
        text = log.read_text("utf-8")
        title = "Windows exception in a native call (ending the analysis process)"
        assert text.count(title) >= 4  # ページの2回・call()・出力の墨消し
        assert "Windows Error 0xe06d7363" in text
    finally:
        a.shutdown()


def test_job_with_memory_page_survives_pickle():
    """メモリの中の画像（様式の画面に貼り付けたクリップボードの画像）を入れた解析の依頼を、解析のプロセスに渡すときと
    同じく pickle に通しても、画素と解像度が変わらない（画面の
    テストは解析のプロセスを置き換えるので、ここで確かめる。墨消しする前の画像なので、ディスクには書かない）"""
    rgb = np.random.default_rng(0).integers(0, 256, (240, 170, 3), dtype=np.uint8)
    page = imaging.memory_page(rgb)
    job = AnalysisJob(3, page, None, ocr.Engine.RAPIDOCR, ())
    back = pickle.loads(pickle.dumps(job))
    assert isinstance(back.source, imaging.MemoryPage)
    assert back.source.rgb is not rgb and np.array_equal(back.source.rgb, rgb)
    assert (back.source.dpi, back.source.name) == (page.dpi, page.name)
    assert (back.index, back.quarter_turns, back.engine) == (
        3,
        None,
        ocr.Engine.RAPIDOCR,
    )
