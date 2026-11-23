# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""確認画面の主画面

自動で見つけた候補を画面で確かめ、解除・追加してから墨消ししたPDFを出力する。
開いたファイルとページのデータは review.py、画面に出す文とボタンの条件は reviewtext.py、出力の計画と出力の
前後の文・作業ログの1行は exporttext.py、出力の流れ（ダイアログを出す順と裏の出力）は exportflow.py、裏で動かす処理は
workers.py、元に戻す操作は commands.py、ページの表示は pageview.py にある。
"""

import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QUndoStack
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

# 画面を先に出すため、ここでは重いライブラリを読み込むモジュール（imaging・pipeline・export など）を
# 読み込まない。それらは、画面が出た後に Preloader が裏で読み込む。
# ページの解析は解析のプロセスで行い、このプロセスでは OCR を用意しない
from . import (
    APP_NAME,
    analysis,
    boxes,
    depthdialog,
    exportflow,
    filetypes,
    folders,
    formstore,
    pageview,
    review,
    reviewtext,
    uitext,
    widgets,
    workcache,
)
from .analysis import AnalysisJob
from .commands import (
    AddCommand,
    DeleteCommand,
    GeometryCommand,
    PageDeleteCommand,
    ToggleCommand,
)
from .i18n import tr
from .pageview import COLOR_AUTO, COLOR_MANUAL, COLOR_OFF, PageView
from .prefs import (
    OPEN_FOLDER_KEY,
    forms_choice,
    forms_off,
    remembered_folder,
    settings,
)
from .review import Document, PageState
from .session import Session
from .workers import FileOpener, FormsLoader

# 凡例：色見本（ページの枠と同じ色と線）と文
LEGEND = [
    (COLOR_AUTO, False, "様式で決めた欄"),
    (COLOR_MANUAL, False, boxes.KIND_MANUAL),
    (COLOR_OFF, True, "墨消ししない（解除した欄）"),
]
EMPTY_TEXT = (
    "「ファイルを開く」「フォルダを開く」で PDF・画像を開いてください。\n"
    "PDF・画像のファイルやフォルダを、この画面にドラッグしても開けます。"
)


# ---------------------------------------------------------------- 主画面

PAGE_DELETE = (
    "このページを削除"  # ページの削除の操作の名前（ツールバーと一覧の右クリック）
)
PAGE_UNDELETE = "削除を取り消す"
# ツールバーの「このページを削除」の説明。一覧で Delete キーでも削除できることを示す
PAGE_DELETE_TIP = "表示しているページを削除する（左の一覧でページを選んで Delete キーを押しても削除できます）"

PAGE_ROLE = (
    Qt.ItemDataRole.UserRole
)  # ページの行に持たせる、全ファイルを通したページの番号
# ファイルを開く途中の進み具合を出すまでの時間（ミリ秒）。すぐに開き終わるときに、ダイアログを一瞬だけ出さないため
OPEN_PROGRESS_MS = 500


class MainWindow(QMainWindow):
    """墨消しの画面（起動の画面の「ファイルを開く」などで開く）

    解析のプロセスと起動の準備は、起動の画面から受け取る（session）。受け取らなければ自分で作り、閉じるときに
    解析のプロセスを終える（テストで使う）。様式の一覧・登録・編集は、様式の編集の画面で行い、この画面では
    行わない（墨消しの途中で誤って様式を変えないため）。
    """

    closed = Signal()  # 閉じた（起動の画面に戻る）

    def __init__(self, session: Session | None = None):
        super().__init__()
        self.setWindowTitle(tr(APP_NAME))
        self.resize(1400, 900)
        self.setAcceptDrops(True)
        # 閉じたら Qt の部品ごと消す。ボタンの処理（lambda）が画面を捕まえていると、Python のガベージコレクションでは
        # 回収されず、閉じた画面とページの画像が残るため
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.docs: list[Document] = []
        self.pages: list[PageState] = []  # 全ファイルのページを開いた順に並べたもの
        self.current = -1
        # 解析の依頼の番号。別の墨消しの画面（閉じた画面）の依頼の結果と取り違えないよう、数でなく
        # その画面だけの物にする
        self.token = object()
        self.own_session = session is None
        self.session = session or Session(self)
        self.analyzer = self.session.analyzer  # ページの解析を、解析のプロセスに頼む
        self.analyzer.page_done.connect(self.page_analyzed)
        # 出力の流れ。出力中か（exporting）と出力先を持つ
        self.export_flow = exportflow.ExportFlow(self)
        self.undo = QUndoStack(self)
        self.engine = self.session.engine  # 題名を読む OCR（RapidOCR だけ）
        # 開いたファイルの解析に使う様式（ファイルを開くときに読む）
        self.forms: formstore.Loaded | None = None
        # 開いたときに除いた墨消し済みのPDF（画面下に件数を出す。次に開くまで持つ）
        self.skipped: list[str] = []
        # 様式のフォルダは裏のスレッドで読み、読み終わってから開く。親を付け、画面のスレッドで
        # 墨消しの画面と一緒に消す
        self.forms_loader = FormsLoader(self)
        self.forms_loader.done.connect(
            self.forms_read, Qt.ConnectionType.QueuedConnection
        )
        self.forms_reading: formstore.FolderChoice | None = None  # 読んでいるフォルダ
        # 開くファイルとフォルダ（様式を読み終わってから開き、開き終わるまで持つ）
        self.opening: OpenRequest | None = None
        self.opener: FileOpener | None = None  # ファイルを開いている途中の処理
        self.ready = (
            self.session.ready
        )  # 起動の準備（重いライブラリの読み込み）が終わった
        self.session.ready_changed.connect(self.preload_done)
        # 準備の途中で開かれ、準備の後に開くファイルとフォルダ
        self.pending: OpenRequest | None = None

        self.view = PageView()
        # 作業画像のキャッシュ（予算は、ファイルを開くときの空いているメモリで決める）。描き直しを頼んでいるページと、
        # 先に描いておくために頼んでいるページ（一度に1ページだけ頼む。表示しているページの描き直しを待たせないため）
        self.cache = workcache.WorkCache(workcache.MIN_BUDGET)
        self.drawing: set[int] = set()
        self.prefetching: int | None = None
        # 空いているメモリを見て予算を変え、少なければメモリが少ない状態にする（最後に見た時刻）
        self.watch = workcache.MemoryWatch(workcache.MIN_BUDGET)
        self.memory_checked = 0.0
        # ページの上の帯（削除したページ・向き・要確認の文）
        self.banner = widgets.Banner()
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        center_layout.addWidget(self.banner)
        center_layout.addWidget(self.view, 1)
        self.setCentralWidget(center)
        self.view.toggled.connect(self.toggle_box)
        self.view.added.connect(self.add_box)
        self.view.moved.connect(self.move_box)
        self.view.resized.connect(self.move_box)
        self.view.delete_requested.connect(self.delete_box)

        self.page_list = QTreeWidget()
        self.page_list.setHeaderHidden(True)
        self.page_list.setItemDelegate(widgets.LabelDelegate(self.page_list))
        self.page_list.currentItemChanged.connect(self.item_selected)
        self.page_list.installEventFilter(self)  # 上下の矢印キー（eventFilter）
        # ページの行の右クリック：ページの削除・削除の取り消し
        self.page_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.page_list.customContextMenuRequested.connect(self.page_list_menu)
        self.doc_items: list[QTreeWidgetItem] = []
        self.page_items: list[QTreeWidgetItem] = []
        dock = QDockWidget(tr("ファイルとページ"), self)
        dock.setWidget(self.page_list)
        dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

        self.status = widgets.LabelRow()  # 画面下：項目ごとのラベル
        self.statusBar().addWidget(self.status, 1)
        # 自動の確認で見分けるための名前（_build_actions のコメント）
        for widget, name in (
            (self, "main_window"),
            (self.banner, "banner"),
            (self.view, "page_view"),
            (self.page_list, "page_list"),
            (dock, "pages_dock"),
            (self.status, "status_labels"),
        ):
            widget.setObjectName(name)
        self.preload_error = ""  # 起動の準備ができなかった理由
        self._build_actions()
        self.addToolBarBreak()
        self._build_legend()
        self.update_state()
        self.view.show_page(None, tr(EMPTY_TEXT))
        if self.own_session:  # 画面が描かれてから、裏で準備を始める
            QTimer.singleShot(0, self.session.start)
        elif self.session.error:  # 起動の画面で、準備に失敗していた
            QTimer.singleShot(0, lambda: self.preload_done(self.session.error))

    # 起動の準備

    def preload_done(self, error: str):
        if error:
            self.preload_error = error
            self.update_state()
            QMessageBox.critical(
                self,
                tr(APP_NAME),
                tr(
                    "{failed}。\n{error}", failed=tr(uitext.PRELOAD_FAILED), error=error
                ),
            )
            return
        self.ready = True
        pending, self.pending = self.pending, None
        if pending is not None:
            self.start_open(pending)
        else:
            self.update_state()

    def _build_actions(self):
        """ツールバー（記号と文字。よく知られた操作は記号だけ）と、右端の主なボタン"""
        bar = QToolBar(tr("操作"))
        bar.setMovable(False)
        self.addToolBar(bar)
        self.tool_bar = bar
        # 記号を付けるボタン：(操作, 記号の名前, 記号だけにするか)
        self.tool_icons: list[tuple[QAction, str, bool]] = []

        def act(text, slot, shortcut=None, icon=None, icon_only=False, tip=None):
            a = QAction(tr(text), self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcuts(shortcut if isinstance(shortcut, list) else [shortcut])
            if tip:
                a.setToolTip(tr(tip))
            bar.addAction(a)
            if icon:
                self.tool_icons.append((a, icon, icon_only))
            return a

        # ボタンの名前は「…」を付けない
        self.act_open = act(
            uitext.OPEN_FILES, self.open_dialog, QKeySequence.StandardKey.Open, "open"
        )
        self.act_open_folder = act(
            uitext.OPEN_FOLDER, self.open_folder_dialog, icon="folder"
        )
        bar.addSeparator()
        self.act_prev = act(
            "前のページ", lambda: self.step(-1), [QKeySequence("PgUp")], "prev"
        )
        self.act_next = act(
            "次のページ", lambda: self.step(1), [QKeySequence("PgDown")], "next"
        )
        bar.addSeparator()
        self.act_left = act(
            "左に90度回す", lambda: self.rotate(1), icon="rotate_left", icon_only=True
        )
        self.act_right = act(
            "右に90度回す", lambda: self.rotate(-1), icon="rotate_right", icon_only=True
        )
        # 削除したページは墨消しPDFに入れない。表示しているページが削除したページなら、
        # 名前が「削除を取り消す」に変わる（update_state）
        self.act_delete_page = act(
            PAGE_DELETE, lambda: self.toggle_page_deleted(), icon="delete"
        )
        bar.addSeparator()
        self.act_undo = self.undo.createUndoAction(self, tr("元に戻す"))
        self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.act_redo = self.undo.createRedoAction(self, tr("やり直す"))
        self.act_redo.setShortcuts(
            [QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")]
        )
        for a, name, tip in (
            (self.act_undo, "undo", "元に戻す（Ctrl+Z）"),
            (self.act_redo, "redo", "やり直す（Ctrl+Y）"),
        ):
            bar.addAction(a)
            a.setToolTip(tr(tip))
            self.tool_icons.append((a, name, True))
        # 元に戻す履歴は、戻せるかどうかでボタンを自分で押せるようにする。出力中は押せないままにするため、
        # 履歴が変わったときにも押せるかどうかを決め直す（この接続は、履歴の接続の後に動く）
        self.undo.canUndoChanged.connect(self._sync_undo_actions)
        self.undo.canRedoChanged.connect(self._sync_undo_actions)
        bar.addSeparator()
        # 「全体を表示」「拡大」「縮小」は、範囲を描く画面・様式の編集の画面と同じもの
        zooms = pageview.zoom_actions(self.view, self)
        for a, name in zooms:
            bar.addAction(a)
            self.tool_icons.append((a, name, True))
        # 主なボタン（右端）。キー（Ctrl+S）と押せるかどうかは、操作（act_export）で持つ
        self.act_export = QAction(tr(reviewtext.EXPORT_LABEL), self)
        self.act_export.setShortcuts([QKeySequence("Ctrl+S")])
        self.act_export.triggered.connect(self.export_flow.export_dialog)
        self.addAction(self.act_export)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)
        self.export_button = widgets.make_primary(
            QPushButton(tr(reviewtext.EXPORT_LABEL))
        )
        self.export_button.clicked.connect(self.act_export.trigger)
        bar.addWidget(self.export_button)
        # 自動の確認で見分けるための、言語によらない名前（起動の画面と同じ決まり。launcher.py）。ツールバーの操作は、
        # ツールバーが操作ごとに作るボタンにも付ける（UI オートメーションで見えるのはボタンのほう）
        named = [
            (self.act_open, "open_files"),
            (self.act_open_folder, "open_folder"),
            (self.act_prev, "prev_page"),
            (self.act_next, "next_page"),
            (self.act_left, "rotate_left"),
            (self.act_right, "rotate_right"),
            (self.act_delete_page, "delete_page"),
            (self.act_undo, "undo"),
            (self.act_redo, "redo"),
            *zooms,
        ]
        for action, name in named:
            action.setObjectName(name)
            bar.widgetForAction(action).setObjectName(name)
        bar.setObjectName("tool_bar")
        self.act_export.setObjectName("export")
        self.export_button.setObjectName("export")
        self._apply_icons()

    def _apply_icons(self):
        """ツールバーの記号を描く。記号の書体がなければ、すべて文字だけにする"""
        widgets.apply_tool_icons(self.tool_bar, self.tool_icons, self.palette())

    def _build_legend(self):
        """凡例（色見本付き）と「操作の説明」のボタン"""
        bar = QToolBar(tr("凡例"))
        bar.setMovable(False)
        for color, dashed, text in LEGEND:
            bar.addWidget(widgets.Swatch(color, dashed))
            label = QLabel(tr(text))
            label.setContentsMargins(4, 0, 16, 0)
            bar.addWidget(label)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)
        self.help_button = QPushButton(tr("操作の説明"))
        self.help_button.setObjectName("help")
        self.help_button.clicked.connect(self.show_help)
        bar.addWidget(self.help_button)
        bar.setObjectName("legend_bar")
        self.addToolBar(bar)
        self.legend_bar = bar

    def show_help(self):
        QMessageBox.information(self, tr(APP_NAME), reviewtext.help_text())

    def changeEvent(self, event):
        """Windows のライトとダークが変わったら、記号を描き直す（ラベルの色は、ラベルが自分で直す）"""
        if event.type() in (
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        ) and hasattr(self, "tool_icons"):
            self._apply_icons()
        super().changeEvent(event)

    def engine_label(self) -> str:
        """作業ログの「OCR」の列に書く名前"""
        return self.engine.label

    # ファイルを開く

    def open_dialog(self):
        if not self.confirm_discard():
            return
        start = remembered_folder(OPEN_FOLDER_KEY)
        with widgets.dialog():
            names, _ = QFileDialog.getOpenFileNames(
                self, tr("開く"), start, filetypes.file_filter()
            )
        if names:
            self.open_paths([Path(n) for n in names])

    def open_folder_dialog(self):
        if not self.confirm_discard():
            return
        start = remembered_folder(OPEN_FOLDER_KEY)
        with widgets.dialog():
            name = QFileDialog.getExistingDirectory(self, tr(uitext.OPEN_FOLDER), start)
        if name:
            self.open_paths([Path(name)])

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [
            Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()
        ]
        if paths and not self.exporting and self.confirm_discard():
            self.open_paths(paths)

    def open_paths(self, paths: list[Path]):
        """ファイルとフォルダを開く。開けないファイルは除き、理由を示す

        開いた場所（ファイルならそのフォルダ、フォルダならその上のフォルダ）を覚え、次に「開く」
        「フォルダを開く」の画面を出すときに最初に見せる。選んだフォルダにサブフォルダがあれば、どの深さまで開くかを
        選ぶダイアログを出す（やめたら開かない）。
        """
        if paths:
            settings().setValue(OPEN_FOLDER_KEY, str(paths[0].parent))
        selection = folders.Selection(paths)
        depth: int | None = 0
        if selection.has_subfolders:
            accepted, depth = depthdialog.choose_depth(self, selection)
            if not accepted:
                self.update_state()
                return
        self.start_open(OpenRequest(selection, depth))

    def start_open(self, request: OpenRequest):
        """選んだファイルとフォルダを開き始める

        起動の準備が終わっていなければ、準備が終わってから開く（準備のあいだにまた開いたときは、後で選んだファイルを
        開く）。様式のフォルダを裏のスレッドで読み、読み終わってから開く（forms_read）。読んでいるあいだや、開いて
        いる途中にまた開いたときも、前に選んだファイルの代わりに、後で選んだファイルを開く。
        """
        if not self.ready:
            self.pending = request
            self.update_state()
            return
        self.opening = request
        if self.forms_reading is None:
            self.read_forms()
        self.update_state()

    def read_forms(self):
        self.forms_reading = forms_choice()
        self.forms_loader.start(self.forms_reading, forms_off())

    def forms_read(self, choice: formstore.FolderChoice, loaded: formstore.Loaded):
        """様式のフォルダを読み終わったとき。選んだファイルを開く

        様式のフォルダが決まっているのに使う様式を読めなければ、開かない。開いていた
        ファイルはそのまま残る。読んでいるあいだに様式のフォルダや、この端末で使う様式が変わっていれば、
        読み直す。
        """
        self.forms_reading = None
        request = self.opening
        if request is None:  # 画面を閉じた
            return
        if choice != forms_choice() or loaded.off != forms_off():
            self.read_forms()
            return
        if text := loaded.blocking_text():
            self.opening = None
            QMessageBox.warning(self, tr(APP_NAME), text)
            self.update_state()
            return
        self.open_with_forms(request, loaded)

    def open_with_forms(self, request: OpenRequest, forms: formstore.Loaded):
        """裏のスレッドでファイルを開く（files_opened）。開いているあいだ、進み具合と「やめる」のボタンを出す（すぐに
        開き終われば出さない）。開いている途中のほかのファイルは、開くのをやめる"""
        if self.opener is not None:
            self.opener.cancel()
        opener = FileOpener(self)
        self.opener = opener
        dialog = QProgressDialog(tr(reviewtext.OPENING), tr("やめる"), 0, 0, self)
        dialog.setWindowTitle(tr(APP_NAME))
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(OPEN_PROGRESS_MS)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.canceled.connect(opener.cancel)
        opener.progress.connect(
            lambda count, total: open_progress(dialog, count, total),
            Qt.ConnectionType.QueuedConnection,
        )
        opener.done.connect(
            lambda opened: self.files_opened(opener, dialog, forms, opened),
            Qt.ConnectionType.QueuedConnection,
        )
        opener.start(request.selection, request.depth, self.engine, self.engine_label())
        self.update_state()

    def files_opened(
        self,
        opener: FileOpener,
        dialog: QProgressDialog,
        forms: formstore.Loaded,
        opened: review.Opened | None,
    ):
        """裏のスレッドでファイルを開き終わったとき。やめたとき（opened が None）は何も開かず、開いていたファイルを
        そのまま残す"""
        dialog.canceled.disconnect()
        dialog.reset()
        dialog.close()
        dialog.deleteLater()
        if opener is not self.opener:  # 後で選んだファイルを開き始めたか、画面を閉じた
            return
        self.opener = None
        self.opening = None
        if opened is None:
            self.update_state()
            return
        docs, failed = opened.docs, opened.failed
        if failed:
            QMessageBox.warning(
                self,
                tr(APP_NAME),
                review.open_failure_text(failed, bool(docs), opened.skipped),
            )
        if not docs:
            if not failed:
                QMessageBox.warning(
                    self, tr(APP_NAME), review.no_files_text(opened.skipped)
                )
            self.update_state()
            return
        self.skipped = opened.skipped
        self.analyzer.clear()
        self.token = object()
        self.forms = forms
        drop_documents(self.docs)  # 前に開いていたファイルの作業画像を、すぐに放す
        self.cache.clear()
        self.cache.budget = workcache.budget_for(available_memory())
        self.watch = workcache.MemoryWatch(self.cache.budget)
        self.drawing, self.prefetching = set(), None
        self.docs = docs
        self.pages = [st for d in docs for st in d.pages]
        self.undo.clear()
        self._build_list()
        self.current = -1
        self.start_analysis(
            [
                AnalysisJob(i, st.source, None, st.doc.engine, forms.active)
                for i, st in enumerate(self.pages)
            ]
        )
        self.goto_page(0)

    def _build_list(self):
        self.page_list.blockSignals(True)
        self.page_list.clear()
        self.doc_items, self.page_items = [], []
        for d in self.docs:
            top = QTreeWidgetItem(self.page_list)
            top.setToolTip(0, str(d.path))
            self.doc_items.append(top)
            for _ in d.pages:
                child = QTreeWidgetItem(top)
                child.setData(0, PAGE_ROLE, len(self.page_items))
                self.page_items.append(child)
            top.setExpanded(True)
        self.page_list.blockSignals(False)

    def start_analysis(self, jobs: list[AnalysisJob]):
        self.analyzer.submit(self.token, jobs)

    def page_analyzed(self, token, index, result, error):
        if token != self.token:
            return  # 前に開いていたファイルの結果
        st = self.pages[index]
        if result is not None and self.forms is not None:
            result = result.sharing(self.forms.active)
        if result is not None and result.work is not None:
            # 作業画像はキャッシュに持ち、解析の結果には高さ・幅だけを残す（予算を超えれば手放す）
            self.cache.drop(index)
            self.cache.put(index, result.work, self.deleted_flags())
            result = result.without_work()
        st.result, st.error = result, error
        self.check_memory()
        if index == self.current:
            self.show_current()
        self.prefetch()
        self.update_state()

    def check_memory(self) -> None:
        """空いているメモリを見て（最大で1秒に1回）、キャッシュの予算を変える。メモリが少ない状態なら、予算を最小に
        し、解析のプロセスを1つにする。画面では知らせない"""
        now = time.monotonic()
        if now - self.memory_checked < workcache.CHECK_SECONDS:
            return
        self.memory_checked = now
        budget = self.watch.update(available_memory(), self.cache.size)
        if budget != self.cache.budget:
            self.cache.budget = budget
            self.cache.trim(self.deleted_flags())
        if self.watch.low:
            self.analyzer.limit_to_one()

    # 作業画像のキャッシュと描き直し

    def deleted_flags(self) -> list[bool]:
        return [st.deleted for st in self.pages]

    def work_size(self, index: int) -> int | None:
        """ページの作業画像の大きさ（バイト）。解析が終わっていなければ None"""
        res = self.pages[index].result
        if res is None:
            return None
        h, w = res.work_shape
        return h * w

    def request_work(self, index: int) -> None:
        """index のページの作業画像を、解析のプロセスに描き直すよう頼む（解析の依頼より先に動く）。元のファイルが
        開いた後に変わっていれば頼まない（表示しているページなら理由を出す）"""
        st = self.pages[index]
        if index in self.drawing or st.result is None:
            return
        from . import (
            export,
        )  # 重いライブラリを読み込むので、使うときに読み込む（開いた後は読み込み済み）

        try:
            export.check_unchanged(st.doc.path, st.doc.stamp)
        except export.ExportError as e:
            if index == self.current:
                self.view.show_page(None, str(e))
            return
        self.drawing.add(index)
        token, res = self.token, st.result
        self.analyzer.call(
            analysis.redraw,
            st.source,
            res.quarter_turns,
            res.skew,
            callback=lambda image, error: self.work_drawn(
                token, index, res, image, error
            ),
        )

    def work_drawn(self, token, index: int, res, image, error) -> None:
        if token != self.token:
            return  # 前に開いていたファイルのページ
        self.drawing.discard(index)
        if self.prefetching == index:
            self.prefetching = None
        st = self.pages[index]
        if st.result is None or (st.result.quarter_turns, st.result.skew) != (
            res.quarter_turns,
            res.skew,
        ):
            pass  # 描いているあいだに回した（新しい解析の結果を待つ）
        elif error is not None:
            if index == self.current:
                self.view.show_page(
                    None, tr(uitext.NOT_DRAWN, error=f"{type(error).__name__}: {error}")
                )
        elif image.shape[:2] == tuple(st.result.work_shape):
            self.cache.put(index, image, self.deleted_flags())
            if index == self.current:
                self.show_current()
        self.prefetch()

    def prefetch(self) -> None:
        """表示しているページの10ページ先までのうち、予算に収まるところまでのページで、キャッシュにないものを、
        近い順に1ページずつ描き直すよう頼む"""
        if self.prefetching is not None or not self.pages or self.current < 0:
            return
        for index in self.cache.wanted(self.deleted_flags(), self.work_size):
            if index not in self.drawing:
                self.prefetching = index
                self.request_work(index)
                if index not in self.drawing:  # 頼めなかった（元のファイルが変わった）
                    self.prefetching = None
                    continue
                return

    # ページの移動と表示

    def item_selected(self, item, _previous):
        if item is None:
            return
        index = item.data(0, PAGE_ROLE)
        if index is None:  # ファイルの行 → そのファイルの1ページ目
            index = item.child(0).data(0, PAGE_ROLE)
        self.goto_page(index)

    def eventFilter(self, obj, event):
        if obj is self.page_list and event.type() == QEvent.Type.KeyPress:
            # 一覧で上下の矢印キーを押したときは、ファイルの行を飛ばして、ページを1つずつ移る。一覧に任せると、
            # ファイルの1ページ目で上を押したとき、ファイルの行に移ってから同じページに戻り、前のファイルに
            # 戻れないため
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                self.step(-1 if event.key() == Qt.Key.Key_Up else 1)
                return True
            # 一覧で Delete キーを押したときは、選んでいるページを削除する。
            # ページの表示では受け付けない（手で追加した範囲の削除と取り違えないため）
            if event.key() == Qt.Key.Key_Delete:
                self.delete_selected_page()
                return True
        return super().eventFilter(obj, event)

    def step(self, delta):
        if self.pages:
            self.goto_page(max(0, min(len(self.pages) - 1, self.current + delta)))

    def goto_page(self, index):
        if 0 <= index < len(self.pages):
            self.current = index
            self.cache.show(index)
            self.check_memory()
            self.cache.trim(self.deleted_flags())
            st = self.pages[index]
            # まだ解析していないページは、そこから先に解析する（削除したページは、解析を待たない）
            if not st.ready and not st.error and not st.deleted:
                self.analyzer.prioritize(self.token, index)
            self.show_current()
            self.prefetch()
            self.update_state()

    def show_current(self):
        st = self.pages[self.current]
        if st.error:
            hint = tr(
                "削除したので、このファイルのほかのページを出力できます。"
                if st.deleted
                else "このページを削除すると、このファイルのほかのページを出力できます。"
            )
            self.view.show_page(
                None,
                tr(
                    "このページを処理できませんでした。\n{error}\n\n{hint}",
                    error=st.error,
                    hint=hint,
                ),
            )
        elif st.result is None:
            self.view.show_page(None, tr(uitext.ANALYZING))
        elif (work := self.cache.get(self.current)) is None:
            # 手放したページは、描き直してから表示する
            self.view.show_page(None, tr(uitext.PREPARING))
            self.request_work(self.current)
        else:
            st.viewed = True
            self.view.show_page(st, work=work)
        # 削除したページ・向き・要確認の文は、ページの上の帯に出す（削除したページなら、画像の有無（解析中・
        # 処理できない）に関係なく出す）
        self.banner.set_messages(
            [(text, widgets.Tone(t)) for text, t in reviewtext.page_messages(st)]
        )
        item = self.page_items[self.current]
        if self.page_list.currentItem() is not item:
            self.page_list.blockSignals(True)
            self.page_list.setCurrentItem(item)
            self.page_list.blockSignals(False)
        self.setWindowTitle(f"{st.doc.path.name} - {tr(APP_NAME)}")

    def boxes_changed(self, page):
        """範囲が変わったとき。変わったページを表示し、そのファイルを未出力に戻す"""
        self.pages[page].doc.exported = False
        if page != self.current:
            self.goto_page(page)
        else:
            self.view.refresh_boxes()
            self.update_state()

    # 範囲の操作。出力のあいだは受け付けない（出力の中身と画面が食い違わないように。
    # 出力の進み具合の画面も操作を止めるが、その仕組みに頼らない）。
    # 削除したページでも受け付けない（表示の側でも操作を始めない）

    def editing_locked(self) -> bool:
        """今のファイルを編集する操作を受け付けない（出力のあいだと、ファイルを開くために様式のフォルダを読んで
        いるあいだ。reviewtext.editing_locked）"""
        return reviewtext.editing_locked(self.exporting, self.opening)

    def boxes_locked(self) -> bool:
        return self.editing_locked() or self.pages[self.current].deleted

    def toggle_box(self, box):
        if self.boxes_locked():
            return
        self.undo.push(ToggleCommand(self, self.current, box))

    def add_box(self, rect):
        if self.boxes_locked():
            return
        box = boxes.Box(*rect, kind=boxes.KIND_MANUAL, source=boxes.Source.MANUAL)
        page_boxes = self.pages[self.current].result.boxes
        self.undo.push(AddCommand(self, self.current, page_boxes, box))

    def move_box(self, box, old, new):
        if self.boxes_locked():
            return
        self.undo.push(GeometryCommand(self, self.current, box, old, new))

    def delete_box(self, box):
        if self.boxes_locked():
            return
        page_boxes = self.pages[self.current].result.boxes
        self.undo.push(DeleteCommand(self, self.current, page_boxes, box))

    def still(self, token, st: PageState) -> bool:
        """確認のメッセージを閉じた後も、確認の前と同じファイルを開いていて、そのページを編集できる
        （確認のメッセージを出しているあいだに開き直していたら、確認の後の操作を別のファイルに効かせない）"""
        return self.token is token and st in self.pages and not self.editing_locked()

    def rotate(self, delta):
        st = self.pages[self.current] if self.pages else None
        if st is None or st.result is None or st.deleted or self.editing_locked():
            return
        if review.is_edited(st.result.boxes):
            token = self.token
            if not widgets.ask(
                self,
                tr(APP_NAME),
                tr(
                    "このページで解除・追加した内容は消え、墨消しする欄を決め直します。回しますか？"
                ),
            ):
                return
            if not self.still(token, st) or st.result is None or st.deleted:
                return
        turns = (st.result.quarter_turns + delta) % 4
        st.result, st.error, st.viewed = None, "", False
        self.cache.drop(self.current)
        st.doc.exported = False
        self.undo.clear()  # 回したページの範囲は作り直すため、元に戻す履歴も消す
        self.show_current()
        self.start_analysis(
            [
                AnalysisJob(
                    self.pages.index(st),
                    st.source,
                    turns,
                    st.doc.engine,
                    self.forms.active,
                )
            ]
        )
        self.update_state()

    # ページの削除。元のファイルは変えず、墨消しPDFに入れない

    def toggle_page_deleted(self, index: int | None = None):
        """表示しているページ（index を渡せばそのページ）を削除する。削除したページなら、削除を取り消す

        削除するときは毎回確かめる（既定のボタンは「いいえ」）。取り消すときは確かめない。
        出力のあいだは受け付けない。
        """
        if self.editing_locked() or not self.pages:
            return
        index = self.current if index is None else index
        st = self.pages[index]
        if not st.deleted:
            token = self.token
            if not widgets.ask(
                self,
                tr(APP_NAME),
                tr(
                    "{file} の {number}ページを削除しますか？\n"
                    "削除したページは、墨消しPDFに入れません（削除は取り消せます）。",
                    file=st.doc.path.name,
                    number=st.number + 1,
                ),
            ):
                return
            if not self.still(token, st) or st.deleted:
                return
            index = self.pages.index(st)
        self.undo.push(PageDeleteCommand(self, index, not st.deleted))

    def delete_selected_page(self):
        """一覧で Delete キーを押したとき：選んでいるページの行のページを削除する（確認のメッセージは毎回出す）

        ファイルの行と、削除したページでは何もしない。同じキーで削除を取り消すと、押し間違えたときに
        気づきにくいため。出力のあいだは受け付けない（toggle_page_deleted）。
        """
        item = self.page_list.currentItem()
        index = item.data(0, PAGE_ROLE) if item is not None else None
        if index is None or self.pages[index].deleted:
            return
        self.toggle_page_deleted(index)

    def page_deleted_changed(self, page):
        """ページを削除したとき・取り消したとき。そのページを表示し、そのファイルを未出力に戻す"""
        self.pages[page].doc.exported = False
        if page != self.current:
            self.goto_page(page)
        else:
            self.show_current()
            self.update_state()

    def page_menu(self, index: int) -> QMenu:
        """一覧のページの行の右クリックのメニュー"""
        menu = QMenu(self)
        st, token = self.pages[index], self.token
        deleted = st.deleted
        # 削除の項目には、一覧で Delete キーでも削除できることを右端に示す（タブの後ろの文は、キーの
        # 表示として右端に出る。キーの割り当てはしない）
        action = menu.addAction(
            tr(PAGE_UNDELETE) if deleted else f"{tr(PAGE_DELETE)}\tDelete"
        )
        # 押したときに、メニューを出したときのファイルのページかを確かめる
        action.triggered.connect(
            lambda: (
                self.still(token, st) and self.toggle_page_deleted(self.pages.index(st))
            )
        )
        action.setEnabled(not self.editing_locked())
        return menu

    def page_list_menu(self, pos):
        item = self.page_list.itemAt(pos)
        index = item.data(0, PAGE_ROLE) if item is not None else None
        if index is None:  # ファイルの行や、何もない所
            return
        self.page_menu(index).exec(self.page_list.viewport().mapToGlobal(pos))

    # 出力

    def export_targets(self) -> list[Document]:
        """出力できるファイル：開いたファイルの全ページの解析が終わったときの、まだ出力していないもの"""
        return review.export_targets(self.docs)

    @property
    def exporting(self) -> bool:
        """出力している（出力の流れ export_flow が持つ値。読むだけ）"""
        return self.export_flow.exporting

    # 状態の表示

    def can_export(self) -> bool:
        """出力できる（reviewtext.can_export）"""
        return reviewtext.can_export(self.docs, self.exporting, self.opening)

    def update_state(self):
        for d, top in zip(self.docs, self.doc_items):
            set_row(top, reviewtext.doc_parts(d))
        for st, item in zip(self.pages, self.page_items):
            set_row(item, reviewtext.page_parts(st))
            font = item.font(0)
            font.setBold(st.ready and not st.viewed)
            item.setFont(0, font)
        a = reviewtext.actions(
            self.docs, self.pages, self.current, self.exporting, self.opening
        )
        self.act_prev.setEnabled(a.prev)
        self.act_next.setEnabled(a.next)
        self.act_left.setEnabled(a.turn)
        self.act_right.setEnabled(a.turn)
        self.act_delete_page.setText(
            tr(PAGE_UNDELETE if a.page_deleted else PAGE_DELETE)
        )
        self.act_delete_page.setToolTip(
            tr(PAGE_UNDELETE if a.page_deleted else PAGE_DELETE_TIP)
        )
        self.act_delete_page.setEnabled(a.delete_page)
        self.act_open.setEnabled(a.open)
        self.act_open_folder.setEnabled(a.open)
        self._sync_undo_actions()
        self.act_export.setText(reviewtext.export_text(a.export_count))
        self.act_export.setEnabled(a.export)
        self.export_button.setText(self.act_export.text())
        self.export_button.setEnabled(self.act_export.isEnabled())
        cur = self.pages[self.current] if self.pages and self.current >= 0 else None
        parts = reviewtext.status_parts(
            self.docs, self.pages, cur, self.forms, self.skipped
        )
        waits = reviewtext.wait_parts(
            self.preload_error,
            self.ready,
            self.pending,
            self.opening,
            self.opener is not None,
        )
        self.status.set_parts(
            [(t, widgets.Tone(k), tip) for t, k, tip in waits + parts]
        )

    def _sync_undo_actions(self, *_):
        """「元に戻す」「やり直す」は、戻せるときだけ押せる。今のファイルを編集できないあいだ（editing_locked）は押せない"""
        locked = self.editing_locked()
        self.act_undo.setEnabled(self.undo.canUndo() and not locked)
        self.act_redo.setEnabled(self.undo.canRedo() and not locked)

    # 終了

    def confirm_discard(self) -> bool:
        left = sum(not d.exported for d in self.docs)
        if not left:
            return True
        return widgets.ask(
            self,
            tr(APP_NAME),
            tr(
                "墨消しPDFをまだ出力していないファイルが{count}件あります。作業を破棄しますか？",
                count=left,
            ),
        )

    def closeEvent(self, event):
        """閉じる（起動の画面に戻る）。待っている解析の依頼を取り消し、解析中のページの結果は捨てる"""
        if self.exporting or not self.confirm_discard():
            event.ignore()
            return
        self.opening = None  # 様式のフォルダを読み終わっても開かない
        if self.opener is not None:  # 開いている途中なら、やめる
            self.opener.cancel()
            self.opener = None
        self.analyzer.clear()
        self.token = object()
        self.analyzer.page_done.disconnect(self.page_analyzed)
        self.session.ready_changed.disconnect(self.preload_done)
        if self.own_session:
            self.session.shutdown()
        # 開いたファイルとページの画像を手放す（画面は WA_DeleteOnClose で消える）
        self.undo.clear()
        self.view.show_page(None)
        drop_documents(self.docs)
        self.cache.clear()
        self.docs, self.pages = [], []
        event.accept()
        self.closed.emit()


@dataclass(frozen=True)
class OpenRequest:
    """開くファイルとフォルダと、フォルダを開く深さ（None はすべて）"""

    selection: folders.Selection
    depth: int | None


def open_progress(dialog: QProgressDialog, count: int, total: int) -> None:
    """ファイルを開く途中の進み具合を出す（workers.FileOpener.progress）"""
    dialog.setLabelText(reviewtext.opening_text(count, total))
    if total >= 0:
        dialog.setMaximum(total)
        dialog.setValue(count)


def available_memory() -> int:
    """空いている物理メモリ（バイト。ページファイルは含めない）"""
    import psutil

    return psutil.virtual_memory().available


def drop_documents(docs: list[Document]) -> None:
    """開いていたファイルからページへの参照を断つ

    ファイルとページはお互いを参照し合う（Document.pages と PageState.doc）。断たないと、手放したファイルとページが
    循環参照のまま回収されずに残り、作業画像（1ページ約4.4MB）が、古い世代までのガベージコレクションが動くまで
    放されない（ガベージコレクションを画面のスレッドで行う設定（guigc.py）で、断たずに200ページを開き直すと、画面の
    プロセスのメモリの使用量が約340MB増えた）。
    """
    for doc in docs:
        doc.pages = []


def set_row(item: QTreeWidgetItem, parts: tuple[str, list[tuple[str, str]]]) -> None:
    """一覧の行に、文とラベルを入れる（widgets.LabelDelegate が描く）。表示の文は、それをつないだもの"""
    text, labels = parts
    item.setText(0, reviewtext.joined(parts))
    item.setData(0, widgets.LabelDelegate.MAIN_ROLE, text)
    item.setData(0, widgets.LabelDelegate.LABELS_ROLE, labels)
