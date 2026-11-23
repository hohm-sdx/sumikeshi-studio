# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""ページの解析を解析のプロセスに頼む仕組み（workers.Analyzer）のテスト（表示なしで動かす）

依頼の順と取り消しは、解析の代わりの関数をスレッドで動かして確かめる。結果の受け渡し・エラーの届き方・
解析のプロセスが終わったときの続け方は、本物の解析のプロセスで確かめる。
"""

import os
import pickle
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import numpy as np
import pytest
from conftest import cached_analyze
from userforms import bundled_forms, write_form

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip(
    "PySide6.QtWidgets", reason="画面の部品を読み込めない環境", exc_type=ImportError
)

from sumikeshi_studio import (
    analysis,
    crashlog,
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


def fake_analyzer(monkeypatch, processes):
    """解析の代わりの関数をスレッドで動かす Analyzer（解析のプロセスの数は processes）

    代わりの関数は、受け取ったページ（source）を calls に記録し、gate が開くまで終わらない。
    ページが "crash" なら毎回、"crash_once" なら1回目だけ、解析のプロセスが途中で終わったときと同じ
    BrokenProcessPool を出す。届いた (番号, ページ, 結果, エラー) は received に入る。
    """
    monkeypatch.setenv(workers.ENV_PROCESSES, str(processes))
    monkeypatch.setattr(workers, "new_executor", lambda n: ThreadPoolExecutor(n))
    monkeypatch.setattr(analysis, "prepare", os.getpid)
    calls, gate = [], threading.Event()

    def analyze(source, quarter_turns, engine, formset=None):
        calls.append(source)
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
        "(cv2). Extract the ZIP of the package and run setup.bat to see the names of the blocked components. "
        "A screenshot of this window may help investigate the cause."
    )
    assert second.startswith("(ImportError: DLL load failed while importing cv2: ")


def test_process_count(monkeypatch):
    """論理 CPU が4つ以上で、空きメモリが4GB以上なら2つ。環境変数で指定もできる"""
    import psutil

    monkeypatch.delenv(workers.ENV_PROCESSES, raising=False)
    memory = psutil.virtual_memory()
    for cpus, available, want in [(8, 4 << 30, 2), (4, 6 << 30, 2), (3, 8 << 30, 1),
                                  (8, (4 << 30) - 1, 1)]:  # fmt: skip
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
