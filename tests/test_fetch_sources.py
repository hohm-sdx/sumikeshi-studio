# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""配布用パッケージに入る LGPL の部品（Qt と GEOS）のソースをダウンロードするスクリプト（tools/fetch_sources.py）のテスト。ネットワークには
接続せず、ダウンロードする関数を置き換えて確かめる"""

import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fs = load("fetch_sources")


def test_records_cover_locked_versions():
    """uv.lock の PySide6-Essentials と shapely のバージョンに、ダウンロードする3つのアーカイブの SHA-256 がすべて記録されている
    （ライブラリのバージョンを上げたら、このテストが落ちて、記録を足すことに気づく）。qtbase の値は make_qt_licenses と同じ"""
    versions = fs.locked_versions()
    found = fs.archives(versions["pyside6-essentials"], versions["shapely"])
    assert [a.name for a in found] == [
        f"pyside-setup-everywhere-src-{versions['pyside6-essentials']}.tar.xz",
        f"qtbase-everywhere-src-{versions['pyside6-essentials']}.tar.xz",
        f"geos-{fs.GEOS_FOR_SHAPELY[versions['shapely']]}.tar.bz2",
    ]
    assert all(a.sha256 and len(a.sha256) == 64 for a in found)
    assert found[0].url.startswith(
        "https://download.qt.io/official_releases/QtForPython/"
    )
    assert found[2].url.startswith("https://download.osgeo.org/geos/")
    qt = load("make_qt_licenses")
    assert found[1].sha256 == qt.QTBASE_SHA256[versions["pyside6-essentials"]]


def test_unknown_shapely_version_stops():
    with pytest.raises(fs.Stop, match="GEOS_FOR_SHAPELY"):
        fs.archives("6.12.0", "0.0.1")


def fake_get(data: bytes, calls: list):
    def get(url, path):
        calls.append(url)
        path.write_bytes(data)

    return get


def test_fetch_checks_sha256(tmp_path):
    """記録と同じなら置く（同じ名前のファイルがあればダウンロードし直さない）。違えば消して止め、記録がなければ計算した値を
    出して止める"""
    data = b"source archive"
    good = hashlib.sha256(data).hexdigest()
    calls = []
    archive = fs.Archive("https://example.invalid/x/a.tar.xz", good)
    path = fs.fetch(archive, tmp_path, fake_get(data, calls))
    assert path == tmp_path / "a.tar.xz" and path.read_bytes() == data
    fs.fetch(archive, tmp_path, fake_get(data, calls))
    assert len(calls) == 1
    with pytest.raises(fs.Stop, match="記録と違うので消した"):
        fs.fetch(
            fs.Archive("https://example.invalid/b.tar.xz", "0" * 64),
            tmp_path,
            fake_get(data, calls),
        )
    assert not (tmp_path / "b.tar.xz").exists()
    with pytest.raises(fs.Stop, match=f"計算した SHA-256 は {good}"):
        fs.fetch(
            fs.Archive("https://example.invalid/c.tar.xz", None),
            tmp_path,
            fake_get(data, calls),
        )


def test_main_writes_sums_after_all_archives_match(tmp_path, monkeypatch, capsys):
    """すべて照合できたら、SHA256SUMS を sha256sum の形で書く。1つでも止まれば書かず、1 で終える"""
    data = b"archive"
    digest = hashlib.sha256(data).hexdigest()
    versions = fs.locked_versions()
    qt, shapely = versions["pyside6-essentials"], versions["shapely"]
    monkeypatch.setattr(
        fs, "QT_SHA256", {qt: {"pyside-setup": digest, "qtbase": digest}}
    )
    monkeypatch.setattr(fs, "GEOS_SHA256", {fs.GEOS_FOR_SHAPELY[shapely]: digest})
    out = tmp_path / "sources"
    assert fs.main(["--out", str(out)], get=fake_get(data, [])) == 0
    names = [a.name for a in fs.archives(qt, shapely)]
    assert (out / fs.SUMS).read_text("utf-8") == "".join(
        f"{digest}  {n}\n" for n in names
    )
    monkeypatch.setattr(fs, "GEOS_SHA256", {})
    other = tmp_path / "other"
    assert fs.main(["--out", str(other)], get=fake_get(data, [])) == 1
    assert not (other / fs.SUMS).exists()
    assert "止めました" in capsys.readouterr().err
