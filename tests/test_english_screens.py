# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""英語の画面で、全部の画面に日本語が出ていないことと、文が部品の幅に収まることのテスト

英語の表の検査（tests/test_structure.py）は、src の日本語の文が英語の表にあることを確かめるが、画面に出すときに
tr を通し忘れた文は見つけられない。ここでは英語にして画面を開いて操作し、部品の文とメッセージの文を集めて、日本語の
文字が残っていないことを確かめる。様式の名前・欄の名前・題名の語（様式のデータ）、ファイルとフォルダの名前、
日本語のまま残す文（KEEP_JAPANESE）は除いて数える。

幅は、この環境の書体（DejaVu Sans など。Windows の Segoe UI より字の幅が広い）で測る。
"""

from gui import (
    QtGui,
    QtWidgets,
    ask_with,
    drive_editor,
    open_and_wait,
    open_forms_window,
    use_forms_folder,
    wait,
    with_work,
)
from test_structure import JAPANESE_TEXT, KEEP_JAPANESE
from userforms import bundled_forms

from sumikeshi_studio import formeditor, i18n, widgets
from sumikeshi_studio.pageview import BoxItem, PageView

W = QtWidgets


def widget_texts(root: W.QWidget) -> list[tuple[str, str]]:
    """root とその中の部品が画面に出す文：(どこ, 文) の一覧。入力欄の中身（画面で入れたもの）は除く"""
    out: list[tuple[str, str]] = []

    def add(where: str, text) -> None:
        if isinstance(text, str) and text:
            out.append((where, text))

    for w in [root, *root.findChildren(W.QWidget)]:
        name = (
            f"{type(w).__name__}({w.objectName()})"
            if w.objectName()
            else type(w).__name__
        )
        if w.isWindow() or isinstance(w, (W.QDockWidget, W.QToolBar)):
            add(f"{name} の題名", w.windowTitle())
        add(f"{name} のツールチップ", w.toolTip())
        if isinstance(w, W.QLabel):
            add(name, w.text())
        if isinstance(w, W.QAbstractButton):
            add(name, w.text())
        if isinstance(w, W.QGroupBox):
            add(f"{name} の題名", w.title())
        if isinstance(w, W.QComboBox):
            for i in range(w.count()):
                add(f"{name} の項目", w.itemText(i))
        if isinstance(w, W.QTableWidget):
            for c in range(w.columnCount()):
                if (item := w.horizontalHeaderItem(c)) is not None:
                    add(f"{name} の見出し", item.text())
            for r in range(w.rowCount()):
                for c in range(w.columnCount()):
                    if (item := w.item(r, c)) is not None:
                        add(f"{name} のセル", item.text())
                        add(f"{name} のセルのツールチップ", item.toolTip())
        if isinstance(w, W.QTreeWidget):
            items = []
            for i in range(w.topLevelItemCount()):
                top = w.topLevelItem(i)
                items.append(top)
                items += [top.child(k) for k in range(top.childCount())]
            for item in items:
                add(f"{name} の行", item.text(0))
                add(f"{name} の行のツールチップ", item.toolTip(0))
                for label in item.data(0, widgets.LabelDelegate.LABELS_ROLE) or []:
                    add(f"{name} の行のラベル", label[0])
        if isinstance(w, W.QListWidget):
            for i in range(w.count()):
                add(f"{name} の行", w.item(i).text())
        if isinstance(w, PageView):
            for item in w.scene().items():
                if isinstance(item, W.QGraphicsTextItem):
                    add(f"{name} の文", item.toPlainText())
                if isinstance(item, BoxItem):
                    add(f"{name} の範囲のツールチップ", item.toolTip())
    for a in root.findChildren(QtGui.QAction):
        add("操作", a.text())
        add("操作のツールチップ", a.toolTip())
    return out


def allowed_words() -> list[str]:
    """英語の画面でも日本語のまま出てよい語（様式のデータと、日本語のまま残す文）。長いものから除く"""
    words = set(KEEP_JAPANESE)
    for spec in bundled_forms():
        words.add(spec.name)
        words.update(f.name for f in (*spec.fields, *spec.erase))
        words.update(spec.title_words)
        words.update(spec.title_excludes)
    return sorted(words, key=len, reverse=True)


def japanese_left(texts: list[tuple[str, str, str]], extra: list[str]) -> list[str]:
    """(画面, どこ, 文) のうち、出てよい語を除いても日本語の文字が残るものの一覧"""
    words = sorted({*allowed_words(), *extra}, key=len, reverse=True)
    bad = []
    for screen, where, text in texts:
        rest = text
        for word in words:
            rest = rest.replace(word, "")
        if JAPANESE_TEXT.search(rest):
            bad.append(f"{screen}：{where}：{text!r}")
    return bad


def test_no_japanese_on_english_screens(
    new_launcher, new_window, files, scan, tmp_path, monkeypatch
):
    """英語の画面で、起動の画面・墨消しの画面（開く・表示・削除・操作の説明・出力）・様式の編集の画面・範囲を描く画面を
    開いて操作し、出た文に日本語が残っていない"""
    i18n.set_language(i18n.EN)
    found: list[tuple[str, str, str]] = []

    def check(screen: str, root: W.QWidget) -> None:
        found.extend((screen, where, text) for where, text in widget_texts(root))

    lw = new_launcher()
    check("起動の画面", lw)
    win = new_window()
    check("墨消しの画面（何も開いていない）", win)
    unknown = scan(
        "2026bun_08"
    )  # どの様式とも位置合わせできない用紙（様式不明・墨消しなし）
    open_and_wait(win, [*files, unknown])
    for i in range(len(win.pages)):
        win.goto_page(i)
        win.qapp.processEvents()
        check(f"墨消しの画面（{i + 1}ページ目）", win)
    box = W.QMessageBox.StandardButton
    asked = ask_with(monkeypatch, [box.Yes])
    win.toggle_page_deleted(1)  # ページを削除する問い
    win.goto_page(1)
    check("墨消しの画面（削除したページ）", win)
    win.toggle_page_deleted(1)
    win.help_button.click()
    # 出力：表示していないページがあるときの警告、上書きの問い、結果のダイアログ（文は gui_env が集める）
    win.pages[0].viewed = False
    out = tmp_path / "out"
    out.mkdir()
    win.export_flow.export_to(out)
    wait(win, lambda: not win.exporting)
    for d in win.docs:
        d.exported = False
    win.export_flow.export_to(out)
    wait(win, lambda: not win.exporting)
    check("墨消しの画面（出力の後）", win)
    win.close()
    # 様式の編集の画面と範囲を描く画面（登録の流れ）
    use_forms_folder(tmp_path)
    fw = open_forms_window(lw)
    fw.panel.table.selectRow(0)
    check("様式の編集の画面", fw)
    fw.close()
    win2 = new_window()
    open_and_wait(win2, [unknown])
    win2.add_box((1074, 183, 410, 59))

    def steps(ed):
        ed.draw_erase.setChecked(True)
        ed.add_box((300, 300, 500, 120))
        ed.read_title()
        wait(win2, lambda: not ed.busy)
        check("範囲を描く画面", ed)
        return False

    opened = drive_editor(monkeypatch, win2, steps)
    formeditor.register_from_page(win2.session, with_work(win2), win2)
    wait(win2, lambda: opened)
    messages = [*lw.shown, *(a[0] for a in asked)]
    found += [("お知らせのウィンドウ", "文", m) for m in messages]
    assert len(found) > 300  # 部品の文を集められている
    # 様式のフォルダ（gui.use_forms_folder が作る tmp_path の「様式」）のパスは、利用者が選んだ名前なので日本語のまま出る
    names = [p.name for p in (*files, unknown, out)] + [
        str(tmp_path / "様式"),
        str(tmp_path),
    ]
    assert japanese_left(found, names) == []


def test_english_texts_fit(
    new_launcher, new_window, files, scan, tmp_path, monkeypatch
):
    """英語の画面で、範囲を描く画面の右の欄（幅420px）の文が欄に収まり、起動の画面・墨消しの画面・様式の編集の画面は
    既定の大きさで部品が収まり、墨消しの画面のツールバーがウィンドウの幅に収まる"""
    i18n.set_language(i18n.EN)
    lw = new_launcher()
    assert lw.minimumSizeHint().width() <= lw.width() == 900
    win = new_window()
    open_and_wait(win, list(files))
    assert win.minimumSizeHint().width() <= win.width()
    assert win.tool_bar.sizeHint().width() <= win.width()
    use_forms_folder(tmp_path)
    fw = open_forms_window(lw)
    assert fw.minimumSizeHint().width() <= fw.width()
    fw.close()
    box = W.QMessageBox.StandardButton
    ask_with(monkeypatch, [box.Yes])
    too_wide = []

    def steps(ed):
        sides = [
            w
            for w in ed.findChildren(W.QWidget)
            if w.minimumWidth() == w.maximumWidth() == 420
        ]
        assert len(sides) == 1
        side = sides[0]
        for w in side.findChildren(W.QWidget):
            plain_label = isinstance(w, W.QLabel) and not w.wordWrap()
            measured = isinstance(w, (W.QAbstractButton, W.QGroupBox, W.QComboBox))
            if (measured or plain_label) and w.sizeHint().width() > side.width():
                too_wide.append((type(w).__name__, w.sizeHint().width()))
        # 範囲の表の見出しの幅の合計も、右の欄に収まる（ウィンドウを出していないので、表の幅ではなく欄の幅と比べる）
        header = ed.table.horizontalHeader()
        heads = sum(header.sectionSizeFromContents(c).width() for c in range(3))
        if heads > side.width():
            too_wide.append(("表の見出し", heads))
        return False

    opened = drive_editor(monkeypatch, win, steps)
    formeditor.register_from_page(win.session, with_work(win), win)
    wait(win, lambda: opened)
    assert too_wide == []


def test_texts_collected_from_parts(new_window, files):
    """部品から文を集める処理は、ウィンドウの題名・ボタン・操作・一覧の行とラベル・画面の下のラベル・ページの表示の文と
    範囲のツールチップを集める（日本語の画面で確かめる）。日本語の文字の見つけ方は、出てよい語を除いてから数える"""
    win = new_window()
    texts = {t: w for w, t in widget_texts(win)}
    assert "ファイルを開く" in texts  # ツールバーの操作
    assert "操作の説明" in texts  # ボタン
    assert "ファイルとページ" in texts  # 一覧の題名
    assert any(
        "ドラッグしても開けます" in t for t in texts
    )  # ページの表示の文（何も開いていないとき）
    open_and_wait(win, list(files))
    texts = {t: w for w, t in widget_texts(win)}
    assert any(t.startswith("確認 ") for t in texts)  # 一覧のファイルの行のラベル
    assert any(t.startswith("使う様式：") for t in texts)  # 画面の下のラベル
    assert any(
        t.endswith("（クリックで切り替え）") for t in texts
    )  # 範囲のツールチップ
    # 様式の名前は除き、英語の文に残った全角のかっこや「様式」は見つける
    assert japanese_left([("画面", "部品", "扶養控除等申告書 (revision 2)")], []) == []
    assert japanese_left([("画面", "部品", "扶養控除等申告書（revision 2）")], []) == [
        "画面：部品：'扶養控除等申告書（revision 2）'"
    ]
    assert japanese_left([("画面", "部品", "様式：扶養控除等申告書")], []) == [
        "画面：部品：'様式：扶養控除等申告書'"
    ]
