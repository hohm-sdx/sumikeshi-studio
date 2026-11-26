# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""墨消しの画面のテスト（表示なしで動かす）。共通のフィクスチャは tests/conftest.py に、補助の関数は tests/gui.py にある"""

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from gui import (
    FIELDS,
    Qt,
    QtCore,
    QTest,
    QtGui,
    QtWidgets,
    ask_with,
    click_box,
    drag,
    first_masked,
    list_dialog,
    log_rows,
    manual_boxes,
    open_and_wait,
    open_files,
    patch_ask,
    wait,
)
from userforms import install_bundled

from sumikeshi_studio import (
    APP_NAME,
    app,
    exporttext,
    folders,
    formlist,
    i18n,
    imaging,
    reviewtext,
    widgets,
    workers,
    worklog,
)

# 深さを選ぶダイアログを出す関数（画面のテストの環境は置き換えるので、置き換える前のものを持つ）
REAL_CHOOSE_DEPTH = app.depthdialog.choose_depth


def test_unopenable_files_are_skipped_with_reason(window, files, tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")
    open_files(window, [files[0], broken, files[1]])
    assert "broken.pdf" in window.shown[-1]
    assert [d.path for d in window.docs] == list(files)


def test_empty_folder_opens_nothing(window, tmp_path):
    """開ける PDF・画像のファイルが1つもない（空のフォルダ）ときは、その旨を出し、何も開かない"""
    empty = tmp_path / "空のフォルダ"
    empty.mkdir()
    (empty / "メモ.txt").write_text(
        "PDF・画像ではない", encoding="utf-8"
    )  # フォルダの中の対応していない形式は数えない
    open_files(window, [empty])
    assert window.shown[-1] == "開ける PDF・画像のファイルがありませんでした。"
    assert window.docs == [] and window.pages == []


def test_previous_outputs_in_folder_are_skipped_and_counted(window, files, tmp_path):
    """フォルダの中の墨消し済みのPDF（「_redacted」「_redacted_2」）は開かず、件数を画面下に出し、マウスを
    重ねると名前を出す。次にファイルを開くまで出しておく。開けるファイルがなければ、その文に件数を添える"""
    folder = tmp_path / "in"
    folder.mkdir()
    shutil.copy(files[0], folder / "fuyou.pdf")
    shutil.copy(files[0], folder / "fuyou_redacted.pdf")
    shutil.copy(files[0], folder / "fuyou_redacted_2.pdf")
    shown = len(window.shown)
    open_files(window, [folder])
    assert [d.path.name for d in window.docs] == ["fuyou.pdf"]
    assert len(window.shown) == shown  # メッセージは出さない
    assert "墨消し済みのPDFを2件除きました" in window.status.text()
    assert "fuyou_redacted.pdf\nfuyou_redacted_2.pdf" in window.status.tips()
    only = tmp_path / "only"
    only.mkdir()
    shutil.copy(files[0], only / "x_redacted_3.pdf")
    open_files(window, [only])
    assert window.shown[-1] == (
        "開ける PDF・画像のファイルがありませんでした。\n"
        "墨消し済みのPDFを1件除きました。"
    )
    # 開けなかったので、前に開いたファイルと件数がそのまま残る
    assert [d.path.name for d in window.docs] == ["fuyou.pdf"]
    assert "墨消し済みのPDFを2件除きました" in window.status.text()
    open_files(window, [files[1]])
    assert "墨消し済みのPDF" not in window.status.text()


def test_folder_with_subfolders_asks_depth_and_shows_relative_paths(
    window, files, tmp_path, monkeypatch
):
    """サブフォルダのあるフォルダを開くと深さを選ぶダイアログを出し、選んだ深さまで開く。左の一覧のファイルの行は
    相対パスで、マウスを重ねると完全なパスを出す。開けなかったファイルも相対パスで示す。ダイアログでやめたら何も
    開かない。サブフォルダのないフォルダは、ダイアログを出さずに開く"""
    folder = tmp_path / "in"
    (folder / "A" / "B").mkdir(parents=True)
    shutil.copy(files[0], folder / "1.pdf")
    shutil.copy(files[1], folder / "A" / "B" / "2.pdf")
    (folder / "A" / "壊れた.pdf").write_bytes(b"x")
    asked = []

    def choose(parent, selection):
        asked.append(selection.counts.count(0))
        return True, None

    monkeypatch.setattr(app.depthdialog, "choose_depth", choose)
    open_files(window, [folder])
    assert asked == [1]
    nested = str(Path("A", "B", "2.pdf"))
    assert [d.label for d in window.docs] == ["1.pdf", nested]
    assert window.doc_items[1].data(0, widgets.LabelDelegate.MAIN_ROLE) == nested
    assert window.doc_items[1].toolTip(0) == str(folder / "A" / "B" / "2.pdf")
    assert window.shown[-1] == (
        "次のファイルは開けませんでした。ほかのファイルは開きます。\n"
        f"PDFを開けません（壊れている可能性があります）: {Path('A', '壊れた.pdf')}"
    )
    monkeypatch.setattr(app.depthdialog, "choose_depth", lambda p, s: (False, None))
    open_files(window, [tmp_path / "in"])
    assert [d.label for d in window.docs] == ["1.pdf", nested]  # やめたので前のまま
    monkeypatch.setattr(
        app.depthdialog, "choose_depth", lambda p, s: pytest.fail("ダイアログを出した")
    )
    open_files(window, [folder / "A" / "B"])
    assert [d.label for d in window.docs] == ["2.pdf"]


def test_depth_dialog_starts_at_top_and_shows_counts(gui_env, tmp_path, monkeypatch):
    """深さを選ぶダイアログは、いつも「直下だけ」を選んだ状態で出し、数え終わった深さから件数を出す。上限で数えるのを
    止めた深さは「n件以上」。選んだ深さで開く。やめたら数えるのを止める"""
    root = tmp_path / "in"
    for name in ("1.pdf", "A/1.pdf", "A/B/1.pdf", "A/B/2.pdf"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(b"")
    selection = folders.Selection([root])
    dialog = app.depthdialog.DepthDialog(None, selection)
    texts = lambda: [b.text() for b in dialog.buttons]
    assert texts() == [
        "直下だけ：1件",
        "1つ下のフォルダまで：数えています…",
        "2つ下まで：数えています…",
        "3つ下まで：数えています…",
        "すべて：数えています…",
    ]
    assert dialog.buttons[0].isChecked() and dialog.depth() == 0
    assert dialog.timer.isActive()
    # 自動の確認で見分けるための、言語によらない名前
    assert dialog.objectName() == "depth_dialog"
    assert [b.objectName() for b in dialog.buttons] == [
        "depth_0",
        "depth_1",
        "depth_2",
        "depth_3",
        "depth_all",
    ]
    assert (dialog.open_button.objectName(), dialog.cancel_button.objectName()) == (
        "depth_open",
        "depth_cancel",
    )
    assert len(dialog.findChildren(QtWidgets.QLabel, "depth_question")) == 1
    selection.count()
    dialog.refresh()
    assert texts() == [
        "直下だけ：1件",
        "1つ下のフォルダまで：2件",
        "2つ下まで：4件",
        "3つ下まで：4件",
        "すべて：4件",
    ]
    assert not dialog.timer.isActive()
    dialog.buttons[4].click()
    assert dialog.depth() is None
    dialog.deleteLater()
    stopped = folders.Selection([root])
    stopped.count(limit=2)
    dialog = app.depthdialog.DepthDialog(None, stopped)
    assert texts()[1:3] == ["1つ下のフォルダまで：2件", "2つ下まで：2件以上"]
    dialog.deleteLater()

    # ダイアログを出すあいだ裏で数え、選んだ深さを返す
    def exec_(self):
        self.selection._thread.join()  # 数え終わるのを待つ
        self.refresh()
        assert self.buttons[2].text() == "2つ下まで：4件"
        self.buttons[2].click()
        return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(app.depthdialog.DepthDialog, "exec", exec_)
    assert REAL_CHOOSE_DEPTH(None, folders.Selection([root])) == (True, 2)
    monkeypatch.setattr(
        app.depthdialog.DepthDialog,
        "exec",
        lambda self: QtWidgets.QDialog.DialogCode.Rejected,
    )
    selection = folders.Selection([root])
    assert REAL_CHOOSE_DEPTH(None, selection)[0] is False
    assert selection._stop.is_set()


def test_opening_shows_progress_and_can_be_cancelled(
    window, files, tmp_path, monkeypatch
):
    """ファイルは裏で開き、開くあいだは画面下に「ファイルを開いています…」と出して今のファイルを編集できなくする。
    時間がかかれば、進み具合と「やめる」のボタンを出す。やめたら何も開かず、前のファイルをそのまま残す"""
    import threading

    open_and_wait(window, [files[0]])
    release = threading.Event()
    real_open = imaging.open_pages

    def slow(path):
        release.wait(30)
        return real_open(path)

    monkeypatch.setattr(imaging, "open_pages", slow)
    window.open_paths([files[1]])
    wait(window, lambda: window.opener is not None)
    assert "ファイルを開いています…" in window.status.text()
    assert window.editing_locked()

    def shown_progress():
        found = window.findChildren(QtWidgets.QProgressDialog)
        return [d for d in found if d.isVisible()]

    wait(window, shown_progress)
    dialog = shown_progress()[0]
    assert dialog.labelText() == "開いています（0／1件）"
    button = dialog.findChild(QtWidgets.QPushButton)
    assert button.text() == "やめる"
    button.click()
    release.set()
    wait(window, lambda: window.opening is None)
    assert [d.path for d in window.docs] == [files[0]]
    assert "ファイルを開いています…" not in window.status.text()
    assert not window.editing_locked()
    assert not shown_progress()


def test_export_waits_for_analysis_and_warns_unviewed(
    window, files, tmp_path, monkeypatch
):
    """全ページの解析が終わるまでは出力できない。表示していないページがあれば、確かめてから出力する"""
    open_files(window, list(files))
    assert not window.act_export.isEnabled()  # 解析中
    wait(window, lambda: all(st.ready for st in window.pages))
    window.goto_page(0)
    window.qapp.processEvents()
    # 2ファイル目を表示していなくても、出力できる
    assert window.export_targets() == window.docs
    assert window.act_export.isEnabled()
    assert window.act_export.text() == "墨消しPDFを出力（2件）"
    assert window.export_button.text() == window.act_export.text()
    assert window.export_button.isEnabled() and window.export_button.isDefault()
    assert "このまま出力すると、出力の前に警告が出ます" in window.status.tips()
    box = QtWidgets.QMessageBox
    asked, answer = [], [box.StandardButton.No]
    patch_ask(monkeypatch, lambda *a, **k: asked.append((a[2], a[4:])) or answer[0])
    out_dir = tmp_path / "出力"
    out_dir.mkdir()
    window.export_flow.export_to(out_dir)
    text, _ = asked[-1]
    assert text.startswith("次のファイルには、表示していないページがあります。")
    assert "・scan.pdf\n　　表示していないページ：1\n" in text
    assert list(out_dir.iterdir()) == []  # 「いいえ」なら出力しない

    answer[0] = box.StandardButton.Yes
    window.export_flow.export_to(out_dir)
    wait(window, lambda: not window.exporting)
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "scan_redacted.pdf",
        "scan_redacted_2.pdf",
    ]
    shown = window.shown[-1]
    assert "表示していないページ：1（確かめずに出力しました）" in shown
    assert "⚠ や ✗ の付いたファイルを確かめてください。" in shown
    rows = log_rows(tmp_path)
    assert [r["Pages not viewed"] for r in rows] == ["", "1"]
    # 全ファイルを出力し終えたら、画面下に表示していないページは出ない
    assert "まだ表示していないページ" not in window.status.text()


def test_click_toggles_and_drag_adds(window, files):
    open_and_wait(window, [files[0]])
    box = first_masked(window)
    click_box(window, box)
    assert not box.active  # クリックで解除
    drag(window, 1100, 140, 1400, 175)
    assert len(manual_boxes(window)) == 1  # ドラッグで追加

    window.undo.undo()
    assert manual_boxes(window) == []
    window.undo.undo()
    assert box.active
    window.undo.redo()
    window.undo.redo()
    assert not box.active and len(manual_boxes(window)) == 1


def test_export_writes_reviewed_files_and_log(window, files, tmp_path):
    open_and_wait(window, list(files))
    click_box(window, first_masked(window))
    drag(window, 1100, 140, 1400, 175)
    window.step(1)  # 2ファイル目も表示する
    out_dir = tmp_path / "出力"
    out_dir.mkdir()
    window.export_flow.export_to(out_dir)
    wait(window, lambda: not window.exporting)

    # 2ファイルとも元の名前が scan.pdf のため、2つ目には「_2」が付く
    assert (out_dir / "scan_redacted.pdf").exists()
    assert (out_dir / "scan_redacted_2.pdf").exists()
    # 見本の10か所 − 解除1 + 追加1
    assert f"墨消しした範囲：{FIELDS}か所" in window.shown[-1]
    assert all(d.exported for d in window.docs)
    assert not window.act_export.isEnabled()
    rows = log_rows(tmp_path)
    assert [r["Input file"] for r in rows] == [str(p) for p in files]
    row = rows[0]
    assert (
        row["Form fields"],
        row["Fields turned off"],
        row["Manually added areas"],
    ) == (
        str(FIELDS),
        "1",
        "1",
    )
    assert row["Pages to review"] == ""
    assert row["OCR"] == "RapidOCR"
    assert [r["Forms used"] for r in rows] == [
        "扶養控除等申告書 (revision 0)",
        "保険料控除申告書 (revision 0)",
    ]
    # 様式の既定の場所に、配布用パッケージの3様式をコピーしてある（conftest.gui_env）
    assert "使う様式：3" in window.status.text()


def test_export_dialog_and_guards(window, files, tmp_path, monkeypatch):
    """出力できないときは、出力先のフォルダを選ぶダイアログを出さない。選んだフォルダに出力する。出力の途中と、
    出力するファイルがないときは、何もしない。元のファイルに上書きする出力先なら、理由を示して出力しない"""
    import shutil

    folder = tmp_path / "入力"
    folder.mkdir()
    scan = shutil.copy(files[0], folder / "scan.pdf")
    # 「scan.pdf」を同じフォルダに出力すると「scan_redacted.pdf」になり、この入力ファイルに上書きしてしまう
    marked = shutil.copy(files[1], folder / "scan_redacted.pdf")
    out_dir = tmp_path / "出力"
    out_dir.mkdir()
    chosen, warned = [], []
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getExistingDirectory",
        lambda *a, **k: chosen.append(a[1]) or str(out_dir),
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning", lambda *a, **k: warned.append(a[2])
    )
    window.export_flow.export_dialog()  # 開いていなければ、出力先を選ぶダイアログを出さない
    assert chosen == []
    open_and_wait(window, [scan, marked])
    window.step(1)
    window.export_flow.export_to(folder)
    assert warned == [exporttext.NO_OVERWRITE] and not window.exporting
    window.export_flow.exporting = True  # 出力の途中は何もしない
    window.export_flow.export_to(out_dir)
    assert list(out_dir.iterdir()) == []
    window.export_flow.exporting = False
    window.export_flow.export_dialog()  # 選んだフォルダに出力する
    wait(window, lambda: not window.exporting)
    assert chosen == ["墨消しPDFの出力先のフォルダを選ぶ"]
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "scan_redacted.pdf",
        "scan_redacted_redacted.pdf",
    ]
    shown = len(window.shown)
    window.export_flow.export_to(out_dir)  # 出力するファイルがなければ何もしない
    assert len(window.shown) == shown and not window.exporting


def test_export_closes_progress_then_shows_result(window, files, tmp_path, monkeypatch):
    """出力を始めると、出力中にしてから画面の状態を更新し（出力のボタンを押せない）、出力のスレッドの合図で進み具合の
    ダイアログを進める。終わると、進み具合のダイアログを閉じ、出力中をやめて画面の状態を更新してから、ファイルごとの
    結果を並べた結果のダイアログを出す。この順を確かめる"""
    open_and_wait(window, list(files))
    window.step(1)
    out_dir = tmp_path / "出力"
    out_dir.mkdir()
    order = []
    dialog = QtWidgets.QProgressDialog
    close, set_value, update = dialog.close, dialog.setValue, window.update_state

    def updated():
        update()
        order.append(("更新", window.exporting, window.act_export.isEnabled()))

    monkeypatch.setattr(
        dialog,
        "close",
        lambda self: (order.append("ウィンドウを閉じる"), close(self))[1],
    )
    monkeypatch.setattr(
        dialog,
        "setValue",
        lambda self, v: (order.append("進める"), set_value(self, v))[1],
    )
    monkeypatch.setattr(window, "update_state", updated)
    monkeypatch.setattr(
        widgets.ResultDialog,
        "exec",
        lambda self: order.append(("結果のウィンドウ", self.text.count(" → "))) or 0,
    )
    window.export_flow.export_to(out_dir)
    wait(window, lambda: not window.exporting)
    assert order[0] == ("更新", True, False)
    assert "進める" in order
    end = order.index("ウィンドウを閉じる")
    assert order[end:] == [
        "ウィンドウを閉じる",
        ("更新", False, False),
        ("結果のウィンドウ", 2),
    ]
    assert ("更新", True, False) not in order[end:]


def test_reexport_after_edit_keeps_output_name(window, files, tmp_path):
    open_and_wait(window, list(files))
    window.step(1)
    out_dir = tmp_path / "出力"
    out_dir.mkdir()
    window.export_flow.export_to(out_dir)
    wait(window, lambda: not window.exporting)
    # 出力した後に2ファイル目を直すと、そのファイルだけを、同じ名前で出力し直せる
    window.add_box((10.0, 10.0, 50.0, 50.0))
    assert window.export_targets() == [window.docs[1]]
    window.export_flow.export_to(out_dir)
    wait(window, lambda: not window.exporting)
    rows = log_rows(tmp_path)
    assert [r["Input file"] for r in rows] == [str(files[0]), *[str(files[1])] * 2]
    assert rows[2]["Output file"] == str(out_dir / "scan_redacted_2.pdf")


def test_files_with_failed_pages_are_skipped_after_confirming(
    window, files, tmp_path, monkeypatch
):
    """処理できないページがあるファイルは、確かめてから除いて出力する"""
    open_and_wait(window, list(files))
    st = window.pages[1]
    st.result, st.error = (
        None,
        "OSError: 試験",
    )  # 2ファイル目を処理できなかったことにする
    window.update_state()
    assert window.export_targets() == [window.docs[0]]
    box = QtWidgets.QMessageBox
    asked, answer = [], [box.StandardButton.No]
    patch_ask(monkeypatch, lambda *a, **k: asked.append(a[2]) or answer[0])
    out_dir = tmp_path / "出力"
    out_dir.mkdir()
    window.export_flow.export_to(out_dir)
    assert asked[-1] == exporttext.skipped_question([window.docs[1]])
    assert (
        not window.exporting and list(out_dir.iterdir()) == []
    )  # 「いいえ」なら出力しない

    answer[0] = box.StandardButton.Yes
    window.export_flow.export_to(out_dir)
    wait(window, lambda: not window.exporting)
    assert [p.name for p in out_dir.iterdir()] == ["scan_redacted.pdf"]
    assert exporttext.skipped_text(window.docs[1]) in window.shown[-1]
    assert window.docs[0].exported and not window.docs[1].exported


def test_overwrite_is_confirmed_and_result_names_folder(
    window, files, tmp_path, monkeypatch
):
    """出力先に同じ名前のファイルがあれば、名前を並べて上書きしてよいかを確かめ、「いいえ」なら出力しない。結果の
    ダイアログの1行目は出力先のフォルダ"""
    open_and_wait(window, [files[0]])
    out_dir = tmp_path / "出力"
    out_dir.mkdir()
    old = out_dir / "scan_redacted.pdf"
    old.write_bytes(b"old")
    box = QtWidgets.QMessageBox
    asked, answer = [], [box.StandardButton.No]
    patch_ask(monkeypatch, lambda *a, **k: asked.append(a[2]) or answer[0])
    window.export_flow.export_to(out_dir)
    assert asked == [exporttext.overwrite_question([old], out_dir)]
    assert (
        not window.exporting and old.read_bytes() == b"old"
    )  # 「いいえ」なら出力しない

    answer[0] = box.StandardButton.Yes
    window.export_flow.export_to(out_dir)
    wait(window, lambda: not window.exporting)
    assert old.read_bytes() != b"old" and window.docs[0].exported
    assert window.shown[-1].split("\n")[0] == f"出力先：{out_dir}"


def test_subfolders_are_made_in_output_folder(window, files, tmp_path, monkeypatch):
    """サブフォルダまで開いたファイルは、出力先の下に開いたフォルダからの相対パスでフォルダを作って出力し、結果の
    ダイアログに相対パスで出す。フォルダを作れなければ、そのファイルは出力せずに理由を出す。出力先のパスが長すぎる
    ファイルは、確かめてから除いてほかを出力し、結果のダイアログに出す"""
    folder = tmp_path / "in"
    (folder / "A" / "B").mkdir(parents=True)
    (folder / "C").mkdir()
    shutil.copy(files[0], folder / "scan.pdf")
    shutil.copy(files[1], folder / "A" / "B" / "scan.pdf")
    shutil.copy(files[1], folder / "C" / "scan.pdf")
    monkeypatch.setattr(app.depthdialog, "choose_depth", lambda p, s: (True, None))
    open_and_wait(window, [folder])
    for i in range(len(window.pages)):
        window.goto_page(i)
    nested = Path("A", "B", "scan.pdf")

    # 長いパスを使えない PC で、出力先のパスが長すぎるファイル。出力先のフォルダを226文字にすると、直下の
    # 「scan_redacted.pdf」は一時的な名前を含めて258文字で出力でき、C と A/B の下は260文字・262文字で上限を超える
    monkeypatch.setattr(exporttext, "long_paths", lambda: False)
    deep_out = tmp_path / ("o" * (226 - len(str(tmp_path)) - 1))
    deep_out.mkdir()
    box = QtWidgets.QMessageBox
    asked = []
    patch_ask(monkeypatch, lambda *a, **k: asked.append(a[2]) or box.StandardButton.Yes)
    window.export_flow.export_to(deep_out)
    wait(window, lambda: not window.exporting)
    assert asked[0].startswith(
        "次のファイルは、出力先のパスが長すぎるため出力しません。ほかのファイルを出力しますか？\n"
        f"・{Path('A', 'B', 'scan_redacted.pdf')}\n・{Path('C', 'scan_redacted.pdf')}\n"
    )
    assert [p.name for p in deep_out.rglob("*.pdf")] == ["scan_redacted.pdf"]
    assert (
        f"✗ {nested}：出力先のパスが長すぎるため、出力しませんでした。"
        in window.shown[-1]
    )
    monkeypatch.setattr(exporttext, "long_paths", lambda: True)

    # まだ出力していない A/B と C を出力する。C のフォルダは作れない
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    real_mkdir = Path.mkdir

    def mkdir(self, *a, **k):
        if self.name == "C":
            raise PermissionError(13, "Permission denied")
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    window.export_flow.export_to(out_dir)
    wait(window, lambda: not window.exporting)
    assert [p.relative_to(out_dir).as_posix() for p in out_dir.rglob("*.pdf")] == [
        "A/B/scan_redacted.pdf"
    ]
    result = window.shown[-1]
    assert f"✓ {nested} → {nested.with_name('scan_redacted.pdf')}　" in result
    assert (
        f"✗ {Path('C', 'scan.pdf')}：出力できませんでした。"
        f"出力先のフォルダを作れませんでした（PermissionError）: {out_dir / 'C'}"
    ) in result


def test_two_folders_are_output_in_folders_of_their_names(window, files, tmp_path):
    """2つ以上のフォルダを開いたときは、出力先の下に開いたフォルダの名前のフォルダを作る（同じ名前は2つ目から
    「_2」）。直接選んだファイルは出力先の直下に出力する"""
    first, second = tmp_path / "a" / "2026", tmp_path / "b" / "2026"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    shutil.copy(files[0], first / "scan.pdf")
    shutil.copy(files[1], second / "scan.pdf")
    single = tmp_path / "single.pdf"
    shutil.copy(files[1], single)
    open_and_wait(window, [first, second, single])
    assert [d.label for d in window.docs] == [
        str(Path("2026", "scan.pdf")),
        str(Path("2026_2", "scan.pdf")),
        "single.pdf",
    ]
    for i in range(len(window.pages)):
        window.goto_page(i)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    window.export_flow.export_to(out_dir)
    wait(window, lambda: not window.exporting)
    assert sorted(
        p.relative_to(out_dir).as_posix() for p in out_dir.rglob("*.pdf")
    ) == ["2026/scan_redacted.pdf", "2026_2/scan_redacted.pdf", "single_redacted.pdf"]


def test_export_continues_when_analysis_process_ends(window, files, tmp_path):
    """出力の途中で解析のプロセスが終わると、そのファイルは出力できなかったとし、残りはこのスレッドで出力する"""
    from concurrent.futures.process import BrokenProcessPool

    open_and_wait(window, list(files))
    window.step(1)  # 2ファイル目も表示する

    class Broken:
        """ページを墨消しする処理を頼むと、解析のプロセスが終わったときと同じエラーになる"""

        def submit(self, fn, *args):
            raise BrokenProcessPool("解析のプロセスが終わった")

        def shutdown(self, **_):
            pass

    window.analyzer.executor = Broken()
    out_dir = tmp_path / "出力"
    out_dir.mkdir()
    window.export_flow.export_to(out_dir)
    wait(window, lambda: not window.exporting)
    assert f"✗ scan.pdf：出力できませんでした。{workers.CRASHED}" in window.shown[-1]
    assert [p.name for p in out_dir.iterdir()] == ["scan_redacted_2.pdf"]
    assert not window.docs[0].exported and window.docs[1].exported


def test_export_reports_worklog_failure(window, files, tmp_path, monkeypatch):
    """作業ログに書けなくても（Excel で開いているなど）、結果のダイアログにそのファイルの行と理由を出し、「⚠」にする"""
    open_and_wait(window, [files[0]])

    def locked(record):
        raise PermissionError(13, "Permission denied", "worklog.csv")

    monkeypatch.setattr(worklog, "append", locked)
    out_dir = tmp_path / "出力"
    out_dir.mkdir()
    window.export_flow.export_to(out_dir)
    wait(window, lambda: not window.exporting)
    shown = window.shown[-1]
    assert f"⚠ scan.pdf → scan_redacted.pdf　墨消しした範囲：{FIELDS}か所" in shown
    assert (
        "作業ログに書けませんでした（PermissionError: [Errno 13] Permission denied: "
        "'worklog.csv'）" in shown
    )
    assert "⚠ や ✗ の付いたファイルを確かめてください。" in shown
    assert window.docs[0].exported and (out_dir / "scan_redacted.pdf").exists()


def test_changed_input_is_not_exported(window, files, tmp_path):
    """開いたときの大きさと更新日時を覚え、出力のときに違えば、そのファイルは出力しない"""
    from sumikeshi_studio import export

    open_and_wait(window, list(files))
    window.step(1)
    assert [d.stamp for d in window.docs] == [imaging.file_stamp(f) for f in files]
    window.docs[0].stamp = (0, 0)  # 開いた後に入力ファイルが変わったとみなす
    out_dir = tmp_path / "出力"
    out_dir.mkdir()
    window.export_flow.export_to(out_dir)
    wait(window, lambda: not window.exporting)
    assert f"✗ scan.pdf：出力できませんでした。{export.CHANGED}" in window.shown[-1]
    assert [p.name for p in out_dir.iterdir()] == ["scan_redacted_2.pdf"]
    assert not window.docs[0].exported and window.docs[1].exported
    assert [r["Input file"] for r in log_rows(tmp_path)] == [str(files[1])]


def test_unreadable_page_skips_only_that_file(window, files, monkeypatch):
    """ページを読めない PDF は、そのファイルだけ理由を示して除き、ほかのファイルを開く"""
    real_page = imaging._pdf_page

    def broken_page(path, pdf, index):
        if path == files[1]:
            raise RuntimeError("ページを読めない")
        return real_page(path, pdf, index)

    monkeypatch.setattr(imaging, "_pdf_page", broken_page)
    open_files(window, list(files))
    assert "PDF の1ページ目を読めません: scan.pdf" in window.shown[-1]
    assert [d.path for d in window.docs] == [files[0]]


def test_folders_are_remembered(new_window, files, tmp_path, monkeypatch):
    """開いた場所と出力先のフォルダを覚え、次に起動したときに、選ぶ画面で最初に見せる"""
    dialog = QtWidgets.QFileDialog
    starts = []  # 選ぶ画面に渡した、最初に見せるフォルダ（渡さなければ空）

    def start_of(args):
        return args[2] if len(args) > 2 else ""

    monkeypatch.setattr(
        dialog, "getOpenFileNames", lambda *a: (starts.append(start_of(a)), ([], ""))[1]
    )
    monkeypatch.setattr(
        dialog, "getExistingDirectory", lambda *a: (starts.append(start_of(a)), "")[1]
    )
    win = new_window()
    win.open_dialog()
    win.open_folder_dialog()
    assert starts == ["", ""]  # まだ覚えていない
    open_and_wait(win, list(files))
    win.step(1)
    out_dir = tmp_path / "出力"
    out_dir.mkdir()
    win.export_flow.export_to(out_dir)
    wait(win, lambda: not win.exporting)
    win.close()

    win = new_window()  # 次に起動したとき
    starts.clear()
    win.open_dialog()
    win.open_folder_dialog()
    open_and_wait(win, list(files))
    win.step(1)
    win.export_flow.export_dialog()
    assert starts == [str(files[0].parent), str(files[0].parent), str(out_dir)]
    # 覚えていた出力先がなくなっていれば、出力するファイルのフォルダを見せる
    for p in out_dir.iterdir():
        p.unlink()
    out_dir.rmdir()
    win.export_flow.export_dialog()
    assert starts[-1] == str(files[0].parent)


def test_page_is_deleted_after_asking_and_can_be_undone(window, files, monkeypatch):
    """ページを削除するときは毎回確かめ、削除の取り消しと元に戻すで戻せる"""
    open_and_wait(window, list(files))  # 1ページずつの2ファイル
    box = QtWidgets.QMessageBox.StandardButton
    answer = [box.No]
    asked = ask_with(monkeypatch, answer)
    window.toggle_page_deleted()
    text, _ = asked[-1]
    assert text.startswith("scan.pdf の 1ページを削除しますか？")
    assert "削除したページは、墨消しPDFに入れません（削除は取り消せます）。" in text
    assert not window.pages[0].deleted  # 「いいえ」なら削除しない

    window.docs[0].exported = True
    answer[0] = box.Yes
    window.toggle_page_deleted()
    st = window.pages[0]
    assert st.deleted and not window.docs[0].exported  # 未出力に戻る
    assert window.page_items[0].text(0) == app.reviewtext.joined(
        app.reviewtext.page_parts(st)
    )
    assert window.act_delete_page.text() == "削除を取り消す"
    assert not window.act_left.isEnabled()  # 削除したページは回せない
    assert window.banner.isVisible() and window.banner.text().startswith(
        "削除したページ"
    )
    masked = first_masked(window)
    window.toggle_box(masked)  # 削除したページでは、範囲の操作を受け付けない
    assert masked.active and not window.undo.canRedo()

    window.undo.undo()
    assert not st.deleted
    window.undo.redo()
    assert st.deleted
    count = len(asked)
    window.toggle_page_deleted()  # 削除を取り消すときは確かめない
    assert not st.deleted and len(asked) == count

    # 削除したページは、画像を薄くし、枠を灰色の点線にし、ページの上の帯で知らせる。取り消せば元に戻る
    view = window.view
    window.toggle_page_deleted()
    assert view.pixmap_item.opacity() < 1
    assert window.banner.isVisible()
    assert window.banner.text() == app.reviewtext.page_messages(st)[0][0]
    assert all(
        i.pen().style() == Qt.PenStyle.DashLine
        and i.brush().style() == Qt.BrushStyle.NoBrush
        for i in view.items
    )
    window.toggle_page_deleted()
    assert view.pixmap_item.opacity() == 1 and not window.banner.isVisible()
    assert any(i.pen().style() == Qt.PenStyle.SolidLine for i in view.items)

    # 回して元に戻す履歴が消えても、削除の印は残る
    window.toggle_page_deleted()
    window.goto_page(1)
    window.rotate(1)
    assert not window.undo.canUndo() and st.deleted


def test_rotating_edited_page_is_cancelled_by_no(window, files, monkeypatch):
    """解除・追加したページを回すときは、編集した内容が消えることを確かめ、「いいえ」なら回さず、編集した内容も
    残す"""
    open_and_wait(window, [files[0]])
    st = window.pages[0]
    masked = first_masked(window)
    click_box(window, masked)
    assert not masked.active
    answer = [QtWidgets.QMessageBox.StandardButton.No]
    asked = ask_with(monkeypatch, answer)
    turns = st.result.quarter_turns
    window.rotate(1)
    assert len(asked) == 1 and asked[0][0].endswith("回しますか？")
    assert st.result is not None and st.result.quarter_turns == turns
    assert not masked.active and masked in st.result.boxes
    # 後始末で画面を閉じるとき、作業の破棄の確認に「はい」と答える（「いいえ」のままだと、画面と解析のプロセスが残る）
    answer[0] = QtWidgets.QMessageBox.StandardButton.Yes


def test_deleted_notice_is_shown_above_the_page(window, files, monkeypatch):
    """ページを削除したことを示す文は、解析中・解析済み・処理できないページのどれでも、ページの上の帯に出す（ページ
    の表示の外なので、拡大してスクロールしても動かない）。取り消したときと、ほかのページへ移ったときに消える"""
    open_and_wait(window, list(files))
    ask_with(monkeypatch, [QtWidgets.QMessageBox.StandardButton.Yes])
    banner = window.banner
    window.toggle_page_deleted(0)  # 解析済みのページ
    assert banner.isVisible() and banner.tone is widgets.Tone.ERROR
    assert banner.geometry().bottom() < window.view.geometry().top()
    window.goto_page(1)  # ほかのページへ移ると消える
    assert not banner.isVisible()
    window.goto_page(0)
    assert banner.isVisible()
    window.toggle_page_deleted(0)  # 取り消すと消える
    assert not banner.isVisible()
    # 解析中のページ・処理できないページを削除しても出す
    st = window.pages[1]
    st.result = None
    window.toggle_page_deleted(1)
    assert banner.isVisible()
    st.error = "OSError: 試験"
    window.show_current()
    assert banner.isVisible()


def test_failed_page_view_tells_how_to_export(window, files, monkeypatch):
    """処理できないページを表示すると、削除すればほかのページを出力できることを知らせる"""
    open_and_wait(window, list(files))
    st = window.pages[1]
    st.result, st.error = None, "OSError: 試験"
    window.goto_page(1)

    def shown_text():
        return "".join(
            i.toPlainText()
            for i in window.view.scene().items()
            if hasattr(i, "toPlainText")
        )

    assert "このページを処理できませんでした。\nOSError: 試験" in shown_text()
    assert (
        "このページを削除すると、このファイルのほかのページを出力できます。"
        in shown_text()
    )
    assert (
        "そのページを削除しないと、そのファイルは出力しません" in window.status.tips()
    )
    ask_with(monkeypatch, [QtWidgets.QMessageBox.StandardButton.Yes])
    window.toggle_page_deleted()
    assert "削除したので、このファイルのほかのページを出力できます。" in shown_text()


def view_text(win) -> str:
    """ページの表示に出ている文"""
    return "".join(
        i.toPlainText() for i in win.view.scene().items() if hasattr(i, "toPlainText")
    )


def test_pages_over_budget_are_drawn_again_when_shown(
    window, files, tmp_path, monkeypatch
):
    """作業画像は予算の分だけ持ち（解析の結果は作業画像を持たない）、手放したページは、表示するときに解析のプロセスで
    元のファイルから描き直して、解析のときと同じ画素で表示する。描き直すあいだは「表示の準備をしています…」。範囲の操作・
    元に戻す・出力は今までどおり"""
    import numpy as np
    import pdfcheck
    from conftest import cached_analyze

    from sumikeshi_studio import export, workcache

    monkeypatch.setattr(workcache, "budget_for", lambda available: 1)
    drawn = []
    real_call = window.analyzer.call

    def spy(fn, *args, callback):
        drawn.append(args[0].path)
        real_call(fn, *args, callback=callback)

    monkeypatch.setattr(window.analyzer, "call", spy)
    open_and_wait(window, list(files))
    assert all(st.result.work is None for st in window.pages)
    assert set(window.cache.images) == {0}  # 表示しているページだけ（予算が足りない）
    for index in (1, 0, 1):
        window.goto_page(index)
        assert view_text(window) == "表示の準備をしています…"
        wait(window, lambda: window.view.pixmap_item is not None)
        st = window.pages[index]
        want = cached_analyze(st.source).work
        assert np.array_equal(window.cache.get(index), want)
        assert set(window.cache.images) == {index} and st.viewed
    assert drawn == [files[1], files[0], files[1]]  # 予算がないので、先に描いておかない
    # 範囲の操作と元に戻すは今までどおり
    box = window.pages[1].result.boxes[0]
    click_box(window, box)
    assert not box.active
    window.undo.undo()
    assert box.active
    # 出力は、作業画像の高さ・幅で範囲を換算するので、今までどおり墨消しする
    out = tmp_path / "out"
    out.mkdir()
    window.export_flow.export_to(out)
    wait(window, lambda: not window.exporting)
    names = export.output_paths([d.path for d in window.docs], out)
    for path, st in zip(names, window.pages):
        job = export.PageJob.of(st.source, st.result)
        assert pdfcheck.problems(path, [job]) == []


def test_pages_ahead_are_drawn_beforehand_one_by_one(window, scan, monkeypatch):
    """表示しているページの先のページは、予算に収まるところまで、1ページずつ先に描いておく（ページを進めるときに
    待たない）。前のページは、予算を超えたら手放す"""
    from sumikeshi_studio import workcache

    paths = [scan("2027bun_01", filled=True), scan("2026bun_04"), scan("2026bun_06")]
    open_and_wait(window, paths)
    # 開いたときに頼んだ描き直しが終わるのを待つ（途中で残ると、下でキャッシュを空にした後に結果が届き、頼んだ順が
    # 変わるため）
    wait(window, lambda: not window.drawing and window.prefetching is None)
    size = window.work_size(0)
    # 2ページ分の予算にし、全部を手放してから、1ページ目を表示し直す。ページを移すときのメモリの確かめ（最大で1秒に
    # 1回）が、空いているメモリから予算を決め直さないようにする（決め直すと予算が32MiB以上に戻り、3ページとも
    # 手放されないので、下の2つ目の待ちが終わらない。描き直しが遅れて前の確かめから1秒を超えたときに起きていた）
    monkeypatch.setattr(window.watch, "update", lambda available, cached: 2 * size)
    window.cache.budget = 2 * size
    window.cache.images.clear()
    calls = []
    real_call = window.analyzer.call

    def spy(fn, *args, callback):
        calls.append(
            window.pages.index(next(st for st in window.pages if st.source == args[0]))
        )
        real_call(fn, *args, callback=callback)

    monkeypatch.setattr(window.analyzer, "call", spy)
    window.goto_page(0)
    wait(window, lambda: set(window.cache.images) == {0, 1})
    assert calls == [
        0,
        1,
    ]  # 表示しているページ、続けて次のページ（予算に収まるところまで）
    window.goto_page(1)  # 描いておいたので、すぐに表示する
    assert window.view.pixmap_item is not None
    wait(
        window, lambda: set(window.cache.images) == {1, 2}
    )  # 前のページを手放し、次を描いておく
    assert calls == [0, 1, 2]
    assert workcache.AHEAD == 10


def test_changed_input_is_not_drawn_again(window, files, tmp_path, monkeypatch):
    """手放したページを表示するときに、元のファイルが開いた後に変わっていれば、描き直さずに理由を出す"""
    import shutil

    from sumikeshi_studio import export, workcache

    monkeypatch.setattr(workcache, "budget_for", lambda available: 1)
    copies = []
    for i, f in enumerate(files):
        copies.append(tmp_path / f"{i}.pdf")
        shutil.copy(f, copies[-1])
    open_and_wait(window, copies)
    assert window.cache.get(1) is None
    with copies[1].open("ab") as f:
        f.write(b"%% changed\n")
    window.goto_page(1)
    assert view_text(window) == export.CHANGED
    assert window.cache.get(1) is None and not window.drawing


def test_low_memory_lowers_budget_and_limits_processes_silently(
    window, files, gui_env, monkeypatch
):
    """空いているメモリが少なくなったら、キャッシュの予算を最小にし、解析のプロセスを1つにする。画面では知らせない。
    空きが戻れば（3回続けて）、予算を戻す"""
    from sumikeshi_studio import workcache

    shown, _ = gui_env
    open_and_wait(window, list(files))
    opened = window.cache.budget
    limited = []
    monkeypatch.setattr(window.analyzer, "limit_to_one", lambda: limited.append(True))
    free = {"bytes": 100 * workcache.MIB}
    monkeypatch.setattr(app, "available_memory", lambda: free["bytes"])
    before = list(shown)
    window.memory_checked = 0.0
    window.goto_page(1)
    assert window.cache.budget == workcache.MIN_BUDGET and window.watch.low
    assert limited and shown == before  # 知らせない
    free["bytes"] = 8 * 1024 * workcache.MIB
    for _ in range(workcache.RECOVER_CHECKS):
        window.memory_checked = 0.0
        window.goto_page(0)
    assert not window.watch.low and window.cache.budget == opened


def test_page_list_menu_deletes_page(window, files, monkeypatch):
    open_and_wait(window, list(files))
    ask_with(monkeypatch, [QtWidgets.QMessageBox.StandardButton.Yes])
    (action,) = window.page_menu(1).actions()
    assert action.text() == "このページを削除\tDelete"  # 右端に Delete キーを示す
    action.trigger()
    assert window.pages[1].deleted and window.current == 1
    (action,) = window.page_menu(1).actions()
    assert action.text() == "削除を取り消す"


def test_delete_key_in_list_deletes_page(window, files, monkeypatch):
    """一覧のページの行で Delete キーを押すと、確かめてからページを削除する。ファイルの行・削除したページ・
    ページの表示・出力のあいだは、何もしない"""
    open_and_wait(window, list(files))  # 1ページずつの2ファイル
    box = QtWidgets.QMessageBox.StandardButton
    answer = [box.No]
    asked = ask_with(monkeypatch, answer)
    page_list = window.page_list
    assert "Delete キー" in window.act_delete_page.toolTip()
    window.goto_page(1)
    assert page_list.currentItem() is window.page_items[1]
    QTest.keyClick(page_list, Qt.Key.Key_Delete)
    text, _ = asked[-1]
    assert text.startswith("scan.pdf の 1ページを削除しますか？")
    assert not window.pages[1].deleted  # 「いいえ」なら削除しない
    answer[0] = box.Yes
    QTest.keyClick(page_list, Qt.Key.Key_Delete)
    assert window.pages[1].deleted and len(asked) == 2
    assert window.act_delete_page.toolTip() == "削除を取り消す"
    QTest.keyClick(page_list, Qt.Key.Key_Delete)  # 削除したページでは、取り消さない
    assert window.pages[1].deleted and len(asked) == 2

    # ファイルの行を選んでいるとき（そのファイルのページは削除しない）
    window.goto_page(0)
    page_list.blockSignals(True)
    page_list.setCurrentItem(window.doc_items[0])
    page_list.blockSignals(False)
    assert page_list.currentItem() is window.doc_items[0]
    QTest.keyClick(page_list, Qt.Key.Key_Delete)
    assert not window.pages[0].deleted and len(asked) == 2
    # ページの表示で押したとき
    window.goto_page(0)
    QTest.keyClick(window.view, Qt.Key.Key_Delete)
    assert not window.pages[0].deleted and len(asked) == 2
    # 出力のあいだ
    window.export_flow.exporting = True
    QTest.keyClick(page_list, Qt.Key.Key_Delete)
    window.export_flow.exporting = False
    assert not window.pages[0].deleted and len(asked) == 2


def test_failed_page_can_be_deleted_to_export(window, two_pages, tmp_path, monkeypatch):
    """処理できないページを削除すれば、残りのページを墨消しして出力できる。削除したページは墨消しPDFに入れず、
    結果の画面と作業ログに示す"""
    import pypdf

    open_and_wait(window, [two_pages])
    st = window.pages[1]
    st.result, st.error = None, "OSError: 試験"
    window.update_state()
    assert window.export_targets() == []
    ask_with(monkeypatch, [QtWidgets.QMessageBox.StandardButton.Yes])
    window.toggle_page_deleted(1)
    assert window.export_targets() == window.docs
    out_dir = tmp_path / "出力"
    out_dir.mkdir()
    window.export_flow.export_to(out_dir)
    wait(window, lambda: not window.exporting)
    assert len(pypdf.PdfReader(out_dir / "二ページ_redacted.pdf").pages) == 1
    assert "　　削除したページ：2" in window.shown[-1]
    (row,) = log_rows(tmp_path)
    assert (row["Deleted pages"], row["Pages"]) == ("2", "1")


def test_file_with_all_pages_deleted_is_not_output(
    window, files, tmp_path, monkeypatch
):
    open_and_wait(window, list(files))
    window.step(1)
    ask_with(monkeypatch, [QtWidgets.QMessageBox.StandardButton.Yes])
    window.toggle_page_deleted(0)
    assert window.export_targets() == [window.docs[1]]
    out_dir = tmp_path / "出力"
    out_dir.mkdir()
    window.export_flow.export_to(out_dir)
    wait(window, lambda: not window.exporting)
    assert [p.name for p in out_dir.iterdir()] == ["scan_redacted_2.pdf"]
    assert (
        "・scan.pdf：全ページを削除したため、出力しませんでした。" in window.shown[-1]
    )


def test_page_not_analyzed_yet_is_analyzed_first(window, files, monkeypatch):
    """まだ解析していないページへ移ると、そのページから先に解析するよう頼む"""
    open_and_wait(window, list(files))
    asked = []
    monkeypatch.setattr(
        window.analyzer, "prioritize", lambda token, index: asked.append((token, index))
    )
    window.pages[1].result = None  # 2ページ目をまだ解析していないことにする
    window.goto_page(1)
    assert asked == [(window.token, 1)]
    window.goto_page(0)  # 解析済みのページ：頼まない
    assert asked == [(window.token, 1)]


def test_arrow_keys_in_list_move_across_files(window, files):
    """一覧で上下の矢印キーを押すと、ファイルの境目を越えて、ページを1つずつ移る"""
    open_and_wait(window, list(files))  # 1ページずつの2ファイル
    page_list = window.page_list
    window.goto_page(1)  # 2ファイル目の1ページ目
    QTest.keyClick(page_list, Qt.Key.Key_Up)
    assert window.current == 0
    QTest.keyClick(page_list, Qt.Key.Key_Up)  # 最初のページでは動かない
    assert window.current == 0
    QTest.keyClick(page_list, Qt.Key.Key_Down)
    assert window.current == 1
    assert page_list.currentItem() is window.page_items[1]
    # ファイルの行を選ぶと、そのファイルの1ページ目を出す。そこから上で、前のファイルに戻れる
    page_list.setCurrentItem(window.doc_items[1])
    assert window.current == 1
    QTest.keyClick(page_list, Qt.Key.Key_Up)
    assert window.current == 0


def test_edit_after_export_marks_unexported(window, files):
    open_and_wait(window, [files[0]])
    click_box(window, first_masked(window))
    window.docs[0].exported = True  # 出力した後の状態にする
    window.undo.undo()  # 出力した後に解除・追加をすると、未出力に戻る
    assert not window.docs[0].exported
    assert window.export_targets() == [window.docs[0]]


def test_page_needing_review_is_marked(window, scan):
    """どの様式の見本にも類似しないページ（従たる給与の申告書）は墨消しせず、一覧に「様式不明」と要確認、画面下に
    「帳票：様式不明」を出す。ページの上の帯には要確認の文だけを出す"""
    open_and_wait(window, [scan("2026bun_08")])
    assert window.pages[0].result.boxes == []
    assert window.page_items[0].text(0).startswith("1ページ　様式不明")
    assert "⚠ 要確認" in window.page_items[0].text(0)
    assert window.banner.isVisible()
    assert "様式を判別できず、墨消ししていません" in window.banner.text()
    assert "墨消しする範囲がありません" not in window.banner.text()
    assert "帳票：様式不明" in window.status.text()
    assert "様式を判別できず" not in window.status.text()


# 確認画面を出すまでに読み込んではいけない、重いライブラリと、それを読み込むモジュール
HEAVY = [
    "pymupdf",
    "pypdfium2",
    "rapidocr",
    "cv2",
    "numpy",
    "onnxruntime",
    "PIL",
    "sumikeshi_studio.imaging",
    "sumikeshi_studio.pipeline",
    "sumikeshi_studio.export",
    "sumikeshi_studio.forms",
    "sumikeshi_studio.orientation",
]


@pytest.mark.parametrize(
    "module", ["sumikeshi_studio.app", "sumikeshi_studio.launcher"]
)
def test_screen_module_does_not_load_heavy_libraries(module):
    """画面を先に出すため、墨消しの画面のモジュールと、起動するときに読み込む起動の画面のモジュールは、重いライブラリ
    を読み込まない"""
    code = (
        f"import sys, {module}; "
        f"print(','.join(m for m in {HEAVY!r} if m in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == ""


def test_screen_process_does_not_load_ocr_model(files, tmp_path):
    """本物の解析のプロセスで解析し、画面のプロセスでは OCR のモデル（onnxruntime）を読み込まない"""
    install_bundled(tmp_path / "appdata" / "SumikeshiStudio" / "forms")
    code = textwrap.dedent(
        f"""
        import sys, time
        from pathlib import Path
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        from sumikeshi_studio import app

        QSettings.setPath(
            QSettings.Format.IniFormat, QSettings.Scope.UserScope, {str(tmp_path / "ini")!r}
        )
        qa = QApplication([])
        win = app.MainWindow()
        win.open_paths([Path({str(files[0])!r})])  # 起動の準備が終わってから開く
        end = time.time() + 300
        while not (win.pages and all(st.ready or st.error for st in win.pages)):
            qa.processEvents()
            time.sleep(0.05)
            assert time.time() < end, "時間内に終わらなかった"
        st = win.pages[0]
        print(st.error or st.result.form, len(st.result.boxes) if st.result else -1)
        print("onnxruntime" in sys.modules)
        win.docs[0].exported = True
        win.close()
        """
    )
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "LOCALAPPDATA": str(tmp_path / "appdata"),
    }
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
        check=True,
    )
    assert out.stdout.split() == ["扶養控除等申告書", str(FIELDS), "False"]


def test_files_opened_while_starting_are_opened_after(new_window, files):
    """起動の準備の途中で開いたファイルは、準備が終わってから開く。準備の途中にまた開いたときは、後で選んだファイルを
    開く"""
    win = new_window(wait_ready=False)
    assert not win.ready
    win.open_paths([files[1]])
    win.open_paths(list(files))
    assert win.docs == [] and win.pending.selection.paths == list(files)
    assert "準備が終わりしだい開きます" in win.status.tips()
    wait(win, lambda: win.ready and len(win.docs) == 2)
    assert win.pending is None


def test_status_shows_preparing_and_failed_preload(window):
    """画面下の起動の準備のラベル（準備中と、準備の失敗）の文・種類・マウスを重ねたときの説明"""
    window.ready = False
    window.update_state()
    text, _, tip = app.reviewtext.wait_parts("", False, [], None)[0]
    assert window.status.parts[0] == (text, widgets.Tone.NEUTRAL, tip)
    error = "解析のプロセスを起動できませんでした。"
    window.preload_done(error)
    text, _, tip = app.reviewtext.wait_parts(error, True, [], None)[0]
    assert window.status.parts[0] == (text, widgets.Tone.ERROR, tip)
    assert window.shown[-1] == (
        "起動の準備ができませんでした。\n解析のプロセスを起動できませんでした。"
    )


def test_box_edits_and_undo_are_blocked_while_exporting(window, files):
    """出力のあいだは、範囲の操作と「元に戻す」「やり直す」を受け付けない"""
    open_and_wait(window, [files[0]])
    boxes = window.pages[0].result.boxes
    box = boxes[0]
    window.toggle_box(box)  # 解除する
    assert not box.active and window.act_undo.isEnabled()

    window.export_flow.exporting = True
    window.update_state()
    assert not window.act_undo.isEnabled() and not window.act_redo.isEnabled()
    window.toggle_box(box)
    window.add_box((10.0, 10.0, 50.0, 50.0))
    assert not box.active and len(boxes) == FIELDS  # 変わらない
    # 履歴が変わって、履歴の側がボタンを押せるようにしても、出力中は押せないまま
    window.undo.undo()
    assert window.undo.canRedo() and not window.act_redo.isEnabled()

    window.export_flow.exporting = False
    window.update_state()
    assert window.act_redo.isEnabled()


# ---------------------------------------------------------------- 墨消しの画面の見た目


def test_redaction_screen_in_english(new_window, files, tmp_path):
    """英語の画面の墨消しの画面：ツールバー・凡例・ボタン・一覧の題名・ファイルを開けないときの文を英語にする"""
    i18n.set_language(i18n.EN)
    window = new_window()
    assert window.windowTitle() == "Sumikeshi Studio"
    texts = [a.text() for a in window.tool_bar.actions() if a.text()]
    assert texts[:7] == [
        "Open files",
        "Open folder",
        "Previous page",
        "Next page",
        "Rotate left 90°",
        "Rotate right 90°",
        "Delete page",
    ]
    assert window.act_undo.text() == "Undo" and window.act_redo.text() == "Redo"
    assert window.act_undo.toolTip() == "Undo (Ctrl+Z)"
    assert {"Fit to window", "Zoom in", "Zoom out"} <= set(texts)
    assert window.export_button.text() == "Export redacted PDF"
    assert window.help_button.text() == "How to use"
    legend = [w.text() for w in window.legend_bar.findChildren(QtWidgets.QLabel)]
    assert legend == [
        "Fields defined in the form",
        "Manually added area",
        "Not redacted (fields turned off)",
    ]
    assert (
        window.findChildren(QtWidgets.QDockWidget)[0].windowTitle() == "Files and pages"
    )
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 broken")
    open_files(window, [broken])
    assert window.shown[-1] == (
        f"The following files could not be opened.\nCannot open the PDF (it may be broken): {broken.name}"
    )


def test_export_in_english(new_window, files, tmp_path, monkeypatch):
    """英語の画面の出力：出力先を選ぶダイアログの題名、進み具合、結果のダイアログの文とボタン、フォルダを開けなかった
    ときの文を英語にする。開けなかったときの文に入れる語句も英語の文の中で自然な形にする"""
    i18n.set_language(i18n.EN)
    window = new_window()
    dialogs = []
    monkeypatch.setattr(
        widgets.ResultDialog,
        "exec",
        lambda self: dialogs.append(self) or self.show() or 0,
    )
    titles = []
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getExistingDirectory",
        lambda parent, title, start: titles.append(title) or "",
    )
    open_and_wait(window, list(files))
    window.export_flow.export_dialog()
    assert titles == ["Select output folder for redacted PDFs"]
    for i in range(len(window.pages)):
        window.goto_page(i)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    window.export_flow.export_to(out_dir)
    wait(window, lambda: not window.exporting)
    done = dialogs[-1]
    assert done.windowTitle() == "Sumikeshi Studio"
    assert [b.text() for b in done.folder_buttons] == ["Open output folder"]
    assert done.close_button.text() == "Close"
    monkeypatch.setattr(widgets.QDesktopServices, "openUrl", lambda url: False)
    done.folder_buttons[0].click()
    assert window.shown[-1] == (
        f"Could not open the output folder ({out_dir}). Check that you are connected to the shared folder."
    )
    done.close_button.click()


def test_help_button_shows_operations(window):
    """長い操作の説明は画面に出し続けず、「操作の説明」のボタンでダイアログに出す"""
    # ウィンドウの題名はツールの名前
    assert window.windowTitle() == APP_NAME
    window.help_button.click()
    assert window.shown[-1] == reviewtext.help_text()
    assert "・手で追加した範囲を右クリック：削除する" in window.shown[-1]
    legend = [w.text() for w in window.legend_bar.findChildren(QtWidgets.QLabel)]
    assert legend == [
        "様式で決めた欄",
        "手で追加した範囲",
        "墨消ししない（解除した欄）",
    ]
    assert len(window.legend_bar.findChildren(widgets.Swatch)) == 3


def test_empty_view_message_uses_widget_font(window):
    """ページがないときの案内の文は、部品の書体（名前のある書体）で描く（名前が空の書体にすると、Windows では
    MS Sans Serif に置き換わり、Qt の警告が出るため）"""
    texts = [i for i in window.view.scene().items() if hasattr(i, "toPlainText")]
    assert [t.toPlainText() for t in texts] == [app.EMPTY_TEXT]
    font = texts[0].font()
    assert font.family() != "" and font.family() == window.view.font().family()
    assert font.pointSize() == 14


def test_toolbar_is_text_only_without_icon_font(window):
    """記号の書体がない環境（この環境）では、ツールバーを文字だけにする"""
    assert widgets.icon_font_family() is None
    for action, _, _ in window.tool_icons:
        button = window.tool_bar.widgetForAction(action)
        assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextOnly
    assert window.act_open.text() == "ファイルを開く"


def test_toolbar_icons_with_icon_font(new_window, monkeypatch):
    """記号の書体があれば、よく知られた操作（回す・元に戻す・拡大など）は記号だけ、ほかは記号と文字にする。
    記号だけのボタンは、マウスを重ねると名前とキーが出る"""
    family = QtGui.QFontDatabase.families()[0]  # 記号の書体の代わり
    monkeypatch.setattr(widgets, "icon_font_family", lambda families=None: family)
    window = new_window()
    styles = {
        action.text(): window.tool_bar.widgetForAction(action).toolButtonStyle()
        for action, _, _ in window.tool_icons
    }
    only, beside = (
        Qt.ToolButtonStyle.ToolButtonIconOnly,
        Qt.ToolButtonStyle.ToolButtonTextBesideIcon,
    )
    assert styles["左に90度回す"] == styles["拡大"] == styles["元に戻す"] == only
    assert (
        styles["ファイルを開く"]
        == styles["前のページ"]
        == styles["このページを削除"]
        == beside
    )
    assert not window.act_left.icon().isNull()
    assert window.act_undo.toolTip() == "元に戻す（Ctrl+Z）"


def test_page_list_and_status_use_labels(window, files):
    """ページの一覧は、行の文のあとにラベルを並べる。画面下は項目ごとのラベルで、注意の要るものに色を付ける"""
    open_and_wait(window, list(files))
    item = window.page_items[1]
    role = widgets.LabelDelegate
    assert item.data(0, role.MAIN_ROLE) == "1ページ　保険料控除申告書"
    assert ("未表示", "neutral") in item.data(0, role.LABELS_ROLE)
    assert item.text(0) == app.reviewtext.joined(
        app.reviewtext.page_parts(window.pages[1])
    )
    labels = {lb.text(): lb.tone for lb in window.status.labels if lb.isVisible()}
    assert labels["まだ表示していないページ：1"] is widgets.Tone.WARN
    # 配布用パッケージのファイルをそのままコピーした様式なので、保存の記録はない
    assert labels["帳票：扶養控除等申告書（第0版）"] is widgets.Tone.NEUTRAL


def test_forms_screen_uses_labels_and_primary_button(window):
    """様式の一覧の状態はラベルにし、「新しい様式を登録」を主なボタンにする"""
    dlg = list_dialog(window)
    assert dlg.b_register.text() == "新しい様式を登録" and dlg.b_register.isDefault()
    assert [b.text() for b in (dlg.b_edit, dlg.b_replace, dlg.b_delete)] == [
        "編集",
        "用紙を置き換える",
        "削除",
    ]
    r = [r[0] for r in dlg.rows].index("nta-fuyou-r9")
    labels = dlg.table.cellWidget(r, len(formlist.COLUMNS) - 1).findChildren(
        widgets.StatusLabel
    )
    assert [(lb.text(), lb.tone) for lb in labels] == [
        ("✓ 問題なし", widgets.Tone.GOOD)
    ]
    assert dlg.table.item(r, len(formlist.COLUMNS) - 1).text() == ""


def test_actions_after_dialog_do_not_apply_to_reopened_files(
    window, files, tmp_path, monkeypatch
):
    """確認のメッセージに答えるのを待つあいだにファイルを開き直していたら、確認の後に回す・削除する・出力するをしない"""
    open_and_wait(window, list(files))
    box = QtWidgets.QMessageBox
    jobs = []
    real_start = window.start_analysis
    monkeypatch.setattr(
        window, "start_analysis", lambda js: jobs.extend(js) or real_start(js)
    )

    def reopen(*a, **k):
        window.open_paths([files[1]])  # 確認の途中で開き直した
        return box.StandardButton.Yes

    patch_ask(monkeypatch, reopen)
    click_box(window, first_masked(window))
    window.rotate(1)
    assert all(j.quarter_turns is None for j in jobs)  # 回す解析を頼んでいない
    wait(
        window,
        lambda: window.opening is None and all(st.ready for st in window.pages),
    )
    assert [d.path for d in window.docs] == [files[1]]
    window.toggle_page_deleted(0)
    assert not any(st.deleted for st in window.pages)
    open_and_wait(
        window, list(files)
    )  # 2ファイル目は表示していないので、出力の前に警告が出る
    out_dir = tmp_path / "出力"
    out_dir.mkdir()
    window.export_flow.export_to(out_dir)
    assert not window.exporting and list(out_dir.iterdir()) == []


def _mouse(view, kind, pos):
    """ページの表示に、左のボタンのマウスの出来事を送る"""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    p = QPointF(pos)
    event = QMouseEvent(
        kind,
        p,
        view.viewport().mapToGlobal(p),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton
        if kind != QtCore.QEvent.Type.MouseButtonRelease
        else Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QtWidgets.QApplication.sendEvent(view.viewport(), event)


def _right(win, kind, pos, buttons=None):
    """墨消しの画面のウィンドウに、右のボタンのマウスの出来事を送る（pos はページの表示の上の位置）。ウィンドウに
    送るので、右クリックのメニューの合図も、Qt が本物の操作と同じく作る"""
    p = win.view.viewport().mapTo(win, QtCore.QPoint(*pos))
    handle = win.windowHandle()
    right, none = Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier
    if kind == "press":
        QTest.mousePress(handle, right, none, p)
    elif kind == "release":
        QTest.mouseRelease(handle, right, none, p)
    else:
        QTest.mouseMove(handle, p)
    win.qapp.processEvents()


def test_right_drag_scrolls_without_menu(window, files, monkeypatch):
    """右のボタンを押しながら4画素を超えて動かすと、ページの表示をスクロールし、そのあいだは手の形にする。範囲の上で
    押して動かしたときは、離した後に右クリックのメニューを出さない（Windows と同じく、メニューの合図を離したときに
    出す設定で確かめる）。4画素以下なら、今までどおりメニューを出す。削除したページでもスクロールできる"""
    hints = window.qapp.styleHints()
    before = hints.contextMenuTrigger()
    hints.setContextMenuTrigger(Qt.ContextMenuTrigger.Release)
    try:
        open_and_wait(window, [files[0]])
        view = window.view
        view.zoom(3)
        window.qapp.processEvents()
        menus = []

        class Menu(QtWidgets.QMenu):
            """右クリックのメニュー：出さずに、出したことを残す"""

            def exec(self, *a):
                menus.append(self)

        monkeypatch.setattr(app.pageview, "QMenu", Menu)
        box = first_masked(window)
        view.centerOn(box.x + box.w / 2, box.y + box.h / 2)
        window.qapp.processEvents()
        center = view.mapFromScene(box.x + box.w / 2, box.y + box.h / 2)
        start = (center.x(), center.y())
        h, v = view.horizontalScrollBar(), view.verticalScrollBar()
        h0, v0 = h.value(), v.value()
        assert h0 + 30 <= h.maximum() and v0 + 20 <= v.maximum() and h0 >= 30
        _right(window, "press", start)
        _right(window, "move", (start[0] + 3, start[1]))
        assert (h.value(), v.value()) == (h0, v0)  # 4画素以下は動かさない
        override = QtWidgets.QApplication.overrideCursor
        assert override() is None
        _right(window, "move", (start[0] - 30, start[1] - 20))
        assert (h.value(), v.value()) == (h0 + 30, v0 + 20)
        assert override().shape() == Qt.CursorShape.ClosedHandCursor
        _right(window, "release", (start[0] - 30, start[1] - 20))
        assert menus == []  # スクロールしたので、メニューを出さない
        assert override() is None
        assert all(b.active for b in window.pages[0].result.boxes)  # 範囲は変えない

        # 少しだけ動かして離せば、今までどおりメニューを出す
        center = view.mapFromScene(box.x + box.w / 2, box.y + box.h / 2)
        start = (center.x(), center.y())
        h1 = h.value()
        _right(window, "press", start)
        _right(window, "move", (start[0] + 2, start[1] + 2))
        _right(window, "release", (start[0] + 2, start[1] + 2))
        assert len(menus) == 1 and h.value() == h1

        # 削除したページでもスクロールできる（範囲の操作ではないため）
        window.toggle_page_deleted(0)
        assert window.pages[0].deleted
        h2 = h.value()
        _right(window, "press", start)
        _right(window, "move", (start[0] + 25, start[1]))
        _right(window, "release", (start[0] + 25, start[1]))
        assert h.value() == h2 - 25 and len(menus) == 1

        # メニューの合図を押したときに出す環境（Windows のほか）では、範囲の上で押すとすぐにメニューを出す。ボタンを
        # 離したことはメニューに届くので、その後にボタンを押さずに動かしてもスクロールしない
        window.toggle_page_deleted(0)
        hints.setContextMenuTrigger(Qt.ContextMenuTrigger.Press)
        center = view.mapFromScene(box.x + box.w / 2, box.y + box.h / 2)
        start = (center.x(), center.y())
        h3 = h.value()
        _right(window, "press", start)
        assert len(menus) == 2
        _right(window, "move", (start[0] + 40, start[1]))
        assert h.value() == h3

        # Ctrl+ホイールの拡大は今までどおり
        scale = view.transform().m11()
        pos = QtCore.QPointF(*start)
        wheel = QtGui.QWheelEvent(
            pos,
            view.viewport().mapToGlobal(pos),
            QtCore.QPoint(),
            QtCore.QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QtWidgets.QApplication.sendEvent(view.viewport(), wheel)
        assert view.transform().m11() == pytest.approx(scale * 1.15)
    finally:
        hints.setContextMenuTrigger(before)


def test_quick_second_click_toggles_again(window, files):
    """欄を素早く2回クリック（2回目はダブルクリックとして届く）すると、2回切り替わって元に戻る"""
    open_and_wait(window, [files[0]])
    box = first_masked(window)
    view = window.view
    center = view.mapFromScene(box.x + box.w / 2, box.y + box.h / 2)
    E = QtCore.QEvent.Type
    for kind in (
        E.MouseButtonPress,
        E.MouseButtonRelease,
        E.MouseButtonDblClick,  # Windows が2回目の押す操作として送るもの
        E.MouseButtonRelease,
    ):
        _mouse(view, kind, center)
    assert box.active
    assert window.undo.count() == 2


def test_box_with_corners_moves_one_corner_or_whole_and_undoes(window, files):
    """角のある範囲（長方形でない範囲。写真で、枠の線の中心に合わせた欄）は、角を結んだ形で表示する。角をドラッグすると
    その角だけが動き、中をドラッグすると全体が動き、どちらも元に戻せる。辺が交わる形（角を向かいの辺の外まで動かす）
    にする操作は、長方形の範囲の小さすぎる操作と同じく受け付けない。クリックで墨消しする・しないが切り替わる"""
    open_and_wait(window, [files[0]])
    box = first_masked(window)
    x, y, w, h = box.rect()
    corners = ((x, y + 6), (x + w, y), (x + w, y + h), (x, y + h + 6))
    box.set_geometry(corners)
    view = window.view
    view.refresh_boxes()
    item = next(i for i in view.items if i.box is box)
    assert [(p.x(), p.y()) for p in item.polygon()] == list(corners)
    tol = 1.5 / view._scale()  # 画面の1画素をページの座標にした大きさの前後

    drag(window, x, y + 6, x - 20, y + 30)  # 左上の角だけを動かす
    assert box.corners[1:] == corners[1:]
    assert abs(box.corners[0][0] - (x - 20)) <= tol
    assert abs(box.corners[0][1] - (y + 30)) <= tol
    window.undo.undo()
    assert box.corners == corners
    window.undo.redo()
    assert box.corners != corners
    window.undo.undo()

    drag(window, x + w / 2, y + h / 2 + 3, x + w / 2 + 40, y + h / 2 + 23)  # 全体
    for (ox, oy), (nx, ny) in zip(corners, box.corners, strict=True):
        assert abs(nx - ox - 40) <= tol and abs(ny - oy - 20) <= tol
    window.undo.undo()
    assert box.corners == corners

    # 左上の角を下の辺の向こうへ動かす：辺が交わる
    done = window.undo.index()
    drag(window, x, y + 6, x + w / 2, y + h + 40)
    assert box.corners == corners and window.undo.index() == done

    click_box(window, box)
    assert not box.active and box.corners == corners
    assert window.undo.index() == done + 1


def shift_drag(win, x0, y0, x1, y1, move=True):
    """Shift を押しながら、ページの座標 (x0, y0) から (x1, y1) までドラッグする（move が False なら押して離すだけ）"""
    view = win.view
    p0, p1 = view.mapFromScene(x0, y0), view.mapFromScene(x1, y1)
    shift = Qt.KeyboardModifier.ShiftModifier
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, shift, p0)
    if move:
        QTest.mouseMove(view.viewport(), p1)
    QTest.mouseRelease(
        view.viewport(), Qt.MouseButton.LeftButton, shift, p1 if move else p0
    )


def test_corner_drag_moves_one_corner_and_shift_resizes_as_rectangle(window, files):
    """墨消しの画面では、長方形の範囲も角をドラッグするとその角だけが動き、角のある範囲になる（元に戻すと長方形に
    戻る）。Shift を押しながら角をドラッグすると、どの範囲も、つかんだ角から最も遠い角を止めた長方形として大きさが
    変わり、角のある範囲は長方形に戻る。Shift で角を押しただけなら変えない"""
    open_and_wait(window, [files[0]])
    view = window.view
    assert view.move_corners
    box = first_masked(window)
    x, y, w, h = box.rect()
    tol = 1.5 / view._scale()  # 画面の1画素をページの座標にした大きさの前後

    drag(window, x, y, x - 20, y - 10)  # 長方形の範囲の左上の角
    assert box.corners is not None and box.corners[1:] == (
        (x + w, y),
        (x + w, y + h),
        (x, y + h),
    )
    assert abs(box.corners[0][0] - (x - 20)) <= tol
    assert abs(box.corners[0][1] - (y - 10)) <= tol
    window.undo.undo()
    assert box.corners is None and box.rect() == (x, y, w, h)

    shift_drag(
        window, x, y, x - 20, y - 10
    )  # 長方形として大きさを変える（右下の角を止める）
    assert box.corners is None
    nx, ny, nw, nh = box.rect()
    assert abs(nx - (x - 20)) <= tol and abs(ny - (y - 10)) <= tol
    assert abs(nx + nw - (x + w)) <= tol and abs(ny + nh - (y + h)) <= tol
    window.undo.undo()
    assert box.rect() == (x, y, w, h)

    corners = ((x, y + 6), (x + w, y), (x + w, y + h), (x, y + h + 6))
    box.set_geometry(corners)
    view.refresh_boxes()
    done = window.undo.index()
    shift_drag(window, x, y + 6, x, y + 6, move=False)  # 押しただけ
    assert box.corners == corners and window.undo.index() == done
    shift_drag(
        window, x, y + 6, x - 10, y
    )  # 角のある範囲：最も遠い角（右下）を止めた長方形に戻る
    assert box.corners is None
    nx, ny, nw, nh = box.rect()
    assert abs(nx - (x - 10)) <= tol and abs(ny - y) <= tol
    assert abs(nx + nw - (x + w)) <= tol and abs(ny + nh - (y + h)) <= tol
    window.undo.undo()
    assert box.corners == corners


def test_corner_cursor_follows_drag_behavior(window, files):
    """墨消しの画面の角の上のマウスの形：その角だけを動かすので十字の矢印、Shift を押していれば、長方形として大きさを
    変える斜めの矢印（左上・右下は ↖↘、右上・左下は ↗↙）。範囲を描く画面と同じ作り（move_corners が False）なら、
    長方形の範囲の角は斜めの矢印"""
    from types import SimpleNamespace

    open_and_wait(window, [files[0]])
    view = window.view
    box = first_masked(window)
    item = next(i for i in view.items if i.box is box)
    x, y, w, h = box.rect()
    none = Qt.KeyboardModifier.NoModifier
    shift = Qt.KeyboardModifier.ShiftModifier
    shapes = Qt.CursorShape

    def cursor(px, py, modifiers):
        event = SimpleNamespace(
            pos=lambda: QtCore.QPointF(px, py), modifiers=lambda: modifiers
        )
        item.hoverMoveEvent(event)
        return item.cursor().shape()

    assert cursor(x, y, none) == shapes.SizeAllCursor
    assert cursor(x, y, shift) == shapes.SizeFDiagCursor
    assert cursor(x + w, y, shift) == shapes.SizeBDiagCursor
    assert cursor(x + w / 2, y + h / 2, none) == shapes.PointingHandCursor
    view.move_corners = False
    try:
        assert cursor(x, y, none) == shapes.SizeFDiagCursor
        assert cursor(x + w, y, none) == shapes.SizeBDiagCursor
    finally:
        view.move_corners = True


def test_clipped_and_simple_shapes_of_boxes_with_corners():
    """角のある範囲を動かしてページの外に出た部分は切る（切った辺の分、角が増えることがある）。辺が交わる形は、範囲に
    しない"""
    from sumikeshi_studio.pageview import QPointF, QPolygonF, QRectF, _clipped, _simple

    area = QRectF(0, 0, 100, 100)
    inside = QPolygonF(
        [QPointF(10, 12), QPointF(60, 10), QPointF(62, 40), QPointF(12, 42)]
    )
    assert _clipped(inside, area) == ((10, 12), (60, 10), (62, 40), (12, 42))
    out = QPolygonF(
        [QPointF(-20, 10), QPointF(50, 0), QPointF(52, 30), QPointF(-18, 40)]
    )
    cut = _clipped(out, area)
    assert min(p[0] for p in cut) >= 0 and len(cut) >= 4 and _simple(cut)
    assert _simple(((0, 0), (10, 0), (10, 10), (0, 10)))
    assert not _simple(((0, 0), (10, 10), (10, 0), (0, 10)))  # 8の字
    assert not _simple(((0, 0), (10, 0)))


def test_result_dialog_opens_folders(window, files, tmp_path, monkeypatch):
    """結果のダイアログに「出力先のフォルダを開く」（⚠ や ✗ のときは「作業ログのフォルダを開く」も）があり、押しても
    ダイアログは閉じない。開けなければ理由を示す"""
    from PySide6.QtCore import QUrl

    dialogs = []
    monkeypatch.setattr(
        widgets.ResultDialog,
        "exec",
        lambda self: dialogs.append(self) or self.show() or 0,
    )
    urls = []
    monkeypatch.setattr(
        widgets.QDesktopServices,
        "openUrl",
        lambda url: urls.append(url) or True,
    )
    open_and_wait(window, list(files))
    window.step(1)
    out_dir = tmp_path / "出力"
    out_dir.mkdir()
    window.export_flow.export_to(out_dir)
    wait(window, lambda: not window.exporting)
    ok = dialogs[-1]
    assert [b.text() for b in ok.folder_buttons] == ["出力先のフォルダを開く"]
    assert ok.close_button.isDefault()
    assert not any(b.autoDefault() for b in ok.folder_buttons)
    ok.folder_buttons[0].click()
    assert urls == [QUrl.fromLocalFile(str(out_dir))] and ok.isVisible()
    ok.close_button.click()
    assert not ok.isVisible()

    click_box(window, first_masked(window))  # 直したので、もう一度出力できる
    window.pages[1].viewed = False  # 表示していないページがあるので「⚠」になる
    window.docs[1].exported = False
    window.export_flow.export_to(out_dir)
    wait(window, lambda: not window.exporting)
    warn = dialogs[-1]
    assert [b.text() for b in warn.folder_buttons] == [
        "出力先のフォルダを開く",
        "作業ログのフォルダを開く",
    ]
    monkeypatch.setattr(widgets.QDesktopServices, "openUrl", lambda url: False)
    warn.folder_buttons[0].click()
    assert window.shown[-1] == (
        f"出力先のフォルダを開けませんでした（{out_dir}）。共有フォルダにつながっているか確かめてください。"
    )
    warn.open_folder(tmp_path / "ない", "作業ログのフォルダ")
    assert window.shown[-1].startswith("作業ログのフォルダを開けませんでした（")
    warn.close_button.click()


def test_declining_discard_keeps_current_files(window, files, tmp_path, monkeypatch):
    """出力していないファイルがあるときに、作業を破棄するかに「いいえ」と答えると、開く・フォルダを開く・ドロップ・
    閉じるのどれでも、ファイルを選ぶ画面を出さず、今のファイルをそのまま残す"""
    open_and_wait(window, [files[0]])
    docs = list(window.docs)
    answer = [QtWidgets.QMessageBox.StandardButton.No]
    asked = ask_with(monkeypatch, answer)
    chosen = []
    dialog = QtWidgets.QFileDialog
    monkeypatch.setattr(
        dialog,
        "getOpenFileNames",
        lambda *a, **k: chosen.append(a) or ([str(files[1])], ""),
    )
    monkeypatch.setattr(
        dialog,
        "getExistingDirectory",
        lambda *a, **k: chosen.append(a) or str(tmp_path),
    )
    closed = []
    window.closed.connect(lambda: closed.append(True))
    window.open_dialog()
    window.open_folder_dialog()
    mime = QtCore.QMimeData()
    mime.setUrls([QtCore.QUrl.fromLocalFile(str(files[1]))])
    window.dropEvent(
        QtGui.QDropEvent(
            QtCore.QPointF(10, 10),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    window.close()
    assert len(asked) == 4 and chosen == [] and closed == []
    assert all(
        text.startswith("墨消しPDFをまだ出力していないファイルが1件あります。")
        for text, _ in asked
    )
    assert window.docs == docs and window.isVisible()
    # 後始末で画面を閉じるとき、作業の破棄の確認に「はい」と答える（「いいえ」のままだと、画面と解析のプロセスが残る）
    answer[0] = QtWidgets.QMessageBox.StandardButton.Yes


# 墨消しの画面のツールバーの操作の、自動の確認（Windows の UI オートメーション）で見分けるための名前
TOOL_NAMES = (
    "open_files",
    "open_folder",
    "prev_page",
    "next_page",
    "rotate_left",
    "rotate_right",
    "delete_page",
    "undo",
    "redo",
    "fit",
    "zoom_in",
    "zoom_out",
)


def test_main_window_widgets_have_names_for_automation(window):
    """墨消しの画面の主な部品は、言語によらない名前（objectName）を1つずつ持つ。ツールバーの操作は、ツールバーが
    操作ごとに作るボタンにも同じ名前がある（UI オートメーションで見えるのはボタン）"""
    assert window.objectName() == "main_window"
    bar = window.tool_bar
    for name in TOOL_NAMES:
        [button] = bar.findChildren(QtWidgets.QToolButton, name)
        assert button.defaultAction().objectName() == name
    buttons = [
        bar.widgetForAction(a)
        for a in bar.actions()
        if isinstance(bar.widgetForAction(a), QtWidgets.QToolButton)
    ]
    assert len(buttons) == len(TOOL_NAMES)
    for name, kind in (
        ("export", QtWidgets.QPushButton),
        ("help", QtWidgets.QPushButton),
        ("banner", QtWidgets.QLabel),
        ("page_list", QtWidgets.QTreeWidget),
        ("page_view", QtWidgets.QWidget),
        ("status_labels", QtWidgets.QWidget),
    ):
        assert len(window.findChildren(kind, name)) == 1, name


def test_result_dialog_has_names_for_automation(window, tmp_path):
    """出力の結果のウィンドウと、そのボタンは、言語によらない名前を持つ"""
    dialog = widgets.ResultDialog(
        window,
        "題名",
        "文",
        False,
        [("出力先", tmp_path, "出力先"), ("作業ログ", tmp_path, "作業ログ")],
    )
    assert dialog.objectName() == "export_result"
    assert dialog.close_button.objectName() == "close"
    (text,) = dialog.findChildren(QtWidgets.QLabel, "export_result_text")
    assert text.text() == "文"
    assert [b.objectName() for b in dialog.folder_buttons] == [
        "show_folder_1",
        "show_folder_2",
    ]
    dialog.deleteLater()
