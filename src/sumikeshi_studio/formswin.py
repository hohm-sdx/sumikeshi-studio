# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""様式の編集の画面（起動の画面の「様式を編集」で開く）

左に様式の一覧と操作（formlist.FormListPanel）、右に、新しい様式にする用紙・新しい用紙のページの一覧と表示を置く。
「新しい様式を登録」「用紙を置き換える」では、PDF・画像のファイルを選ぶか、クリップボードの画像を
貼り付けて用紙を開く（Ctrl+V は、クリップボードの画像で新しい様式を登録する）。ページを選ぶと、そのページを
解析のプロセスで解析し（向きと傾きを直した作業画像と、自動の候補を求める）、範囲を描く画面（formeditor.FormEditor）
に進む。貼り付けた画像はディスクに書かず、メモリのまま解析のプロセスに渡す。
閉じたら、起動の画面に戻る。
"""

from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QImage, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from . import (
    APP_NAME,
    analysis,
    filetypes,
    formeditor,
    formlist,
    formstext,
    uitext,
    widgets,
)
from .formstext import replace_text, similar_text
from .formstore import FormSpec
from .i18n import tr
from .pageview import PageView, zoom_bar
from .prefs import remember_geometry, restore_geometry
from .review import PageState
from .session import Session

TITLE = "様式の編集"
SOURCE_FILE = "file"
SOURCE_CLIPBOARD = "clipboard"
NO_CLIPBOARD_IMAGE = "クリップボードに画像がありません。用紙の画像をコピーしてから、もう一度貼り付けてください。"
GEOMETRY_KEY = "windows/forms_window"  # この画面の大きさと最大化の状態
REGISTER = "register"
REPLACE = "replace"
PICK_TITLES = {
    REGISTER: "様式にする用紙の PDF・画像を選んでください",
    REPLACE: "新しい用紙の PDF・画像を選んでください",
}
# 登録を始めるときに、見本に類似した様式があったときの答え
OPEN_SIMILAR = "open"
CONTINUE = "continue"


def choose_source(parent: QWidget, operation: str) -> str | None:
    """用紙をファイルから開くか、クリップボードの画像を貼り付けるかを選ばせる（やめたら None）"""
    box = QMessageBox(parent)
    # 自動の確認で見分けるための、言語によらない名前（launcher.py の起動の画面と同じ決まり）
    box.setObjectName("source_choice")
    box.setWindowTitle(tr(TITLE))
    box.setText(
        tr(
            "様式にする用紙を、どこから開きますか？"
            if operation == REGISTER
            else "新しい用紙を、どこから開きますか？"
        )
    )
    file_button = box.addButton(
        tr("PDF・画像のファイルを選ぶ"), QMessageBox.ButtonRole.AcceptRole
    )
    paste_button = box.addButton(
        tr("クリップボードの画像を貼り付ける"), QMessageBox.ButtonRole.AcceptRole
    )
    cancel_button = box.addButton(tr("やめる"), QMessageBox.ButtonRole.RejectRole)
    file_button.setObjectName("source_file")
    paste_button.setObjectName("source_clipboard")
    cancel_button.setObjectName("source_cancel")
    box.exec()
    clicked = box.clickedButton()
    if clicked is file_button:
        return SOURCE_FILE
    if clicked is paste_button:
        return SOURCE_CLIPBOARD
    return None


def similar_choice(parent: QWidget, similar: tuple[FormSpec, ...]) -> str | None:
    """選んだページが登録済みの様式の見本に類似していれば、その様式を開くか、新しい様式として登録を続けるかを
    尋ねる。OPEN_SIMILAR・CONTINUE、やめるなら None（テストでは置き換える）"""
    box = QMessageBox(parent)
    box.setObjectName("similar_choice")
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(tr(TITLE))
    box.setText(similar_text(similar))
    open_button = box.addButton(
        tr("様式『{name}』を開く", name=similar[0].name),
        QMessageBox.ButtonRole.AcceptRole,
    )
    continue_button = box.addButton(
        tr("新しい様式として登録を続ける"), QMessageBox.ButtonRole.AcceptRole
    )
    cancel_button = box.addButton(tr("やめる"), QMessageBox.ButtonRole.RejectRole)
    open_button.setObjectName("open_similar")
    continue_button.setObjectName("continue_register")
    cancel_button.setObjectName("cancel_register")
    box.setDefaultButton(open_button)
    box.setEscapeButton(cancel_button)
    with widgets.dialog():
        box.exec()
    clicked = box.clickedButton()
    if clicked is open_button:
        return OPEN_SIMILAR
    if clicked is continue_button:
        return CONTINUE
    return None


def clipboard_rgb() -> np.ndarray | None:
    """クリップボードの画像（RGB）。画像がなければ None"""
    image = QApplication.clipboard().image()
    if image.isNull():
        return None
    image = image.convertToFormat(QImage.Format.Format_RGB888)
    w, h, stride = image.width(), image.height(), image.bytesPerLine()
    data = np.frombuffer(image.constBits(), np.uint8, count=stride * h)
    return data.reshape(h, stride)[:, : w * 3].reshape(h, w, 3).copy()


class FormsWindow(QMainWindow):
    """様式の編集の画面"""

    closed = Signal()  # 閉じた（起動の画面に戻る）

    def __init__(self, session: Session):
        super().__init__()
        self.setWindowTitle(f"{tr(TITLE)} - {tr(APP_NAME)}")
        self.resize(1500, 900)
        restore_geometry(self, GEOMETRY_KEY)
        # 閉じたら Qt の部品ごと消す（ボタンの処理が画面を参照しているので、消さないと、閉じた画面と用紙の画像が
        # 残る）
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.session = session
        self.pages: list = []  # 開いた用紙のページ（imaging.SourcePage・MemoryPage）
        self.results: dict[int, object] = {}  # ページの番号 → 解析の結果
        self.analyzing: int | None = None  # 解析しているページの番号
        self.operation: tuple[str, FormSpec | None] | None = (
            None  # 用紙を待っている操作
        )
        self.pending_use = False  # 解析が終わったら、そのページを使う
        # 用紙を開くたびに増やす番号。前の用紙の解析の結果を、今の用紙の結果として使わない
        self.generation = 0
        self.panel = formlist.FormListPanel(
            session, on_register=self.start_register, on_replace=self.start_replace
        )
        self.source_label = QLabel(tr("用紙：まだ開いていません"))
        self.source_label.setWordWrap(True)
        self.page_list = QListWidget()
        self.page_list.currentRowChanged.connect(self.page_selected)
        self.view = PageView()
        self.view.edit_mode = True  # 表示だけ（範囲の操作は、範囲を描く画面で行う）
        self.use_button = QPushButton(tr("このページを使う"))
        self.use_button.clicked.connect(self.use_page)
        self.cancel_button = QPushButton(tr("やめる"))
        self.cancel_button.clicked.connect(self.cancel_operation)
        self.status = QLabel()
        self.status.setWordWrap(True)
        pages_row = QHBoxLayout()
        pages_row.addWidget(self.page_list, 1)
        view_column = QVBoxLayout()  # ページの表示と、その上の全体を表示・拡大・縮小
        view_column.addWidget(zoom_bar(self.view, self))
        view_column.addWidget(self.view, 1)
        pages_row.addLayout(view_column, 4)
        buttons = QHBoxLayout()
        buttons.addWidget(self.status, 1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.use_button)
        right = QVBoxLayout()
        right.addWidget(self.source_label)
        right.addLayout(pages_row, 1)
        right.addLayout(buttons)
        right_widget = QWidget()
        right_widget.setLayout(right)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.panel)
        splitter.addWidget(right_widget)
        splitter.setSizes([650, 850])
        self.setCentralWidget(splitter)
        self.paste_action = QAction(
            tr("クリップボードの画像から新しい様式を登録する"), self
        )
        self.paste_action.setShortcut(QKeySequence.StandardKey.Paste)
        self.paste_action.triggered.connect(
            lambda: self.start_register(SOURCE_CLIPBOARD)
        )
        self.addAction(self.paste_action)
        # 自動の確認で見分けるための、言語によらない名前（launcher.py の起動の画面と同じ決まり）
        for widget, name in (
            (self, "forms_window"),
            (self.source_label, "forms_source"),
            (self.page_list, "forms_pages"),
            (self.view, "forms_view"),
            (self.use_button, "use_page"),
            (self.cancel_button, "cancel_page"),
            (self.status, "forms_window_status"),
        ):
            widget.setObjectName(name)
        self.view.show_page(
            None,
            tr(
                "「新しい様式を登録」「用紙を置き換える」で、用紙の PDF・画像のファイルを選ぶか、\n"
                "クリップボードの画像を貼り付けてください（Ctrl+V でも貼り付けられます）"
            ),
        )
        self.update_state()

    # 用紙を開く

    def start_register(self, source: str | None = None):
        self.start(REGISTER, None, source)

    def start_replace(self, spec: FormSpec, source: str | None = None):
        self.start(REPLACE, spec, source)

    def start(self, operation: str, spec: FormSpec | None, source: str | None):
        if self.panel.busy:
            return
        source = source or choose_source(self, operation)
        if source == SOURCE_FILE:
            with widgets.dialog():
                name, _ = QFileDialog.getOpenFileName(
                    self, tr(PICK_TITLES[operation]), "", filetypes.file_filter()
                )
            if not name:
                return
            from . import imaging

            try:
                pages = imaging.open_pages(Path(name))
            except imaging.InputError as e:
                QMessageBox.warning(self, tr(TITLE), str(e))
                return
            label = Path(name).name
        elif source == SOURCE_CLIPBOARD:
            rgb = clipboard_rgb()
            if rgb is None:
                QMessageBox.information(self, tr(TITLE), tr(NO_CLIPBOARD_IMAGE))
                return
            from . import imaging

            pages = [imaging.memory_page(rgb)]
            label = tr(
                "クリップボードの画像（{width}×{height}画素）",
                width=rgb.shape[1],
                height=rgb.shape[0],
            )
        else:
            return
        self.operation = (operation, spec)
        self.show_source(pages, label)

    def show_source(self, pages: list, label: str):
        self.generation += 1
        self.pending_use = False
        self.pages, self.results = pages, {}
        self.source_label.setText(tr("用紙：{name}", name=label))
        self.page_list.blockSignals(True)
        self.page_list.clear()
        for i in range(len(pages)):
            self.page_list.addItem(tr(uitext.PAGE_NUMBER, number=i + 1))
        self.page_list.blockSignals(False)
        self.page_list.setCurrentRow(0)  # 1ページ目を解析して表示する
        if len(pages) == 1:
            self.use_page()  # 1ページだけなら、そのページを使う
        self.update_state()

    def page_selected(self, row: int):
        if row < 0 or row >= len(self.pages):
            return
        if row in self.results:
            self.show_result(row)
        else:
            self.analyze(row)
        self.update_state()

    def analyze(self, row: int):
        """ページを解析のプロセスで解析する（様式のフォルダの全部の様式を使う。この端末で使わない様式も
        含める。登録するときに、ほかの様式と取り違えないかを見るため）"""
        if self.analyzing is not None:
            return
        self.analyzing = row
        self.view.show_page(None, uitext.ANALYZING)
        loaded = self.panel.loaded
        formset = tuple(loaded.forms) if loaded is not None and not loaded.error else ()
        self.session.analyzer.call(
            analysis.analyze,
            analysis.AnalysisJob(
                row, self.pages[row], None, self.session.engine, formset
            ),
            callback=lambda result, error, row=row, gen=self.generation: self.analyzed(
                row, result, error, gen
            ),
        )

    def analyzed(self, row: int, result, error, generation: int | None = None):
        self.analyzing = None
        if generation is not None and generation != self.generation:
            # 解析しているあいだに、別の用紙を開いた。前の用紙の結果は捨て、今の用紙の選んでいるページを解析する
            current = self.page_list.currentRow()
            if 0 <= current < len(self.pages) and current not in self.results:
                self.analyze(current)
            self.update_state()
            return
        if error is not None:
            QMessageBox.warning(
                self,
                tr(TITLE),
                tr("このページを解析できませんでした。{error}", error=error),
            )
            self.view.show_page(
                None, tr("このページを解析できませんでした。\n{error}", error=error)
            )
            self.update_state()
            return
        self.results[row] = result
        current = self.page_list.currentRow()
        if current == row:
            self.show_result(row)
            if self.pending_use:
                self.use_page()
        elif current >= 0 and current not in self.results:
            self.analyze(current)  # 解析しているあいだに、別のページを選んだ
        self.update_state()

    def show_result(self, row: int):
        self.view.show_page(self.page_state(row))

    def page_state(self, row: int) -> PageState:
        return PageState(self.pages[row], None, row, result=self.results[row])

    # 用紙のページを使う

    def use_page(self):
        """選んだページで、登録・置き換えの範囲を描く画面に進む"""
        row = self.page_list.currentRow()
        if self.operation is None or row < 0:
            return
        if row not in self.results:
            self.pending_use = True
            if self.analyzing is None:
                self.analyze(row)
            self.update_state()
            return
        self.pending_use = False
        operation, spec = self.operation
        self.operation = None
        st = self.page_state(row)
        # 見本に類似した様式（点の多い順。解析は、この PC で使わない様式も含む全部の様式で行っている）
        similar = st.result.similar
        if operation == REGISTER:
            # 登録済みの様式の見本に類似していれば、その様式で足りないかを尋ねる
            choice = similar_choice(self, similar) if similar else CONTINUE
            if choice == OPEN_SIMILAR:
                self.panel.edit_form(similar[0].id)
            elif choice == CONTINUE:
                formeditor.register_from_page(
                    self.session, st, self, on_saved=self.panel.registered
                )
        else:
            # ほかの様式の見本により類似していれば、置き換える様式が正しいかを尋ねる
            ids = [s.id for s in similar]
            if (
                similar
                and ids[0] != spec.id
                and not widgets.confirm(
                    self,
                    tr(TITLE),
                    replace_text(spec, similar[0]),
                    "",
                    tr("置き換える"),
                    tr("やめる"),
                )
            ):
                self.update_state()
                return
            self.panel.replace_with(spec, st)
        self.update_state()

    def cancel_operation(self):
        self.operation, self.pending_use = None, False
        self.update_state()

    def update_state(self):
        waiting = self.operation is not None
        self.use_button.setEnabled(waiting and self.page_list.currentRow() >= 0)
        self.cancel_button.setEnabled(waiting)
        registering = self.operation[0] == REGISTER if waiting else None
        self.status.setText(
            formstext.pick_page_status(
                self.analyzing is not None, registering, len(self.pages)
            )
        )

    def busy(self) -> bool:
        """閉じられない：様式を読み書きしている・ページを解析している途中。裏の処理（session.background）の
        途中も含める。「このページを使う」の後に様式のフォルダを読んでいるあいだに閉じると、読み終わったときに、
        閉じた画面を親にして範囲を描く画面を開こうとするため"""
        return (
            self.panel.busy
            or self.analyzing is not None
            or self.session.background.running > 0
        )

    def closeEvent(self, event):
        """閉じる（起動の画面に戻る）。様式を保存している途中・ページを解析している途中は閉じない"""
        if self.busy():
            event.ignore()
            return
        remember_geometry(self, GEOMETRY_KEY)
        # 開いた用紙とその画像（クリップボードから貼り付けた画像を含む）を手放す
        self.pages, self.results, self.operation = [], {}, None
        self.view.show_page(None)
        event.accept()
        self.closed.emit()
