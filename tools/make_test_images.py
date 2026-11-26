# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""テストの検査用の、ツールが開かない形式の画像（tests/data/試験_中身が*）を作る

中身は WebP・JPEG 2000・AVIF・GIF で、名前の拡張子は、ツールが開ける形式のもの（.jpg・.png）にした小さな画像。
ツールが、開く形式を拡張子でなく中身で見分け、開ける形式（JPEG・MPO・PNG・TIFF・BMP）でない画像を開かないことを
確かめるテストで使う。

作り方を変えたら、このスクリプトで作り直してコミットする。画素は毎回同じだが、バイト列は、Pillow に入っている
符号化のライブラリ（libwebp・OpenJPEG・libavif）のバージョンで変わることがある。

使い方（リポジトリの直下で）:
    uv run python tools/make_test_images.py
"""

import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "data"
# ファイルの名前 → Pillow で書く形式
FORMATS = {
    "試験_中身がWebP.jpg": "WEBP",
    "試験_中身がJPEG2000.jpg": "JPEG2000",
    "試験_中身がAVIF.jpg": "AVIF",
    "試験_中身がGIF.png": "GIF",
}


def picture() -> Image.Image:
    """灰色の地に黒い長方形を1つ描いた、64×48画素の RGB の画像"""
    img = Image.new("RGB", (64, 48), (200, 200, 200))
    ImageDraw.Draw(img).rectangle((16, 12, 31, 23), fill=(0, 0, 0))
    return img


def build() -> dict[str, bytes]:
    """作る画像（ファイルの名前 → バイト列）"""
    made = {}
    for name, kind in FORMATS.items():
        buf = io.BytesIO()
        picture().save(buf, format=kind)
        made[name] = buf.getvalue()
    return made


def main() -> None:
    for name, data in build().items():
        (OUT / name).write_bytes(data)
        print(f"{name}\t{len(data):,}")


if __name__ == "__main__":
    sys.exit(main())
