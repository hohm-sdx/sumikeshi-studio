# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""tools/make_qt_licenses.py（Qt の部品の許諾文を、qtbase のソースのアーカイブから作る）のテスト

インターネットにも本物のアーカイブにも頼らず、作り物の qt_attribution.json と許諾文を入れた小さな tar.xz で確かめる。
本物の Qt 6.12.0 のアーカイブで、licenses/qt/ と targets.tsv がバイト単位で同じにできることは、バージョンを上げるときに
スクリプトを動かして確かめる。
"""

import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "make_qt_licenses", ROOT / "tools" / "make_qt_licenses.py"
)
mq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mq)

TOP = "qtbase-everywhere-src-9.9.9"
ATTRIBUTION = [
    {
        "Id": "alpha",
        "Name": "Alpha",
        "Version": "1.0",
        "Homepage": "https://alpha.example",
        "LicenseId": "BSD-3-Clause",
        "LicenseFile": "COPYING",
        "Copyright": ["Copyright A", "Copyright B"],
        "QtUsage": "Used in\n   Qt Core.",
    },
    {
        "Id": "beta",
        "Name": "Beta",
        "LicenseId": "CC0-1.0 OR Apache-2.0",
        "Copyright": "",
        "QtUsage": "Used in Qt Gui.",
    },
    {
        "Id": "gamma",
        "Name": "Gamma",
        "Version": "2.0",
        "DownloadLocation": "https://gamma.example/dl",
        "LicenseId": "FTL OR GPL-2.0-only",
        "LicenseFiles": ["LICENSE.TXT"],
        "Copyright": "Copyright G\n",
        "QtUsage": "Used in Qt Gui.",
    },
    {"Id": "skip", "Name": "Skip", "LicenseId": "MIT", "QtUsage": "Used on Linux."},
]


def make_archive(path: Path, attribution=ATTRIBUTION, extra=None, links=None) -> Path:
    """作り物の qtbase のアーカイブ（tar.xz）。extra はアーカイブの先頭のフォルダからのパス → 中身、links は
    パス → リンク先"""
    files = {
        "LICENSES/CC0-1.0.txt": "CC0 の全文\n",
        "LICENSES/FTL.txt": "FTL の全文\n\n",
        "src/3rdparty/a/COPYING": "Alpha の許諾文\n",
        "src/3rdparty/a/LICENSE.TXT": "Gamma の許諾文（FTL を指す）",
        "src/3rdparty/a/qt_attribution.json": json.dumps(attribution),
        **(extra or {}),
    }
    with tarfile.open(path, "w:xz") as tar:
        for name, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(f"{TOP}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(f"{TOP}/{name}")
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)
    return path


@pytest.fixture
def lists(monkeypatch):
    """作り物の部品に合わせた一覧（入れる部品・入れない部品・OR の選び方）"""
    monkeypatch.setattr(
        mq,
        "INCLUDED",
        {"alpha": [mq.CORE], "beta": [mq.GUI], "gamma": [mq.GUI, mq.QWINDOWS]},
    )
    monkeypatch.setattr(mq, "EXCLUDED", {"skip": "Linux だけで使う"})
    monkeypatch.setattr(
        mq,
        "CHOSEN",
        {
            "beta": ("CC0-1.0 OR Apache-2.0", "CC0-1.0"),
            "gamma": ("FTL OR GPL-2.0-only", "FTL"),
        },
    )


def test_file_format(tmp_path, lists):
    """部品ごとのファイルは、名前・Id・バージョン・ホームページ・ライセンス・入っている DLL・Qt での使い道・著作権の表示と、
    許諾文（アーカイブの中のパスと中身）を書く。targets.tsv の行は、部品ごとに DLL の数だけ書く"""
    files, rows = mq.build(make_archive(tmp_path / "q.tar.xz"), "9.9.9")
    assert list(files) == ["alpha", "beta", "gamma"]
    assert files["alpha"] == "\n".join(
        [
            "Third-party component of Qt 9.9.9 (qtbase), included in this package",
            "Name: Alpha",
            "Id: alpha",
            "Version: 1.0",
            "Homepage: https://alpha.example",
            "License: BSD-3-Clause",
            "In this package: Qt6Core.dll",
            "Qt usage: Used in Qt Core.",
            "",
            "Copyright:",
            "Copyright A",
            "Copyright B",
            "",
            "=" * 78,
            "src/3rdparty/a/COPYING (from the Qt 9.9.9 source, qtbase)",
            "=" * 78,
            "Alpha の許諾文",
            "",
        ]
    )
    assert rows == [
        "qt/alpha.txt\t" + mq.CORE,
        "qt/beta.txt\t" + mq.GUI,
        "qt/gamma.txt\t" + mq.GUI,
        "qt/gamma.txt\t" + mq.QWINDOWS,
    ]


def test_or_licenses_use_the_chosen_one(tmp_path, lists):
    """OR で選べるライセンスは、決めたものを使うと書く。許諾文のファイルがなければ、使うライセンスの全文を入れる。
    FTL を使う部品は、許諾文のファイルに加えて FTL の全文も入れる"""
    files, _ = mq.build(make_archive(tmp_path / "q.tar.xz"), "9.9.9")
    beta = files["beta"].splitlines()
    assert "License: CC0-1.0 OR Apache-2.0 (used under CC0-1.0)" in beta
    assert "Version: (not given)" in beta and "Homepage: (not given)" in beta
    assert "(see the license text)" in beta
    assert beta[-3:] == [
        "LICENSES/CC0-1.0.txt (from the Qt 9.9.9 source, qtbase)",
        "=" * 78,
        "CC0 の全文",
    ]
    gamma = files["gamma"]
    assert "License: FTL OR GPL-2.0-only (used under FTL)" in gamma
    assert "Homepage: https://gamma.example/dl" in gamma
    assert "In this package: Qt6Gui.dll, qwindows.dll" in gamma
    assert gamma.index("src/3rdparty/a/LICENSE.TXT (from") < gamma.index(
        "LICENSES/FTL.txt (from"
    )
    assert gamma.endswith("FTL の全文\n")


def test_unlisted_and_missing_components_stop(tmp_path, lists, monkeypatch):
    """qt_attribution.json にあってどちらの一覧にもない部品と、入れる一覧にあってソースにない部品があれば、止めて
    その部品を並べる"""
    new = [*ATTRIBUTION, {"Id": "newone", "Name": "New", "LicenseId": "MIT"}]
    with pytest.raises(mq.Stop, match="どちらの一覧にもない部品.*newone"):
        mq.build(make_archive(tmp_path / "new.tar.xz", new), "9.9.9")
    monkeypatch.setitem(mq.INCLUDED, "delta", [mq.CORE])
    with pytest.raises(mq.Stop, match="INCLUDED にあってソースにない部品：delta"):
        mq.build(make_archive(tmp_path / "q.tar.xz"), "9.9.9")


def test_or_license_without_choice_stops(tmp_path, lists, monkeypatch):
    """入れる部品のライセンスが OR で選べるのに選び方がない・決めた式と違うときは、止める"""
    monkeypatch.delitem(mq.CHOSEN, "beta")
    with pytest.raises(mq.Stop, match="beta のライセンスは OR で選べる"):
        mq.build(make_archive(tmp_path / "q.tar.xz"), "9.9.9")
    monkeypatch.setitem(mq.CHOSEN, "beta", ("MIT OR Apache-2.0", "MIT"))
    with pytest.raises(mq.Stop, match="選び方を決めた式と違う"):
        mq.build(make_archive(tmp_path / "q.tar.xz"), "9.9.9")


def test_sha256_is_checked(tmp_path, monkeypatch):
    """アーカイブの SHA-256 が記録と違えば止める。記録のないバージョンでは、計算した SHA-256 と、md5sums.txt の MD5 と比べた
    結果を出して止める"""
    archive = make_archive(tmp_path / "q.tar.xz")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setattr(mq, "QTBASE_SHA256", {"9.9.9": digest})
    mq.check_archive(archive, "9.9.9")
    monkeypatch.setattr(mq, "QTBASE_SHA256", {"9.9.9": "0" * 64})
    with pytest.raises(mq.Stop, match="SHA-256 が記録と違う"):
        mq.check_archive(archive, "9.9.9")
    md5 = hashlib.md5(archive.read_bytes()).hexdigest()
    urls = []

    def fake_urlopen(url, timeout):
        urls.append(url)
        return io.BytesIO(f"{md5}  {mq.ARCHIVE.format(version='9.9.8')}\n".encode())

    monkeypatch.setattr(mq.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(mq.Stop) as e:
        mq.check_archive(archive, "9.9.8")
    assert f"計算した SHA-256 {digest}" in str(e.value)
    assert "md5sums.txt の MD5 と一致" in str(e.value)
    assert urls == [
        "https://download.qt.io/official_releases/qt/9.9/9.9.8/submodules/md5sums.txt"
    ]


@pytest.mark.parametrize(
    "case",
    ["親のフォルダ", "絶対のパス", "外を指すリンク", "ファイルの名前にできない Id"],
)
def test_paths_outside_archive_are_refused(case, tmp_path, lists, monkeypatch):
    """許諾文のファイルのパスやリンクがアーカイブの外を指すとき、Id がファイルの名前にできないときは、読まず書かずに
    止める"""
    attribution = [dict(e) for e in ATTRIBUTION]
    links = {}
    if case == "親のフォルダ":
        attribution[0]["LicenseFile"] = "../../../../outside.txt"
    elif case == "絶対のパス":
        attribution[0]["LicenseFile"] = "/etc/passwd"
    elif case == "外を指すリンク":
        attribution[0]["LicenseFile"] = "LINK"
        links = {"src/3rdparty/a/LINK": "../../../../outside.txt"}
    else:
        attribution[0]["Id"] = "../alpha"
        monkeypatch.setattr(
            mq, "INCLUDED", {"../alpha": [mq.CORE], "beta": [mq.GUI], "gamma": [mq.GUI]}
        )
    with pytest.raises(mq.Stop):
        mq.build(make_archive(tmp_path / "q.tar.xz", attribution, links=links), "9.9.9")


def test_links_inside_archive_are_followed(tmp_path, lists):
    """アーカイブの中を指すリンクの許諾文は、リンク先の中身と、リンク先のパスを書く"""
    attribution = [dict(e) for e in ATTRIBUTION]
    attribution[0]["LicenseFile"] = "LINK"
    archive = make_archive(
        tmp_path / "q.tar.xz", attribution, links={"src/3rdparty/a/LINK": "COPYING"}
    )
    files, _ = mq.build(archive, "9.9.9")
    assert "src/3rdparty/a/COPYING (from the Qt 9.9.9 source, qtbase)" in files["alpha"]


def test_write_replaces_qt_rows_and_removes_stale_files(tmp_path, lists):
    """licenses/qt/ に書き、作らなくなったファイルを消す。targets.tsv は、Qt の部品の説明の行と qt/ の行だけを替え、
    ほかの行は変えない。licenses/ のほかの場所には書かない"""
    licenses = tmp_path / "licenses"
    (licenses / "qt").mkdir(parents=True)
    (licenses / "qt" / "old.txt").write_text("前の部品", encoding="utf-8")
    (licenses / "targets.tsv").write_text(
        "# 説明\nGPL-3.0.txt\tPySide6_Essentials\n"
        + mq.TARGETS_HEAD.format(version="9.9.8")
        + "\nqt/old.txt\tpython/x.dll\n# ほかの説明\nMIT.txt\tsix\n",
        encoding="utf-8",
    )
    files, rows = mq.build(make_archive(tmp_path / "q.tar.xz"), "9.9.9")
    assert mq.write(files, rows, "9.9.9", licenses) == ["old.txt"]
    assert sorted(p.name for p in (licenses / "qt").iterdir()) == [
        "alpha.txt",
        "beta.txt",
        "gamma.txt",
    ]
    assert (licenses / "targets.tsv").read_text("utf-8") == (
        "# 説明\nGPL-3.0.txt\tPySide6_Essentials\n"
        + mq.TARGETS_HEAD.format(version="9.9.9")
        + "\n"
        + "\n".join(rows)
        + "\n# ほかの説明\nMIT.txt\tsix\n"
    )
    assert sorted(
        p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()
    ) == [
        "licenses/qt/alpha.txt",
        "licenses/qt/beta.txt",
        "licenses/qt/gamma.txt",
        "licenses/targets.tsv",
        "q.tar.xz",
    ]


def test_download_folder_must_be_outside_repository(capsys):
    """ダウンロードしたファイルを置くフォルダがリポジトリの中なら、ダウンロードせずに止める"""
    assert mq.main(["--version", "6.12.0", "--download-dir", str(ROOT / "dl")]) == 1
    assert "リポジトリの外に置く" in capsys.readouterr().err
    assert not (ROOT / "dl").exists()


def test_lists_match_repository_licenses():
    """スクリプトの一覧が、licenses/qt/ のファイルと targets.tsv の qt/ の行と合う。Qt のバージョンは uv.lock の
    PySide6-Essentials のバージョンで、その SHA-256 が記録されている。入れる部品と入れない部品は重ならない"""
    version = mq.lock_version()
    assert version in mq.QTBASE_SHA256
    assert not set(mq.INCLUDED) & set(mq.EXCLUDED)
    assert set(mq.CHOSEN) <= set(mq.INCLUDED)
    qt = mq.LICENSES / "qt"
    assert sorted(p.stem for p in qt.glob("*.txt")) == sorted(mq.INCLUDED)
    for cid in mq.INCLUDED:
        assert (
            (qt / f"{cid}.txt")
            .read_text("utf-8")
            .startswith(f"Third-party component of Qt {version} (qtbase)")
        )
    lines = (mq.LICENSES / "targets.tsv").read_text("utf-8").splitlines()
    start = lines.index(mq.TARGETS_HEAD.format(version=version))
    rows = [f"qt/{cid}.txt\t{dll}" for cid, dlls in mq.INCLUDED.items() for dll in dlls]
    assert lines[start + 1 : start + 1 + len(rows)] == rows
    assert [line for line in lines if line.startswith("qt/")] == rows
