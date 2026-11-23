# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""90度単位のページの向きの判定

1. 文字の行の位置を検出し、横長の行と縦長の行のどちらが多いかで、90度回すかを決める。
2. 横長の行を切り出し、行ごとの向き判定（0度か180度か）の確からしさの合計で、上下を決める。
"""

from dataclasses import dataclass

import cv2
import numpy as np
from rapidocr.utils.process_img import get_rotate_crop_image

from . import ocr

MIN_LINES = 10  # これより文字の行が少ないページは、向きを判定しない
MIN_RATIO = 2.0  # 判定の根拠の比がこれより小さいと、自信がないとみなす


@dataclass(frozen=True)
class Orientation:
    quarter_turns: int  # 正しい向きにするための反時計回りの90度回転の回数（0〜3）
    confident: bool  # False のときは画面で向きを確かめてもらう
    wide: int
    tall: int
    up: float
    down: float


def detect(gray: np.ndarray, long_side: int = 1280) -> Orientation:
    h, w = gray.shape[:2]
    f = long_side / max(h, w)
    # 縮めた大きさは1画素以上にする（縦横比のとても大きい画像で0になると、cv2.error になるため）
    size = (max(1, int(w * f)), max(1, int(h * f)))
    small = cv2.resize(gray, size, interpolation=cv2.INTER_AREA)
    small = cv2.cvtColor(small, cv2.COLOR_GRAY2BGR)
    lines, wide, tall = _line_shapes(small)
    turns = 0
    if tall > wide:  # 縦長の行が多い → 90度回っている
        small = np.ascontiguousarray(np.rot90(small, 1))
        turns = 1
        lines, _, _ = _line_shapes(small)
    crops = [get_rotate_crop_image(small, b.astype(np.float32)) for b in lines]
    res = ocr.classify_lines(crops) if crops else []
    up = float(sum(s for label, s in res if label == "0"))
    down = float(sum(s for label, s in res if label == "180"))
    if down > up:
        turns += 2
    confident = (
        len(res) >= MIN_LINES
        and max(wide, tall) >= MIN_RATIO * max(min(wide, tall), 1)
        and max(up, down) >= MIN_RATIO * max(min(up, down), 1e-6)
    )
    return Orientation(turns % 4, confident, wide, tall, up, down)


def _line_shapes(img: np.ndarray):
    """文字の行の枠のうち横長のものと、横長・縦長の行の数を返す"""
    boxes = ocr.find_lines(img).boxes
    if boxes is None:
        return [], 0, 0
    wide = tall = 0
    keep = []
    for b in boxes:
        bw = np.linalg.norm(b[1] - b[0])
        bh = np.linalg.norm(b[2] - b[1])
        if bw > 1.5 * bh:
            wide += 1
            keep.append(b)
        elif bh > 1.5 * bw:
            tall += 1
    return keep, wide, tall
