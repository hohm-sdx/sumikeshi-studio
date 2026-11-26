# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""Windows のジョブ

画面のプロセスを、起動の始めに「閉じたら中のプロセスを終わらせる」ジョブ（JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE）に
入れる。後から立ち上がる解析のプロセスも、自動で同じジョブに入る。画面のプロセスが終われば（異常終了したときも）、
Windows がジョブの中のプロセスを終わらせる。プロセスの名前や番号（pid）で探して終わらせることはしない（ツールを
2つ起動したときに、もう片方を巻き込まないため。pid は Windows が使い回すことがある）。

解析のプロセスは、立ち上げるときに、確保できるメモリを ANALYSIS_MEMORY_LIMIT に限るジョブにも入る（limit_memory。
画面のプロセスのジョブの中に入れ子にする。入れ子のジョブは Windows 8 から）。細工した PDF で PDFium が部品を展開し
続けても、PC 全体の空きメモリを使い切る前に、そのプロセスの確保が失敗して終わる（そのページは処理できないページに
なる）。画面のプロセスには上限を付けない。

注意：ジョブに入ったツールから別のプログラム（例：出力した PDF を開くビューア）を起動すると、そのプログラムも
ツールと一緒に終わる。そうした機能を足すときは、ジョブから外して起動する（CreateProcess の
CREATE_BREAKAWAY_FROM_JOB と、ジョブの JOB_OBJECT_LIMIT_BREAKAWAY_OK）。

Windows 以外（開発用の環境）では、ジョブは使わない。
"""

import ctypes
import sys
from ctypes import wintypes

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100
# 解析のプロセス1つが確保できるメモリ（バイト。ジョブの上限は、プロセスがコミットしたメモリで数える）。開発の環境
# （Linux・論理 CPU 8）で測った、解析のプロセスと同じ関数を動かしたプロセスの物理メモリの最大（VmHWM）：画素の数が
# 上限（imaging.MAX_PIXELS）の 99.98% の、向き6（右に90度回して見る）のカラーの JPEG（16,384×10,920画素）を出力で
# 墨消しすると、OCR を用意した後で約2.0GB（元の解像度で読んで向きを直すため。傾きを直すときも同じ）。同じ画像の解析は
# 約0.4GB、ふつうのページ（架空の記入の4ページの PDF）の解析は約0.5GB。上限は、出力の約1.5倍。コミットは、物理
# メモリに載った分に、確保しただけで使っていない分を足したもので、その差は測っていない（Windows の試験用の仮想マシンの
# 画面の操作の確認の A9 で、同じ画像のコミットの最大を測る）。Linux のデータの領域（VmData）はスレッドのスタックの予約も
# 数え、論理 CPU の数で増えるが、Windows のスタックは使った分だけをコミットするので、論理 CPU の多い PC でも、上限に
# 近づく量は小さい見込み（推測）。上限を超えたページは、処理できないページになる
ANALYSIS_MEMORY_LIMIT = 3 * 1024**3
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9  # SetInformationJobObject の情報の種類


# 構造体の名前は、Windows の型の名前のままにする
class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_job = None  # ジョブのハンドル。プロセスが終わるまで閉じない（閉じると、中のプロセスが終わる）
_memory_job = None  # 解析のプロセスの、メモリを限るジョブのハンドル（プロセスが終わるまで閉じない）


def active() -> bool:
    """画面のプロセスをジョブに入れた（後始末を飛ばして終えても、子と孫は Windows が終わらせる）"""
    return _job is not None


def enter() -> str:
    """画面のプロセスをジョブに入れる。入れたら空を、入れられなければ理由を返す"""
    global _job
    if _job is not None:
        return ""
    if sys.platform != "win32":
        return "not Windows; a job object is not used"
    job, reason = _new_job(_kernel32(), JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
    _job = job
    return reason


def limit_memory(limit: int = ANALYSIS_MEMORY_LIMIT) -> str:
    """このプロセス（解析のプロセス）を、確保できるメモリを limit バイトに限るジョブに入れる。入れたら空を、入れられ
    なければ理由を返す"""
    global _memory_job
    if _memory_job is not None:
        return ""
    if sys.platform != "win32":
        return "not Windows; a job object is not used"
    job, reason = _new_job(_kernel32(), JOB_OBJECT_LIMIT_PROCESS_MEMORY, limit)
    _memory_job = job
    return reason


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]  # fmt: skip
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32


def _new_job(kernel32, flags: int, process_memory: int = 0):
    """flags の制限（JOB_OBJECT_LIMIT_PROCESS_MEMORY なら、プロセスが確保できるメモリ process_memory）のジョブを作り、
    このプロセスを入れる。(ジョブのハンドル, "") か、入れられなければ (None, 理由)。理由は異常終了の記録に書く文なので
    英語にする"""
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return (
            None,
            f"could not create a job object (CreateJobObjectW, error {ctypes.get_last_error()})",
        )
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = flags
    info.ProcessMemoryLimit = process_memory
    if not kernel32.SetInformationJobObject(
        job,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        return (
            None,
            f"could not set the job object limits (SetInformationJobObject, error {error})",
        )
    if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
        # 会社の管理ソフトなどが、すでにプロセスを入れ子にできないジョブに入れていると失敗する
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        return (
            None,
            f"could not assign the process to the job object (AssignProcessToJobObject, error {error})",
        )
    return job, ""
