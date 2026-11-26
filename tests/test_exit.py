# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""終わり方と、一度に1つだけ起動することのテスト（表示なしで動かす）。共通のフィクスチャは tests/conftest.py に、補助の関数は tests/gui.py にある"""

import os
import subprocess
import sys
import time

from gui import (
    list_dialog,
    open_forms_window,
    row_cells,
    select,
    use_forms_folder,
    wait,
)
from userforms import write_form

from sumikeshi_studio import (
    app,
    formstore,
    launcher,
)

# ---------------------------------------------------------------- 終わり方と、一度に1つだけ起動すること


def test_windows_job_structures_and_other_platforms():
    """Windows のジョブの構造体の大きさは、Windows の定義（64ビット）と同じ。Windows 以外ではジョブを使わない"""
    import ctypes

    from sumikeshi_studio import winjob

    assert ctypes.sizeof(winjob.JOBOBJECT_BASIC_LIMIT_INFORMATION) == 64
    assert ctypes.sizeof(winjob.JOBOBJECT_EXTENDED_LIMIT_INFORMATION) == 144
    if sys.platform != "win32":
        assert winjob.enter() == "not Windows; a job object is not used"
        assert not winjob.active()


def test_analysis_process_limits_its_memory_with_nested_job(monkeypatch):
    """解析のプロセスは、立ち上げるとき（analysis.configure）に、確保できるメモリを ANALYSIS_MEMORY_LIMIT（3GB）に限る
    ジョブ（JOB_OBJECT_LIMIT_PROCESS_MEMORY）に自分を入れる。画面のプロセスのジョブ（enter）とは別のジョブで、画面の
    プロセスには上限を付けない。Windows の関数は置き換えて確かめる（効き方は Windows の PC で確かめる）"""
    import ctypes

    from sumikeshi_studio import analysis, ocr, winjob

    assert winjob.ANALYSIS_MEMORY_LIMIT == 3 * 1024**3
    calls = []

    class Kernel32:
        def CreateJobObjectW(self, attributes, name):
            calls.append("create")
            return 7

        def SetInformationJobObject(self, job, kind, info, size):
            got = info._obj  # ctypes.byref で渡された構造体
            calls.append(
                (
                    "set",
                    job,
                    kind,
                    got.BasicLimitInformation.LimitFlags,
                    got.ProcessMemoryLimit,
                    size,
                )
            )
            return 1

        def GetCurrentProcess(self):
            return -1

        def AssignProcessToJobObject(self, job, process):
            calls.append(("assign", job, process))
            return 1

    job, reason = winjob._new_job(
        Kernel32(), winjob.JOB_OBJECT_LIMIT_PROCESS_MEMORY, winjob.ANALYSIS_MEMORY_LIMIT
    )
    assert (job, reason) == (7, "")
    assert calls == [
        "create",
        (
            "set",
            7,
            winjob.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            winjob.JOB_OBJECT_LIMIT_PROCESS_MEMORY,
            3 * 1024**3,
            ctypes.sizeof(winjob.JOBOBJECT_EXTENDED_LIMIT_INFORMATION),
        ),
        ("assign", 7, -1),
    ]
    # 画面のプロセスのジョブは、閉じたら中のプロセスを終わらせるだけで、メモリの上限を付けない
    calls.clear()
    winjob._new_job(Kernel32(), winjob.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
    assert calls[1][3:5] == (winjob.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, 0)
    # 解析のプロセスの立ち上げで呼ぶ（Windows のほかでは何もしない）
    limited = []
    monkeypatch.setattr(winjob, "limit_memory", lambda: limited.append(1) or "")
    monkeypatch.setattr(analysis.signal, "signal", lambda *args: None)
    monkeypatch.setattr("sumikeshi_studio.crashlog.setup", lambda role: None)
    analysis.configure(ocr.THREADS)
    assert limited == [1]
    if sys.platform != "win32":
        monkeypatch.undo()
        assert winjob.limit_memory() == "not Windows; a job object is not used"


def test_closing_launcher_while_preparing(new_launcher, monkeypatch):
    """起動の準備の途中で起動の画面を閉じたとき、ジョブに入っていれば後始末を飛ばして終え、入っていなければ
    今までどおり解析のプロセスを終えてから終える"""
    from sumikeshi_studio import winjob

    lw = new_launcher()
    calls = []
    monkeypatch.setattr(type(lw.session), "preloading", property(lambda self: True))
    monkeypatch.setattr(lw.session, "shutdown", lambda: calls.append("待って終える"))
    monkeypatch.setattr(
        launcher, "fast_exit", lambda: calls.append("後始末を飛ばして終える")
    )
    monkeypatch.setattr(winjob, "_job", object())  # ジョブに入っている
    lw.close()
    assert calls == ["後始末を飛ばして終える"] and not lw.isVisible()
    lw.show()
    monkeypatch.setattr(winjob, "_job", None)  # ジョブに入れなかった
    lw.close()
    assert calls[-1] == "待って終える"


def test_launcher_is_not_closed_while_writing_forms(new_launcher):
    """様式のフォルダを読み書きしている途中は、起動の画面を閉じない"""
    lw = new_launcher()
    lw.session.background.running = 1
    lw.close()
    assert lw.isVisible()
    assert (
        lw.shown[-1]
        == "様式のフォルダを読み書きしています。終わってから閉じてください。"
    )
    lw.session.background.running = 0


def test_things_written_before_exit(tmp_path):
    """後始末を飛ばして終えるときも、利用者ごとの設定・「動いている」マーカー・異常終了の記録を書き終える"""
    code = f"""
import os, sys
from PySide6.QtCore import QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, {str(tmp_path / "ini")!r})
from sumikeshi_studio import crashlog, launcher, prefs
crashlog.setup(crashlog.ROLE_SCREEN)
crashlog.mark_running()
prefs.settings().setValue("試験/値", "書いた")
launcher.fast_exit()
print("ここには来ない")
"""
    env = {**os.environ, "LOCALAPPDATA": str(tmp_path / "appdata")}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env=env, timeout=120, check=False)  # fmt: skip
    assert out.returncode == 0 and "ここには来ない" not in out.stdout
    ini = next((tmp_path / "ini").rglob("*.ini")).read_text("utf-8")
    assert "書いた" in ini
    assert list((tmp_path / "appdata" / "SumikeshiStudio" / "running").iterdir()) == []
    log = (tmp_path / "appdata" / "SumikeshiStudio" / "crash.log").read_text("utf-8")
    assert "exit  role: screen" in log


def test_second_launch_forwards_files(gui_env, tmp_path, monkeypatch):
    """後から起動したツールは、先のツールに開くファイルを伝えて終わる。先のツールがなければ、自分が起動する。
    先のツールが返事をしなければ（終わる途中）、少し待ってから自分が起動する"""
    from PySide6.QtNetwork import QLocalServer

    from sumikeshi_studio import instance

    _, qapp = gui_env
    name = f"sumikeshi-studio-test-forward-{os.getpid()}"
    received = []
    server = instance.Server(name)
    server.message.connect(received.append)
    assert server.listen()
    code = f"""
from PySide6.QtCore import QCoreApplication
from sumikeshi_studio import instance
app = QCoreApplication([])
print(instance.forward(["C:/スキャン/a.pdf"], {name!r}))
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, text=True
    )
    end = time.time() + 60
    while proc.poll() is None:
        qapp.processEvents()
        time.sleep(0.01)
        assert time.time() < end
    assert proc.stdout.read().strip() == "True"
    assert received == [["C:/スキャン/a.pdf"]]
    server.server.close()
    assert not instance.forward([], name)  # 先のツールがない
    silent = QLocalServer()  # つながるが、返事をしない（終わる途中のツールに見立てる）
    assert silent.listen(name)
    monkeypatch.setattr(instance, "RETRY_SECONDS", 1.0)
    start = time.time()
    assert not instance.forward([], name)
    assert time.time() - start < 10
    silent.close()


def test_launcher_handles_message_from_second_launch(new_launcher, files):
    """後から起動したツールからの通知で、今の画面を前に出し、ファイルを墨消しの画面で開く。様式の編集の画面
    なら、墨消しの画面で開くよう知らせる"""
    lw = new_launcher()
    lw.handle_message([])  # 前に出すだけ
    assert lw.isVisible() and lw.child is None
    lw.handle_message([str(files[0])])  # 起動の画面なら、墨消しの画面を開いてから開く
    win = lw.child
    assert isinstance(win, app.MainWindow)
    win.shown, win.qapp = lw.shown, lw.qapp
    wait(lw, lambda: win.docs and all(st.ready for st in win.pages))
    assert [d.path for d in win.docs] == [files[0]]
    lw.handle_message(
        [str(files[1])]
    )  # 墨消しの画面なら、そこで開く（確認には「はい」）
    wait(lw, lambda: [d.path for d in win.docs] == [files[1]])
    win.close()
    forms = open_forms_window(lw)
    lw.handle_message([str(files[0])])
    assert lw.shown[-1].startswith("ファイルは墨消しの画面で開いてください。")
    assert lw.child is forms
    forms.close()


def test_message_waits_while_exporting(new_launcher, files):
    """墨消しの画面が出力している途中に、後から起動したツールから届いたファイルは、出力が終わるまで開かない（起動の
    画面は、墨消しの画面の出力の流れ（export_flow）が持つ exporting を読む）"""
    lw = new_launcher()
    lw.handle_message([str(files[0])])
    win = lw.child
    win.shown, win.qapp = lw.shown, lw.qapp
    wait(lw, lambda: win.docs and all(st.ready for st in win.pages))
    win.export_flow.exporting = True
    lw.handle_message([str(files[1])])
    start = time.time()
    while time.time() - start < launcher.MESSAGE_RETRY_MS * 3 / 1000:  # 待ち直す3回分
        lw.qapp.processEvents()
        time.sleep(0.02)
    assert [d.path for d in win.docs] == [files[0]]
    win.export_flow.exporting = False
    wait(lw, lambda: [d.path for d in win.docs] == [files[1]])
    win.close()


def test_newer_format_forms_cannot_be_changed(window, tmp_path):
    """新しいバージョンのツールで保存された様式は、墨消しに使えても、編集・置き換え・削除のどれも
    できず、理由を示す"""
    folder = use_forms_folder(tmp_path)
    newer = formstore.FORMAT + 1
    write_form(folder, "u-0002", "新しい版の様式", base="nta-hoken-r8",
               format=newer, usable_from_format=1)  # fmt: skip
    write_form(
        folder,
        "u-0003",
        "読めない新しい版の様式",
        format=newer,
        usable_from_format=newer,
    )
    dlg = list_dialog(window)
    newer = "使える（新しいバージョンで保存。このバージョンでは編集できません）"
    assert row_cells(dlg, "u-0002")[7] == f"✓ {newer}"
    select(dlg, "u-0002")
    buttons = (dlg.b_edit, dlg.b_replace, dlg.b_delete)
    assert not any(b.isEnabled() for b in buttons)
    assert dlg.status.text() == formstore.NEWER_FORM
    select(dlg, "u-0003")  # 読めない、新しいバージョンのツールで保存した様式も消さない
    assert not dlg.b_delete.isEnabled() and dlg.status.text() == formstore.NEWER_FORM
    select(dlg, "u-0002")
    select(dlg, "nta-fuyou-r9")
    assert dlg.b_edit.isEnabled() and dlg.status.text() == ""
