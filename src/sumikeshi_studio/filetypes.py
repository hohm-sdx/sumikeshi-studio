# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""開ける PDF・画像のファイルの形式（拡張子と、画像の中身の形式）と、ファイルを選ぶダイアログの形式の文字列

画像を開く処理（imaging）と、ファイルを選ぶダイアログ（起動の画面・墨消しの画面・様式の画面）が使う。imaging は
重いライブラリを読み込むので、画面のプロセスが起動してすぐに読み込めるよう、一覧はここに置く。
"""

from .i18n import tr

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")
# 画像の中身として開く形式（Pillow の形式の名前）。Pillow は拡張子でなく中身で形式を決め、拡張子が .jpg の WebP・
# JPEG 2000・AVIF なども開くので、開く形式を拡張子の形式に限る（使わない形式の符号化のライブラリの脆弱性を通さない
# ため）。複数の画像を持つ JPEG（MPO 形式。スマートフォンの写真）は、Pillow が JPEG の形式として開く（開いた後の
# 形式の名前は MPO）。MPO は開く形式として登録されていないので、一覧に入れると開くときに KeyError になる
IMAGE_FORMATS = ("JPEG", "PNG", "TIFF", "BMP")
SUPPORTED_SUFFIXES = (".pdf", *IMAGE_SUFFIXES)


def file_filter() -> str:
    """ファイルを選ぶダイアログ（QFileDialog）の形式。開ける形式の一覧から作る（形式の名前を、呼んだときの
    画面の言語の文にする）"""
    return tr(
        "PDF・画像 ({patterns})", patterns=" ".join(f"*{s}" for s in SUPPORTED_SUFFIXES)
    )
