# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""範囲が隣の行に置かれている見込みの高い欄の見分け（rowshift）のテスト（1ページの解析に数秒かかる）"""

from dataclasses import replace

import cv2
import numpy as np
import pytest
from scan import page_image, written_page
from userforms import bundled_forms

from sumikeshi_studio import align, forms, imaging, pipeline, rowshift

PAD = 14  # 動かす帯の、欄の辺からの余裕
# 公開様式の画像（tests/data。samples/ の PDF を描いたもの）と、その見本の様式
HOKEN = ("2026bun_04", "nta-hoken-r8")
KIHAI = ("2026bun_06", "nta-kihai-r8")


def form_of(form_id: str):
    return {s.id: s for s in bundled_forms()}[form_id]


def rows_moved(img: np.ndarray, fields, k: int, d: int, wide: bool) -> np.ndarray:
    """欄 k を含む矩形の中で、欄とその上下 PAD 画素の帯を縦に d 画素動かし、帯と矩形の端のあいだを伸び縮みさせた
    画像（用紙の一部の行だけがずれた形）。矩形は、欄の左右に70画素、上下に欄の高さの1.6倍か70画素の大きいほうを
    足し（wide なら、左右に250画素、上下に220画素）、途中で切れるほかの欄を含むまで広げる。矩形の外は動かない"""
    f = fields[k]
    mx, my = (250, 220) if wide else (70, max(1.6 * f.h, 70))
    r = [f.x - mx, f.y - my, f.x + f.w + mx, f.y + f.h + my]
    for _ in range(4):
        for g in fields:
            if (
                g.x + g.w + PAD <= r[0]
                or g.x - PAD >= r[2]
                or g.y + g.h + PAD <= r[1]
                or g.y - PAD >= r[3]
            ):
                continue
            r = [
                min(r[0], g.x - PAD - 2),
                min(r[1], g.y - PAD - 2),
                max(r[2], g.x + g.w + PAD + 2),
                max(r[3], g.y + g.h + PAD + 2),
            ]
    x0, y0, x1, y1 = (round(v) for v in r)
    lo, hi = f.y - PAD, f.y + f.h + PAD
    out = np.arange(y0, y1, dtype=np.float32)
    src = np.interp(out, [y0, lo + d, hi + d, y1], [y0, lo, hi, y1])
    map_x = np.tile(np.arange(x1 - x0, dtype=np.float32), (y1 - y0, 1))
    map_y = np.tile((src.astype(np.float32) - y0)[:, None], (1, x1 - x0))
    moved = img.copy()
    moved[y0:y1, x0:x1] = cv2.remap(
        img[y0:y1, x0:x1],
        map_x,
        map_y,
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return moved


def analyze(img: np.ndarray) -> pipeline.PageResult:
    rgb = np.ascontiguousarray(cv2.cvtColor(img, cv2.COLOR_GRAY2RGB))
    return pipeline.analyze(
        imaging.MemoryPage(rgb, 200.0, "p"), formset=bundled_forms()
    )


@pytest.mark.parametrize(
    ("page", "k", "rows", "wide", "touch"),
    [
        # 保険料控除申告書の1欄を1行上へ
        (HOKEN, 0, -1, False, False),
        # 基・配・特・所の申告書の4欄のうち、所得金額調整控除の欄を1行上へ
        (KIHAI, 3, -1, False, False),
        # 同じ申告書の特定親族 1 の欄を1行下へ。線に接する高さの数字を全部の欄に書く（欄の中と枠の線を照合に
        # 含めると、記入と線で値が下がり、拾えない）
        (KIHAI, 1, 1, True, True),
    ],
    ids=["hoken", "kihai-adjustment", "kihai-relative-touching-digits"],
)
def test_field_on_adjacent_row_is_named_in_review(
    page, k, rows, wide, touch, monkeypatch
):
    """用紙の一部の行がちょうど1行ずれると、範囲を置いた位置にも隣の行の同じ形の枠の線が来て、ほかの確かめでは
    要確認が付かない。その欄の名前だけを挙げた要確認を付け、範囲は動かさない"""
    sample, form_id = page
    spec = form_of(form_id)
    f = spec.fields[k]
    img = page_image(sample)
    if touch:
        img = written_page(sample, 200, analyze(img).boxes, "touch")
    img = rows_moved(img, spec.fields, k, rows * round(f.h), wide)
    res = analyze(img)
    assert not res.variant
    assert res.note == f"「{f.name}」の欄の範囲が、欄に合っていないおそれがあります"
    monkeypatch.setattr(rowshift, "shifted", lambda *args: [])
    bare = analyze(img)
    assert bare.note == ""
    assert [b.rect() for b in bare.boxes] == [b.rect() for b in res.boxes]


@pytest.mark.parametrize("page", [HOKEN, KIHAI], ids=["hoken", "kihai"])
def test_field_on_its_row_is_not_named(page):
    """行がずれていない用紙では、どの欄も挙げない"""
    sample, form_id = page
    res = analyze(page_image(sample))
    assert res.used[0].id == form_id
    assert res.note == ""


def test_failure_keeps_placed_ranges(monkeypatch):
    """照合に失敗しても、ページの解析は失敗にせず、置いた範囲をそのまま使う"""
    sample, _ = HOKEN
    img = page_image(sample)
    before = analyze(img)

    def fail(*args):
        raise ValueError("試験の失敗")

    monkeypatch.setattr(rowshift, "shifted", fail)
    res = analyze(img)
    assert res.note == ""
    assert [b.rect() for b in res.boxes] == [b.rect() for b in before.boxes]


def test_search_is_centered_on_placed_range():
    """照合は、変換で移した位置でなく、置いた範囲の位置を中心に行う（枠の線に合わせたり、罫線の対応で置き直したり
    すると、範囲は変換で移した位置から動く）。行がずれていない用紙でも、範囲を1行下に置いた欄は挙げる"""
    sample, form_id = KIHAI
    gray = page_image(sample)
    tpl = align.templates([form_of(form_id)])[0]
    H, _ = align.register(tpl, align.page_features(gray))
    placed = [forms.move_field(f, H) for f in tpl.fields]
    assert rowshift.shifted(gray, tpl, H, placed) == []
    f = placed[0]
    down = [replace(f, y=f.y + f.h), *placed[1:]]
    assert rowshift.shifted(gray, tpl, H, down) == [f.name]


def test_shifted_names_join_field_note():
    """隣の行に置かれている見込みの高い欄は、ほかの欄の要確認と同じ1つの文に、枠の線を越えた記入の欄の次に挙げる。
    枠の線が見つからない欄や線が傾いた欄と重なる欄は、1回だけ挙げる"""
    fields = [forms.Field(n, 0, 0, 10, 10) for n in ("甲", "乙", "丙", "丁")]
    placed = forms._Placed(
        fields,
        unframed=["丙"],
        crossed=["甲"],
        frameless=[],
        tilted=["乙", "丙"],
        shifted=["乙"],
        photo=False,
    )
    assert forms._field_notes(placed) == [
        "「甲」「乙」「丙」の欄の範囲が、欄に合っていないおそれがあります"
    ]
    placed = placed._replace(crossed=[], tilted=[], shifted=["乙", "丙"])
    assert forms._field_notes(placed) == [
        "「乙」「丙」の欄の範囲が、欄に合っていないおそれがあります"
    ]
