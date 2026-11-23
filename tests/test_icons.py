# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""ツールのアイコン：元の絵（SVG）と作ったファイル（.ico・PNG）が合っていること、画面とショートカットでの使い方"""

import importlib.util
import struct
from pathlib import Path

import pytest
from gui import QtWidgets

from sumikeshi_studio import appicon

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "make_icons", ROOT / "tools" / "make_icons.py"
)
make_icons = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_icons)


def pixels(data: bytes):
    """PNG のバイト列の、大きさと画素（ARGB）の並び"""
    from PySide6.QtGui import QImage

    image = QImage.fromData(data, "PNG").convertToFormat(QImage.Format.Format_ARGB32)
    return (image.width(), image.height()), bytes(image.constBits())


@pytest.fixture(scope="module")
def built():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return make_icons.build()


def test_icon_files_match_svg(built):
    """リポジトリの PNG とアイコンのファイルは、元の絵の SVG から tools/make_icons.py で作ったものと、画素が同じ"""
    for path, data in built.items():
        assert path.is_file(), path
        if path.suffix == ".png":
            assert pixels(path.read_bytes()) == pixels(data), path.name
    have = make_icons.ico_entries(appicon.ICO_PATH.read_bytes())
    want = make_icons.ico_entries(built[appicon.ICO_PATH])
    assert [s for s, _ in have] == list(appicon.SIZES)
    for (size, got), (_, expected) in zip(have, want, strict=True):
        if size >= make_icons.PNG_MIN:
            assert pixels(got) == pixels(expected) and pixels(got)[0] == (size, size)
        else:
            assert got == expected, size


def test_ico_entries_are_dib_below_256_and_read_like_png():
    """アイコンのファイルの256画素は PNG の形、それより小さい大きさは DIB（32ビットの色と透明）の形で入る。別の読み手
    （Pillow）で読んでも、どの大きさも、同じ大きさの PNG と画素が同じ"""
    from PIL import Image

    for size, data in make_icons.ico_entries(appicon.ICO_PATH.read_bytes()):
        if size >= 256:
            assert data[:8] == b"\x89PNG\r\n\x1a\n"
        else:
            header = struct.unpack_from("<IiiHH", data)
            assert header == (40, size, size * 2, 1, 32), size
    ico = Image.open(appicon.ICO_PATH)
    for size in appicon.SIZES:
        got = ico.ico.getimage((size, size)).convert("RGBA")
        want = Image.open(appicon.png_path(size)).convert("RGBA")
        assert got.tobytes() == want.tobytes(), size


def test_dib_rows_and_mask():
    """DIB は、下の行から BGRA の色を並べ、その後に下の行からの透明の印（透明な画素を 1。行は4バイトの倍数）を並べる"""
    from PySide6.QtGui import QColor, QImage

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    image = QImage(2, 2, QImage.Format.Format_ARGB32)
    image.setPixelColor(0, 0, QColor(255, 0, 0, 255))  # 上の左：赤
    image.setPixelColor(1, 0, QColor(0, 0, 0, 0))  # 上の右：透明
    image.setPixelColor(0, 1, QColor(0, 255, 0, 255))  # 下の左：緑
    image.setPixelColor(1, 1, QColor(0, 0, 255, 128))  # 下の右：半分透明の青
    data = make_icons.dib_data(image)
    assert struct.unpack_from("<IiiHHI", data) == (40, 2, 4, 1, 32, 0)
    color, mask = data[40:56], data[56:]
    assert color == bytes([0, 255, 0, 255, 255, 0, 0, 128, 0, 0, 255, 255, 0, 0, 0, 0])
    assert mask == bytes(
        [0, 0, 0, 0, 0x40, 0, 0, 0]
    )  # 下の行は印なし、上の行は右の画素だけ


def test_small_sizes_use_small_drawing():
    """32画素以下は小さい大きさ用の絵（表の線が太く、印に内枠がない）、40画素以上は大きい大きさ用の絵から作る"""
    assert [make_icons.source(s).name for s in appicon.SIZES] == [
        *(["sumikeshi-studio-small.svg"] * 4),
        *(["sumikeshi-studio.svg"] * 4),
    ]
    # 大きさごとに、その大きさで描いた絵が入る（縮めた絵ではない）
    small = make_icons.png_data(make_icons.render(make_icons.SMALL, 32))
    large = make_icons.png_data(make_icons.render(make_icons.LARGE, 32))
    assert pixels(appicon.png_path(32).read_bytes()) == pixels(small)
    assert pixels(small) != pixels(large)


def test_ico_round_trip():
    """アイコンのファイルの形：大きさと PNG の組を入れて読み戻すと同じ。256画素は 0 と書く"""
    pngs = [(16, b"\x89PNG16"), (256, b"\x89PNG256")]
    data = make_icons.ico_data(pngs)
    assert data[:6] == b"\x00\x00\x01\x00\x02\x00"
    assert data[6 + 16] == 0  # 2つ目の項目の幅（256画素）
    assert make_icons.ico_entries(data) == pngs


def test_app_icon_has_every_size():
    """ツールの画面のアイコンは、PNG（配布用パッケージの Qt で読める形）を SIZES の大きさで持つ"""
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    icon = appicon.app_icon()
    assert not icon.isNull()
    assert sorted(s.width() for s in icon.availableSizes()) == list(appicon.SIZES)
    assert all(appicon.png_path(s).suffix == ".png" for s in appicon.SIZES)


def test_launcher_sets_icon_and_app_id():
    """起動のときに、タスク バーで見分ける名前を、ウィンドウを作る前（QApplication の前）に付け、アプリケーションの
    アイコンを付ける（どの画面も、このアイコンを題名バーとタスク バーに出す）"""
    import inspect

    from sumikeshi_studio import launcher

    main = inspect.getsource(launcher.main)
    assert main.index("appicon.set_app_id()") < main.index("QApplication(sys.argv)")
    assert "app.setWindowIcon(appicon.app_icon())" in main
    assert appicon.set_app_id() is False  # Windows のほかでは何もしない


def test_shortcuts_and_app_list_point_to_icon_file():
    """導入で作るショートカットのアイコンと、アプリの一覧の DisplayIcon は、配布用パッケージの中の
    アイコンのファイル（ツールのパッケージの icons\\sumikeshi-studio.ico）を指す"""
    rel = "python\\Lib\\site-packages\\sumikeshi_studio\\icons\\sumikeshi-studio.ico"
    assert appicon.ICO_PATH.relative_to(ROOT / "src").as_posix() == (
        "sumikeshi_studio/icons/sumikeshi-studio.ico"
    )
    install = (ROOT / "windows" / "scripts" / "install.ps1").read_text("utf-8-sig")
    assert f'$IconFile = "{rel}"' in install
    assert '$Link.IconLocation = "$(Join-Path $Dir $IconFile),0"' in install
    assert '"DisplayIcon"     = "$(Join-Path $Dir $IconFile),0"' in install
