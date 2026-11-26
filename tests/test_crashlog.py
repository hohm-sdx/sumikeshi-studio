# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""異常終了の記録（crashlog）のテスト

記録の仕組みはプロセス全体の設定（faulthandler・エラーの出力・例外のフック）を変えるため、別のプロセスで試す。
pythonw.exe と同じく sys.stderr がない状態にしてから、記録の仕組みを入れる。
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from sumikeshi_studio import __version__, crashlog, i18n

HEAD = """
import os, sys
sys.stderr = None  # pythonw.exe と同じく、エラーの出力がない
from sumikeshi_studio import crashlog
"""


def no_core_dump():
    """強制終了させる子のプロセスに、コアダンプのファイルを書かせない（Linux などで試すとき）"""
    import resource

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run(code: str, appdata: Path, check=True) -> subprocess.CompletedProcess:
    env = {**os.environ, "LOCALAPPDATA": str(appdata), "QT_QPA_PLATFORM": "offscreen"}
    out = subprocess.run(
        [sys.executable, "-c", HEAD + textwrap.dedent(code)],
        capture_output=True, text=True, env=env, timeout=300, check=False,
        cwd=appdata, preexec_fn=no_core_dump if os.name == "posix" else None,
    )  # fmt: skip
    if check:
        assert out.returncode == 0, out.stderr
    return out


def read_log(appdata: Path) -> str:
    return (appdata / "SumikeshiStudio" / crashlog.LOG_NAME).read_text("utf-8")


def test_header_errors_and_exceptions_are_recorded(tmp_path):
    """見出しの行、エラーの出力（Fatal Python error の文など）、スレッドの例外、処理できなかった例外を書く"""
    out = run(
        """
        import threading
        crashlog.setup(crashlog.ROLE_SCREEN)
        t = threading.Thread(target=lambda: 1 / 0, name="試験用のスレッド")
        t.start()
        t.join()
        os.write(2, "Fatal Python error: 試験用の文\\n".encode())
        class Bad:
            def __del__(self):
                raise ValueError("後始末の例外")
        Bad()
        print(os.getpid())
        """,
        tmp_path,
    )
    pid = out.stdout.strip()
    log = read_log(tmp_path)
    head = log.splitlines()[0]
    assert (
        head.startswith("==== ")
        and f"start  version: {__version__} (no version.txt)" in head
    )
    assert f"role: screen  pid: {pid}" in head
    assert (
        "exception in thread '試験用のスレッド'" in log and "ZeroDivisionError" in log
    )
    assert "Fatal Python error: 試験用の文" in log
    assert "ValueError: 後始末の例外" in log


def test_abort_leaves_python_stack(tmp_path):
    """abort で強制終了しても、そのときの Python の処理の場所が残る（faulthandler）"""
    out = run(
        """
        crashlog.setup(crashlog.ROLE_ANALYSIS)

        def abort_here():
            os.abort()

        abort_here()
        """,
        tmp_path,
        check=False,
    )
    assert out.returncode != 0
    log = read_log(tmp_path)
    assert "role: analysis" in log
    assert "Fatal Python error: Aborted" in log and "abort_here" in log


def test_qt_messages_are_recorded(tmp_path):
    """Qt の警告と、致命的なエラー（この後 Qt が abort する）の理由を書く。動いているスレッド（QThread）を
    手放すと、Qt は致命的なエラーにして abort する。スレッドが run に入ったのを待ってから手放す（run に入る前に手放すと、
    スレッドの出力に「in run」が出ない）"""
    out = run(
        """
        import threading
        import time
        from PySide6.QtCore import QCoreApplication, QThread, qWarning
        crashlog.setup(crashlog.ROLE_SCREEN)
        crashlog.install_qt_handler()
        qWarning("試験用の警告")
        app = QCoreApplication([])

        started = threading.Event()

        class Sleeper(QThread):
            def run(self):
                started.set()
                time.sleep(5)

        t = Sleeper()
        t.start()
        assert started.wait(30)
        del t
        """,
        tmp_path,
        check=False,
    )
    assert out.returncode != 0
    log = read_log(tmp_path)
    assert "Qt warning: 試験用の警告" in log
    assert (
        "Qt fatal error (the process will abort): "
        "QThread: Destroyed while thread '' is still running" in log
    )
    assert "Fatal Python error: Aborted" in log and "in run" in log


def test_large_log_is_kept_once_and_restarted(tmp_path):
    """画面のプロセスの起動のときに1MB を超えていれば、前のものを1つだけ残して新しく書き始める"""
    folder = tmp_path / "SumikeshiStudio"
    folder.mkdir()
    (folder / crashlog.LOG_NAME).write_text("前の記録\n" * 200_000, encoding="utf-8")
    run(
        "crashlog.setup(crashlog.ROLE_ANALYSIS)", tmp_path
    )  # 解析のプロセスでは変えない
    assert (folder / crashlog.LOG_NAME).stat().st_size > crashlog.MAX_BYTES
    run("crashlog.setup(crashlog.ROLE_SCREEN)", tmp_path)
    assert (folder / crashlog.OLD_NAME).read_text("utf-8").startswith("前の記録")
    assert read_log(tmp_path).startswith("==== ")
    assert (folder / crashlog.LOG_NAME).stat().st_size < 1000


def test_previous_abnormal_exit_is_found(tmp_path, monkeypatch):
    """閉じる操作をせずに終わった画面のプロセスのマーカーがあれば、次の起動で見つける。動いているものは除く"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert not crashlog.previous_abnormal()
    gone = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True).stdout.strip()  # fmt: skip
    marks = tmp_path / "SumikeshiStudio" / crashlog.RUNNING_DIR
    marks.mkdir(parents=True)
    (marks / f"{gone}.txt").write_text("1.0", "utf-8")
    crashlog.mark_running()  # このプロセス（動いている）のマーカー
    assert crashlog.previous_abnormal()
    assert [p.name for p in marks.iterdir()] == [f"{os.getpid()}.txt"]
    assert (
        not crashlog.previous_abnormal()
    )  # 動いているプロセスのマーカーだけなら、異常終了ではない
    crashlog.clear_running()
    assert list(marks.iterdir()) == []


def test_app_records_start_and_end_and_tells_previous_abnormal_exit(tmp_path):
    """画面のプロセスは、起動と終了を記録し、前回の異常終了があれば起動のときに知らせる"""
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    gone = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True).stdout.strip()  # fmt: skip
    marks = tmp_path / "SumikeshiStudio" / crashlog.RUNNING_DIR
    marks.mkdir(parents=True)
    (marks / f"{gone}.txt").write_text("1.0", "utf-8")
    out = run(
        f"""
        from PySide6.QtCore import QSettings, QTimer
        from PySide6.QtWidgets import QApplication, QMessageBox
        from sumikeshi_studio import launcher
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                          {str(tmp_path / "ini")!r})
        shown = []
        QMessageBox.warning = lambda *a, **k: shown.append(a[2])
        show = launcher.LauncherWindow.show

        def show_then_close(win):  # 起動の画面を出してから（起動の準備が終わるころに）閉じる
            show(win)
            QTimer.singleShot(5000, win.close)

        launcher.LauncherWindow.show = show_then_close
        sys.argv = ["sumikeshi-studio"]
        try:
            launcher.main()
        except SystemExit:
            pass
        import json
        print(json.dumps(shown, ensure_ascii=False))
        """,
        tmp_path,
    )
    shown = json.loads(out.stdout.strip().splitlines()[-1])
    log_path = tmp_path / "SumikeshiStudio" / crashlog.LOG_NAME
    assert shown == [crashlog.abnormal_text(log_path)]
    log = read_log(tmp_path)
    assert "role: screen" in log and "role: analysis" in log  # 解析のプロセスも書く
    assert "exit  role: screen" in log
    assert list(marks.iterdir()) == []  # 閉じる操作で終わったので、マーカーは残らない


def test_abnormal_text_is_in_screen_language():
    """前回の異常な終了を示す文は、画面の言語にする（記録の中身は英語のまま）"""
    path = Path("C:/data/crash.log")
    assert crashlog.abnormal_text(path).startswith(
        "前回の起動で、ツールが閉じる操作をしないまま終わりました"
    )
    assert crashlog.abnormal_text(path).endswith(
        "この記録は、原因の調査に役立つ場合があります。"
    )
    i18n.set_language(i18n.EN)
    assert crashlog.abnormal_text(path) == (
        "The last time the tool was started, it ended without being closed (for example, the window "
        f"suddenly disappeared).\nLog: {path}\n"
        "This record may help investigate the cause."
    )


def test_log_is_appended_when_it_cannot_be_renamed(tmp_path):
    """記録のファイルの名前を変えられないとき（ほかのプロセスが開いているなど）は、そのまま書き足す"""
    folder = tmp_path / "SumikeshiStudio"
    folder.mkdir()
    (folder / crashlog.LOG_NAME).write_text("前の記録\n" * 200_000, encoding="utf-8")
    run(
        """
        def cannot_rename(*a):
            raise PermissionError("ほかのプロセスが開いている")
        os.replace = cannot_rename
        crashlog.setup(crashlog.ROLE_SCREEN)
        """,
        tmp_path,
    )
    log = read_log(tmp_path)
    assert log.startswith("前の記録") and "role: screen" in log
    assert not (folder / crashlog.OLD_NAME).exists()


# ---------------------------------------------------------------- Windows のエラー報告

E_FAIL = 0x80004005 - (1 << 32)  # HRESULT（符号付き）


class FakeKernel32:
    """kernel32 の代わり。WerGetFlags・WerSetFlags の呼び出しを記録し、フラグを覚える"""

    def __init__(self, flags=0, get_result=0, set_result=0, has_set=True):
        self.flags, self.calls = flags, []

        def get_current_process():
            return -1

        def wer_get_flags(process, ptr):
            self.calls.append(("get", process))
            if get_result == 0:
                ptr._obj.value = self.flags
            return get_result

        def wer_set_flags(value):
            self.calls.append(("set", value))
            if set_result == 0:
                self.flags = value
            return set_result

        self.GetCurrentProcess = get_current_process
        self.WerGetFlags = wer_get_flags
        if has_set:
            self.WerSetFlags = wer_set_flags


def test_error_report_excludes_heap_on_windows(monkeypatch):
    """Windows では、今のフラグに WER_FAULT_REPORTING_FLAG_NOHEAP（1）を足して設定し、読み直した値を書く"""
    k = FakeKernel32(flags=0x4)
    monkeypatch.setattr(crashlog.sys, "platform", "win32")
    monkeypatch.setattr(crashlog, "_kernel32", lambda: k)
    assert crashlog.exclude_heap_from_error_report() == (
        "error report: heap excluded (flags 0x5)"
    )
    assert k.calls == [("get", -1), ("set", 0x5), ("get", -1)]


def test_error_report_failure_does_not_stop_the_tool(monkeypatch):
    """設定できなくても例外を出さず、理由を返す（関数がない、HRESULT が S_OK でない）"""
    monkeypatch.setattr(crashlog.sys, "platform", "win32")
    monkeypatch.setattr(crashlog, "_kernel32", lambda: FakeKernel32(set_result=E_FAIL))
    assert crashlog.exclude_heap_from_error_report() == (
        "error report: could not set the flags (0x80004005)"
    )
    monkeypatch.setattr(crashlog, "_kernel32", lambda: FakeKernel32(has_set=False))
    text = crashlog.exclude_heap_from_error_report()
    assert text.startswith("error report: could not set the flags (AttributeError")
    # 一度も設定していなくて今の値を読めないときは、0 に足す
    k = FakeKernel32(flags=0x8, get_result=E_FAIL)
    monkeypatch.setattr(crashlog, "_kernel32", lambda: k)
    assert crashlog.exclude_heap_from_error_report().startswith(
        "error report: set the flags but could not read them back"
    )
    assert ("set", 0x1) in k.calls


def test_error_report_is_not_touched_outside_windows(monkeypatch):
    """Windows 以外では、kernel32 を呼ばない"""
    called = []
    monkeypatch.setattr(crashlog.sys, "platform", "linux")
    monkeypatch.setattr(crashlog, "_kernel32", lambda: called.append(True))
    assert crashlog.exclude_heap_from_error_report() == "" and called == []


def test_error_report_result_is_written_in_header(tmp_path):
    """設定の結果は、異常終了の記録の起動の行に書く（Windows で効いたかを確かめられるように）"""
    run(
        """
        import types
        state = {"flags": 0}
        def get_flags(process, ptr):
            ptr._obj.value = state["flags"]
            return 0
        def set_flags(value):
            state["flags"] = value
            return 0
        k = types.SimpleNamespace(
            GetCurrentProcess=lambda: -1, WerGetFlags=get_flags, WerSetFlags=set_flags
        )
        crashlog._kernel32 = lambda: k
        sys.platform = "win32"
        crashlog.setup(crashlog.ROLE_ANALYSIS)
        """,
        tmp_path,
    )
    head = read_log(tmp_path).splitlines()[0]
    assert "role: analysis" in head
    assert head.endswith("  error report: heap excluded (flags 0x1)")


def test_analysis_process_starts_with_crashlog_setup(monkeypatch):
    """解析のプロセスも、始めに crashlog.setup（エラー報告の設定を含む）を呼ぶ（画面のプロセスは launcher.main の
    始めで呼ぶ）"""
    from sumikeshi_studio import analysis, ocr

    roles = []
    monkeypatch.setattr(crashlog, "setup", lambda role: roles.append(role))
    monkeypatch.setattr(ocr, "THREADS", ocr.THREADS)
    analysis.configure(1)  # 解析のプロセスを立ち上げるときの設定
    assert roles == [crashlog.ROLE_ANALYSIS]
