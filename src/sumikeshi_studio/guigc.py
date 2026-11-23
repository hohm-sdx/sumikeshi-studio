# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""画面のプロセスのガベージコレクション

Python の循環参照のガベージコレクションは、オブジェクトを作ったスレッドで始まる。画面のプロセスの裏のスレッド（様式の
読み書き・出力・解析のプロセスとの受け渡し）で始まると、閉じたウィンドウなどの Qt の部品が、そのスレッドで消えることが
ある。Qt の部品は画面のスレッドで消す決まりで、ほかのスレッドで消すと強制終了することがある（テストで、前のテストの
起動の画面と部品54個が、解析のスレッドで消えたことを記録した）。そのため、画面のプロセスでは自動の
ガベージコレクションを止め、画面のスレッドのタイマーで、自動のときと同じしきい値を超えた世代を回収する（pyqtgraph の
GarbageCollector と同じ形。Python 3.14.5 から、ガベージコレクションは 3.13 と同じ世代別に戻っている）。解析の
プロセスは別の Python なので、自動のままにする。
"""

import gc

from PySide6.QtCore import QObject, QTimer

INTERVAL_MS = 500  # しきい値を超えたかを調べる間隔（ミリ秒）


class GarbageCollector(QObject):
    """自動のガベージコレクションを止め、画面のスレッドのタイマーで、しきい値を超えた世代を回収する"""

    def __init__(self, parent: QObject | None = None, interval_ms: int = INTERVAL_MS):
        super().__init__(parent)
        self.threshold = gc.get_threshold()
        self.timer = QTimer(self)
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self.check)

    def start(self) -> None:
        gc.disable()
        self.timer.start()

    def stop(self) -> None:
        """タイマーを止め、画面のスレッドで残りを回収してから、自動のガベージコレクションに戻す（テストの後始末で使う）"""
        self.timer.stop()
        gc.collect()
        gc.enable()

    def check(self) -> None:
        """自動のガベージコレクションと同じ程度に、しきい値を超えた世代を回収する

        自動なら、若い世代の数がしきい値を超えるたびに若い世代を回収し、そのたびに次の世代の数が1つ増える。タイマーで
        調べるあいだにしきい値の何倍も増えていれば、その回数だけ次の世代の数を増やして比べる（増えた分を1回と数えると、
        古い世代を回収するのが遅れ、閉じた画面の作業画像など、循環参照のまま回収されないオブジェクトが残るため）。
        """
        t0, t1, t2 = self.threshold
        count0, count1, count2 = gc.get_count()
        if count0 <= t0:
            return
        count1 += count0 // t0
        if count1 <= t1:
            gc.collect(0)
            return
        count2 += count1 // t1
        gc.collect(2 if count2 > t2 else 1)


def start(app: QObject) -> GarbageCollector:
    """画面のプロセスのガベージコレクションを、画面のスレッドに移す（launcher.main で呼ぶ。呼んだ側が戻り値を
    持ち続ける）"""
    collector = GarbageCollector(app)
    collector.start()
    return collector
