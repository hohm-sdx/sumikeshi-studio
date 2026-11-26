# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""墨消しする範囲が個人番号欄に合っているかを確かめる関数（テスト用）

試験画像（スキャン・写真に見立てたもの）の中の欄の位置は、紙を置いたときの変換から分かる。
傾けずに画像にしたページを解析した墨消しする範囲（conftest.py の reference）を、その変換と、ツールの補正
（imaging.render・imaging.correct）と同じ計算で作業画像の上に移し、試験画像を解析した墨消しする範囲と比べる。
墨消しは広げず、印刷された枠の線の中心に合わせる。
"""

import dataclasses

import cv2
import numpy as np

from sumikeshi_studio import export, imaging

# 枠の線に合わせた辺のずれとして許す画素。傾けないページと試験画像で、それぞれ線の中心を見つけたもの同士を
# 比べる（試作では、見つけた線の中心と正解の線の中心の差は最大1.7画素）
TOLERANCE = 2.0
# 枠の線が見つからない範囲は、見本の位置のまま墨消しするので、位置合わせのずれとして許す画素
# （試作で、余白を引いた見本の欄の辺と枠の線の内側の縁の差の99%点は5.0〜5.9画素）
UNFRAMED_TOLERANCE = 6.0


def jpeg_gray(image: export.PageImage) -> np.ndarray:
    """墨消しして JPEG にした1ページを、グレーの画像に戻す"""
    return cv2.imdecode(np.frombuffer(image.jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)


def left_digits(res, filled, blank):
    """番号を書いた用紙の出力と、番号を書かない用紙を同じ範囲で墨消しした出力の差（墨消しの外に残った数字の画素）、
    書いた数字の画素の数、墨消しの範囲"""
    job = export.PageJob.of(filled, res)
    jobs = (job, dataclasses.replace(job, source=blank))
    masked = [jpeg_gray(export.mask_page(j)) for j in jobs]
    plain = [
        jpeg_gray(export.mask_page(dataclasses.replace(j, rects=(), polygons=())))
        for j in jobs
    ]
    left = np.abs(masked[0].astype(int) - masked[1]) > 64
    digit = np.abs(plain[0].astype(int) - plain[1]) > 64
    return left, int(digit.sum()), job


def to_work(pts, photo_shape, page, res):
    """試験画像の上の点を、作業画像の上の点に移す（imaging.render と imaging.correct と同じ計算）"""
    h, w = photo_shape
    dpi = imaging.resolve(page).dpi
    if abs(imaging.WORK_DPI - dpi) > 0.5:
        f = imaging.WORK_DPI / dpi
        pts = pts * f
        w, h = round(w * f), round(h * f)
    for _ in range(res.quarter_turns % 4):  # 反時計回りに90度
        pts = np.stack([pts[:, 1], w - pts[:, 0]], axis=1)
        w, h = h, w
    if abs(res.skew) >= 0.05:
        m = cv2.getRotationMatrix2D((w / 2, h / 2), res.skew, 1.0)
        c, s = abs(m[0, 0]), abs(m[0, 1])
        m[0, 2] += (round(h * s + w * c) - w) / 2
        m[1, 2] += (round(h * c + w * s) - h) / 2
        pts = np.hstack([pts, np.ones((len(pts), 1))]) @ m.T
    return pts


def assert_covered(ref_boxes, M, photo_shape, page, res, start=0, unframed=False):
    """ページの上の墨消しする範囲（ref_boxes）を試験画像の作業画像の上に移したものと、試験画像を解析した墨消しする
    範囲（res.boxes の start 番目から）が、上下左右とも TOLERANCE 以内で一致する（欄を覆い、はみ出さない）

    印刷された枠の線が見つからない範囲（res.unframed）は、見本の位置のまま墨消しするので、UNFRAMED_TOLERANCE 以内で
    比べる。unframed が偽なら、そういう範囲があってはいけない。角のある範囲（写真で、線の中心を結んだ四隅）は、
    移した範囲の四隅と、角ごとに TOLERANCE 以内で一致する（傾きの分の許しを足さない）。
    """
    got = res.boxes[start : start + len(ref_boxes)]
    assert [b.kind for b in got] == [b.kind for b in ref_boxes]
    for ref, box in zip(ref_boxes, got):
        tol = TOLERANCE
        if box.kind in res.unframed:
            assert unframed, f"枠の線が見つからない：{box.kind}"
            assert box.corners is None, box.kind  # 線が見つからない欄の範囲は長方形
            tol = UNFRAMED_TOLERANCE
        corners = np.float32([[ref.x, ref.y], [ref.x + ref.w, ref.y],
                              [ref.x + ref.w, ref.y + ref.h], [ref.x, ref.y + ref.h]])  # fmt: skip
        photo = cv2.perspectiveTransform(corners[None], M)[0]
        q = to_work(photo, photo_shape, page, res)
        if box.corners is not None:
            assert len(box.corners) == 4, box.kind
            for want, have in zip(q, box.corners, strict=True):
                gap = float(np.hypot(want[0] - have[0], want[1] - have[1]))
                assert gap <= tol, (box.kind, tuple(want), have)
            continue
        # 移した範囲は少し傾くので、辺ごとに、範囲の辺から最も遠い角との差を見る
        sides = [
            (q[:, 0].min(), box.x),
            (q[:, 0].max(), box.x + box.w),
            (q[:, 1].min(), box.y),
            (q[:, 1].max(), box.y + box.h),
        ]
        for want, have in sides:
            assert abs(want - have) <= tol + _tilt(q), (box.kind, want, have)


def _tilt(q: np.ndarray) -> float:
    """移した範囲の傾きで、角の位置が辺からずれる量（作業画像の傾きの補正の残り。0.1度でも幅400画素で0.7画素）"""
    dx = abs(q[1, 1] - q[0, 1])  # 上の辺の両端の高さの差
    dy = abs(q[3, 0] - q[0, 0])  # 左の辺の両端の横の差
    return max(dx, dy)


def ref_to_work(M, photo_shape, page, res) -> np.ndarray:
    """ページの画像（傾けない 200dpi）の座標から、試験画像の作業画像の座標への射影変換（3×3）"""
    src = np.float32([[0, 0], [1000, 0], [1000, 1000], [0, 1000]])
    work = to_work(cv2.perspectiveTransform(src[None], M)[0], photo_shape, page, res)
    return cv2.getPerspectiveTransform(src, np.float32(work))


def number_left_cells(left, work_shape, ref_boxes, M, photo_shape, page, res):
    """墨消しの外に残った数字の画素（出力の画像の上の真偽の配列 left）が、どの欄の、12等分したます目の何番目
    （0〜11。欄の左右の外は、近い端のます目とする）にあたるかを、画素ごとに (欄の番号, ます目の番号) で返す

    出力の画像の画素を作業画像の座標に縮め、ページの画像の座標に戻して、いちばん近い欄で決める。ます目は、
    欄（ref_boxes。印刷された枠の線の中心に合わせたもの）を12等分したもの。
    """
    pts = _to_page(left, work_shape, M, photo_shape, page, res)
    cells = []
    for x, y in pts:
        # 点から欄の長方形までの距離（中なら0）がいちばん小さい欄
        dist = [
            np.hypot(max(b.x - x, 0, x - b.x - b.w), max(b.y - y, 0, y - b.y - b.h))
            for b in ref_boxes
        ]
        i = int(np.argmin(dist))
        b = ref_boxes[i]
        cells.append((i, int(np.clip((x - b.x) // (b.w / 12), 0, 11))))
    return cells


def beyond_line(changed, work_shape, M, photo_shape, page, res, line, side, span):
    """墨消しで黒くなった画素（出力の画像の上の真偽の配列 changed）が、ページの画像の上の枠の線の外側の縁 line
    （side が bottom なら y、right なら x など）を越える量の最大（ページの画像の画素。越えなければ0以下）。
    span は、線に沿った向きの、調べる範囲（ページの画像の座標）"""
    pts = _to_page(changed, work_shape, M, photo_shape, page, res)
    if not len(pts):
        return -np.inf
    along = pts[:, 0] if side in ("top", "bottom") else pts[:, 1]
    across = pts[:, 1] if side in ("top", "bottom") else pts[:, 0]
    keep = (span[0] <= along) & (along <= span[1])
    if not keep.any():
        return -np.inf
    over = across[keep] - line if side in ("bottom", "right") else line - across[keep]
    return float(over.max())


def _to_page(mask, work_shape, M, photo_shape, page, res) -> np.ndarray:
    """出力の画像の上の真偽の配列の真の画素（の中心）を、ページの画像（傾けない 200dpi）の座標にする"""
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return np.zeros((0, 2), np.float32)
    f = np.array([work_shape[1] / mask.shape[1], work_shape[0] / mask.shape[0]])
    work = (np.stack([xs, ys], axis=1) + 0.5) * f
    H = np.linalg.inv(ref_to_work(M, photo_shape, page, res))
    return cv2.perspectiveTransform(np.float32(work)[None], H)[0]
