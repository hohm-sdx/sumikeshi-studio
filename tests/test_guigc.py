# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""画面のプロセスのガベージコレクション（guigc.py）のテスト"""

import gc
import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip(
    "PySide6.QtWidgets", reason="画面の部品を読み込めない環境", exc_type=ImportError
)
from PySide6 import QtCore

from sumikeshi_studio import guigc


def test_garbage_is_collected_in_gui_thread():
    """自動のガベージコレクションを止めると、ほかのスレッドで多くのオブジェクトを作っても、参照されなくなった
    循環参照の中の Qt のオブジェクトはそのスレッドで消えず、画面のスレッドのタイマーで回収したときに、画面のスレッドで
    消える（自動のままでは、ほかのスレッドで消える）"""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    collector = guigc.GarbageCollector(interval_ms=10)
    collector.start()
    kept = []
    try:
        assert not gc.isenabled()
        gc.collect()  # それまでに参照されなくなったオブジェクトを回収し、若い世代の数を0に近くする
        threads = []
        obj = (
            QtCore.QObject()
        )  # 親がない（Python の側が持ち主なので、ガベージコレクションで消える）
        obj.destroyed.connect(
            lambda *_: threads.append(threading.current_thread().name),
            QtCore.Qt.ConnectionType.DirectConnection,
        )
        cycle = {"obj": obj}
        cycle["self"] = cycle  # 次の行で名前を消すと、循環参照だけが残る
        del obj, cycle

        def work():
            # 自動のままなら、このスレッドで若い世代のガベージコレクションが動く量のオブジェクトを作り、そのまま持つ
            kept.append([[] for _ in range(3 * gc.get_threshold()[0])])

        worker = threading.Thread(target=work, name="物を作るスレッド")
        worker.start()
        worker.join()
        assert threads == []  # ほかのスレッドでは消えない
        end = time.time() + 5
        while not threads and time.time() < end:
            app.processEvents()
            time.sleep(0.01)
        assert threads == [threading.main_thread().name]
    finally:
        collector.stop()
    assert gc.isenabled()


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ((1500, 9, 9), []),  # しきい値を超えていない
        ((2500, 9, 9), [0]),  # 若い世代だけ
        # 自動なら若い世代を3回回収し、次の世代の数が11になっていた
        ((6500, 8, 0), [1]),
        ((2500, 10, 10), [2]),
        # 自動なら若い世代を12回回収し、中の世代も1回回収していた
        ((25000, 0, 10), [2]),
    ],
)
def test_check_counts_collections_automatic_gc_would_have_done(
    counts, expected, monkeypatch
):
    """タイマーで調べるあいだに若い世代の数がしきい値の何倍も増えていれば、自動なら若い世代を回収していた回数だけ
    次の世代の数を増やして比べ、古い世代も回収する"""
    collector = guigc.GarbageCollector()
    collector.threshold = (2000, 10, 10)
    done = []
    monkeypatch.setattr(guigc.gc, "get_count", lambda: counts)
    monkeypatch.setattr(
        guigc.gc, "collect", lambda generation=2: done.append(generation) or 0
    )
    collector.check()
    assert done == expected
