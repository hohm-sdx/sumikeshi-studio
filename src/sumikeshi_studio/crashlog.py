# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""異常終了の記録

pythonw.exe で起動すると、エラーの出力（sys.stderr）がなく、プロセスが強制終了したときの理由が残らない
（Windows で解析中に画面のウィンドウが消えたときも、イベント ログには、ucrtbase.dll の abort による終了だけが
残った）。プロセスの始めに setup を呼び、%LOCALAPPDATA%\\SumikeshiStudio\\crash.log に、次のものを書く。
- 起動のたびの見出しの行（日時、バージョン、プロセスの役目、pid）
- faulthandler の出力（abort・アクセス違反などのときの、全部のスレッドの Python の処理の場所）
- エラーの出力（pythonw.exe で sys.stderr がないときだけ、ファイルに向ける）
- スレッドの例外と、処理できなかった例外
- Qt の警告・重大なエラー・致命的なエラー（画面のプロセスだけ。install_qt_handler）

記録に入るのは処理の場所とエラーの文で、画像は入れない。ファイルのパスは入りうる（作業ログと同じ扱い）。
画面のプロセスは、閉じる操作をせずに終わったことを次の起動で見分けるため、動いているあいだマーカーのファイルを置く。
"""

import faulthandler
import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

from . import version
from .i18n import tr
from .worklog import log_dir

# ファイルとフォルダの名前
LOG_NAME = "crash.log"
OLD_NAME = "crash_previous.log"  # 大きくなった記録を1つだけ残す名前
MAX_BYTES = (
    1024 * 1024
)  # 画面のプロセスの起動のときに、これを超えていれば新しく書き始める
# 画面のプロセスが動いていることを示すマーカー（<pid>.txt）を置くフォルダ
RUNNING_DIR = "running"

# 記録に書く文は英語
# プロセスの役目
ROLE_SCREEN = "screen"
ROLE_ANALYSIS = "analysis"
ROLE_COMMAND = "command"  # コマンドラインからの出力（画面を出さない）

QT_LEVELS = {  # Qt のメッセージの種類の名前（QtMsgType の名前 → 記録に書く名前）
    "QtWarningMsg": "warning",
    "QtCriticalMsg": "critical error",
    "QtFatalMsg": "fatal error (the process will abort)",
}

_file = None  # 記録のファイル（faulthandler が書くので、開いたままにする）
_qt_handler = (
    None  # Qt のメッセージを受け取る関数（Qt に渡したものを手放さないように持つ）
)


def log_path() -> Path:
    return log_dir() / LOG_NAME


def _now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


# Windows のエラー報告（WER）の報告に、ヒープを含めない（WerSetFlags の WER_FAULT_REPORTING_FLAG_NOHEAP）
WER_NOHEAP = 0x1


def _kernel32():
    """Windows の kernel32（テストでは置き換える）"""
    import ctypes

    return ctypes.WinDLL("kernel32")


def exclude_heap_from_error_report() -> str:
    """Windows のエラー報告（WER）が、このプロセスの報告にヒープを含めないようにする。
    起動の行に書く文を返す（Windows 以外では何もせず、空を返す）

    墨消しする前の画像をディスクに書かない決まりがあり、強制終了したときに WER が保存・送信する報告に、その画像が
    置かれたメモリが入らないようにするため。WerSetFlags はこのプロセスだけに効く（レジストリの設定は変えない。
    レジストリは実行ファイルの名前ごとか PC 全体に効き、ほかのプログラムにも及ぶため）。今の値を読み、
    WER_NOHEAP を足して設定し、読み直した値を返す文に入れる。失敗してもツールは止めない。
    """
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        k = _kernel32()
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.WerGetFlags.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k.WerGetFlags.restype = ctypes.c_long  # HRESULT
        k.WerSetFlags.argtypes = [wintypes.DWORD]
        k.WerSetFlags.restype = ctypes.c_long
        process = k.GetCurrentProcess()
        flags = wintypes.DWORD(0)
        # まだ一度も設定していないと、読めないことがある。そのときは 0 から足す（ほかに設定するものはない）
        if k.WerGetFlags(process, ctypes.byref(flags)) != 0:
            flags.value = 0
        hr = k.WerSetFlags(flags.value | WER_NOHEAP)
        if hr != 0:
            return f"error report: could not set the flags (0x{hr & 0xFFFFFFFF:08X})"
        after = wintypes.DWORD(0)
        hr = k.WerGetFlags(process, ctypes.byref(after))
        if hr != 0:
            return f"error report: set the flags but could not read them back (0x{hr & 0xFFFFFFFF:08X})"
        if not after.value & WER_NOHEAP:
            return (
                f"error report: could not set the flags (read back 0x{after.value:X})"
            )
        return f"error report: heap excluded (flags 0x{after.value:X})"
    except Exception as e:  # noqa: BLE001  関数がないなど。ツールは止めない
        return f"error report: could not set the flags ({type(e).__name__}: {e})"


def setup(role: str) -> Path | None:
    """プロセスの始めに呼ぶ。Windows のエラー報告にヒープを含めないようにし（画像を読む前）、記録のファイルを
    開いて見出しの行を書き、異常終了の理由が残るようにする

    記録のファイルを開けなければ、記録は書かない（ツールの動作は止めない）。開いた記録のパスを返す。
    """
    global _file
    if _file is not None:
        return log_path()
    report = exclude_heap_from_error_report()
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 大きくなりすぎないよう、画面のプロセスの起動のときに、前のものを1つだけ残して新しく書き始める
        # （解析などのプロセスは、画面のプロセスが起動した後に始まるので、ここでは名前を変えない）
        if role == ROLE_SCREEN and path.exists() and path.stat().st_size > MAX_BYTES:
            try:
                os.replace(path, path.with_name(OLD_NAME))
            except (
                OSError
            ):  # 名前を変えられない（ほかのプロセスが開いているなど）。そのまま書き足す
                pass
        # faulthandler が書き続けるので、with で閉じずに開いたままにする
        _file = open(  # noqa: SIM115
            path, "a", encoding="utf-8", errors="backslashreplace", buffering=1
        )
    except OSError:
        return None
    write(
        f"==== {_now()} start  version: {version.version_text()}  role: {role}  pid: {os.getpid()}"
        f"  Python {sys.version.split()[0]}" + (f"  {report}" if report else "")
    )
    faulthandler.enable(file=_file, all_threads=True)
    if (
        sys.stderr is None
    ):  # pythonw.exe：エラーの出力（Fatal Python error の文などを含む）をファイルに向ける
        try:
            os.dup2(_file.fileno(), 2)
        except OSError:
            pass
        sys.stderr = _file
    _install_hooks()
    return path


def flush() -> None:
    """記録を書き終える（後始末を飛ばしてプロセスを終える前に呼ぶ）"""
    if _file is None:
        return
    try:
        _file.flush()
        os.fsync(_file.fileno())
    except OSError, ValueError:
        pass


def stderr_redirected() -> bool:
    """エラーの出力を記録のファイルに向けている（pythonw.exe）"""
    return _file is not None and sys.stderr is _file


def write(text: str) -> None:
    """記録に1行（以上）書く。書けなくても、ツールの動作は止めない"""
    if _file is None:
        return
    try:
        _file.write(text.rstrip("\n") + "\n")
        _file.flush()
    except OSError, ValueError:
        pass


def write_exception(title: str, exc_type, value, tb) -> None:
    lines = "".join(traceback.format_exception(exc_type, value, tb))
    write(f"---- {_now()} {title} (pid: {os.getpid()})\n{lines}")


def _install_hooks() -> None:
    old_thread_hook = threading.excepthook
    old_unraisable = sys.unraisablehook

    def thread_hook(args):
        name = args.thread.name if args.thread is not None else "unknown"
        write_exception(
            f"exception in thread '{name}'",
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
        )
        if (
            sys.stderr is not _file
        ):  # 開発用の環境では、今までどおりエラーの出力にも出す
            old_thread_hook(args)

    def unraisable_hook(u):
        try:
            what = f"{u.err_msg or 'unraisable exception'}: {u.object!r}"
        except Exception:  # noqa: BLE001  repr に失敗しても記録は残す
            what = u.err_msg or "unraisable exception"
        write_exception(what, u.exc_type, u.exc_value, u.exc_traceback)
        if sys.stderr is not _file:
            old_unraisable(u)

    threading.excepthook = thread_hook
    sys.unraisablehook = unraisable_hook


def install_qt_handler() -> None:
    """Qt の警告・重大なエラー・致命的なエラーを記録に書く（画面のプロセスで、setup の後に呼ぶ）

    致命的なエラー（qFatal。例：「QThread: Destroyed while thread is still running」）は、Qt が理由を書いた後に
    abort するため、ここで書かないと理由が残らない。開発用の環境では、今までどおりエラーの出力にも出す。
    """
    global _qt_handler
    from PySide6.QtCore import qInstallMessageHandler

    def handler(mode, context, message):
        level = QT_LEVELS.get(getattr(mode, "name", ""))
        if level is None:  # 情報・デバッグのメッセージは書かない
            return
        where = ""
        if context is not None and context.file:
            where = f" ({context.file}:{context.line} {context.function or ''})"
        write(f"---- {_now()} Qt {level}: {message}{where}")
        if sys.stderr is not None and sys.stderr is not _file:
            print(message, file=sys.stderr)

    _qt_handler = handler
    qInstallMessageHandler(handler)


# ---------------------------------------------------------------- 前回の異常終了


def abnormal_text(path: Path) -> str:
    """前回、閉じる操作をせずに終わったときに、起動のときに出す文（画面の言語の文）"""
    return tr(
        "前回の起動で、ツールが閉じる操作をしないまま終わりました（ウィンドウが急に消えた場合など）。\n"
        "記録：{path}\n"
        "この記録は、原因の調査に役立つ場合があります。",
        path=path,
    )


def _running_dir() -> Path:
    return log_dir() / RUNNING_DIR


def _started_at(pid: int) -> float | None:
    """動いているプロセス（pid）の起動の時刻。動いていなければ None"""
    import psutil

    try:
        return psutil.Process(pid).create_time()
    except psutil.Error, OSError:
        return None


def previous_abnormal() -> bool:
    """前回までに、閉じる操作をせずに終わった画面のプロセスがあったか（あれば、そのマーカーを消す）

    動いている画面のプロセス（同じ pid で、起動の時刻も同じもの）のマーカーは残す。ツールを2つ起動したときに、
    もう1つのツールを異常終了とみなさないため。
    """
    found = False
    try:
        marks = list(_running_dir().glob("*.txt"))
    except OSError:
        return False
    for mark in marks:
        try:
            pid, started = int(mark.stem), float(mark.read_text("utf-8"))
        except ValueError, OSError:
            pid, started = -1, 0.0
        now = _started_at(pid) if pid > 0 else None
        if now is not None and abs(now - started) < 1.0:
            continue
        found = True
        try:
            mark.unlink()
        except OSError:
            pass
    return found


def mark_running() -> None:
    """画面のプロセスが動いていることを示すマーカーを置く（clear_running で消す）"""
    started = _started_at(os.getpid())
    try:
        _running_dir().mkdir(parents=True, exist_ok=True)
        (_running_dir() / f"{os.getpid()}.txt").write_text(str(started or 0.0), "utf-8")
    except OSError:
        pass


def clear_running() -> None:
    """閉じる操作で終わるときに、マーカーを消し、記録に終わった行を書く"""
    try:
        (_running_dir() / f"{os.getpid()}.txt").unlink(missing_ok=True)
    except OSError:
        pass
    write(f"==== {_now()} exit  role: {ROLE_SCREEN}  pid: {os.getpid()}")
