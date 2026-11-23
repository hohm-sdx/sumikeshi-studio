# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""元に戻す操作（解除・追加・削除・移動、ページの削除）"""

from PySide6.QtGui import QUndoCommand

from .i18n import tr


class BoxCommand(QUndoCommand):
    def __init__(self, win, page, text):
        super().__init__(text)
        self.win = win
        self.page = page

    def changed(self):
        self.win.boxes_changed(self.page)


class ToggleCommand(BoxCommand):
    def __init__(self, win, page, box):
        super().__init__(win, page, tr("墨消しする・しないの切り替え"))
        self.box = box

    def redo(self):
        self.box.active = not self.box.active
        self.changed()

    undo = redo


class AddCommand(BoxCommand):
    def __init__(self, win, page, boxes, box):
        super().__init__(win, page, tr("範囲の追加"))
        self.boxes = boxes
        self.box = box

    def redo(self):
        self.boxes.append(self.box)
        self.changed()

    def undo(self):
        self.boxes.remove(self.box)
        self.changed()


class DeleteCommand(BoxCommand):
    def __init__(self, win, page, boxes, box):
        super().__init__(win, page, tr("範囲の削除"))
        self.boxes = boxes
        self.box = box
        self.pos = boxes.index(box)

    def redo(self):
        self.boxes.remove(self.box)
        self.changed()

    def undo(self):
        self.boxes.insert(self.pos, self.box)
        self.changed()


class GeometryCommand(BoxCommand):
    def __init__(self, win, page, box, old, new):
        """old・new は範囲の形（boxes.Geometry。長方形は (x, y, 幅, 高さ)、長方形でない範囲は角の並び）"""
        super().__init__(win, page, tr("範囲の移動・大きさの変更"))
        self.box = box
        self.old = old
        self.new = new

    def _set(self, geometry):
        self.box.set_geometry(geometry)
        self.changed()

    def redo(self):
        self._set(self.new)

    def undo(self):
        self._set(self.old)


class PageDeleteCommand(QUndoCommand):
    """ページの削除と、削除の取り消し

    削除の印はページ（PageState.deleted）に持たせるので、回転したときや開き直したときに元に戻す履歴が消えても残る。
    """

    def __init__(self, win, page, deleted):
        super().__init__(tr("ページの削除" if deleted else "ページの削除の取り消し"))
        self.win = win
        self.page = page
        self.deleted = deleted

    def _set(self, value):
        self.win.pages[self.page].deleted = value
        self.win.page_deleted_changed(self.page)

    def redo(self):
        self._set(self.deleted)

    def undo(self):
        self._set(not self.deleted)
