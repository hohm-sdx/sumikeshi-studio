# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""様式の画面（様式の一覧・登録・編集、範囲を描く画面）のテスト（表示なしで動かす）。共通のフィクスチャは tests/conftest.py に、補助の関数は tests/gui.py にある"""

import dataclasses
import sys
import threading

from conftest import cached_analyze
from gui import (
    Qt,
    QtCore,
    QTest,
    QtGui,
    QtWidgets,
    ask_with,
    drive_editor,
    first_masked,
    list_dialog,
    log_rows,
    manual_boxes,
    open_and_wait,
    open_files,
    open_forms_window,
    patch_ask,
    row_cells,
    select,
    use_forms_folder,
    wait,
    with_work,
)
from userforms import bundled, install_bundled, write_form

from sumikeshi_studio import (
    analysis,
    formcheck,
    formeditor,
    formsave,
    formstext,
    formstore,
    formswin,
    i18n,
    imaging,
    pipeline,
    prefs,
    reviewtext,
    rulings,
    variants,
    widgets,
)
from sumikeshi_studio.boxes import Source

# ---------------------------------------------------------------- 様式


def test_unreadable_forms_block_opening(window, files, tmp_path):
    """様式のフォルダや使う様式を読めなければ、ファイルを開かず、開いていたファイルもそのまま"""
    open_and_wait(window, [files[0]])
    prefs.settings().setValue(prefs.FORMS_FOLDER_KEY, str(tmp_path / "つながらない"))
    open_files(window, [files[1]])
    assert window.shown[-1].startswith(
        "様式のフォルダを読めないため、ファイルを開けません。"
    )
    assert [d.path for d in window.docs] == [files[0]]
    folder = use_forms_folder(tmp_path)
    (folder / "u-broken.json").write_text("{", encoding="utf-8")
    open_files(window, [files[1]])
    assert window.shown[-1].startswith("次の様式を読めないため、ファイルを開けません。")
    assert "u-broken.json" in window.shown[-1]
    assert [d.path for d in window.docs] == [files[0]]
    # この端末で使わない様式なら、読めなくても開く。画面下には数だけを出す
    prefs.set_forms_off({"u-broken"})
    open_and_wait(window, [files[1]])
    assert [d.path for d in window.docs] == [files[1]]
    status = window.status.text()
    assert "使う様式：3　✕ この PC で使わない様式：1" in status
    assert "u-broken" not in status


def test_user_form_is_shown_and_logged(window, files, tmp_path):
    """様式で判別したページは、画面下の「判別：」に版と保存した人を出し、作業ログに版を書く"""
    folder = use_forms_folder(tmp_path)
    write_form(
        folder, "nta-fuyou-r9", "扶養控除等申告書", fields=3, revision=2, frame=True
    )
    open_and_wait(window, list(files))
    assert len(window.pages[0].result.boxes) == 3
    status = window.status.text()
    assert "帳票：扶養控除等申告書（第2版、2026-11-07 yamada）" in status
    assert "使う様式：3" in status
    window.rotate(1)  # 回して解析し直すときも、開いたときの様式を使う
    wait(window, lambda: window.pages[0].ready)
    window.rotate(-1)
    wait(window, lambda: window.pages[0].ready)
    assert [s.revision for s in window.pages[0].result.used] == [2]
    window.step(1)
    out_dir = tmp_path / "出力"
    out_dir.mkdir()
    window.export_flow.export_to(out_dir)
    wait(window, lambda: not window.exporting)
    assert [r["Forms used"] for r in log_rows(tmp_path)] == [
        "扶養控除等申告書 (revision 2)",
        "保険料控除申告書 (revision 0)",
    ]


def test_forms_folder_is_read_in_background(window, files, tmp_path, monkeypatch):
    """様式のフォルダは裏のスレッドで読み、そのあいだもページの移動とメニューは使える。読み終わってから開く

    共有フォルダにつながらないときのように読むのが遅いフォルダは、読む関数を置き換えて待たせて試す。
    """
    open_and_wait(window, list(files))
    gate, calls = threading.Event(), []
    real = formstore.load

    def slow(choice, off=()):
        calls.append(choice)
        gate.wait(60)
        return real(choice, off)

    monkeypatch.setattr(formstore, "load", slow)
    window.open_paths([files[1]])
    wait(window, lambda: len(calls) == 1)
    assert "様式のフォルダを読んでいます…" in window.status.text()
    assert len(window.docs) == 2  # 読み終わるまでは開かない
    assert not window.act_export.isEnabled()
    # 読んでいるあいだも、ページの移動はできる
    window.step(1)
    assert window.current == 1
    window.goto_page(0)
    # 読んでいるあいだにまた開くと、読み直さずに、後で選んだファイルを読み終わってから開く
    window.open_paths([files[0]])
    assert len(calls) == 1
    gate.set()
    wait(window, lambda: window.opening is None)
    assert [d.path for d in window.docs] == [files[0]]
    assert "様式のフォルダを読んでいます" not in window.status.text()

    # 読んでいるあいだに様式のフォルダを選び直すと、新しいフォルダで読み直してから開く
    gate.clear()
    window.open_paths([files[1]])
    wait(window, lambda: len(calls) == 2)
    folder = tmp_path / "選び直した様式"
    install_bundled(folder)
    prefs.settings().setValue(prefs.FORMS_FOLDER_KEY, str(folder))
    gate.set()
    wait(window, lambda: window.opening is None)
    assert [c.path for c in calls[1:]] == [formstore.default_folder(), folder]
    assert window.forms.folder == folder and [d.path for d in window.docs] == [files[1]]


# ---------------------------------------------------------------- 様式の一覧・登録・編集

JUUTARU_NAME = "従たる給与の申告書（独自）"


def test_registered_form_near_another_gets_higher_line_limit(
    window, scan, tmp_path, monkeypatch
):
    """登録する様式の罫線の一致度の下限の初めの値は、使うほかの様式の見本の罫線と比べて決める。表の作りが扶養控除等
    申告書に似た帳票（公的年金等の受給者の扶養親族等申告書）を登録すると、いちばん近い様式との一致度（2つの割合の
    小さいほう）に 0.10 を足して 0.05 刻みに切り上げた値（0.60 より高い）になり、その値で保存する"""
    folder = use_forms_folder(tmp_path)
    open_and_wait(window, [scan("2027bun_09")])
    assert window.pages[0].result.form == "様式不明"
    window.add_box((1074, 183, 410, 59))
    box = QtWidgets.QMessageBox.StandardButton
    ask_with(monkeypatch, [box.Yes])

    def steps(ed):
        ed.name_edit.setText("公的年金等の申告書（独自）")
        ed.words_edit.setPlainText("公的年金等の受給者の扶養親族等申告書")

    opened = drive_editor(monkeypatch, window, steps)
    formeditor.register_from_page(window.session, with_work(window), window)
    wait(window, lambda: opened and not opened[0].busy)
    ed = opened[0]
    assert ed.result() == QtWidgets.QDialog.DialogCode.Accepted
    use, limit = ed.line_match
    bundled_specs = [
        f
        for f in formstore.load(formstore.FolderChoice(folder)).active
        if f.id.startswith("nta-")
    ]
    assert len(bundled_specs) == 3
    assert (use, limit) == variants.default_limit(rulings.read(ed.base), bundled_specs)
    assert use and 0.65 <= limit <= 0.80
    saved = {f.name: f for f in formstore.load(formstore.FolderChoice(folder)).forms}[
        "公的年金等の申告書（独自）"
    ]
    assert (saved.line_match_use, saved.line_match_min) == (use, limit)


def test_register_opens_editor_with_default_limit_when_limit_cannot_be_computed(
    window, scan, tmp_path, monkeypatch
):
    """罫線の一致度の下限の初めの値を求める処理が失敗しても、範囲を描く画面は開き、下限は 0.60（使う）で始まる。
    状態の行に、比べられなかったことと下限の値を出す。見本の罫線は、保存の前の確認で読む"""
    from sumikeshi_studio import analysis

    folder = use_forms_folder(tmp_path)
    open_and_wait(window, [scan("2027bun_09")])
    window.add_box((1074, 183, 410, 59))
    box = QtWidgets.QMessageBox.StandardButton
    ask_with(monkeypatch, [box.Yes])
    real_call = window.session.analyzer.call

    def call(fn, *args, callback):
        if fn is analysis.line_match_default:
            callback(None, RuntimeError("求められない"))
            return
        real_call(fn, *args, callback=callback)

    monkeypatch.setattr(window.session.analyzer, "call", call)

    def steps(ed):
        assert ed.line_match == (True, 0.60) and ed.lines is None
        assert ed.status.text() == (
            "ほかの様式の罫線と比べられなかったので、罫線の一致度の下限を 0.60 にしています"
        )
        ed.name_edit.setText("公的年金等の申告書（独自）")
        ed.words_edit.setPlainText("公的年金等の受給者の扶養親族等申告書")

    opened = drive_editor(monkeypatch, window, steps)
    formeditor.register_from_page(window.session, with_work(window), window)
    wait(window, lambda: opened and not opened[0].busy)
    ed = opened[0]
    assert ed.result() == QtWidgets.QDialog.DialogCode.Accepted
    saved = {f.name: f for f in formstore.load(formstore.FolderChoice(folder)).forms}[
        "公的年金等の申告書（独自）"
    ]
    assert (saved.line_match_use, saved.line_match_min) == (True, 0.60)
    assert saved.lines == rulings.read(ed.base)


def test_register_form_from_page(window, scan, tmp_path, monkeypatch):
    """表示しているページから様式を登録し、その様式で別の傾きのページを判別する"""
    folder = use_forms_folder(tmp_path)
    open_and_wait(window, [scan("2026bun_08")])
    assert window.pages[0].result.form == "様式不明"
    window.add_box((1074, 183, 410, 59))  # 主の画面で範囲を手で追加してから登録する
    box = QtWidgets.QMessageBox.StandardButton
    asked = ask_with(monkeypatch, [box.Yes])

    def steps(ed):
        assert ed.windowTitle() == "様式を登録"
        assert [(b.kind, b.source) for b in ed.boxes] == [("範囲1", Source.MANUAL)]
        ed.read_title()  # 題名を OCR で読み、題名の語の欄に入れる
        wait(window, lambda: not ed.busy)
        assert ed.status.text() == (
            "読めた題名を、「題名に次の語があれば、この様式と判別します」の欄の最後の行に入れました。"
            "年分と帳票の名前の語だけをスペースで区切って残し（例：令和8年分　扶養控除等）、ほかの語は消してください。"
        )
        assert "従たる給与についての扶養控除等" in ed.words_edit.toPlainText()
        ed.words_edit.setPlainText("従たる給与についての扶養控除等")
        ed.name_edit.setText(JUUTARU_NAME)
        ed.draw_erase.setChecked(True)
        ed.add_box((300, 300, 500, 120))
        ed.table.item(0, 1).setText("本人の個人番号")  # 範囲の名前を付ける
        # 「枠の線」の列：枠の線のない所に描いた範囲は「なし」。選ぶと記録が変わる
        frame = ed.table.cellWidget(0, 2)
        assert frame.currentText() == "なし" and ed.table.cellWidget(1, 2) is None
        frame.setCurrentIndex(1)
        assert frame.currentText() == "上下"
        assert ed.boxes[0].frame == (formstore.TOP_BOTTOM,)
        frame.setCurrentIndex(3)

    opened = drive_editor(monkeypatch, window, steps)
    formeditor.register_from_page(window.session, with_work(window), window)
    wait(window, lambda: opened and not opened[0].busy)
    assert asked[0][0].startswith(
        "見本の画像は、墨消しする範囲の外がそのまま様式のフォルダに保存されます。"
    )
    ed = opened[0]
    assert ed.result() == QtWidgets.QDialog.DialogCode.Accepted
    saved = {f.name: f for f in formstore.load(formstore.FolderChoice(folder)).forms}[
        JUUTARU_NAME
    ]
    assert (saved.name, saved.revision, saved.title_words) == (
        JUUTARU_NAME,
        1,
        ("従たる給与についての扶養控除等",),
    )
    assert [(f.name, f.frame) for f in saved.fields] == [("本人の個人番号", ())]
    assert [(f.name, f.frame) for f in saved.erase] == [("消す範囲1", None)]
    # 登録では、塗る前のページの作業画像から読んだ見本の罫線を持つ（下限の初めの値を求めたときに読んだものを、
    # 画面が持って、保存の確認に渡す）
    assert saved.lines == ed.lines == rulings.read(ed.base)
    # 罫線の一致度の下限の初めの値は、使うほかの様式（配布用パッケージの3様式）の見本の罫線と比べて決める。
    # 従たる給与の申告書は、いちばん近い様式との一致度が 0.50 に届かないので、0.60 のまま
    assert ed.line_match == (True, 0.60)
    assert (saved.line_match_use, saved.line_match_min) == (True, 0.60)
    assert (folder / formsave.CHANGE_LOG).read_text("utf-8-sig").splitlines()[1].split(
        ","
    )[2:4] == [
        "register",
        JUUTARU_NAME,
    ]
    # 次に開くファイルから、登録した様式で判別する
    open_and_wait(window, [scan("2026bun_08", angle=3)])
    res = window.pages[0].result
    assert res.form == JUUTARU_NAME and len(res.boxes) == 1 and res.note == ""
    assert f"帳票：{JUUTARU_NAME}（第1版、" in window.status.text()


def close_to(a, b, tol):
    return max(abs(u - v) for u, v in zip(a, b)) <= tol


def test_editor_fits_drawn_and_resized_ranges(window, files, tmp_path, monkeypatch):
    """範囲を描く画面は「範囲を自動で枠の線に合わせる」にチェックのある状態で始まり、チェックがあれば、墨消しする
    範囲を描いたときと大きさを変えたときに、枠の線が見つかった辺を線の中心に合わせる。登録で開いたときと、動かした
    ときは、位置を変えない。元に戻す・やり直すでは、操作の前と後に見えていた位置になる。チェックを外すと描いたとおりの
    位置にし、組は記録する。チェックを変えても、すでにある範囲は動かさない"""
    use_forms_folder(tmp_path)
    open_and_wait(window, [files[0]])
    box = QtWidgets.QMessageBox.StandardButton
    ask_with(monkeypatch, [box.Yes])
    both = (formstore.TOP_BOTTOM, formstore.LEFT_RIGHT)
    page_box = next(b for b in window.pages[0].result.boxes if b.active)
    target = page_box.rect()  # 解析で枠の線の中心に合わせた範囲
    x, y, w, h = target
    inside = (x + 6, y, w - 12, h)  # 左右の線より6画素内側
    page_box.x, page_box.w = inside[0], inside[2]
    seen = []

    def steps(ed):
        assert ed.fit_check.isChecked()
        first = ed.boxes[0]  # ページの範囲から引き継いだ範囲
        assert first.rect() == inside and first.frame == both
        ed.add_box(inside)
        drawn = ed.boxes[-1]
        assert close_to(drawn.rect(), target, 0.5) and drawn.frame == both
        fitted = drawn.rect()
        moved = (fitted[0] + 3, fitted[1] + 2, fitted[2], fitted[3])
        ed.move_box(drawn, fitted, moved)
        assert drawn.rect() == moved
        ed.undo.undo()
        assert drawn.rect() == fitted
        ed.undo.redo()
        assert drawn.rect() == moved
        ed.resize_box(drawn, moved, (x + 5, y + 1, w - 10, h - 2))
        assert close_to(drawn.rect(), target, 0.5) and drawn.frame == both
        resized = drawn.rect()
        ed.undo.undo()  # 大きさを変える前に見えていた位置（合わせ直さない）
        assert drawn.rect() == moved
        ed.undo.redo()
        assert drawn.rect() == resized
        # チェックを外したとき
        ed.fit_check.setChecked(False)
        ed.add_box(inside)
        plain = ed.boxes[-1]
        assert plain.rect() == inside and plain.frame == both
        smaller = (x + 7, y + 1, w - 14, h - 2)
        ed.resize_box(plain, inside, smaller)
        assert plain.rect() == smaller
        ed.fit_check.setChecked(True)
        assert plain.rect() == smaller and drawn.rect() == resized
        # 保存する様式の座標は整数にする（formsave.new_spec）
        saved = ed.new_spec().fields[-1]
        assert (saved.x, saved.y, saved.w, saved.h) == tuple(round(v) for v in smaller)
        ed.fit_check.setChecked(False)  # 外したまま閉じる
        seen.append(True)
        return False

    opened = drive_editor(monkeypatch, window, steps)
    formeditor.register_from_page(window.session, with_work(window), window)
    wait(window, lambda: opened)
    assert seen == [True]

    def check_again(ed):
        assert ed.fit_check.isChecked()  # 前に閉じた画面のチェックの状態は覚えない
        seen.append(True)
        return False

    drive_editor(monkeypatch, window, check_again)
    formeditor.register_from_page(window.session, with_work(window), window)
    wait(window, lambda: len(seen) == 2)


def test_editor_keeps_recorded_ranges_when_opened(window, reference, tmp_path):
    """編集で開いたとき、枠の線の記録のある範囲は、線より内側にあっても、位置も記録も変えない"""
    work, boxes = reference("2027bun_01")
    [b] = [b for b in boxes if b.kind == "A 源泉控除対象配偶者"]
    both = (formstore.TOP_BOTTOM, formstore.LEFT_RIGHT)
    inside = (b.x + 4, b.y + 3, b.w - 8, b.h - 6)
    spec = dataclasses.replace(
        bundled("nta-fuyou-r9"),
        fields=(
            formstore.Field("線の内側", *inside, both),
            formstore.Field("なし", *inside, ()),
        ),
    )
    ed = formeditor.FormEditor(
        window.session,
        operation=formsave.OP_EDIT,
        spec=spec,
        base=formcheck.make_template(work, True, [inside]),
        base_is_work=False,
        fields=list(spec.fields),
        erase=[],
        loaded=formstore.Loaded(tmp_path),
    )
    try:
        recorded, kept = ed.boxes
        assert recorded.rect() == inside and recorded.frame == both
        assert kept.rect() == inside and kept.frame == ()
    finally:
        ed.close()
        ed.deleteLater()


def test_editor_checks_before_saving(window, files, tmp_path, monkeypatch):
    """範囲を描く画面を開く前に、初めの範囲の記入を確かめて警告する（記入した扶養控除等申告書のページ）。保存の前に、
    名前・範囲・類似した様式と題名で見分けられるか・描いた範囲の記入・手がかりを確かめる"""
    folder = use_forms_folder(tmp_path)
    open_and_wait(window, [files[0]])  # 扶養控除等申告書のページから登録する
    box = QtWidgets.QMessageBox.StandardButton
    answer = [box.Yes]  # 登録の前の注意には「はい」
    asked = ask_with(monkeypatch, answer)

    def save(ed):
        ed.save()
        wait(window, lambda: not ed.busy)

    def steps(ed):
        save(ed)
        assert window.shown[-1] == "様式の名前を入れてください。"
        ed.name_edit.setText("扶養控除等申告書")
        save(ed)
        assert "という名前の様式がすでにあります" in window.shown[-1]
        ed.name_edit.setText("独自の扶養控除等申告書")
        fields = list(ed.boxes)
        for b in fields:
            ed.delete_box(b)
        save(ed)
        assert window.shown[-1] == "墨消しする範囲を1つ以上描いてください。"
        ed.undo.undo()  # 1つ戻す
        # ほかの様式と同じ題名の語でも、保存を押した直後の確認は出さない。見本が類似した配布用パッケージの扶養控除等
        # 申告書と、2つの見本の題名（どちらも令和9年分）で見分けられないので、警告する
        confirms, confirm_answer = [], [False]
        monkeypatch.setattr(
            widgets, "confirm", lambda *a, **k: confirms.append(a) or confirm_answer[0]
        )
        ed.words_edit.setPlainText("扶養控除等")
        save(ed)
        assert not any("ほかの様式にもあります" in text for text, _ in asked)
        _, _, text, _, yes, no = confirms[-1]
        # 年分が同じ（どちらも令和9年分）なので、年分を足しても見分けられず、案は作れない
        assert text == formstext.unclear_text("扶養控除等申告書", "", ())
        assert (yes, no) == ("保存する", "保存しない")
        confirm_answer[0] = True
        real_build = analysis.build_form

        def written_check(*args):
            png, check = real_build(*args)
            return png, dataclasses.replace(check, written=("本人",))

        monkeypatch.setattr(analysis, "build_form", written_check)
        answer[0] = box.No  # 描いた範囲の記入の警告：いいえ
        save(ed)
        assert asked[-1][0] == "\n".join(
            [*formstext.save_warnings(["本人"], 6000), "保存しますか？"]
        )
        monkeypatch.setattr(analysis, "build_form", real_build)
        answer_seq = [box.Yes] * 3
        patch_ask(
            monkeypatch,
            lambda *a, **k: asked.append((a[2], a[4:])) or answer_seq.pop(0),
        )
        # 範囲を消しすぎると、手がかりが足りないので保存しない
        h, w = ed.base.shape
        ed.draw_erase.setChecked(True)
        ed.add_box((0, 0, w, h - 40))
        answer_seq[:] = [box.Yes] * 3
        save(ed)
        assert window.shown[-1].startswith(
            "この画像では位置合わせの手がかりが足りません"
        )
        return False

    opened = drive_editor(monkeypatch, window, steps)
    formeditor.register_from_page(window.session, with_work(window), window)
    wait(window, lambda: opened and not opened[0].busy)
    entry = asked[0][
        0
    ]  # 範囲を描く画面を開く前の確かめ：記入した範囲の警告と、登録を続けるかの問い
    assert "に記入があるようです。" in entry
    assert entry.endswith(formstext.REGISTER_QUESTION)
    assert opened[0].saved is None
    assert not list(folder.glob("u-*.json"))  # どの場合も保存していない


def test_forms_list_shows_folder_when_loading_fails(window, tmp_path, monkeypatch):
    """様式を読む処理が予期しない例外で終わっても、一覧の上に、読もうとした様式のフォルダの場所と出どころを出す"""
    folder = use_forms_folder(tmp_path)

    def broken(choice, off=()):
        raise RuntimeError("試験用の失敗")

    monkeypatch.setattr(formstore, "load", broken)
    dlg = list_dialog(window)
    assert dlg.folder_label.text() == f"様式のフォルダ：{folder}（選んだフォルダ）"
    assert "None" not in dlg.folder_label.text()
    assert dlg.status.text() == "様式を読めませんでした。試験用の失敗"


def test_edit_bundled_form(window, files, tmp_path, monkeypatch):
    """配布用パッケージの様式も、画面から登録した様式と同じく、編集すると新しい版を保存して使い、前の版は「history」に移す"""
    folder = use_forms_folder(tmp_path)
    dlg = list_dialog(window)
    assert [r[0] for r in dlg.rows] == ["nta-hoken-r8", "nta-kihai-r8", "nta-fuyou-r9"]
    assert row_cells(dlg, "nta-hoken-r8")[:4] == ["保険料控除申告書", "第0版", "", ""]
    select(dlg, "nta-hoken-r8")
    assert dlg.b_edit.text() == "編集"
    assert dlg.b_delete.isEnabled()

    def steps(ed):
        assert ed.windowTitle() == "様式の編集：保険料控除申告書"
        assert not ed.base_is_work and len(ed.boxes) == 1
        ed.table.item(0, 1).setText("扶養親族の個人番号")

    opened = drive_editor(monkeypatch, window, steps)
    dlg.edit()
    wait(window, lambda: opened and not dlg.busy)
    cells = row_cells(dlg, "nta-hoken-r8")
    assert cells[1] == "第1版" and cells[7] == "✓ 問題なし"
    open_and_wait(window, [files[1]])  # 保険料控除申告書
    res = window.pages[0].result
    assert [b.kind for b in res.boxes] == ["扶養親族の個人番号"]
    assert [(s.id, s.revision) for s in res.used] == [("nta-hoken-r8", 1)]
    assert (folder / formsave.HISTORY_DIR / "nta-hoken-r8-r0.json").is_file()


def test_editor_sets_line_match_limit(window, tmp_path, monkeypatch):
    """範囲を描く画面のまとまり「この様式のバリエーションの判別」で、罫線の並びを判別に使うかと、罫線の一致度の
    下限（0.10〜0.95、0.05 刻み。値は小数2桁）を変えて保存すると、様式の版が上がり、値が様式のファイルに残る。
    チェックを外すと、スライダーは使えない"""
    folder = use_forms_folder(tmp_path)
    dlg = list_dialog(window)
    select(dlg, "nta-fuyou-r9")
    texts = []

    def steps(ed):
        box = next(
            b
            for b in ed.findChildren(QtWidgets.QGroupBox)
            if "バリエーション" in b.title()
        )
        texts.append(box.title())
        texts.extend(lab.text() for lab in box.findChildren(QtWidgets.QLabel))
        texts.append(ed.lines_check.text())
        slider = ed.limit_slider
        assert ed.lines_check.isChecked() and slider.isEnabled()
        assert (slider.minimum(), slider.maximum(), slider.value()) == (2, 19, 13)
        assert ed.limit_value.text() == "0.65" and ed.line_match == (True, 0.65)
        slider.setValue(slider.minimum())
        assert ed.limit_value.text() == "0.10"
        slider.setValue(slider.maximum())
        assert ed.limit_value.text() == "0.95"
        slider.setValue(14)
        assert ed.limit_value.text() == "0.70" and ed.line_match == (True, 0.70)
        ed.lines_check.setChecked(False)
        assert not slider.isEnabled() and not ed.limit_value.isEnabled()
        assert ed.line_match == (False, 0.70)
        ed.lines_check.setChecked(True)
        assert slider.isEnabled() and ed.line_match == (True, 0.70)

    opened = drive_editor(monkeypatch, window, steps)
    dlg.edit()
    wait(window, lambda: opened and not dlg.busy)
    assert texts == [
        "この様式のバリエーションの判別",
        (
            "この様式と同じ帳票で、欄の追加などのカスタマイズによりレイアウトが少し違う用紙（この様式のバリエーション）を、"
            "罫線の並び方から判別します。"
        ),
        "罫線の一致度の下限",
        "0.65",
        "・別の帳票がこの様式と判別されるときは、上げてください。",
        "・この様式の用紙が様式不明になるときは、下げてください。",
        "・上げても別の帳票が判別されるときは、上のチェックを外してください。",
        "この様式の判別に罫線の並び方を使う",
    ]
    saved = {f.id: f for f in formstore.load(formstore.FolderChoice(folder)).forms}
    fuyou = saved["nta-fuyou-r9"]
    assert (fuyou.revision, fuyou.line_match_use, fuyou.line_match_min) == (
        1,
        True,
        0.70,
    )
    assert row_cells(dlg, "nta-fuyou-r9")[1] == "第1版"
    changes = (folder / formsave.CHANGE_LOG).read_text("utf-8-sig").splitlines()
    assert changes[-1].split(",")[2:4] == ["edit", "扶養控除等申告書"]

    def turn_off(ed):
        assert ed.limit_value.text() == "0.70"
        ed.lines_check.setChecked(False)

    opened = drive_editor(monkeypatch, window, turn_off)
    dlg.edit()
    wait(window, lambda: opened and not dlg.busy)
    fuyou = {f.id: f for f in formstore.load(formstore.FolderChoice(folder)).forms}[
        "nta-fuyou-r9"
    ]
    assert (fuyou.revision, fuyou.line_match_use, fuyou.line_match_min) == (
        2,
        False,
        0.70,
    )


def test_editor_keeps_limit_off_the_steps(window, tmp_path, monkeypatch):
    """様式のファイルの下限が 0.05 刻みに合わない値（.json を直接書いた値）でも、スライダーを動かさなければ、
    その値のまま保存する（表示は小数2桁、スライダーは近い位置）"""
    folder = use_forms_folder(tmp_path)
    write_form(
        folder,
        "u-0001",
        "刻みに合わない下限の様式",
        base="nta-hoken-r8",
        frame=True,
        line_match={"use": True, "min": 0.62},
    )
    dlg = list_dialog(window)
    select(dlg, "u-0001")

    def steps(ed):
        assert ed.limit_value.text() == "0.62" and ed.limit_slider.value() == 12
        assert ed.line_match == (True, 0.62)
        ed.name_edit.setText("刻みに合わない下限の様式（名前を変えた）")

    opened = drive_editor(monkeypatch, window, steps)
    dlg.edit()
    wait(window, lambda: opened and not dlg.busy)
    saved = {f.id: f for f in formstore.load(formstore.FolderChoice(folder)).forms}[
        "u-0001"
    ]
    assert saved.revision == 2 and saved.line_match_min == 0.62


def test_editor_side_scrolls_when_window_is_low(window, tmp_path, monkeypatch):
    """範囲を描く画面の右の欄は、ウィンドウが低いときだけ縦にスクロールする。範囲の表は5行、題名の語の入力欄は2行を
    下回らず、画面の最小の高さは右の欄の中身で決まらない。下限のスライダーは、マウスのホイールでは動かず、
    ホイールの操作は右の欄のスクロールになる"""
    use_forms_folder(tmp_path)
    dlg = list_dialog(window)
    select(dlg, "nta-fuyou-r9")
    seen = {}

    def steps(ed):
        side = next(iter(ed.findChildren(formeditor.SideScroll)))
        bar = side.verticalScrollBar()
        ed.show()
        ed.resize(1400, side.needed + 400)
        window.qapp.processEvents()
        seen["tall"] = bar.maximum()
        ed.resize(1400, 700)
        window.qapp.processEvents()
        seen["low"] = bar.maximum()
        seen["min_height"] = ed.minimumSizeHint().height()
        table = ed.table
        rows = (
            table.minimumHeight() - table.horizontalHeader().sizeHint().height()
        ) // table.verticalHeader().defaultSectionSize()
        seen["rows"] = rows
        seen["lines"] = [
            (e.minimumHeight() - 2 * e.frameWidth()) // e.fontMetrics().lineSpacing()
            for e in (ed.words_edit, ed.excludes_edit)
        ]
        slider = ed.limit_slider
        before = slider.value()

        def wheel(widget):
            center = widget.rect().center()
            event = QtGui.QWheelEvent(
                QtCore.QPointF(center),
                QtCore.QPointF(widget.mapToGlobal(center)),
                QtCore.QPoint(0, 0),
                QtCore.QPoint(0, -120),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            )
            QtWidgets.QApplication.sendEvent(widget, event)
            window.qapp.processEvents()
            return event.isAccepted()

        # スライダーはホイールの操作を受け取らない（受け取らなかった操作は、Qt が親の部品＝右の欄に渡す。テストで
        # 送る操作は親に渡らないので、右の欄には別に送って確かめる）
        seen["slider_took_wheel"] = wheel(slider)
        seen["slider_moved"] = slider.value() != before
        wheel(side.viewport())
        seen["scrolled"] = bar.value()
        return False

    opened = drive_editor(monkeypatch, window, steps)
    dlg.edit()
    wait(window, lambda: opened and not dlg.busy)
    assert seen["tall"] == 0 and seen["low"] > 0
    assert seen["min_height"] < 700
    assert seen["rows"] == 5 and all(n >= 2 for n in seen["lines"])
    assert not seen["slider_took_wheel"] and not seen["slider_moved"]
    assert seen["scrolled"] > 0


def test_frame_choice_ignores_wheel(window, tmp_path, monkeypatch):
    """範囲の表の「枠の線」の列の選択は、マウスのホイールでは変わらない（ホイールの操作を受け取らないので、操作は表や
    右の欄のスクロールになる）。キーボードでは今までどおり変えられ、範囲の枠の線の記録も変わる"""
    use_forms_folder(tmp_path)
    dlg = list_dialog(window)
    select(dlg, "nta-fuyou-r9")
    seen = {}

    def steps(ed):
        ed.show()
        window.qapp.processEvents()
        choice = ed.table.cellWidget(0, 2)
        both = (formstore.TOP_BOTTOM, formstore.LEFT_RIGHT)
        assert choice.currentText() == "上下・左右" and ed.boxes[0].frame == both
        center = choice.rect().center()
        for delta in (-120, 120):
            event = QtGui.QWheelEvent(
                QtCore.QPointF(center),
                QtCore.QPointF(choice.mapToGlobal(center)),
                QtCore.QPoint(0, 0),
                QtCore.QPoint(0, delta),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            )
            QtWidgets.QApplication.sendEvent(choice, event)
            window.qapp.processEvents()
            seen[delta] = (event.isAccepted(), choice.currentIndex())
        choice.setFocus()
        QTest.keyClick(choice, Qt.Key.Key_Down)
        window.qapp.processEvents()
        seen["key"] = (choice.currentText(), ed.boxes[0].frame)
        return False

    opened = drive_editor(monkeypatch, window, steps)
    dlg.edit()
    wait(window, lambda: opened and not dlg.busy)
    assert seen[-120] == (False, 0) and seen[120] == (False, 0)
    assert seen["key"] == ("上下", (formstore.TOP_BOTTOM,))


def test_delete_from_list(window, tmp_path, monkeypatch):
    """利用者の様式の削除（読めない様式のファイルも消せる。5.2）"""
    folder = use_forms_folder(tmp_path)
    write_form(folder, "u-0001", "住宅借入金等特別控除申告書", base="nta-hoken-r8")
    (folder / "u-broken.json").write_text("{", encoding="utf-8")
    dlg = list_dialog(window)
    assert (
        row_cells(dlg, "u-broken")[7]
        == "✕ 読めません（ファイルの中身が様式の形になっていません）"
    )
    assert row_cells(dlg, "u-0001")[7] == "✓ 問題なし"
    ask_with(monkeypatch, [QtWidgets.QMessageBox.StandardButton.Yes])
    for form_id in ("u-0001", "u-broken"):
        select(dlg, form_id)
        assert dlg.b_delete.isEnabled()
        dlg.delete()
        wait(window, lambda: not dlg.busy)
    assert [r[0] for r in dlg.rows] == ["nta-hoken-r8", "nta-kihai-r8", "nta-fuyou-r9"]
    ops = (folder / formsave.CHANGE_LOG).read_text("utf-8-sig").splitlines()[1:]
    assert [line.split(",")[2] for line in ops] == ["delete", "delete"]


def test_later_saver_is_asked_before_overwriting(window, tmp_path, monkeypatch):
    """編集のあいだに、ほかの人が同じ様式を保存していたら、上書きしてよいかを確かめる"""
    folder = use_forms_folder(tmp_path)
    write_form(folder, "u-0002", "医療費控除の明細書", base="nta-kihai-r8",
               title_words=["医療費控除の明細"])  # fmt: skip
    dlg = list_dialog(window)
    select(dlg, "u-0002")
    box = QtWidgets.QMessageBox.StandardButton
    answers = [box.No, box.Yes]  # 1回目の上書きの確認は「いいえ」、2回目は「はい」
    asked = []
    patch_ask(
        monkeypatch,
        lambda *a, **k: asked.append((a[2], a[4:])) or answers.pop(0),
    )

    def steps(ed):
        other = {f.id: f for f in formstore.load(formstore.FolderChoice(folder)).forms}[
            "u-0002"
        ]
        formsave.save(folder, dataclasses.replace(other, name="ほかの人の名前"), None,
                       formsave.OP_EDIT, "tanaka")  # fmt: skip
        ed.name_edit.setText("医療費控除の明細書（編集した）")
        ed.save()
        wait(window, lambda: not ed.busy)
        assert asked[-1][0].startswith("tanakaさんが（")
        assert ed.saved is None  # 「いいえ」なら保存しない

    opened = drive_editor(monkeypatch, window, steps)
    dlg.edit()
    wait(window, lambda: opened and not dlg.busy)
    saved = opened[0].saved
    assert (saved.name, saved.revision) == ("医療費控除の明細書（編集した）", 3)
    history = sorted(p.name for p in (folder / formsave.HISTORY_DIR).glob("*.json"))
    assert history == ["u-0002-r1.json", "u-0002-r2.json"]


def test_save_warns_written_range_and_removed_form(window, tmp_path, monkeypatch):
    """保存の前に、記入があるとみた範囲と、位置合わせの手がかりの少ない様式（確認の点が1,500点未満）を警告し
    （記入の警告が先）、保存の直前にほかの人が様式を外していたら、保存し直してよいかを確かめる"""
    folder = use_forms_folder(tmp_path)
    write_form(folder, "u-0003", "医療費控除の明細書", base="nta-kihai-r8",
               title_words=["医療費控除の明細"])  # fmt: skip
    dlg = list_dialog(window)
    select(dlg, "u-0003")
    real = analysis.build_form

    def written_check(*args):
        png, check = real(*args)
        return png, dataclasses.replace(check, written=("医療費",), inliers=443)

    monkeypatch.setattr(analysis, "build_form", written_check)
    box = QtWidgets.QMessageBox.StandardButton
    answers, asked = [box.Yes, box.No], []
    patch_ask(
        monkeypatch,
        lambda *a, **k: asked.append(a[2]) or answers.pop(0),
    )

    def steps(ed):
        (folder / "u-0003.json").unlink()  # ほかの人が様式のフォルダから外した
        ed.save()
        wait(window, lambda: not ed.busy)
        assert asked[0] == (
            "範囲「医療費」に記入があるようです。\n"
            "この様式は、文字や線が少なく、位置合わせの手がかりが少ないようです（443点）。"
            "スキャンの状態によっては、様式不明になることがあります。\n"
            "保存しますか？"
        )
        assert len(asked) == 2
        assert asked[1].startswith(
            "この様式は、ほかの人が様式のフォルダから外しています"
        )
        assert ed.saved is None  # 「いいえ」なら保存しない
        return False

    opened = drive_editor(monkeypatch, window, steps)
    dlg.edit()
    wait(window, lambda: opened and not dlg.busy)
    assert not (folder / "u-0003.json").exists()


def test_current_files_cannot_be_changed_while_forms_are_read(
    window, files, tmp_path, monkeypatch
):
    """様式のフォルダを読み終わったら今のファイルを閉じる・解析し直すあいだは、今のファイルを直す操作を
    受け付けない。読んでいるあいだに編集した内容が、確認なしに消えないようにするため"""
    open_and_wait(window, list(files))
    window.toggle_box(first_masked(window))  # 読む前に1つ解除しておく（元に戻せる）
    assert window.act_undo.isEnabled()
    gate, calls = threading.Event(), []
    real = formstore.load

    def slow(choice, off=()):
        calls.append(choice)
        gate.wait(60)
        return real(choice, off)

    monkeypatch.setattr(formstore, "load", slow)
    # つながらない様式のフォルダを読んでいるあいだ（読み終わると、開くのをやめる）
    prefs.settings().setValue(prefs.FORMS_FOLDER_KEY, str(tmp_path / "つながらない"))
    window.open_paths([files[1]])
    wait(window, lambda: len(calls) == 1)
    assert "様式のフォルダを読んでいます…" in window.status.text()
    assert (
        "読み終わりしだい選んだファイルを開き、今のファイルは閉じます。"
        "そのため、今のファイルは編集できません" in window.status.tips()
    )
    actions = [window.act_left, window.act_right, window.act_delete_page]
    actions += [window.act_undo, window.act_redo]
    assert not any(a.isEnabled() for a in actions)
    assert not window.page_menu(0).actions()[0].isEnabled()
    boxes = window.pages[0].result.boxes
    before = [(b.rect(), b.active) for b in boxes]
    window.toggle_box(boxes[1])
    window.add_box((100, 100, 200, 50))
    window.move_box(boxes[1], boxes[1].rect(), (0, 0, 50, 50))
    window.rotate(1)
    window.toggle_page_deleted(0)
    QTest.keyClick(window.page_list, Qt.Key.Key_Delete)
    assert [(b.rect(), b.active) for b in window.pages[0].result.boxes] == before
    assert window.pages[0].result is not None and not window.pages[0].deleted
    window.step(1)  # ページの移動はできる
    assert window.current == 1
    window.goto_page(0)
    gate.set()
    wait(window, lambda: window.opening is None)
    assert window.shown[-1].startswith("様式のフォルダを読めないため")
    # 開くのをやめたら、元どおり編集できる
    assert window.act_undo.isEnabled() and window.act_left.isEnabled()
    window.toggle_box(boxes[1])
    assert not boxes[1].active
    assert "編集できません" not in window.status.tips()


def test_drag_is_dropped_when_page_is_redrawn(window, files, monkeypatch):
    """範囲をドラッグしている途中でページを描き直しても（様式を読み終わって別のファイルが開いたとき、
    元に戻す操作をしたときなど）、マウスを離したときにエラーにならない"""
    open_and_wait(window, [files[0]])
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: errors.append(a))
    view = window.view
    p0, p1 = view.mapFromScene(100, 100), view.mapFromScene(400, 300)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=p0)
    QTest.mouseMove(view.viewport(), p1)
    window.show_current()  # ページを描き直す
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=p1)
    assert errors == [] and manual_boxes(window) == []
    # 枠を動かしている途中で、枠を描き直したとき
    box = first_masked(window)
    c0 = view.mapFromScene(box.x + box.w / 2, box.y + box.h / 2)
    c1 = view.mapFromScene(box.x + box.w / 2 + 60, box.y + box.h / 2 + 40)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=c0)
    QTest.mouseMove(view.viewport(), c1)
    view.refresh_boxes()
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=c1)
    assert errors == []


def test_drag_tells_move_from_resize(window, files):
    """範囲の中をドラッグすると「動かした」、角をドラッグすると「大きさを変えた」として知らせる（範囲を描く画面は、
    大きさを変えたときだけ線の中心に合わせる）。墨消しの画面では、どちらも範囲の位置と大きさに入る"""
    open_and_wait(window, [files[0]])
    view = window.view
    got = []
    view.moved.connect(lambda *a: got.append("moved"))
    view.resized.connect(lambda *a: got.append("resized"))

    def drag(p0, p1):
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=p0)
        QTest.mouseMove(view.viewport(), p1)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=p1)

    box = first_masked(window)
    x, y, w, h = box.rect()
    drag(
        view.mapFromScene(x + w / 2, y + h / 2),
        view.mapFromScene(x + w / 2 + 60, y + h / 2 + 40),
    )
    assert got == ["moved"] and box.x != x and close_to((box.w, box.h), (w, h), 1e-6)
    x, y = box.x, box.y
    drag(view.mapFromScene(x + w, y + h), view.mapFromScene(x + w + 30, y + h + 20))
    assert got == ["moved", "resized"] and box.w > w and box.h > h


def test_threads_are_released_after_they_end(window):
    """裏のスレッド（起動の準備・出力）は、終わるのを待ってから手放す。動いている QThread を手放すと、
    Qt が致命的なエラーにして強制終了するため"""
    session = window.session
    wait(
        window, lambda: not session.threads
    )  # 起動の準備のスレッドは、終わった後に手放した
    order = []

    class Thread:
        def wait(self):
            order.append(("待った", self in session.threads))

    th = Thread()
    session.threads.append(th)
    session.release_thread(th)
    assert order == [("待った", True)] and th not in session.threads


def test_action_states(window, files):
    """ツールバーのボタンを押せるかとボタンの文は、reviewtext.actions の値のとおりにする（状態ごとの値は、
    test_review.py の test_actions_follow_pages_and_locks で確かめる）。開く前と、2つ目のページを削除したとき"""

    def matches():
        a = reviewtext.actions(
            window.docs, window.pages, window.current, window.exporting, window.opening
        )
        assert (window.act_prev.isEnabled(), window.act_next.isEnabled()) == (
            a.prev,
            a.next,
        )
        assert window.act_left.isEnabled() == window.act_right.isEnabled() == a.turn
        assert window.act_delete_page.isEnabled() == a.delete_page
        assert window.act_delete_page.text() == (
            "削除を取り消す" if a.page_deleted else "このページを削除"
        )
        assert (
            window.act_open.isEnabled() == window.act_open_folder.isEnabled() == a.open
        )
        export_text = reviewtext.export_text(a.export_count)
        assert window.act_export.text() == window.export_button.text() == export_text
        assert (
            window.act_export.isEnabled()
            == window.export_button.isEnabled()
            == a.export
        )
        return a

    assert matches().open  # 開く前
    open_files(window, list(files))  # 1ページのファイルを2つ
    wait(window, lambda: all(st.ready for st in window.pages))
    window.goto_page(1)
    window.pages[1].deleted = True
    window.update_state()
    a = matches()
    assert a.page_deleted and a.prev and not a.turn and a.export_count == 1


# ---------------------------------------------------------------- 様式の編集の画面


def test_forms_window_registers_from_file(new_launcher, two_pages, monkeypatch):
    """「様式を編集」で様式の編集の画面を開き、ファイルのページから新しい様式を登録する。閉じたら起動の
    画面に戻り、一覧を読み直す"""
    lw = new_launcher()
    win = open_forms_window(lw)
    assert win.panel.b_register.text() == "新しい様式を登録"
    assert win.panel.b_replace.text() == "用紙を置き換える"
    titles = []
    monkeypatch.setattr(formswin, "choose_source", lambda *a: formswin.SOURCE_FILE)
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda parent, title, *a, **k: (titles.append(title), (str(two_pages), ""))[1],
    )

    def steps(ed):
        assert ed.windowTitle() == "様式を登録"
        assert [b.kind for b in ed.boxes] == ["年齢23歳未満の扶養親族"]  # 自動の候補
        ed.name_edit.setText("保険料控除申告書（独自）")

    opened = drive_editor(monkeypatch, lw, steps)
    win.panel.b_register.click()
    assert titles == ["様式にする用紙の PDF・画像を選んでください"]
    shown_before = len(lw.shown)
    wait(lw, lambda: win.analyzing is None and 0 in win.results)
    assert win.status.text() == formstext.pick_page_status(False, True, 2)
    win.page_list.setCurrentRow(1)  # 2ページ目（保険料控除申告書）
    wait(lw, lambda: 1 in win.results)
    win.use_button.click()
    wait(lw, lambda: opened and not opened[0].busy and not win.panel.busy)
    # 配布用パッケージの保険料控除申告書の見本に類似しているので尋ねる（テストでは「新しい様式として登録を続ける」）
    assert lw.shown[shown_before] == formstext.similar_text((bundled("nta-hoken-r8"),))
    assert opened[0].saved is not None
    assert "保険料控除申告書（独自）" in [r[1].name for r in win.panel.rows if r[1]]
    win.close()
    assert lw.isVisible() and lw.child is None
    wait(lw, lambda: not lw.loading)
    assert "保険料控除申告書（独自）" in [r[1].name for r in lw.rows if r[1]]


def test_forms_window_asks_page_for_replace(new_launcher, two_pages, monkeypatch):
    """「用紙を置き換える」で2ページのファイルを選ぶと、解析が終わった後、画面の下で新しい用紙にするページを選ぶよう
    示す。「やめる」で待つのをやめ、文を消す"""
    lw = new_launcher()
    win = open_forms_window(lw)
    select(win.panel, "nta-hoken-r8")
    monkeypatch.setattr(formswin, "choose_source", lambda *a: formswin.SOURCE_FILE)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(two_pages), "")
    )
    win.panel.b_replace.click()
    wait(lw, lambda: win.analyzing is None and 0 in win.results)
    assert win.status.text() == formstext.pick_page_status(False, False, 2)
    assert win.use_button.isEnabled() and win.cancel_button.isEnabled()
    win.cancel_button.click()
    assert win.operation is None and win.status.text() == ""
    assert not win.use_button.isEnabled() and not win.cancel_button.isEnabled()


def test_editor_freezes_contents_while_saving(window, files, tmp_path, monkeypatch):
    """「保存」を押した後、確認と保存のあいだは、範囲の操作・元に戻す・名前の入力・範囲の表を止め、押した時点の
    内容で保存する。保存の後の処理の失敗は、保存できたうえでの注意として出す"""
    from sumikeshi_studio import analysis

    use_forms_folder(tmp_path)
    open_and_wait(window, [files[0]])
    held = []
    real_call = window.session.analyzer.call

    def hold(fn, *args, callback):
        if fn is analysis.build_form:  # 保存の前の確認を止めておく
            held.append(lambda: real_call(fn, *args, callback=callback))
            return
        real_call(fn, *args, callback=callback)

    monkeypatch.setattr(window.session.analyzer, "call", hold)

    def locked(*a, **k):
        raise PermissionError(13, "Permission denied", formsave.CHANGE_LOG)

    monkeypatch.setattr(formsave, "append_change", locked)

    def steps(ed):
        ed.name_edit.setText("独自の扶養控除等申告書")
        count = len(ed.boxes)
        ed.save()
        assert ed.busy and len(held) == 1
        assert not ed.view.items_ready()  # 範囲を描く・動かす・クリックを受け付けない
        assert ed.name_edit.isReadOnly() and ed.words_edit.isReadOnly()
        assert not ed.table.isEnabled() and not ed.draw_erase.isEnabled()
        assert not any(a.isEnabled() for a in ed.undo_actions)
        # 待っているあいだに変えても（画面の値を直接変えても）、保存には使わない
        ed.name_edit.setText("変えた名前")
        ed.add_box((10, 10, 50, 50))
        held.pop()()
        wait(window, lambda: not ed.busy)
        assert (ed.saved.name, len(ed.saved.fields)) == (
            "独自の扶養控除等申告書",
            count,
        )
        assert window.shown[-1].startswith(
            "保存しました。ただし、次のことができませんでした。\n"
            "・様式の変更の記録（changes.csv）に書けませんでした（PermissionError"
        )
        return False

    opened = drive_editor(monkeypatch, window, steps)
    formeditor.register_from_page(window.session, with_work(window), window)
    wait(window, lambda: opened and opened[0].saved is not None)


def test_forms_window_drops_result_of_previous_paper(
    new_launcher, two_pages, scan, monkeypatch
):
    """用紙Aのページを解析しているあいだに用紙Bを開くと、Aの結果は捨て、Bのページを解析して進む"""
    from sumikeshi_studio import analysis

    lw = new_launcher()
    win = open_forms_window(lw)
    held = []
    real_call = win.session.analyzer.call

    def hold(fn, *args, callback):
        if fn is analysis.analyze and not held:  # 用紙Aの最初の解析を止めておく
            held.append(callback)
            return
        real_call(fn, *args, callback=callback)

    monkeypatch.setattr(win.session.analyzer, "call", hold)
    monkeypatch.setattr(formswin, "choose_source", lambda *a: formswin.SOURCE_FILE)
    paths = [two_pages, scan("2026bun_04")]  # A は2ページ、B は1ページ
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *a, **k: (str(paths.pop(0)), ""),
    )
    win.panel.b_register.click()
    assert held and win.analyzing == 0
    opened = drive_editor(monkeypatch, lw, lambda ed: False)
    win.panel.b_register.click()  # 用紙B を開く
    held[0]("用紙Aの結果", None)  # 用紙Aの1ページ目の結果が、後から届く
    assert "用紙Aの結果" not in win.results.values()
    wait(lw, lambda: opened)
    assert win.results[0] != "用紙Aの結果"
    assert win.results[0].form == "保険料控除申告書"
    assert opened[0].base is win.results[0].work


def test_forms_window_registers_from_clipboard(new_launcher, scan, monkeypatch):
    """貼り付けた画像（ディスクに書かない）から新しい様式を登録し、その様式で別の傾きのページを判別できる。
    解像度は長い辺を A4 の長い辺とみなして決め、低ければ保存の前に警告する"""
    lw = new_launcher()
    win = open_forms_window(lw)
    rgb = imaging.render(imaging.open_pages(scan("2026bun_08"))[0], dpi=120)
    h, w, _ = rgb.shape
    image = QtGui.QImage(rgb.data, w, h, 3 * w, QtGui.QImage.Format.Format_RGB888)
    QtWidgets.QApplication.clipboard().setImage(image.copy())
    box = QtWidgets.QMessageBox.StandardButton
    asked = ask_with(monkeypatch, [box.Yes])

    def steps(ed):
        assert isinstance(win.pages[0], imaging.MemoryPage)
        ed.name_edit.setText(JUUTARU_NAME)
        ed.add_box((1074, 183, 410, 59))

    opened = drive_editor(monkeypatch, lw, steps)
    win.paste_action.trigger()  # Ctrl+V
    wait(lw, lambda: opened and not opened[0].busy and not win.panel.busy)
    # 低い解像度の警告は、範囲を描く画面を開く前に出す
    assert any("元のページの解像度は" in text for text, _ in asked)
    assert opened[0].saved is not None
    loaded = formstore.load(formstore.choose_folder(""))
    page = imaging.open_pages(scan("2026bun_08", angle=3))[0]
    res = pipeline.analyze(page, formset=loaded.active)
    assert res.form == JUUTARU_NAME and len(res.boxes) == 1


def test_forms_window_does_not_close_while_reading_forms(new_launcher, monkeypatch):
    """様式の編集の画面は、裏で様式のフォルダを読んでいるあいだ（「このページを使う」の後など）は閉じない。
    読み終わったときに、閉じた画面を親にして範囲を描く画面を開かないため"""
    lw = new_launcher()
    win = open_forms_window(lw)
    gate = threading.Event()
    lw.session.background.run(lambda: gate.wait(60), callback=lambda *a: None)
    win.close()
    assert win.isVisible() and lw.child is win
    gate.set()
    wait(lw, lambda: lw.session.background.running == 0)
    win.close()
    assert not win.isVisible() and lw.child is None
    wait(lw, lambda: not lw.loading)


def test_paste_without_image_tells_how(new_launcher):
    """クリップボードに画像がなければ、用紙の画像をコピーしてから貼り付けるよう知らせる"""
    lw = new_launcher()
    win = open_forms_window(lw)
    QtWidgets.QApplication.clipboard().clear()
    win.paste_action.trigger()
    assert lw.shown[-1] == formswin.NO_CLIPBOARD_IMAGE
    assert win.operation is None


def test_forms_window_replaces_with_new_paper(new_launcher, scan, monkeypatch):
    """「新しい用紙に置き換える」で、選んだ様式の見本の画像を、新しい用紙のページに置き換える。範囲を描く画面は、
    新しい用紙のページを今の見本と位置合わせし、範囲を移したものを初めの値にする。範囲を描く画面を開く前に、移した
    範囲の記入を確かめて警告する（記入した用紙なので警告が出て、「はい」で続ける）。保存した様式の大きさは新しい
    用紙の作業画像の大きさで、変更の記録に置き換えを書く"""
    lw = new_launcher()
    win = open_forms_window(lw)
    select(win.panel, "nta-fuyou-r9")
    path = scan("2027bun_01", angle=3, filled=True)
    page = cached_analyze(imaging.open_pages(path)[0])
    monkeypatch.setattr(formswin, "choose_source", lambda *a: formswin.SOURCE_FILE)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), "")
    )

    def steps(ed):
        assert (
            ed.windowTitle() == "様式の編集：扶養控除等申告書（新しい用紙への置き換え）"
        )
        assert ed.base_is_work and ed.status.text().startswith(
            "今の見本と位置合わせできたので"
        )
        assert [(b.kind, round(b.x), round(b.y)) for b in ed.boxes] == [
            (b.kind, round(b.x), round(b.y)) for b in page.boxes
        ]

    asked = ask_with(monkeypatch, [QtWidgets.QMessageBox.StandardButton.Yes])
    opened = drive_editor(monkeypatch, lw, steps)
    win.panel.b_replace.click()  # 1ページだけなので、そのページを使う
    wait(lw, lambda: opened and not opened[0].busy and not win.panel.busy)
    entry = asked[0][0]
    assert "に記入があるようです。" in entry and entry.endswith(
        formstext.REPLACE_QUESTION
    )
    saved = opened[0].saved
    h, w = page.work.shape
    assert saved.id == "nta-fuyou-r9" and saved.size == (w, h)
    # 見本の罫線は、新しい用紙のページから読み直す
    assert saved.lines == rulings.read(page.work)
    assert row_cells(win.panel, "nta-fuyou-r9")[1] == "第1版"
    log = formstore.default_folder() / formsave.CHANGE_LOG
    assert log.read_text("utf-8-sig").splitlines()[1].split(",")[2] == (
        "replace sample image"
    )


def test_replacing_stops_when_warning_is_declined(new_launcher, scan, monkeypatch):
    """置き換えで、範囲を描く画面を開く前の警告に「いいえ」と答えると、範囲を描く画面を開かない。確かめる処理が
    失敗したときは、その理由を警告にする"""
    lw = new_launcher()
    win = open_forms_window(lw)
    select(win.panel, "nta-fuyou-r9")
    path = scan("2027bun_01", angle=3, filled=True)
    monkeypatch.setattr(formswin, "choose_source", lambda *a: formswin.SOURCE_FILE)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), "")
    )
    asked = ask_with(monkeypatch, [QtWidgets.QMessageBox.StandardButton.No])
    opened = drive_editor(monkeypatch, lw, lambda ed: False)
    win.panel.b_replace.click()
    wait(lw, lambda: asked and win.analyzing is None and not win.panel.busy)
    assert "に記入があるようです。" in asked[0][0] and opened == []
    assert formeditor.page_check_warnings(None, RuntimeError("失敗"), None) == [
        "記入と歪みを確かめられませんでした。失敗"
    ]


def test_forms_window_asks_when_page_is_similar(new_launcher, scan, monkeypatch):
    """登録を始めるとき、選んだページが登録済みの様式の見本に類似していれば、その様式を開くか、新しい様式として
    登録を続けるか、やめるかを尋ねる。どの見本にも類似しないページでは尋ねない"""
    lw = new_launcher()
    win = open_forms_window(lw)
    path = [scan("2027bun_01", angle=3)]
    monkeypatch.setattr(formswin, "choose_source", lambda *a: formswin.SOURCE_FILE)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(path[0]), "")
    )
    asked, answer = [], [formswin.OPEN_SIMILAR]
    monkeypatch.setattr(
        formswin,
        "similar_choice",
        lambda parent, similar: asked.append([s.name for s in similar]) or answer[0],
    )
    opened = drive_editor(monkeypatch, lw, lambda ed: False)
    # 「様式『扶養控除等申告書』を開く」：その様式の編集の画面を開き、登録はしない
    win.panel.b_register.click()  # 1ページだけなので、そのページを使う
    wait(lw, lambda: opened and not win.panel.busy)
    assert asked == [["扶養控除等申告書"]]
    assert opened[0].windowTitle() == "様式の編集：扶養控除等申告書"
    # 「やめる」：何も開かない
    answer[0] = None
    win.panel.b_register.click()
    wait(lw, lambda: len(asked) == 2 and win.analyzing is None and not win.panel.busy)
    assert len(opened) == 1 and win.operation is None
    # どの見本にも類似しないページ（従たる給与の申告書）では尋ねずに、登録に進む
    path[0] = scan("2026bun_08")
    win.panel.b_register.click()
    wait(lw, lambda: len(opened) == 2 and not win.panel.busy)
    assert len(asked) == 2 and opened[1].windowTitle() == "様式を登録"


def test_forms_window_asks_before_replacing_with_other_form(
    new_launcher, scan, monkeypatch
):
    """置き換えるとき、選んだページが置き換える様式よりもほかの様式の見本に類似していれば、置き換えるかを尋ねる。
    既定は「やめる」"""
    lw = new_launcher()
    win = open_forms_window(lw)
    select(win.panel, "nta-hoken-r8")
    path = scan("2027bun_01", angle=3)  # 扶養控除等申告書
    monkeypatch.setattr(formswin, "choose_source", lambda *a: formswin.SOURCE_FILE)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), "")
    )
    asked = []
    monkeypatch.setattr(widgets, "confirm", lambda *a, **k: asked.append(a) or False)
    opened = drive_editor(monkeypatch, lw, lambda ed: False)
    win.panel.b_replace.click()
    wait(lw, lambda: asked and win.analyzing is None and not win.panel.busy)
    _, _, text, _, yes, no = asked[0]
    assert text == formstext.replace_text(
        bundled("nta-hoken-r8"), bundled("nta-fuyou-r9")
    )
    assert (yes, no) == ("置き換える", "やめる")
    assert opened == [] and win.operation is None


def test_editor_explains_title_words(window, files, monkeypatch):
    """範囲を描く画面の題名の語のまとまりは「題名での判別」とし、使う場面と、2つの欄の見出し、行とスペースの違いを
    示す"""
    open_and_wait(window, [files[0]])
    texts = []

    def steps(ed):
        box = next(
            b for b in ed.findChildren(QtWidgets.QGroupBox) if "題名" in b.title()
        )
        texts.append(box.title())
        texts.extend(lab.text() for lab in box.findChildren(QtWidgets.QLabel))
        return False

    drive_editor(monkeypatch, window, steps)
    formeditor.register_from_page(window.session, with_work(window), window)
    wait(window, lambda: texts)
    assert texts == [
        "題名での判別",
        "見本に類似した様式が2つ以上あるページで、どの様式かを題名から判別するのに使います。",
        "題名に次の語があれば、この様式と判別します",
        "ただし、題名に次の語があれば、この様式と判別しません",
        "・行ごとに、別々の条件になります。どれか1行に当てはまれば十分です。",
        (
            "・1行の中をスペースで区切ると、区切った語がすべて題名にあるときだけ、その行に当てはまります。"
            "例：「令和8年分　扶養控除等」は、「令和8年分」と「扶養控除等」の両方がある題名にだけ当てはまります。"
        ),
    ]


def test_editor_can_be_maximized_zoomed_and_remembers_size(
    window, files, tmp_path, monkeypatch
):
    """範囲を描く画面は最大化でき（最小化のボタンはない）、全体を表示・拡大・縮小があり、拡大した後も描いた範囲は
    作業画像の座標になる。ウィンドウの大きさは次に開いたときに戻る"""
    use_forms_folder(tmp_path)
    open_and_wait(window, [files[0]])
    ask_with(monkeypatch, [QtWidgets.QMessageBox.StandardButton.Yes])
    sizes = []

    def steps(ed):
        flags = ed.windowFlags()
        assert flags & Qt.WindowType.WindowMaximizeButtonHint
        assert not flags & Qt.WindowType.WindowMinimizeButtonHint
        if sizes:  # 2回目は、1回目に閉じたときの大きさで開く
            assert (ed.width(), ed.height()) == sizes[0]
            ed.reject()
            return False
        ed.show()
        ed.resize(700, 500)  # 表示なしの環境の画面（800×600）に収まる大きさ
        window.qapp.processEvents()
        actions = {a.text(): a for a in ed.findChildren(QtGui.QAction)}
        assert [s.toString() for s in actions["拡大"].shortcuts()] == [
            "Ctrl++",
            "Ctrl+=",
        ]
        assert actions["全体を表示"].toolTip() == "全体を表示（Ctrl+0）"
        view = ed.view
        actions["全体を表示"].trigger()
        fit = view.transform().m11()
        actions["拡大"].trigger()
        assert abs(view.transform().m11() - fit * 1.25) < 1e-6
        actions["縮小"].trigger()
        assert abs(view.transform().m11() - fit) < 1e-6
        actions["拡大"].trigger()
        before = len(ed.boxes)
        p0, p1 = view.mapFromScene(100, 100), view.mapFromScene(300, 200)
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=p0)
        QTest.mouseMove(view.viewport(), p1)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=p1)
        assert len(ed.boxes) == before + 1
        x, y, w, h = ed.boxes[-1].rect()
        tol = 2 / view.transform().m11()  # 画面の1画素分の丸め
        assert abs(x - 100) <= tol and abs(y - 100) <= tol
        assert abs(w - 200) <= 2 * tol and abs(h - 100) <= 2 * tol
        sizes.append((ed.width(), ed.height()))
        ed.reject()
        return False

    opened = drive_editor(monkeypatch, window, steps)
    for n in range(1, 3):
        formeditor.register_from_page(window.session, with_work(window), window)
        wait(window, lambda n=n: len(opened) == n)
    assert len(sizes) == 1


def test_forms_screens_have_names_for_automation(new_launcher, reference, tmp_path):
    """様式の編集の画面・範囲を描く画面・用紙の出どころと類似した様式の確認のメッセージの部品は、言語によらない名前を
    持つ（Windows の上の自動の確認で見分けるため）"""
    lw = new_launcher()
    win = open_forms_window(lw)
    try:
        assert win.objectName() == "forms_window"
        for name in (
            "forms_source",
            "forms_pages",
            "forms_view",
            "use_page",
            "cancel_page",
            "forms_window_status",
            "form_list_folder",
            "form_list",
            "form_list_status",
            "edit_form",
            "replace_form",
            "delete_form",
            "register_form",
        ):
            assert len(win.findChildren(QtWidgets.QWidget, name)) == 1, name
    finally:
        win.close()
    work, _ = reference("2027bun_01")
    ed = formeditor.FormEditor(
        lw.session,
        operation=formsave.OP_REGISTER,
        spec=None,
        base=work,
        base_is_work=True,
        fields=[],
        erase=[],
        loaded=formstore.Loaded(tmp_path),
    )
    try:
        assert ed.objectName() == "form_editor"
        for name in (
            "editor_view",
            "draw_mask",
            "draw_erase",
            "form_name",
            "title_words",
            "title_excludes",
            "read_title",
            "use_lines",
            "line_limit",
            "editor_ranges",
            "fit_lines",
            "editor_status",
            "editor_save",
            "editor_cancel",
        ):
            assert len(ed.findChildren(QtWidgets.QWidget, name)) == 1, name
    finally:
        ed.close()
        ed.deleteLater()


def test_source_and_similar_choices_have_names_for_automation(monkeypatch):
    """用紙の出どころを選ぶ確認のメッセージと、類似した様式があったときの確認のメッセージのボタンは、言語によらない
    名前を持つ"""
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    boxes = []

    def exec_(box):
        boxes.append({b.objectName() for b in box.buttons()} | {box.objectName()})
        return 0

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", exec_)
    formswin.choose_source(None, formswin.REGISTER)
    formswin.similar_choice(None, (bundled("nta-fuyou-r9"),))
    assert boxes == [
        {"source_choice", "source_file", "source_clipboard", "source_cancel"},
        {"similar_choice", "open_similar", "continue_register", "cancel_register"},
    ]


def test_forms_window_has_zoom_and_remembers_size(new_launcher):
    """様式の編集の画面のページの表示にも、全体を表示・拡大・縮小があり、ウィンドウの大きさは次に開いたときに戻る"""
    lw = new_launcher()
    win = open_forms_window(lw)
    actions = {a.text(): a for a in win.findChildren(QtGui.QAction)}
    scale = win.view.transform().m11()
    actions["拡大"].trigger()
    assert abs(win.view.transform().m11() - scale * 1.25) < 1e-6
    win.resize(700, 500)  # 表示なしの環境の画面（800×600）に収まる大きさ
    lw.qapp.processEvents()
    size = (win.width(), win.height())
    win.close()
    again = open_forms_window(lw)
    assert (again.width(), again.height()) == size
    again.close()


# ---------------------------------------------------------------- 英語の画面


def test_forms_screens_in_english(
    new_launcher, new_window, scan, tmp_path, monkeypatch
):
    """英語の画面の様式の編集の画面と範囲を描く画面：題名・ボタン・一覧の見出し・枠の題名・範囲の表・範囲の名前の
    初めの値を英語にする"""
    i18n.set_language(i18n.EN)
    lw = new_launcher()
    win = open_forms_window(lw)
    assert win.windowTitle() == "Edit forms - Sumikeshi Studio"
    panel = win.panel
    assert [
        b.text()
        for b in (
            panel.b_edit,
            panel.b_replace,
            panel.b_delete,
            panel.b_register,
        )
    ] == [
        "Edit",
        "Replace sample image",
        "Delete",
        "Register new form",
    ]
    headers = [
        panel.table.horizontalHeaderItem(c).text()
        for c in range(panel.table.columnCount())
    ]
    assert headers == [
        "Name",
        "Revision",
        "Saved at",
        "Saved by",
        "Redaction areas",
        "Erase areas",
        "Title words",
        "Status",
    ]
    assert panel.findChildren(QtWidgets.QGroupBox)[0].title() == "Forms"
    assert win.source_label.text() == "Sheet: not opened yet"
    assert [win.use_button.text(), win.cancel_button.text()] == [
        "Use this page",
        "Cancel",
    ]
    win.close()
    window = new_window()
    use_forms_folder(tmp_path)
    open_and_wait(window, [scan("2026bun_08")])
    window.add_box((1074, 183, 410, 59))
    box = QtWidgets.QMessageBox.StandardButton
    asked = ask_with(monkeypatch, [box.Yes])

    def steps(ed):
        assert ed.windowTitle() == "Register form"
        assert [(b.kind, b.source) for b in ed.boxes] == [("Area 1", Source.MANUAL)]
        headers = [ed.table.horizontalHeaderItem(c).text() for c in range(3)]
        assert headers == ["Area", "Name (shown in the frame tooltip)", "Frame lines"]
        assert [ed.draw_mask.text(), ed.draw_erase.text()] == [
            "Draw redaction areas",
            "Draw erase areas of the sample image",
        ]
        groups = [g.title() for g in ed.findChildren(QtWidgets.QGroupBox)]
        assert groups == [
            "Form name",
            "Identifying by title",
            "Variations of this form",
            "Areas",
        ]
        lines_box = ed.findChildren(QtWidgets.QGroupBox)[2]
        assert [lab.text() for lab in lines_box.findChildren(QtWidgets.QLabel)] == [
            (
                "Sheets of the same document as this form whose layout differs slightly because of "
                "customization such as added fields (variations of this form) are identified by the "
                "order of their ruled lines."
            ),
            "Minimum line match",
            "0.60",
            "- Raise it if other documents are identified as this form.",
            '- Lower it if sheets of this form become "Unknown form".',
            "- If other documents are still identified after you raise it, uncheck the box above.",
        ]
        assert ed.lines_check.text() == (
            "Use the order of ruled lines to identify this form"
        )
        assert ed.table.item(0, 0).text() == "Redact"
        frame = ed.table.cellWidget(0, 2)
        assert [frame.itemText(i) for i in range(frame.count())] == [
            "Top and bottom, left and right",
            "Top and bottom",
            "Left and right",
            "None",
        ]
        ed.draw_erase.setChecked(True)
        ed.add_box((300, 300, 500, 120))
        assert (
            ed.boxes[1].kind == "Erase area 1" and ed.table.item(1, 0).text() == "Erase"
        )
        assert ed.read_title_button.text() == "Read title"
        assert [b.text() for b in ed.buttons.buttons()] == ["Save", "Cancel"]
        return False  # 保存しない

    opened = drive_editor(monkeypatch, window, steps)
    formeditor.register_from_page(window.session, with_work(window), window)
    wait(window, lambda: opened)
    assert asked[0][0].startswith(
        "Everything outside the redaction areas of the sample image is saved as is"
    )
    assert asked[0][0].endswith("\nContinue registering?")
