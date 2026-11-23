# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""配布用パッケージに残す Qt の DLL に入っている、qtbase の第三者の部品の許諾文（licenses/qt/<Id>.txt）と、licenses/targets.tsv の
qt/ の行を、Qt のソース（qtbase-everywhere-src-<バージョン>.tar.xz）の qt_attribution.json から作る

- Qt のバージョンは、既定では uv.lock の PySide6-Essentials のバージョン（PySide6 に入る Qt とバージョンをそろえる）。
- ソースは download.qt.io からダウンロードし（--archive で手元のものも渡せる）、ここに記録したバージョンごとの SHA-256 と照合する。
  記録のないバージョンでは作らずに止め、計算した SHA-256 と、同じ場所の md5sums.txt の MD5 と比べた結果を出す（人が確かめて
  から QTBASE_SHA256 に足す）。
- インターネットからダウンロードしたものなので、アーカイブは展開せず、qt_attribution.json と許諾文のファイルだけを tarfile で
  読む。書くのは licenses/qt/<Id>.txt と licenses/targets.tsv だけで、アーカイブの中のパスで書く場所を決めない。
- 入れる部品（INCLUDED）と入れない部品（EXCLUDED）を、ここに一覧で置く。qt_attribution.json にあってどちらの一覧にも
  ない部品と、INCLUDED にあってソースにない部品があれば、作らずに止める（バージョンを上げたときに、人が見て振り分ける）。
- licenses/qt/ にあって、作らなくなったファイルは消す。

使い方（リポジトリの直下で。--archive を渡さなければ、インターネットへの接続が要る）:
    uv run python tools/make_qt_licenses.py [--version 6.12.0] [--archive <tar.xz>] [--download-dir <フォルダ>]
"""

import argparse
import hashlib
import json
import posixpath
import re
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
LICENSES = ROOT / "licenses"
BASE_URL = "https://download.qt.io/official_releases/qt/{minor}/{version}/submodules/"
ARCHIVE = "qtbase-everywhere-src-{version}.tar.xz"
# バージョンごとの qtbase のアーカイブの SHA-256（md5sums.txt の MD5 と合うことを確かめてから足す）
QTBASE_SHA256 = {
    "6.12.0": "a951bd163c7b80fc6b8c88d7668fb56abf91c152373e13c10666763238131307",
}

CORE = "python/Lib/site-packages/PySide6/Qt6Core.dll"
GUI = "python/Lib/site-packages/PySide6/Qt6Gui.dll"
NETWORK = "python/Lib/site-packages/PySide6/Qt6Network.dll"
QWINDOWS = "python/Lib/site-packages/PySide6/plugins/platforms/qwindows.dll"
# 入れる部品の Id → 入っている配布用パッケージの DLL（targets.tsv の対象。この順に書く）。qt_attribution.json の QtUsage と、
# DLL の中の文字列で確かめた。文字列で見えない部品は、QtUsage に Windows を除く条件がないものを入れた
INCLUDED = {
    # Qt6Core
    "blake2": [CORE],
    "doubleconversion": [CORE],
    "easing": [CORE],
    "md4": [CORE],
    "md5": [CORE],
    "pcre2": [CORE],
    "pcre2-sljit": [CORE],
    "rfc6234": [CORE],
    "sha1": [CORE],
    "sha3_endian": [CORE],
    "sha3_keccak": [CORE],
    "siphash": [CORE],
    "tinycbor": [CORE],
    "zlib": [CORE],
    "tlexpected": [CORE],
    "tika-mimetypes": [CORE],
    "unicode-character-database": [CORE],
    "unicode-cldr": [CORE],
    # Qt6Gui
    "freetype": [GUI],
    "freetype-zlib": [GUI],
    "freetype-bdf": [GUI],
    "freetype-pcf": [GUI],
    "grayraster": [GUI],
    "harfbuzz-ng": [GUI],
    "emoji-segmenter": [GUI],
    "icc-srgb-color-profile": [GUI],
    "libpng": [GUI],
    "md4c": [GUI],
    "d3d12memoryallocator": [GUI],
    "rhi-miniengine-d3d12-mipmap": [GUI],
    "vulkanmemoryallocator": [GUI],
    "vulkan-xml-spec": [GUI],
    "opengl-headers": [GUI, QWINDOWS],
    "opengl-es2-headers": [GUI, QWINDOWS],
    "smooth-scaling-algorithm": [GUI],
    "xserverhelper": [GUI],
    "aglfn": [GUI],
    "xsvg": [GUI],
    "webgradients": [GUI],
    # Qt6Network
    "psl-data": [NETWORK],
    "libpsl": [NETWORK],
    # qwindows
    "wintab": [QWINDOWS],
}

BUILD = "ビルドの仕組みだけで使う（DLL に入らない）"
TEST = "Qt のテストと Qt Test で使う（配布用パッケージに Qt Test は入れない）"
WAYLAND = "Wayland（Linux の画面）のプラグインと Compositor だけで使う"
# 入れない部品の Id → 理由
EXCLUDED = {
    "extra-cmake-modules": BUILD,
    "kwin": BUILD,
    "cmake-runcmake-test-modules": BUILD,
    "android-native-style": "Android だけで使う",
    "android-gradle-wrapper": "Android だけで使う",
    "forkfd": "Unix だけで使う",
    "libjpeg": "配布用パッケージに入れない qjpeg の画像のプラグインで使う",
    "pixman": "ARM NEON の環境だけで使う（配布用パッケージは x86-64）",
    "sqlite": "配布用パッケージに入れない Qt SQL の SQLite のプラグインで使う",
    "dejayvu": "WebAssembly だけで使う",
    "xcb-xinput": "XCB（Linux の画面）のプラグインだけで使う",
    "qeventdispatcher_cf": "macOS だけで使う",
    "cocoa-platform-plugin": "macOS だけで使う",
    "libdbus-1-headers": "配布用パッケージに入れない Qt D-Bus で使う",
    "catch2": TEST,
    "cycle": TEST,
    "linuxperf": TEST,
    "valgrind": TEST,
    "sbom-test-parent": TEST,
    "sbom-test-child": TEST,
    **{
        i: WAYLAND
        for i in (
            "appmenu",
            "wayland-color-management-protocol",
            "fractional-scale-v1",
            "wayland-fullscreen-protocol",
            "wayland-linux-dmabuf-v1",
            "wayland-linux-dmabuf-unstable-v1",
            "wayland-pointer-gestures-protocol",
            "wayland-pointer-warp-protocol",
            "presentation-time.xml",
            "wayland-scaler-protocol",
            "wayland-session-management-protocol",
            "wayland-tablet-protocol",
            "wayland-text-input-unstable-v1",
            "wayland-text-input-unstable-v2",
            "wayland-text-input-unstable-v3",
            "wayland-viewporter-protocol",
            "wayland-protocol",
            "wayland-eglstream-controller",
            "wlr-data-control-unstable-v1-protocol",
            "wayland-primary-selection-protocol",
            "wayland-xdg-activation",
            "wayland-xdg-decoration-protocol",
            "xdg-dialog-v1",
            "xdg-foreign-unstable-v2",
            "wayland-xdg-output-protocol",
            "wayland-xdg-shell-protocol",
            "wayland-xdg-system-bell-protocol",
            "wayland-xdg-toplevel-icon-protocol",
        )
    },
}
# 入れる部品のうち、OR で選べるライセンスの部品の Id → 使うライセンス（LicenseId がこの式のときだけ当てる）
CHOSEN = {
    "blake2": ("CC0-1.0 OR Apache-2.0", "CC0-1.0"),
    "freetype": ("FTL OR GPL-2.0-only", "FTL"),
    "grayraster": ("FTL OR GPL-2.0-only", "FTL"),
    "vulkan-xml-spec": ("Apache-2.0 OR MIT", "MIT"),
}
# targets.tsv の qt/ の行の前の説明の行（{version} にバージョンを入れる）
TARGETS_HEAD = "# 配布用パッケージに残す Qt の DLL（tools/make_dist.py の PYSIDE_KEEP）に入っている、Qt {version} の qtbase の第三者の部品（qt_attribution.json から作った）"
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class Stop(Exception):
    """作らずに止める理由"""


def lock_version(lock: Path = ROOT / "uv.lock") -> str:
    """uv.lock の PySide6-Essentials のバージョン"""
    packages = tomllib.loads(lock.read_text("utf-8"))["package"]
    return next(p["version"] for p in packages if p["name"] == "pyside6-essentials")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def archive_url(version: str, name: str) -> str:
    minor = ".".join(version.split(".")[:2])
    return BASE_URL.format(minor=minor, version=version) + name


def check_archive(path: Path, version: str) -> None:
    """アーカイブの SHA-256 を記録と照合する。記録がなければ、md5sums.txt の MD5 と比べた結果を添えて止める"""
    got = sha256(path)
    want = QTBASE_SHA256.get(version)
    if want is None:
        md5 = hashlib.md5(path.read_bytes()).hexdigest()
        try:
            with urllib.request.urlopen(
                archive_url(version, "md5sums.txt"), timeout=60
            ) as r:
                listed = r.read().decode("utf-8", "replace")
            name = ARCHIVE.format(version=version)
            line = next(
                (ln for ln in listed.splitlines() if ln.split()[-1:] == [name]), ""
            )
            md5_text = (
                "md5sums.txt の MD5 と一致"
                if line.split()[:1] == [md5]
                else f"md5sums.txt の MD5 と違う（{line or '行がない'}）"
            )
        except OSError as e:
            md5_text = f"md5sums.txt をダウンロードできない（{type(e).__name__}: {e}）"
        raise Stop(
            f"Qt {version} の SHA-256 が記録にない。計算した SHA-256 {got}、MD5 {md5}（{md5_text}）。"
            "確かめてから QTBASE_SHA256 に足す"
        )
    if got != want:
        raise Stop(f"{path} の SHA-256 が記録と違う：{got}（記録は {want}）")


def download(version: str, folder: Path) -> Path:
    """アーカイブを folder（リポジトリの外）にダウンロードする。同じ名前のファイルがあれば、ダウンロードし直さない"""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ARCHIVE.format(version=version)
    if not path.exists():
        url = archive_url(version, path.name)
        print(f"ダウンロードしています：{url}", flush=True)
        tmp = path.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=60) as r, tmp.open("wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        tmp.replace(path)
    return path


def inside(root: str, folder: str, name: str) -> str:
    """アーカイブの中のフォルダ folder から見た name の、アーカイブの中のパス。root の外を指せば Stop"""
    path = posixpath.normpath(posixpath.join(folder, name))
    if PurePosixPath(name).is_absolute() or not (path + "/").startswith(root + "/"):
        raise Stop(f"アーカイブの外を指すパス：{name}（{folder}）")
    return path


def regular_files(tar: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    """アーカイブの中の、ふつうのファイルとリンク（名前 → 項目）"""
    return {m.name: m for m in tar.getmembers() if m.isfile() or m.issym() or m.islnk()}


def read_text(
    tar: tarfile.TarFile, members: dict, root: str, path: str, depth: int = 0
) -> tuple[str, str] | None:
    """アーカイブの中のファイルを文字列で読み、(リンクをたどった後のパス, 中身) を返す。ないファイルは None。
    リンクはアーカイブの中だけたどる"""
    m = members.get(path)
    if m is None:
        return None
    if m.issym() or m.islnk():
        if depth > 8:
            raise Stop(f"リンクが深すぎる：{path}")
        target = (
            m.linkname
            if m.islnk()
            else inside(root, posixpath.dirname(path), m.linkname)
        )
        return read_text(tar, members, root, target, depth + 1)
    f = tar.extractfile(m)
    return path, f.read().decode("utf-8", errors="replace")


def spdx_ids(expr: str) -> list[str]:
    return [
        t
        for t in expr.replace("(", " ").replace(")", " ").split()
        if t not in ("AND", "OR", "WITH")
    ]


def copyright_lines(value) -> list[str]:
    """qt_attribution.json の Copyright（文字列か、文字列の並び）を行にする"""
    if not value:
        return ["(see the license text)"]
    if isinstance(value, list):
        value = "\n".join(value)
    return value.strip().splitlines()


def build(archive: Path, version: str) -> tuple[dict[str, str], list[str]]:
    """アーカイブから、作るファイル（Id → 中身）と targets.tsv の qt/ の行を作る"""
    with tarfile.open(archive, "r:xz") as tar:
        members = regular_files(tar)
        roots = {n.split("/", 1)[0] for n in members}
        if len(roots) != 1:
            raise Stop(f"アーカイブの先頭のフォルダが1つでない：{sorted(roots)}")
        (root,) = roots
        found: dict[str, tuple[str, dict]] = {}
        for name in sorted(
            (n for n in members if n.endswith("/qt_attribution.json")),
            key=PurePosixPath,
        ):
            _, text = read_text(tar, members, root, name)
            data = json.loads(text, strict=False)
            for e in data if isinstance(data, list) else [data]:
                found[str(e.get("Id"))] = (posixpath.dirname(name), e)
        unknown = sorted(set(found) - set(INCLUDED) - set(EXCLUDED))
        missing = sorted(set(INCLUDED) - set(found))
        if unknown or missing:
            raise Stop(
                "部品の一覧がソースと合わない。"
                + (
                    f"どちらの一覧にもない部品（INCLUDED か EXCLUDED に振り分ける）：{', '.join(unknown)}。"
                    if unknown
                    else ""
                )
                + (
                    f"INCLUDED にあってソースにない部品：{', '.join(missing)}。"
                    if missing
                    else ""
                )
            )
        files, rows = {}, []
        for cid, dlls in INCLUDED.items():
            if not SAFE_ID.fullmatch(cid):
                raise Stop(f"ファイルの名前にできない Id：{cid}")
            folder, e = found[cid]
            lic = e.get("LicenseId", "")
            used_lic = lic
            if cid in CHOSEN:
                expr, choice = CHOSEN[cid]
                if lic != expr:
                    raise Stop(
                        f"{cid} のライセンスが、選び方を決めた式と違う：{lic}（決めた式は {expr}）"
                    )
                used_lic = choice
            elif " OR " in f" {lic} ":
                raise Stop(
                    f"{cid} のライセンスは OR で選べる（{lic}）。CHOSEN に、使うライセンスを足す"
                )
            names = e.get("LicenseFiles") or e.get("LicenseFile") or []
            names = [names] if isinstance(names, str) else names
            texts = []
            for name in names:
                got = read_text(tar, members, root, inside(root, folder, name))
                if got is None:
                    print(f"許諾文のファイルがない：{cid} {name}", file=sys.stderr)
                    continue
                texts.append((got[0][len(root) + 1 :], got[1]))
            # 部品の許諾文がなければ、使うライセンスの全文（LICENSES/）を入れる。FreeType の LICENSE.txt は FTL の
            # 全文を指すだけなので、FTL の全文も入れる
            if not texts or "FTL" in spdx_ids(used_lic):
                for sid in spdx_ids(used_lic):
                    got = read_text(tar, members, root, f"{root}/LICENSES/{sid}.txt")
                    if got is None:
                        raise Stop(f"ライセンスの全文がない：{cid} LICENSES/{sid}.txt")
                    texts.append((f"LICENSES/{sid}.txt", got[1]))
            used = ", ".join(d.rsplit("/", 1)[-1] for d in dlls)
            head = [
                f"Third-party component of Qt {version} (qtbase), included in this package",
                f"Name: {e.get('Name')}",
                f"Id: {cid}",
                f"Version: {e.get('Version') or '(not given)'}",
                f"Homepage: {e.get('Homepage') or e.get('DownloadLocation') or '(not given)'}",
                f"License: {lic}"
                + (f" (used under {used_lic})" if cid in CHOSEN else ""),
                f"In this package: {used}",
                f"Qt usage: {' '.join((e.get('QtUsage') or '').split())}",
                "",
                "Copyright:",
                *copyright_lines(e.get("Copyright")),
            ]
            body = []
            for name, text in texts:
                body += [
                    "",
                    "=" * 78,
                    f"{name} (from the Qt {version} source, qtbase)",
                    "=" * 78,
                    text.rstrip(),
                ]
            files[cid] = "\n".join(head + body) + "\n"
            rows += [f"qt/{cid}.txt\t{d}" for d in dlls]
    return files, rows


def replace_rows(text: str, version: str, rows: list[str]) -> str:
    """targets.tsv の中身の、qt/ の行の前の説明の行と qt/ の行（続いた1つのまとまり）を、新しいものに替える"""
    lines = text.split("\n")
    heads = [
        i
        for i, line in enumerate(lines)
        if line.startswith(TARGETS_HEAD.split("Qt {version}")[0])
    ]
    if len(heads) != 1:
        raise Stop("targets.tsv に、Qt の部品の行の前の説明の行が1つでない")
    start = heads[0]
    end = start + 1
    while end < len(lines) and lines[end].startswith("qt/"):
        end += 1
    if any(line.startswith("qt/") for line in lines[end:] + lines[:start]):
        raise Stop(
            "targets.tsv の qt/ の行が、説明の行に続く1つのまとまりになっていない"
        )
    return "\n".join(
        lines[:start] + [TARGETS_HEAD.format(version=version), *rows] + lines[end:]
    )


def write(
    files: dict[str, str], rows: list[str], version: str, licenses: Path | None = None
) -> list[str]:
    """licenses/qt/ に書き、targets.tsv の qt/ の行を替える（licenses を省くとリポジトリの licenses/）。作らなくなった
    ファイルは消し、その名前を返す"""
    licenses = licenses or LICENSES
    out = licenses / "qt"
    out.mkdir(parents=True, exist_ok=True)
    stale = sorted(p.name for p in out.glob("*.txt") if p.stem not in files)
    for name in stale:
        (out / name).unlink()
    for cid, text in files.items():
        (out / f"{cid}.txt").write_text(text, encoding="utf-8", newline="\n")
    targets = licenses / "targets.tsv"
    targets.write_text(
        replace_rows(targets.read_text("utf-8"), version, rows),
        encoding="utf-8",
        newline="\n",
    )
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--version",
        help="Qt のバージョン（既定は uv.lock の PySide6-Essentials のバージョン）",
    )
    parser.add_argument(
        "--archive", type=Path, help="手元の qtbase-everywhere-src-<バージョン>.tar.xz"
    )
    parser.add_argument(
        "--download-dir",
        type=Path,
        help="ダウンロードしたアーカイブを置くフォルダ（リポジトリの外。既定は一時的なフォルダ）",
    )
    args = parser.parse_args(argv)
    version = args.version or lock_version()
    try:
        if args.archive is not None:
            archive = args.archive
        else:
            folder = args.download_dir or Path(tempfile.mkdtemp(prefix="qtbase-"))
            if folder.resolve().is_relative_to(ROOT):
                raise Stop(
                    f"ダウンロードしたファイルは、リポジトリの外に置く：{folder}"
                )
            archive = download(version, folder)
        check_archive(archive, version)
        files, rows = build(archive, version)
        stale = write(files, rows, version)
    except Stop as e:
        print(f"作りませんでした：{e}", file=sys.stderr)
        return 1
    print(
        f"Qt {version}：licenses/qt/ に {len(files)}個を書き、targets.tsv の qt/ の行を {len(rows)}行にした"
    )
    for name in stale:
        print(f"作らなくなったファイルを消した：licenses/qt/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
