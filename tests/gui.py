# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""画面のテストで共通に使う補助の関数と定数

画面の部品（PySide6）を読み込めない環境では、このモジュールを読み込んだ時点で、読み込んだテストのファイルを
飛ばす。そのため、画面のテストのファイルは、PySide6 の名前もこのモジュールから読み込む（Qt を読み込む前に
飛ばすため）。
"""

import csv
import dataclasses
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip(
    "PySide6.QtWidgets", reason="画面の部品を読み込めない環境", exc_type=ImportError
)
# 画面のテストのファイルが、ここから読み込む
import shiboken6  # noqa: F401
from PySide6 import QtCore, QtGui  # noqa: F401
from PySide6.QtCore import QSettings, Qt  # noqa: F401
from PySide6.QtTest import QTest
from userforms import install_bundled

from sumikeshi_studio import formeditor, formlist, formswin, prefs, widgets
from sumikeshi_studio.boxes import Source

FIELDS = 10  # 扶養控除等申告書 令和9年分の見本にある個人番号欄の数


def settle(win):
    """テストの終わりに、裏の処理（様式の読み書き・ページの解析・保存の前の確認）が終わるのを待ち、開いている
    確認のメッセージを閉じる。テストが途中で失敗しても、終わっていない処理の結果が、閉じた画面に後から届かないように
    する（届くと、次のテストの途中で Python が強制終了することがあるため）"""
    session = win.session
    try:
        wait(
            win,
            lambda: (
                session.background.running == 0
                and session.analyzer.running == 0
                and session.analyzer.calling == 0
            ),
            sec=120,
        )
    finally:
        for w in QtWidgets.QApplication.topLevelWidgets():
            if isinstance(w, QtWidgets.QDialog) and w.isVisible():
                w.done(0)
        win.qapp.processEvents()


def wait(win, cond, sec=300):
    start = time.time()
    while not cond():
        win.qapp.processEvents()
        time.sleep(0.05)
        assert time.time() - start < sec, "時間内に終わらなかった"


def open_files(win, paths):
    """ファイルを開く。様式のフォルダを裏で読み終わり、開く・開かないが決まるまで待つ"""
    win.open_paths(paths)
    wait(win, lambda: win.opening is None)


def open_and_wait(win, paths):
    open_files(win, paths)
    wait(win, lambda: all(st.ready or st.error for st in win.pages))
    win.goto_page(0)
    win.qapp.processEvents()


def with_work(win, index: int = 0):
    """墨消しの画面のページを、作業画像を持つ解析の結果にしたもの。墨消しの画面のページの解析の結果は、作業画像を
    持たない（キャッシュに持つ）ので、登録の画面を開くときは、様式の画面のページと同じく作業画像を持つ結果で渡す"""
    st = win.pages[index]
    work = win.cache.get(index)
    assert work is not None
    return dataclasses.replace(st, result=dataclasses.replace(st.result, work=work))


def click_box(win, box):
    view = win.view
    center = view.mapFromScene(box.x + box.w / 2, box.y + box.h / 2)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=center)


def drag(win, x0, y0, x1, y1):
    view = win.view
    p0, p1 = view.mapFromScene(x0, y0), view.mapFromScene(x1, y1)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=p0)
    QTest.mouseMove(view.viewport(), p1)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=p1)


def manual_boxes(win):
    return [b for b in win.pages[0].result.boxes if b.source == Source.MANUAL]


def first_masked(win):
    """1ページ目の、墨消しすることになっている自動の候補の1つ目"""
    return next(b for b in win.pages[0].result.boxes if b.active)


def log_rows(tmp_path):
    path = tmp_path / "appdata" / "SumikeshiStudio" / "worklog.csv"
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def patch_ask(monkeypatch, answer):
    """「はい」「いいえ」の確認（widgets.ask）を、answer の答えにする。answer は、前の作り（QMessageBox.question）と同じ
    引数（親・題名・文・ボタン・既定のボタン）で呼び、StandardButton.Yes を返せば「はい」。既定のボタンは、widgets.ask_box
    が作る確認のメッセージのもの（「いいえ」なら StandardButton.No）を、置き換えるときに1回だけ読んで渡す（画面を閉じる
    途中などで確認のメッセージを作らないため）。答えるあいだは、本物と同じくダイアログを出している扱いにする（その
    あいだに届いた、ファイルを開く合図などを待たせるため）"""
    buttons = QtWidgets.QMessageBox.StandardButton
    box, yes = widgets.ask_box(None, "", "")
    default = buttons.Yes if box.defaultButton() is yes else buttons.No
    del box, yes

    def ask(parent, title, text):
        with widgets.dialog():
            reply = answer(parent, title, text, buttons.Yes | buttons.No, default)
        return reply == buttons.Yes

    monkeypatch.setattr(widgets, "ask", ask)


def ask_with(monkeypatch, answer):
    """確認のメッセージに answer で答える。出したメッセージ（文、既定のボタン）を返す一覧に記録する"""
    asked = []
    patch_ask(monkeypatch, lambda *a, **k: asked.append((a[2], a[4:])) or answer[0])
    return asked


def use_forms_folder(tmp_path):
    """起動の画面で選んだ様式のフォルダに見立てたフォルダ。導入で入れるのと同じ配布用パッケージの3様式を置く"""
    folder = tmp_path / "様式"
    install_bundled(folder)
    prefs.settings().setValue(prefs.FORMS_FOLDER_KEY, str(folder))
    return folder


# drive_editor の steps で起きた例外。steps は Qt から呼ばれる処理の中で動き、そこで起きた例外は Qt が表示するだけで
# テストを失敗にしないので、ここに残して、テストの後に conftest が出す
STEP_ERRORS: list[BaseException] = []


def drive_editor(monkeypatch, window, steps):
    """様式の編集の画面の exec を置き換える。steps(画面) で操作し、False を返さなければ保存して、
    保存の処理が終わるまで待つ。開いた画面の一覧を返す。steps で起きた例外は、テストの後に出す（STEP_ERRORS）"""
    opened = []

    def fake_exec(self):
        opened.append(self)
        try:
            go_on = steps(self) is not False
        except BaseException as e:  # noqa: BLE001  テストの後に出す
            STEP_ERRORS.append(e)
            return QtWidgets.QDialog.DialogCode.Rejected
        if go_on:
            self.save()
            wait(window, lambda: not self.busy)
        return self.result()

    monkeypatch.setattr(formeditor.FormEditor, "exec", fake_exec)
    return opened


def list_dialog(window):
    """様式の一覧（様式の編集の画面に置く部品。登録・置き換えのボタンは出さない）"""
    dlg = formlist.FormListPanel(window.session)
    wait(window, lambda: not dlg.busy)
    return dlg


def select(dlg, form_id):
    dlg.table.selectRow([r[0] for r in dlg.rows].index(form_id))


def row_cells(dlg, form_id):
    return dlg.row_texts([r[0] for r in dlg.rows].index(form_id))


def open_forms_window(lw):
    lw.forms_button.click()
    win = lw.child
    assert isinstance(win, formswin.FormsWindow) and win.isVisible()
    assert not lw.isVisible()
    wait(lw, lambda: not win.panel.busy)
    return win
