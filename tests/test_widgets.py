# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""3つの画面に共通の部品（widgets.py）のテスト"""

import ast
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip(
    "PySide6.QtWidgets", reason="画面の部品を読み込めない環境", exc_type=ImportError
)
from PySide6.QtGui import QColor, QFontDatabase, QPalette

from sumikeshi_studio import i18n, widgets
from sumikeshi_studio.widgets import Tone


@pytest.fixture
def qapp():
    qa = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    original = QtWidgets.QApplication.palette()
    yield qa
    QtWidgets.QApplication.setPalette(original)


def palette(window: str) -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(window))
    return p


def test_label_colors_are_readable_in_light_and_dark():
    """ラベルの色の組は、ライトとダークのどちらでも、文字と背景のコントラスト比が4.5以上"""
    for tone in Tone:
        for dark in (False, True):
            bg, fg = widgets.tone_colors(tone, dark)
            assert widgets.contrast_ratio(bg, fg) >= 4.5, (tone, dark)


def test_contrast_ratio():
    assert widgets.contrast_ratio(QColor("black"), QColor("white")) == pytest.approx(21)
    assert widgets.contrast_ratio(QColor("#777777"), QColor("#777777")) == 1


def test_dark_is_decided_by_window_color(qapp):
    """Windows の設定ではなく、描いているパレットの Window の色で決める"""
    assert not widgets.is_dark(palette("#f3f3f3"))
    assert widgets.is_dark(palette("#202020"))


def test_status_label_follows_light_and_dark(qapp):
    """状態のラベルは、アプリのパレットが変わったら（Windows のライトとダークの切り替え）色を直す"""
    QtWidgets.QApplication.setPalette(palette("#f3f3f3"))
    label = widgets.StatusLabel("✓ 問題なし", Tone.GOOD)
    light_bg, light_fg = widgets.tone_colors(Tone.GOOD, False)
    assert (
        light_bg.name() in label.styleSheet() and light_fg.name() in label.styleSheet()
    )
    QtWidgets.QApplication.setPalette(palette("#202020"))
    qapp.processEvents()
    dark_bg, _ = widgets.tone_colors(Tone.GOOD, True)
    assert dark_bg.name() in label.styleSheet()
    label.set_status("✕ 読めません", Tone.ERROR)
    error_bg, _ = widgets.tone_colors(Tone.ERROR, True)
    assert label.text() == "✕ 読めません" and error_bg.name() in label.styleSheet()


def test_icon_font_is_chosen_in_order():
    """記号の書体は Segoe Fluent Icons、なければ Segoe MDL2 Assets。どちらもなければ記号を使わない"""
    both = ["Segoe UI", "Segoe MDL2 Assets", "Segoe Fluent Icons"]
    assert widgets.icon_font_family(both) == "Segoe Fluent Icons"
    assert widgets.icon_font_family(["Segoe MDL2 Assets"]) == "Segoe MDL2 Assets"
    assert widgets.icon_font_family(["Yu Gothic UI"]) is None


def test_glyph_icon_has_normal_and_disabled_pictures(qapp):
    """記号は、ふだんの絵と押せないときの絵を持つ（この環境に記号の書体はないので、ほかの書体で描いて確かめる）"""
    family = QFontDatabase.families()[0]
    icon = widgets.glyph_icon("rotate_left", family, qapp.palette())
    assert not icon.isNull()
    for mode in (widgets.QIcon.Mode.Normal, widgets.QIcon.Mode.Disabled):
        assert not icon.pixmap(20, 20, mode).isNull()


def test_primary_button_is_default_and_taller(qapp):
    button = QtWidgets.QPushButton("ファイルを開く")
    height = button.sizeHint().height()
    widgets.make_primary(button)
    assert button.isDefault()
    assert button.minimumHeight() >= round(height * 1.5)


def test_primary_button_is_filled_with_accent_only_when_enabled(qapp):
    """主なボタンの色の組：押せるとき（Active・Inactive）は Button をアクセントの色にし、文字はコントラスト比
    4.5以上の色にする。押せないとき（Disabled）は指定しない"""
    group, role = QPalette.ColorGroup, QPalette.ColorRole
    for accent in ("#0078d4", "#ffd700"):  # Windows の既定の青と、明るいアクセントの色
        p = QPalette(qapp.palette())
        for g in (group.Active, group.Inactive):
            p.setColor(g, role.Accent, QColor(accent))
        QtWidgets.QApplication.setPalette(p)
        button = widgets.make_primary(QtWidgets.QPushButton("ファイルを開く"))
        pal = button.palette()
        for g in (group.Active, group.Inactive):
            assert pal.isBrushSet(g, role.Button) and pal.isBrushSet(g, role.ButtonText)
            assert pal.color(g, role.Button) == QColor(accent)
            assert (
                widgets.contrast_ratio(
                    pal.color(g, role.Button), pal.color(g, role.ButtonText)
                )
                >= 4.5
            )
        assert not pal.isBrushSet(group.Disabled, role.Button)
        assert not pal.isBrushSet(group.Disabled, role.ButtonText)
    assert pal.color(group.Active, role.ButtonText) == QColor(
        "black"
    )  # 明るいアクセントの色には黒の文字


def test_heading_and_note_sizes(qapp):
    base = QtWidgets.QLabel("本文").font().pointSizeF()
    head = widgets.heading("墨消しスタジオ")
    small = widgets.note("バージョン 0.1.0")
    assert head.font().bold() and head.font().pointSizeF() > base
    assert small.font().pointSizeF() < base
    assert small.foregroundRole() == QPalette.ColorRole.PlaceholderText


def test_confirm_box_defaults_to_no(qapp):
    """確認のメッセージの既定のボタン（Enter）と Esc のボタンは、「しない」側"""
    box, yes = widgets.confirm_box(
        None,
        "墨消しスタジオ",
        "問い",
        "補足",
        "チェックを外す",
        "チェックを外さない",
    )
    assert yes.text() == "チェックを外す"
    assert box.defaultButton().text() == "チェックを外さない"
    assert box.escapeButton() is box.defaultButton()


def test_confirm_and_choice_buttons_have_names_for_automation(qapp, monkeypatch):
    """確認のメッセージの「する」「しない」と、3つ以上の選択肢のボタンは、言語によらない名前を持つ（Windows の上の
    自動の確認で見分けるため）"""
    box, yes = widgets.confirm_box(None, "墨消しスタジオ", "問い", "", "する", "しない")
    assert yes.objectName() == "accept"
    assert box.escapeButton().objectName() == "reject"
    names = []

    def exec_(box):
        names.extend(b.objectName() for b in box.buttons())
        return 0

    monkeypatch.setattr(widgets.QMessageBox, "exec", exec_)
    widgets.choose(None, "墨消しスタジオ", "問い", "", ("一", "二", "三"))
    assert sorted(names) == ["choice_0", "choice_1", "choice_2"]


def test_confirm_box_can_default_to_yes(qapp):
    """default_yes なら、既定のボタン（Enter）は「する」側。Esc は「しない」側のまま"""
    box, yes = widgets.confirm_box(
        None, "墨消しスタジオ", "問い", "", "入れる", "入れない", True
    )
    assert box.defaultButton() is yes
    assert box.escapeButton().text() == "入れない"


def test_ask_has_own_yes_no_buttons(qapp, monkeypatch):
    """「はい」「いいえ」の確認（widgets.ask）は、ツールが付けたボタン（日本語の画面では「はい」「いいえ」、英語の画面
    では「Yes」「No」）で尋ねる。既定のボタン（Enter）と Esc は「いいえ」で、「はい」のときだけ True。ボタンには
    言語によらない名前（accept・reject）がある"""
    box, yes = widgets.ask_box(None, "墨消しスタジオ", "問い")
    no = box.escapeButton()
    assert (box.text(), yes.text(), no.text()) == ("問い", "はい", "いいえ")
    assert box.defaultButton() is no
    assert (yes.objectName(), no.objectName()) == ("accept", "reject")
    assert box.standardButtons() == widgets.QMessageBox.StandardButton.NoButton
    box.deleteLater()
    i18n.set_language(i18n.EN)
    try:
        box, yes = widgets.ask_box(None, "Sumikeshi Studio", "Question")
        assert (yes.text(), box.escapeButton().text()) == ("Yes", "No")
        box.deleteLater()
    finally:
        i18n.set_language(i18n.JA)
    for press, want in (("accept", True), ("reject", False)):

        def exec_(box, press=press):
            next(b for b in box.buttons() if b.objectName() == press).click()
            return 0

        monkeypatch.setattr(widgets.QMessageBox, "exec", exec_)
        assert widgets.ask(None, "墨消しスタジオ", "問い") is want


def test_yes_no_questions_use_own_buttons():
    """src では Qt の標準のボタンで尋ねる確認（QMessageBox.question）を使わない（Qt の翻訳を読み込んでいないので、
    標準のボタンの文は日本語の画面でも英語になる。「はい」「いいえ」は widgets.ask で尋ねる）"""
    src = Path(widgets.__file__).resolve().parent
    found = sorted(
        p.name
        for p in src.rglob("*.py")
        if any(
            isinstance(node, ast.Attribute) and node.attr == "question"
            for node in ast.walk(ast.parse(p.read_text("utf-8")))
        )
    )
    assert found == []
