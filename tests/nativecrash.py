# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""Windows の ctypes が、C の関数の中で起きた例外（C++ の例外 0xE06D7363 など）を受け止めて作る OSError を、解析の
プロセスで出すための部品（tests/test_workers.py が使う。解析のプロセスは spawn で立ち上がるので、そこで読み込める
モジュールに置く）

Windows の PDFium は、細工した PDF のページを読む途中で C++ の例外を投げることがあり、ctypes はそれを、例外の番号を
winerror（負の数）にした OSError にして返す。この環境では、その OSError を同じ形で作って出す。
"""

CODE = (
    -529697949
)  # 0xE06D7363（MSVC の C++ の例外）を、ctypes が winerror に入れる形（符号付きの数）


def native_error() -> OSError:
    """ctypes が Windows の例外から作るのと同じ形の OSError"""
    error = OSError(f"[WinError {CODE}] Windows Error 0xe06d7363")
    error.winerror = CODE
    return error


def raise_native(*_args) -> None:
    raise native_error()


class NativeErrorPage:
    """読もうとすると native_error を出すページ（解析の依頼と、出力の墨消しの依頼の source に使う）"""

    def __getattr__(self, name: str):
        if name.startswith("__"):  # pickle などが探す名前は、ふつうにないものとして扱う
            raise AttributeError(name)
        raise native_error()
