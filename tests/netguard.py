# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""テストのあいだ、インターネットへの通信を禁じる

ツールは導入した後に通信しない。ライブラリ（RapidOCR など）のバージョンを上げたときに、通信する処理が足されても気づける
よう、テストのプロセスの中で、AF_INET・AF_INET6 のソケットの接続・送信と、名前の解決を例外にする。AF_UNIX など、
同じ PC の中の通信（Qt・multiprocessing・pytest-xdist が使う）は禁じない。

効くのは、この仕組みを入れたテストのプロセスの中だけ。解析のプロセス（spawn で別に起動する）の中には効かない。
通信しようとした記録（ATTEMPTS）も残すので、例外を受け止めて黙って続けるコードがあっても、テストを失敗にできる
（conftest.py の no_network_attempts）。
"""

import socket
from typing import NoReturn

_INET = (socket.AF_INET, socket.AF_INET6)
# 通信しようとした先の記録（テストが終わるたびに、conftest.py が確かめて消す）
ATTEMPTS: list[str] = []


class NetworkBlocked(RuntimeError):
    """テストの中で、インターネットへ通信しようとした（OSError にしないのは、通信の失敗として受け止めて
    やり直したり、黙って続けたりするコードに紛れないようにするため）"""


def _block(what: str) -> NoReturn:
    ATTEMPTS.append(what)
    raise NetworkBlocked(f"テストの中でインターネットへ通信しようとしました：{what}")


def pop_attempts() -> list[str]:
    """通信しようとした先の記録を返し、記録を消す"""
    tried = ATTEMPTS.copy()
    ATTEMPTS.clear()
    return tried


def _where(address) -> str:
    """接続先の (ホスト, ポート, …) を「ホスト:ポート」にする"""
    if isinstance(address, tuple) and len(address) >= 2:
        return f"{address[0]}:{address[1]}"
    return repr(address)


def install() -> None:
    """socket の関数を、インターネットへの通信を禁じるものに置き換える（何度呼んでも1回だけ置き換える）"""
    if getattr(socket.socket.connect, "netguard", False):
        return
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
    sendto = socket.socket.sendto

    def guarded_connect(self, address):
        if self.family in _INET:
            _block(f"接続 {_where(address)}")
        return connect(self, address)

    def guarded_connect_ex(self, address):
        if self.family in _INET:
            _block(f"接続 {_where(address)}")
        return connect_ex(self, address)

    def guarded_sendto(self, data, *args):
        if self.family in _INET:
            _block(f"送信 {_where(args[-1])}")
        return sendto(self, data, *args)

    def guarded_getaddrinfo(host, port, *args, **kwargs):
        _block(f"名前の解決 {host}:{port}")

    def guarded_gethostbyname(host, *args):
        _block(f"名前の解決 {host}")

    for f in (guarded_connect, guarded_connect_ex, guarded_sendto):
        f.netguard = True
    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.socket.sendto = guarded_sendto
    socket.getaddrinfo = guarded_getaddrinfo
    socket.gethostbyname = guarded_gethostbyname
    socket.gethostbyname_ex = guarded_gethostbyname
    socket.gethostbyaddr = guarded_gethostbyname
