# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""様式の一覧と、その操作

様式のフォルダの読み書きは裏のスレッド（workers.Background）で、用紙を置き換えるときの新しいページと今の見本の
位置合わせは解析のプロセス（workers.Analyzer.call）で行い、画面を止めない。共有フォルダにつながらないと、
読み書きに数十秒かかることがあるため。処理のあいだは、操作のボタンを押せなくし、様式の画面（formswin.py）は
閉じる操作を受け付けない。

起動を遅くしないよう、このモジュールは、起動の画面が様式の画面を開くときに読み込む。範囲を描く画面は
formeditor.py に置く。
"""

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import (
    analysis,
    formeditor,
    formsave,
    formstext,
    formstore,
    i18n,
    uitext,
    widgets,
)
from .formstext import (
    done_with_warnings,
    form_cells,
    form_rows,
    form_status,
    load_failed,
)
from .formstore import FormSpec, Loaded
from .i18n import tr
from .prefs import forms_choice
from .widgets import Tone


def read_template(spec: FormSpec):
    """様式の型の画像（TEMPLATE_SCALE の解像度、グレー）を読む（裏のスレッドで呼ぶ）。解析と同じ確かめ方で読み、読め
    なければ formstore.FormError"""
    from . import align  # OpenCV を読み込むので、画面のプロセスでは使うときに読み込む

    return align.read_template_image(spec)


COLUMNS = [
    "名前",
    "版",
    "保存した日時",
    "保存した人",
    "墨消しする範囲",
    "見本から消す範囲",
    "題名の語",
    "状態",
]


class FormListPanel(QWidget):
    """様式の一覧と、その操作（編集する・新しい用紙に置き換える・削除する・新しい様式を登録する）。
    様式の編集の画面（formswin.FormsWindow）に置く

    新しい様式を登録する・新しい用紙に置き換えるは、用紙を選ぶ流れを持つ画面が受け持つ（on_register・
    on_replace。渡されなければ、そのボタンを出さない）。
    """

    def __init__(
        self,
        session,
        on_register=None,
        on_replace=None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.session = session  # 解析のプロセス・裏の処理・使う OCR（session.Session）
        self.on_register = on_register  # 新しい様式を登録する（引数なし）
        self.on_replace = on_replace  # 新しい用紙に置き換える（引数は様式）
        self.loaded: Loaded | None = None
        self.rows: list[tuple[str, FormSpec | None, formstore.Problem | None]] = []
        self.busy = False
        self.changed = False  # 様式を変えた（閉じた後に、起動の画面が一覧を読み直す）
        self.folder_label = QLabel()
        self.folder_label.setWordWrap(True)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([tr(c) for c in COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self.update_buttons)
        self.table.doubleClicked.connect(lambda _: self.edit())
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.b_edit = QPushButton(tr("編集"))
        # 「型の画像」は使う人に伝わりにくいため、用紙を置き換えると書く。ボタンの名前には「…」を付けない
        self.b_replace = QPushButton(tr("用紙を置き換える"))
        self.b_delete = QPushButton(tr("削除"))
        # この画面の主なボタン
        self.b_register = widgets.make_primary(QPushButton(tr("新しい様式を登録")))
        self.b_edit.clicked.connect(self.edit)
        self.b_replace.clicked.connect(self.replace_image)
        self.b_delete.clicked.connect(self.delete)
        self.b_register.clicked.connect(self.register)
        # 自動の確認で見分けるための、言語によらない名前（launcher.py の起動の画面と同じ決まり）
        for widget, name in (
            (self.folder_label, "form_list_folder"),
            (self.table, "form_list"),
            (self.status, "form_list_status"),
            (self.b_edit, "edit_form"),
            (self.b_replace, "replace_form"),
            (self.b_delete, "delete_form"),
            (self.b_register, "register_form"),
        ):
            widget.setObjectName(name)
        buttons = QHBoxLayout()
        for b in (self.b_edit, self.b_replace, self.b_delete):
            buttons.addWidget(b)
        buttons.addStretch()
        buttons.addWidget(self.b_register)
        group = QGroupBox(tr("様式", context="一覧の題名"))
        group_layout = QVBoxLayout(group)
        group_layout.addWidget(self.folder_label)
        group_layout.addWidget(self.table, 1)
        group_layout.addWidget(self.status)
        layout = QVBoxLayout(self)
        layout.addWidget(group, 1)
        layout.addLayout(buttons)
        self.cell_texts: list[list[str]] = []
        self.reload()

    # 読み込み

    def reload(self):
        choice = forms_choice()
        self.set_busy(True, tr(uitext.FORMS_READING))
        self.session.background.run(
            formstore.load,
            choice,
            callback=lambda loaded, error, choice=choice: self.loaded_forms(
                loaded, error, choice
            ),
        )

    def loaded_forms(
        self,
        loaded: Loaded | None,
        error,
        choice: formstore.FolderChoice | None = None,
    ):
        """様式を読み終わった。choice は読もうとした様式のフォルダ（例外で終わったときに、その場所を出す）"""
        self.set_busy(False)
        if error is not None:
            loaded = load_failed(error, choice)
        self.loaded = loaded
        self.folder_label.setText(formstext.folder_label(loaded.folder, loaded.origin))
        if loaded.error:
            self.status.setText(loaded.error)
        self.rows = form_rows(loaded)
        self.table.setRowCount(len(self.rows))
        self.cell_texts = []
        state_column = len(COLUMNS) - 1
        for r, (form_id, spec, problem) in enumerate(self.rows):
            texts = form_cells(spec, problem)
            self.cell_texts.append(texts)
            for c, text in enumerate(texts):
                # 状態の列は、ラベルをセルに置く（表の文は空にする）
                item = QTableWidgetItem("" if c == state_column else text)
                if c == COLUMNS.index("版") and spec is not None and spec.saved_with:
                    item.setToolTip(
                        tr("保存したツール：{version}", version=spec.saved_with)
                    )
                self.table.setItem(r, c, item)
            text, tone = form_status(spec, problem)
            label = widgets.StatusLabel(text, Tone(tone))
            cell = QWidget()
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(4, 1, 4, 1)
            cell_layout.addWidget(label)
            cell_layout.addStretch()
            self.table.setCellWidget(r, state_column, cell)
        self.table.resizeColumnsToContents()
        self.table.resizeRowsToContents()
        self.update_buttons()

    def row_texts(self, row: int) -> list[str]:
        """一覧の行の、列ごとの文（状態の列はラベルの文。テストで使う）"""
        return self.cell_texts[row]

    def selected(self):
        rows = self.table.selectionModel().selectedRows() if self.rows else []
        return self.rows[rows[0].row()] if rows else (None, None, None)

    def update_buttons(self):
        a = formstext.list_actions(*self.selected(), self.busy, self.loaded)
        self.b_edit.setEnabled(a.edit)
        self.b_replace.setEnabled(a.replace)
        self.b_delete.setEnabled(a.delete)
        if a.status is not None:
            self.status.setText(a.status)
        self.b_register.setEnabled(a.register)
        # 登録・置き換えは、用紙を選ぶ流れを持つ画面に置いたときだけ出す
        self.b_register.setVisible(self.on_register is not None)
        self.b_replace.setVisible(self.on_replace is not None)

    def set_busy(self, busy: bool, text: str = ""):
        self.busy = busy
        self.status.setText(text)
        self.update_buttons()

    def folder_ready(self) -> bool:
        """様式のフォルダを読めた。読めなければ、その理由を出す"""
        if self.loaded is None or self.loaded.folder is None or self.loaded.error:
            text = (
                self.loaded.error
                if self.loaded
                else tr("様式のフォルダを読んでいます。")
            )
            QMessageBox.information(
                self, tr(formeditor.TITLE), text or tr("様式のフォルダがありません。")
            )
            return False
        return True

    def done_then_reload(self, failed: str, done: str, warnings: list[str]):
        """削除が終わったとき。failed はできなかったときの文（{error} に理由を入れる。翻訳できるよう、
        操作の語を文につながずに文全体を渡す）、done はできたときの文。warnings は、移した後の処理でできなかったこと"""

        def callback(_result, error):
            self.set_busy(False)
            if error is not None:
                QMessageBox.warning(self, tr(formeditor.TITLE), tr(failed, error=error))
            else:
                self.changed = True
                if warnings:
                    QMessageBox.warning(
                        self,
                        tr(formeditor.TITLE),
                        done_with_warnings(tr(done), warnings),
                    )
            self.reload()

        return callback

    def register(self):
        if self.on_register is not None and self.folder_ready():
            self.on_register()

    def registered(self):
        self.changed = True
        self.reload()

    # 操作

    def edit(self):
        _, spec, _ = self.selected()
        if spec is None or not self.folder_ready():
            return
        self.set_busy(True, tr("見本の画像を読んでいます…"))
        self.session.background.run(
            read_template,
            spec,
            callback=lambda image, error: self.open_editor(spec, image, error),
        )

    def edit_form(self, form_id: str):
        """識別子の様式を一覧で選び、「編集」と同じく範囲を描く画面を開く（登録を始めるときに、類似した様式を開く）"""
        rows = [r for r, (fid, _, _) in enumerate(self.rows) if fid == form_id]
        if not rows:
            return
        self.table.selectRow(rows[0])
        self.edit()

    def open_editor(self, spec: FormSpec, image, error):
        self.set_busy(False)
        if error is not None:
            QMessageBox.warning(
                self,
                tr(formeditor.TITLE),
                str(error)
                if isinstance(error, formstore.FormError)
                else tr(
                    "見本の画像を読めません。{error}",
                    error=f"{type(error).__name__}: {error}",
                ),
            )
            return
        editor = formeditor.FormEditor(
            self.session,
            operation=formsave.OP_EDIT,
            spec=spec,
            base=image,
            base_is_work=False,
            fields=list(spec.fields),
            erase=list(spec.erase),
            loaded=self.loaded,
            parent=self,
        )
        self.run_editor(editor)

    def run_editor(self, editor: formeditor.FormEditor):
        accepted = editor.exec() == QDialog.DialogCode.Accepted
        # 閉じた画面を消す（消さないと、様式の一覧の部品の子として残り、用紙の作業画像を持ち続ける）
        editor.deleteLater()
        if accepted:
            self.changed = True
            self.reload()

    def replace_image(self):
        """選んだ様式を、新しい用紙に置き換える（様式の改定に対応するため）。用紙は画面が選ばせる"""
        _, spec, _ = self.selected()
        if spec is not None and self.on_replace is not None and self.folder_ready():
            self.on_replace(spec)

    def replace_with(self, spec: FormSpec, page):
        """新しい用紙のページ（解析が終わった PageState）を、spec の新しい型の画像にする

        新しいページを今の型と位置合わせし、成り立てば、今の範囲を新しいページの上に移したものを初めの値に
        する。
        """
        self.set_busy(True, tr("新しいページを、今の見本と位置合わせしています…"))
        self.session.analyzer.call(
            analysis.move_ranges,
            page.result.work,
            spec,
            callback=lambda moved, error: self.open_replacing(spec, page, moved, error),
        )

    def open_replacing(self, spec: FormSpec, page, moved, error):
        self.set_busy(False)
        if moved is not None:
            fields, erase = moved
            notice = tr(
                "今の見本と位置合わせできたので、今の範囲を新しいページの上に移しました。位置を確かめてください。"
            )
            lost = [
                new.name
                for old, new in zip(spec.fields, fields)
                if set(formstore.frame_pairs(old)) - set(new.frame or ())
            ]
            if lost:  # 前の記録で枠の線があった組の線が、新しいページで見つからない
                notice = i18n.sentences(
                    notice,
                    tr(
                        "新しいページで、次の範囲の枠の線が見つかりませんでした：{names}。"
                        "「枠の線」の列を確かめてください。",
                        names=i18n.join(lost),
                        count=len(lost),
                    ),
                )
        else:
            fields, erase = list(spec.fields), list(spec.erase)
            notice = tr(
                "今の見本と位置合わせできなかったので、今の範囲の座標をそのまま置きました。"
                "範囲の位置を確かめ、ずれていれば動かしてください。"
            )
            if error is not None:
                notice += tr("（{error}）", error=error)
        # 範囲を描く画面を開く前に、移した範囲の記入と、写真などで歪んだ画像と、低い解像度を確かめる（登録と同じ）
        self.set_busy(True, tr("新しいページを確かめています…"))
        self.session.analyzer.call(
            analysis.check_page,
            page.result.work,
            fields,
            callback=lambda check, error: self.checked_replacing(
                spec, page, fields, erase, notice, check, error
            ),
        )

    def checked_replacing(
        self, spec: FormSpec, page, fields, erase, notice, check, error
    ):
        self.set_busy(False)
        warnings = formeditor.page_check_warnings(
            check, error, formeditor.page_dpi(page)
        )
        if warnings and not formeditor.ask(
            self, "\n".join([*warnings, tr(formstext.REPLACE_QUESTION)])
        ):
            return
        editor = formeditor.FormEditor(
            self.session,
            operation=formsave.OP_REPLACE,
            spec=spec,
            base=page.result.work,
            base_is_work=True,
            fields=fields,
            erase=erase,
            loaded=self.loaded,
            notice=notice,
            parent=self,
        )
        self.run_editor(editor)

    def delete(self):
        form_id, spec, problem = self.selected()
        if form_id is None or not self.folder_ready():
            return
        name = spec.name if spec else problem.name or problem.file
        if not formeditor.ask(
            self,
            tr(
                "様式「{name}」を削除しますか？\n"
                "様式のファイルは、様式のフォルダの「history」に移します。"
                "様式のフォルダを使う全員の PC で、この様式を使わなくなります。",
                name=name,
            ),
        ):
            return
        self.set_busy(True, tr("削除しています…"))
        warnings: list[str] = []
        self.session.background.run(
            formsave.move_to_history,
            self.loaded.folder,
            form_id,
            formsave.OP_DELETE,
            None,
            warnings,
            callback=self.done_then_reload(
                "削除できませんでした。{error}", "削除しました", warnings
            ),
        )
