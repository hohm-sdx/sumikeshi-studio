# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""ツールの部品（.pyd・.dll）を Windows が読み込めるかを、1つずつ確かめる

スマート アプリ コントロールや会社のアプリケーション制御は、署名がなく判定できない部品を、読み込むときに止める。
止められると、Python の import は「DLL load failed while importing …」の例外になり、どのファイルが止められたかが
分からない。導入（install.ps1）のときに、python フォルダと site-packages の .pyd・.dll を1つずつ読み込み、
止められた部品を、分かる文で示す。

読み込みは LoadLibraryExW の DONT_RESOLVE_DLL_REFERENCES で行う（その部品が使う DLL は読み込まず、初めの処理も
動かさない）。止められたかは、Windows のエラーの番号 4551（ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION）で見分ける。
この指定でも、スマート アプリ コントロールは判定する（Windows の PC での試験で、pypdfium2 5.15.1 の pdfium.dll を
4551 で読み込めず、setup.bat がその部品を示した）。

使い方：python.exe -I -m sumikeshi_studio.nativecheck
止められた部品がなければ 0、ツールが使わない部品だけなら 10、ツールが使う部品が止められたら 20 で終わる
（Python が例外で終わったときの 1 と分けるため）。文の言語は、導入（install.ps1）が環境変数
SUMIKESHI_STUDIO_LANGUAGE に入れた言語にする。
"""

import ctypes
import re
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from . import i18n
from .i18n import tr

POLICY_BLOCKED = 4551  # ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION
NONE_BLOCKED, ONLY_UNUSED, USED_BLOCKED = 0, 10, 20  # 終わるときの番号
DONT_RESOLVE_DLL_REFERENCES = 0x1
SUFFIXES = (".pyd", ".dll")
SITE = "Lib/site-packages"
# ツールが読み込まない、署名のない部品（site-packages からのパス）。止められても、ツールは動く
NOT_USED = (
    "numpy/_core/_multiarray_tests.*",
    "numpy/_core/_operand_flag_tests.*",
    "numpy/_core/_rational_tests.*",
    "numpy/_core/_struct_ufunc_tests.*",
    "numpy/_core/_umath_tests.*",
    "numpy/_core/_simd.*",
    "numpy/fft/*",
    "numpy/linalg/lapack_lite.*",
    "numpy/random/*",
    "PIL/_avif.*",
    "PIL/_imagingcms.*",
    "PIL/_imagingmath.*",
    "PIL/_imagingmorph.*",
    "PIL/_imagingtk.*",
    "PIL/_webp.*",
    "google/**",
)
TELL = "この画面のスクリーンショットを撮っておくと、原因の調査に役立つ場合があります。"
# 止められたことを示す文を判別する正規表現（日本語の Windows の文と、英語の Windows の文）
POLICY_PATTERN = r"4551|アプリケーション制御|Application Control"


def parts(python: Path) -> list[Path]:
    """確かめる部品：python フォルダの .pyd・.dll と、site-packages の中の .pyd・.dll"""
    found = [p for p in python.iterdir() if p.suffix.lower() in SUFFIXES]
    site = python / SITE
    if site.is_dir():
        found += [p for p in site.rglob("*") if p.suffix.lower() in SUFFIXES]
    return sorted(p for p in found if p.is_file())


def windows_load(path: Path) -> int:
    """部品を読み込めたら 0、読み込めなければ Windows のエラーの番号を返す（読み込んだものはすぐ放す）"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.LoadLibraryExW.restype = ctypes.c_void_p
    kernel32.LoadLibraryExW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
    handle = kernel32.LoadLibraryExW(str(path), None, DONT_RESOLVE_DLL_REFERENCES)
    if not handle:
        return ctypes.get_last_error()  # type: ignore[attr-defined]
    kernel32.FreeLibrary(handle)
    return 0


def blocked_parts(python: Path, load: Callable[[Path], int]) -> list[Path]:
    """止められた部品（エラーの番号が 4551 のもの）。ほかの理由で読み込めないものは、ここでは扱わない"""
    return [p for p in parts(python) if load(p) == POLICY_BLOCKED]


def label(python: Path, path: Path) -> str:
    """画面に出す部品の名前：「ライブラリ：ファイル名」（python フォルダのものは「Python：ファイル名」）"""
    try:
        rel = path.relative_to(python / SITE)
    except ValueError:
        return tr("{library}：{file}", library="Python", file=path.name)
    return tr(
        "{library}：{file}", library=rel.parts[0].removesuffix(".libs"), file=path.name
    )


def used(python: Path, path: Path) -> bool:
    """ツールが使う部品か（NOT_USED に当たらなければ使う部品とする）"""
    try:
        rel = PurePosixPath(path.relative_to(python / SITE).as_posix())
    except ValueError:
        return True
    return not any(rel.full_match(pattern) for pattern in NOT_USED)


def report(python: Path, blocked: list[Path]) -> tuple[int, str]:
    """終わるときの番号と、画面に出す文"""
    if not blocked:
        return NONE_BLOCKED, ""
    needed = [p for p in blocked if used(python, p)]
    others = [p for p in blocked if not used(python, p)]
    lines = []
    if needed:
        lines += [
            i18n.sentences(
                tr(
                    "Windows のスマート アプリ コントロール（または会社のアプリケーション制御）が、"
                    "ツールが使う次の部品を止めました。このままでは、ツールは起動しないか、途中で止まります。"
                ),
                tr(TELL),
            ),
            *(i18n.bullet(label(python, p)) for p in needed),
        ]
    if others:
        lines += [
            tr(
                "Windows のスマート アプリ コントロール（または会社のアプリケーション制御）が、"
                "ツールが使わない次の部品を止めました。ツールの動きには影響しません。"
            ),
            *(i18n.bullet(label(python, p)) for p in others),
        ]
    return (USED_BLOCKED if needed else ONLY_UNUSED), "\n".join(lines)


def policy_message(error: str) -> str | None:
    """起動の準備の失敗の文が、アプリケーション制御に止められたものなら、画面に出す文を返す"""
    if not re.search(POLICY_PATTERN, error):
        return None
    name = re.search(r"importing ([\w.]+)", error)
    if name:
        text = tr(
            "Windows のスマート アプリ コントロール（または会社のアプリケーション制御）が、"
            "ツールの部品（{name}）を止めました。配布用パッケージの ZIP を展開して setup.bat を実行すると、"
            "止められた部品の名前が出ます。",
            name=name[1],
        )
    else:
        text = tr(
            "Windows のスマート アプリ コントロール（または会社のアプリケーション制御）が、"
            "ツールの部品を止めました。配布用パッケージの ZIP を展開して setup.bat を実行すると、"
            "止められた部品の名前が出ます。"
        )
    return i18n.sentences(text, tr(TELL))


def main() -> int:
    # 導入と同じ言語にする（install.ps1 が環境変数に入れる。Qt を読み込まないので、設定のファイルは読まない）
    i18n.set_language(i18n.choose_language(None))
    if sys.platform != "win32":
        print(tr("Windows ではないため、確かめません"))
        return 0
    python = Path(sys.executable).parent
    code, text = report(python, blocked_parts(python, windows_load))
    print(text or tr("ツールの部品は、すべて読み込めました。"))
    return code


if __name__ == "__main__":
    sys.exit(main())
