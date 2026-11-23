# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""墨消しする範囲のデータ

確認画面は起動してすぐに使うため、重いライブラリ（OpenCV・numpy など）を読み込まない
（画面のモジュールが重いライブラリを読み込まないように、pipeline.py と分けている）。
"""

from dataclasses import dataclass
from enum import StrEnum

from .i18n import tr

# 手で追加した範囲の名前（Box.kind）。値はそのままにし、画面に出すときは kind_text を通す
KIND_MANUAL = "手で追加した範囲"


def kind_text(kind: str) -> str:
    """画面に出す範囲の名前（手で追加した範囲は画面の言語の文。様式で決めた欄の名前はそのまま）"""
    return tr(KIND_MANUAL) if kind == KIND_MANUAL else kind


class Source(StrEnum):
    """墨消しする範囲の出どころ"""

    AUTO = "auto"  # 型で決めた個人番号欄
    MANUAL = "manual"  # 手で追加した範囲
    # 様式の編集の画面で、型の画像から消す範囲（出力の墨消しには使わない）
    ERASE = "erase"


Point = tuple[float, float]
# 範囲の形：長方形は (x, y, 幅, 高さ)、長方形でない範囲は角の並び（(x, y) の組）
Geometry = tuple[float, float, float, float] | tuple[Point, ...]


def bounds(points) -> tuple[float, float, float, float]:
    """角の並びを囲む長方形 (x, y, 幅, 高さ)"""
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def is_corners(geometry: Geometry) -> bool:
    """範囲の形が、角の並び（長方形でない範囲）か"""
    return bool(geometry) and isinstance(geometry[0], tuple)


@dataclass(eq=False)
class Box:
    """墨消しする範囲。座標は補正後の作業画像（WORK_DPI）の上の値"""

    x: float
    y: float
    w: float
    h: float
    kind: str  # 欄の名前（「本人の個人番号」など）か、KIND_MANUAL
    source: Source
    active: bool = True  # False は「墨消ししない」（画面で解除した欄）
    # 様式の編集の画面で、印刷された枠の線がある組（formstore.Field.frame と同じ。墨消しの画面では使わない）
    frame: tuple[str, ...] | None = None
    # 長方形でない範囲の角（画面の上で時計回り。写真で、枠の線の中心に合わせた欄）。None なら x・y・w・h の長方形。
    # 角があるときの x・y・w・h は、角を囲む長方形（作るときに角から決め直す）
    corners: tuple[Point, ...] | None = None

    def __post_init__(self):
        if self.corners is not None:
            self.set_geometry(self.corners)

    def rect(self) -> tuple[float, float, float, float]:
        """範囲を囲む長方形（長方形の範囲は、その範囲）"""
        return (self.x, self.y, self.w, self.h)

    def polygon(self) -> tuple[Point, ...]:
        """範囲の角（長方形の範囲は、左上から時計回りの4つ）"""
        if self.corners is not None:
            return self.corners
        x, y, w, h = self.rect()
        return ((x, y), (x + w, y), (x + w, y + h), (x, y + h))

    def geometry(self) -> Geometry:
        """範囲の形（元に戻す操作で、前と後の形として持つ）"""
        return self.corners if self.corners is not None else self.rect()

    def set_geometry(self, geometry: Geometry) -> None:
        """範囲の形を geometry にする。角の並びなら、x・y・w・h を角を囲む長方形にする"""
        if is_corners(geometry):
            self.corners = tuple((float(x), float(y)) for x, y in geometry)
            self.x, self.y, self.w, self.h = bounds(self.corners)
        else:
            self.x, self.y, self.w, self.h = geometry
            self.corners = None
