# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""起動の画面

起動すると、まずこの画面を出す。「ファイルを開く」「フォルダを開く」（またはこの画面へのドラッグ）で
墨消しの画面を開き、「様式を編集」で様式の編集の画面を開く。様式を確定してから墨消しする運用なので、
墨消しの画面からは様式を編集できないようにした（墨消しの途中で誤って様式を変えないため）。
- 上に、ツールの名前・バージョンと、起動の準備の状態を出す。
- 様式の一覧と、それぞれをこの PC で使うかどうかを出し、変えられる（利用者ごとの設定に覚える）。「使う」の
  チェックを外すときと、様式のフォルダを既定の場所に戻すときは、確認のメッセージを出す。
- 様式のフォルダもこの画面で指定する（なければ既定の場所）。
- 起動の準備（解析のプロセスの立ち上げ）は、この画面を出した直後から裏で行う。墨消しの画面と様式の編集の画面の
  両方で使う。
- 墨消しの画面・様式の編集の画面を閉じたら、この画面に戻り、一覧を読み直す。この画面を閉じたら、ツールを終える。
"""

import multiprocessing
import os
import sys
import traceback
from dataclasses import replace
from itertools import pairwise
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import (
    APP_NAME,
    __version__,
    appicon,
    crashlog,
    datadir,
    filetypes,
    forminstall,
    formsave,
    formstext,
    formstore,
    guigc,
    i18n,
    instance,
    prefs,
    uitext,
    version,
    widgets,
    winjob,
)
from .app import MainWindow
from .i18n import tr
from .prefs import (
    FORMS_FOLDER_KEY,
    OPEN_FOLDER_KEY,
    forms_choice,
    forms_off,
    remembered_folder,
    save_language,
    set_forms_off,
    settings,
)
from .session import Session
from .widgets import Tone

COLUMNS = ["様式", "状態", "使う"]
USE_COLUMN = 2
DROP_NOTE = "PDF・画像のファイルやフォルダを、この画面にドラッグしても開けます"
# 言語を選ぶ欄。ラベルは、言語によらず両方の語を出す（読めない言語で起動した人も、欄を見つけられるように）。
# 言語の名前は、その言語の語で出す
LANGUAGE_LABEL = "言語 / Language"
LANGUAGE_NAMES = ((i18n.JA, "日本語"), (i18n.EN, "English"))
# 後から起動したツールから届いたファイルを、ダイアログが閉じるまで開かずに待つときに、確かめ直す間隔（ミリ秒）
MESSAGE_RETRY_MS = 200
# 入力の焦点がこれらの部品にあるときは、Enter キーで主なボタンを押さない
INPUT_WIDGETS = (
    QAbstractButton,
    QAbstractItemView,
    QLineEdit,
    QTextEdit,
    QPlainTextEdit,
    QAbstractSpinBox,
    QComboBox,
)


class LauncherWindow(QMainWindow):
    """起動の画面"""

    def __init__(self, session: Session | None = None):
        super().__init__()
        self.setObjectName("launcher")
        self.setWindowTitle(tr(APP_NAME))
        self.resize(900, 640)
        self.setAcceptDrops(True)
        self.session = session or Session(self)
        self.session.ready_changed.connect(self.preload_done)
        self.loaded: formstore.Loaded | None = None
        self.loading = False
        # 様式のフォルダを替えるときに、裏のスレッドで様式を数えている・コピーしているあいだの状態の文。そのあいだは
        # 起動の画面のボタンを押せない
        self.folder_task = ""
        # 様式の読み込みの番号。最後に頼んだ読み込みの結果だけを使う（古い読み込みの結果を使うと、外した
        # 「使う」のチェックが入った表示に戻るため）
        self.load_id = 0
        self.rows: list[
            tuple[str, formstore.FormSpec | None, formstore.Problem | None]
        ] = []
        # 行ごとの、名前・種類・状態のラベル（一覧のセルに置く部品）
        self.row_labels: list[tuple[QLabel, QLabel, widgets.StatusLabel]] = []
        self.child: QWidget | None = (
            None  # 開いている画面（墨消しの画面・様式の編集の画面）
        )
        self.first_load_done = False  # 起動して最初の様式の読み込みが終わった（after_first_load を1回だけ動かす）

        self._build_all()
        self.update_state()
        QTimer.singleShot(
            0, self.start
        )  # 画面が描かれてから、裏で準備と読み込みを始める

    def _build_all(self):
        """部品を今の言語で作り、ウィンドウの本体にする（言語を切り替えたときも、作り直すために呼ぶ）"""
        title_row = self._build_title()
        blackout = self._build_blackout()
        forms = self._build_forms()
        self._build_body(title_row, blackout, forms)
        # 自動の確認（Windows の UI オートメーション。pywinauto）で部品を見分けるための、言語によらない名前。Qt はこの名前
        # から UI オートメーションの AutomationId を作る（表示の文は画面の言語で変わり、名前がないと同じ種類の部品が
        # 同じ AutomationId になるため）
        for widget, name in (
            (self.ready_label, "ready_status"),
            (self.language_box, "language"),
            (self.version_label, "version"),
            (blackout, "blackout"),
            (self.open_button, "open_files"),
            (self.open_folder_button, "open_folder"),
            (forms, "forms"),
            (self.folder_label, "forms_folder"),
            (self.choose_button, "change_folder"),
            (self.default_button, "default_folder"),
            (self.reload_button, "reload_forms"),
            (self.samples_button, "add_samples"),
            (self.forms_button, "edit_forms"),
            (self.table, "forms_table"),
            (self.summary_label, "forms_summary"),
            (self.status, "forms_status"),
        ):
            widget.setObjectName(name)

    def _build_title(self) -> QHBoxLayout:
        """上の行（ツールの名前と起動の準備の状態、言語を選ぶ欄）とバージョンの文を作る。上の行を返す"""
        # 上：ツールの名前・バージョンと、起動の準備の状態
        self.ready_label = widgets.StatusLabel(tr(formstext.PREPARING), Tone.NEUTRAL)
        # 言語を選ぶ欄は、上の行の右端に置く
        self.language_box = QComboBox()
        for lang, name in LANGUAGE_NAMES:
            self.language_box.addItem(name, lang)
        self.language_box.setCurrentIndex(self.language_box.findData(i18n.language()))
        self.language_box.currentIndexChanged.connect(self.change_language)
        language_label = QLabel(LANGUAGE_LABEL)
        language_label.setBuddy(self.language_box)
        title_row = QHBoxLayout()
        title_row.addWidget(widgets.heading(tr(APP_NAME)))
        title_row.addStretch()
        title_row.addWidget(self.ready_label)
        title_row.addSpacing(16)
        title_row.addWidget(language_label)
        title_row.addWidget(self.language_box)
        self.version_label = widgets.note(version.version_label())
        return title_row

    def _build_blackout(self) -> QGroupBox:
        """「帳票を墨消しする」の枠（ファイルを開く・フォルダを開く）を作る"""
        # 帳票を墨消しする（ボタンの名前は「…」を付けない）
        self.open_button = widgets.make_primary(QPushButton(tr(uitext.OPEN_FILES)))
        self.open_folder_button = QPushButton(tr(uitext.OPEN_FOLDER))
        self.open_button.clicked.connect(self.open_files)
        self.open_folder_button.clicked.connect(self.open_folder)
        open_row = QHBoxLayout()
        open_row.addWidget(self.open_button)
        side = QVBoxLayout()
        side.addWidget(self.open_folder_button, 0, Qt.AlignmentFlag.AlignLeft)
        side.addWidget(widgets.note(tr(DROP_NOTE)))
        open_row.addLayout(side, 1)
        blackout = QGroupBox(tr("帳票を墨消しする"))
        blackout_layout = QVBoxLayout(blackout)
        blackout_layout.addWidget(
            QLabel(
                tr("スキャンした帳票の PDF・画像を開き、様式で決めた欄を墨消しします。")
            )
        )
        blackout_layout.addLayout(open_row)
        return blackout

    def _build_forms(self) -> QGroupBox:
        """「墨消しに使う様式」の枠（様式のフォルダとそのボタン、様式の一覧、件数と状態の文）を作る"""
        # 墨消しに使う様式。様式のフォルダのパスの行の右に「フォルダを」と3つのボタン、その下の行の右端に
        # 「様式を編集」を置く
        self.folder_label = QLabel()
        self.folder_label.setWordWrap(True)
        self.folder_buttons_label = QLabel(tr("フォルダを"))
        self.choose_button = QPushButton(tr("変更"))
        self.default_button = QPushButton(tr("既定の場所に戻す"))
        self.reload_button = QPushButton(tr("読み直す"))
        self.forms_button = QPushButton(tr("様式を編集"))
        # 様式がないときだけ出す（update_state）
        self.samples_button = QPushButton(tr(formstext.ADD_SAMPLES))
        self.samples_button.clicked.connect(self.add_samples)
        self.choose_button.clicked.connect(self.choose_folder)
        self.default_button.clicked.connect(self.reset_folder)
        self.reload_button.clicked.connect(self.reload)
        self.forms_button.clicked.connect(self.open_forms)
        folder_buttons = QHBoxLayout()
        folder_buttons.addWidget(self.folder_buttons_label)
        for b in (self.choose_button, self.default_button, self.reload_button):
            folder_buttons.addWidget(b)
        forms_head = QHBoxLayout()
        forms_head.addWidget(self.folder_label, 1)
        # パスが2行に折り返しても、ボタンはパスの1行目にそろえる
        forms_head.addLayout(folder_buttons)
        forms_head.setAlignment(folder_buttons, Qt.AlignmentFlag.AlignTop)
        edit_row = QHBoxLayout()
        edit_row.addStretch()
        edit_row.addWidget(self.samples_button)
        edit_row.addWidget(self.forms_button)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([tr(c) for c in COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.verticalHeader().setVisible(False)
        # 「使う」のチェックは、列の中央に描く
        self.table.setItemDelegateForColumn(
            USE_COLUMN, widgets.CenteredCheckDelegate(self.table)
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self.use_changed)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.status = QLabel()
        self.status.setWordWrap(True)
        forms = QGroupBox(tr("墨消しに使う様式"))
        forms_layout = QVBoxLayout(forms)
        forms_layout.addLayout(forms_head)
        forms_layout.addLayout(edit_row)
        forms_layout.addWidget(self.table, 1)
        forms_layout.addWidget(self.summary_label)
        forms_layout.addWidget(self.status)
        return forms

    def _build_body(
        self, title_row: QHBoxLayout, blackout: QGroupBox, forms: QGroupBox
    ):
        """部品を並べてウィンドウの本体にし、焦点の決まり・Enter で押すボタン・Tab の順を決める"""
        layout = QVBoxLayout()
        layout.addLayout(title_row)
        layout.addWidget(self.version_label)
        layout.addSpacing(8)
        layout.addWidget(blackout)
        layout.addSpacing(16)
        layout.addWidget(forms, 1)
        body = QWidget()
        body.setLayout(layout)
        # 起動した直後は、どの部品にも入力の焦点を置かない（主なボタンは、様式を読み終わるまで押せないため。
        # 押せるようになったら主なボタンに焦点を移す。update_state）
        body.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCentralWidget(body)
        # Enter キーで、焦点のあるボタンを押す（QMainWindow の中のボタンは、そのままでは Enter で押されない）。
        # Tab キーで焦点が移る順は、画面の上から（隠れている「サンプルの様式を入れる」は、Qt が Tab で飛ばす）
        order = (
            self.open_button,
            self.open_folder_button,
            self.choose_button,
            self.default_button,
            self.reload_button,
            self.samples_button,
            self.forms_button,
        )
        for b in order:
            b.setAutoDefault(True)
        for a, b in pairwise((self.language_box, *order, self.table)):
            QWidget.setTabOrder(a, b)
        body.setFocus()

    def start(self):
        self.session.start()
        self.reload()

    # 様式の一覧

    def reload(self):
        """様式のフォルダを裏のスレッドで読む（共有フォルダにつながらないと、数十秒かかることがある）"""
        self.loading = True
        self.load_id += 1
        self.update_state()
        self.session.background.run(
            formstore.load,
            forms_choice(),
            forms_off(),
            callback=lambda loaded, error, n=self.load_id: self.loaded_forms(
                loaded, error, n
            ),
        )

    def loaded_forms(self, loaded: formstore.Loaded | None, error, load_id=None):
        """様式を読み終わった。後から頼んだ読み込みがあれば、この結果は使わない。この PC で使うかどうかは、
        読み始めたときではなく、今の設定から決める"""
        if load_id is not None and load_id != self.load_id:
            return
        self.loading = False
        if error is not None:
            loaded = formstext.load_failed(error, forms_choice())
        loaded = replace(loaded, off=forms_off())
        self.loaded = loaded
        self.rows = formstext.form_rows(loaded)
        self.row_labels = []
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        for r, (form_id, spec, problem) in enumerate(self.rows):
            name = QLabel(
                spec.name if spec is not None else problem.name or problem.file
            )
            cell = QWidget()
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(6, 2, 6, 2)
            cell_layout.setSpacing(0)
            cell_layout.addWidget(name)
            saved = widgets.note(formstext.saved_text(spec))
            if saved.text():  # 読めない様式などは、保存の情報の行を出さない
                cell_layout.addWidget(saved)
            state = widgets.StatusLabel()
            state_cell = QWidget()
            state_layout = QHBoxLayout(state_cell)
            state_layout.setContentsMargins(6, 2, 6, 2)
            state_layout.addWidget(state)
            state_layout.addStretch()
            self.row_labels.append((name, saved, state))
            self.table.setItem(r, 0, QTableWidgetItem(""))
            self.table.setCellWidget(r, 0, cell)
            self.table.setItem(r, 1, QTableWidgetItem(""))
            self.table.setCellWidget(r, 1, state_cell)
            use = QTableWidgetItem("")
            use.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            use.setCheckState(
                Qt.CheckState.Unchecked
                if form_id in loaded.off
                else Qt.CheckState.Checked
            )
            self.table.setItem(r, USE_COLUMN, use)
        self.table.blockSignals(False)
        self.table.resizeRowsToContents()
        self.update_rows()
        self.update_state()
        if not self.first_load_done:
            self.first_load_done = True
            QTimer.singleShot(0, self.after_first_load)

    def after_first_load(self):
        """すべてのユーザー向けの導入から起動したときだけ、最初の様式の読み込みの後に行う：この利用者の自分だけの
        導入もあれば知らせ、この利用者の最初の起動なら、様式がなければサンプルの様式を入れるかを尋ねる（導入は
        管理者のアカウントで動くことがあり、利用者ごとの準備を導入のときにできないため）"""
        if datadir.install_kind() != datadir.ALL_USERS:
            return
        if folder := datadir.per_user_install():
            widgets.message(
                self,
                tr(APP_NAME),
                tr(formstext.MIXED_NOTICE, folder=folder),
                tr(formstext.MIXED_INFO),
            )
        if prefs.samples_offered() or self.loaded is None or self.loaded.error:
            return  # 様式のフォルダを読めなければ、次の起動で尋ねる
        prefs.set_samples_offered()
        if formstext.no_forms(self.loaded):
            self.add_samples()

    def update_rows(self):
        """一覧の状態のラベル（「使う」のチェックで変わる）"""
        off = self.loaded.off if self.loaded is not None else frozenset()
        for (form_id, spec, problem), (_, _, state) in zip(self.rows, self.row_labels):
            text, tone = formstext.form_status(spec, problem, form_id in off)
            state.set_status(text, Tone(tone))
        # 状態の列の幅は、セルに置いたラベルの幅に合わせる（列の中身から決める幅は、セルの部品の文を見ない）
        widths = [
            self.table.cellWidget(r, 1).sizeHint().width()
            for r in range(len(self.rows))
        ]
        header = self.table.horizontalHeader().sectionSizeFromContents(1).width()
        self.table.setColumnWidth(1, max([header, *widths]))
        self.table.resizeRowsToContents()

    def row_texts(self, row: int) -> tuple[str, str, str]:
        """一覧の行の、名前・保存の情報・状態の文（テストで使う）"""
        name, saved, state = self.row_labels[row]
        return name.text(), saved.text(), state.text()

    def use_changed(self, item: QTableWidgetItem):
        """「使う」を変えた。利用者ごとの設定に覚える。チェックを外すときは、確認のメッセージを出す"""
        if item.column() != USE_COLUMN or self.loaded is None:
            return
        form_id, spec, problem = self.rows[item.row()]
        off = set(forms_off())
        if item.checkState() == Qt.CheckState.Checked:
            off.discard(form_id)
        else:
            name = spec.name if spec is not None else problem.name or problem.file
            if not widgets.confirm(
                self,
                tr(APP_NAME),
                tr("「使う」のチェックを外しますか？"),
                tr(
                    "「{name}」のチェックを外すと、この PC では、その帳票のページの欄が自動で墨消しされなく"
                    "なります。その帳票のページは「様式不明」になり、「⚠ 要確認」が付きます。",
                    name=name,
                ),
                tr("チェックを外す"),
                tr("チェックを外さない"),
            ):
                self.table.blockSignals(True)
                item.setCheckState(Qt.CheckState.Checked)
                self.table.blockSignals(False)
                return
            off.add(form_id)
        set_forms_off(off)
        self.loaded = replace(self.loaded, off=frozenset(off))
        self.update_rows()
        self.update_state()

    def can_blackout(self) -> bool:
        """墨消しの画面を開ける：様式を読み終わり、この PC で使う様式をすべて読めた"""
        return formstext.can_blackout(
            self.loaded, self.loading or bool(self.folder_task)
        )

    def status_message(self) -> str:
        """一覧の下に出す文（様式のフォルダを確かめている・様式をコピーしている・様式を読んでいる・読めない・起動の
        準備ができなかった）"""
        if self.folder_task:
            return self.folder_task
        return formstext.status_message(self.loaded, self.loading, self.session.error)

    def summary_text(self) -> str:
        """一覧の下の、使う件数と説明"""
        return formstext.summary_text(self.loaded, self.loading, len(self.rows))

    def update_state(self):
        choice = forms_choice()
        self.folder_label.setText(formstext.folder_label(choice.path, choice.origin))
        busy = self.loading or bool(self.folder_task)
        self.choose_button.setEnabled(not self.folder_task)
        self.default_button.setEnabled(
            choice.origin == formstore.ORIGIN_USER and not self.folder_task
        )
        self.forms_button.setEnabled(not self.folder_task)
        self.samples_button.setVisible(formstext.no_forms(self.loaded))
        self.samples_button.setEnabled(not busy)
        self.status.setText(self.status_message())
        self.summary_label.setText(self.summary_text())
        text, tone = formstext.ready_status(self.session.error, self.session.ready)
        self.ready_label.set_status(text, Tone(tone))
        can = self.can_blackout()
        # 押せなくする主なボタンに焦点があれば、先に本体に移す。そのままだと、Qt が焦点を Tab の順で次の押せる
        # 「変更」に移し、読み終えた後も「変更」に残って、Enter で「変更」が押されてしまうため（墨消しの画面から
        # 戻って様式を読み直したときなど）
        if not can and self.focusWidget() in (
            self.open_button,
            self.open_folder_button,
        ):
            self.centralWidget().setFocus()
        self.open_button.setEnabled(can)
        self.open_folder_button.setEnabled(can)
        self.reload_button.setEnabled(not busy)
        self.table.setEnabled(not busy)  # 読んでいるあいだは、チェックを変えない
        # 言語を選ぶ欄は、ほかの画面を開いているあいだと、様式のフォルダを読んでいるあいだは押せない（開いている
        # 画面と言語が食い違わないように。読んでいるあいだに作り直さないように）
        self.language_box.setEnabled(self.child is None and not busy)
        # 焦点は、このウィンドウの中で焦点を持つ部品で調べる（QApplication.focusWidget は、ウィンドウが非アクティブだと None に
        # なり、使う人が置いた焦点も主なボタンに動かしてしまう）
        focus = self.focusWidget()
        if can and (focus is None or focus is self.centralWidget()):
            self.open_button.setFocus()

    def preload_done(self, error: str):
        if error:
            QMessageBox.critical(
                self,
                tr(APP_NAME),
                tr(
                    "{failed}。\n{error}", failed=tr(uitext.PRELOAD_FAILED), error=error
                ),
            )
        self.update_state()

    # 画面の言語

    def change_language(self, index: int):
        """言語を選んだ：設定に覚え、その場で起動の画面をその言語で作り直し、様式のフォルダを読み直す（様式の一覧の
        理由の文もその言語にするため）

        ウィンドウは作り直さないので、位置と大きさは変わらない。「使う」は設定から読むので変わらない。焦点は、
        読み終えたら「ファイルを開く」に移る（update_state）。ほかの画面を開いているあいだと、様式のフォルダを
        読んでいるあいだは選べない（選ぶ欄を押せなくしているが、そのときに届いたら元の言語に戻す）。
        """
        lang = self.language_box.itemData(index)
        if lang == i18n.language():
            return
        if self.child is not None or self.loading or self.folder_task:
            self.language_box.blockSignals(True)
            self.language_box.setCurrentIndex(
                self.language_box.findData(i18n.language())
            )
            self.language_box.blockSignals(False)
            return
        save_language(lang)
        i18n.set_language(lang)
        self.setWindowTitle(tr(APP_NAME))
        # 前の部品は、QMainWindow が後で消す（この関数は前の選ぶ欄の合図の中で動くので、すぐには消さない）
        self._build_all()
        # 読み直すまで一覧が空にならないように、今の様式を出しておく
        if self.loaded is not None:
            self.loaded_forms(self.loaded, None)
        self.reload()

    # Enter キー

    def keyPressEvent(self, event):
        """焦点のある部品が受け取らなかった Enter キー。焦点がボタンや入力の部品にないときだけ、主なボタンを押す
        （ボタンに焦点があれば、そのボタンが Enter で押される。setAutoDefault）"""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focus = QApplication.focusWidget()
            if not isinstance(focus, INPUT_WIDGETS) and self.open_button.isEnabled():
                self.open_button.click()
                return
        super().keyPressEvent(event)

    # ファイルを開く

    def open_files(self):
        """この画面の上でファイルを選ぶダイアログを出し、選んだら墨消しの画面で開く（キャンセルなら、この画面のまま）"""
        if not self.can_blackout():
            return
        with widgets.dialog():
            names, _ = QFileDialog.getOpenFileNames(
                self,
                tr("開く"),
                remembered_folder(OPEN_FOLDER_KEY),
                filetypes.file_filter(),
            )
        if names:
            self.open_blackout([Path(n) for n in names])

    def open_folder(self):
        if not self.can_blackout():
            return
        with widgets.dialog():
            name = QFileDialog.getExistingDirectory(
                self, tr(uitext.OPEN_FOLDER), remembered_folder(OPEN_FOLDER_KEY)
            )
        if name:
            self.open_blackout([Path(name)])

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        """PDF・画像・フォルダをドラッグした：墨消しの画面で開く。まだ開けなければ、理由を示す"""
        paths = [
            Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()
        ]
        if not paths or self.child is not None:
            return
        if not self.can_blackout():
            QMessageBox.information(
                self,
                tr(APP_NAME),
                tr("まだファイルを開けません。{reason}", reason=self.status_message()),
            )
            return
        self.open_blackout(paths)

    # 様式のフォルダ

    def choose_folder(self):
        """様式のフォルダを選んで替える（今のフォルダと同じなら何もしない）"""
        if self.folder_task:
            return
        old = forms_choice().path
        with widgets.dialog():
            name = QFileDialog.getExistingDirectory(
                self, tr("様式のフォルダを選ぶ"), str(old)
            )
        if name and not datadir.same_path(name, str(old)):
            self.switch_folder(old, Path(name), reset=False)

    def reset_folder(self):
        """起動の画面で指定したフォルダを消し、既定の場所に戻す（確認のメッセージを出す）"""
        if not self.folder_task:
            self.switch_folder(forms_choice().path, formstore.default_folder(), True)

    def switch_folder(self, old: Path, new: Path, reset: bool):
        """様式のフォルダを old から new に替える。reset なら「既定の場所に戻す」（new は既定の場所）

        2つのフォルダの様式のファイルを裏のスレッドで数えてから（共有フォルダでは時間がかかるため）、new に様式が
        なく old に様式があれば、old の様式をコピーするかを1回尋ねる。new に様式があれば、new の様式を使うことを
        示す。一方のフォルダがもう一方の中にあるときは、コピーする元と先が重なるので尋ねない。
        """
        self.folder_task = tr(uitext.FORMS_CHECKING)
        self.update_state()
        self.session.background.run(
            count_both,
            old,
            new,
            callback=lambda counts, error: self.folders_counted(
                old, new, reset, counts, error
            ),
        )

    def folders_counted(self, old: Path, new: Path, reset: bool, counts, error):
        self.folder_task = ""
        self.update_state()
        old_count, new_count, overlap = (0, 0, True) if error is not None else counts
        title = tr(APP_NAME)
        if new_count:  # 新しいフォルダの様式を使う
            if not reset:
                self.use_folder(new, reset)
                widgets.message(
                    self, title, *formstext.switched_text(old, new, new_count)
                )
                return
            info = formstext.reset_info(new, old, new_count)
        elif old_count and not overlap:  # 前のフォルダの様式をコピーするかを尋ねる
            text, info, choices = formstext.copy_question(reset, old, new, old_count)
            answer = widgets.choose(self, title, text, info, choices)
            if answer == 0:
                self.copy_forms(old, new, reset)
            elif answer == 1:
                self.use_folder(new, reset)
            return
        elif not reset:  # 前のフォルダに様式がない・読めない
            self.use_folder(new, reset)
            return
        else:
            info = formstext.reset_info(new, old, 0)
        if widgets.confirm(
            self,
            title,
            tr(formstext.RESET_QUESTION),
            info,
            tr("既定の場所に戻す"),
            tr("戻さない"),
        ):
            self.use_folder(new, reset)

    def copy_forms(self, old: Path, new: Path, reset: bool):
        """前のフォルダの様式を、裏のスレッドで新しいフォルダにコピーし、できたら新しいフォルダに替える。できなければ
        理由を示し、前のフォルダのままにする"""
        self.folder_task = tr(formstext.COPYING)
        self.update_state()
        self.session.background.run(
            formsave.copy_forms,
            old,
            new,
            callback=lambda _result, error: self.forms_copied(new, reset, error),
        )

    def forms_copied(self, new: Path, reset: bool, error):
        self.folder_task = ""
        if error is None:
            self.use_folder(new, reset)
            return
        self.update_state()
        widgets.message(
            self,
            tr(APP_NAME),
            tr(formstext.COPY_FAILED),
            f"{type(error).__name__}: {error}",
            warning=True,
        )

    def add_samples(self):
        """サンプルの様式を入れるかを尋ね（入れる様式の名前と年分を示す。既定は「入れる」）、入れるなら裏のスレッドで
        入れて読み直す"""
        if self.loading or self.folder_task:
            return
        folder = forms_choice().path
        text, info = formstext.samples_question(folder, forminstall.sample_items())
        if not widgets.confirm(
            self, tr(APP_NAME), text, info, tr("入れる"), tr("入れない"), True
        ):
            return
        self.folder_task = tr(formstext.SAMPLES_ADDING)
        self.update_state()
        self.session.background.run(
            forminstall.add_samples,
            folder,
            callback=lambda result, error: self.samples_added(folder, result, error),
        )

    def samples_added(self, folder: Path, result, error):
        self.folder_task = ""
        if error is not None:
            self.update_state()
            widgets.message(
                self,
                tr(APP_NAME),
                tr(formstext.SAMPLES_FAILED),
                forminstall.error_text(error),
                warning=True,
            )
            return
        # 入れなかった（ほかの PC が先に様式を保存した）ときも、読み直してその様式を出す
        _saved, warnings = result
        if warnings:
            text = tr(formstext.SAMPLES_ADDED, folder=folder)
            widgets.message(
                self,
                tr(APP_NAME),
                formstext.done_with_warnings(text, warnings),
                "",
                warning=True,
            )
        self.reload()

    def use_folder(self, new: Path, reset: bool):
        """設定の様式のフォルダを替えて、読み直す"""
        if reset:
            settings().remove(FORMS_FOLDER_KEY)
        else:
            settings().setValue(FORMS_FOLDER_KEY, str(new))
        self.reload()

    # 画面を開く

    def open_blackout(self, paths: list[Path] | None = None):
        """墨消しの画面を開く（閉じたら、この画面に戻る）。paths があれば、そのファイルを開く"""
        win = MainWindow(self.session)
        win.closed.connect(self.child_closed)
        self.child = win
        self.update_state()  # 言語を選ぶ欄を押せなくする
        win.show()
        self.hide()
        if paths:
            win.open_paths(paths)

    def open_forms(self):
        """様式の編集の画面を開く（閉じたら、この画面に戻り、一覧を読み直す）"""
        from .formswin import FormsWindow

        win = FormsWindow(self.session)
        win.closed.connect(self.child_closed)
        self.child = win
        self.update_state()  # 言語を選ぶ欄を押せなくする
        win.show()
        self.hide()

    def child_closed(self):
        self.child = None
        self.show()
        self.reload()

    def handle_message(self, files: list[str]):
        """後から起動したツールから通知が届いた：今出ている画面を前に出す。ファイルがあれば墨消しの画面で開く

        墨消しの画面なら、そこで開く（「作業を破棄しますか？」の確認は今のまま）。起動の画面なら、墨消しの画面を
        開いてから開く。様式の編集の画面なら、前に出すだけにして、墨消しの画面で開くよう知らせる。
        """
        win = self.child or self
        win.showNormal()
        win.raise_()
        win.activateWindow()
        paths = [Path(f) for f in files]
        if paths:
            self.open_message_files(paths)

    def open_message_files(self, paths: list[Path]):
        """後から起動したツールから届いたファイルを開く。確認のメッセージ・ファイルを選ぶダイアログ・右クリックの
        メニューが開いているあいだと、墨消しの画面がファイルを開いている途中・出力の途中は、終わるまで待つ
        （ダイアログを出しているあいだに開き直すと、確認の後の操作が別のファイルに効き、墨消しの画面が2つ開くことも
        あるため）"""
        child = self.child
        busy = isinstance(child, MainWindow) and (
            child.opening is not None or child.exporting
        )
        if widgets.dialog_open() or busy:
            QTimer.singleShot(MESSAGE_RETRY_MS, lambda: self.open_message_files(paths))
            return
        win = child or self
        if isinstance(self.child, MainWindow):
            if self.child.confirm_discard():
                self.child.open_paths(paths)
        elif self.child is None:
            self.open_blackout(paths)
        else:
            QMessageBox.information(
                win,
                tr(APP_NAME),
                tr(
                    "ファイルは墨消しの画面で開いてください。様式の編集の画面を閉じてから、"
                    "起動の画面の「ファイルを開く」で開くか、起動の画面にドラッグしてください。"
                ),
            )

    def closeEvent(self, event):
        """ツールを終える（解析のプロセスを終える）

        様式を保存している途中は閉じない（後始末を飛ばして終えると、様式のフォルダのファイルが中途半端な状態で
        残るおそれがあるため）。起動の準備（解析のプロセスの立ち上げなど）が終わる前に閉じたときは、ジョブに
        入っていれば、ウィンドウを消して、終える前に書くものを書き終えてから、後始末を飛ばして終える
        （fast_exit）。動いている QThread を手放す後始末を通らないので、abort しない。子と孫のプロセスは、
        Windows がジョブごと終わらせる。ジョブに入れなかったときは、裏のスレッドが終わるのを待ってから終える。
        """
        if self.child is not None:
            event.ignore()
            return
        if self.session.background.running:
            QMessageBox.information(
                self,
                tr(APP_NAME),
                tr("様式のフォルダを読み書きしています。終わってから閉じてください。"),
            )
            event.ignore()
            return
        if self.session.preloading and winjob.active():
            self.hide()
            fast_exit()
            return
        self.session.shutdown()
        event.accept()


def sync_settings() -> None:
    """利用者ごとの設定を書き終える"""
    settings().sync()


# ツールを終える前に書くもの（後始末を飛ばして終えるときも、ふつうに終えるときも、この順に行う）。
# 終えるときの処理を足すときは、ここに足す（後始末を飛ばして終えるときに、黙って飛ばされないように）
BEFORE_EXIT = (
    sync_settings,  # 利用者ごとの設定を書き終える
    crashlog.clear_running,  # 「動いている」マーカーを消し、異常終了の記録に「終了」を書く
    crashlog.flush,  # 異常終了の記録を書き終える
)


def finish_before_exit() -> None:
    for step in BEFORE_EXIT:
        step()


def fast_exit() -> None:
    """終える前に書くものを書き終えてから、後始末を飛ばしてプロセスを終える（ジョブに入っているときだけ使う）"""
    finish_before_exit()
    os._exit(0)


def count_both(old: Path, new: Path) -> tuple[int, int, bool]:
    """前のフォルダと新しいフォルダの様式のファイルの数と、一方がもう一方の中にあるか（裏のスレッドで呼ぶ）"""
    return (
        formstore.count_forms(old),
        formstore.count_forms(new),
        formstore.overlaps(old, new),
    )


def main():
    multiprocessing.freeze_support()  # exe にまとめたときに、解析のプロセスを動かすため
    # 画面の言語。起動の画面で選んだ言語、なければ Windows の表示の言語。前回の異常な終了の警告のメッセージを
    # 出す前に決める
    i18n.set_language(i18n.choose_language(prefs.saved_language()))
    # 異常終了の記録。pythonw.exe で起動しても、強制終了したときの理由が残るようにする
    log = crashlog.setup(crashlog.ROLE_SCREEN)
    crashlog.install_qt_handler()
    # タスク バーでツールのウィンドウを見分ける名前は、ウィンドウを作る前に付ける
    appicon.set_app_id()
    app = QApplication(sys.argv)
    app.setWindowIcon(appicon.app_icon())
    # Qt のアプリケーションの名前は、言語によらない内部の名前にする（画面に出す名前は tr(APP_NAME)）。
    # 表示の名前（setApplicationDisplayName）は決めない。決めると、Qt がウィンドウの題名の後にその名前を
    # 足すことがあり、今の題名が変わるおそれがあるため（推測。Windows で確かめていない）
    app.setApplicationName(prefs.SETTINGS_NAME)
    app.setApplicationVersion(__version__)
    # 画面のプロセスのガベージコレクションは、画面のスレッドで行う（裏のスレッドで始まり、Qt の部品をそのスレッドで
    # 消さないため）。解析のプロセスは別の Python なので、自動のまま
    collector = guigc.start(app)  # noqa: F841  終わるまで持つ
    # 一度に1つだけ起動する。先に起動しているツールがあれば、前に出ることと開くファイルを伝えて
    # 終える（解析のプロセスを立ち上げる前に調べる）。ファイルのパスは、作業のフォルダが違うため絶対パスで渡す
    files = [str(Path(a).resolve()) for a in sys.argv[1:]]
    if instance.forward(files):
        crashlog.write(
            "forwarded the files to the running instance; this process exits"
        )
        crashlog.flush()
        return
    server = instance.Server()
    if not server.listen():
        crashlog.write("could not start listening for later launches")
    abnormal = crashlog.previous_abnormal()
    crashlog.mark_running()
    # 解析のプロセスなど、ツールが立ち上げるプロセスを、閉じたら終わるジョブに入れる（Windows）
    if reason := winjob.enter():
        crashlog.write(
            f"job: {reason} (on closing, wait for the background work before exiting)"
        )

    def excepthook(kind, value, tb):
        crashlog.write_exception("unhandled exception", kind, value, tb)
        if not crashlog.stderr_redirected():
            traceback.print_exception(kind, value, tb)
        QMessageBox.critical(
            None,
            tr(APP_NAME),
            tr(
                "予期しないエラーが起きました。\n{kind}: {value}",
                kind=kind.__name__,
                value=value,
            ),
        )

    sys.excepthook = excepthook
    launcher = LauncherWindow()
    server.message.connect(launcher.handle_message)
    launcher.show()
    if abnormal and log is not None:
        QTimer.singleShot(
            0,
            lambda: QMessageBox.warning(
                launcher, tr(APP_NAME), crashlog.abnormal_text(log)
            ),
        )
    if files:  # 起動の引数のファイルは、墨消しの画面で開く
        launcher.open_blackout([Path(f) for f in files])
    code = app.exec()
    finish_before_exit()  # 閉じる操作で終わった
    sys.exit(code)
