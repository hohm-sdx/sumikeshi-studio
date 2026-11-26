# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""起動の画面のテスト（表示なしで動かす）。共通のフィクスチャは tests/conftest.py に、補助の関数は tests/gui.py にある"""

import sys
import threading

import pytest
from gui import (
    Qt,
    QtCore,
    QTest,
    QtGui,
    QtWidgets,
    ask_with,
    click_box,
    drive_editor,
    first_masked,
    open_and_wait,
    open_forms_window,
    patch_ask,
    select,
    shiboken6,
    wait,
)
from userforms import install_bundled

from sumikeshi_studio import (
    APP_NAME,
    __version__,
    app,
    datadir,
    forminstall,
    formsave,
    formstext,
    formstore,
    i18n,
    launcher,
    prefs,
    version,
    widgets,
)

# ---------------------------------------------------------------- 起動の画面


def settled(lw) -> bool:
    """様式のフォルダを確かめる・様式をコピーする・様式を読むのが、どれも終わった"""
    return not lw.folder_task and not lw.loading


def test_settings_file_can_be_replaced_by_environment(gui_env, tmp_path, monkeypatch):
    """環境変数 SUMIKESHI_STUDIO_SETTINGS があれば、設定はそのパスのファイルに読み書きし、利用者の設定のファイルには
    書かない（Windows の上の自動の確認で、利用者の様式のフォルダの設定を変えないため）"""
    prefs.settings().setValue(prefs.FORMS_FOLDER_KEY, "利用者のフォルダ")
    ini = tmp_path / "試験の設定" / "設定.ini"
    monkeypatch.setenv(prefs.SETTINGS_ENV, str(ini))
    assert prefs.settings().value(prefs.FORMS_FOLDER_KEY) is None
    replaced = prefs.settings()
    replaced.setValue(prefs.FORMS_FOLDER_KEY, "試験のフォルダ")
    replaced.sync()
    assert ini.is_file()
    assert prefs.settings().value(prefs.FORMS_FOLDER_KEY) == "試験のフォルダ"
    monkeypatch.delenv(prefs.SETTINGS_ENV)
    assert prefs.settings().value(prefs.FORMS_FOLDER_KEY) == "利用者のフォルダ"


def test_launcher_lists_forms_and_remembers_use_here(new_launcher, tmp_path):
    """起動の画面に様式の一覧と「使う」を出す。チェックを外すと、確認のメッセージに答えてから利用者ごとの
    設定に覚え、次の起動でも使う"""
    lw = new_launcher()
    assert lw.windowTitle() == APP_NAME  # ウィンドウの題名はツールの名前
    # 様式の既定の場所には、配布用パッケージの3様式をコピーしてある（conftest.gui_env。保存の記録がないので、名前の下は空）
    assert [r[0] for r in lw.rows] == ["nta-hoken-r8", "nta-kihai-r8", "nta-fuyou-r9"]
    assert [lw.row_texts(r) for r in range(3)] == [
        ("保険料控除申告書", "", "✓ 問題なし"),
        ("基・配・特・所の申告書", "", "✓ 問題なし"),
        ("扶養控除等申告書", "", "✓ 問題なし"),
    ]
    table = lw.table
    assert all(table.item(r, 2).checkState() == Qt.CheckState.Checked for r in range(3))
    default = tmp_path / "appdata" / "SumikeshiStudio" / "forms"
    assert lw.folder_label.text() == f"様式のフォルダ：{default}（既定の場所）"
    assert default.is_dir()  # 既定の場所は、なければ作る
    assert lw.open_button.isEnabled() and lw.forms_button.isEnabled()
    assert lw.open_button.isDefault()  # 主なボタン
    assert not lw.default_button.isEnabled()
    assert lw.summary_label.text() == formstext.summary_text(lw.loaded, False, 3)
    table.item(1, 2).setCheckState(Qt.CheckState.Unchecked)
    assert lw.shown[-1] == "「使う」のチェックを外しますか？"
    assert prefs.forms_off() == {"nta-kihai-r8"}
    assert lw.row_texts(1)[2] == "✕ この PC では使わない"
    assert lw.row_labels[1][2].tone is widgets.Tone.ERROR
    assert lw.summary_label.text().startswith("3件のうち2件を使います。")
    again = new_launcher()
    assert again.table.item(1, 2).checkState() == Qt.CheckState.Unchecked


def test_unchecking_use_here_can_be_cancelled(new_launcher, monkeypatch):
    """「使う」のチェックを外すときの確認のメッセージで「チェックを外さない」を選ぶと、チェックは戻り、設定は
    変わらない。メッセージの文には「自動で」を入れる（チェックを外しても、手で範囲を追加すれば墨消しできるため）"""
    lw = new_launcher()
    asked = []
    monkeypatch.setattr(widgets, "confirm", lambda *a, **k: asked.append(a) or False)
    lw.table.item(0, 2).setCheckState(Qt.CheckState.Unchecked)
    (_, _, text, info, yes, no) = asked[0]
    assert text == "「使う」のチェックを外しますか？"
    assert info == (
        "「保険料控除申告書」のチェックを外すと、この PC では、その帳票のページの欄が自動で墨消しされなく"
        "なります。その帳票のページは「様式不明」になり、「⚠ 要確認」が付きます。"
    )
    assert (yes, no) == ("チェックを外す", "チェックを外さない")
    assert lw.table.item(0, 2).checkState() == Qt.CheckState.Checked
    assert prefs.forms_off() == set() and lw.row_texts(0)[2] == "✓ 問題なし"


def test_launcher_blocks_blackout_until_forms_are_readable(
    new_launcher, tmp_path, monkeypatch
):
    """使う様式を読めないとき・様式のフォルダを読めないときは「ファイルを開く」を押せない。読めない
    様式は、この PC で使わないことにすれば押せる。様式のフォルダは起動の画面で指定し、既定の場所に戻せる
    （戻すときは確認のメッセージを出す）"""
    folder = tmp_path / "共有の様式"
    folder.mkdir()
    (folder / "u-broken.json").write_text("{", encoding="utf-8")
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getExistingDirectory", lambda *a, **k: str(folder)
    )
    lw = new_launcher()
    lw.choose_folder()
    wait(lw, lambda: settled(lw))
    assert lw.folder_label.text() == (f"様式のフォルダ：{folder}（選んだフォルダ）")
    assert (
        lw.shown[-1] == "選んだフォルダの様式を使います。"
    )  # 選んだフォルダに様式がある
    assert lw.status.text().startswith("次の様式を読めないため、ファイルを開けません。")
    assert not lw.open_button.isEnabled() and not lw.open_folder_button.isEnabled()
    assert lw.forms_button.isEnabled()
    row = [r[0] for r in lw.rows].index("u-broken")
    assert lw.row_texts(row) == (
        "u-broken.json",
        "",
        "✕ 読めません（ファイルの中身が様式の形になっていません）",
    )
    lw.table.item(row, 2).setCheckState(Qt.CheckState.Unchecked)
    assert lw.open_button.isEnabled() and lw.status.text() == ""
    prefs.settings().setValue(prefs.FORMS_FOLDER_KEY, str(tmp_path / "つながらない"))
    lw.reload()
    wait(lw, lambda: not lw.loading)
    assert lw.status.text().startswith("様式のフォルダを読めないため")
    assert "起動の画面の「変更」で選び直せます" in lw.status.text()
    assert not lw.open_button.isEnabled() and lw.reload_button.isEnabled()
    assert not lw.open_folder_button.isEnabled() and lw.default_button.isEnabled()
    lw.reset_folder()
    wait(lw, lambda: settled(lw))
    assert lw.shown[-1] == "様式のフォルダを既定の場所に戻しますか？"
    assert lw.folder_label.text().endswith("（既定の場所）")
    assert lw.open_button.isEnabled()


def test_reset_folder_can_be_cancelled(new_launcher, tmp_path, monkeypatch):
    """既定の場所に戻すときの確認のメッセージで「戻さない」を選ぶと、様式のフォルダは変わらない"""
    folder = tmp_path / "共有の様式"
    folder.mkdir()
    prefs.settings().setValue(prefs.FORMS_FOLDER_KEY, str(folder))
    lw = new_launcher()
    asked = []
    monkeypatch.setattr(widgets, "confirm", lambda *a, **k: asked.append(a) or False)
    lw.reset_folder()
    wait(lw, lambda: settled(lw))
    assert asked[0][4:] == ("既定の場所に戻す", "戻さない")
    assert prefs.settings().value(prefs.FORMS_FOLDER_KEY) == str(folder)


# ---- 様式のフォルダを替えたとき（「変更」と「既定の場所に戻す」）


def choose_to(monkeypatch, folder):
    """「変更」でフォルダを選ぶ画面が folder を返すようにする"""
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getExistingDirectory", lambda *a, **k: str(folder)
    )


def record(monkeypatch, name, answer=None):
    """widgets.choose・message・confirm を、引数（本文から後）を残して answer を返すものに置き換える"""
    calls = []
    monkeypatch.setattr(
        widgets, name, lambda *a, **k: calls.append((*a[2:], k)) or answer
    )
    return calls


def json_names(folder):
    return sorted(p.name for p in folder.glob("*.json"))


BUNDLED_JSON = ["nta-fuyou-r9.json", "nta-hoken-r8.json", "nta-kihai-r8.json"]


def test_add_samples_button_when_no_forms(new_launcher, tmp_path, monkeypatch):
    """様式がないときだけ「サンプルの様式を入れる」を出す。押すと、入れる様式の名前と年分だけを示して尋ね（既定は
    「入れる」）、入れると答えたら様式のフォルダに入れて読み直す。「入れない」なら何もしない"""
    empty = tmp_path / "空"
    empty.mkdir()
    prefs.settings().setValue(prefs.FORMS_FOLDER_KEY, str(empty))
    asked = record(monkeypatch, "confirm", False)
    lw = new_launcher()
    button = lw.samples_button
    assert button.isVisibleTo(lw) and button.text() == "サンプルの様式を入れる"
    assert (
        "「サンプルの様式を入れる」で、国税庁の様式を入れられます。" in lw.status.text()
    )
    # 出ているときは、Tab キーの順の「読み直す」と「様式を編集」のあいだに入り、焦点があれば Enter キーで押せる
    order, w = [], lw.reload_button.nextInFocusChain()
    while w is not lw.table:
        if w.focusPolicy() & Qt.FocusPolicy.TabFocus and w.isVisibleTo(lw):
            order.append(w)
        w = w.nextInFocusChain()
    assert order == [button, lw.forms_button]
    lw.activateWindow()
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Return)
    assert len(asked) == 1
    asked.clear()
    lw.add_samples()
    text, info = formstext.samples_question(empty, forminstall.sample_items())
    assert asked == [(text, info, "入れる", "入れない", True, {})]
    assert info.splitlines() == [
        "・保険料控除申告書（令和8年分）",
        "・基・配・特・所の申告書（令和8年分）",
        "・扶養控除等申告書（令和9年分）",
    ]
    assert json_names(empty) == []
    record(monkeypatch, "confirm", True)
    lw.add_samples()
    wait(lw, lambda: settled(lw))
    assert json_names(empty) == BUNDLED_JSON
    assert len(lw.rows) == 3 and not button.isVisibleTo(lw)


def test_all_users_first_start_offers_samples(new_launcher, tmp_path, monkeypatch):
    """すべてのユーザー向けの導入から起動したときだけ、この利用者の最初の起動で、様式がなければサンプルの様式を
    入れるかを尋ねる。答えにかかわらず、次からは尋ねない。展開した場所で使う形では尋ねない"""
    empty = tmp_path / "空"
    empty.mkdir()
    prefs.settings().setValue(prefs.FORMS_FOLDER_KEY, str(empty))
    asked = record(monkeypatch, "confirm", False)
    monkeypatch.setattr(datadir, "per_user_install", lambda: None)
    lw = (
        new_launcher()
    )  # 起動した直後の after_first_load は、展開した場所で使う形なので何もしない
    lw.qapp.processEvents()
    assert asked == [] and not prefs.samples_offered()
    monkeypatch.setattr(datadir, "install_kind", lambda: datadir.ALL_USERS)
    lw.after_first_load()
    assert asked == [
        (
            *formstext.samples_question(empty, forminstall.sample_items()),
            "入れる",
            "入れない",
            True,
            {},
        )
    ]
    assert prefs.samples_offered() and json_names(empty) == []
    lw.after_first_load()  # 2回目の起動に当たる
    assert len(asked) == 1


def test_all_users_first_start_with_forms_only_remembers(new_launcher, monkeypatch):
    """様式があれば尋ねず、尋ねたことだけを覚える"""
    asked = record(monkeypatch, "confirm", False)
    monkeypatch.setattr(datadir, "per_user_install", lambda: None)
    lw = new_launcher()
    monkeypatch.setattr(datadir, "install_kind", lambda: datadir.ALL_USERS)
    lw.after_first_load()
    assert asked == [] and prefs.samples_offered()


def test_all_users_start_tells_per_user_install_exists(new_launcher, monkeypatch):
    """すべてのユーザー向けの導入から起動したときに、この利用者の自分だけの導入もあれば知らせる。自分だけの導入や
    展開した場所から起動したときは知らせない"""
    folder = r"C:\Users\利用者\AppData\Local\Programs\SumikeshiStudio"
    monkeypatch.setattr(datadir, "per_user_install", lambda: folder)
    messages = record(monkeypatch, "message")
    lw = new_launcher()
    for kind in (None, datadir.PER_USER):
        monkeypatch.setattr(datadir, "install_kind", lambda k=kind: k)
        lw.after_first_load()
    assert messages == []
    monkeypatch.setattr(datadir, "install_kind", lambda: datadir.ALL_USERS)
    lw.after_first_load()
    assert messages == [
        (
            f"このアカウントには、自分だけの墨消しスタジオもあります（{folder}）。",
            formstext.MIXED_INFO,
            {},
        )
    ]


def test_add_samples_failure_is_shown(new_launcher, tmp_path, monkeypatch):
    """サンプルの様式を入れられなければ、理由を示し、様式のない状態のまま（途中まで入れた分は消す）"""
    empty = tmp_path / "空"
    empty.mkdir()
    prefs.settings().setValue(prefs.FORMS_FOLDER_KEY, str(empty))
    record(monkeypatch, "confirm", True)
    messages = record(monkeypatch, "message")

    def fail(folder, specs, user, warnings):
        raise PermissionError(13, "アクセスが拒否されました。")

    monkeypatch.setattr(forminstall, "save_samples", fail)
    lw = new_launcher()
    lw.add_samples()
    wait(lw, lambda: settled(lw) and messages)
    assert messages == [
        (
            "サンプルの様式を入れられませんでした。途中まで入れた様式は削除しました。",
            "PermissionError: アクセスが拒否されました。",
            {"warning": True},
        )
    ]
    assert json_names(empty) == [] and lw.samples_button.isVisibleTo(lw)


@pytest.mark.parametrize("answer", [0, 1, 2], ids=["copy", "dont-copy", "keep"])
def test_changing_to_empty_folder_asks_to_copy(
    answer, new_launcher, tmp_path, monkeypatch
):
    """選んだフォルダに様式がなく、前のフォルダに様式があれば、前のフォルダの様式をコピーするかを1回尋ねる。
    「コピーする」ならコピーしてから替え、「コピーしない」ならそのまま替え（様式がないことを示す）、「変更しない」なら
    何もしない"""
    new = tmp_path / "新しいフォルダ"
    new.mkdir()
    choose_to(monkeypatch, new)
    asked = record(monkeypatch, "choose", answer)
    lw = new_launcher()
    old = formstore.default_folder()
    lw.choose_folder()
    wait(lw, lambda: settled(lw))
    assert asked == [(*formstext.copy_question(False, old, new, 3), {})]
    folder = prefs.settings().value(prefs.FORMS_FOLDER_KEY)
    if answer == 0:
        assert folder == str(new) and json_names(new) == BUNDLED_JSON
        assert [r[0] for r in lw.rows] == [
            "nta-hoken-r8",
            "nta-kihai-r8",
            "nta-fuyou-r9",
        ]
    elif answer == 1:
        assert folder == str(new) and json_names(new) == [] and lw.rows == []
        assert lw.status.text().startswith("様式のフォルダに様式がありません。")
    else:
        assert folder is None and list(new.iterdir()) == []
        assert len(lw.rows) == 3
    assert json_names(old) == BUNDLED_JSON  # 前のフォルダの様式は消えない


def test_changing_to_folder_with_forms_tells_it_is_used(
    new_launcher, tmp_path, monkeypatch
):
    """選んだフォルダに様式があれば、尋ねずに替え、そのフォルダの様式を使うことを示す"""
    new = tmp_path / "共有"
    install_bundled(new)
    (new / "u-broken.json").write_text("{", encoding="utf-8")  # 読めない様式も数える
    choose_to(monkeypatch, new)
    asked, said = record(monkeypatch, "choose"), record(monkeypatch, "message")
    lw = new_launcher()
    lw.choose_folder()
    wait(lw, lambda: settled(lw))
    assert asked == []
    assert said == [(*formstext.switched_text(formstore.default_folder(), new, 4), {})]
    assert prefs.settings().value(prefs.FORMS_FOLDER_KEY) == str(new)


@pytest.mark.parametrize("case", ["no-forms", "inside", "outside", "same"])
def test_changing_folder_without_question(case, new_launcher, tmp_path, monkeypatch):
    """前のフォルダに様式がない（読めない）とき、選んだフォルダが前のフォルダの中にある・前のフォルダが選んだ
    フォルダの中にあるときは、尋ねずに替える（コピーする元と先が重なるため）。今のフォルダと同じなら何もしない"""
    current = tmp_path / "今" if case != "no-forms" else tmp_path / "空"
    if case != "no-forms":
        install_bundled(current)
    else:
        current.mkdir()
    new = {
        "no-forms": tmp_path / "新",
        "inside": current / "下",
        "outside": tmp_path,
        "same": current,
    }[case]
    new.mkdir(exist_ok=True)
    prefs.settings().setValue(prefs.FORMS_FOLDER_KEY, str(current))
    choose_to(monkeypatch, new)
    asked, said = record(monkeypatch, "choose"), record(monkeypatch, "message")
    lw = new_launcher()
    lw.choose_folder()
    wait(lw, lambda: settled(lw))
    assert asked == [] and said == []
    assert prefs.settings().value(prefs.FORMS_FOLDER_KEY) == str(new)
    assert json_names(new) == (BUNDLED_JSON if case == "same" else [])  # コピーしない


def test_copy_failure_keeps_previous_folder(new_launcher, tmp_path, monkeypatch):
    """コピーに失敗したら、理由を示し、様式のフォルダは前のフォルダのままにする"""
    new = tmp_path / "新しいフォルダ"
    new.mkdir()
    choose_to(monkeypatch, new)
    record(monkeypatch, "choose", 0)
    said = record(monkeypatch, "message")

    def fail(old, dst):
        raise PermissionError(13, "アクセスが拒否されました")

    monkeypatch.setattr(formsave, "copy_forms", fail)
    lw = new_launcher()
    lw.choose_folder()
    wait(lw, lambda: settled(lw))
    assert said == [
        (
            formstext.COPY_FAILED,
            "PermissionError: [Errno 13] アクセスが拒否されました",
            {"warning": True},
        )
    ]
    assert prefs.settings().value(prefs.FORMS_FOLDER_KEY) is None
    assert lw.folder_label.text().endswith("（既定の場所）") and len(lw.rows) == 3


def test_reset_folder_messages(new_launcher, tmp_path, monkeypatch):
    """「既定の場所に戻す」は、1つの確認のメッセージにまとめる：既定の場所に様式があれば、その様式を使うことを示して
    確かめる。既定の場所に様式がなく、今のフォルダに様式があれば、コピーして戻すかを尋ねる。今のフォルダにも様式が
    なければ、今までの確認のメッセージを出す"""
    current = tmp_path / "共有"
    install_bundled(current)
    default = formstore.default_folder()
    prefs.settings().setValue(prefs.FORMS_FOLDER_KEY, str(current))
    confirmed = record(monkeypatch, "confirm", True)
    asked = record(monkeypatch, "choose", 0)
    lw = new_launcher()
    # 既定の場所に様式がある（conftest.gui_env が置いた3様式）
    lw.reset_folder()
    wait(lw, lambda: settled(lw))
    assert asked == []
    assert confirmed[0][:4] == (
        formstext.RESET_QUESTION,
        formstext.reset_info(default, current, 3),
        "既定の場所に戻す",
        "戻さない",
    )
    assert prefs.settings().value(prefs.FORMS_FOLDER_KEY) is None
    # 既定の場所に様式がなく、今のフォルダに様式がある：コピーして戻す
    for p in default.iterdir():
        p.unlink()
    prefs.settings().setValue(prefs.FORMS_FOLDER_KEY, str(current))
    lw.reload()
    wait(lw, lambda: settled(lw))
    lw.reset_folder()
    wait(lw, lambda: settled(lw))
    assert asked == [(*formstext.copy_question(True, current, default, 3), {})]
    assert len(confirmed) == 1
    assert prefs.settings().value(prefs.FORMS_FOLDER_KEY) is None
    assert json_names(default) == BUNDLED_JSON and len(lw.rows) == 3
    # 今のフォルダにも様式がない：今までの確認のメッセージ
    empty = tmp_path / "空"
    empty.mkdir()
    for p in default.iterdir():
        p.unlink()
    prefs.settings().setValue(prefs.FORMS_FOLDER_KEY, str(empty))
    lw.reload()
    wait(lw, lambda: settled(lw))
    lw.reset_folder()
    wait(lw, lambda: settled(lw))
    assert len(asked) == 1
    assert confirmed[1][:2] == (
        formstext.RESET_QUESTION,
        formstext.reset_info(default, empty, 0),
    )


def test_buttons_wait_while_checking_folders(new_launcher, tmp_path, monkeypatch):
    """2つのフォルダの様式を数えているあいだは、起動の画面のボタンを押せず、状態の文に「様式のフォルダを確かめて
    います…」を出す"""
    gate = threading.Event()
    real = launcher.count_both

    def slow(old, new):
        gate.wait(60)
        return real(old, new)

    monkeypatch.setattr(launcher, "count_both", slow)
    new = tmp_path / "新しいフォルダ"
    new.mkdir()
    choose_to(monkeypatch, new)
    record(monkeypatch, "choose", 2)
    lw = new_launcher()
    lw.choose_folder()
    assert lw.status.text() == "様式のフォルダを確かめています…"
    buttons = (lw.open_button, lw.open_folder_button, lw.choose_button, lw.reload_button,
               lw.forms_button, lw.language_box)  # fmt: skip
    assert not any(b.isEnabled() for b in buttons)
    gate.set()
    wait(lw, lambda: settled(lw))
    assert all(b.isEnabled() for b in buttons)


def test_launcher_reads_forms_in_background(new_launcher, monkeypatch):
    """様式のフォルダは裏で読み、読んでいるあいだは「ファイルを開く」を押せない"""
    lw = new_launcher()
    gate, calls = threading.Event(), []
    real = formstore.load

    def slow(choice, off=()):
        calls.append(choice)
        gate.wait(60)
        return real(choice, off)

    monkeypatch.setattr(formstore, "load", slow)
    lw.reload()
    wait(lw, lambda: calls)
    assert lw.status.text() == "様式のフォルダを読んでいます…"
    assert not lw.open_button.isEnabled() and not lw.reload_button.isEnabled()
    assert not lw.open_folder_button.isEnabled() and lw.summary_label.text() == ""
    gate.set()
    wait(lw, lambda: not lw.loading)
    assert lw.open_button.isEnabled()


def test_launcher_state_when_preparation_failed(new_launcher):
    """起動の準備に失敗したら、準備の状態のラベルと一覧の下の文で知らせ、開くボタンは押せるままにする（開くときに
    知らせる）。状態ごとの文と押せるかは、test_formstext.py の test_launcher_conditions で確かめる"""
    lw = new_launcher()
    lw.session.error = "解析のプロセスを起動できませんでした。"
    lw.update_state()
    text, _ = formstext.ready_status(lw.session.error, True)
    assert (lw.ready_label.text(), lw.ready_label.tone) == (text, widgets.Tone.ERROR)
    assert lw.status.text() == formstext.status_message(
        lw.loaded, False, lw.session.error
    )
    assert lw.open_button.isEnabled()


def test_launcher_shows_version_and_readiness(new_launcher, tmp_path, monkeypatch):
    """上に、バージョン（開発用の環境ならそのことも。配布用パッケージでも作った日時は出さない）と、起動の準備の状態を出す"""
    lw = new_launcher()
    assert lw.version_label.text() == f"バージョン {__version__}（開発用の環境）"
    assert lw.ready_label.text() == "✓ 準備ができました"
    assert lw.ready_label.tone is widgets.Tone.GOOD
    python = tmp_path / "python"
    python.mkdir()
    (tmp_path / "version.txt").write_text(
        "Sumikeshi Studio 0.1.0\r\nBuilt: 2026-11-24\r\n", "utf-8-sig"
    )
    monkeypatch.setattr(sys, "base_prefix", str(python))
    assert version.version_label() == f"バージョン {__version__}"
    i18n.set_language(i18n.EN)
    assert version.version_label() == f"Version {__version__}"


def test_open_files_from_launcher(new_launcher, files, monkeypatch):
    """「ファイルを開く」は、起動の画面の上でファイルを選ぶダイアログを出す。選べば墨消しの画面で開き、
    キャンセルなら起動の画面のまま"""
    lw = new_launcher()
    picked = []
    dialog = QtWidgets.QFileDialog
    monkeypatch.setattr(dialog, "getOpenFileNames", lambda *a, **k: (picked.pop(0), ""))
    picked.append([])
    lw.open_button.click()
    assert lw.child is None and lw.isVisible()
    picked.append([str(files[1])])
    lw.open_button.click()
    win = lw.child
    assert isinstance(win, app.MainWindow) and not lw.isVisible()
    win.shown, win.qapp = lw.shown, lw.qapp
    wait(win, lambda: win.pages and all(st.ready for st in win.pages))
    assert win.pages[0].result.form == "保険料控除申告書"
    win.close()
    assert lw.isVisible() and lw.child is None
    wait(lw, lambda: not lw.loading)


def test_enter_key_presses_the_focused_button(new_launcher, monkeypatch):
    """Enter キーは、焦点が主なボタンにあるとき・ボタンや入力の部品にないときだけ「ファイルを開く」を
    押す。ほかのボタンに焦点があれば、そのボタンが押され、ファイルを選ぶダイアログは出ない"""
    lw = new_launcher()
    lw.activateWindow()
    opened, chosen = [], []
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileNames",
        lambda *a, **k: opened.append(a) or ([], ""),
    )
    monkeypatch.setattr(lw, "choose_folder", lambda: chosen.append(True))
    lw.choose_button.clicked.disconnect()
    lw.choose_button.clicked.connect(lw.choose_folder)
    assert QtWidgets.QApplication.focusWidget() is lw.open_button
    QTest.keyClick(lw.open_button, Qt.Key.Key_Return)
    assert len(opened) == 1
    lw.centralWidget().setFocus()  # 焦点がボタンや入力の部品にない
    QTest.keyClick(lw.centralWidget(), Qt.Key.Key_Return)
    assert len(opened) == 2
    lw.choose_button.setFocus()  # ほかのボタン
    QTest.keyClick(lw.choose_button, Qt.Key.Key_Return)
    assert len(opened) == 2 and chosen == [True]
    lw.table.setFocus()  # 入力の部品（表）
    QTest.keyClick(lw.table, Qt.Key.Key_Return)
    assert len(opened) == 2


def test_focus_returns_to_open_button_after_child_closes(new_launcher, monkeypatch):
    """墨消しの画面を閉じて起動の画面に戻り、様式を読み直した後も、焦点は「ファイルを開く」にあり、Enter で
    ファイルを選ぶダイアログが出る。読み直すあいだに主なボタンが押せなくなっても、焦点が Tab の順で次の「変更」に
    移ったまま残らず、「変更」が既定のボタンにならないことを確かめる"""
    lw = new_launcher()
    lw.activateWindow()
    assert lw.focusWidget() is lw.open_button
    lw.open_blackout()  # ファイルを開かずに墨消しの画面を開く
    win = lw.child
    win.shown, win.qapp = lw.shown, lw.qapp
    assert not lw.isVisible()
    win.close()
    assert lw.isVisible() and lw.child is None
    wait(lw, lambda: not lw.loading)
    lw.activateWindow()
    lw.qapp.processEvents()
    assert lw.focusWidget() is lw.open_button
    assert lw.open_button.isDefault() and not lw.choose_button.isDefault()
    opened = []
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileNames",
        lambda *a, **k: opened.append(a) or ([], ""),
    )
    QTest.keyClick(lw.focusWidget(), Qt.Key.Key_Return)
    assert len(opened) == 1


def test_saved_language(gui_env):
    """起動の画面で選んだ言語を設定から読む。選んでいない・知らない値なら None（Windows の表示の言語に合わせる）"""
    assert prefs.saved_language() is None
    prefs.settings().setValue(prefs.LANGUAGE_KEY, i18n.EN)
    assert prefs.saved_language() == i18n.EN
    prefs.settings().setValue(prefs.LANGUAGE_KEY, "fr")
    assert prefs.saved_language() is None


def test_language_box_is_at_right_end_of_title_row(new_launcher):
    """言語を選ぶ欄は、上の行（ツールの名前と起動の準備の状態）の右端に、両方の語のラベルと一緒に置く。
    ほかの画面を開いているあいだと、様式のフォルダを読んでいるあいだは押せない"""
    lw = new_launcher()
    assert lw.windowTitle() == "墨消しスタジオ"
    box = lw.language_box
    row = box.parentWidget().layout().itemAt(0).layout()  # 上の行
    widgets_in_row = [row.itemAt(i).widget() for i in range(row.count())]
    assert widgets_in_row[-1] is box
    assert widgets_in_row[-2].text() == launcher.LANGUAGE_LABEL
    assert widgets_in_row[-2].buddy() is box
    assert widgets_in_row[0].text() == "墨消しスタジオ"  # 見出し
    assert [box.itemText(i) for i in range(box.count())] == ["日本語", "English"]
    assert [box.itemData(i) for i in range(box.count())] == [i18n.JA, i18n.EN]
    assert box.currentData() == i18n.JA and box.isEnabled()
    lw.reload()
    assert lw.loading and not box.isEnabled()
    # 押せないあいだに選ばれても、言語は変えず、欄も元に戻す
    box.setCurrentIndex(1)
    assert i18n.language() == i18n.JA and box.currentData() == i18n.JA
    assert prefs.saved_language() is None
    wait(lw, lambda: not lw.loading)
    assert box.isEnabled()
    lw.open_blackout()
    win = lw.child
    win.shown, win.qapp = lw.shown, lw.qapp
    assert not box.isEnabled()
    win.close()
    assert lw.child is None
    wait(lw, lambda: not lw.loading)
    assert lw.language_box.isEnabled()


def test_switching_language_rebuilds_launcher_at_once(new_launcher, monkeypatch):
    """言語を選ぶと、その場で起動の画面をその言語で作り直し、設定に覚えて、様式のフォルダを読み直す。ウィンドウの
    位置と大きさ、「使う」の状態は変わらず、読み終えたら焦点は「ファイルを開く」にあり、Enter でファイルを
    選ぶダイアログが出る"""
    lw = new_launcher()
    lw.activateWindow()
    lw.table.item(1, 2).setCheckState(Qt.CheckState.Unchecked)  # この PC では使わない
    lw.move(40, 30)
    lw.resize(1000, 700)
    lw.qapp.processEvents()
    geometry = lw.geometry()
    old_button = lw.open_button
    lw.language_box.setCurrentIndex(lw.language_box.findData(i18n.EN))
    assert i18n.language() == i18n.EN
    assert prefs.saved_language() == i18n.EN
    assert lw.windowTitle() == "Sumikeshi Studio"
    assert lw.open_button is not old_button  # 作り直した
    assert lw.language_box.currentData() == i18n.EN
    assert lw.loading  # 様式のフォルダを読み直している
    assert lw.table.rowCount() == 3  # 読み直すあいだも、一覧は空にしない
    wait(lw, lambda: not lw.loading)
    lw.qapp.processEvents()
    row = lw.language_box.parentWidget().layout().itemAt(0).layout()
    assert row.itemAt(0).widget().text() == "Sumikeshi Studio"  # 見出し
    assert lw.geometry() == geometry
    assert [lw.table.item(r, 2).checkState() for r in range(3)] == [
        Qt.CheckState.Checked,
        Qt.CheckState.Unchecked,
        Qt.CheckState.Checked,
    ]
    assert prefs.forms_off() == {"nta-kihai-r8"}
    lw.activateWindow()
    lw.qapp.processEvents()
    assert lw.focusWidget() is lw.open_button
    assert lw.open_button.isDefault()
    opened = []
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileNames",
        lambda *a, **k: opened.append(a) or ([], ""),
    )
    QTest.keyClick(lw.focusWidget(), Qt.Key.Key_Return)
    assert len(opened) == 1
    # 日本語に戻す
    lw.language_box.setCurrentIndex(lw.language_box.findData(i18n.JA))
    wait(lw, lambda: not lw.loading)
    assert lw.windowTitle() == "墨消しスタジオ"
    assert prefs.saved_language() == i18n.JA


# 起動の画面の部品の、自動の確認（Windows の UI オートメーション）で見分けるための名前
LAUNCHER_NAMES = (
    "ready_status",
    "language",
    "version",
    "blackout",
    "open_files",
    "open_folder",
    "forms",
    "forms_folder",
    "change_folder",
    "default_folder",
    "reload_forms",
    "add_samples",
    "edit_forms",
    "forms_table",
    "forms_summary",
    "forms_status",
)


def test_launcher_widgets_have_names_for_automation(new_launcher):
    """起動の画面の主な部品は、言語によらない名前（objectName）を1つずつ持ち、ボタンはどれも重ならない名前を持つ。
    言語を切り替えて部品を作り直しても、同じ名前"""
    lw = new_launcher()
    assert lw.objectName() == "launcher"
    for lang in (i18n.EN, i18n.JA):
        for name in LAUNCHER_NAMES:
            assert len(lw.findChildren(QtWidgets.QWidget, name)) == 1, name
        buttons = lw.centralWidget().findChildren(QtWidgets.QPushButton)
        assert all(b.objectName() for b in buttons)
        assert len({b.objectName() for b in buttons}) == len(buttons)
        lw.language_box.setCurrentIndex(lw.language_box.findData(lang))
        wait(lw, lambda: not lw.loading)
        lw.qapp.processEvents()
        QtCore.QCoreApplication.sendPostedEvents(
            None, QtCore.QEvent.Type.DeferredDelete
        )


def test_launcher_in_english(new_launcher, monkeypatch):
    """英語の画面の起動の画面：ボタン・枠の題名・一覧の見出しと行・様式のフォルダ・件数の文・準備の状態を英語にする。
    確認のメッセージの文も英語にする"""
    i18n.set_language(i18n.EN)
    lw = new_launcher()
    assert lw.windowTitle() == "Sumikeshi Studio"
    assert [lw.open_button.text(), lw.open_folder_button.text()] == [
        "Open files",
        "Open folder",
    ]
    assert [
        b.text()
        for b in (
            lw.choose_button,
            lw.default_button,
            lw.reload_button,
            lw.forms_button,
        )
    ] == [
        "Change",
        "Use default location",
        "Reload",
        "Edit forms",
    ]
    assert lw.folder_buttons_label.text() == "Folder:"
    groups = [g.title() for g in lw.findChildren(QtWidgets.QGroupBox)]
    assert groups == ["Redact documents", "Forms used for redaction"]
    headers = [lw.table.horizontalHeaderItem(c).text() for c in range(3)]
    assert headers == ["Form", "Status", "Use"]
    assert lw.row_texts(0) == ("保険料控除申告書", "", "✓ OK")
    assert lw.folder_label.text().startswith(
        "Forms folder: "
    ) and lw.folder_label.text().endswith(" (default location)")
    assert lw.summary_label.text().startswith("Using 3 of 3 forms. ")
    assert lw.ready_label.text() == "✓ Ready"
    assert lw.version_label.text() == f"Version {__version__} (development environment)"
    asked = []
    monkeypatch.setattr(widgets, "confirm", lambda *a, **k: asked.append(a) or False)
    lw.table.item(0, 2).setCheckState(Qt.CheckState.Unchecked)
    (_, title, text, info, yes, no) = asked[0]
    assert (title, text, yes, no) == (
        "Sumikeshi Studio",
        'Uncheck "Use"?',
        "Uncheck",
        "Keep checked",
    )
    assert info.startswith(
        'If you uncheck "保険料控除申告書", the fields on pages of that document'
    )


def test_launcher_folder_buttons_layout(new_launcher):
    """「フォルダを」と3つのボタンは、様式のフォルダのパスの行の右に左から順に並べ、「様式を編集」はその下の行の
    右端に置く。Tab キーの順は画面の上から。ドラッグの説明の文も確かめる"""
    lw = new_launcher()

    def top_left(w):
        return w.mapTo(lw, QtCore.QPoint(0, 0))

    row = [
        lw.folder_buttons_label,
        lw.choose_button,
        lw.default_button,
        lw.reload_button,
    ]
    assert [w.text() for w in row] == [
        "フォルダを",
        "変更",
        "既定の場所に戻す",
        "読み直す",
    ]
    path = lw.folder_label
    xs = [top_left(w).x() for w in row]
    assert top_left(path).x() < xs[0] and xs == sorted(xs)
    path_top, path_bottom = top_left(path).y(), top_left(path).y() + path.height()
    for w in row:  # パスの行と同じ高さにある
        assert top_left(w).y() < path_bottom and top_left(w).y() + w.height() > path_top
    edit = lw.forms_button
    assert edit.text() == "様式を編集"
    assert top_left(edit).y() >= max(top_left(w).y() + w.height() for w in row)
    right = top_left(lw.reload_button).x() + lw.reload_button.width()
    assert abs(top_left(edit).x() + edit.width() - right) <= 2  # 下の行の右端
    # Tab キーの順：焦点の鎖を、Tab で焦点を受け取る部品だけたどる（ラベルなども鎖に入るため）
    order, w = [lw.open_button], lw.open_button.nextInFocusChain()
    while w is not lw.table:
        if w.focusPolicy() & Qt.FocusPolicy.TabFocus and w.isVisibleTo(lw):
            order.append(w)
        w = w.nextInFocusChain()
    assert order == [lw.open_button, lw.open_folder_button, lw.choose_button,
                     lw.default_button, lw.reload_button, lw.forms_button]  # fmt: skip
    assert [lw.open_button.text(), lw.open_folder_button.text()] == [
        "ファイルを開く",
        "フォルダを開く",
    ]
    labels = [lab.text() for lab in lw.findChildren(QtWidgets.QLabel)]
    assert "PDF・画像のファイルやフォルダを、この画面にドラッグしても開けます" in labels


def test_use_here_check_is_centered(new_launcher):
    """「使う」のチェックは列の中央に描き、中央のクリックとスペースキーで切り替える。セルの左端（Qt の
    標準ではチェックを描く所）のクリックでは切り替えない。列は見出しの「使う」の幅で決まる狭い列で、チェックは
    その中に収まる（左端のクリックは、チェックと重ならない位置が取れるように、列を広げて確かめる）"""
    lw = new_launcher()
    table = lw.table
    item = table.item(0, launcher.USE_COLUMN)
    index = table.model().index(0, launcher.USE_COLUMN)

    def rects():
        cell = table.visualRect(index)
        option = QtWidgets.QStyleOptionViewItem()
        option.rect, option.widget = cell, table
        delegate = table.itemDelegateForColumn(launcher.USE_COLUMN)
        return cell, delegate.check_rect(option, index)

    cell, mark = rects()
    assert abs(mark.center().x() - cell.center().x()) <= 1
    assert abs(mark.center().y() - cell.center().y()) <= 1
    assert cell.left() <= mark.left() and mark.right() <= cell.right()
    assert not table.horizontalScrollBar().isVisible()
    header = table.horizontalHeader()
    header.setSectionResizeMode(
        launcher.USE_COLUMN, QtWidgets.QHeaderView.ResizeMode.Fixed
    )
    table.setColumnWidth(launcher.USE_COLUMN, 90)
    cell, mark = rects()
    assert cell.width() >= 80 and abs(mark.center().x() - cell.center().x()) <= 1
    viewport = table.viewport()
    left = QtCore.QPoint(cell.left() + mark.width() // 2 + 3, cell.center().y())
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=left)
    assert item.checkState() == Qt.CheckState.Checked  # 左端では切り替えない
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=cell.center())
    assert (
        item.checkState() == Qt.CheckState.Unchecked
    )  # 確認のメッセージは「チェックを外す」で答える
    assert prefs.forms_off() == {"nta-hoken-r8"}
    table.setCurrentCell(0, launcher.USE_COLUMN)
    table.setFocus()
    QTest.keyClick(table, Qt.Key.Key_Space)
    assert item.checkState() == Qt.CheckState.Checked
    assert prefs.forms_off() == set()


def test_buttons_and_actions_have_no_ellipsis(new_launcher, monkeypatch):
    """起動の画面・墨消しの画面・様式の編集の画面・範囲を描く画面のボタンと操作の名前に「…」を付けない（「…」は
    押した後に入力や確認が要ることを示す記号で、画面ごとに付いたり付かなかったりすると意味が分からなくなるため）"""
    lw = new_launcher()
    names = []

    def collect(w):
        names.extend(b.text() for b in w.findChildren(QtWidgets.QAbstractButton))
        names.extend(a.text() for a in w.findChildren(QtGui.QAction))

    collect(lw)
    lw.open_blackout()
    win = lw.child
    win.shown, win.qapp = lw.shown, lw.qapp
    collect(win)
    win.close()
    wait(lw, lambda: lw.child is None and not lw.loading)
    forms_win = open_forms_window(lw)
    collect(forms_win)
    opened = drive_editor(monkeypatch, lw, lambda ed: collect(ed) or False)
    select(forms_win.panel, "nta-fuyou-r9")
    forms_win.panel.b_edit.click()
    wait(lw, lambda: opened and not forms_win.panel.busy)
    forms_win.close()
    wait(lw, lambda: lw.child is None and not lw.loading)
    names = [n for n in names if n]
    for expected in ("ファイルを開く", "墨消しPDFを出力", "新しい様式を登録", "保存"):
        assert expected in names
    assert [n for n in names if "…" in n] == []


def test_dropping_files_on_launcher_opens_blackout_screen(new_launcher, files):
    """PDF・画像を起動の画面にドラッグすると、墨消しの画面で開く。まだ開けないときは理由を示す"""
    lw = new_launcher()

    class Drop:
        def __init__(self, paths):
            self.data = QtCore.QMimeData()
            self.data.setUrls([QtCore.QUrl.fromLocalFile(str(p)) for p in paths])

        def mimeData(self):
            return self.data

    lw.loading = True  # 様式のフォルダを読んでいる
    lw.dropEvent(Drop([files[1]]))
    assert lw.child is None
    assert lw.shown[-1] == "まだファイルを開けません。様式のフォルダを読んでいます…"
    lw.loading = False
    lw.dropEvent(Drop([files[1]]))
    win = lw.child
    assert isinstance(win, app.MainWindow)
    win.shown, win.qapp = lw.shown, lw.qapp
    wait(win, lambda: win.pages and all(st.ready for st in win.pages))
    win.close()
    wait(lw, lambda: not lw.loading)


def test_blackout_screen_opens_from_launcher_and_returns(new_launcher, files):
    """起動の画面から墨消しの画面を開く。様式のメニューはなく、この PC で使わない様式は判別に使わない（その帳票の
    ページは様式不明になり、要確認が付く）。閉じたら起動の画面に戻る"""
    lw = new_launcher()
    row = [r[0] for r in lw.rows].index("nta-fuyou-r9")
    lw.table.item(row, 2).setCheckState(Qt.CheckState.Unchecked)  # 扶養控除等申告書
    lw.open_blackout()
    win = lw.child
    assert isinstance(win, app.MainWindow) and win.isVisible() and not lw.isVisible()
    assert win.findChildren(QtWidgets.QMenuBar) == []  # メニューはない
    win.shown, win.qapp = lw.shown, lw.qapp
    open_and_wait(win, [files[0]])
    assert win.pages[0].result.form == "様式不明"  # 扶養控除等申告書を使わない
    assert win.pages[0].result.note
    assert "使う様式：2　✕ この PC で使わない様式：1" in win.status.text()
    win.close()
    assert lw.isVisible() and lw.child is None
    wait(lw, lambda: not lw.loading)


def test_message_during_dialog_waits_until_closed(new_launcher, files, monkeypatch):
    """確認のメッセージが開いているあいだに、後から起動したツールから開くファイルの通知が来ても、メッセージを閉じる
    まで開かない。確認の後の操作は、確認の前のファイルに効く"""
    lw = new_launcher()
    lw.open_blackout([files[0]])
    win = lw.child
    win.shown, win.qapp = lw.shown, lw.qapp
    wait(lw, lambda: win.docs and all(st.ready for st in win.pages))
    win.goto_page(0)
    click_box(win, first_masked(win))  # 編集したので、回すときに確認のメッセージが出る
    st = win.pages[0]
    box = QtWidgets.QMessageBox

    def question(*a, **k):
        lw.handle_message([str(files[1])])
        lw.qapp.processEvents()
        assert [d.path for d in win.docs] == [files[0]]  # 確認の途中は開かない
        return box.StandardButton.Yes

    patch_ask(monkeypatch, question)
    win.rotate(1)
    assert st in win.pages and st.result is None  # 前のファイルのページを回した
    asked = ask_with(monkeypatch, [box.StandardButton.Yes])
    wait(lw, lambda: [d.path for d in win.docs] == [files[1]])
    assert asked[0][0].startswith(
        "墨消しPDFをまだ出力していないファイルが1件あります。"
    )


def test_message_during_file_dialog_opens_one_blackout_screen(
    new_launcher, files, monkeypatch
):
    """起動の画面でファイルを選んでいるあいだに、開くファイルの通知が来ても、墨消しの画面は1つだけ開く"""
    lw = new_launcher()
    made = []
    real_init = app.MainWindow.__init__

    def counting_init(self, *a, **k):
        made.append(self)
        real_init(self, *a, **k)

    monkeypatch.setattr(app.MainWindow, "__init__", counting_init)

    def choose(*a, **k):
        lw.handle_message([str(files[1])])
        lw.qapp.processEvents()
        assert lw.child is None  # 選んでいるあいだは開かない
        return [str(files[0])], ""

    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileNames", choose)
    lw.open_files()
    win = lw.child
    win.shown, win.qapp = lw.shown, lw.qapp
    wait(lw, lambda: [d.path for d in win.docs] == [files[1]])  # 破棄の確認は「はい」
    assert len(made) == 1


def test_old_reading_does_not_restore_checks(new_launcher, monkeypatch):
    """起動の画面の様式の読み込みを重ねて頼んだとき、古い結果は使わず、チェックは結果を使う時点の設定から作る。
    読んでいるあいだは一覧を押せない"""
    lw = new_launcher()
    calls = []
    monkeypatch.setattr(
        lw.session.background,
        "run",
        lambda fn, *a, callback: calls.append((fn, a, callback)),
    )
    lw.reload()
    first = calls[-1]
    assert lw.loading and not lw.table.isEnabled()
    lw.reload()
    second = calls[-1]
    prefs.set_forms_off({"nta-hoken-r8"})  # 読んでいるあいだに設定が変わった
    first[2](formstore.load(*first[1]), None)  # 1回目（古い）の結果は使わない
    assert lw.loading
    second[2](formstore.load(*second[1]), None)
    assert not lw.loading and lw.table.isEnabled()
    row = [r[0] for r in lw.rows].index("nta-hoken-r8")
    item = lw.table.item(row, launcher.USE_COLUMN)
    assert item.checkState() == Qt.CheckState.Unchecked
    assert "nta-hoken-r8" in lw.loaded.off


def test_closed_screens_are_released(new_launcher, files, tmp_path, monkeypatch):
    """閉じた墨消しの画面と範囲を描く画面は Qt の部品ごと消え、開いたファイルとページの画像を手放す。ファイルから
    ページへの参照を断つので、ガベージコレクションを待たずに放す。開き直したときも、前のファイルからページへの
    参照を断つ"""
    import weakref

    lw = new_launcher()
    lw.open_blackout([files[0]])
    win = lw.child
    win.shown, win.qapp = lw.shown, lw.qapp
    wait(lw, lambda: win.docs and all(st.ready for st in win.pages))
    first = [win.docs[0]]
    win.open_paths([files[1]])
    wait(lw, lambda: win.docs[0] is not first[0] and all(st.ready for st in win.pages))
    assert first[0].pages == []
    first.clear()
    doc = weakref.ref(win.docs[0])
    win.close()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    assert doc() is None and not shiboken6.isValid(win) and lw.child is None


def test_files_from_later_start_wait_for_declined_discard(
    new_launcher, files, monkeypatch
):
    """後から起動したツールから届いたファイルは、墨消しの画面に出力していないファイルがあれば、作業を破棄するかを
    尋ね、「いいえ」なら開かずに、今のファイルのままにする"""
    lw = new_launcher()
    lw.open_blackout([files[0]])
    win = lw.child
    win.shown, win.qapp = lw.shown, lw.qapp
    wait(lw, lambda: win.docs and all(st.ready for st in win.pages))
    answer = [QtWidgets.QMessageBox.StandardButton.No]
    asked = ask_with(monkeypatch, answer)
    lw.handle_message([str(files[1])])
    wait(lw, lambda: asked)
    lw.qapp.processEvents()
    assert asked[0][0].startswith(
        "墨消しPDFをまだ出力していないファイルが1件あります。"
    )
    assert [d.path for d in win.docs] == [files[0]] and lw.child is win
    # 後始末で画面を閉じるとき、作業の破棄の確認に「はい」と答える（「いいえ」のままだと、画面と解析のプロセスが残る）
    answer[0] = QtWidgets.QMessageBox.StandardButton.Yes
