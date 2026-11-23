# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""3つの画面に共通の部品

部品（ボタン・チェック・表など）の形と色は Windows の見た目（Qt の既定の見た目）に任せ、ここではツールが決める
ものだけを作る。
- 状態のラベル（記号・文字・色）と、その色（ライトとダーク。StatusLabel・tone_colors）
- ページの上の帯（Banner）、項目ごとのラベルの並び（LabelRow）、一覧の行に並べるラベル（LabelDelegate）
- 凡例の色見本（Swatch）
- 主なボタン（make_primary）と、見出し・補足の文字（heading・note）
- ツールバーの記号（Windows の記号の書体の字。icon_font_family・glyph_icon）
- 確認のメッセージ（既定のボタンを「しない」側にする。confirm・ask）
- ダイアログが開いているかを数える仕組み（dialog・dialog_open。後から起動したツールからの通知を、ダイアログが
  閉じるまで待たせる）
- 出力の結果のダイアログ（ResultDialog。出力先のフォルダを開くボタンを持つ）
"""

import enum
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QUrl
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QTransform,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .i18n import tr


class Tone(enum.Enum):
    """状態のラベルの種類"""

    GOOD = "good"  # ✓ 問題なし、準備ができた、表示したページ、出力済み
    WARN = "warn"  # ⚠ 要確認、⚠ 向き、まだ表示していないページ、範囲を描く画面の注意
    ERROR = "error"  # ✕ 読めない、この PC では使わない、処理できなかったページ、起動の準備の失敗
    NEUTRAL = "neutral"  # 帳票の名前、OCR、ページの数、解析中など


# 種類ごとの（背景, 文字）の色。ライト用とダーク用。どの組も、WCAG 2 のコントラスト比が4.5以上になるようにする
# （tests/test_widgets.py で確かめる）
COLORS = {
    Tone.GOOD: (("#DFF6DD", "#0E5A0E"), ("#1F3D1F", "#9BE39B")),
    Tone.WARN: (("#FFF4CE", "#5C3C00"), ("#4A3B10", "#FCE38A")),
    Tone.ERROR: (("#FDE7E9", "#A4262C"), ("#4A2125", "#FFB3BA")),
    Tone.NEUTRAL: (("#EDEBE9", "#323130"), ("#3B3A39", "#E1DFDD")),
}


def is_dark(palette: QPalette | None = None) -> bool:
    """パレット（渡さなければアプリのパレット）の Window の色が暗いか。Windows の設定でなく、実際に描いている色で決める
    （Windows 10 の見た目は、Windows がダークでもライトで描く見込みのため）"""
    palette = palette or QGuiApplication.palette()
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def tone_colors(tone: Tone, dark: bool | None = None) -> tuple[QColor, QColor]:
    """ラベルの（背景, 文字）の色"""
    light_pair, dark_pair = COLORS[tone]
    bg, fg = dark_pair if (is_dark() if dark is None else dark) else light_pair
    return QColor(bg), QColor(fg)


def contrast_ratio(a: QColor, b: QColor) -> float:
    """2つの色のコントラスト比（WCAG 2。1〜21）"""

    def luminance(c: QColor) -> float:
        def channel(v: float) -> float:
            return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

        return (
            0.2126 * channel(c.redF())
            + 0.7152 * channel(c.greenF())
            + 0.0722 * channel(c.blueF())
        )

    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class StatusLabel(QLabel):
    """状態のラベル：記号と文字を、種類の色の角の丸い枠に出す。Windows のライトとダークが変わったら色を直す"""

    def __init__(self, text: str = "", tone: Tone = Tone.NEUTRAL, parent=None):
        super().__init__(parent)
        self.tone = tone
        self._dark: bool | None = None
        # 高さは文字に合わせる（表のセルや画面下に置いても、上下に伸ばさない）
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_status(text, tone)

    def set_status(self, text: str, tone: Tone | None = None) -> None:
        if tone is not None and tone != self.tone:
            self.tone = tone
            self._dark = None
        self.setText(text)
        self._apply()

    def _apply(self) -> None:
        dark = is_dark()
        if dark == self._dark:
            return
        self._dark = dark
        bg, fg = tone_colors(self.tone, dark)
        self.setStyleSheet(
            f"QLabel {{ background-color: {bg.name()}; color: {fg.name()};"
            " border-radius: 4px; padding: 1px 6px; }"
        )

    def changeEvent(self, event):
        if event.type() in (
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        ):
            self._apply()
        super().changeEvent(event)


class Banner(StatusLabel):
    """ページの上の帯：ラベルと同じ色で、ウィンドウの幅いっぱいに出し、長い文は折り返す。文がなければ隠す"""

    def __init__(self, parent=None):
        super().__init__("", Tone.WARN, parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setWordWrap(True)
        self.hide()

    def set_messages(self, messages: list[tuple[str, Tone]]) -> None:
        """出す文（複数なら行を分ける）。色は、いちばん強い種類（誤り・注意・中立の順）にする"""
        if not messages:
            self.hide()
            return
        order = (Tone.ERROR, Tone.WARN, Tone.NEUTRAL, Tone.GOOD)
        tone = min((t for _, t in messages), key=order.index)
        self.set_status("\n".join(text for text, _ in messages), tone)
        self.show()

    def _apply(self) -> None:
        dark = is_dark()
        if dark == self._dark:
            return
        self._dark = dark
        bg, fg = tone_colors(self.tone, dark)
        self.setStyleSheet(
            f"QLabel {{ background-color: {bg.name()}; color: {fg.name()};"
            " padding: 6px 10px; }"
        )


class LabelRow(QWidget):
    """項目ごとのラベルを横に並べる（墨消しの画面の下）。項目は（文, 種類, マウスを重ねたときの説明）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.labels: list[StatusLabel] = []
        self.parts: list[tuple[str, Tone, str]] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addStretch()

    def set_parts(self, parts: list[tuple[str, Tone, str]]) -> None:
        self.parts = list(parts)
        layout = self.layout()
        while len(self.labels) < len(
            parts
        ):  # 足りないラベルを作る（多い分は隠して使い回す）
            label = StatusLabel()
            layout.insertWidget(len(self.labels), label)
            self.labels.append(label)
        for label, (text, tone, tip) in zip(self.labels, parts):
            label.set_status(text, tone)
            label.setToolTip(tip)
            label.show()
        for label in self.labels[len(parts) :]:
            label.hide()

    def text(self) -> str:
        """並べた文をつないだもの（テストで使う）"""
        return "　".join(text for text, _, _ in self.parts)

    def tips(self) -> str:
        """マウスを重ねたときの説明をつないだもの（テストで使う）"""
        return "　".join(tip for _, _, tip in self.parts if tip)


class LabelDelegate(QStyledItemDelegate):
    """一覧の行の、文のあとにラベルを並べて描く（ページは数百になるので、行ごとに部品を置かない）

    行の項目には、MAIN_ROLE に行の文、LABELS_ROLE に（文, 種類の値）の並びを入れる。表示の文（DisplayRole）は、
    それをつないだもの（読み上げなどのため）にしておく。
    """

    MAIN_ROLE = Qt.ItemDataRole.UserRole + 1
    LABELS_ROLE = Qt.ItemDataRole.UserRole + 2
    GAP = 6  # 文とラベル、ラベルどうしの間
    PAD = 5  # ラベルの中の左右の余白

    def paint(self, painter, option, index):
        labels = index.data(self.LABELS_ROLE) or []
        main = index.data(self.MAIN_ROLE)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        if main is not None:
            opt.text = main
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget
        )
        if not labels:
            return
        rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget
        )
        metrics = opt.fontMetrics
        x = rect.left() + metrics.horizontalAdvance(opt.text) + self.GAP
        h = metrics.height()
        dark = is_dark()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(opt.font)
        for text, tone in labels:
            bg, fg = tone_colors(Tone(tone), dark)
            w = metrics.horizontalAdvance(text) + 2 * self.PAD
            r = QRectF(x, rect.center().y() - h / 2 + 0.5, w, h)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(r, 4, 4)
            painter.setPen(fg)
            painter.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
            x += w + self.GAP
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)  # 表示の文（ラベルの文を含む）の幅
        labels = index.data(self.LABELS_ROLE) or []
        extra = len(labels) * (2 * self.PAD + self.GAP)
        return QSize(size.width() + extra, size.height() + 2)


class CenteredCheckDelegate(QStyledItemDelegate):
    """表の項目のチェック（ItemIsUserCheckable）を、セルの中央に描き、中央のチェックのクリックで切り替える
    （Qt はセルの左端に描くため）

    チェックの状態は項目のまま持つので、itemChanged と setCheckState はそのまま使える。スペースキーで切り替える
    処理は、Qt の今の処理を使う。
    """

    def check_rect(self, option, index):
        """セルの中央に置いたチェックの四角（大きさは、Qt が求めるチェックの大きさ）"""
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else QApplication.style()
        rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, opt.widget
        )
        rect.moveCenter(option.rect.center())
        return rect

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else QApplication.style()
        checked = opt.checkState == Qt.CheckState.Checked
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget
        )  # 背景だけ（チェックを描かない）
        mark = QStyleOptionViewItem(opt)
        mark.rect = self.check_rect(option, index)
        mark.state &= ~QStyle.StateFlag.State_HasFocus
        mark.state |= (
            QStyle.StateFlag.State_On if checked else QStyle.StateFlag.State_Off
        )
        style.drawPrimitive(
            QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck,
            mark,
            painter,
            opt.widget,
        )

    def editorEvent(self, event, model, option, index):
        mouse = (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
        )
        if event.type() not in mouse:
            return super().editorEvent(event, model, option, index)
        if not index.flags() & Qt.ItemFlag.ItemIsEnabled:
            return False
        if event.button() != Qt.MouseButton.LeftButton or not self.check_rect(
            option, index
        ).contains(event.position().toPoint()):
            return False
        if event.type() != QEvent.Type.MouseButtonRelease:
            return True  # 押したとき・2回目のクリックは受け取るだけ（離したときに切り替える）
        state = Qt.CheckState(index.data(Qt.ItemDataRole.CheckStateRole))
        new = (
            Qt.CheckState.Unchecked
            if state == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        return model.setData(index, new, Qt.ItemDataRole.CheckStateRole)


class Swatch(QWidget):
    """凡例の色見本：ページの枠と同じ色・線の小さな四角。周りに部品の文字の色で細い枠を付ける（ダークの背景でも
    見本の形が分かるように）"""

    def __init__(self, color: QColor, dashed: bool = False, parent=None):
        super().__init__(parent)
        self.color = color
        self.dashed = dashed
        self.setFixedSize(QSize(22, 14))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        style = Qt.PenStyle.DashLine if self.dashed else Qt.PenStyle.SolidLine
        p.setPen(QPen(self.color, 3, style))
        p.drawRect(r.adjusted(3, 3, -3, -3))
        p.end()


def make_primary(button: QPushButton) -> QPushButton:
    """主なボタン（1つの画面に1つだけ）。既定のボタンにし、押せるときは Windows のアクセントの色で塗る。
    色が付かない見た目でも分かるよう、文字を一回り大きくし、高さを1.5倍にする

    Windows 11 の見た目（Qt 6.12 の windows11）は、既定のボタンの枠の線だけをアクセントの色にして、塗らない。
    ボタンの色の組に Button・ButtonText を指定すると、その色で塗る（Qt のソースで確認した）。押せないときの色は
    指定せず、ふつうの押せないボタンの色にする。
    文字の色は HighlightedText とし、アクセントの色とのコントラスト比が4.5に満たなければ、黒か白の高いほうにする"""
    button.setDefault(True)
    button.setAutoDefault(True)
    palette = button.palette()
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        accent = palette.color(group, QPalette.ColorRole.Accent)
        text = palette.color(group, QPalette.ColorRole.HighlightedText)
        if contrast_ratio(accent, text) < 4.5:
            text = max(
                (QColor("black"), QColor("white")),
                key=lambda c: contrast_ratio(accent, c),
            )
        palette.setColor(group, QPalette.ColorRole.Button, accent)
        palette.setColor(group, QPalette.ColorRole.ButtonText, text)
    button.setPalette(palette)
    font = QFont(button.font())
    font.setPointSizeF(font.pointSizeF() * 1.15)
    button.setFont(font)
    button.setMinimumHeight(round(button.sizeHint().height() * 1.5))
    return button


def heading(text: str, parent=None) -> QLabel:
    """見出しの文字（本文の1.25倍・太字）"""
    label = QLabel(text, parent)
    font = QFont(label.font())
    font.setPointSizeF(font.pointSizeF() * 1.25)
    font.setBold(True)
    label.setFont(font)
    return label


def note(text: str = "", parent=None) -> QLabel:
    """補足の文字（本文の0.9倍。色はパレットの薄い文字の色。灰色を固定で書かない）"""
    label = QLabel(text, parent)
    font = QFont(label.font())
    font.setPointSizeF(font.pointSizeF() * 0.9)
    label.setFont(font)
    label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
    label.setWordWrap(True)
    return label


# ---- ツールバーの記号

# 使う書体（最初に見つかったもの）。Segoe Fluent Icons は Windows 11、Segoe MDL2 Assets は Windows 10 に入っている
ICON_FONTS = ("Segoe Fluent Icons", "Segoe MDL2 Assets")
# 記号の字の番号（Segoe MDL2 Assets と Segoe Fluent Icons で同じ番号のもの）。左に回すは、右に回すの字を左右に
# 反転して描く。番号と形は、Windows のスクリーンショットで確かめる
GLYPHS = {
    "open": "",  # OpenFile
    "folder": "",  # Folder
    "prev": "",  # ChevronLeft
    "next": "",  # ChevronRight
    "rotate_left": "",  # Rotate（左右に反転）
    "rotate_right": "",  # Rotate
    "delete": "",  # Delete
    "undo": "",  # Undo
    "redo": "",  # Redo
    "fit": "",  # FitPage
    "zoom_in": "",  # ZoomIn
    "zoom_out": "",  # ZoomOut
    "help": "",  # Help
}
MIRRORED = {"rotate_left"}


def icon_font_family(families: list[str] | None = None) -> str | None:
    """使う記号の書体の名前。どちらもなければ None（ツールバーを文字だけにする）"""
    available = set(QFontDatabase.families() if families is None else families)
    return next((f for f in ICON_FONTS if f in available), None)


def apply_tool_icons(bar: QToolBar, items, palette: QPalette) -> None:
    """ツールバーのボタンに記号を描く。items は (操作, 記号の名前, 記号だけにするか)。記号の書体がなければ、
    すべて文字だけにする"""
    family = icon_font_family()
    for action, name, icon_only in items:
        button = bar.widgetForAction(action)
        if button is None:
            continue
        if family is None:
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            continue
        action.setIcon(glyph_icon(name, family, palette))
        button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
            if icon_only
            else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )


def glyph_icon(name: str, family: str, palette: QPalette, size: int = 20) -> QIcon:
    """記号の書体の字 name を描いた記号。色はパレットのボタンの文字の色、押せないときはその薄い色"""
    icon = QIcon()
    for mode, group in (
        (QIcon.Mode.Normal, QPalette.ColorGroup.Active),
        (QIcon.Mode.Disabled, QPalette.ColorGroup.Disabled),
    ):
        ratio = (
            QGuiApplication.primaryScreen().devicePixelRatio()
            if QGuiApplication.primaryScreen()
            else 1.0
        )
        pixmap = QPixmap(round(size * ratio), round(size * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        font = QFont(family)
        font.setPixelSize(round(size * 0.8))
        p.setFont(font)
        p.setPen(palette.color(group, QPalette.ColorRole.ButtonText))
        if name in MIRRORED:
            p.setTransform(QTransform().translate(size, 0).scale(-1, 1))
        p.drawText(
            QRectF(QPointF(0, 0), QSize(size, size)),
            Qt.AlignmentFlag.AlignCenter,
            GLYPHS[name],
        )
        p.end()
        icon.addPixmap(pixmap, mode)
    return icon


# ---- 確認のメッセージ


def confirm_box(
    parent, title: str, text: str, info: str, yes: str, no: str, default_yes=False
) -> tuple[QMessageBox, QPushButton]:
    """確認のメッセージと「する」側のボタン。既定のボタン（Enter で押されるもの）は「しない」側にする（default_yes
    なら「する」側）。Esc はどちらでも「しない」側"""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    box.setInformativeText(info)
    yes_button = box.addButton(yes, QMessageBox.ButtonRole.AcceptRole)
    no_button = box.addButton(no, QMessageBox.ButtonRole.RejectRole)
    # 自動の確認で見分けるための、言語によらない名前（ボタンの文は確認ごとに違う）
    yes_button.setObjectName("accept")
    no_button.setObjectName("reject")
    box.setDefaultButton(yes_button if default_yes else no_button)
    box.setEscapeButton(no_button)
    return box, yes_button


def confirm(
    parent, title: str, text: str, info: str, yes: str, no: str, default_yes=False
) -> bool:
    """確認のメッセージを出し、「する」側が押されたか（テストでは置き換える）"""
    box, yes_button = confirm_box(parent, title, text, info, yes, no, default_yes)
    with dialog():
        box.exec()
    return box.clickedButton() is yes_button


def choose(parent, title: str, text: str, info: str, choices: tuple[str, ...]) -> int:
    """選ぶボタンが3つ以上ある確認のメッセージを出し、押されたボタンの番号を返す（テストでは置き換える）。最後の
    ボタンを「しない」側にし、既定のボタン（Enter）と Esc もそれにする"""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    box.setInformativeText(info)
    roles = [QMessageBox.ButtonRole.AcceptRole]
    roles += [QMessageBox.ButtonRole.ActionRole] * (len(choices) - 2)
    roles += [QMessageBox.ButtonRole.RejectRole]
    buttons = [box.addButton(c, r) for c, r in zip(choices, roles, strict=True)]
    # 自動の確認で見分けるための、言語によらない名前（並びの番号。ボタンの文は確認ごとに違う）
    for i, button in enumerate(buttons):
        button.setObjectName(f"choice_{i}")
    box.setDefaultButton(buttons[-1])
    box.setEscapeButton(buttons[-1])
    with dialog():
        box.exec()
    clicked = box.clickedButton()
    return buttons.index(clicked) if clicked in buttons else len(choices) - 1


def message(parent, title: str, text: str, info: str, warning: bool = False) -> None:
    """本文と説明のあるメッセージを出す（テストでは置き換える）。warning なら警告のアイコンにする"""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning if warning else QMessageBox.Icon.Information)
    box.setWindowTitle(title)
    box.setText(text)
    box.setInformativeText(info)
    box.addButton(QMessageBox.StandardButton.Ok)
    with dialog():
        box.exec()


def ask_box(parent, title: str, text: str) -> tuple[QMessageBox, QPushButton]:
    """「はい」「いいえ」で確かめる確認のメッセージと「はい」のボタン。既定のボタン（Enter）と Esc は「いいえ」。
    ボタンの名前は confirm_box と同じ（accept・reject）。Qt の標準のボタンは、Qt の翻訳を読み込んでいないので日本語の
    画面でも英語の文になるため、ボタンの文はツールが付ける"""
    return confirm_box(parent, title, text, "", tr("はい"), tr("いいえ"))


def ask(parent, title: str, text: str) -> bool:
    """「はい」「いいえ」で確かめ（ask_box）、「はい」が押されたか（テストでは置き換える）"""
    box, yes = ask_box(parent, title, text)
    with dialog():
        box.exec()
    return box.clickedButton() is yes


# ---- ダイアログが開いているか

_dialogs = 0  # dialog() の中にいる数


@contextmanager
def dialog() -> Iterator[None]:
    """確認のメッセージ・ファイルを選ぶダイアログを開いているあいだ数える。Windows の標準のファイルを選ぶダイアログ
    は、Qt の中でダイアログとして数えられるかを確かめていないので、ツールが自分で数える"""
    global _dialogs
    _dialogs += 1
    try:
        yield
    finally:
        _dialogs -= 1


def dialog_open() -> bool:
    """確認のメッセージ・ファイルを選ぶダイアログ・右クリックのメニューが開いている（後から起動したツールから届いた
    ファイルは、閉じるまで開かない。ダイアログを出しているあいだに開き直すと、確認の後の操作が別のファイルに効くため）"""
    return (
        _dialogs > 0
        or QApplication.activeModalWidget() is not None
        or QApplication.activePopupWidget() is not None
    )


# ---- 出力の結果のダイアログ


class ResultDialog(QDialog):
    """出力の結果のダイアログ。文と、フォルダを開くボタンと「閉じる」を持つ

    QMessageBox はどのボタンを押しても閉じるので、フォルダを開いてもダイアログが開いたままになるよう、QDialog で作る。
    folders は (ボタンの文, フォルダ, 開けなかったときに示す名前)。「閉じる」が既定のボタン（Enter）で、Esc でも閉じる。
    """

    def __init__(
        self,
        parent,
        title: str,
        text: str,
        warning: bool,
        folders: list[tuple[str, Path, str]],
    ):
        super().__init__(parent)
        # 自動の確認で見分けるための、言語によらない名前（launcher.py の起動の画面と同じ決まり）
        self.setObjectName("export_result")
        self.setWindowTitle(title)
        self.text = text
        style = self.style()
        icon = QLabel()
        kind = (
            QStyle.StandardPixmap.SP_MessageBoxWarning
            if warning
            else QStyle.StandardPixmap.SP_MessageBoxInformation
        )
        icon.setPixmap(style.standardIcon(kind).pixmap(32, 32))
        icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        label = QLabel(text)
        label.setObjectName("export_result_text")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(label)
        body = QHBoxLayout()
        body.addWidget(icon)
        body.addWidget(scroll, 1)
        self.folder_buttons: list[QPushButton] = []
        row = QHBoxLayout()
        for i, (text_, folder, what) in enumerate(folders, 1):
            b = QPushButton(text_)
            b.setObjectName(f"show_folder_{i}")
            b.setAutoDefault(False)  # Enter で押さない
            b.clicked.connect(lambda _=False, f=folder, w=what: self.open_folder(f, w))
            row.addWidget(b)
            self.folder_buttons.append(b)
        row.addStretch()
        self.close_button = QPushButton(tr("閉じる"))
        self.close_button.setObjectName("close")
        self.close_button.setDefault(True)
        self.close_button.clicked.connect(self.accept)
        row.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addLayout(row)
        self.resize(640, min(160 + 22 * text.count("\n"), 600))

    def open_folder(self, folder: Path, what: str) -> None:
        """フォルダをエクスプローラーで開く。ダイアログは開いたままにする。開けなければ理由を知らせる"""
        if not (
            folder.is_dir()
            and QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        ):
            QMessageBox.warning(
                self,
                self.windowTitle(),
                tr(
                    "{what}を開けませんでした（{folder}）。共有フォルダにつながっているか確かめてください。",
                    what=what,
                    folder=folder,
                ),
            )
