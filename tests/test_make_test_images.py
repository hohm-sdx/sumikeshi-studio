# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""tools/make_test_images.py のテスト：tests/data の、開かない形式の画像が、スクリプトの一覧どおりの形式と大きさであること"""

import importlib.util
import io
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "make_test_images", ROOT / "tools" / "make_test_images.py"
)
make_test_images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_test_images)


def test_committed_images_have_listed_formats():
    """tests/data/試験_中身が* は、スクリプトの一覧のファイルだけで、中身はその形式、大きさは作る画像と同じ。名前の
    拡張子は中身の形式と違う（バイト列は、符号化のライブラリのバージョンで変わることがあるので比べない）"""
    data = make_test_images.OUT
    assert sorted(p.name for p in data.glob("試験_中身が*")) == sorted(
        make_test_images.FORMATS
    )
    size = make_test_images.picture().size
    for name, kind in make_test_images.FORMATS.items():
        with Image.open(data / name) as im:
            assert (im.format, im.size) == (kind, size), name
        assert Path(name).suffix in (".jpg", ".png")


def test_build_writes_each_listed_format():
    """スクリプトは、一覧の形式で画像を作る（この環境の Pillow で作れること）"""
    for name, data in make_test_images.build().items():
        with Image.open(io.BytesIO(data)) as im:
            assert im.format == make_test_images.FORMATS[name]
