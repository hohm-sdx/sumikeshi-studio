# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""印刷された枠の線を探す処理と、枠の線を越えた記入を見つける処理（frames.py）を、描いた画像で確かめる"""

import numpy as np
import pytest

from sumikeshi_studio import frames

# 枠の内側：左 100・上 100・右 600・下 160（画素の境目）。線は欄の外まで続く表の線とする
INNER = (100, 100, 600, 160)


def page(paper=255):
    return np.full((300, 760), paper, np.uint8)


def hline(img, y, thick=2, x0=40, x1=720, value=0):
    """y から下へ thick 画素の横の線"""
    img[y : y + thick, x0:x1] = value


def vline(img, x, thick=2, y0=60, y1=240, value=0):
    img[y0:y1, x : x + thick] = value


def framed(value=0, thick=2):
    """枠の線（上の線は内側の縁が100、下の線は内側の縁が160、左は100、右は600）"""
    img = page()
    hline(img, 100 - thick, thick, value=value)
    hline(img, 160, thick, value=value)
    vline(img, 100 - thick, thick, value=value)
    vline(img, 600, thick, value=value)
    return img


def rect(dx=0.0, dy=0.0):
    """見本の欄をページに移したもの（位置合わせのずれ dx・dy を含む）"""
    left, top, right, bottom = INNER
    return (left + dx, top + dy, right - left, bottom - top)


@pytest.mark.parametrize(("dx", "dy"), [(0, 0), (5, -6), (-10, 7)])
def test_lines_are_found_within_misalignment(dx, dy):
    """位置合わせのずれ（上下 ±8画素・左右 ±12画素以内）があっても、枠の線を見つけ、線の中心に合わせる"""
    f = frames.find(framed(), rect(dx, dy))
    assert (f.top, f.bottom, f.left, f.right) == (
        (100, 98),
        (160, 162),
        (100, 98),
        (600, 602),
    )
    assert frames.fit(rect(dx, dy), f) == (99.0, 99.0, 502.0, 62.0)


def test_thick_line():
    img = page()
    hline(img, 98, 2)
    hline(img, 160, 4)  # 下の線は4画素（扶養控除等申告書の最後の欄）
    vline(img, 98)
    vline(img, 600)
    f = frames.find(img, rect())
    assert f.bottom == (160, 164)


def test_broken_line_is_found():
    """白黒2値で途切れた線（長さの半分ほどが残る）も見つける"""
    img = framed()
    for x in range(40, 720, 40):
        img[160:162, x : x + 18] = 255
    assert frames.find(img, rect()).bottom == (160, 162)


def test_light_lines_with_dark_digits():
    """紙の地より35だけ暗い線（見本のスキャン）を、濃い数字があっても見つける"""
    img = page(210)
    hline(img, 98, value=175)
    hline(img, 160, value=175)
    vline(img, 98, value=175)
    vline(img, 600, value=175)
    for i in range(12):  # 線に接する高さいっぱいの、濃い数字の画
        cx = 100 + round((i + 0.5) * 500 / 12)
        img[100:160, cx - 2 : cx + 2] = 0
    f = frames.find(img, rect())
    assert (f.top, f.bottom, f.left, f.right) == (
        (100, 98),
        (160, 162),
        (100, 98),
        (600, 602),
    )


def test_one_side_only_is_not_used():
    """上の線がなく下の線だけあれば、上も下も使わない（組にならない）"""
    img = page()
    hline(img, 160)
    vline(img, 98)
    vline(img, 600)
    f = frames.find(img, rect())
    assert f.top is None and f.bottom is None
    assert f.missing == ("top", "bottom")
    assert frames.fit(rect(), f)[1] == 100  # 見つからない辺は見本の位置のまま


def test_digits_touching_line_do_not_move_edges():
    """線に接する数字の画があっても、線の縁は動かない"""
    img = framed()
    for i in range(12):
        x0 = 100 + round(i * 500 / 12) + 8
        img[150:160, x0 : x0 + 20] = 0  # 下の線に接する数字の底の画
        img[100:108, x0 : x0 + 20] = 0  # 上の線に接する数字の上の画
    f = frames.find(img, rect())
    assert (f.top, f.bottom) == ((100, 98), (160, 162))


def test_text_row_near_line_is_not_a_line():
    """欄の中の上の注意書き（文字の並び）を、上の線とみなさない"""
    img = framed()
    for x in range(110, 590, 24):
        img[104:106, x : x + 12] = 0  # 文字の横の画の並び（線の4画素下）
    f = frames.find(img, rect(dy=4))
    assert f.top == (100, 98)


def test_digits_are_not_lines_when_lines_are_gone():
    """枠の線が消えた画像で、数字の画や並びを線とみなさない（白黒2値で細い線が消えたとき）"""
    img = page()
    for i in range(12):
        x0 = 100 + round(i * 500 / 12)
        img[100:160, x0 + 34 : x0 + 38] = (
            0  # ます目の右に寄せた数字の縦の画（右の線の近くにもある）
        )
        img[100:103, x0 + 10 : x0 + 38] = 0  # 上の線に接する数字の並び
        img[157:160, x0 + 10 : x0 + 38] = 0
    f = frames.find(img, rect())
    assert f.missing == frames.SIDES


# ---- 枠の線を越えた記入


def crossing_page():
    """枠の線と、ます目の区切りの線（下の線から上へ、欄の幅を12に分けた位置）"""
    img = framed()
    for i in range(1, 12):
        x = 100 + round(i * 500 / 12)
        img[140:160, x : x + 1] = 0
    return img


def template_of(img):
    """見本の画像をページに移したもの（ここでは同じ印刷の画像）"""
    return img.copy()


def test_stroke_crossing_bottom_line():
    img = crossing_page()
    blank = template_of(img)
    img[130:172, 200:203] = 0  # 下の線を越えて10画素外まで続く画
    f = frames.find(img, rect())
    assert frames.crossings(img, rect(), f, blank) == ("bottom",)


def test_writing_touching_from_outside_is_not_crossing():
    """下の欄に、線に外から接して書いた記入（生年月日など）は、越えた記入としない"""
    img = crossing_page()
    blank = template_of(img)
    img[162:186, 200:203] = 0
    img[162:165, 200:215] = 0
    f = frames.find(img, rect())
    assert frames.crossings(img, rect(), f, blank) == ()


def test_printed_text_touching_line_is_not_crossing():
    """線のすぐ外に印刷された文字（見本にもある）と、線に内から接する数字は、越えた記入としない"""
    img = crossing_page()
    img[162:172, 300:310] = 0  # 印刷の文字（線に接する）
    blank = template_of(img)
    img[130:160, 304:307] = 0  # 線に接して書いた数字の画
    f = frames.find(img, rect())
    assert frames.crossings(img, rect(), f, blank) == ()


def test_tick_continuing_outside_is_not_crossing():
    """区切りの線が線の外へ続く印刷（扶養控除等申告書の生年月日の段の区切り）は、越えた記入としない"""
    img = crossing_page()
    x = 100 + round(4 * 500 / 12)
    img[162:180, x : x + 1] = 0
    f = frames.find(img, rect())
    assert frames.crossings(img, rect(), f, None) == ()


def test_line_at_corner_without_crossing_line_is_not_crossing():
    """左右の線が見つからない欄で、見本の位置から10画素内側にある本当の縦の線（上の線の外へ続く）を、上の線を
    越えた記入としない（白黒2値で線が途切れた欄。直交する線が見つからない角は、線を探した範囲の分も除く）"""
    img = page()
    hline(img, 98)
    hline(img, 160)
    vline(img, 110, y0=75, y1=140)  # 左の線（右の線がないので、左右の組にならない）
    f = frames.find(img, rect())
    assert f.left is None and f.top == (100, 98)
    assert frames.crossings(img, rect(), f, None) == ()


def test_crossing_is_not_checked_where_line_is_missing():
    img = page()
    vline(img, 98)
    vline(img, 600)
    img[130:172, 200:203] = 0
    f = frames.find(img, rect())
    assert frames.crossings(img, rect(), f, None) == ()


def test_overdrawn_range_fits_lines():
    """範囲が枠の線より外まで描かれていても（例：線より4画素外まで描いた範囲）、線に合わせる。右の二重線では、
    外の線のほうが範囲の辺に近くても、内側の線を選ぶ"""
    img = framed()
    vline(img, 606)
    left, top, right, bottom = INNER
    wide = (left - 4, top - 4, right - left + 8, bottom - top + 8)
    f = frames.find(img, wide)
    assert (f.top, f.bottom, f.left, f.right) == (
        (100, 98),
        (160, 162),
        (100, 98),
        (600, 602),
    )


def test_digit_stroke_inside_is_not_chosen_over_line():
    """左に寄せて書いた数字「1」の縦の画（線の12画素内側。下の欄の同じ位置にも「1」があり、欄の外まで続いて
    見える）を、期待する位置にやや近くても、左の線とみなさない"""
    img = page()
    hline(img, 98)
    hline(img, 160)
    vline(img, 94, thick=4)  # 太い左の線（内側の縁は98）
    vline(img, 600)
    img[110:190, 109:113] = 0  # 数字の縦の画（欄の下の外まで続く）
    f = frames.find(img, (102, 100, 498, 60))
    assert f.left == (98, 94)


def test_only_requested_pairs_are_searched():
    """記録した組だけ線を探す。探さない組の辺は、線が見つからない辺に数えない"""
    f = frames.find(framed(), rect(), ("top_bottom",))
    assert (f.top, f.bottom, f.left, f.right) == ((100, 98), (160, 162), None, None)
    assert f.missing == () and f.found == ("top_bottom",)
    f = frames.find(page(), rect(), ("left_right",))
    assert f.missing == ("left", "right") and f.found == ()


@pytest.mark.parametrize("height", [10, 12])
def test_one_line_is_not_both_top_and_bottom(height):
    """高さ約12画素以下の欄を横線1本が通っても、その線を上と下の両方には選ばない（両方に選ぶと、
    墨消しの高さが0になるため）"""
    img = page()
    hline(img, 100, 3)
    f = frames.find(img, (100.0, 95.0, 200.0, float(height)), ("top_bottom",))
    assert (f.top, f.bottom) == (None, None)


def test_range_beyond_left_edge_finds_visible_lines():
    """欄が画像の左の端からはみ出しても（左の線は写っていない）、写っている上下の線を探す（切り抜く範囲の始めが負になり、
    画像の反対側を切り出して、上下の線も探さないことがあった）。左右は組にならないので使わない"""
    img = framed()[:, 200:]
    left, top, right, bottom = INNER
    f = frames.find(img, (left - 200, top, right - left, bottom - top))
    assert (f.top, f.bottom, f.left, f.right) == ((100, 98), (160, 162), None, None)


def test_crossings_with_empty_bands_do_not_crash():
    """越えた記入を調べる所が空（線が切り抜いた画像の端に接して外側・内側の帯が0行、辺の列が0）でも、強制終了しない
    （cv2.connectedComponentsWithStats は空の配列でプロセスごと終わるため）

    空の配列を渡す不具合があってもテストのプロセスごと止まらないよう、子のプロセスで動かす。
    """
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent(
        """
        import numpy as np
        from sumikeshi_studio import frames
        img = np.full((40, 300), 255, np.uint8)
        img[0:3] = 0
        img[37:40] = 0
        print(
            frames.crossings(img, (20.0, 3.0, 260.0, 34.0), frames.Frame((3, 0), (37, 40), None, None, ("top_bottom",)), None),
            frames.crossings(img, (20.0, 3.0, 1.0, 34.0), frames.Frame((3, 0), (37, 40), None, None, ("top_bottom",)), None),
            frames.crossings(img[0:0], (20.0, 3.0, 260.0, 34.0), frames.Frame((3, 0), (37, 40), None, None, ("top_bottom",)), img[0:0]),
            frames._parts(np.zeros((0, 40), bool), 3),
        )
        """
    )
    out = subprocess.run(
        [sys.executable, "-c", code], check=False, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "() () () []"
