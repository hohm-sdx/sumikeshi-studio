# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""ツールのアイコン（題名バーとタスク バー）と、Windows のタスク バーでツールを見分ける名前

アイコンの画像は icons/ の icon-<大きさ>.png（tools/make_icons.py が元の絵の SVG から作る）。配布用パッケージの Qt は
SVG とアイコンのファイルの形式を読む部品を除いているので、Qt の本体で読める PNG を使う。
"""

import sys
from pathlib import Path

ICON_DIR = Path(__file__).resolve().parent / "icons"
# アイコンの大きさ（画素）。Windows の表示の拡大率ごとに、題名バー・タスク バー・エクスプローラーが使う大きさ
SIZES = (16, 20, 24, 32, 40, 48, 64, 256)
# デスクトップとスタートメニューのショートカット、アプリの一覧が指すアイコンのファイル
ICO_PATH = ICON_DIR / "sumikeshi-studio.ico"
# Windows のタスク バーでツールのウィンドウを見分ける名前（AppUserModelID）。付けないと、Windows はプロセスの実行
# ファイル（pythonw.exe）でボタンを見分けるので、ほかの Python のプログラムのウィンドウとまとめられることがあるため
APP_ID = "SumikeshiStudio.SumikeshiStudio"


def png_path(size: int) -> Path:
    return ICON_DIR / f"icon-{size}.png"


def app_icon():
    """ツールのアイコン（QIcon。SIZES の大きさの画像を持つ）"""
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QIcon

    icon = QIcon()
    for size in SIZES:
        icon.addFile(str(png_path(size)), QSize(size, size))
    return icon


def set_app_id() -> bool:
    """このプロセスのウィンドウを、タスク バーで APP_ID のものとして見分けさせる（Windows。ウィンドウを作る前に呼ぶ）。
    できたか（Windows でなければ偽）"""
    if sys.platform != "win32":
        return False
    import ctypes

    try:
        result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except OSError, AttributeError:
        return False
    return result == 0
