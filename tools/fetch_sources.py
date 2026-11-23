# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""配布用パッケージに入る LGPL の部品（Qt と GEOS）のソースのアーカイブをダウンロードし、記録した SHA-256 と照合して、フォルダに置く

配布用パッケージを公開するときに、同じバージョンの Releases のページに、このアーカイブを添える。LGPL の部品を配布用パッケージに入れて配るので、その
部品のソースを、配布用パッケージと同じ場所から入手できるようにするため。

- Qt：PySide6・shiboken6 のソース（pyside-setup）と、配布用パッケージに残す Qt の DLL とプラグインのソース（qtbase）。バージョンは uv.lock の
  PySide6-Essentials のバージョン（PySide6 と Qt は同じバージョン）。
- GEOS：shapely の配布物に入っている GEOS。バージョンは、uv.lock の shapely のバージョンから GEOS_FOR_SHAPELY で決める。shapely の
  Windows 用の配布物の shapely.libs/geos_c-*.dll のバージョンの文字列（「3.14.1-CAPI-1.20.5」の形）で確かめてから足す。
- バージョンごとの SHA-256 を、ここに置く。Qt は配布サイトの .mirrorlist のページの SHA-256、GEOS は GitHub のリリースの
  digest と、ダウンロードしたファイルで計算した値が合うことを確かめてから足す。記録のないバージョンでは、ダウンロードして計算した
  SHA-256 を出して止める（人が確かめてから足す）。記録と違えば、ダウンロードしたファイルを消して止める。
- 置く場所は、既定では dist/sources-<ツールのバージョン>/（git の対象外）。同じ名前のファイルがあれば、ダウンロードし直さずに照合
  する。すべて照合できたら、SHA256SUMS（sha256sum の形）を書く。
- インターネットからダウンロードしたものなので、アーカイブは展開しない。

使い方（リポジトリの直下で。インターネットへの接続が要る）:
    uv run python tools/fetch_sources.py [--out <フォルダ>]
"""

import argparse
import hashlib
import sys
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QT_URLS = {
    "pyside-setup": "https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-{version}-src/"
    "pyside-setup-everywhere-src-{version}.tar.xz",
    "qtbase": "https://download.qt.io/official_releases/qt/{minor}/{version}/submodules/"
    "qtbase-everywhere-src-{version}.tar.xz",
}
GEOS_URL = "https://download.osgeo.org/geos/geos-{version}.tar.bz2"
# Qt のバージョンごとの、アーカイブの SHA-256（qtbase の値は tools/make_qt_licenses.py の QTBASE_SHA256 と同じ）
QT_SHA256 = {
    "6.12.0": {
        "pyside-setup": "099c1a597cf33c1b000e11cf68a0b82d216fb802bf81726efc720394f10c7751",
        "qtbase": "a951bd163c7b80fc6b8c88d7668fb56abf91c152373e13c10666763238131307",
    },
}
# shapely のバージョン → その Windows 用の配布物に入っている GEOS のバージョン
GEOS_FOR_SHAPELY = {"2.2.0": "3.14.1"}
GEOS_SHA256 = {
    "3.14.1": "3c20919cda9a505db07b5216baa980bacdaa0702da715b43f176fb07eff7e716",
}
SUMS = "SHA256SUMS"


class Stop(Exception):
    """ダウンロードをやめる理由"""


@dataclass(frozen=True)
class Archive:
    url: str
    sha256: str | None  # 記録がなければ None

    @property
    def name(self) -> str:
        return self.url.rsplit("/", 1)[1]


def locked_versions(lock: Path = ROOT / "uv.lock") -> dict[str, str]:
    """uv.lock の、配布物の名前 → バージョン"""
    return {
        p["name"]: p["version"]
        for p in tomllib.loads(lock.read_text("utf-8"))["package"]
    }


def tool_version(pyproject: Path = ROOT / "pyproject.toml") -> str:
    return tomllib.loads(pyproject.read_text("utf-8"))["project"]["version"]


def archives(qt: str, shapely: str) -> list[Archive]:
    """Qt のバージョンと shapely のバージョンから、ダウンロードするアーカイブ。shapely のバージョンに GEOS のバージョンの記録がなければ止める"""
    minor = ".".join(qt.split(".")[:2])
    qt_sums = QT_SHA256.get(qt, {})
    found = [
        Archive(url.format(version=qt, minor=minor), qt_sums.get(key))
        for key, url in QT_URLS.items()
    ]
    geos = GEOS_FOR_SHAPELY.get(shapely)
    if geos is None:
        raise Stop(
            f"shapely {shapely} に入っている GEOS のバージョンが記録にない。shapely の Windows 用の配布物の "
            "shapely.libs/geos_c-*.dll のバージョンの文字列で確かめてから、GEOS_FOR_SHAPELY に足す"
        )
    found.append(Archive(GEOS_URL.format(version=geos), GEOS_SHA256.get(geos)))
    return found


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, path: Path) -> None:
    tmp = path.with_name(path.name + ".part")
    print(f"ダウンロードしています：{url}", flush=True)
    with urllib.request.urlopen(url, timeout=60) as r, tmp.open("wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    tmp.replace(path)


def fetch(archive: Archive, folder: Path, get=download) -> Path:
    """アーカイブを folder に置き、記録の SHA-256 と照合する。記録がない・記録と違うときは止める（違うときは消す）"""
    path = folder / archive.name
    if not path.exists():
        get(archive.url, path)
    got = sha256(path)
    if archive.sha256 is None:
        raise Stop(
            f"{archive.name} の SHA-256 が記録にない。計算した SHA-256 は {got}。配布元が示す値と合うことを"
            "確かめてから、QT_SHA256 か GEOS_SHA256 に足す"
        )
    if got != archive.sha256:
        path.unlink()
        raise Stop(
            f"{archive.name} の SHA-256 が記録と違うので消した：{got}（記録は {archive.sha256}）"
        )
    return path


def main(argv: list[str] | None = None, get=download) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out",
        type=Path,
        help="置くフォルダ（既定は dist/sources-<ツールのバージョン>/）",
    )
    args = parser.parse_args(argv)
    folder = args.out or ROOT / "dist" / f"sources-{tool_version()}"
    try:
        versions = locked_versions()
        found = archives(versions["pyside6-essentials"], versions["shapely"])
        folder.mkdir(parents=True, exist_ok=True)
        paths = [fetch(a, folder, get) for a in found]
    except Stop as e:
        print(f"止めました：{e}", file=sys.stderr)
        return 1
    lines = [f"{a.sha256}  {p.name}\n" for a, p in zip(found, paths)]
    (folder / SUMS).write_text("".join(lines), "utf-8", newline="\n")
    for line in lines:
        print(line, end="")
    print(f"置いた場所：{folder}（Releases のページに、{SUMS} と一緒に添える）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
