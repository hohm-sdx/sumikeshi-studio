# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""配布用パッケージを作るスクリプト（tools/make_dist.py）のうち、署名の確認と配布用パッケージの中身を整える処理のテスト。
配布用パッケージそのものは作らない"""

import base64
import hashlib
import importlib.util
import json
import re
import shutil
import struct
import tomllib
import urllib.error
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

import pytest

from sumikeshi_studio import APP_NAME, __version__, nativecheck, version

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "make_dist", ROOT / "tools" / "make_dist.py"
)
make_dist = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_dist)

SIGNED = """Signer's certificate:
\tSigner #0:
\t\tSubject: /C=US/ST=Oregon/L=Beaverton/O=Python Software Foundation/CN=Python Software Foundation
\t\tIssuer : /C=US/O=Microsoft Corporation/CN=Microsoft ID Verified CS EOC CA 04

Timestamp Server Signature verification: ok
Signature verification: ok

Number of verified signatures: 1
Succeeded
"""


# osslsigncode 2.9-2 の出力（開発用の devcontainer で実物からコピーして縮めたもの）。
# 「Signer's certificate:」と「Signer #0:」のあいだに区切りの行が入り、タイムスタンプの署名者の節が先に出る
SIGNED_29 = """Signature Index: 0  (Primary Signature)

Signer's certificate:
\t------------------
\tSigner #0:
\t\tSubject: /jurisdictionC=FI/businessCategory=Private Organization/serialNumber=2637805-2/C=FI/ST=Uusimaa/L=Espoo/O=The QT Company Oy/CN=The QT Company Oy
\t\tIssuer : /C=US/O=DigiCert, Inc./CN=DigiCert Trusted G4 Code Signing RSA4096 SHA384 2021 CA1

Countersignatures:
\tTimestamp time: Oct  5 14:23:50 2026 GMT

Timestamp Server Signature certificate:
\t------------------
\tSigner #0:
\t\tSubject: /C=US/O=DigiCert, Inc./CN=DigiCert SHA256 RSA4096 Timestamp Responder 2026 1

Ignored TSA's CRL distribution point: http://crl3.digicert.com/TS.crl
Timestamp Server Signature CRL verification: ok
Timestamp Server Signature verification: ok
Signature verification time: Oct  5 14:23:50 2026 GMT
Ignored CRL distribution point: http://crl3.digicert.com/CS.crl
Signature CRL verification: ok
Signature verification: ok

Number of verified signatures: 1
Succeeded
"""
# CRL を渡さずに流したとき（集める1回目）。配布先の行が出て、検証は通らない
NO_CRL_29 = """Signer's certificate:
\t------------------
\tSigner #0:
\t\tSubject: /C=US/ST=Oregon/L=Beaverton/O=Python Software Foundation/CN=Python Software Foundation

Ignored TSA's CRL distribution point: http://www.microsoft.com/pkiops/crl/Microsoft%20Public%20RSA%20Timestamping%20CA%202020.crl
Use the "-TSA-CRLfile" option to add one or more Time-Stamp Authority CRLs in PEM format.
Timestamp Server Signature verification: failed
Signature verification: failed

Number of verified signatures: 1
Failed
"""
UNSIGNED_29 = "PE checksum   : 00000000\n\nNo signature found\n\nFailed\n"
# 署名の後に中身の1バイトを書き換えたもの。署名者の節を出さずに終わる
TAMPERED_29 = """Signature verification: failed

Current PE checksum   : 00062DA4
Calculated PE checksum: 00062CBD
Warning: invalid PE checksum

Signature Index: 0  (Primary Signature)

Message digest algorithm  : SHA256
Current message digest    : B871
Calculated message digest : 42B1     MISMATCH!!!

Number of verified signatures: 1
Failed
"""


def test_parse_verify():
    """osslsigncode verify の出力から、署名者の組織と、検証が通ったかを読む"""
    assert make_dist.parse_verify(SIGNED) == ("Python Software Foundation", True)
    unsigned = "Current PE checksum   : 00000000\n\nNo signature found\n\nFailed\n"
    assert make_dist.parse_verify(unsigned) == (None, False)
    broken = SIGNED.replace(
        "Signature verification: ok", "Signature verification: failed"
    )
    assert make_dist.parse_verify(broken.replace("Succeeded", "Failed")) == (
        "Python Software Foundation",
        False,
    )


def test_parse_verify_29():
    """osslsigncode 2.9-2 の出力（区切りの行あり）でも、署名者の組織を読む。タイムスタンプの署名者は拾わない"""
    assert make_dist.parse_verify(SIGNED_29) == ("The QT Company Oy", True)
    assert make_dist.parse_verify(NO_CRL_29) == ("Python Software Foundation", False)
    assert make_dist.parse_verify(UNSIGNED_29) == (None, False)
    # 書き換えたものは組織を読めない（check_signatures が「発行先を読めない」と出す）
    assert make_dist.parse_verify(TAMPERED_29) == ("", False)
    # 区切りの行のほかの行は飛び越えない
    other = SIGNED_29.replace(
        "\t------------------\n\tSigner #0:", "\tOther line\n\tSigner #0:", 1
    )
    assert make_dist.parse_verify(other) == ("", True)


def test_crl_urls():
    """取らなかった CRL の配布先を、署名とタイムスタンプの両方から読む"""
    assert make_dist.crl_urls(SIGNED_29) == [
        "http://crl3.digicert.com/TS.crl",
        "http://crl3.digicert.com/CS.crl",
    ]
    assert make_dist.crl_urls(UNSIGNED_29) == []
    assert make_dist.crl_urls(TAMPERED_29) == []


def der_crl(size: int) -> bytes:
    """DER の形（SEQUENCE と長さ）だけを持つ、仮の CRL"""
    body = b"\x00" * size
    if size < 0x80:
        return b"\x30" + bytes([size]) + body
    n = (size.bit_length() + 7) // 8
    return b"\x30" + bytes([0x80 | n]) + size.to_bytes(n) + body


@pytest.mark.parametrize("size", [10, 200, 70000])
def test_crl_pem_from_der(size):
    """DER の CRL を PEM にする（長さの書き方が1バイト・2バイト・3バイトのもの）"""
    data = der_crl(size)
    pem = make_dist.crl_pem("http://x/a.crl", data)
    lines = pem.decode().splitlines()
    assert (
        lines[0] == "-----BEGIN X509 CRL-----" and lines[-1] == "-----END X509 CRL-----"
    )
    assert all(len(line) <= 64 for line in lines[1:-1])
    assert base64.b64decode("".join(lines[1:-1])) == data


def test_crl_pem_keeps_pem_and_rejects_others():
    pem = b"-----BEGIN X509 CRL-----\nAAAA\n-----END X509 CRL-----\n"
    assert make_dist.crl_pem("http://x/a.crl", pem) == pem
    for bad in (b"<html>not found</html>", der_crl(10)[:-1], b"\x30"):
        with pytest.raises(SystemExit, match="CRL ではないもの.*http://x/a.crl"):
            make_dist.crl_pem("http://x/a.crl", bad)


def crl_runner(tmp_path, needs, crl_free=()):
    """仮のパッケージと、差し替える verify・fetch。needs は部品ごとに、要る CRL の URL を、出る順に並べたもの
    （1つ目が束にないと1つ目だけを出す。Python の部品のように、前の CRL がないと次の配布先まで進まない形）。
    crl_free の部品は、束を渡さなければ通り、束を渡すと「unable to get certificate CRL」で通らない"""
    bundle = tmp_path / "bundle"
    for rel in [*needs, *crl_free]:
        (bundle / rel).parent.mkdir(parents=True, exist_ok=True)
        (bundle / rel).write_bytes(b"MZ")
    calls = []
    fetched = []

    def verify(p, crls):
        rel = p.relative_to(bundle).as_posix()
        have = crls.read_text() if crls else ""
        calls.append((rel, crls is not None))
        head = "Signer's certificate:\n\t------------------\n\tSigner #0:\n\t\tSubject: /O=Org/CN=x\n"
        if rel in crl_free:
            tail = (
                "unable to get certificate CRL\nFailed\n"
                if crls
                else "Signature verification: ok\nSucceeded\n"
            )
            return head + tail
        for url in needs[rel]:
            if url not in have:
                return head + f"Ignored CRL distribution point: {url}\nFailed\n"
        return head + "Signature verification: ok\nSucceeded\n"

    def fetch(url):
        fetched.append(url)
        return f"-----BEGIN X509 CRL-----\n{url}\n-----END X509 CRL-----\n".encode()

    return bundle, verify, fetch, calls, fetched


def test_collect_crls_in_rounds(tmp_path):
    """URL が2回に分けて出るとき、新しい URL を出した部品だけを流し直し、出なくなるまでくり返す"""
    needs = {
        "a/python.exe": ["http://ms/ts.crl", "http://ms/eoc.crl"],
        "a/qt.dll": ["http://dc/cs.crl"],
        "a/plain.pyd": [],
    }
    bundle, verify, fetch, calls, fetched = crl_runner(tmp_path, needs)
    urls, count = make_dist.collect_crls(bundle, verify, fetch, tmp_path / "crls.pem")
    assert count == 3
    assert urls == ["http://dc/cs.crl", "http://ms/ts.crl", "http://ms/eoc.crl"]
    assert fetched == urls
    # 1回目は束を渡さない。2回目からは、新しい URL を出した部品だけ
    assert calls == [
        ("a/plain.pyd", False), ("a/python.exe", False), ("a/qt.dll", False),
        ("a/python.exe", True), ("a/qt.dll", True),
        ("a/python.exe", True),
    ]  # fmt: skip


def test_collect_crls_stops_at_limit(tmp_path):
    """上限の回数を流しても新しい URL が出るなら、残った URL と部品の名前を出して止める"""
    needs = {"a/python.exe": [f"http://ms/{i}.crl" for i in range(5)]}
    bundle, verify, fetch, _, _ = crl_runner(tmp_path, needs)
    with pytest.raises(
        SystemExit, match=r"5回流しても(.|\n)*http://ms/4.crl：a/python.exe"
    ):
        make_dist.collect_crls(bundle, verify, fetch, tmp_path / "crls.pem")


def test_collect_crls_stops_when_fetch_fails(tmp_path):
    """CRL を取れなければ、URL と理由を出して止める"""
    bundle, verify, _, _, _ = crl_runner(tmp_path, {"a/qt.dll": ["http://dc/cs.crl"]})

    def fetch(url):
        raise urllib.error.URLError("connection refused")

    with pytest.raises(
        SystemExit, match="CRL を取れませんでした：http://dc/cs.crl.*connection refused"
    ):
        make_dist.collect_crls(bundle, verify, fetch, tmp_path / "crls.pem")


@pytest.mark.parametrize("url", ["ldap://dc/cs.crl", "file:///etc/passwd"])
def test_collect_crls_accepts_only_http(tmp_path, url):
    bundle, verify, fetch, _, fetched = crl_runner(tmp_path, {"a/qt.dll": [url]})
    with pytest.raises(SystemExit, match="http・https ではありません"):
        make_dist.collect_crls(bundle, verify, fetch, tmp_path / "crls.pem")
    assert fetched == []


def test_collect_crls_stops_when_not_crl(tmp_path):
    bundle, verify, _, _, _ = crl_runner(tmp_path, {"a/qt.dll": ["http://dc/cs.crl"]})
    with pytest.raises(SystemExit, match="CRL ではないもの"):
        make_dist.collect_crls(
            bundle, verify, lambda url: b"<html>", tmp_path / "crls.pem"
        )


def test_check_bundle_decides_with_final_crls(tmp_path, monkeypatch):
    """合否は、集め終わった束を渡して全部の部品を流し直した結果で決める。束を渡さない1回目に通った部品
    （CRL の配布先を持たない証明書の部品）でも、束を渡して通らなければ止まる"""
    needs = {"a/qt.dll": ["http://dc/cs.crl"]}
    bundle, verify, fetch, calls, _ = crl_runner(
        tmp_path, needs, crl_free=["a/nocdp.dll"]
    )
    monkeypatch.setattr(make_dist, "SIGNERS", [("a/*", "Org")])
    monkeypatch.setattr(make_dist, "UNSIGNED_FILES", set())
    counts, problems, urls, count = make_dist.check_bundle(
        bundle, verify, fetch, tmp_path / "crls.pem"
    )
    assert urls == ["http://dc/cs.crl"] and count == 2
    assert problems == ["署名の検証が通らない（Org）：a/nocdp.dll"]
    assert counts == {"Org": 1}
    # 最後の確認では、全部の部品に束を渡す
    assert calls[-2:] == [("a/nocdp.dll", True), ("a/qt.dll", True)]


def make_bundle(root: Path) -> dict[str, tuple[str | None, bool]]:
    """決まりどおりの仮のパッケージ（ファイルの中身は空）と、ファイルごとの署名（組織, 検証が通ったか）"""
    folder = make_dist.FOLDER
    site = make_dist.SITE
    signs = {
        f"{folder}/python/python.exe": (make_dist.PSF, True),
        f"{folder}/python/python314.dll": (make_dist.PSF, True),
        f"{folder}/python/_ctypes.pyd": (make_dist.PSF, True),
        f"{folder}/python/vcruntime140.dll": (make_dist.MICROSOFT, True),
        f"{folder}/python/msvcp140.dll": (make_dist.QT, True),
        f"{site}/PySide6/Qt6Core.dll": (make_dist.QT, True),
        f"{site}/shiboken6/Shiboken.pyd": (make_dist.QT, True),
        f"{site}/onnxruntime/capi/onnxruntime.dll": (make_dist.MICROSOFT, True),
        f"{site}/numpy.libs/msvcp140-0123.dll": (make_dist.MICROSOFT, True),
    }
    signs |= {f"{site}/{name}": (None, False) for name in make_dist.UNSIGNED_FILES}
    for rel in signs:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"MZ")
    (root / site / "numpy" / "__init__.py").write_text("")  # .py は確かめない
    return signs


def test_check_signatures_accepts_bundle_following_rules(tmp_path):
    signs = make_bundle(tmp_path)
    counts, problems = make_dist.check_signatures(
        tmp_path, lambda p: signs[p.relative_to(tmp_path).as_posix()]
    )
    assert problems == []
    assert counts == {
        make_dist.PSF: 3,
        make_dist.MICROSOFT: 3,
        make_dist.QT: 3,
        "署名なし": 38,
    }
    assert len(make_dist.UNSIGNED_FILES) == 38


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        # 決めていない署名のない部品が増えた
        (
            ("add", "numpy/_core/_new.cp314-win_amd64.pyd", (None, False)),
            "署名がない：",
        ),
        # Python の部品の署名がない
        (("set", "python/python.exe", (None, False)), "署名がない："),
        # 発行先が違う
        (("set", "python/python314.dll", (make_dist.QT, True)), "発行先が違う"),
        # 検証が通らない
        (("set", "python/_ctypes.pyd", (make_dist.PSF, False)), "署名の検証が通らない"),
        # 署名の後に書き換えた（2.9-2 は組織を出さない）
        (
            ("set", "python/_ctypes.pyd", ("", False)),
            "署名の検証が通らない（発行先を読めない）",
        ),
        # 組織を読めないのに検証は通った（照合は甘くしない）
        (("set", "python/_ctypes.pyd", ("", True)), "発行先が違う"),
        # 決めた署名のない部品がなくなった（ライブラリのバージョンが変わったなど）
        (
            ("remove", "pypdfium2_raw/pdfium.dll", None),
            "決めた署名のない部品が見つからない",
        ),
        # 署名のない部品のはずのファイルに、決まりにない署名がある
        (("set", "cv2/cv2.pyd", ("OpenCV", True)), "決まりにない署名"),
    ],
)
def test_check_signatures_reports_files_off_rules(tmp_path, change, expected):
    """決まりから外れたら、そのファイルの名前と理由を返す（make_dist は配布用パッケージを作るのを止める）"""
    signs = make_bundle(tmp_path)
    kind, name, value = change
    base = make_dist.SITE if not name.startswith("python/") else make_dist.FOLDER
    rel = f"{base}/{name}"
    if kind == "add":
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"MZ")
        signs[rel] = value
    elif kind == "set":
        signs[rel] = value
    else:
        (tmp_path / rel).unlink()
    _, problems = make_dist.check_signatures(
        tmp_path, lambda p: signs[p.relative_to(tmp_path).as_posix()]
    )
    assert len(problems) == 1 and problems[0].startswith(expected)
    assert problems[0].endswith(name)


def test_add_site_line(tmp_path):
    """python314._pth に Lib\\site-packages の行を足す（import site の行は足さない。何度呼んでも1行）"""
    pth = tmp_path / "python314._pth"
    pth.write_text(
        "python314.zip\n.\n\n# Uncomment to run site.main() automatically\n#import site\n"
    )
    make_dist.add_site_line(pth)
    make_dist.add_site_line(pth)
    assert pth.read_bytes().decode().split("\r\n") == [
        "python314.zip",
        ".",
        "Lib\\site-packages",
        "",
        "# Uncomment to run site.main() automatically",
        "#import site",
        "",
    ]


def test_remove_build_leftovers(tmp_path):
    """作った環境のパスを書いたスクリプト（bin）と記録（direct_url.json）、uv のロックファイルを除く"""
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "sumikeshi-studio").write_text(
        "#!/workspaces/.venv/bin/python\n"
    )
    (tmp_path / ".lock").write_text("")
    for dist in ("sumikeshi_studio-0.1.0.dist-info", "numpy-2.5.4.dist-info"):
        (tmp_path / dist).mkdir()
        (tmp_path / dist / "METADATA").write_text("")
    (tmp_path / "sumikeshi_studio-0.1.0.dist-info" / "direct_url.json").write_text("{}")
    removed = make_dist.remove_build_leftovers(tmp_path)
    assert removed == [
        "bin",
        ".lock",
        "sumikeshi_studio-0.1.0.dist-info/direct_url.json",
    ]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "numpy-2.5.4.dist-info",
        "sumikeshi_studio-0.1.0.dist-info",
    ]
    assert (tmp_path / "sumikeshi_studio-0.1.0.dist-info" / "METADATA").exists()


def test_pdfium_line_and_dll_check(tmp_path):
    """version.txt の PDFium の行は、入れた pypdfium2 のバージョンの情報から作る。pdfium.dll は、決めた SHA-256 と照合し、
    違えば止める（PyPI の同じバージョンの配布物と取り違えないようにする）"""
    raw = tmp_path / "pypdfium2_raw"
    raw.mkdir()
    (tmp_path / "pypdfium2").mkdir()
    (raw / "version.json").write_text(
        '{"major": 158, "minor": 0, "build": 8125, "patch": 0, "origin": "pdfium-binaries", "flags": []}'
    )
    (tmp_path / "pypdfium2" / "version.json").write_text(
        '{"major": 5, "minor": 14, "patch": 0, "data_source": "record"}'
    )
    assert make_dist.pdfium_line(tmp_path) == (
        "PDFium: 158.0.8125.0 (pdfium-binaries; built from the source of pypdfium2 5.14.0)"
    )
    # version.txt の中身（作った日時の行は、ツールが version.MADE_PREFIX で探す）
    stamp = datetime(2026, 11, 11, 9, 5)
    text = make_dist.version_file_text(stamp, tmp_path, "a" * 40)
    assert text.split("\r\n") == [
        f"Sumikeshi Studio {__version__}",
        "Built: 2026-11-11 09:05",
        "Commit: " + "a" * 40,
        f"Source: https://github.com/hohm-sdx/sumikeshi-studio (tag v{__version__})",
        f"Python: {make_dist.PYTHON_FILE} (python.org)",
        "PDFium: 158.0.8125.0 (pdfium-binaries; built from the source of pypdfium2 5.14.0)",
        "",
    ]
    assert any(s.startswith(version.MADE_PREFIX) for s in text.splitlines())
    (raw / "pdfium.dll").write_bytes(b"other")
    with pytest.raises(SystemExit):
        make_dist.check_pdfium_dll(tmp_path)


def test_icons_check(tmp_path):
    """配布用パッケージの site-packages に、ツールのアイコンのファイル（.ico と PNG）がリポジトリと同じ中身で入っていれば
    通り、ないもの・違うものがあれば止める"""
    site = tmp_path / "site"
    dst = site / "sumikeshi_studio" / "icons"
    shutil.copytree(make_dist.ICONS, dst)
    make_dist.check_icons(site)
    (dst / "icon-16.png").write_bytes(b"other")
    with pytest.raises(SystemExit):
        make_dist.check_icons(site)
    shutil.copy(make_dist.ICONS / "icon-16.png", dst)
    (dst / "sumikeshi-studio.ico").unlink()
    with pytest.raises(SystemExit):
        make_dist.check_icons(site)


def test_bundled_forms_check(tmp_path):
    """配布用パッケージの site-packages に、導入で入れる3様式（.json と見本の画像）がリポジトリと同じ中身で入っていれば通り、
    ないもの・違うもの・余分なものがあれば止める"""
    site = tmp_path / "site"
    dst = site / "sumikeshi_studio" / "bundled_forms"
    shutil.copytree(make_dist.BUNDLED_FORMS, dst)
    make_dist.check_bundled_forms(site)
    assert sorted(p.name for p in dst.iterdir()) == [
        f"{form_id}.{ext}"
        for form_id in ("nta-fuyou-r9", "nta-hoken-r8", "nta-kihai-r8")
        for ext in ("json", "png")
    ]
    (dst / "nta-hoken-r8.png").write_bytes(b"other")
    with pytest.raises(SystemExit):
        make_dist.check_bundled_forms(site)
    shutil.copy(make_dist.BUNDLED_FORMS / "nta-hoken-r8.png", dst)
    (dst / "nta-kihai-r8.json").unlink()
    with pytest.raises(SystemExit):
        make_dist.check_bundled_forms(site)
    with pytest.raises(SystemExit):
        make_dist.check_bundled_forms(tmp_path / "空")


def test_not_used_patterns_match_unsigned_files():
    """nativecheck.NOT_USED（ツールが使わない部品）の型は、どれも UNSIGNED（署名のない部品）のファイルの1つ以上に
    当たる。ライブラリのバージョンを上げてファイル名が変わり、型がどのファイルにも当たらなくなったら失敗するので、両方を見直す。
    ツールがそれらを読み込まないことは、test_nativecheck.py で確かめる"""
    files = [PurePosixPath(f) for f in make_dist.UNSIGNED_FILES]
    assert [
        n for n in nativecheck.NOT_USED if not any(f.full_match(n) for f in files)
    ] == []


def test_repo_licenses_and_targets_agree():
    """リポジトリの licenses/ の全文には、どれも対応表の行があり、対応表の全文はどれもある。原文のまま置く全文の
    SHA-256 は記録と同じ。リポジトリの直下の LICENSE は AGPL-3.0 の原文で、pyproject.toml がツールのライセンスを
    AGPL-3.0-only とし、LICENSE と追加の許可（第7条）のファイルを配布物に入れる。配布の README（日本語版・英語版）は、
    GPL・LGPL の全文の場所を書いている"""
    src = make_dist.ROOT / "licenses"
    rows = make_dist.read_targets(src)
    texts = {
        p.relative_to(src).as_posix()
        for p in src.rglob("*")
        if p.is_file() and p.name != make_dist.LICENSE_TARGETS
    }
    assert {t for t, _ in rows} == texts
    for name, digest in make_dist.LICENSE_TEXTS.items():
        assert hashlib.sha256((src / name).read_bytes()).hexdigest() == digest
    project_license = make_dist.ROOT / make_dist.PROJECT_LICENSE
    assert (
        hashlib.sha256(project_license.read_bytes()).hexdigest()
        == make_dist.PROJECT_LICENSE_SHA256
    )
    project = tomllib.loads((make_dist.ROOT / "pyproject.toml").read_text("utf-8"))
    assert project["project"]["license"] == "AGPL-3.0-only"
    assert project["project"]["license-files"] == [
        make_dist.PROJECT_LICENSE,
        make_dist.PROJECT_LICENSE_EXCEPTION,
    ]
    exception = (make_dist.ROOT / make_dist.PROJECT_LICENSE_EXCEPTION).read_text(
        "utf-8"
    )
    assert "Additional permission under GNU AGPL version 3 section 7" in exception
    for readme_name in make_dist.README_FILES:
        readme = (make_dist.ROOT / "windows" / readme_name).read_text("utf-8-sig")
        for name in ("GPL-3.0.txt", "LGPL-3.0.txt"):
            assert f"{make_dist.LICENSE_DIR}\\{name}" in readme


def fake_dist(site: Path, name: str, version: str, meta: str, files=(), extra=()):
    """仮の配布物（dist-info の METADATA と RECORD）。files はライセンスのファイル、extra はほかのファイル
    （どちらも site-packages からの位置）"""
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n{meta}\n"
    )
    for rel in (*files, *extra):
        (site / rel).parent.mkdir(parents=True, exist_ok=True)
        (site / rel).write_text(f"text of {rel}")
    record = [f"{info.name}/METADATA,,", *(f"{r},," for r in (*files, *extra))]
    (info / "RECORD").write_text("\n".join(record) + "\n")


def license_setup(tmp_path: Path, rows: list[str], texts=("qt/x.txt", "vc.txt")):
    """仮のパッケージ（python フォルダと site-packages の配布物）と、仮のリポジトリの licenses/"""
    base = tmp_path / make_dist.FOLDER
    site = base / "python" / "Lib" / "site-packages"
    (base / "python").mkdir(parents=True)
    (base / "python" / "LICENSE.txt").write_text("PSF")
    (base / "python" / "vcruntime140.dll").write_bytes(b"MZ")
    fake_dist(
        site,
        "alpha",
        "1.0",
        "License-Expression: MIT",
        ["alpha-1.0.dist-info/licenses/LICENSE"],
    )
    fake_dist(
        site,
        "beta",
        "2.0",
        "Classifier: License :: OSI Approved :: Apache Software License",
    )
    fake_dist(
        site,
        "gamma",
        "3.0",
        "License: BSD",
        ["gamma/LICENSE.txt", "gamma-3.0.dist-info/COPYING"],
        ["gamma/core.dll", "gamma/__init__.py"],
    )
    fake_dist(
        site,
        "sumikeshi-studio",
        "0.1.0",
        "License-Expression: AGPL-3.0-only",
        [
            "sumikeshi_studio-0.1.0.dist-info/licenses/LICENSE",
            "sumikeshi_studio-0.1.0.dist-info/licenses/LICENSE-EXCEPTION.txt",
        ],
    )
    for name in (make_dist.PROJECT_LICENSE, make_dist.PROJECT_LICENSE_EXCEPTION):
        shutil.copy2(make_dist.ROOT / name, tmp_path / name)
    src = tmp_path / "licenses"
    src.mkdir()
    shutil.copy2(make_dist.ROOT / "licenses" / "Apache-2.0.txt", src / "Apache-2.0.txt")
    for name in texts:
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_text(f"text {name}")
    (src / make_dist.LICENSE_TARGETS).write_text("# 説明\n" + "\n".join(rows) + "\n")
    return base, site, src


ROWS = [
    "Apache-2.0.txt\tbeta",
    "qt/x.txt\tpython/Lib/site-packages/gamma/core.dll",
    "vc.txt\tpython/vcruntime140*.dll",
]


def test_make_licenses_copies_texts_and_writes_index(tmp_path):
    """リポジトリの全文と、配布物ごとのライセンスのファイル（配布物の名前のフォルダ）をコピーし、配布物ごとのバージョン・
    ライセンス・全文の場所の一覧を書く。ファイルの型の対象は、そのファイルを持つ配布物（python フォルダなら Python）に
    付ける。ツール自身の配布物も、ほかの配布物と同じに一覧に入れる"""
    base, site, src = license_setup(tmp_path, ROWS)
    index = make_dist.make_licenses(base, site, src)
    dest = base / make_dist.LICENSE_DIR
    assert sorted(
        p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()
    ) == [
        "Apache-2.0.txt",
        "alpha/LICENSE",
        "gamma/COPYING",
        "gamma/gamma/LICENSE.txt",
        "index.txt",
        "qt/x.txt",
        "sumikeshi-studio/LICENSE",
        "sumikeshi-studio/LICENSE-EXCEPTION.txt",
        "vc.txt",
    ]
    assert (dest / "gamma/gamma/LICENSE.txt").read_text() == "text of gamma/LICENSE.txt"
    assert index.split("\r\n") == [
        "Licenses of the components included in this package",
        "(made by tools/make_dist.py when this package was built)",
        "",
        f"Python {make_dist.PYTHON_VERSION} (python.org)",
        "  License: PSF-2.0",
        "  python\\LICENSE.txt",
        "  licenses\\vc.txt",
        "",
        "alpha 1.0",
        "  License: MIT",
        "  licenses\\alpha\\LICENSE",
        "",
        "beta 2.0",
        "  License: Apache Software License",
        "  licenses\\Apache-2.0.txt",
        "",
        "gamma 3.0",
        "  License: BSD",
        "  licenses\\gamma\\COPYING",
        "  licenses\\gamma\\gamma\\LICENSE.txt",
        "  licenses\\qt\\x.txt",
        "",
        "sumikeshi-studio 0.1.0",
        "  License: AGPL-3.0-only",
        "  licenses\\sumikeshi-studio\\LICENSE",
        "  licenses\\sumikeshi-studio\\LICENSE-EXCEPTION.txt",
        "",
        "Microsoft Visual C++ Runtime files in this package",
        "  Terms: licenses\\vc.txt",
        "  python\\vcruntime140.dll",
        f"    from: Python {make_dist.PYTHON_VERSION} (python.org)",
        "",
    ]
    assert (dest / "index.txt").read_bytes() == index.encode("utf-8-sig")


def test_make_licenses_lists_vc_runtime_files(tmp_path):
    """一覧の最後に、配布用パッケージの中の Visual C++ のランタイムのファイルを全部（名前に「-」と16進の文字を付けたものも）、
    場所と、どの配布物から来たかを付けて書く。make_dist がコピーしたものは、コピー元の配布物と、コピー元の場所を書く"""
    rows = [
        *ROWS,
        "vc.txt\tpython/msvcp140*.dll",
        "vc.txt\tpython/Lib/site-packages/delta/*140*.dll",
        "vc.txt\tpython/Lib/site-packages/delta.libs/msvcp140-*.dll",
    ]
    base, site, src = license_setup(tmp_path, rows)
    fake_dist(
        site,
        "delta",
        "4.0",
        "License: BSD",
        ["delta/LICENSE"],
        [
            "delta/msvcp140.dll",
            "delta/vcomp140.dll",
            "delta/core.pyd",
            "delta.libs/msvcp140-0123abcd.dll",
        ],
    )
    shutil.copy2(site / "delta" / "msvcp140.dll", base / "python" / "msvcp140.dll")
    index = make_dist.make_licenses(
        base,
        site,
        src,
        copies={"python/msvcp140.dll": "python/Lib/site-packages/delta/msvcp140.dll"},
    )
    lines = index.split("\r\n")
    assert lines[lines.index("delta 4.0") :] == [
        "delta 4.0",
        "  License: BSD",
        "  licenses\\delta\\delta\\LICENSE",
        "  licenses\\vc.txt",
        "",
        "gamma 3.0",
        "  License: BSD",
        "  licenses\\gamma\\COPYING",
        "  licenses\\gamma\\gamma\\LICENSE.txt",
        "  licenses\\qt\\x.txt",
        "",
        "sumikeshi-studio 0.1.0",
        "  License: AGPL-3.0-only",
        "  licenses\\sumikeshi-studio\\LICENSE",
        "  licenses\\sumikeshi-studio\\LICENSE-EXCEPTION.txt",
        "",
        "Microsoft Visual C++ Runtime files in this package",
        "  Terms: licenses\\vc.txt",
        "  python\\Lib\\site-packages\\delta.libs\\msvcp140-0123abcd.dll",
        "    from: delta 4.0",
        "  python\\Lib\\site-packages\\delta\\msvcp140.dll",
        "    from: delta 4.0",
        "  python\\Lib\\site-packages\\delta\\vcomp140.dll",
        "    from: delta 4.0",
        "  python\\msvcp140.dll",
        "    from: delta 4.0",
        "    copied from: python\\Lib\\site-packages\\delta\\msvcp140.dll",
        "  python\\vcruntime140.dll",
        f"    from: Python {make_dist.PYTHON_VERSION} (python.org)",
        "",
    ]


@pytest.mark.parametrize(
    ("row", "add", "in_record", "expected"),
    [
        # 対応表の型のどれにも当てはまらない（gamma の配布物にある concrt140.dll）
        (
            None,
            "gamma/concrt140.dll",
            True,
            (
                "対応表のファイルの型のどれにも当てはまらない Visual C++ のランタイム："
                "python/Lib/site-packages/gamma/concrt140.dll"
            ),
        ),
        # 型には当てはまるが、どの配布物の RECORD にもない
        (
            "vc.txt\tpython/Lib/site-packages/stray/*.dll",
            "stray/vcruntime140.dll",
            False,
            (
                "どの配布物から来たかわからない Visual C++ のランタイム："
                "python/Lib/site-packages/stray/vcruntime140.dll"
            ),
        ),
    ],
)
def test_make_licenses_stops_on_unlisted_vc_runtime(
    tmp_path, row, add, in_record, expected
):
    base, site, src = license_setup(tmp_path, [*ROWS, *([row] if row else [])])
    (site / add).parent.mkdir(parents=True, exist_ok=True)
    (site / add).write_bytes(b"MZ")
    if in_record:
        with (site / "gamma-3.0.dist-info" / "RECORD").open("a") as f:
            f.write(f"{add},,\n")
    with pytest.raises(SystemExit, match=re.escape(expected)):
        make_dist.make_licenses(base, site, src)
    assert not (base / make_dist.LICENSE_DIR).exists()


@pytest.mark.parametrize(
    ("rows", "texts", "expected"),
    [
        # 足りない：ライセンスのファイルも対応表の行もない配布物（beta）
        (
            ROWS[1:],
            ("qt/x.txt", "vc.txt"),
            "足りない：beta 2.0（配布物にライセンスのファイルがなく",
        ),
        # 余計：対象が配布用パッケージに1つもない全文
        (
            [
                *ROWS[:2],
                "vc.txt\tpython/Lib/site-packages/nothere.dll",
                "vc.txt\tdelta",
            ],
            ("qt/x.txt", "vc.txt"),
            "余計な全文（対象が配布用パッケージに1つもない）：vc.txt（対象：python/Lib/site-packages/nothere.dll、delta）",
        ),
        # 対応表に行のない全文
        (ROWS, ("qt/x.txt", "vc.txt", "extra.txt"), "対応表に行がない全文：extra.txt"),
        # 対応表にあって licenses/ にない全文
        (
            [*ROWS, "gone.txt\tbeta"],
            ("qt/x.txt", "vc.txt"),
            "対応表にあって licenses/ にない全文：gone.txt",
        ),
    ],
)
def test_make_licenses_stops_on_missing_or_extra(tmp_path, rows, texts, expected):
    base, site, src = license_setup(tmp_path, rows, texts)
    with pytest.raises(SystemExit, match=re.escape(expected)):
        make_dist.make_licenses(base, site, src)
    assert not (base / make_dist.LICENSE_DIR).exists()


def test_make_licenses_checks_original_texts(tmp_path):
    """原文のまま置く全文（GPL・LGPL・Apache-2.0）の SHA-256 が記録と違えば止める"""
    base, site, src = license_setup(tmp_path, ROWS)
    (src / "Apache-2.0.txt").write_text("changed")
    with pytest.raises(
        SystemExit, match="licenses/Apache-2.0.txt の SHA-256 が記録と違います"
    ):
        make_dist.make_licenses(base, site, src)


@pytest.mark.parametrize(
    ("name", "change", "expected"),
    [
        ("LICENSE", "changed", "リポジトリの直下の LICENSE が、AGPL-3.0 の原文"),
        ("LICENSE", None, "リポジトリの直下の LICENSE が、AGPL-3.0 の原文"),
        (
            "LICENSE-EXCEPTION.txt",
            None,
            "リポジトリの直下に、AGPL-3.0 の追加の許可のファイル LICENSE-EXCEPTION.txt がありません",
        ),
    ],
)
def test_make_licenses_checks_project_license(tmp_path, name, change, expected):
    """リポジトリの直下の LICENSE（ツールのライセンス）が AGPL-3.0 の原文と違うか、ないとき、追加の許可のファイルが
    ないときは止める"""
    base, site, src = license_setup(tmp_path, ROWS)
    if change is None:
        (tmp_path / name).unlink()
    else:
        (tmp_path / name).write_text(change)
    with pytest.raises(SystemExit, match=re.escape(expected)):
        make_dist.make_licenses(base, site, src)
    assert not (base / make_dist.LICENSE_DIR).exists()


def test_make_licenses_needs_project_license_files_in_bundle(tmp_path):
    """配布用パッケージに入るツール自身の配布物に、LICENSE と追加の許可のファイルがなければ止める（pyproject.toml の
    license-files から外れたとき）"""
    base, site, src = license_setup(tmp_path, ROWS)
    record = site / "sumikeshi_studio-0.1.0.dist-info" / "RECORD"
    record.write_text(
        "".join(
            line
            for line in record.read_text().splitlines(keepends=True)
            if "LICENSE-EXCEPTION" not in line
        )
    )
    with pytest.raises(
        SystemExit,
        match=re.escape(
            "ツール自身の配布物（sumikeshi-studio）に、LICENSE-EXCEPTION.txt がありません"
        ),
    ):
        make_dist.make_licenses(base, site, src)


def test_vc_runtime_versions(tmp_path):
    """配布用パッケージの中の Visual C++ のランタイム（numpy・shapely が名前を変えて同梱するものを含む）の、バージョンの情報のファイルの
    バージョンを読む（バージョンを上げるときに、Microsoft の修正と比べるため）"""

    def dll(version: tuple[int, int, int, int]) -> bytes:
        a, b, c, d = version
        info = struct.pack("<IIII", 0xFEEF04BD, 0x10000, (a << 16) | b, (c << 16) | d)
        return b"MZ" + bytes(100) + info + bytes(40)

    assert make_dist.file_version(dll((14, 44, 35211, 0))) == "14.44.35211.0"
    assert make_dist.file_version(b"MZ" + bytes(200)) is None
    (tmp_path / "python" / "Lib" / "site-packages" / "shapely.libs").mkdir(parents=True)
    (tmp_path / "python" / "vcruntime140.dll").write_bytes(dll((14, 51, 36247, 0)))
    libs = tmp_path / "python" / "Lib" / "site-packages" / "shapely.libs"
    (libs / "msvcp140-0fa7eb792d3fbcf2233e4ea47e9144b9.dll").write_bytes(
        dll((14, 44, 35229, 0))
    )
    (libs / "geos-abc.dll").write_bytes(dll((3, 14, 1, 0)))  # ランタイムではない
    assert make_dist.vc_runtime_versions(tmp_path) == {
        "python/Lib/site-packages/shapely.libs/msvcp140-0fa7eb792d3fbcf2233e4ea47e9144b9.dll": "14.44.35229.0",
        "python/vcruntime140.dll": "14.51.36247.0",
    }


def test_names_of_bundle():
    """配布用パッケージの名前：ZIP は sumikeshi-studio-<バージョン>-<日付>-win64.zip、中の先頭のフォルダは sumikeshi-studio、
    中のファイルとフォルダの名前は英語。windows/ の配置のまま入れるので、一番上にはダブルクリックする .bat と README だけを
    置き、PowerShell のスクリプトは scripts/ に置く"""
    assert (make_dist.NAME, make_dist.FOLDER) == (
        "sumikeshi-studio",
        "sumikeshi-studio",
    )
    assert (make_dist.LICENSE_DIR, version.VERSION_FILE) == ("licenses", "version.txt")
    assert make_dist.README_FILES == ("README-ja.txt", "README-en.txt")
    windows = make_dist.ROOT / "windows"
    names = [p.relative_to(windows).as_posix() for p in windows.rglob("*")]
    assert sorted(names) == [
        "README-en.txt",
        "README-ja.txt",
        "scripts",
        "scripts/install.ps1",
        "scripts/uninstall.ps1",
        "setup.bat",
        "start.bat",
        "sumikeshi.bat",
        "uninstall.bat",
    ]


def test_readmes_get_measured_path_lengths():
    """配布用パッケージを作るときに測った ZIP の中の最長のパスと、展開先のフォルダの名前の上限（260 から最長のパスと3文字を引いた
    値）を、README の日本語版・英語版の文に書き込む。文がちょうど1つないときは止める（古い数のまま配布用パッケージを作らない
    ため）。今の README の数は見込みの値（136文字。先頭のフォルダの名前が5文字短い配布用パッケージで測った最長のパス（pypdfium2
    のライセンスの文）の131文字に、5文字を足したもの）"""
    dist = make_dist.ROOT / "windows"
    for name in make_dist.README_FILES:
        text = (dist / name).read_text("utf-8-sig")
        assert make_dist.set_path_lengths(text, 136) == text  # 今の数は見込みの値
        filled = make_dist.set_path_lengths(text, 140)
        assert ("140" in filled and "117" in filled) and filled != text
    assert (
        make_dist.folder_limit(131) == 126
    )  # 前の README の数（131文字・126文字）と同じ決め方
    with pytest.raises(SystemExit, match="1つではありません（0個）"):
        make_dist.set_path_lengths("最長のパスの文のない README", 136)


def test_version_is_the_same_everywhere():
    """ツールのバージョンは、pyproject.toml の version と __version__ と uv.lock の sumikeshi-studio の version で
    同じ。バージョンを上げるときに、どれかを書き換え忘れないため"""
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = [p["version"] for p in lock["package"] if p["name"] == "sumikeshi-studio"]
    assert project["project"]["version"] == __version__
    assert locked == [__version__]


def run_git(cwd: Path, *args: str) -> str:
    """利用者の設定を読まずに git を動かす"""
    import os
    import subprocess

    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    r = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True
    )
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """コミットが1つある一時のリポジトリ（ROOT の外。source_commit が上のフォルダのリポジトリを見ないように、
    GIT_CEILING_DIRECTORIES で探す範囲を限る）"""
    if not make_dist.shutil.which("git"):
        pytest.skip("git がない")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    root = tmp_path / "repo"
    root.mkdir()
    run_git(root, "init", "-q")
    (root / "a.txt").write_text("a")
    run_git(root, "add", "a.txt")
    run_git(root, "commit", "-q", "-m", "a")
    return root


def test_source_commit_without_changes(repo):
    """version.txt の Commit の行の値：変更がなければ 40桁のハッシュだけ"""
    head = run_git(repo, "rev-parse", "HEAD")
    (repo / "untracked.txt").write_text("x")  # 追跡していないファイルは数えない
    assert make_dist.source_commit(repo) == head


def test_source_commit_with_uncommitted_changes(repo):
    head = run_git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("changed")
    assert make_dist.source_commit(repo) == f"{head} (uncommitted changes)"


def test_source_commit_outside_repository(tmp_path, monkeypatch):
    """git のリポジトリでない・git がないときは unknown（配布用パッケージを作るのは止めない）"""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    plain = tmp_path / "plain"
    plain.mkdir()
    assert make_dist.source_commit(plain) == "unknown"
    monkeypatch.setattr(make_dist.shutil, "which", lambda name: None)
    assert make_dist.source_commit(ROOT) == "unknown"


def test_commit_line_does_not_change_build_time(monkeypatch, tmp_path):
    """version.txt に Commit の行があっても、起動の画面のバージョン（version.made_at）は Built の行を読む"""
    monkeypatch.setattr(make_dist, "pdfium_line", lambda site: "PDFium: x")
    text = make_dist.version_file_text(
        datetime(2026, 11, 13, 9, 30), tmp_path, "b" * 40 + " (uncommitted changes)"
    )
    assert "\r\nCommit: " + "b" * 40 + " (uncommitted changes)\r\n" in text
    monkeypatch.setattr(version, "_version_file", lambda: text)
    assert version.made_at() == "2026-11-13 09:30"
    assert version.version_text() == f"{__version__} (built 2026-11-13 09:30)"


def test_public_package_writes_source_without_commit(monkeypatch, tmp_path):
    """公開用のパッケージ（commit が None）の version.txt は、Commit の行を書かず、Source の行を書く"""
    monkeypatch.setattr(make_dist, "pdfium_line", lambda site: "PDFium: x")
    text = make_dist.version_file_text(datetime(2026, 11, 17, 9, 0), tmp_path, None)
    assert text.split("\r\n") == [
        f"Sumikeshi Studio {__version__}",
        "Built: 2026-11-17 09:00",
        f"Source: https://github.com/hohm-sdx/sumikeshi-studio (tag v{__version__})",
        f"Python: {make_dist.PYTHON_FILE} (python.org)",
        "PDFium: x",
        "",
    ]


def test_public_package_needs_committed_tree(monkeypatch):
    """公開用のパッケージは、コミットしていない変更があるときと、元のコミットが分からないときは、作り始める前に止める"""
    assert make_dist.public_commit_problem("c" * 40) is None
    assert "コミットしていない変更" in make_dist.public_commit_problem(
        "c" * 40 + " (uncommitted changes)"
    )
    assert "元のコミットが分からない" in make_dist.public_commit_problem("unknown")
    monkeypatch.setattr(
        make_dist, "source_commit", lambda: "c" * 40 + " (uncommitted changes)"
    )
    monkeypatch.setattr(make_dist, "signature_tool", lambda: pytest.fail("作り始めた"))
    with pytest.raises(SystemExit, match="コミットしていない変更"):
        make_dist.main(["--public"])


# ---- 配布用パッケージに入れる PySide6 の部品


def pe_with_imports(imports=(), delayed=()) -> bytes:
    """インポート表と遅延読み込みの表だけを持つ、64ビットの PE（動かない。dll_imports が読む所だけを持つ）"""
    va, raw = 0x1000, 0x200
    dly = 20 * (len(imports) + 1)  # 表の終わりの、0 の項目を含む
    names_at = dly + 32 * (len(delayed) + 1)
    body = bytearray(names_at)
    names = b""
    rvas = {}
    for name in (*imports, *delayed):
        rvas[name] = va + names_at + len(names)
        names += name.encode("ascii") + b"\0"
    for i, name in enumerate(imports):
        struct.pack_into("<5I", body, 20 * i, 0, 0, 0, rvas[name], 0)
    for i, name in enumerate(delayed):
        struct.pack_into("<8I", body, dly + 32 * i, 1, rvas[name], 0, 0, 0, 0, 0, 0)
    body += names
    pe, opt = 0x80, 0x80 + 24
    head = bytearray(raw)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, pe)
    head[pe : pe + 4] = b"PE\0\0"
    struct.pack_into("<HH", head, pe + 4, 0x8664, 1)  # 64ビットの CPU、セクションは1つ
    struct.pack_into("<H", head, pe + 20, 240)  # オプションのヘッダーの大きさ
    struct.pack_into("<H", head, opt, 0x20B)  # PE32+
    if imports:
        struct.pack_into("<II", head, opt + 112 + 8 * 1, va, dly)
    if delayed:
        struct.pack_into("<II", head, opt + 112 + 8 * 13, va + dly, names_at - dly)
    struct.pack_into(
        "<8sIIII", head, opt + 240, b".idata", len(body), va, len(body), raw
    )
    return bytes(head) + bytes(body)


def test_provide_vc_runtime_copies_missing_runtime_from_pyside6(tmp_path):
    """ライブラリが読み込む Visual C++ のランタイムのうち、同じフォルダにも python フォルダにもないものだけを、
    PySide6 が同梱しているものから python フォルダにコピーする。PySide6 にもなければ止まる"""
    python = tmp_path / "python"
    site = python / "Lib" / "site-packages"
    files = {
        "onnxruntime/capi/state.pyd": pe_with_imports(
            ["KERNEL32.dll", "MSVCP140.dll", "VCRUNTIME140.dll", "VCRUNTIME140_1.dll"]
        ),
        "cv2/cv2.pyd": pe_with_imports(["MSVCP140.dll"]),
        "cv2/msvcp140.dll": b"opencv",  # 同じフォルダにある
        "PySide6/msvcp140.dll": b"pyside msvcp140",
        "PySide6/vcruntime140_1.dll": b"pyside vcruntime140_1",
    }
    for rel, data in files.items():
        (site / rel).parent.mkdir(parents=True, exist_ok=True)
        (site / rel).write_bytes(data)
    (python / "vcruntime140.dll").write_bytes(b"python")  # python フォルダにある
    copies = make_dist.provide_vc_runtime(python, site)
    assert copies == {
        python / "msvcp140.dll": site / "PySide6" / "msvcp140.dll",
        python / "vcruntime140_1.dll": site / "PySide6" / "vcruntime140_1.dll",
    }
    assert (python / "msvcp140.dll").read_bytes() == b"pyside msvcp140"
    # あるものはコピーしない
    assert (python / "vcruntime140.dll").read_bytes() == b"python"
    (site / "onnxruntime" / "capi" / "omp.pyd").write_bytes(
        pe_with_imports(["VCOMP140.dll"])
    )
    with pytest.raises(SystemExit, match="vcomp140.dll"):
        make_dist.provide_vc_runtime(python, site)


def test_dll_imports_reads_delay_load_table():
    """インポート表の DLL を返し、delayed なら遅延読み込みの表の DLL も足す"""
    data = pe_with_imports(["KERNEL32.dll", "Qt6Core.dll"], ["Qt6Network.dll"])
    assert make_dist.dll_imports(data) == ["KERNEL32.dll", "Qt6Core.dll"]
    assert make_dist.dll_imports(data, delayed=True) == [
        "KERNEL32.dll",
        "Qt6Core.dll",
        "Qt6Network.dll",
    ]


def make_qt(site: Path, extra: dict[str, bytes] | None = None) -> None:
    """仮の PySide6・shiboken6（残すもの・除くもの）"""
    files = {
        "PySide6/__init__.py": b"",
        "PySide6/support/__init__.py": b"",
        "PySide6/QtCore.pyd": pe_with_imports(["Qt6Core.dll", "pyside6.abi3.dll"]),
        "PySide6/QtCore.pyi": b"",
        "PySide6/QtNetwork.pyd": pe_with_imports(["Qt6Network.dll"]),
        "PySide6/Qt6Core.dll": pe_with_imports(["KERNEL32.dll", "MSVCP140.dll"]),
        "PySide6/Qt6Network.dll": pe_with_imports(["Qt6Core.dll"], ["DNSAPI.dll"]),
        "PySide6/pyside6.abi3.dll": pe_with_imports(["shiboken6.abi3.dll"]),
        "PySide6/msvcp140.dll": pe_with_imports(),
        "PySide6/vcomp140.dll": pe_with_imports(),  # 読み込む部品のないランタイムも残す
        "PySide6/plugins/platforms/qwindows.dll": pe_with_imports(["Qt6Core.dll"]),
        "PySide6/plugins/platforms/qminimal.dll": b"MZ",
        "PySide6/plugins/imageformats/qjpeg.dll": b"MZ",
        "PySide6/Qt6Qml.dll": b"MZ",
        "PySide6/QtQml.pyd": b"MZ",
        "PySide6/QtQml.pyi": b"",
        "PySide6/opengl32sw.dll": b"MZ",
        "PySide6/translations/qtbase_ja.qm": b"",
        "PySide6/resources/icudtl.dat": b"",
        "shiboken6/Shiboken.pyd": pe_with_imports(["shiboken6.abi3.dll"]),
        "shiboken6/shiboken6.abi3.dll": pe_with_imports(["MSVCP140.dll"]),
        "shiboken6/msvcp140.dll": pe_with_imports(),
        "shiboken6/shiboken6.abi3.lib": b"",
        "shiboken6/include/sbkpython.h": b"",
        "shiboken6/lib/cmake/Shiboken6/Shiboken6Config.cmake": b"",
    } | (extra or {})
    for rel, data in files.items():
        (site / rel).parent.mkdir(parents=True, exist_ok=True)
        (site / rel).write_bytes(data)


def test_prune_pyside_keeps_used_modules_and_their_dlls(tmp_path):
    """使うモジュールと、それらが読み込む DLL・2つのプラグイン・ランタイム・.py を残し、ほかを除いて、空になった
    フォルダも消す。除いたファイルの位置と大きさを返す"""
    make_qt(tmp_path)
    removed = make_dist.prune_pyside(tmp_path)
    assert [rel for rel, _ in removed] == [
        "PySide6/Qt6Qml.dll",
        "PySide6/QtQml.pyd",
        "PySide6/QtQml.pyi",
        "PySide6/opengl32sw.dll",
        "PySide6/plugins/imageformats/qjpeg.dll",
        "PySide6/plugins/platforms/qminimal.dll",
        "PySide6/resources/icudtl.dat",
        "PySide6/translations/qtbase_ja.qm",
        "shiboken6/include/sbkpython.h",
        "shiboken6/lib/cmake/Shiboken6/Shiboken6Config.cmake",
        "shiboken6/shiboken6.abi3.lib",
    ]
    assert dict(removed)["PySide6/Qt6Qml.dll"] == 2
    left = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert left == [
        "PySide6",
        "PySide6/Qt6Core.dll",
        "PySide6/Qt6Network.dll",
        "PySide6/QtCore.pyd",
        "PySide6/QtCore.pyi",
        "PySide6/QtNetwork.pyd",
        "PySide6/__init__.py",
        "PySide6/msvcp140.dll",
        "PySide6/plugins",
        "PySide6/plugins/platforms",
        "PySide6/plugins/platforms/qwindows.dll",
        "PySide6/pyside6.abi3.dll",
        "PySide6/support",
        "PySide6/support/__init__.py",
        "PySide6/vcomp140.dll",
        "shiboken6",
        "shiboken6/Shiboken.pyd",
        "shiboken6/msvcp140.dll",
        "shiboken6/shiboken6.abi3.dll",
    ]


def test_prune_pyside_stops_when_kept_dll_needs_removed_one(tmp_path):
    """残した DLL が、除いた DLL をインポート表か遅延読み込みの表で読み込むなら止める"""
    make_qt(
        tmp_path,
        {
            "PySide6/Qt6Widgets.dll": pe_with_imports(
                ["Qt6Core.dll"], ["Qt6OpenGL.dll"]
            ),
            "PySide6/Qt6OpenGL.dll": b"MZ",
        },
    )
    with pytest.raises(
        SystemExit, match="PySide6/Qt6Widgets.dll が Qt6OpenGL.dll を読み込みます"
    ):
        make_dist.prune_pyside(tmp_path)


def test_ffmpeg_of_opencv_is_left_out():
    """OpenCV の動画の部品（FFmpeg を含む）は配布用パッケージから除くので、署名のない部品の一覧にも、ツールが使わない部品の
    一覧にも書かない"""
    name = PurePosixPath("cv2/opencv_videoio_ffmpeg500_64.dll")
    assert any(name.full_match(p) for p in make_dist.SITE_UNUSED_FILES)
    assert not any(
        PurePosixPath(f).full_match(p)
        for f in make_dist.UNSIGNED_FILES
        for p in make_dist.SITE_UNUSED_FILES
    )
    assert not any(name.full_match(p) for p in nativecheck.NOT_USED)


def test_pure_python_packages_and_yaml_c_part_are_left_out():
    """charset-normalizer は Python だけの配布物を入れ、PyYAML の C の部品（_yaml）は除くので、どちらの .pyd も署名の
    ない部品の一覧に書かない（署名がなく、スマート アプリ コントロールに止められると導入が止まるため）"""
    assert make_dist.PURE_PACKAGES == ("charset-normalizer",)
    name = PurePosixPath("yaml/_yaml.cp314-win_amd64.pyd")
    assert any(name.full_match(p) for p in make_dist.SITE_UNUSED_FILES)
    assert not [
        f
        for f in make_dist.UNSIGNED_FILES
        if f.startswith(("charset_normalizer/", "yaml/"))
    ]
    assert not any(
        PurePosixPath(f).full_match(p)
        for f in ("charset_normalizer/md.cp314-win_amd64.pyd", str(name))
        for p in nativecheck.NOT_USED
    )


def test_pure_hashes_come_from_lock():
    """Python だけの配布物の SHA-256 は、uv.lock のその配布物（-py3-none-any.whl）のもの。なければ止める"""
    lock = (make_dist.ROOT / "uv.lock").read_text("utf-8")
    hashes = make_dist.pure_hashes(lock)
    assert hashes == {
        "charset-normalizer": "sha256:b6b751274acb69d77b3323d6b7dbaa3c7fdfc1eb829b7eb61d262f32e1af9685"
    }
    with pytest.raises(SystemExit, match="pyyaml の Python だけの配布物"):
        make_dist.pure_hashes(lock, ("pyyaml",))


def test_keep_pure_hashes_rewrites_only_the_package():
    """uv export の一覧で、指定したライブラリの --hash の行だけを、その SHA-256 の1行にする（ほかの項目と、via の
    注釈は変えない）。項目がなければ止める"""
    requirements = (
        "# This file was autogenerated by uv\n"
        "certifi==2026.10.5 \\\n"
        "    --hash=sha256:aaa \\\n"
        "    --hash=sha256:bbb\n"
        "    # via requests\n"
        "charset-normalizer==3.5.2 ; sys_platform == 'linux' or sys_platform == 'win32' \\\n"
        "    --hash=sha256:ccc \\\n"
        "    --hash=sha256:ddd \\\n"
        "    --hash=sha256:eee\n"
        "    # via requests\n"
        "colorama==0.4.6 ; sys_platform == 'win32' \\\n"
        "    --hash=sha256:fff\n"
    )
    got = make_dist.keep_pure_hashes(
        requirements, {"charset-normalizer": "sha256:pure"}
    )
    assert got == (
        "# This file was autogenerated by uv\n"
        "certifi==2026.10.5 \\\n"
        "    --hash=sha256:aaa \\\n"
        "    --hash=sha256:bbb\n"
        "    # via requests\n"
        "charset-normalizer==3.5.2 ; sys_platform == 'linux' or sys_platform == 'win32' \\\n"
        "    --hash=sha256:pure\n"
        "    # via requests\n"
        "colorama==0.4.6 ; sys_platform == 'win32' \\\n"
        "    --hash=sha256:fff\n"
    )
    with pytest.raises(SystemExit, match="pyyaml がありません"):
        make_dist.keep_pure_hashes(requirements, {"pyyaml": "sha256:x"})


def test_readmes_point_to_public_source():
    """配布の README（日本語版・英語版）は、題名にツールの名前を日本語と英語で書き、この配布用パッケージに対応するソースの
    場所として、公開のリポジトリと、version.txt の Source の行（公開のリポジトリの同じバージョンのタグ）を示す"""
    dist = make_dist.ROOT / "windows"
    titles = {
        "README-ja.txt": f"{APP_NAME}（Sumikeshi Studio）配布用パッケージ",
        "README-en.txt": f"Sumikeshi Studio ({APP_NAME}) distribution package",
    }
    for name in make_dist.README_FILES:
        text = (dist / name).read_text("utf-8-sig")
        assert text.startswith(titles[name]), name
        assert make_dist.PUBLIC_REPOSITORY in text, name
        assert "Source" in text and "version.txt" in text, name


def test_pdfium_build_of_wheels_matches_build_script():
    """wheels/ の pypdfium2 の配布物（Windows 用と Linux 用）に入っている PDFium の build の番号が、
    tools/build_pypdfium2.py の PDFIUM_VERSION と同じ（PDFium のバージョンを上げるときに、片方だけ直すのを防ぐ）"""
    spec = importlib.util.spec_from_file_location(
        "build_pypdfium2", make_dist.ROOT / "tools" / "build_pypdfium2.py"
    )
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    wheels = sorted((make_dist.ROOT / "wheels").glob("pypdfium2-*.whl"))
    assert len(wheels) == 2
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as z:
            raw = json.loads(z.read("pypdfium2_raw/version.json"))
        assert (raw["build"], raw["origin"]) == (
            build.PDFIUM_VERSION,
            "pdfium-binaries",
        ), wheel.name
