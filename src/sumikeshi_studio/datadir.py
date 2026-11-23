# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""ツールのデータのフォルダ（作業ログ・異常終了の記録・様式のフォルダの既定の場所）、パスの比べ方、導入の種類

Windows では %LOCALAPPDATA%\\SumikeshiStudio。LOCALAPPDATA がなければ ~/.local/share/SumikeshiStudio。
導入の種類は、ツールを起動した Python の場所で決める：%ProgramFiles%\\SumikeshiStudio の下ならすべてのユーザー向け、
%LOCALAPPDATA%\\Programs\\SumikeshiStudio の下なら自分だけ（windows/scripts/install.ps1 が入れる場所）、どちらでもなければ
展開した場所か開発の環境。標準ライブラリだけを使う。
"""

import ntpath
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

DATA_DIR_NAME = "SumikeshiStudio"
# 導入先のフォルダの名前（windows/scripts/install.ps1 と同じ）
INSTALL_DIR_NAME = "SumikeshiStudio"
ALL_USERS, PER_USER = "all_users", "per_user"  # 導入の種類
# アプリの一覧の鍵（HKCU・HKLM の下。windows/scripts/install.ps1 が書く）
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\SumikeshiStudio"


def _root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) if base else Path.home() / ".local" / "share"


def data_dir() -> Path:
    """データのフォルダ"""
    return _root() / DATA_DIR_NAME


def same_path(a: str, b: str) -> bool:
    """2つのパスが同じ場所を指す（Windows の書き方の違い：大文字と小文字、末尾の区切り、/ と \\ を問わない）"""
    if not a or not b:
        return False
    return ntpath.normcase(ntpath.normpath(a)) == ntpath.normcase(ntpath.normpath(b))


def install_dirs(env: Mapping[str, str] = os.environ) -> dict[str, str | None]:
    """導入の種類ごとの導入先（環境変数がなければ None）"""
    program_files, local = env.get("ProgramFiles"), env.get("LOCALAPPDATA")
    return {
        ALL_USERS: os.path.join(program_files, INSTALL_DIR_NAME)
        if program_files
        else None,
        PER_USER: os.path.join(local, "Programs", INSTALL_DIR_NAME) if local else None,
    }


def is_under(path: str, folder: str) -> bool:
    """path が folder の下にある（Windows の書き方の違いを問わない。folder 自身は含めない）"""
    if not path or not folder:
        return False
    a = ntpath.normcase(ntpath.normpath(path))
    b = ntpath.normcase(ntpath.normpath(folder)).rstrip("\\")
    return a.startswith(b + "\\")


def install_kind(
    executable: str = sys.executable, env: Mapping[str, str] = os.environ
) -> str | None:
    """ツールを起動した Python がある導入先の種類（ALL_USERS・PER_USER）。どちらでもなければ None"""
    for kind, folder in install_dirs(env).items():
        if folder and is_under(executable, folder):
            return kind
    return None


def _per_user_registry() -> str | None:
    """この利用者のアプリの一覧の鍵（HKCU）の InstallLocation。鍵がなければ None（Windows のときだけ読む）"""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "InstallLocation")
    except OSError:
        return None
    return str(value)


def per_user_install(
    env: Mapping[str, str] = os.environ,
    registry: Callable[[], str | None] = _per_user_registry,
) -> str | None:
    """この利用者の自分だけの導入の場所：アプリの一覧の鍵（HKCU）の InstallLocation か、自分だけの導入先の
    フォルダがあればそのパス。どちらもなければ None"""
    if location := registry():
        return location
    folder = install_dirs(env)[PER_USER]
    return folder if folder and Path(folder).is_dir() else None
