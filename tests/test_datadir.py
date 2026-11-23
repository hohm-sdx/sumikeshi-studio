# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""データのフォルダとパスの比べ方（datadir.py）のテスト"""

from sumikeshi_studio import datadir


def test_data_dir_is_under_local_app_data(tmp_path, monkeypatch):
    """データのフォルダは LOCALAPPDATA の下の SumikeshiStudio"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert datadir.data_dir() == tmp_path / "SumikeshiStudio"


def test_same_path_ignores_windows_spelling():
    """同じ場所かの判別は、大文字と小文字、末尾の区切り、/ と \\ の違いを問わない。空のパスは同じとしない"""
    base = r"C:\Users\利用者\AppData\Local\SumikeshiStudio\forms"
    for other in (
        base,
        base.upper(),
        base + "\\",
        base.replace("\\", "/"),
        base.lower() + "/",
    ):
        assert datadir.same_path(other, base)
    assert not datadir.same_path(r"\\server\共有\forms", base)
    assert not datadir.same_path("", base)
    assert not datadir.same_path(base, "")


WINDOWS_ENV = {
    "ProgramFiles": r"C:\Program Files",
    "LOCALAPPDATA": r"C:\Users\利用者\AppData\Local",
}


def test_install_kind_from_python_location():
    """ツールを起動した Python の場所で、導入の種類を決める（Windows の書き方の違いを問わない）"""
    all_users = r"C:\Program Files\SumikeshiStudio\python\pythonw.exe"
    per_user = (
        r"C:\Users\利用者\AppData\Local\Programs\SumikeshiStudio\python\pythonw.exe"
    )
    assert datadir.install_kind(all_users, WINDOWS_ENV) == datadir.ALL_USERS
    assert datadir.install_kind(all_users.lower(), WINDOWS_ENV) == datadir.ALL_USERS
    assert datadir.install_kind(per_user, WINDOWS_ENV) == datadir.PER_USER
    # 展開した場所・名前が似た別のフォルダ・環境変数がないとき
    for other in (
        r"C:\tools\sumikeshi-studio\python\pythonw.exe",
        r"C:\Program Files\SumikeshiStudio2\python\pythonw.exe",
    ):
        assert datadir.install_kind(other, WINDOWS_ENV) is None
    assert datadir.install_kind(all_users, {}) is None


def test_per_user_install_from_registry_or_folder(tmp_path):
    """自分だけの導入の場所は、アプリの一覧の鍵（HKCU）の InstallLocation か、自分だけの導入先のフォルダ"""
    env = {"LOCALAPPDATA": str(tmp_path)}
    assert (
        datadir.per_user_install(env, registry=lambda: r"D:\somewhere")
        == r"D:\somewhere"
    )
    assert datadir.per_user_install(env, registry=lambda: None) is None
    folder = tmp_path / "Programs" / "SumikeshiStudio"
    folder.mkdir(parents=True)
    assert datadir.per_user_install(env, registry=lambda: None) == str(folder)
