# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""墨消しの画面の作業画像のキャッシュ

開いた全ページの作業画像を持つと、ページの数だけメモリが増える（A4 で1ページ約4.1MB）。そのため、上限（予算）の
ある量までだけ持ち、予算を超えたら手放す。手放したページは、表示するときに元のファイルから描き直す（画面の側で、
解析のプロセスに頼む）。

手放す順：
1. 表示しているページより前のページと、削除したページ（最後に表示してから時間の経ったものから。表示していない
   ページを先に）
2. 表示しているページより先のページのうち、10ページ先（削除したページを数えない）より遠いもの（遠いものから）
3. 10ページ先までのページ（遠いものから。予算が足りないときだけ）
表示しているページは手放さない。前のページに戻るより先に進むことが多い使い方に合わせ、先のページを優先して持つ。
"""

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

MIB = 1024**2
AHEAD = 10  # 表示しているページの何ページ先まで、優先して持ち、先に描いておくか
# 予算：ファイルを開くときの空いているメモリから RESERVE を引いた残りの半分を、MIN_BUDGET〜MAX_BUDGET に収める。
# RESERVE は、解析と出力の一時的な分と、ほかのソフトのための余裕
MIN_BUDGET = 32 * MIB
MAX_BUDGET = 512 * MIB
RESERVE = 384 * MIB


def budget_for(available: int) -> int:
    """空いているメモリ（バイト）から決めたキャッシュの予算（バイト）"""
    return int(min(MAX_BUDGET, max(MIN_BUDGET, (available - RESERVE) / 2)))


# メモリが少ない状態に切り替える境目：空いているメモリが LOW_MEMORY を下回ったら切り替え、RECOVER を RECOVER_CHECKS 回
# 続けて上回ったら解く。空いているメモリは、最大で CHECK_SECONDS 秒に1回見る（数は Windows の PC で測ってから決める）
LOW_MEMORY = 256 * MIB
RECOVER = 512 * MIB
RECOVER_CHECKS = 3
CHECK_SECONDS = 1.0


class MemoryWatch:
    """空いているメモリを見て、キャッシュの予算と、メモリが少ない状態かを決める

    予算は、ファイルを開くときの予算（opened）までで、空いているメモリにキャッシュの分を足した量から budget_for で
    決める（ほかのソフトがメモリを使って空きが減れば、予算を下げる。空きが戻れば、開くときの予算まで戻す）。
    メモリが少ない状態では、予算を最小にする（画面の側は、解析のプロセスを1つにする。画面では知らせない）。
    """

    def __init__(self, opened: int):
        self.opened = opened
        self.low = False
        self.recovered = 0

    def update(self, available: int, cached: int) -> int:
        """空いているメモリ（バイト）と、キャッシュが持っている量から、予算を返す（low を更新する）"""
        if available < LOW_MEMORY:
            self.low, self.recovered = True, 0
        elif self.low:
            self.recovered = self.recovered + 1 if available > RECOVER else 0
            if self.recovered >= RECOVER_CHECKS:
                self.low, self.recovered = False, 0
        if self.low:
            return MIN_BUDGET
        return min(self.opened, budget_for(available + cached))


class WorkCache:
    """ページの番号（開いた全ファイルを通した番号）→ 作業画像"""

    def __init__(self, budget: int):
        self.budget = budget
        self.images: dict[int, np.ndarray] = {}
        self.shown: dict[
            int, int
        ] = {}  # ページの番号 → 表示した順の番号（大きいほど新しい）
        self.clock = 0
        self.current = -1

    @property
    def size(self) -> int:
        """持っている作業画像の合計（バイト）"""
        return sum(a.nbytes for a in self.images.values())

    def get(self, index: int) -> np.ndarray | None:
        return self.images.get(index)

    def show(self, index: int) -> None:
        """index のページを表示した（表示しているページにする）"""
        self.current = index
        self.clock += 1
        self.shown[index] = self.clock

    def put(self, index: int, image: np.ndarray, deleted: Sequence[bool]) -> None:
        """index のページの作業画像を持ち、予算を超えたら手放す（入れたページを手放すこともある）"""
        self.images[index] = image
        self.trim(deleted)

    def drop(self, index: int) -> None:
        """index のページの作業画像を手放す（回して解析し直すときなど）"""
        self.images.pop(index, None)

    def clear(self) -> None:
        self.images.clear()
        self.shown.clear()
        self.current = -1

    def ahead(self, deleted: Sequence[bool]) -> list[int]:
        """表示しているページの先の、削除していないページの番号（近い順に AHEAD 個まで）"""
        out = []
        for i in range(self.current + 1, len(deleted)):
            if not deleted[i]:
                out.append(i)
                if len(out) == AHEAD:
                    break
        return out

    def trim(self, deleted: Sequence[bool]) -> None:
        """合計が予算を超えていれば、手放す順（このモジュールの説明）に手放す"""
        total = self.size
        if total <= self.budget:
            return
        for index in self._victims(deleted):
            total -= self.images.pop(index).nbytes
            if total <= self.budget:
                break

    def _victims(self, deleted: Sequence[bool]) -> list[int]:
        cur = self.current
        near = set(self.ahead(deleted))
        behind = [i for i in self.images if i != cur and (i < cur or deleted[i])]
        behind.sort(key=lambda i: self.shown.get(i, 0))
        far = [i for i in self.images if i > cur and not deleted[i] and i not in near]
        close = [i for i in self.images if i in near]
        return behind + sorted(far, reverse=True) + sorted(close, reverse=True)

    def wanted(
        self, deleted: Sequence[bool], size_of: Callable[[int], int | None]
    ) -> list[int]:
        """先に描いておくページ：10ページ先までのうち、予算に収まるところまでのページで、キャッシュにないもの（近い順）。
        size_of はページの作業画像の大きさ（バイト。解析が終わっていなければ None）。解析が終わっていないページは、
        解析の結果が届けば入るので含めない"""
        used = size_of(self.current) or 0
        out = []
        for i in self.ahead(deleted):
            size = size_of(i)
            if size is None:
                continue
            used += size
            if used > self.budget:
                break
            if i not in self.images:
                out.append(i)
        return out
