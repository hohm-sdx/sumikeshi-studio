# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""フォルダを開くときに、サブフォルダの中のファイルをどの深さまで開くかを選ぶダイアログ

数える処理（folders.Selection.start_counting）は裏のスレッドで動く。このダイアログは数えた結果を一定の間隔で読み、
選択肢の件数を書き換える。数え終わる前でも選んで開ける。
"""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, folders, reviewtext, widgets
from .i18n import tr

REFRESH_MS = 100  # 数えた結果を読む間隔（ミリ秒）


class DepthDialog(QDialog):
    """深さを選ぶダイアログ。いつも「直下だけ」を選んだ状態で出す（選んだ深さは覚えない）"""

    def __init__(self, parent: QWidget | None, selection: folders.Selection):
        super().__init__(parent)
        self.selection = selection
        # 自動の確認で見分けるための、言語によらない名前（launcher.py の起動の画面と同じ決まり）
        self.setObjectName("depth_dialog")
        self.setWindowTitle(tr(APP_NAME))
        layout = QVBoxLayout(self)
        question = QLabel(tr(reviewtext.DEPTH_QUESTION))
        question.setObjectName("depth_question")
        question.setWordWrap(True)
        layout.addWidget(question)
        self.group = QButtonGroup(self)
        self.buttons: list[QRadioButton] = []
        for i, (depth, _label) in enumerate(reviewtext.DEPTH_CHOICES):
            button = QRadioButton()
            button.setObjectName("depth_all" if depth is None else f"depth_{depth}")
            self.group.addButton(button, i)
            layout.addWidget(button)
            self.buttons.append(button)
        self.buttons[0].setChecked(True)
        box = QDialogButtonBox()
        self.open_button = widgets.make_primary(
            box.addButton(tr("開く"), QDialogButtonBox.ButtonRole.AcceptRole)
        )
        self.open_button.setObjectName("depth_open")
        self.cancel_button = box.addButton(
            tr("やめる"), QDialogButtonBox.ButtonRole.RejectRole
        )
        self.cancel_button.setObjectName("depth_cancel")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(REFRESH_MS)
        self.refresh()

    def refresh(self) -> None:
        """数えた結果を選択肢の文に入れる。数え終わるか、上限で止めたら、読むのをやめる"""
        counts = self.selection.counts
        for button, (depth, label) in zip(self.buttons, reviewtext.DEPTH_CHOICES):
            button.setText(reviewtext.depth_text(label, counts, depth))
        if counts.complete or counts.stopped:
            self.timer.stop()

    def depth(self) -> int | None:
        """選んだ深さ（None はすべて）"""
        return reviewtext.DEPTH_CHOICES[self.group.checkedId()][0]


def choose_depth(
    parent: QWidget | None, selection: folders.Selection
) -> tuple[bool, int | None]:
    """深さを選ぶダイアログを出し、（開くか, 選んだ深さ）を返す（テストでは置き換える）。出すあいだ、裏のスレッドで
    数える。やめたら、数えるのを止める（たどっている途中のフォルダを読み終わるのは待たない）"""
    selection.start_counting()
    dialog = DepthDialog(parent, selection)
    with widgets.dialog():
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
    if not accepted:
        selection.stop_counting(wait=False)
    return accepted, dialog.depth()
