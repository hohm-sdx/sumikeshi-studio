# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""テストのあいだ、インターネットへの通信を禁じる仕組み（netguard.py）のテスト

接続先は、ドキュメント用のアドレス（192.0.2.1・2001:db8::1。RFC 5737・RFC 3849 で、実在の機械には割り当てない）と、
example.com（RFC 2606）にする。仕組みが効いていなくても、実在の機械には届かない。
"""

import contextlib
import socket
import time
import urllib.request

import netguard
import pytest

DOC_V4 = ("192.0.2.1", 80)
DOC_V6 = ("2001:db8::1", 443)
# 例外になるまでの時間の上限（秒）。実際に接続しようとすると、届かない先では、テストで渡す timeout（1秒）か、
# それより長く待つので、その半分までに例外になったことが、通信の前に止めたことの確認になる。全件のテストを並べて
# 流すと、すぐに例外になるときでも数十ミリ秒かかることがあるので、それより十分に長くしている
LIMIT = 0.5


@pytest.fixture
def tried():
    """このテストで、わざと通信しようとした記録を返す（テストの終わりに記録を消し、conftest.py の
    no_network_attempts に数えさせない）"""
    yield netguard.ATTEMPTS
    netguard.pop_attempts()


def test_connect_is_blocked(tried):
    """AF_INET・AF_INET6 の接続は、接続先を示した例外にし、つながない"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        start = time.perf_counter()
        with pytest.raises(netguard.NetworkBlocked, match=r"接続 192\.0\.2\.1:80"):
            s.connect(DOC_V4)
        with pytest.raises(netguard.NetworkBlocked, match=r"接続 192\.0\.2\.1:80"):
            s.connect_ex(DOC_V4)
        assert time.perf_counter() - start < LIMIT
        with pytest.raises(OSError):
            s.getpeername()  # つながっていない
    with (
        socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s,
        pytest.raises(netguard.NetworkBlocked, match=r"接続 2001:db8::1:443"),
    ):
        s.connect(DOC_V6)
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s,
        pytest.raises(netguard.NetworkBlocked, match=r"送信 192\.0\.2\.1:80"),
    ):
        s.sendto(b"x", DOC_V4)
    assert tried == [
        "接続 192.0.2.1:80",
        "接続 192.0.2.1:80",
        "接続 2001:db8::1:443",
        "送信 192.0.2.1:80",
    ]


def test_name_resolution_is_blocked(tried):
    """名前の解決（getaddrinfo など）と、それを通る create_connection は、すぐに例外になる"""
    start = time.perf_counter()
    with pytest.raises(netguard.NetworkBlocked, match=r"名前の解決 example\.com:443"):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(netguard.NetworkBlocked, match=r"名前の解決 example\.com"):
        socket.gethostbyname("example.com")
    with pytest.raises(netguard.NetworkBlocked, match=r"名前の解決 192\.0\.2\.1:80"):
        socket.create_connection(DOC_V4, timeout=1)
    assert time.perf_counter() - start < LIMIT
    assert len(tried) == 3


def test_http_libraries_are_blocked(tried):
    """urllib と、RapidOCR がファイルを取るときに使う requests も、通信の前に例外になる（例外を通信の失敗として
    受け止めて、やり直さない）"""
    import requests

    start = time.perf_counter()
    with pytest.raises(netguard.NetworkBlocked):
        urllib.request.urlopen("http://192.0.2.1/", timeout=1)
    with pytest.raises(netguard.NetworkBlocked):
        requests.get("https://example.com/", timeout=1)
    assert time.perf_counter() - start < LIMIT
    assert tried == ["名前の解決 192.0.2.1:80", "名前の解決 example.com:443"]


def test_caught_attempt_is_recorded(tried):
    """例外を受け止めて続けるコードがあっても、通信しようとした記録は残る（conftest.py の no_network_attempts が、
    この記録でテストを失敗にする）"""
    with contextlib.suppress(Exception):  # 受け止めて黙って続けるコードに見立てる
        socket.create_connection(DOC_V4)
    assert netguard.pop_attempts() == ["名前の解決 192.0.2.1:80"]
    assert netguard.pop_attempts() == []


def test_local_communication_is_not_blocked():
    """AF_UNIX など、同じ PC の中の通信は禁じない（Qt・multiprocessing・pytest-xdist が使う）"""
    a, b = socket.socketpair(socket.AF_UNIX)
    with a, b:
        a.sendall(b"x")
        assert b.recv(1) == b"x"
    assert netguard.ATTEMPTS == []
