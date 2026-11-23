# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""画面に出す文の言語（日本語・英語）と、英語の表から文を引く関数

画面に出す文は、日本語の文をそのまま鍵にして、英語の表（i18n_en.EN）から引く。日本語の画面では引数の文を
そのまま使う。文を引くのは画面に出すとき（部品を作るとき・通知を出すとき）にする。モジュールの定数は日本語の
文のまま持つ（読み込むときに英語にすると、言語を決める前に読み込んだモジュールの文が日本語のまま固まり、言語を
切り替えても変わらないため）。

言語は、画面のプロセスの起動のとき（launcher.main）に choose_language で決める。解析のプロセスには、依頼を出す
たびにその時の言語を渡す（analysis.call_in）。Qt を使わない。
"""

import ctypes
import os
import string
import sys

from . import i18n_en

JA, EN = "ja", "en"
LANGUAGES = (JA, EN)
# 開発とテストで言語を決める環境変数（ja・en）。設定と Windows の言語より先に使う
LANGUAGE_ENV = "SUMIKESHI_STUDIO_LANGUAGE"
JAPANESE_PRIMARY = (
    0x11  # Windows の言語の番号（LANGID）の、日本語の主な言語の番号（下の10ビット）
)

_language = JA


def language() -> str:
    """今の言語（JA・EN）"""
    return _language


def set_language(lang: str) -> None:
    """言語を決める（JA・EN のどちらか）"""
    global _language
    if lang not in LANGUAGES:
        raise ValueError(f"unknown language: {lang!r}")
    _language = lang


def tr(text: str, /, context: str = "", count: int | None = None, **values) -> str:
    """text（日本語の文）を、今の言語の文にする

    - context：同じ日本語で、別の英語を当てる文を分ける（例：tr("編集", context="ボタン")）。英語の表は
      (context, text) の組で引く。context がなければ text で引く。
    - count：英語の表の値が（単数, 複数）の組なら、count が 1 のときに単数を使う。文の中では {count} で使える。
    - values：文の中の {name} に入れる値。値を渡したときだけ format する（値のない文の { } は、そのまま出す）。
      text は位置だけで渡すので、値の名前に text も使える。context と count は値の名前に使えない。
    英語の表にない文は、英語の画面でも日本語のまま出す。
    """
    found = text
    if _language == EN:
        entry = i18n_en.EN.get((context, text) if context else text)
        if isinstance(entry, tuple):
            entry = entry[0] if count == 1 else entry[1]
        if entry is not None:
            found = entry
    if count is not None:
        values["count"] = count
    return found.format(**values) if values else found


def join(items) -> str:
    """語を並べる（日本語は「、」、英語は「, 」で区切る）"""
    return ("、" if _language == JA else ", ").join(items)


def sentences(*texts: str) -> str:
    """文を続ける（日本語は間を空けず、英語は空白を1つ挟む）。空の文は除く"""
    return ("" if _language == JA else " ").join(t for t in texts if t)


def gap() -> str:
    """語と語の間の空き（日本語は全角の空白、英語は空白2つ。一覧の行のラベルの間、ページの上の帯の見出しの後など）"""
    return "\u3000" if _language == JA else "  "


def bullet(text: str) -> str:
    """箇条の1行（日本語は行頭に「・」、英語は「- 」）"""
    return tr("・{item}", item=text)


def quoted(items, brackets: str = "「」") -> str:
    """名前を引用符で囲んで並べる（日本語は brackets で囲んで続け（例：「a」「b」）、英語は "a", "b"）"""
    if _language == JA:
        return "".join(f"{brackets[0]}{x}{brackets[1]}" for x in items)
    return ", ".join(f'"{x}"' for x in items)


def field_names(text: str) -> list[str]:
    """文の中の {name} の名前（出てくる順。同じ名前が2回あれば2つ）。英語の表の検査に使う"""
    return [
        name for _, name, _, _ in string.Formatter().parse(text) if name is not None
    ]


def _ui_language_id() -> int:
    """Windows の表示の言語の番号（GetUserDefaultUILanguage。テストでは置き換える）"""
    return ctypes.WinDLL("kernel32").GetUserDefaultUILanguage()


def system_language() -> str:
    """Windows の表示の言語が日本語なら JA、それ以外は EN。Windows 以外（開発用の環境）は、環境変数の
    LC_ALL・LC_MESSAGES・LANG で同じように決める"""
    if sys.platform == "win32":
        try:
            primary = _ui_language_id() & 0x3FF
        except Exception:  # noqa: BLE001  関数がないなど。英語にする
            return EN
        return JA if primary == JAPANESE_PRIMARY else EN
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if value := os.environ.get(name):
            return JA if value.startswith("ja") else EN
    return EN


def choose_language(saved: str | None) -> str:
    """起動のときの言語：環境変数 SUMIKESHI_STUDIO_LANGUAGE、設定に選んだ言語（saved）、Windows の表示の言語の順"""
    for lang in (os.environ.get(LANGUAGE_ENV), saved):
        if lang in LANGUAGES:
            return lang
    return system_language()
