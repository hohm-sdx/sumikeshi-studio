# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""Windows のジョブ

画面のプロセスを、起動の始めに「閉じたら中のプロセスを終わらせる」ジョブ（JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE）に
入れる。後から立ち上がる解析のプロセスも、自動で同じジョブに入る。画面のプロセスが終われば（異常終了したときも）、
Windows がジョブの中のプロセスを終わらせる。プロセスの名前や番号（pid）で探して終わらせることはしない（ツールを
2つ起動したときに、もう片方を巻き込まないため。pid は Windows が使い回すことがある）。

注意：ジョブに入ったツールから別のプログラム（例：出力した PDF を開くビューア）を起動すると、そのプログラムも
ツールと一緒に終わる。そうした機能を足すときは、ジョブから外して起動する（CreateProcess の
CREATE_BREAKAWAY_FROM_JOB と、ジョブの JOB_OBJECT_LIMIT_BREAKAWAY_OK）。

Windows 以外（開発用の環境）では、ジョブは使わない。
"""

import ctypes
import sys
from ctypes import wintypes

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
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
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]  # fmt: skip
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return f"could not create a job object (CreateJobObjectW, error {ctypes.get_last_error()})"
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        job,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        # 異常終了の記録に書く文なので英語にする
        return f"could not set the job object limits (SetInformationJobObject, error {error})"
    if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
        # 会社の管理ソフトなどが、すでにプロセスを入れ子にできないジョブに入れていると失敗する
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        return f"could not assign the process to the job object (AssignProcessToJobObject, error {error})"
    _job = job
    return ""
