# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""利用者ごとの設定（Windows では %APPDATA%\\SumikeshiStudio\\SumikeshiStudio.ini）の項目と読み書き

起動の画面・墨消しの画面・様式の画面が使う。
"""

import os
from pathlib import Path

from PySide6.QtCore import QSettings

from . import formstore, i18n

FORMS_FOLDER_KEY = "forms/folder"  # 起動の画面で指定した様式のフォルダ
# この端末で使わない様式の識別子の一覧。使う様式ではなく使わない様式の一覧にするのは、
# ほかの人が後から足した様式を、この端末でも初めから使うようにするため
FORMS_OFF_KEY = "forms/off_here"
# 覚えておくフォルダ。次に起動したときにも使う
OPEN_FOLDER_KEY = "folders/open"  # 最後に開いたときに見ていた場所
OUTPUT_FOLDER_KEY = "folders/output"  # 最後に出力したフォルダ
# 起動の画面で選んだ画面の言語（ja・en）。選んでいなければ書かず、Windows の表示の言語に合わせる
LANGUAGE_KEY = "ui/language"
# すべてのユーザー向けの導入で、最初の起動でサンプルの様式を入れるかを尋ねた（尋ねなかったが様式があったときも書く）
SAMPLES_OFFERED_KEY = "setup/samples_offered"

SETTINGS_NAME = "SumikeshiStudio"  # 設定のファイルの組織とアプリの名前
# 設定のファイルのパスを替える（Windows の上の自動の確認で、利用者の設定のファイルに書かずにツールを動かすため。
# Qt は Windows で、場所を環境変数 APPDATA でなく既知のフォルダの API（SHGetKnownFolderPath）で決めるので、APPDATA
# では替えられない）
SETTINGS_ENV = "SUMIKESHI_STUDIO_SETTINGS"


def settings() -> QSettings:
    """利用者ごとの設定（Windows では %APPDATA%\\SumikeshiStudio\\SumikeshiStudio.ini。SETTINGS_ENV があれば、その
    パスのファイル）"""
    if path := os.environ.get(SETTINGS_ENV):
        return QSettings(path, QSettings.Format.IniFormat)
    return QSettings(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        SETTINGS_NAME,
        SETTINGS_NAME,
    )


def forms_choice() -> formstore.FolderChoice:
    """様式のフォルダ：起動の画面で指定したフォルダ、なければ既定の場所"""
    value = settings().value(FORMS_FOLDER_KEY, "")
    return formstore.choose_folder(value if isinstance(value, str) else "")


def forms_off() -> frozenset[str]:
    """この端末で使わない様式の識別子（利用者ごとの設定。なくなった様式の識別子も含みうるが、使われない）"""
    value = settings().value(FORMS_OFF_KEY, [])
    if isinstance(value, str):  # 1つだけのときは、文字列として読める
        value = [value] if value else []
    return frozenset(v for v in value or [] if isinstance(v, str) and v)


def set_forms_off(ids) -> None:
    """この端末で使わない様式の識別子を覚える"""
    ids = sorted(set(ids))
    if ids:
        settings().setValue(FORMS_OFF_KEY, ids)
    else:
        settings().remove(FORMS_OFF_KEY)


def saved_language() -> str | None:
    """起動の画面で選んだ画面の言語（i18n.JA・i18n.EN）。選んでいない・知らない値なら None"""
    value = settings().value(LANGUAGE_KEY, "")
    return value if value in i18n.LANGUAGES else None


def save_language(lang: str) -> None:
    """起動の画面で選んだ画面の言語を覚える（次に起動したときも使う）"""
    settings().setValue(LANGUAGE_KEY, lang)


def samples_offered() -> bool:
    """すべてのユーザー向けの導入で、最初の起動のサンプルの様式の問いを済ませたか"""
    return settings().value(SAMPLES_OFFERED_KEY, False) in (True, "true", "1")


def set_samples_offered() -> None:
    """最初の起動のサンプルの様式の問いを済ませたことを覚える（次の起動からは尋ねない）"""
    settings().setValue(SAMPLES_OFFERED_KEY, True)


def remembered_folder(key: str) -> str:
    """覚えているフォルダ。覚えていないか、なくなっていれば空"""
    value = settings().value(key, "")
    return value if isinstance(value, str) and value and Path(value).is_dir() else ""


def restore_geometry(window, key: str) -> None:
    """ウィンドウの大きさと最大化の状態を、利用者ごとの設定から戻す。
    覚えていなければ何もしない。前に使っていた画面がなくなっていれば、Qt が今の画面の中に戻す"""
    value = settings().value(key)
    if value is not None:
        window.restoreGeometry(value)


def remember_geometry(window, key: str) -> None:
    """ウィンドウの大きさと最大化の状態を、利用者ごとの設定に覚える（閉じるときに呼ぶ）"""
    settings().setValue(key, window.saveGeometry())
