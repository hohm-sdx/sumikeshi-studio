# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""一度に1つだけ起動する

起動が遅いときにショートカットを何回も押して、ツールがその数だけ起動し、メモリを使い切らないようにする。
後から起動したツールは、解析のプロセスを立ち上げる前に、先に起動しているツールへ「前に出る」ことと開くファイルを
伝えて終わる。見分ける範囲は Windows の利用者ごと（ローカルサーバーの名前に、利用者の名前から作った値を入れる）。

仕組みは QLocalServer・QLocalSocket（Windows では名前付きパイプ）。先のツールが異常終了しても名前付きパイプは
残らないので、古いマーカーで起動できなくなることはない（Unix のソケットのファイルが残ったときは消してからローカルサーバーを始め直す）。
先のツールが終わる途中に起動したときは、つながっても返事がなければ、少し待ってつなぎ直し、つながらなくなれば
自分がローカルサーバーになる。どちらも起動しない状態にはしない。
"""

import getpass
import hashlib
import json
import os
import sys
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

NAME_PREFIX = "SumikeshiStudio"
ENV_NAME = "SUMIKESHI_STUDIO_INSTANCE_NAME"  # ローカルサーバーの名前を変える（テストで、使っているツールと分けるため）
CONNECT_MS = 500  # 先のツールにつながるまで待つ時間
REPLY_MS = 3000  # 先のツールの返事を待つ時間
RETRY_SECONDS = (
    6.0  # 返事がないときに、つなぎ直す時間の上限（先のツールが終わる途中のとき）
)


def server_name() -> str:
    """ローカルサーバーの名前（Windows の利用者ごと）"""
    if name := os.environ.get(ENV_NAME):
        return name
    user = hashlib.sha256(getpass.getuser().encode("utf-8")).hexdigest()[:16]
    return f"{NAME_PREFIX}-{user}"


def allow_foreground() -> None:
    """先のツールが、ウィンドウを前に出せるようにする（Windows）

    Windows は、前面にある（利用者が操作した）プロセスの許しがないと、別のプロセスのウィンドウを前に出さず、
    タスク バーを点滅させるだけのことがある。後から起動した（利用者がいま起動した）このプロセスから許す。
    """
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY


def forward(files: list[str], name: str | None = None) -> bool:
    """先に起動しているツールに、前に出ることと開くファイルを伝える。伝えられたら True

    先のツールがなければ（つながらなければ）False。つながっても返事がなければ（先のツールが終わる途中）、
    つなぎ直す。RETRY_SECONDS を過ぎても返事がなければ False（自分が起動する）。
    """
    name = name or server_name()
    end = time.monotonic() + RETRY_SECONDS
    message = (json.dumps({"files": files}, ensure_ascii=False) + "\n").encode("utf-8")
    while True:
        sock = QLocalSocket()
        sock.connectToServer(name)
        if not sock.waitForConnected(CONNECT_MS):
            return False
        allow_foreground()
        sock.write(message)
        sock.waitForBytesWritten(CONNECT_MS)
        if sock.waitForReadyRead(REPLY_MS) and bytes(sock.readAll()).startswith(b"ok"):
            sock.disconnectFromServer()
            return True
        sock.abort()
        if time.monotonic() > end:
            return False
        time.sleep(0.3)


class Server(QObject):
    """後から起動したツールからの通知を受け取るローカルサーバー"""

    message = Signal(list)  # 開くファイル（空なら、前に出るだけ）

    def __init__(self, name: str | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.name = name or server_name()
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._accept)

    def listen(self) -> bool:
        """ローカルサーバーを始める。始められなければ False（そのときも、ツールは起動する）"""
        if self.server.listen(self.name):
            return True
        QLocalServer.removeServer(
            self.name
        )  # 前に異常終了したツールのマーカー（Unix のソケットのファイル）
        return self.server.listen(self.name)

    def _accept(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._read(s))
            if sock.bytesAvailable():
                self._read(sock)

    def _read(self, sock: QLocalSocket):
        if not sock.canReadLine():
            return
        line = bytes(sock.readLine()).decode("utf-8", "replace")
        try:
            files = json.loads(line).get("files", [])
        except ValueError, AttributeError:
            files = []
        sock.write(b"ok\n")
        sock.flush()
        sock.disconnectFromServer()
        self.message.emit([f for f in files if isinstance(f, str)])
