# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""起動の画面・墨消しの画面・様式の編集の画面で共有するもの

解析のプロセス（Analyzer）、起動の準備（Preloader）、様式のフォルダの読み書き（Background）、使う OCR（RapidOCR）。
起動の画面が作り、墨消しの画面と様式の編集の画面に渡す。起動の準備は、起動の画面を出した直後から裏で行う。
"""

from PySide6.QtCore import QObject, QThread, Signal

from . import ocr
from .workers import Analyzer, Background, Preloader


class Session(QObject):
    """画面どうしで共有する、解析のプロセスと起動の準備"""

    # 起動の準備が終わった（失敗したときはエラーの文、成功したときは空）
    ready_changed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        # ページの解析を、解析のプロセスに頼む。親を付け、画面のスレッドで Session と一緒に消す（親がないと、
        # Python の側の参照が裏のスレッドで消えたときに、そのスレッドで消えることがあるため）
        self.analyzer = Analyzer(self)
        # 様式のフォルダの読み書き（裏のスレッドで行う）
        self.background = Background(self)
        self.threads: list[QThread] = []  # 起動の準備と出力
        self.started = False
        self.ready = False
        self.error = ""

    @property
    def preloading(self) -> bool:
        """起動の準備（解析のプロセスの立ち上げなど）の途中"""
        return self.started and not self.ready and not self.error

    @property
    def engine(self) -> ocr.Engine:
        """使う OCR（RapidOCR だけ。設定の「ocr/engine」は読まない）"""
        return ocr.Engine.RAPIDOCR

    def start(self) -> None:
        """起動の準備を裏で始める（2回目からは何もしない）"""
        if self.started:
            return
        self.started = True
        th = Preloader(self.analyzer)
        th.done.connect(self._preloaded)
        self.keep(th)
        th.start()

    def keep(self, th: QThread) -> None:
        """裏のスレッドを、終わるまで手放さないように持つ"""
        th.finished.connect(lambda th=th: self.release_thread(th))
        self.threads.append(th)

    def release_thread(self, th: QThread) -> None:
        """終わった裏のスレッド（起動の準備・出力）を手放す

        QThread は、動いているうちに手放すと Qt が致命的なエラーにして強制終了する。終わりの合図（finished）は
        スレッドが終わる直前に出るので、終わるのを待ってから手放す。
        """
        th.wait()
        if th in self.threads:
            self.threads.remove(th)

    def _preloaded(self, error: str) -> None:
        self.ready, self.error = not error, error
        self.ready_changed.emit(error)

    def shutdown(self) -> None:
        """解析のプロセスを終え、裏のスレッドの終わりを待つ（ツールを終えるとき）"""
        self.analyzer.shutdown()  # 解析中のページが終わると、解析のプロセスも終わる
        for th in list(self.threads):
            th.wait(30000)
