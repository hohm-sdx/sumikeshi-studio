# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""ページの表示と、マウスでの範囲の操作"""

from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QContextMenuEvent,
    QCursor,
    QFont,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsPolygonItem,
    QGraphicsScene,
    QGraphicsView,
    QMenu,
    QToolBar,
    QWidget,
)

from . import boxes, widgets
from .i18n import tr
from .review import PageState

if TYPE_CHECKING:
    import numpy as np

MIN_BOX = 8  # 手で追加する範囲の最小の幅・高さ（作業画像のピクセル）
# 押したまま動かしたとみなす距離（画面の画素）。枠の中のドラッグで動かすときと、右のボタンのドラッグでスクロールする
# ときに使う。Windows の既定（ドラッグを始める距離の SM_CXDRAG）と同じ値で、どの PC でも同じに動かすため、
# QApplication.startDragDistance（環境で変わる。この開発の環境では10）は使わない
DRAG_DISTANCE = 4
ZOOM_IN, ZOOM_OUT = 1.25, 0.8  # 「拡大」「縮小」の倍率

COLOR_AUTO = QColor(255, 140, 0)
COLOR_MANUAL = QColor(30, 110, 230)
COLOR_OFF = QColor(120, 120, 120)
FILL_ON = QColor(0, 0, 0, 110)
# 削除したページ（墨消しPDFに入れない）の表示：ページの画像を薄くし、枠を灰色の点線にし、左上に文を出す
# （画面を見ただけで、墨消しPDFに入らないことが分かるようにする）
DELETED_OPACITY = 0.35
DELETED_BANNER = "削除したページ（墨消しPDFに入れません）"


class BoxItem(QGraphicsPolygonItem):
    """範囲の表示。長方形の範囲も、長方形でない範囲（角のある範囲）も、角を結んだ形で描く"""

    HANDLE = 10  # 角をつかめる範囲（画面のピクセル）

    def __init__(self, box: boxes.Box, deleted: bool = False):
        super().__init__()
        self.box = box
        self.deleted = (
            deleted  # 削除したページの枠（墨消ししないので、灰色の点線にする）
        )
        self.setAcceptHoverEvents(True)
        self.setZValue(1)
        self.refresh()

    def rect(self) -> QRectF:
        """表示している形を囲む長方形（長方形の範囲は、その範囲）"""
        return self.polygon().boundingRect()

    def setRect(self, r: QRectF) -> None:
        self.setPolygon(
            QPolygonF([r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft()])
        )

    def refresh(self):
        b = self.box
        self.setPolygon(QPolygonF([QPointF(x, y) for x, y in b.polygon()]))
        if b.source == boxes.Source.ERASE:  # 様式の編集の画面の、型の画像から消す範囲
            pen = QPen(COLOR_OFF, 2)
            pen.setCosmetic(True)
            self.setPen(pen)
            self.setBrush(QBrush(COLOR_OFF, Qt.BrushStyle.BDiagPattern))
            self.setToolTip(
                tr(
                    "{kind}：見本の画像から消す範囲（墨消しには使わない）",
                    kind=boxes.kind_text(b.kind),
                )
            )
            return
        if b.active and not self.deleted:
            color = COLOR_MANUAL if b.source == boxes.Source.MANUAL else COLOR_AUTO
            pen = QPen(color, 3)
            self.setBrush(QBrush(FILL_ON))
        else:
            pen = QPen(COLOR_OFF, 2, Qt.PenStyle.DashLine)
            self.setBrush(Qt.BrushStyle.NoBrush)
        pen.setCosmetic(True)  # 拡大しても線の太さを変えない
        self.setPen(pen)
        kind = boxes.kind_text(b.kind)
        if self.deleted:
            self.setToolTip(
                tr("{kind}：{banner}", kind=kind, banner=tr(DELETED_BANNER))
            )
            return
        # 範囲の状態（右クリックのメニューの、操作の「墨消しする」「墨消ししない」とは英語を分ける）
        state = tr("墨消しする" if b.active else "墨消ししない", context="状態")
        self.setToolTip(
            tr("{kind}：{state}（クリックで切り替え）", kind=kind, state=state)
        )

    def corner_at(self, p: QPointF, scale: float) -> int | None:
        """p の近くの角の番号（長方形の範囲は左上・右上・右下・左下の順。長方形でない範囲は角の並びの順）"""
        tol = self.HANDLE / scale
        for i, c in enumerate(self.polygon()):
            if abs(p.x() - c.x()) <= tol and abs(p.y() - c.y()) <= tol:
                return i
        return None

    def near(self, p: QPointF, scale: float) -> bool:
        """p が範囲の中か、範囲の辺から角をつかめる距離の中にある"""
        tol = self.HANDLE / scale
        if self.box.corners is None:
            return self.rect().adjusted(-tol, -tol, tol, tol).contains(p)
        if self.polygon().containsPoint(p, Qt.FillRule.OddEvenFill):
            return True
        pts = list(self.polygon())
        n = len(pts)
        return any(_distance(p, pts[i], pts[(i + 1) % n]) <= tol for i in range(n))

    def hoverMoveEvent(self, event):
        views = self.scene().views()
        scale = views[0].transform().m11() if views else 1.0
        corner = self.corner_at(event.pos(), scale)
        if corner is not None and len(self.polygon()) != 4:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif corner in (0, 2):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif corner in (1, 3):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)


class PageView(QGraphicsView):
    toggled = Signal(object)
    added = Signal(tuple)
    moved = Signal(object, tuple, tuple)
    resized = Signal(object, tuple, tuple)
    delete_requested = Signal(object)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor(90, 90, 90))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.state: PageState | None = None
        self.items: list[BoxItem] = []
        self.pixmap_item = None  # ページの画像
        self.mode = None
        self.temp = None
        self.image_rect = QRectF()
        self.fit_on_resize = True
        # 様式の編集の画面で使う。右クリックのメニューは、どの範囲も「この範囲を削除」だけにする
        self.edit_mode = False
        # 範囲の操作を受け付けない（範囲を描く画面で、保存を待つあいだ）
        self.locked = False
        # 右のボタンのドラッグでスクロールする：押した位置（画面の上）とスクロールバーの値。動かしたか（panned）。
        # panned は、ボタンを離した後に届く右クリックの合図（Windows では離したときに届く）まで持ち、メニューを
        # 出さないために使う
        self.pan: tuple[QPointF, int, int] | None = None
        self.panned = False

    # 表示

    def show_page(
        self, state: PageState | None, message: str = "", work: np.ndarray | None = None
    ):
        """state のページを表示する（state が None か解析の結果がなければ message を出す）。work は作業画像で、省くと
        解析の結果の作業画像（墨消しの画面は、作業画像をキャッシュに持って渡す）"""
        self.scene().clear()
        self.items = []
        self.pixmap_item = None
        # ドラッグの途中なら、その操作を捨てる。描いていた枠・動かしていた枠は、上の clear で消えたため
        # （残すと、マウスを離したときに、消えた枠を触ってエラーになるため）
        self.mode, self.temp = None, None
        self.state = state
        if state is None or state.result is None:
            # 部品の書体をコピーし、大きさだけ変える。名前を空にすると、Windows では古いビットマップの書体
            # （MS Sans Serif）に置き換わり、DirectWrite で扱えずに Qt の警告が出るため（異常終了の記録で確認した）
            font = QFont(self.font())
            font.setPointSize(14)
            text = self.scene().addText(message, font)
            text.setDefaultTextColor(QColor("white"))
            self.setSceneRect(text.boundingRect())
            return
        if work is None:
            work = state.result.work
        h, w = work.shape
        image = QImage(work.data, w, h, w, QImage.Format.Format_Grayscale8).copy()
        self.pixmap_item = self.scene().addPixmap(QPixmap.fromImage(image))
        if state.deleted:
            self.pixmap_item.setOpacity(DELETED_OPACITY)
        self.image_rect = QRectF(0, 0, w, h)
        self.setSceneRect(self.image_rect)
        self.refresh_boxes()
        if self.fit_on_resize:
            self.fit()

    def refresh_boxes(self):
        for item in self.items:
            self.scene().removeItem(item)
        self.items = []
        if self.mode in (
            "press",
            "move",
            "resize",
            "corner",
        ):  # 動かしていた枠は、いま取り除いた
            self.mode = None
        if self.state is None or self.state.result is None:
            return
        for box in self.state.result.boxes:
            item = BoxItem(box, deleted=self.state.deleted)
            self.scene().addItem(item)
            self.items.append(item)

    def fit(self):
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.fit_on_resize = True

    def zoom(self, factor: float):
        self.scale(factor, factor)
        self.fit_on_resize = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_on_resize:
            self.fit()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1.15 ** (event.angleDelta().y() / 120))
        else:
            super().wheelEvent(event)

    # マウスの操作

    def _scale(self) -> float:
        return self.transform().m11()

    def _item_at(self, p: QPointF) -> BoxItem | None:
        for item in reversed(self.items):  # 後から描いたものを優先
            if item.near(p, self._scale()):
                return item
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton and self.mode is None:
            # 範囲の操作ではないので、止めているあいだや削除したページでもスクロールできる
            self.pan = (
                event.position(),
                self.horizontalScrollBar().value(),
                self.verticalScrollBar().value(),
            )
            self.panned = False
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton or not self.items_ready():
            return super().mousePressEvent(event)
        p = self.mapToScene(event.position().toPoint())
        self.start = p
        item = self._item_at(p)
        if item is not None:
            self.item = item
            # 押したときの形（長方形の範囲は長方形、長方形でない範囲は角の並び）
            self.orig = (
                QRectF(item.rect())
                if item.box.corners is None
                else QPolygonF(item.polygon())
            )
            self.corner = item.corner_at(p, self._scale())
            if self.corner is None:
                self.mode = "press"
            else:
                # 長方形の範囲は、向かいの角を止めて大きさを変え、長方形でない範囲は、つかんだ角だけを動かす
                self.mode = "resize" if item.box.corners is None else "corner"
        elif self.image_rect.contains(p):
            self.mode = "draw"
            pen = QPen(COLOR_MANUAL, 2, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            self.temp = self.scene().addRect(QRectF(p, p), pen)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        """素早い2回目のクリックも、1回のクリックとして扱う（2回目はダブルクリックとして届くので、そのままでは
        切り替わらず、すぐに戻した欄が「墨消ししない」のまま残るため）"""
        self.mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.pan is not None and not event.buttons() & Qt.MouseButton.RightButton:
            # 離したことが届かなかった（右クリックのメニューが受け取ったときなど）
            self._end_pan()
        if self.pan is not None:
            self._pan_to(event.position())
            event.accept()
            return
        p = self.mapToScene(event.position().toPoint())
        if (
            self.mode == "press"
            and (p - self.start).manhattanLength() > DRAG_DISTANCE / self._scale()
        ):
            self.mode = "move"
        if self.mode == "move" and isinstance(self.orig, QPolygonF):
            self.item.setPolygon(self.orig.translated(p - self.start))
        elif self.mode == "move":
            r = QRectF(self.orig)
            r.translate(p - self.start)
            self.item.setRect(r)
        elif self.mode == "corner":
            points = list(self.orig)
            points[self.corner] = p
            self.item.setPolygon(QPolygonF(points))
        elif self.mode == "resize":
            r = self.orig
            fixed = [r.bottomRight(), r.bottomLeft(), r.topLeft(), r.topRight()][
                self.corner
            ]
            self.item.setRect(QRectF(fixed, p).normalized())
        elif self.mode == "draw":
            self.temp.setRect(
                QRectF(self.start, p).normalized().intersected(self.image_rect)
            )
        else:
            super().mouseMoveEvent(event)

    def _pan_to(self, pos: QPointF) -> None:
        """右のボタンを押したまま pos まで動かした：DRAG_DISTANCE を超えて動かしたら、手の形にしてスクロールする"""
        start, h, v = self.pan
        d = pos - start
        if not self.panned and d.manhattanLength() > DRAG_DISTANCE:
            self.panned = True
            # ページの表示の形（範囲の上の指の形など）は、スクロールでマウスの下の範囲が変わるたびに範囲が決め直す
            # ので、アプリ全体の形にする
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
        if self.panned:
            self.horizontalScrollBar().setValue(h - round(d.x()))
            self.verticalScrollBar().setValue(v - round(d.y()))

    def _end_pan(self) -> None:
        if self.pan is not None and self.panned:
            QApplication.restoreOverrideCursor()
        self.pan = None

    def hideEvent(self, event):
        """スクロールの途中で隠れたら（画面を閉じたときなど）、マウスの形を戻す"""
        self._end_pan()
        super().hideEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton and self.pan is not None:
            self._end_pan()
            event.accept()
            return
        mode, self.mode = self.mode, None
        if mode == "press":
            self.toggled.emit(self.item.box)
        elif mode in ("move", "corner") and isinstance(self.orig, QPolygonF):
            # 辺が交わる形（角を向かいの辺の外まで動かしたとき）と、小さすぎる形は、動かす前に戻す（長方形の範囲の
            # MIN_BOX と同じく、何もしない）
            moved = _points(self.item.polygon())
            new = (
                _clipped(self.item.polygon(), self.image_rect) if _simple(moved) else ()
            )
            self.item.setPolygon(self.orig)
            old = _points(self.orig)
            r = boxes.bounds(new) if len(new) >= 3 else (0, 0, 0, 0)
            if r[2] >= MIN_BOX and r[3] >= MIN_BOX and _simple(new) and new != old:
                signal = self.moved if mode == "move" else self.resized
                signal.emit(self.item.box, old, new)
        elif mode in ("move", "resize"):
            new = self.item.rect().intersected(self.image_rect)
            self.item.setRect(self.orig)
            if new.width() >= MIN_BOX and new.height() >= MIN_BOX and new != self.orig:
                signal = self.moved if mode == "move" else self.resized
                signal.emit(self.item.box, _tuple(self.orig), _tuple(new))
        elif mode == "draw":
            r = self.temp.rect()
            self.scene().removeItem(self.temp)
            self.temp = None
            if r.width() >= MIN_BOX and r.height() >= MIN_BOX:
                self.added.emit(_tuple(r))
        else:
            super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        if self.panned and event.reason() == QContextMenuEvent.Reason.Mouse:
            # 右のボタンでドラッグしてスクロールした後の合図（Windows では、ボタンを離したときに届く）
            self.panned = False
            return
        if not self.items_ready():
            return
        item = self._item_at(self.mapToScene(event.pos()))
        if item is None:
            return
        # 押したときに合図が届く環境（Windows のほか）では、メニューを出すと、ボタンを離したことがメニューに届くので、
        # ここでスクロールを終える
        self._end_pan()
        menu = QMenu(self)
        toggle = delete = None
        if not self.edit_mode:
            toggle = menu.addAction(
                tr("墨消ししない" if item.box.active else "墨消しする")
            )
        # 自動の候補は削除せず、解除で扱う
        if self.edit_mode or item.box.source == boxes.Source.MANUAL:
            delete = menu.addAction(tr("この範囲を削除"))
        state = self.state
        chosen = menu.exec(event.globalPos())
        # メニューを出しているあいだに、表示しているページが変わったり、枠が作り直されたりしていれば、何もしない
        # （前のページの枠に効いてしまうため）
        if chosen is None or self.state is not state or item not in self.items:
            return
        if toggle is not None and chosen == toggle:
            self.toggled.emit(item.box)
        elif delete is not None and chosen == delete:
            self.delete_requested.emit(item.box)

    def items_ready(self) -> bool:
        """範囲を操作できる。削除したページでは操作しない。止めているあいだ（locked）も操作しない"""
        return (
            not self.locked
            and self.state is not None
            and self.state.result is not None
            and not self.state.deleted
        )


def _tuple(r: QRectF) -> tuple[float, float, float, float]:
    return (r.x(), r.y(), r.width(), r.height())


def _points(poly: QPolygonF) -> tuple[boxes.Point, ...]:
    """多角形の角の並び。閉じるために繰り返した最後の角と、続けて同じ位置にある角を除く"""
    out: list[boxes.Point] = []
    for p in poly:
        q = (p.x(), p.y())
        if not out or q != out[-1]:
            out.append(q)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return tuple(out)


def _clipped(poly: QPolygonF, area: QRectF) -> tuple[boxes.Point, ...]:
    """多角形の、area（ページの画像）の中の部分の角の並び（長方形の範囲を画像の中に切るのと同じく、外に出た部分を
    除く。切った辺の分、角が増えることがある）"""
    if area.contains(poly.boundingRect()):
        return _points(poly)
    return _points(poly.intersected(QPolygonF(area)))


def _simple(points: tuple[boxes.Point, ...]) -> bool:
    """角の並びが3つ以上で、隣り合わない辺どうしが交わらない（辺が交わると、塗る形が8の字のようになるため）"""
    n = len(points)
    if n < 3:
        return False

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def crosses(p1, p2, q1, q2):
        d1, d2 = cross(q1, q2, p1), cross(q1, q2, p2)
        d3, d4 = cross(p1, p2, q1), cross(p1, p2, q2)
        return d1 * d2 < 0 and d3 * d4 < 0

    edges = [(points[i], points[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue  # 隣り合う辺は、角を共にする
            if crosses(*edges[i], *edges[j]):
                return False
    return True


def _distance(p: QPointF, a: QPointF, b: QPointF) -> float:
    """点 p から線分 a-b までの距離"""
    dx, dy = b.x() - a.x(), b.y() - a.y()
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else ((p.x() - a.x()) * dx + (p.y() - a.y()) * dy) / length
    t = min(max(t, 0.0), 1.0)
    return ((p.x() - a.x() - t * dx) ** 2 + (p.y() - a.y() - t * dy) ** 2) ** 0.5


def zoom_actions(view: PageView, parent: QWidget) -> list[tuple[QAction, str]]:
    """ページの表示の「全体を表示」「拡大」「縮小」の操作と、記号の名前（widgets.glyph_icon）。説明とキーは、墨消しの
    画面・範囲を描く画面・様式の編集の画面で同じにする"""
    items = []
    for text, slot, keys, icon in (
        ("全体を表示", view.fit, ["Ctrl+0"], "fit"),
        ("拡大", lambda: view.zoom(ZOOM_IN), ["Ctrl++", "Ctrl+="], "zoom_in"),
        ("縮小", lambda: view.zoom(ZOOM_OUT), ["Ctrl+-"], "zoom_out"),
    ):
        a = QAction(tr(text), parent)
        a.triggered.connect(slot)
        a.setShortcuts([QKeySequence(k) for k in keys])
        a.setToolTip(tr("{text}（{key}）", text=tr(text), key=keys[0]))
        items.append((a, icon))
    return items


def zoom_bar(view: PageView, parent: QWidget) -> QToolBar:
    """「全体を表示」「拡大」「縮小」を並べたツールバー（範囲を描く画面・様式の編集の画面のページの表示の上に置く。
    記号の書体がなければ文字だけ）"""
    bar = QToolBar(parent)
    bar.setMovable(False)
    items = zoom_actions(view, parent)
    for a, _ in items:
        bar.addAction(a)
    widgets.apply_tool_icons(
        bar, [(a, name, True) for a, name in items], parent.palette()
    )
    return bar
