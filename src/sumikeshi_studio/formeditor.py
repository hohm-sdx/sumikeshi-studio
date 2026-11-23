# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""範囲を描く画面と、ページから様式を登録する流れ

型の画像を作る処理・保存の前に確かめる処理・題名を読む処理は解析のプロセス（workers.Analyzer.call）で、様式の
フォルダの読み書きは裏のスレッド（workers.Background）で行い、画面を止めない。共有フォルダにつながらないと、
読み書きに数十秒かかることがあるため。処理のあいだは、画面を閉じる操作を受け付けない。

起動を遅くしないよう、このモジュールは、起動の画面が様式の画面（formswin.py）を開くときに読み込む。様式の一覧は
formlist.py に置く。
"""

import dataclasses
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QUndoStack
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import analysis, formsave, formstext, formstore, uitext, widgets
from .boxes import Box, Source
from .commands import AddCommand, DeleteCommand, GeometryCommand
from .formstext import (
    EXCLUDES_HEADING,
    WORDS_HEADING,
    done_with_warnings,
    unclear_text,
)
from .formstore import Field, FormSpec, Loaded
from .i18n import tr
from .pageview import PageView, zoom_bar
from .prefs import forms_choice, remember_geometry, restore_geometry
from .widgets import Tone

TITLE = "様式"
EDITOR_GEOMETRY_KEY = "windows/form_editor"  # 範囲を描く画面の大きさと最大化の状態
NOTE = (
    "墨消しする範囲と、見本から消す範囲の外は、見本の画像として、そのまま様式のフォルダに保存されます。"
    "記入のない帳票のスキャンから登録してください。"
)


SIDE_WIDTH = 420  # 右の欄の幅
QWIDGETSIZE_MAX = 16777215  # 部品の大きさの上限をなくす値（Qt の QWIDGETSIZE_MAX）
# 右の欄がつぶれすぎないようにする、範囲の表の行の数と、題名の語の入力欄の行の数（これより低いウィンドウでは、右の欄を
# スクロールする）
TABLE_ROWS_MIN = 5
WORDS_LINES_MIN = 2


class SideScroll(QScrollArea):
    """右の欄を、縦にだけスクロールできるようにする枠

    中身は枠の高さいっぱいに広げ、枠が中身の要る高さ（needed）より低いときだけ、縦のスクロールバーを出す。QScrollArea の
    widgetResizable を使わないのは、折り返す文のある中身では、中身の高さを入力欄を広げた高さ（heightForWidth）にして、
    枠が十分に高くてもスクロールになるため。スクロールバーの分の幅は、出ないときも空けておく（出たり消えたりで中身の
    幅が変わると、文の折り返しが変わるため）。
    """

    def __init__(self, inner: QWidget, needed: int):
        super().__init__()
        self.needed = needed
        inner.setFixedWidth(SIDE_WIDTH)
        self.setWidget(inner)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        bar = self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        self.setFixedWidth(SIDE_WIDTH + bar)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.widget().setFixedHeight(max(self.viewport().height(), self.needed))


class LimitSlider(QSlider):
    """下限のスライダー。マウスのホイールでは動かさない（右の欄をホイールでスクロールしているときに、ポインターが
    スライダーの上を通っても、下限を変えないため。ホイールの操作は、右の欄のスクロールになる）"""

    def wheelEvent(self, event):
        event.ignore()


class FrameChoice(QComboBox):
    """範囲の表の「枠の線」の列の選択。マウスのホイールでは変えない（右の欄や表をホイールでスクロールしているときに、
    ポインターがこの列の上を通っても、枠の線の設定を変えないため。クリックとキーボードでは変えられる）"""

    def wheelEvent(self, event):
        event.ignore()


def ask(parent, text: str) -> bool:
    """「はい」「いいえ」で確かめる。既定のボタンは「いいえ」"""
    return widgets.ask(parent, tr(TITLE), text)


def overwrite_ok(parent, spec: FormSpec | None, saved: formsave.Saved | None) -> bool:
    """保存の直前に読み直した版（saved）が、編集を始めたときの版と違えば、上書きしてよいかを確かめる"""
    question = formstext.overwrite_question(spec, saved)
    return question is None or ask(parent, question)


# ---------------------------------------------------------------- 様式の編集の画面


# 範囲の表の「枠の線」の選び方
FRAME_CHOICES = [
    ("上下・左右", (formstore.TOP_BOTTOM, formstore.LEFT_RIGHT)),
    ("上下", (formstore.TOP_BOTTOM,)),
    ("左右", (formstore.LEFT_RIGHT,)),
    ("なし", ()),
]
FRAME_NOTE = (
    "枠の線：印刷された枠の線に合わせて墨消しする辺です。"
    "「なし」の範囲は、描いたとおりに墨消しされ、"
    "写真など、帳票が見本と違う大きさで写っているページでだけ「⚠ 要確認」が付きます。"
)
FIT_LABEL = "範囲を自動で枠の線に合わせる"
FIT_NOTE = (
    "チェックがあると、墨消しする範囲を描いたときと大きさを変えたときに、線が見つかった辺を線の中心に合わせます。"
    "動かしたときは合わせません。"
)
# 「この様式のバリエーションの判別」のまとまりの文
LINES_TITLE = "この様式のバリエーションの判別"
LINES_USE_NOTE = (
    "この様式と同じ帳票で、欄の追加などのカスタマイズによりレイアウトが少し違う用紙（この様式のバリエーション）を、"
    "罫線の並び方から判別します。"
)
LINES_CHECK = "この様式の判別に罫線の並び方を使う"
LINES_LIMIT = "罫線の一致度の下限"
LINES_RULES = (
    "・別の帳票がこの様式と判別されるときは、上げてください。",
    "・この様式の用紙が様式不明になるときは、下げてください。",
    "・上げても別の帳票が判別されるときは、上のチェックを外してください。",
)
# 下限のスライダーの刻みと範囲（刻みの数で持つ。0.10〜0.95）
LIMIT_STEP = formstore.LINE_MATCH_STEP
LIMIT_STEPS = (2, round(formstore.LINE_MATCH_MAX / formstore.LINE_MATCH_STEP))
# 「題名での判別」のまとまりの文
TITLE_USE = "見本に類似した様式が2つ以上あるページで、どの様式かを題名から判別するのに使います。"
TITLE_RULES = (
    "・行ごとに、別々の条件になります。どれか1行に当てはまれば十分です。",
    (
        "・1行の中をスペースで区切ると、区切った語がすべて題名にあるときだけ、その行に当てはまります。"
        "例：「令和8年分　扶養控除等」は、「令和8年分」と「扶養控除等」の両方がある題名にだけ当てはまります。"
    ),
)


class FormEditor(QDialog):
    """様式の編集の画面。登録（spec が None）・編集・型の画像の置き換えで使う

    base は型にする画像。base_is_work なら作業画像（登録・置き換え）、そうでなければ今の型の画像（編集）。
    範囲の座標は、どちらの場合も作業画像の上の値で扱う。
    """

    def __init__(
        self,
        session,
        *,
        operation: str,
        spec: FormSpec | None,
        base,
        base_is_work: bool,
        fields: list[Field],
        erase: list[Field],
        loaded: Loaded,
        notice: str = "",
        parent: QWidget | None = None,
        line_match: tuple[bool, float] | None = None,
        lines: tuple | None = None,
    ):
        super().__init__(parent)
        self.session = session  # 解析のプロセス・裏の処理・使う OCR（session.Session）
        self.operation = operation
        self.spec = spec
        self.form_id = spec.id if spec is not None else formsave.new_user_id()
        self.base, self.base_is_work = base, base_is_work
        self.lines = lines  # base から先に読んだ罫線（登録で、下限の初めの値を求めたときのもの）。なければ None
        self.loaded = loaded
        # (罫線の並びを判別に使うか, 罫線の一致度の下限)。編集と置き換えでは様式の値、登録では、使うほかの様式の
        # 見本の罫線と比べて決めた初めの値（line_match。register_from_page）
        if spec is not None:
            self.line_match = (spec.line_match_use, spec.line_match_min)
        else:
            self.line_match = line_match or (True, formstore.LINE_MATCH_MIN)
        self.folder: Path = loaded.folder
        self.busy = False
        self.saved: FormSpec | None = None
        self.png = None  # 確かめた後に保存する型の画像（PNG のデータ）
        # 「保存」を押した時点の様式。確認と保存の最後までこれを使う（途中で画面の値を読み直さない）
        self.pending: FormSpec | None = None
        self.save_warnings: list[str] = []  # 保存の後の処理でできなかったこと
        if spec is None:
            title = tr("様式を登録")
        elif operation == formsave.OP_REPLACE:
            # 「用紙を置き換える」で開いたことを題名に出す（変更の記録の値 OP_REPLACE は英語なので、画面の文は分ける）
            title = tr("様式の編集：{name}（新しい用紙への置き換え）", name=spec.name)
        else:
            title = tr("様式の編集：{name}", name=spec.name)
        self.setWindowTitle(title)
        self.resize(1400, 900)
        # 最大化のボタンを付ける。最小化のボタンは付けない（様式の編集の画面を操作できなくするウィンドウで、タスクバーに
        # 出ないため、最小化すると戻す場所が見つけにくい）
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        restore_geometry(self, EDITOR_GEOMETRY_KEY)
        self.undo = QUndoStack(self)

        if base_is_work:
            display = base
        else:  # 型の画像を作業画像の大きさで表示する（表示だけ。保存する画像は縮め直さない）
            factor = round(1 / formstore.TEMPLATE_SCALE)
            display = base.repeat(factor, 0).repeat(factor, 1)
        self.boxes = [
            Box(f.x, f.y, f.w, f.h, f.name, Source.MANUAL, frame=f.frame)
            for f in fields
        ]
        self.boxes += [Box(f.x, f.y, f.w, f.h, f.name, Source.ERASE) for f in erase]
        # 枠の線の記録のない範囲：様式の編集なら記録のない様式の扱い（formstore.frame_pairs）、登録・置き換えなら
        # 元のページで調べる。開いたときは、範囲の位置を変えない（置き換えの範囲は、新しいページに移すときに線の中心に
        # 合わせてある。formcheck.move_ranges）。範囲を描いたり動かしたりしたときも調べ直す（_check_frames）
        self.checked_rects: dict[int, tuple] = {}
        for b in self.boxes:
            if b.source == Source.ERASE:
                continue
            if b.frame is None:
                if base_is_work or spec is None:
                    _, b.frame = self.find_frame(b.rect())
                else:
                    b.frame = formstore.frame_pairs(Field(b.kind, *b.rect(), None))
            self.checked_rects[id(b)] = b.rect()
        self.state = SimpleNamespace(
            result=SimpleNamespace(work=display, boxes=self.boxes), deleted=False
        )
        # 部品を作る
        left = self._build_page_area()
        side = self._build_side(spec)
        self._build_frame(left, side, notice)
        self._build_undo_actions()
        self.view.show_page(self.state)
        self.fill_table()

    # 範囲の操作（元に戻す操作は commands.py のものを使う。win・page の代わりに、この画面と 0 を渡す）

    def _build_page_area(self) -> QVBoxLayout:
        """左の欄（ページの表示、描く範囲の選び方、操作の説明、拡大・縮小）を作り、その並びを返す"""
        self.view = PageView()
        self.view.edit_mode = True
        self.view.added.connect(self.add_box)
        self.view.moved.connect(self.move_box)
        self.view.resized.connect(self.resize_box)
        self.view.delete_requested.connect(self.delete_box)
        self.view.toggled.connect(self.select_box)

        self.draw_mask = QRadioButton(tr("墨消しする範囲を描く"))
        self.draw_erase = QRadioButton(tr("見本から消す範囲を描く"))
        self.draw_mask.setChecked(True)
        modes = QHBoxLayout()
        modes.addWidget(self.draw_mask)
        modes.addWidget(self.draw_erase)
        modes.addWidget(
            widgets.note(
                tr(
                    "何もない所をドラッグ：範囲を描く　角をドラッグ：大きさを変える　"
                    "中をドラッグ：動かす　右クリック：削除　右のボタンでドラッグ：スクロール　"
                    "Ctrl+Z：元に戻す"
                )
            )
        )
        modes.addStretch()
        modes.addWidget(zoom_bar(self.view, self))  # 全体を表示・拡大・縮小
        left = QVBoxLayout()
        left.addLayout(modes)
        left.addWidget(self.view, 1)
        return left

    def _build_side(self, spec: FormSpec | None) -> QWidget:
        """右の欄（様式の名前・題名での判別・バリエーションの判別・範囲の表）を作る。spec は編集する様式（登録なら
        None）。右の欄は、ウィンドウが低いときに縦にスクロールできる（SideScroll）"""
        self.name_edit = QLineEdit(spec.name if spec is not None else "")
        self.words_edit = QPlainTextEdit(
            "\n".join(spec.title_words) if spec is not None else ""
        )
        self.excludes_edit = QPlainTextEdit(
            "\n".join(spec.title_excludes) if spec is not None else ""
        )
        self.read_title_button = QPushButton(tr("題名を読む"))
        self.read_title_button.clicked.connect(self.read_title)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            [
                tr("範囲", context="表の見出し"),
                tr("名前（枠の説明に出る）"),
                tr("枠の線"),
            ]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self.rename)
        # 右の欄は、様式の名前・題名の語・範囲のまとまりに分ける
        name_box = QGroupBox(tr("様式の名前"))
        QVBoxLayout(name_box).addWidget(self.name_edit)
        # 題名の語と除く語
        words_box = QGroupBox(tr("題名での判別"))
        words = QVBoxLayout(words_box)
        words.addWidget(widgets.note(tr(TITLE_USE)))
        for heading, edit in (
            (WORDS_HEADING, self.words_edit),
            (EXCLUDES_HEADING, self.excludes_edit),
        ):
            label = QLabel(
                tr(heading)
            )  # 入力欄の見出しなので、補足の文字ではなく本文の文字にする
            label.setWordWrap(True)
            label.setBuddy(edit)
            words.addWidget(label)
            words.addWidget(edit, 1)
            if edit is self.words_edit:
                words.addWidget(self.read_title_button)
        for rule in TITLE_RULES:
            words.addWidget(widgets.note(tr(rule)))
        lines_box = self._build_lines_box()
        # 画面を開くたびにチェックのある状態で始める（外したまま忘れて、線より内側の範囲を保存しないため）
        self.fit_check = QCheckBox(tr(FIT_LABEL))
        self.fit_check.setChecked(True)
        ranges_box = QGroupBox(tr("範囲"))
        ranges = QVBoxLayout(ranges_box)
        ranges.addWidget(self.table, 1)
        ranges.addWidget(widgets.note(tr(FRAME_NOTE)))
        ranges.addWidget(self.fit_check)
        ranges.addWidget(widgets.note(tr(FIT_NOTE)))
        right = QVBoxLayout()
        right.addWidget(name_box)
        right.addWidget(words_box, 1)
        right.addWidget(lines_box)
        right.addWidget(ranges_box, 2)
        # つぶれすぎないように、範囲の表と題名の語の入力欄に最小の高さを持たせ、入らない高さでは右の欄をスクロールする
        frame = 2 * self.table.frameWidth()
        self.table.setMinimumHeight(
            self.table.horizontalHeader().sizeHint().height()
            + TABLE_ROWS_MIN * self.table.verticalHeader().defaultSectionSize()
            + frame
        )
        for edit in (self.words_edit, self.excludes_edit):
            margins = 2 * (edit.frameWidth() + round(edit.document().documentMargin()))
            edit.setMinimumHeight(
                WORDS_LINES_MIN * edit.fontMetrics().lineSpacing() + margins
            )
        side = QWidget()
        side.setLayout(right)
        # 右の欄の要る高さ：伸び縮みする部品（表と入力欄）を最小の高さにしたときの、幅 SIDE_WIDTH での中身の高さ。
        # 中身の minimumSizeHint は、文が折り返して増える行の分を含まないので使わない（英語の文で、文が重なった）
        flexible = (self.table, self.words_edit, self.excludes_edit)
        for w in flexible:
            w.setMaximumHeight(w.minimumHeight())
        right.invalidate()
        needed = right.totalHeightForWidth(SIDE_WIDTH)
        for w in flexible:
            w.setMaximumHeight(QWIDGETSIZE_MAX)
        right.invalidate()
        return SideScroll(side, needed)

    def _build_lines_box(self) -> QGroupBox:
        """「この様式のバリエーションの判別」のまとまり（罫線の並びを判別に使うかのチェックと、下限のスライダー）"""
        use, limit = self.line_match
        self.lines_check = QCheckBox(tr(LINES_CHECK))
        self.lines_check.setChecked(use)
        self.limit_slider = LimitSlider(Qt.Orientation.Horizontal)
        self.limit_slider.setRange(*LIMIT_STEPS)
        self.limit_slider.setPageStep(1)
        # 刻みに合わない値の様式（.json を直接書いた値など）は、近い位置に置くだけで、動かすまで値を変えない
        steps = min(max(round(limit / LIMIT_STEP), LIMIT_STEPS[0]), LIMIT_STEPS[1])
        self.limit_slider.setValue(steps)
        self.limit_label = QLabel(tr(LINES_LIMIT))
        self.limit_label.setBuddy(self.limit_slider)
        self.limit_value = QLabel(f"{limit:.2f}")
        self.lines_check.toggled.connect(self.line_match_changed)
        self.limit_slider.valueChanged.connect(self.line_match_changed)
        box = QGroupBox(tr(LINES_TITLE))
        layout = QVBoxLayout(box)
        layout.addWidget(widgets.note(tr(LINES_USE_NOTE)))
        layout.addWidget(self.lines_check)
        row = QHBoxLayout()
        row.addWidget(self.limit_label)
        row.addWidget(self.limit_slider, 1)
        row.addWidget(self.limit_value)
        layout.addLayout(row)
        for rule in LINES_RULES:
            layout.addWidget(widgets.note(tr(rule)))
        self._enable_limit()
        return box

    def line_match_changed(self, _value=None):
        """チェックかスライダーを変えた。下限は、スライダーを動かしたときだけ、その位置の値にする"""
        limit = self.line_match[1]
        if self.sender() is self.limit_slider:
            limit = round(self.limit_slider.value() * LIMIT_STEP, 2)
        self.line_match = (self.lines_check.isChecked(), limit)
        self.limit_value.setText(f"{limit:.2f}")
        self._enable_limit()

    def _enable_limit(self):
        """下限のスライダーは、罫線の並びを判別に使うときだけ使える"""
        on = self.lines_check.isChecked() and not self.busy
        for w in (self.limit_label, self.limit_slider, self.limit_value):
            w.setEnabled(on)

    def _build_frame(self, left: QVBoxLayout, side: QWidget, notice: str):
        """注意の帯、左右の欄、状態の文、保存・やめるのボタンを並べる。notice は状態の文の初めの値"""
        body = QHBoxLayout()
        body.addLayout(left, 1)
        body.addWidget(side)
        # 注意の帯（ダークでも読めるよう、固定の赤い文字ではなく、ラベルの色にする）
        note = widgets.Banner()
        note.set_messages([(f"⚠ {tr(NOTE)}", Tone.WARN)])
        self.status = QLabel(notice)
        self.status.setWordWrap(True)
        self.buttons = QDialogButtonBox()
        self.save_button = widgets.make_primary(
            self.buttons.addButton(tr("保存"), QDialogButtonBox.ButtonRole.AcceptRole)
        )
        self.cancel_button = self.buttons.addButton(
            tr("やめる"), QDialogButtonBox.ButtonRole.RejectRole
        )
        self.save_button.clicked.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addLayout(body, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.buttons)
        # 自動の確認で見分けるための、言語によらない名前（launcher.py の起動の画面と同じ決まり）
        for widget, name in (
            (self, "form_editor"),
            (self.view, "editor_view"),
            (self.draw_mask, "draw_mask"),
            (self.draw_erase, "draw_erase"),
            (self.name_edit, "form_name"),
            (self.words_edit, "title_words"),
            (self.excludes_edit, "title_excludes"),
            (self.read_title_button, "read_title"),
            (self.lines_check, "use_lines"),
            (self.limit_slider, "line_limit"),
            (self.table, "editor_ranges"),
            (self.fit_check, "fit_lines"),
            (self.status, "editor_status"),
            (self.save_button, "editor_save"),
            (self.cancel_button, "editor_cancel"),
        ):
            widget.setObjectName(name)

    def _build_undo_actions(self):
        """元に戻す・やり直すの操作とショートカットを作る"""
        self.undo_actions = []
        for text, keys, slot in [
            ("元に戻す", [QKeySequence.StandardKey.Undo], self.undo.undo),
            ("やり直す", [QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")], self.undo.redo),
        ]:  # fmt: skip
            a = QAction(tr(text), self)
            a.setShortcuts(keys)
            a.triggered.connect(slot)
            self.addAction(a)
            self.undo_actions.append(a)

    def boxes_changed(self, _page):
        self._check_frames()
        self.view.refresh_boxes()
        self.fill_table()

    def find_frame(
        self, rect
    ) -> tuple[tuple[float, float, float, float], tuple[str, ...]]:
        """範囲 rect の枠の線を、範囲を描いた元の画像で探し、線が見つかった組の辺を線の中心に合わせた範囲と、見つかった
        組を返す（重い処理のモジュールは、ここで読み込む）"""
        from . import forms

        return forms.fit_to_frame(self.base, self.base_is_work, rect)

    def _check_frames(self):
        """位置や大きさが変わった範囲の、枠の線を調べ直す。範囲の位置は変えない（画面で選んだ枠の線の値は、範囲を
        動かすまで残す）"""
        for b in self.boxes:
            if b.source != Source.ERASE and self.checked_rects.get(id(b)) != b.rect():
                _, b.frame = self.find_frame(b.rect())
                self.checked_rects[id(b)] = b.rect()

    def add_box(self, rect):
        """描いた範囲を足す。墨消しする範囲は、チェックがあれば線が見つかった辺を線の中心に合わせる"""
        source = Source.ERASE if self.draw_erase.isChecked() else Source.MANUAL
        box = Box(*rect, self.next_name(source), source)
        if source == Source.MANUAL:
            fitted, box.frame = self.find_frame(box.rect())
            if self.fit_check.isChecked():
                box.x, box.y, box.w, box.h = fitted
            self.checked_rects[id(box)] = box.rect()
        self.undo.push(AddCommand(self, 0, self.boxes, box))

    def move_box(self, box, old, new):
        """動かした範囲は、線の中心に合わせない（置いた位置から動くと、利用者が混乱するため）"""
        self.undo.push(GeometryCommand(self, 0, box, old, new))

    def resize_box(self, box, old, new):
        """大きさを変えた墨消しする範囲は、チェックがあれば、描いたときと同じく線が見つかった辺を線の中心に合わせる

        元に戻す操作には合わせた後の位置を入れ、元に戻す・やり直すでは、操作の前と後に見えていた位置にする。
        """
        if box.source != Source.ERASE:
            fitted, box.frame = self.find_frame(new)
            if self.fit_check.isChecked():
                new = fitted
            self.checked_rects[id(box)] = new
        # 合わせると大きさを変える前と同じ位置になったときは、何も変わらない操作を記録しない
        if new == old:
            self.boxes_changed(0)
            return
        self.undo.push(GeometryCommand(self, 0, box, old, new))

    def delete_box(self, box):
        self.undo.push(DeleteCommand(self, 0, self.boxes, box))

    def select_box(self, box):
        """枠をクリックしたときは、範囲の一覧のその行を選ぶ（名前を編集しやすいように）"""
        if box in self.boxes:
            self.table.selectRow(self.boxes.index(box))

    def next_name(self, source: Source) -> str:
        """描いた範囲の名前の初めの値（「範囲1」「消す範囲1」など。画面の言語の文）"""
        template = "消す範囲{number}" if source == Source.ERASE else "範囲{number}"
        used = {b.kind for b in self.boxes}
        n = 1
        while tr(template, number=n) in used:
            n += 1
        return tr(template, number=n)

    def fill_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.boxes))
        for row, b in enumerate(self.boxes):
            kind = QTableWidgetItem(
                tr("見本から消す" if b.source == Source.ERASE else "墨消しする")
            )
            kind.setFlags(kind.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, kind)
            self.table.setItem(row, 1, QTableWidgetItem(b.kind))
            if b.source == Source.ERASE:
                self.table.setCellWidget(row, 2, None)
                blank = QTableWidgetItem("")
                blank.setFlags(blank.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, 2, blank)
                continue
            choice = FrameChoice()
            for label, _ in FRAME_CHOICES:
                choice.addItem(tr(label))
            pairs = [p for _, p in FRAME_CHOICES]
            choice.setCurrentIndex(pairs.index(tuple(b.frame or ())))
            choice.currentIndexChanged.connect(
                lambda i, box=b: self.set_frame(box, FRAME_CHOICES[i][1])
            )
            self.table.setCellWidget(row, 2, choice)
        self.table.blockSignals(False)

    def set_frame(self, box: Box, pairs: tuple[str, ...]):
        """「枠の線」の列で選んだ組にする"""
        box.frame = pairs

    def rename(self, item: QTableWidgetItem):
        if item.column() != 1 or item.row() >= len(self.boxes):
            return
        box = self.boxes[item.row()]
        if text := item.text().strip():
            box.kind = text
            self.view.refresh_boxes()
        else:  # 空の名前にはしない
            self.fill_table()

    # 題名を読む

    def display_image(self):
        return self.state.result.work

    def read_title(self):
        self.set_busy(True, tr("題名を読んでいます…"))
        self.session.analyzer.call(
            analysis.read_title,
            self.display_image(),
            self.session.engine,
            callback=self.title_read,
        )

    def title_read(self, text, error):
        self.set_busy(False)
        if error is not None:
            QMessageBox.warning(
                self, tr(TITLE), tr("題名を読めませんでした。{error}", error=error)
            )
            return
        text = (text or "").strip()
        if not text:
            QMessageBox.information(self, tr(TITLE), tr("題名を読めませんでした。"))
            return
        # 読めた文をそのまま足す。画面で編集してから保存する
        lines = [w for w in self.words_edit.toPlainText().splitlines() if w.strip()]
        self.words_edit.setPlainText("\n".join([*lines, text]))
        self.status.setText(
            tr(
                "読めた題名を、「{heading}」の欄の最後の行に入れました。年分と帳票の名前の語だけを"
                "スペースで区切って残し（例：令和8年分　扶養控除等）、ほかの語は消してください。",
                heading=tr(WORDS_HEADING),
            )
        )

    # 保存（保存の前に確かめてから、保存する）

    def words(self, edit: QPlainTextEdit) -> tuple[str, ...]:
        return tuple(w.strip() for w in edit.toPlainText().splitlines() if w.strip())

    def others(self) -> list[FormSpec]:
        """この様式と誤認されないかを確かめる、ほかの様式（この端末で使わない様式も含む。ほかの端末では使うため）"""
        return [f for f in self.loaded.forms if f.id != self.form_id]

    def save(self):
        name = self.name_edit.text().strip()
        has_fields = any(b.source != Source.ERASE for b in self.boxes)
        problem = formstext.save_problem(name, self.others(), has_fields)
        if problem is not None:
            QMessageBox.warning(self, tr(TITLE), problem)
            return
        rects = [(b.x, b.y, b.w, b.h) for b in self.boxes]
        self.pending = self.new_spec()  # 押した時点の内容で保存する
        self.set_busy(
            True,
            tr(
                "保存の前に確かめています（見本の画像、ほかの様式との位置合わせ、類似した様式の題名）…"
            ),
        )
        self.session.analyzer.call(
            analysis.build_form,
            self.base,
            self.base_is_work,
            rects,
            self.others(),
            self.pending,
            self.session.engine,
            self.lines,
            callback=self.checked,
        )

    def checked(self, result, error):
        if error is not None:
            self.set_busy(False)
            QMessageBox.warning(
                self,
                tr(TITLE),
                tr("保存の前に確かめることができませんでした。{error}", error=error),
            )
            return
        png, check = result
        if not check.aligned:
            self.set_busy(False)
            QMessageBox.warning(
                self, tr(TITLE), formstext.not_aligned_text(check.inliers, check.needed)
            )
            return
        # 見本が類似した様式（互いに位置合わせが成り立った様式）は、解析で題名で決める。題名で見分けられない
        # ものだけ、入れる語を示して警告する
        for name, line, deletions in check.unclear:
            if not widgets.confirm(
                self,
                tr(TITLE),
                unclear_text(name, line, deletions),
                "",
                tr("保存する"),
                tr("保存しない"),
            ):
                self.set_busy(False)
                return
        warnings = formstext.save_warnings(check.written, check.inliers)
        if warnings and not ask(self, "\n".join([*warnings, tr("保存しますか？")])):
            self.set_busy(False)
            return
        if (
            self.base_is_work
        ):  # 範囲だけの編集では、持っている罫線のまま（formsave.new_spec）
            self.pending = dataclasses.replace(self.pending, lines=check.lines)
        self.png = png
        self.set_busy(True, tr(uitext.FORMS_CHECKING))
        self.session.background.run(
            formsave.saved_revision,
            self.folder,
            self.form_id,
            callback=self.revision_read,
        )

    def revision_read(self, saved, error):
        if error is not None:
            self.set_busy(False)
            QMessageBox.warning(
                self, tr(TITLE), tr("様式のフォルダを読めません。{error}", error=error)
            )
            return
        if not overwrite_ok(self, self.spec, saved):
            self.set_busy(False)
            return
        self.set_busy(True, tr("保存しています…"))
        self.save_warnings = []
        self.session.background.run(
            formsave.save,
            self.folder,
            self.pending,
            self.png,
            self.operation,
            None,
            self.save_warnings,
            callback=self.written,
        )

    def new_spec(self) -> FormSpec:
        """今の内容から、保存する様式を作る（formsave.new_spec）"""
        if self.base_is_work:
            h, w = self.base.shape
            size = (w, h)
        else:
            size = self.spec.size
        return formsave.new_spec(
            self.form_id,
            self.name_edit.text().strip(),
            self.spec,
            size,
            self.boxes,
            self.words(self.words_edit),
            self.words(self.excludes_edit),
            self.line_match,
        )

    def written(self, saved, error):
        self.set_busy(False)
        if error is not None:
            QMessageBox.warning(
                self, tr(TITLE), tr("保存できませんでした。{error}", error=error)
            )
            return
        if self.save_warnings:  # 保存はできた
            QMessageBox.warning(
                self,
                tr(TITLE),
                done_with_warnings(tr("保存しました"), self.save_warnings),
            )
        self.saved = saved
        self.accept()

    def set_busy(self, busy: bool, text: str = ""):
        """処理のあいだは、保存・題名を読むに加えて、範囲の操作・元に戻す・名前と題名の語の入力・範囲の表も止める
        （確認・保存の途中で内容を変えても、保存には使わないため）"""
        self.busy = busy
        self.save_button.setEnabled(not busy)
        self.read_title_button.setEnabled(not busy)
        self.view.locked = busy
        for a in self.undo_actions:
            a.setEnabled(not busy)
        for edit in (self.name_edit, self.words_edit, self.excludes_edit):
            edit.setReadOnly(busy)
        for w in (
            self.table,
            self.draw_mask,
            self.draw_erase,
            self.fit_check,
            self.lines_check,
        ):
            w.setEnabled(not busy)
        self._enable_limit()
        self.status.setText(text)

    def reject(self):
        if not self.busy:  # 処理のあいだは閉じない
            super().reject()

    def done(self, result):
        """閉じるとき（保存・やめる・Esc）。ウィンドウの大きさと最大化の状態を覚える"""
        remember_geometry(self, EDITOR_GEOMETRY_KEY)
        super().done(result)

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            super().closeEvent(event)


def register_from_page(session, st, parent: QWidget, on_saved=None) -> None:
    """ページ（解析が終わった PageState）から様式を登録する

    墨消しする範囲の初めの値は、そのページで「墨消しする」になっている範囲（自動の候補は欄の名前、手で
    追加した範囲は「範囲1」「範囲2」…）とする。範囲を描く画面を開く前に、初めの範囲の記入と、写真などで歪んだ
    画像と、低い解像度を確かめ、警告があれば合わせて示して、登録を続けるかを尋ねる。保存したら on_saved を呼ぶ。
    """
    fields, n = [], 0
    for b in st.result.boxes:
        if not b.active:
            continue
        if b.source == Source.AUTO:  # 自動の候補から引き継いだ範囲は、その欄の名前
            name = b.kind
        else:
            n += 1
            name = tr("範囲{number}", number=n)
        fields.append(Field(name, b.x, b.y, b.w, b.h))

    def forms_loaded(loaded: Loaded | None, error):
        if error is not None or loaded.error:
            QMessageBox.warning(
                parent, tr(TITLE), loaded.error if error is None else str(error)
            )
            return
        # 罫線の一致度の下限の初めの値は、使うほかの様式の見本の罫線と比べて決める。求められなければ、比べない値にして、
        # 範囲を描く画面の状態の行で知らせる
        session.analyzer.call(
            analysis.line_match_default,
            st.result.work,
            list(loaded.active),
            callback=lambda value, failed: open_editor(
                loaded, *((None, None) if failed is not None else value)
            ),
        )

    def open_editor(
        loaded: Loaded, line_match: tuple[bool, float] | None, lines: tuple | None
    ):
        notice = ""
        if line_match is None:
            notice = tr(
                formstext.LIMIT_NOT_COMPARED, limit=f"{formstore.LINE_MATCH_MIN:.2f}"
            )
        editor = FormEditor(
            session,
            operation=formsave.OP_REGISTER,
            spec=None,
            base=st.result.work,
            base_is_work=True,
            fields=fields,
            erase=[],
            loaded=loaded,
            notice=notice,
            parent=parent,
            line_match=line_match,
            lines=lines,
        )
        accepted = editor.exec() == QDialog.DialogCode.Accepted
        editor.deleteLater()  # 閉じた画面を消す（消さないと、親の部品の子として残り続ける）
        if accepted and on_saved is not None:
            on_saved()

    def checked(check, error):
        warnings = page_check_warnings(check, error, st.source.dpi)
        if not ask(parent, "\n".join([*warnings, tr(formstext.REGISTER_QUESTION)])):
            return
        session.background.run(formstore.load, forms_choice(), callback=forms_loaded)

    session.analyzer.call(analysis.check_page, st.result.work, fields, callback=checked)


def page_check_warnings(check, error, source_dpi: float | None) -> list[str]:
    """範囲を描く画面を開く前に確かめた結果（analysis.check_page）の警告。確かめられなければ、その理由を警告にする
    （確かめずに進んだことを知らせる）"""
    if error is not None:
        return [tr("記入と歪みを確かめられませんでした。{error}", error=error)]
    return formstext.page_warnings(check.distorted, check.written, source_dpi)
