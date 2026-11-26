# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""ツールのアイコンの元の絵（SVG）から、Windows 用のアイコンのファイル（.ico）と、ツールの画面に付ける画像（PNG）を作る

元の絵は src/sumikeshi_studio/icons/ の2つ：sumikeshi-studio.svg（40画素以上）と sumikeshi-studio-small.svg（32画素以下。
表の線が太く、印に内枠がない。小さいと細い線と内枠がつぶれるため）。作るもの（同じフォルダ）：
- sumikeshi-studio.ico：SIZES の大きさの絵を入れたアイコンのファイル（デスクトップとスタートメニューの
  ショートカット、アプリの一覧が指す）。256画素は PNG の形、それより小さい大きさは BMP（DIB。32ビットの色と透明）の形
- icon-<大きさ>.png：ツールの画面の題名バーとタスク バーに付ける画像（appicon.py が読む）。配布用パッケージの Qt は、
  SVG とアイコンのファイルの形式を読む部品（imageformats）を除いているので、Qt の本体で読める PNG にする

作ったファイルもリポジトリに置く（配布用パッケージを作るときに SVG を描かなくてよいように）。SVG を直したら、この
スクリプトで作り直してコミットする。

使い方（リポジトリの直下で）：
    uv run python tools/make_icons.py
"""

import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sumikeshi_studio import appicon

LARGE = appicon.ICON_DIR / "sumikeshi-studio.svg"
SMALL = appicon.ICON_DIR / "sumikeshi-studio-small.svg"
SMALL_MAX = 32  # この大きさまでは小さい大きさ用の絵を使う
PNG_MIN = 256  # アイコンのファイルに、この大きさ以上は PNG の形で入れる（小さい大きさは DIB の形）


def source(size: int) -> Path:
    return SMALL if size <= SMALL_MAX else LARGE


def render(svg: Path, size: int):
    """SVG を size 画素四方の画像（QImage。ARGB32）に描く"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(str(svg))
    if not renderer.isValid():
        sys.exit(f"SVG を読めません：{svg}")
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter)
    painter.end()
    return image.convertToFormat(QImage.Format.Format_ARGB32)


def png_data(image) -> bytes:
    """画像を PNG のバイト列にする"""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        sys.exit("PNG にできません")
    buffer.close()
    return bytes(data)


def dib_data(image) -> bytes:
    """画像（QImage。ARGB32）を、アイコンのファイルに入れる BMP（DIB）の形にする：BITMAPINFOHEADER（高さは色と透明の
    印の2つ分）、下の行からの BGRA の色（32ビット。透明つき）、下の行からの透明の印（1ビット。透明な画素を 1。行は
    4バイトの倍数）"""
    width, height = image.width(), image.height()
    stride = image.bytesPerLine()
    bits = bytes(image.constBits())
    rows = [bits[y * stride : y * stride + width * 4] for y in range(height)]
    mask_stride = (width + 31) // 32 * 4
    masks = []
    for row in rows:
        mask = bytearray(mask_stride)
        for x in range(width):
            if row[x * 4 + 3] == 0:
                mask[x // 8] |= 0x80 >> (x % 8)
        masks.append(bytes(mask))
    color = b"".join(reversed(rows))
    mask = b"".join(reversed(masks))
    header = struct.pack(
        "<IiiHHIIiiII",
        40,
        width,
        height * 2,
        1,
        32,
        0,
        len(color) + len(mask),
        0,
        0,
        0,
        0,
    )
    return header + color + mask


def ico_data(images: list[tuple[int, bytes]]) -> bytes:
    """大きさと中身（PNG か DIB のバイト列）の組から、アイコンのファイル（.ico）を作る"""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, body = b"", b""
    for size, data in images:
        side = 0 if size >= 256 else size  # 256画素は 0 と書く
        entries += struct.pack(
            "<BBBBHHII", side, side, 0, 0, 1, 32, len(data), offset + len(body)
        )
        body += data
    return header + entries + body


def ico_entries(data: bytes) -> list[tuple[int, bytes]]:
    """アイコンのファイルの中の、大きさと中身のバイト列の組（ico_data の逆）"""
    _, kind, count = struct.unpack_from("<HHH", data, 0)
    if kind != 1:
        raise ValueError("アイコンのファイルではありません")
    found = []
    for i in range(count):
        side, _, _, _, _, _, length, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + 16 * i
        )
        found.append((side or 256, data[offset : offset + length]))
    return found


def ico_image(size: int, image) -> bytes:
    """アイコンのファイルに入れる1つの大きさの中身：PNG_MIN 画素以上は PNG、それより小さい大きさは DIB（小さい大きさは
    DIB の形がふつうで、すべての大きさを PNG の形にしたファイルでは、Windows のショートカットに既定の絵が出たため）"""
    return png_data(image) if size >= PNG_MIN else dib_data(image)


def build() -> dict[Path, bytes]:
    """作るファイルの場所と中身"""
    images = {size: render(source(size), size) for size in appicon.SIZES}
    files = {appicon.png_path(size): png_data(images[size]) for size in appicon.SIZES}
    files[appicon.ICO_PATH] = ico_data(
        [(size, ico_image(size, images[size])) for size in appicon.SIZES]
    )
    return files


def main() -> None:
    for path, data in build().items():
        path.write_bytes(data)
        print(f"{path.relative_to(ROOT)}（{len(data):,}バイト）")


if __name__ == "__main__":
    main()
