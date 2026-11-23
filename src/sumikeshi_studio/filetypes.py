# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""開ける PDF・画像のファイルの形式（拡張子）と、ファイルを選ぶダイアログの形式の文字列

画像を開く処理（imaging）と、ファイルを選ぶダイアログ（起動の画面・墨消しの画面・様式の画面）が使う。imaging は
重いライブラリを読み込むので、画面のプロセスが起動してすぐに読み込めるよう、一覧はここに置く。
"""

from .i18n import tr

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")
SUPPORTED_SUFFIXES = (".pdf", *IMAGE_SUFFIXES)


def file_filter() -> str:
    """ファイルを選ぶダイアログ（QFileDialog）の形式。開ける形式の一覧から作る（形式の名前を、呼んだときの
    画面の言語の文にする）"""
    return tr(
        "PDF・画像 ({patterns})", patterns=" ".join(f"*{s}" for s in SUPPORTED_SUFFIXES)
    )
