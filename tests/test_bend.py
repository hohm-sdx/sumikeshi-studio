# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""曲がりの補正（bend.py）のうち、照合に使う区画の選び方と、補正で動く量の上限（forms.BEND_LIMIT）のテスト。
補正の結果は test_photos.py で確かめる"""

import numpy as np
from scan import GRAY_DPI, page_image, written_page
from userforms import bundled_forms

from sumikeshi_studio import bend, imaging, pipeline


def _printed(image: np.ndarray, y: int, x: int) -> None:
    """image の (x, y) から100画素四方に、印刷に見立てた濃淡を描く"""
    rng = np.random.default_rng(y * 1000 + x)
    image[y : y + 100, x : x + 100] = rng.integers(0, 255, (100, 100), np.uint8)


def test_usable_patches_are_recomputed_when_template_changes():
    """使える区画は見本の画像の中身ごとに求め、同じ大きさで中身の違う画像に前の結果を使わない（様式の編集で
    見本の画像を差し替えたとき）。同じ中身なら、記録した値を返す"""
    bend._PATCHES.clear()  # 記録の数の上限で、途中で消されないようにする
    a = np.full((400, 300), 255, np.uint8)
    _printed(a, 100, 100)
    b = np.full((400, 300), 255, np.uint8)
    _printed(b, 250, 50)
    first_a, first_b = bend._usable_patches(a), bend._usable_patches(b)
    assert (
        first_a and first_b and first_a != first_b
    )  # 印刷の位置で、使える区画が分かれる
    assert bend._usable_patches(a) is first_a
    assert (
        bend._usable_patches(a.copy()) is first_a
    )  # 別の配列でも、中身が同じなら記録した値


def test_field_moved_onto_same_size_neighbor_keeps_review(monkeypatch):
    """曲がりの補正で、欄が同じ大きさの隣の欄の位置まで動いたら、動いた先で隣の欄の線がすべて見つかっても補正を
    使わず、線が見つからない要確認を残し、欄の番号を墨消しの外に出さない（補正の結果を、隣の欄の位置への移動に
    置き換えて確かめる。上限がないと、要確認が消え、番号がすべて墨消しの外に出る）"""
    form = "2027bun_01"
    specs = bundled_forms()

    def page(gray):
        return imaging.MemoryPage(np.dstack([gray] * 3), float(GRAY_DPI), "test")

    ref = pipeline.analyze(page(page_image(form)), formset=specs).boxes
    first = next(b for b in ref if b.kind == "住民税 16歳未満の扶養親族 1")
    second = next(b for b in ref if b.kind == "住民税 16歳未満の扶養親族 2")
    filled = written_page(form, GRAY_DPI, [first])
    blank = written_page(form, GRAY_DPI)
    y = round(first.y)
    for img in (filled, blank):  # 1つ目の欄の上の線を消し、補正の対象にする
        img[y - 3 : y + 4, round(first.x) + 2 : round(first.x + first.w) - 2] = 255
    down = np.array([[1, 0, 0], [0, 1, second.y - first.y], [0, 0, 1.0]])

    def moved(gray, image, H, fields):
        return [H @ down if f.name == first.kind else None for f in fields]

    monkeypatch.setattr(bend, "transforms", moved)
    res = pipeline.analyze(page(filled), formset=specs)
    assert first.kind in res.unframed and res.note
    digits = (filled < 128) & (blank >= 128)
    covered = np.zeros(digits.shape, bool)
    for b in res.boxes:
        y0, y1 = max(int(b.y), 0), int(np.ceil(b.y + b.h))
        covered[y0:y1, max(int(b.x), 0) : int(np.ceil(b.x + b.w))] = True
    assert digits.sum() > 1000
    assert (digits & ~covered).sum() == 0
