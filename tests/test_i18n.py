# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""画面の言語と、英語の表から文を引く関数のテスト"""

import sys

import pytest

from sumikeshi_studio import i18n, i18n_en

TABLE = {
    "ファイルを開く": "Open files",
    "様式「{name}」を削除しますか？": 'Delete the form "{name}"?',
    "{count}ページ": ("{count} page", "{count} pages"),
    ("ボタン", "編集"): "Edit",
    ("題名", "編集"): "Editing",
}


@pytest.fixture
def table(monkeypatch):
    monkeypatch.setattr(i18n_en, "EN", TABLE)


def test_japanese_returns_text(table):
    """日本語の画面では、英語の表があっても引数の文を使う。値を渡したときだけ { } に値を入れる"""
    assert i18n.language() == i18n.JA
    assert i18n.tr("ファイルを開く") == "ファイルを開く"
    assert i18n.tr("様式「{name}」を削除しますか？") == "様式「{name}」を削除しますか？"
    assert (
        i18n.tr("様式「{name}」を削除しますか？", name="源泉")
        == "様式「源泉」を削除しますか？"
    )
    assert i18n.tr("{count}ページ", count=3) == "3ページ"
    assert i18n.tr("編集", context="ボタン") == "編集"


def test_english_uses_table(table):
    """英語の画面では英語の表から引く。文脈は (文脈, 文) の組で引き、数で単数と複数を選ぶ。表にない文は日本語のまま"""
    i18n.set_language(i18n.EN)
    assert i18n.tr("ファイルを開く") == "Open files"
    assert (
        i18n.tr("様式「{name}」を削除しますか？", name="源泉")
        == 'Delete the form "源泉"?'
    )
    assert i18n.tr("{count}ページ", count=1) == "1 page"
    assert i18n.tr("{count}ページ", count=2) == "2 pages"
    assert i18n.tr("{count}ページ", count=0) == "0 pages"
    assert i18n.tr("編集", context="ボタン") == "Edit"
    assert i18n.tr("編集", context="題名") == "Editing"
    # 文脈のある文は、文脈のない鍵では引かない（文脈を付けた文が表になければ日本語のまま）
    assert i18n.tr("編集") == "編集"
    assert i18n.tr("編集", context="一覧") == "編集"
    assert i18n.tr("表にない{n}件", n=2) == "表にない2件"


def test_set_language_rejects_unknown():
    with pytest.raises(ValueError):
        i18n.set_language("fr")
    assert i18n.language() == i18n.JA


def test_field_names():
    """{ } の名前を出てくる順に返す（{{ }} は文字なので数えない）"""
    assert i18n.field_names("{a}と{b:>3}と{a}と{{c}}") == ["a", "b", "a"]
    assert i18n.field_names("値のない文") == []


def test_choose_language(monkeypatch):
    """環境変数、設定に選んだ言語、Windows の表示の言語の順に決める。知らない値は使わない"""
    monkeypatch.setattr(i18n, "system_language", lambda: i18n.EN)
    monkeypatch.setenv(i18n.LANGUAGE_ENV, "ja")
    assert i18n.choose_language("en") == i18n.JA
    monkeypatch.delenv(i18n.LANGUAGE_ENV)
    assert i18n.choose_language("ja") == i18n.JA
    assert i18n.choose_language(None) == i18n.EN
    monkeypatch.setenv(i18n.LANGUAGE_ENV, "fr")
    assert i18n.choose_language("de") == i18n.EN


@pytest.mark.parametrize(
    ("language_id", "expected"),
    [(0x0411, i18n.JA), (0x0409, i18n.EN), (0x0809, i18n.EN), (0x0004, i18n.EN)],
)
def test_system_language_on_windows(monkeypatch, language_id, expected):
    """Windows では、表示の言語の番号の主な言語（下の10ビット）が日本語（0x11）なら日本語"""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(i18n, "_ui_language_id", lambda: language_id)
    assert i18n.system_language() == expected


def test_system_language_on_windows_without_function(monkeypatch):
    """Windows の言語を読めなければ英語"""

    def fail():
        raise OSError("no function")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(i18n, "_ui_language_id", fail)
    assert i18n.system_language() == i18n.EN


def test_system_language_elsewhere(monkeypatch):
    """Windows 以外では、LC_ALL・LC_MESSAGES・LANG の最初にあるもので決める"""
    monkeypatch.setattr(sys, "platform", "linux")
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    assert i18n.system_language() == i18n.EN
    monkeypatch.setenv("LANG", "ja_JP.UTF-8")
    assert i18n.system_language() == i18n.JA
    monkeypatch.setenv("LC_MESSAGES", "C")
    assert i18n.system_language() == i18n.EN
    monkeypatch.setenv("LC_ALL", "ja_JP.UTF-8")
    assert i18n.system_language() == i18n.JA


def test_join_sentences_bullet_and_quoted():
    """語の区切り・文のつなぎ方・箇条書きの記号・引用符は、言語ごとの書き方にする"""
    assert i18n.join(["a", "b"]) == "a、b"
    assert i18n.sentences("一。", "", "二。") == "一。二。"
    assert i18n.bullet("項目") == "・項目"
    assert i18n.quoted(["扶養", "令和9年分"]) == "「扶養」「令和9年分」"
    assert i18n.quoted(["扶養"], "『』") == "『扶養』"
    i18n.set_language(i18n.EN)
    assert i18n.join(["a", "b"]) == "a, b"
    assert i18n.sentences("One.", "", "Two.") == "One. Two."
    assert i18n.bullet("item") == "- item"
    assert i18n.quoted(["扶養", "令和9年分"], "『』") == '"扶養", "令和9年分"'
