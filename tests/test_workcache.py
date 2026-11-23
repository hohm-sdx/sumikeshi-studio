# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""墨消しの画面の作業画像のキャッシュ（workcache.py）のテスト"""

import numpy as np

from sumikeshi_studio import workcache

MIB = workcache.MIB


def image(mib: int = 1) -> np.ndarray:
    return np.zeros((mib * 1024, 1024), np.uint8)


def filled(budget_mib: int, pages: list[int], shown: list[int], current: int):
    """pages の作業画像（1つ1MB）を、予算を考えずに入れ、shown の順に表示して current を表示したキャッシュ"""
    cache = workcache.WorkCache(budget_mib * MIB)
    for i in pages:
        cache.images[i] = image()
    for i in [*shown, current]:
        cache.show(i)
    return cache


def test_budget_is_half_of_free_memory_after_reserve_within_limits():
    """予算は、空いているメモリから 384MB を引いた残りの半分を、32MB〜512MB に収めた値"""
    assert workcache.budget_for(4 * 1024 * MIB) == 512 * MIB
    assert workcache.budget_for(780 * MIB) == 198 * MIB
    assert workcache.budget_for(448 * MIB) == 32 * MIB
    assert workcache.budget_for(400 * MIB) == 32 * MIB
    assert workcache.budget_for(0) == 32 * MIB


def test_pages_are_released_behind_first_then_far_ahead_then_near():
    """手放す順：表示しているページより前のページ（表示していないもの、表示してから時間の経ったものから）→ 10ページ先
    より遠いページ（遠いものから）→ 10ページ先までのページ（遠いものから）。表示しているページは手放さない"""
    pages = [2, 3, 7, 10, 11, 12, 21, 25]
    deleted = [False] * 30
    cases = {
        5: {10, 11, 12, 21, 25},  # 前のページの 2（表示していない）・3・7 を手放す
        3: {10, 11, 12},  # 続けて、10ページ先（20）より遠い 25・21
        2: {10, 11},  # 続けて、10ページ先までの遠いもの
        0: {10},  # 表示しているページは残す
    }
    for budget, kept in cases.items():
        cache = filled(budget, pages, shown=[3, 7], current=10)
        cache.trim(deleted)
        assert set(cache.images) == kept, budget
    # 前のページは、表示してから時間の経ったものから手放す（3 を後で表示し直した）
    cache = filled(7, pages, shown=[7, 3], current=10)
    cache.trim(deleted)
    assert set(cache.images) == set(pages) - {2}
    cache.budget = 6 * MIB
    cache.trim(deleted)
    assert set(cache.images) == set(pages) - {2, 7}


def test_deleted_pages_are_released_first_and_not_counted_ahead():
    """削除したページは、表示しているページより先にあっても先に手放し、10ページ先には数えない"""
    deleted = [False] * 30
    deleted[11] = True
    cache = filled(3, [10, 11, 12, 21, 22], shown=[], current=10)
    assert cache.ahead(deleted) == [12, 13, 14, 15, 16, 17, 18, 19, 20, 21]
    cache.trim(deleted)
    # 削除した 11 を先に手放し、次に10ページ先（21）より遠い 22
    assert set(cache.images) == {10, 12, 21}


def test_put_keeps_within_budget_and_drop_and_clear():
    """入れたら予算に収まるまで手放す（入れたページを手放すこともある）。drop と clear で手放す"""
    deleted = [False] * 40
    cache = workcache.WorkCache(2 * MIB)
    cache.show(0)
    for i in (0, 1, 30):
        cache.put(i, image(), deleted)
    assert set(cache.images) == {0, 1}  # 遠い 30 は入れた直後に手放す
    assert cache.size == 2 * MIB
    cache.drop(1)
    assert set(cache.images) == {0} and cache.get(1) is None
    cache.clear()
    assert cache.images == {} and cache.current == -1


def test_wanted_pages_are_ahead_within_budget_and_not_cached():
    """先に描いておくページは、10ページ先までのうち、表示しているページから数えて予算に収まるところまでのページで、
    キャッシュにないもの（近い順）。解析が終わっていないページ（大きさが分からない）は含めない"""
    deleted = [False] * 30
    cache = filled(4, [10, 11], shown=[], current=10)
    sizes = {i: 1 * MIB for i in range(30)}
    sizes[13] = None
    assert cache.wanted(deleted, sizes.get) == [12, 14]
    cache.budget = 32 * MIB
    assert cache.wanted(deleted, sizes.get) == [12, 14, 15, 16, 17, 18, 19, 20]
    # 予算が11ページ分に足りないときは、10ページ先より手前までにする
    cache.budget = 2 * MIB
    assert cache.wanted(deleted, sizes.get) == []


def test_memory_watch_lowers_budget_and_switches_to_low_memory():
    """予算は、開くときの予算までで、空いているメモリにキャッシュの分を足した量から決める。空きが 256MB を下回ったら
    メモリが少ない状態にして予算を最小にし、512MB を3回続けて上回ったら解く（途中で下回れば数え直す）"""
    opened = workcache.budget_for(2048 * MIB)  # 512MB
    watch = workcache.MemoryWatch(opened)
    assert watch.update(2048 * MIB, 0) == opened and not watch.low
    # ほかのソフトが使って空きが 600MB になった（キャッシュは 200MB 持っている）
    assert watch.update(600 * MIB, 200 * MIB) == workcache.budget_for(800 * MIB)
    assert watch.update(4096 * MIB, 0) == opened  # 戻れば開くときの予算まで
    assert watch.update(200 * MIB, 100 * MIB) == workcache.MIN_BUDGET and watch.low
    for available in (600, 600, 300, 600, 600):  # 300MB で数え直す
        assert watch.update(available * MIB, 0) == workcache.MIN_BUDGET
        assert watch.low
    assert watch.update(600 * MIB, 0) == workcache.budget_for(600 * MIB)
    assert not watch.low
