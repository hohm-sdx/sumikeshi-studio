# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""配布用パッケージ（Windows 64ビット用、インターネットなしで導入できるもの）を ZIP で作る

Linux でも Windows でも作れる。作るときはインターネットに接続する（Python とライブラリを取得する）。
1. Windows 用の Python（python.org の embeddable package。Python Software Foundation の署名付き）を取得し、
   ここに書いた SHA-256 と照合して展開する。python314._pth に Lib\\site-packages を足す
2. uv.lock でバージョンを固定したライブラリの Windows 用の配布物を、SHA-256 を照合して python\\Lib\\site-packages に入れる。
   pypdfium2 は、wheels/ の配布物（pypdfium2 5.14.0 のソースと pdfium-binaries 8138 の PDFium で作ったもの）が入る。
   入れた pdfium.dll の SHA-256 も照合する
3. ツール本体を同じ場所に入れ、使わない部分（ライブラリのツール・テスト、作った環境のパスを書いたスクリプトと記録）を除く。
   導入で入れる3様式と、ツールのアイコンのファイルが、リポジトリと同じ中身で入ったかを確かめる。
   ライブラリが読み込む Visual C++ のランタイムのうち、同梱されていないものを python フォルダにコピーする
4. すべての .exe・.dll・.pyd の署名を osslsigncode で検証し、決まり（SIGNERS・UNSIGNED）から外れたら止める。
   署名した証明書の失効は、署名の中の証明書の CRL の配布先から make_dist が CRL を取って確かめる
5. windows/ の導入用のファイルを、フォルダの配置のまま加え（入口の .bat は一番上、PowerShell のスクリプトは scripts\\）、
   version.txt（バージョン・作った日時・元のコミット・公開のソースの場所・Python と PDFium のバージョン。--public の
   ときは元のコミットを書かない）を書き、
   README-ja.txt・README-en.txt の最長のパスの数を書き込み、
   dist/sumikeshi-studio-<バージョン>-<日付>-win64.zip にまとめる

使い方（リポジトリの直下で。osslsigncode が PATH にないときは、環境変数 OSSLSIGNCODE で場所を渡す）:
    uv run python tools/make_dist.py [--public]
--public：公開用のパッケージにする。version.txt に元のコミット（Commit の行）を書かず、公開のソースの場所（Source の行）
だけを書く。コミットしていない変更があるとき・元のコミットが分からないときは作らない。元のコミットは、最後の出力に出す。
"""

import argparse
import base64
import csv
import email
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path, PurePosixPath

from sumikeshi_studio import __version__
from sumikeshi_studio.version import MADE_PREFIX, VERSION_FILE

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "dist"
BUNDLED_FORMS = (
    ROOT / "src" / "sumikeshi_studio" / "bundled_forms"
)  # 導入で様式のフォルダに入れる3様式
# ツールのアイコン（ショートカットとアプリの一覧が指すアイコンのファイルと、画面に付ける PNG。tools/make_icons.py が作る）
ICONS = ROOT / "src" / "sumikeshi_studio" / "icons"
PYTHON_VERSION = "3.14.10"
PYTHON_FILE = f"python-{PYTHON_VERSION}-embed-amd64.zip"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{PYTHON_FILE}"
# python.org の配布の一覧（https://www.python.org/api/v2/downloads/release_file/?release=1171）にある SHA-256。
# バージョンを上げるときは、配布の一覧の値に書き直す
PYTHON_SHA256 = "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159"
# Python が読み込む場所を決めるファイル。ここに書いた場所だけを読み込み、PYTHONPATH などの環境変数とレジストリを
# 使わない。embeddable package のものに、ライブラリの場所の1行を足す
PYTHON_PTH = "python314._pth"
SITE_LINE = "Lib\\site-packages"
# 画面のライブラリ（PySide6）のうち、配布用パッケージに残すもの（PySide6 のフォルダからの位置。ほかは除く。Visual C++ の
# ランタイムは VC_RUNTIME_FILES で別に残す）。ツールが使うモジュール（QT_MODULES）と、それらが動くのに要る DLL と
# プラグインだけにする。使わないモジュールの DLL には、ほかの第三者の部品と、GPL でしか使えないもの（Qt Quick
# Timeline など）があり、許諾文を調べて入れる手間と配布用パッケージの大きさが増えるため。DLL は、4つの .pyd から、インポート表と
# 遅延読み込みの表をたどって集めた（残した DLL が除いた DLL を読み込むなら、prune_pyside が止める）。
# プラグインは、Qt が実行中に名前で探して読み込むので、インポート表に出ない。ウィンドウを出す qwindows と、既定の
# 見た目の qmodernwindowsstyle だけを残す（ツールは、画像の読み書き・SVG・TLS・翻訳・OpenGL を Qt で行わない。
# アイコンは書体の記号から描き、クリップボードの画像は qwindows が PNG・DIB を QtGui の組み込みの処理で読む）
# QtNetwork は、一度に1つだけ起動する仕組み（instance.py の QLocalServer・QLocalSocket）で使う
QT_MODULES = ("QtCore", "QtGui", "QtWidgets", "QtNetwork")
PYSIDE_KEEP = [
    "__init__.py",
    "_config.py",
    "_git_pyside_version.py",
    "py.typed",
    "PySide6_Essentials.json",
    "support/*.py",
    "pyside6.abi3.dll",
    *(f"{m}.pyd" for m in QT_MODULES),
    *(f"{m}.pyi" for m in QT_MODULES),
    *(f"Qt6{m[2:]}.dll" for m in QT_MODULES),
    "plugins/platforms/qwindows.dll",
    "plugins/styles/qmodernwindowsstyle.dll",
]
# shiboken6 のうち、C++ で拡張を作るためのもの（ヘッダー・CMake の設定・リンク用の .lib）。ツールは使わない
SHIBOKEN_UNUSED = ["include", "lib", "*.lib"]
# ZIP の中の先頭のフォルダの名前。エクスプローラーの「すべて展開」は ZIP と同じ名前のフォルダの中に
# 展開するため、ここを短くしてパスを短くする。バージョンと日付は ZIP の名前と version.txt に残す
FOLDER = "sumikeshi-studio"
NAME = "sumikeshi-studio"  # ZIP の名前の始まり
PRODUCT = (
    "Sumikeshi Studio"  # version.txt の1行目に書くツールの名前（言語によらない名前）
)
# ライブラリのうち、ツールの動作中に読み込まれない部分（モデルを変換するツールと、ライブラリ自身のテスト）。
# パスが長いため除く。見本のスキャンの解析・出力と確認画面で読み込まれる810個のモジュールに、
# これらの中のものはなかった
# RapidOCR の推論の部分のうち、onnxruntime のほかのもの（PyTorch・Paddle など）も、ツールは使わない
# （その種類を選んだときだけ読み込む作り）。PyTorch の部分は、setup.bat で .pyc に変換するときに
# SyntaxWarning（"\d" is an invalid escape sequence）を出す（配布物の .py の中で唯一）
RAPIDOCR_ENGINES = "rapidocr/inference_engine"
SITE_UNUSED_DIRS = [
    "onnxruntime/tools",
    *(
        f"{RAPIDOCR_ENGINES}/{name}"
        for name in ("pytorch", "paddle", "openvino", "tensorrt", "mnn")
    ),
]
SITE_UNUSED_NAMES = {"tests", "test"}
# ライブラリのうち、ツールが使わないファイル。OpenCV の動画の部品は FFmpeg（LGPL-2.1）を含み、ツールは動画の機能を
# 使わない。PyYAML の C の部品（_yaml）は、なければ PyYAML が同じ配布物の Python の実装を使う（RapidOCR の設定を読む
# omegaconf は CSafeLoader の代わりに SafeLoader を使う）。署名がなく、スマート アプリ コントロールに止められると導入が
# 止まるため除く
SITE_UNUSED_FILES = ["cv2/opencv_videoio_ffmpeg*.dll", "yaml/_yaml.*.pyd"]
# Python だけの配布物（py3-none-any）を入れるライブラリ。uv export の一覧の SHA-256 を、uv.lock にあるその配布物のもの
# だけにして入れる（--require-hashes で、その配布物だけが入る）。Windows 用の配布物の .pyd は署名がなく、スマート アプリ
# コントロールに止められると導入が止まるため。Windows 用の配布物の .py は、改行のほかは同じ
PURE_PACKAGES = ("charset-normalizer",)
# site-packages の直下の、ツールが使わないもの。bin は uv pip install --target が作るコマンドのスクリプトで、1行目に作った
# 環境のパスが書かれ、Windows では動かない。.lock は uv の作業用のロックファイル
SITE_UNUSED_TOP = ["bin", ".lock", "README.txt"]
# 作ったときのパス（file:///tmp/…）を書いた記録。ツールは読まない
BUILD_RECORDS = "direct_url.json"
# 配布用パッケージに入れてはいけないライブラリ（GNU AGPL の PyMuPDF。ツールは pypdfium2 を使う）。入っていたら、作るのを止める
FORBIDDEN = re.compile(
    r"^(pymupdf|fitz|pymupdf_layout|pymupdf4llm)([-_.].*)?$", re.IGNORECASE
)
# Visual C++ のランタイム（Windows に含まれず、端末に入っているとは限らない DLL）
VC_RUNTIME = re.compile(
    r"^(msvcp140(_\d+)?|vcruntime140(_\d+)?|concrt140|vcomp140)\.dll$", re.IGNORECASE
)
# 配布用パッケージの中の Visual C++ のランタイムの名前。読み込む部品のない vccorlib140・vcamp140・msvcp140_codecvt_ids と、
# numpy・shapely が名前に「-」と16進の文字を付けて同梱するものも含める。バージョンを上げたときに見慣れない名前のランタイムが
# 入っても、Microsoft の条件の文の対象から漏れないよう、始まりだけで決める
VC_RUNTIME_FILES = re.compile(
    r"^(msvcp140|vcruntime140|concrt140|vcomp140|vccorlib140|vcamp140).*\.dll$",
    re.IGNORECASE,
)
# 配布用パッケージの直下の licenses フォルダ。make_dist が、リポジトリの licenses/ の全文（依存先が自分では持っていないもの）、
# 依存先の配布物にあるライセンスのファイル（配布物の名前のフォルダにコピーする）、一覧（LICENSE_INDEX）を入れる。配布物に
# ある全文をリポジトリにコピーしないのは、バージョンを上げるたびに古くなるため。リポジトリの licenses/ の対応表
# （LICENSE_TARGETS）に、全文ごとの対象を書く
LICENSE_DIR = "licenses"
LICENSE_TARGETS = "targets.tsv"
LICENSE_INDEX = "index.txt"
# 原文のまま置き、SHA-256 を照合する全文（gnu.org の gpl-3.0.txt・lgpl-3.0.txt、apache.org の LICENSE-2.0.txt）
LICENSE_TEXTS = {
    "GPL-3.0.txt": "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986",
    "LGPL-3.0.txt": "e3a994d82e644b03a792a930f574002658412f62407f5fee083f2555c5f23118",
    "Apache-2.0.txt": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
}
# 配布物の中のライセンスのファイル：RECORD にあるファイルのうち、dist-info の licenses フォルダ（PEP 639）の中の
# ものと、名前がこれに当たるもの（パッケージのフォルダに置く onnxruntime・cv2 と、dist-info の直下に置く配布物がある）
LICENSE_FILE_NAME = re.compile(
    r"^(licen[cs]e|copying|notice|thirdpartynotices|third[-_]party|authors)",
    re.IGNORECASE,
)
# ツール自身のライセンス（AGPL-3.0-only）の全文と、AGPL-3.0 の第7条の追加の許可。リポジトリの直下に置き、
# pyproject.toml の license-files で配布物の dist-info に入る。全文は gnu.org の agpl-3.0.txt の原文のまま置き、
# SHA-256 を照合する
PROJECT = "sumikeshi-studio"
PROJECT_LICENSE = "LICENSE"
PROJECT_LICENSE_SHA256 = (
    "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0"
)
PROJECT_LICENSE_EXCEPTION = "LICENSE-EXCEPTION.txt"

# 配布用パッケージに入れる pdfium.dll の SHA-256（pdfium-binaries の chromium/8138 の pdfium-win-x64.tgz の bin/pdfium.dll）。
# pypdfium2 は wheels/ の配布物を uv.lock の SHA-256 で入れるが、PyPI の同じバージョンの配布物（中の PDFium は 156.0.8076）と
# 取り違えないよう、入れた後に照合する。PDFium のバージョンを上げるときは書き直す
PDFIUM_DLL_SHA256 = "71a3e63e6ab8f5746a0fc9de9bc3d0c0dc07eb60990dfd37d9db7fc891821943"

# ---- 署名の決まり
# 署名の確認で使う CRL。CDP_LINE は、osslsigncode verify -ignore-cdp
# の出力の、取らなかった CRL の配布先の行。CRL_ROUNDS は、配布先を集めるために部品を流す回数の上限
# （配布用パッケージの部品では3回で集まった）。CRL_TIMEOUT は、CRL を取るときに待つ秒数
CDP_LINE = re.compile(
    r"^Ignored (?:TSA's )?CRL distribution point: (\S+)\s*$", re.MULTILINE
)
CRL_ROUNDS = 5
CRL_TIMEOUT = 60
PEM_CRL_BEGIN = b"-----BEGIN X509 CRL-----"
PEM_CRL_END = b"-----END X509 CRL-----"
# 配布用パッケージの中の .exe・.dll・.pyd は、ここに当たれば、その発行先の署名で検証が通ること（上から順に当てる）。どれにも
# 当たらないものは、署名がなく、UNSIGNED に書いたものであること。外れたら配布用パッケージを作るのを止める
PE_SUFFIXES = (".exe", ".dll", ".pyd")
SITE = f"{FOLDER}/python/Lib/site-packages"
PSF = "Python Software Foundation"
MICROSOFT = "Microsoft Corporation"
QT = "The QT Company Oy"
SIGNERS = [
    (f"{FOLDER}/python/vcruntime140*.dll", MICROSOFT),
    # PySide6 からコピーしたもの（provide_vc_runtime）
    (f"{FOLDER}/python/msvcp140*.dll", QT),
    (f"{FOLDER}/python/*", PSF),
    (f"{SITE}/PySide6/**", QT),
    (f"{SITE}/shiboken6/**", QT),
    (f"{SITE}/onnxruntime/**", MICROSOFT),
    (f"{SITE}/*.libs/msvcp140-*.dll", MICROSOFT),
]
# 署名のない部品（site-packages からの相対パス。ライブラリのバージョンを上げて変わったら、確かめて書き直す）。
# ツールが読み込むのは14個で、ほかは読み込まない
UNSIGNED = {
    "numpy 2.5.4": [
        "numpy/_core/_multiarray_tests.cp314-win_amd64.pyd",
        "numpy/_core/_multiarray_umath.cp314-win_amd64.pyd",
        "numpy/_core/_operand_flag_tests.cp314-win_amd64.pyd",
        "numpy/_core/_rational_tests.cp314-win_amd64.pyd",
        "numpy/_core/_simd.cp314-win_amd64.pyd",
        "numpy/_core/_struct_ufunc_tests.cp314-win_amd64.pyd",
        "numpy/_core/_umath_tests.cp314-win_amd64.pyd",
        "numpy/fft/_pocketfft_umath.cp314-win_amd64.pyd",
        "numpy/linalg/_umath_linalg.cp314-win_amd64.pyd",
        "numpy/linalg/lapack_lite.cp314-win_amd64.pyd",
        "numpy/random/_bounded_integers.cp314-win_amd64.pyd",
        "numpy/random/_common.cp314-win_amd64.pyd",
        "numpy/random/_generator.cp314-win_amd64.pyd",
        "numpy/random/_mt19937.cp314-win_amd64.pyd",
        "numpy/random/_pcg64.cp314-win_amd64.pyd",
        "numpy/random/_philox.cp314-win_amd64.pyd",
        "numpy/random/_sfc64.cp314-win_amd64.pyd",
        "numpy/random/bit_generator.cp314-win_amd64.pyd",
        "numpy/random/mtrand.cp314-win_amd64.pyd",
        "numpy.libs/libscipy_openblas64_-ed4f167a5330424524f45258e7ca2c8d.dll",
    ],
    "pillow 13.0.0": [
        "PIL/_avif.cp314-win_amd64.pyd",
        "PIL/_imaging.cp314-win_amd64.pyd",
        "PIL/_imagingcms.cp314-win_amd64.pyd",
        "PIL/_imagingft.cp314-win_amd64.pyd",
        "PIL/_imagingmath.cp314-win_amd64.pyd",
        "PIL/_imagingmorph.cp314-win_amd64.pyd",
        "PIL/_imagingtk.cp314-win_amd64.pyd",
        "PIL/_webp.cp314-win_amd64.pyd",
    ],
    "opencv-python-headless 5.0.0.93": ["cv2/cv2.pyd"],
    "shapely 2.2.0": [
        "shapely/_geometry_helpers.cp314-win_amd64.pyd",
        "shapely/_geos.cp314-win_amd64.pyd",
        "shapely/lib.cp314-win_amd64.pyd",
        "shapely.libs/geos-bf067cd6ff74ee0ad5f3ff52c7ef08c9.dll",
        "shapely.libs/geos_c-6dd9fd915eef8a7928285416bef1e666.dll",
    ],
    "pyclipper 1.4.0": ["pyclipper/_pyclipper.cp314-win_amd64.pyd"],
    "psutil 7.2.2": ["psutil/_psutil_windows.pyd"],
    "pypdfium2 5.14.0（PDFium 159.0.8138）": ["pypdfium2_raw/pdfium.dll"],
    "protobuf 7.36.2": ["google/_upb/_message.pyd"],
}
UNSIGNED_FILES = {f for files in UNSIGNED.values() for f in files}


def pure_hashes(lock_text: str, names=PURE_PACKAGES) -> dict[str, str]:
    """uv.lock の中の、names のライブラリの Python だけの配布物（-py3-none-any.whl）の SHA-256（"sha256:…"）。
    1つでなければ止める"""
    packages = tomllib.loads(lock_text)["package"]
    found = {}
    for name in names:
        wheels = [
            w
            for p in packages
            if p["name"] == name
            for w in p.get("wheels", [])
            if w["url"].endswith("-py3-none-any.whl")
        ]
        if len(wheels) != 1:
            sys.exit(
                f"uv.lock に、{name} の Python だけの配布物（py3-none-any）が1つではありません（{len(wheels)}個）"
            )
        found[name] = wheels[0]["hash"]
    return found


def keep_pure_hashes(requirements: str, hashes: dict[str, str]) -> str:
    """uv export の一覧（requirements.txt の形）で、hashes の名前の項目の --hash の行を、その SHA-256 の1行だけにする。
    項目がなければ止める"""
    out: list[str] = []
    current = None
    done = set()
    for line in requirements.splitlines():
        if line and not line[0].isspace() and not line.startswith("#"):
            name = re.split(r"[\s=<>!~;\[\\]", line, maxsplit=1)[0].lower()
            current = name if name in hashes else None
            if current is not None:
                out += [line, f"    --hash={hashes[current]}"]
                done.add(current)
                continue
        if current is not None and line.lstrip().startswith("--hash="):
            continue
        out.append(line)
    if missing := sorted(set(hashes) - done):
        sys.exit(f"uv export の一覧に、{'、'.join(missing)} がありません")
    return "\n".join(out) + "\n"


def uv(*args: str) -> None:
    subprocess.run([shutil.which("uv") or "uv", *args], check=True, cwd=ROOT)


def download(url: str, dest: Path) -> Path:
    # 標準の名乗り（Python-urllib）では、配布元に 403 で断られた
    req = urllib.request.Request(
        url, headers={"User-Agent": "sumikeshi-studio-make-dist"}
    )
    with urllib.request.urlopen(req) as r, dest.open("wb") as f:
        shutil.copyfileobj(r, f)
    return dest


def fetch_python(work: Path) -> Path:
    """Windows 用の Python（embeddable package）を取得して照合し、展開したフォルダを返す。python314._pth に
    ライブラリの場所を足し、その場所を作る"""
    archive = download(PYTHON_URL, work / PYTHON_FILE)
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != PYTHON_SHA256:
        sys.exit(
            f"Python の SHA-256 が、python.org の配布の一覧の値と合いません: {actual}"
        )
    python = work / "python"
    with zipfile.ZipFile(archive) as z:
        z.extractall(python)
    add_site_line(python / PYTHON_PTH)
    (python / "Lib" / "site-packages").mkdir(parents=True)
    return python


def add_site_line(pth: Path) -> None:
    """python314._pth に、ライブラリの場所（Lib\\site-packages）の行を足す。import site の行は足さない（site の処理と
    ユーザーの site-packages を使わない）"""
    lines = pth.read_text("utf-8").splitlines()
    if SITE_LINE not in lines:
        at = next((i for i, line in enumerate(lines) if not line.strip()), len(lines))
        lines.insert(at, SITE_LINE)
    pth.write_text("\r\n".join(lines) + "\r\n", "utf-8")


def remove_build_leftovers(site: Path) -> list[str]:
    """ツールが使わない、作った環境のパスを書いたスクリプトと記録を除く。除いたものの site-packages からのパスを返す"""
    removed = []
    for name in SITE_UNUSED_TOP:
        p = site / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
        else:
            continue
        removed.append(name)
    for p in sorted(site.glob(f"*.dist-info/{BUILD_RECORDS}")):
        p.unlink()
        removed.append(p.relative_to(site).as_posix())
    return removed


def parse_verify(text: str) -> tuple[str | None, bool]:
    """osslsigncode verify の出力から、署名者の組織（証明書の O）と、検証が通ったかを読む。署名がなければ (None, False)。
    組織を読めなければ ""（2.9-2 は、署名の後に書き換えたファイルでは署名者の節を出さずに終わる）"""
    if "No signature found" in text:
        return None, False
    # 2.9-2 は「Signer's certificate:」と「Signer #0:」のあいだに「------------------」の行を出す（2.5-4 は出さない）。
    # ほかの行を飛び越えないよう、区切りの行だけを許す（タイムスタンプの署名者の Subject を拾わないため）
    signer = re.search(
        r"Signer's certificate:\s*(?:-+\s*)?Signer #0:\s*Subject: (.*)", text
    )
    org = re.search(r"/O=([^/\n]+)", signer[1]) if signer else None
    ok = "Signature verification: ok" in text and text.rstrip().endswith("Succeeded")
    return (org[1].strip() if org else ""), ok


def verify_output(path: Path, tool: str, roots: str, crls: Path | None) -> str:
    """ファイルの署名を、Microsoft の Trusted Root Program のルート証明書（roots）で検証した osslsigncode の出力
    （タイムスタンプも）。CRL は配布先から取らず（-ignore-cdp）、crls（PEM の束）があれば渡す。
    osslsigncode 2.9 は CRL の応答の Content-Type を application/pkix-crl に決め打ちしていて（osslsigncode.c
    の x509_crl_get）、application/octet-stream で返す Microsoft の CRL を取れないため、CRL は make_dist が取る"""
    args = [tool, "verify", "-in", str(path), "-CAfile", roots, "-TSA-CAfile", roots]
    args.append("-ignore-cdp")
    if crls is not None:
        args += ["-CRLfile", str(crls), "-TSA-CRLfile", str(crls)]
    r = subprocess.run(
        args,
        check=False,  # 署名がないと 1 で終わる。結果は出力から読む
        capture_output=True,
        text=True,
        errors="replace",
    )
    return r.stdout + r.stderr


def crl_urls(text: str) -> list[str]:
    """osslsigncode verify -ignore-cdp の出力から、取らなかった CRL の配布先の URL（署名とタイムスタンプ）を読む"""
    return CDP_LINE.findall(text)


def crl_pem(url: str, data: bytes) -> bytes:
    """取った CRL を PEM にする。DER（先頭が SEQUENCE で、DER の長さが大きさと合う）か PEM の CRL でなければ止める。
    CRL の署名と期限は osslsigncode が確かめる"""
    if data.lstrip().startswith(PEM_CRL_BEGIN) and PEM_CRL_END in data:
        return data.strip() + b"\n"
    if len(data) > 2 and data[0] == 0x30:
        if data[1] < 0x80:
            length = 2 + data[1]
        else:
            n = data[1] & 0x7F
            length = 2 + n + int.from_bytes(data[2 : 2 + n]) if 0 < n <= 4 else -1
        if length == len(data):
            b64 = base64.b64encode(data)
            lines = [b64[i : i + 64] for i in range(0, len(b64), 64)]
            return b"\n".join([PEM_CRL_BEGIN, *lines, PEM_CRL_END]) + b"\n"
    sys.exit(f"CRL の配布先から、CRL ではないものが返りました：{url}")


def fetch_crl(url: str) -> bytes:
    """CRL を取る（通信の失敗は OSError。collect_crls が URL と理由を出して止める）"""
    req = urllib.request.Request(
        url, headers={"User-Agent": "sumikeshi-studio-make-dist"}
    )
    with urllib.request.urlopen(req, timeout=CRL_TIMEOUT) as r:
        return r.read()


def pe_files(bundle: Path) -> list[Path]:
    """配布用パッケージ（bundle の下）のすべての .exe・.dll・.pyd"""
    return [
        p
        for p in sorted(bundle.rglob("*"))
        if p.suffix.lower() in PE_SUFFIXES and p.is_file()
    ]


def collect_crls(
    bundle: Path,
    verify: Callable[[Path, Path | None], str],
    fetch: Callable[[str], bytes],
    crls: Path,
    limit: int = CRL_ROUNDS,
) -> tuple[list[str], int]:
    """配布用パッケージの部品を流して CRL の配布先の URL を集め、取った CRL を crls（PEM の束）にまとめる。
    verify(部品, 束) は osslsigncode の出力を返す。束がまだ空の1回目は、束を渡さない（None）。
    新しい URL を出した部品だけを流し直し、新しい URL が出なくなるまでくり返す（流すのは limit 回まで）。
    Python の部品は、タイムスタンプの CRL がないとタイムスタンプの検証が通らず、署名の CRL の配布先まで進まない
    ため、1回では集まらない。取った URL と、流した回数を返す。ここでの結果は合否に使わない（check_bundle）"""
    urls: list[str] = []
    pending = pe_files(bundle)
    for count in range(1, limit + 1):
        found: dict[str, list[Path]] = {}
        for p in pending:
            for url in crl_urls(verify(p, crls if urls else None)):
                if url not in urls:
                    found.setdefault(url, []).append(p)
        if not found:
            return urls, count
        if count == limit:
            break
        for url in sorted(found):
            if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
                sys.exit(f"CRL の配布先が http・https ではありません：{url}")
            try:
                data = fetch(url)
            except (OSError, ValueError) as e:
                sys.exit(f"CRL を取れませんでした：{url}（{e}）")
            with crls.open("ab") as f:
                f.write(crl_pem(url, data))
            urls.append(url)
        pending = sorted({p for ps in found.values() for p in ps})
    lines = [
        f"CRL の配布先を、{limit}回流しても集め終わりませんでした。残った URL と部品："
    ]
    for url, ps in sorted(found.items()):
        names = "、".join(p.relative_to(bundle).as_posix() for p in ps)
        lines.append(f"{url}：{names}")
    sys.exit("\n".join(lines))


def check_bundle(
    bundle: Path,
    verify: Callable[[Path, Path | None], str],
    fetch: Callable[[str], bytes],
    crls: Path,
) -> tuple[Counter, list[str], list[str], int]:
    """CRL を集めてから、集め終わった束を渡して全部の部品を流し直し、署名を決まりと比べる。
    合否を、束を渡さない1回目や集める途中の回の結果に左右させないため（1回目は CRL を確かめないので、
    CRL の配布先を持たない証明書の部品は、束を渡さなければ通ってしまう）。
    発行先ごとの数、決まりから外れたものの文、取った CRL の URL、集めるのに流した回数を返す"""
    urls, count = collect_crls(bundle, verify, fetch, crls)
    final = crls if urls else None
    counts, problems = check_signatures(
        bundle, lambda p: parse_verify(verify(p, final))
    )
    return counts, problems, urls, count


def check_signatures(
    bundle: Path, verify: Callable[[Path], tuple[str | None, bool]]
) -> tuple[Counter, list[str]]:
    """配布用パッケージ（bundle の下の FOLDER）のすべての .exe・.dll・.pyd の署名を、決まり（SIGNERS・UNSIGNED）と比べる。
    発行先ごとの数と、決まりから外れたものの文の一覧を返す"""
    counts: Counter = Counter()
    problems = []
    found = set()  # 配布用パッケージにあった、決めた署名のない部品
    for p in pe_files(bundle):
        rel = p.relative_to(bundle).as_posix()
        expected = next(
            (who for pattern, who in SIGNERS if PurePosixPath(rel).full_match(pattern)),
            None,
        )
        name = rel.removeprefix(SITE + "/")
        listed = expected is None and name in UNSIGNED_FILES
        if listed:
            found.add(name)
        org, ok = verify(p)
        if org is None:
            counts["署名なし"] += 1
            if not listed:
                problems.append(f"署名がない：{rel}")
        elif org == "" and not ok:
            # 署名の後に書き換えたファイルなど。組織を読めても通らないものは、下で組織つきで出す
            problems.append(f"署名の検証が通らない（発行先を読めない）：{rel}")
        elif expected is None:
            problems.append(f"決まりにない署名（{org}）：{rel}")
        elif org != expected:
            problems.append(f"発行先が違う（{org}。決まりは {expected}）：{rel}")
        elif not ok:
            problems.append(f"署名の検証が通らない（{org}）：{rel}")
        else:
            counts[org] += 1
    for name in sorted(UNSIGNED_FILES - found):
        problems.append(f"決めた署名のない部品が見つからない：{name}")
    return counts, problems


def signature_tool() -> tuple[str, str]:
    """osslsigncode の場所と、Microsoft の Trusted Root Program のルート証明書のファイル（mscerts）"""
    tool = os.environ.get("OSSLSIGNCODE") or shutil.which("osslsigncode")
    if not tool:
        sys.exit(
            "osslsigncode が見つかりません。入れるか（Debian・Ubuntu なら apt install osslsigncode）、"
            "環境変数 OSSLSIGNCODE で場所を渡してください"
        )
    import mscerts

    return tool, mscerts.where()


def dll_imports(data: bytes, delayed: bool = False) -> list[str]:
    """Windows の実行ファイル（64ビットの .dll・.pyd）が読み込む DLL の名前を、インポート表から返す。delayed なら、
    遅延読み込みの表の DLL も足す"""
    if data[:2] != b"MZ":
        return []
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe : pe + 4] != b"PE\0\0":
        return []
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    optsize = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    if struct.unpack_from("<H", data, opt)[0] != 0x20B:  # 64ビット（PE32+）だけを扱う
        return []
    imp_rva = struct.unpack_from("<I", data, opt + 120)[0]
    sections = []
    for i in range(nsec):
        vsize, va, rawsize, rawptr = struct.unpack_from(
            "<IIII", data, opt + optsize + 40 * i + 8
        )
        sections.append((va, max(vsize, rawsize), rawptr))

    def offset(rva: int) -> int | None:
        for va, size, raw in sections:
            if va <= rva < va + size:
                return rva - va + raw
        return None

    def read(table_rva: int, size: int, name_at: int) -> list[str]:
        """表（1項目 size バイト。DLL の名前の位置は項目の name_at バイト目）の DLL の名前"""
        names, p = [], offset(table_rva) if table_rva else None
        while p is not None:
            name_rva = struct.unpack_from("<I", data, p + name_at)[0]
            q = offset(name_rva) if name_rva else None
            if q is None:
                break
            names.append(data[q : data.index(b"\0", q)].decode("ascii"))
            p += size
        return names

    names = read(imp_rva, 20, 12)
    if delayed:
        names += read(struct.unpack_from("<I", data, opt + 216)[0], 32, 4)
    return names


def prune_pyside(site: Path) -> list[tuple[str, int]]:
    """PySide6 のうち、PYSIDE_KEEP と Visual C++ のランタイムのほかを除き、shiboken6 から SHIBOKEN_UNUSED を除く。
    除いたファイルの site-packages からの位置と大きさを返す。残した .pyd・.dll が、除いた DLL を読み込む
    （インポート表か遅延読み込みの表にある）なら止める"""
    pyside = site / "PySide6"
    keep = {p for pattern in PYSIDE_KEEP for p in pyside.glob(pattern)}
    keep |= {p for p in pyside.iterdir() if VC_RUNTIME_FILES.match(p.name)}
    targets = [p for p in pyside.rglob("*") if p.is_file() and p not in keep]
    for pattern in SHIBOKEN_UNUSED:
        for p in (site / "shiboken6").glob(pattern):
            targets += [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file()]
    removed = sorted(
        (p.relative_to(site).as_posix(), p.stat().st_size) for p in targets
    )
    gone = {p.name.lower() for p in targets}
    for p in targets:
        p.unlink()
    # 空になったフォルダを、深いものから消す
    for root in (pyside, site / "shiboken6"):
        for d in sorted(root.rglob("*"), reverse=True):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
    left = [
        p for root in (pyside, site / "shiboken6") for p in root.rglob("*")
        if p.suffix.lower() in (".dll", ".pyd") and p.is_file()
    ]  # fmt: skip
    gone -= {p.name.lower() for p in left}
    problems = [
        f"{p.relative_to(site).as_posix()} が {name} を読み込みます"
        for p in left
        for name in dll_imports(p.read_bytes(), delayed=True)
        if name.lower() in gone
    ]
    if problems:
        sys.exit(
            "\n".join(["PySide6 から、残した部品が使う DLL を除きました：", *problems])
        )
    return removed


def provide_vc_runtime(python: Path, site: Path) -> dict[Path, Path]:
    """ライブラリが名前どおりに読み込む Visual C++ のランタイムのうち、同じフォルダにも python フォルダにも
    無いものを、PySide6 が同梱しているものから python フォルダ（pythonw.exe の隣）にコピーする。コピー先 → コピー元を返す

    onnxruntime・pyclipper は MSVCP140.dll などを同梱せず、PC に入っているものを使う。
    画面のプロセスでは PySide6 が先に読み込むため見つかるが、解析のプロセス（PySide6 を読み込まない）では、
    Visual C++ のランタイムが入っていない PC で見つからず、起動の準備に失敗するため。
    python フォルダは、そこから起動したすべてのプロセスが DLL を探す場所である。
    """
    listing: dict[Path, set[str]] = {}

    def has(folder: Path, name: str) -> bool:
        if folder not in listing:
            listing[folder] = {f.name.lower() for f in folder.iterdir()}
        return name.lower() in listing[folder]

    needed = set()
    for f in site.rglob("*"):
        if f.suffix.lower() not in (".dll", ".pyd") or not f.is_file():
            continue
        for name in dll_imports(f.read_bytes()):
            if (
                VC_RUNTIME.match(name)
                and not has(f.parent, name)
                and not has(python, name)
            ):
                needed.add(name.lower())
    copies = {}
    for name in sorted(needed):
        src = site / "PySide6" / name
        if not src.is_file():
            sys.exit(
                f"Visual C++ のランタイム {name} を同梱できません（PySide6 にもありません）"
            )
        shutil.copy2(src, python / name)
        copies[python / name] = src
    return copies


def file_version(data: bytes) -> str | None:
    """Windows の .dll・.exe のバージョンの情報（VS_FIXEDFILEINFO）にある、ファイルのバージョン。見つからなければ None"""
    i = data.find(struct.pack("<I", 0xFEEF04BD))
    if i < 0 or i + 16 > len(data):
        return None
    ms, ls = struct.unpack_from("<II", data, i + 8)
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


def vc_runtime_versions(base: Path) -> dict[str, str | None]:
    """配布用パッケージの中の Visual C++ のランタイムの、配布用パッケージの直下からの位置とバージョン"""
    return {
        p.relative_to(base).as_posix(): file_version(p.read_bytes())
        for p in sorted(base.rglob("*"))
        if p.is_file() and VC_RUNTIME_FILES.match(p.name)
    }


def dist_key(name: str) -> str:
    """配布物の名前を比べる形にする（PEP 503 の正規化）"""
    return re.sub(r"[-_.]+", "-", name).lower()


def license_name(meta) -> str:
    """配布物の情報（METADATA）のライセンス。License-Expression、License の1行目、分類（Classifier）の順に探す"""
    if meta.get("License-Expression"):
        return meta["License-Expression"]
    line = (meta.get("License") or "").strip().split("\n")[0].strip()
    if line and len(line) <= 80:
        return line
    found = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    return " / ".join(found) or "(not given)"


def read_dists(site: Path) -> list[dict]:
    """site-packages の配布物：名前・バージョン・ライセンス・dist-info の名前・ライセンスのファイル・RECORD のファイル"""
    dists = []
    for info in sorted(site.glob("*.dist-info")):
        meta = email.message_from_bytes((info / "METADATA").read_bytes())
        record = [
            row[0]
            for row in csv.reader((info / "RECORD").read_text("utf-8").splitlines())
            if row and not row[0].startswith("..")
        ]
        files = sorted(
            {
                r
                for r in record
                if (site / r).is_file()
                and (
                    r.startswith(f"{info.name}/licenses/")
                    or LICENSE_FILE_NAME.match(PurePosixPath(r).name)
                )
            }
        )
        dists.append(
            {
                "name": meta["Name"],
                "key": dist_key(meta["Name"]),
                "version": meta["Version"],
                "license": license_name(meta),
                "info": info.name,
                "files": files,
                "record": set(record),
            }
        )
    return dists


def read_targets(src: Path) -> list[tuple[str, str]]:
    """対応表（LICENSE_TARGETS）の行：(全文のファイルの licenses/ からの位置, 対象)。# で始まる行と空の行は飛ばす"""
    rows = []
    for line in (src / LICENSE_TARGETS).read_text("utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            text, target = line.split("\t")
            rows.append((text, target))
    return rows


def make_licenses(
    base: Path,
    site: Path,
    src: Path = ROOT / "licenses",
    copies: dict[str, str] | None = None,
) -> str:
    """配布用パッケージの licenses フォルダを作り、一覧（LICENSE_INDEX）の文を返す

    リポジトリの licenses/ の全文をコピーし、配布物ごとのライセンスのファイルを、配布物の名前のフォルダにコピーする。
    全文の対象は、対応表に、配布物の名前か、配布用パッケージの中のファイルの型（配布用パッケージのフォルダからの位置。/ を含むもの）で書く。
    一覧の最後に、配布用パッケージの中の Visual C++ のランタイムのファイルごとに、場所と、どの配布物から来たかを書く。copies は、
    make_dist がコピーしたファイルの、コピー先 → コピー元（どちらも配布用パッケージのフォルダからの位置）で、コピー元も書く。
    次のときは止める：対応表に行のない全文、対応表にあって licenses/ にない全文、全文の対象が配布用パッケージに1つもない（余計）、
    ライセンスのファイルも対応表の行もない配布物（足りない）、LICENSE_TEXTS の全文の SHA-256 が違う、
    リポジトリの直下の LICENSE（PROJECT_LICENSE）が AGPL-3.0 の原文と違う、追加の許可（PROJECT_LICENSE_EXCEPTION）がない、
    ツール自身の配布物にその2つのファイルがない、
    Visual C++ のランタイムが対応表のファイルの型のどれにも当てはまらないか、どの配布物から来たかわからない"""
    copies = copies or {}
    rows = read_targets(src)
    texts = sorted(
        p.relative_to(src).as_posix()
        for p in src.rglob("*")
        if p.is_file() and p.name != LICENSE_TARGETS
    )
    named = {text for text, _ in rows}
    problems = [f"対応表に行がない全文：{t}" for t in texts if t not in named]
    problems += [
        f"対応表にあって licenses/ にない全文：{t}" for t in sorted(named - set(texts))
    ]
    dists = read_dists(site)
    by_key = {d["key"]: d for d in dists}
    site_rel = site.relative_to(base).as_posix() + "/"
    owner = {f"{site_rel}{r}": d["key"] for d in dists for r in d["record"]}
    python = "python"  # 一覧の Python の項目の鍵（python フォルダのうち、site-packages の外のファイル）

    def origin(rel: str) -> str | None:
        """配布用パッケージの中のファイルが来た配布物の鍵（コピーしたものはコピー元の）。わからなければ None"""
        rel = copies.get(rel, rel)
        if rel in owner:
            return owner[rel]
        if rel.startswith("python/") and not rel.startswith(site_rel):
            return python
        return None

    runtime = sorted(
        p.relative_to(base).as_posix()
        for p in base.rglob("*")
        if p.is_file() and VC_RUNTIME_FILES.match(p.name)
    )
    # 配布用パッケージの中のファイル → それを対象にする全文
    covered: dict[str, list[str]] = {}
    attached: dict[str, list[str]] = {}
    for text in texts:
        hits = set()
        for _, target in (r for r in rows if r[0] == text):
            if "/" in target:
                for p in base.glob(target):
                    rel = p.relative_to(base).as_posix()
                    covered.setdefault(rel, []).append(text)
                    hits.add(origin(rel) or rel)
            elif dist_key(target) in by_key:
                hits.add(dist_key(target))
        if not hits:
            targets = "、".join(t for x, t in rows if x == text)
            problems.append(
                f"余計な全文（対象が配布用パッケージに1つもない）：{text}（対象：{targets}）"
            )
        for key in hits:
            attached.setdefault(key, []).append(text)
    problems += [
        f"足りない：{d['name']} {d['version']}（配布物にライセンスのファイルがなく、対応表の行もない）"
        for d in dists
        if not d["files"] and d["key"] not in attached
    ]
    problems += [
        f"対応表のファイルの型のどれにも当てはまらない Visual C++ のランタイム：{r}"
        for r in runtime
        if r not in covered
    ]
    problems += [
        f"どの配布物から来たかわからない Visual C++ のランタイム：{r}"
        for r in runtime
        if origin(r) is None
    ]
    for name, digest in LICENSE_TEXTS.items():
        if (src / name).is_file() and hashlib.sha256(
            (src / name).read_bytes()
        ).hexdigest() != digest:
            problems.append(
                f"licenses/{name} の SHA-256 が記録と違います（原文から変わっていないか確かめてください）"
            )
    project_license = src.parent / PROJECT_LICENSE
    if (
        not project_license.is_file()
        or hashlib.sha256(project_license.read_bytes()).hexdigest()
        != PROJECT_LICENSE_SHA256
    ):
        problems.append(
            f"リポジトリの直下の {PROJECT_LICENSE} が、AGPL-3.0 の原文（gnu.org の agpl-3.0.txt）ではありません"
        )
    if not (src.parent / PROJECT_LICENSE_EXCEPTION).is_file():
        problems.append(
            f"リポジトリの直下に、AGPL-3.0 の追加の許可のファイル {PROJECT_LICENSE_EXCEPTION} がありません"
        )
    own = {
        PurePosixPath(f).name
        for d in dists
        if d["key"] == dist_key(PROJECT)
        for f in d["files"]
    }
    if missing := sorted({PROJECT_LICENSE, PROJECT_LICENSE_EXCEPTION} - own):
        problems.append(
            f"ツール自身の配布物（{PROJECT}）に、{'・'.join(missing)} がありません（pyproject.toml の license-files）"
        )
    if problems:
        sys.exit("\n".join(["配布用パッケージの licenses を作れません：", *problems]))
    dest = base / LICENSE_DIR
    dest.mkdir()
    for text in texts:
        (dest / text).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / text, dest / text)
    win = PurePosixPath(LICENSE_DIR)
    lines = [
        "Licenses of the components included in this package",
        "(made by tools/make_dist.py when this package was built)",
        "",
        f"Python {PYTHON_VERSION} (python.org)",
        "  License: PSF-2.0",
        "  python\\LICENSE.txt",
        *(f"  {win / t}".replace("/", "\\") for t in sorted(attached.get(python, []))),
    ]
    for d in dists:
        copied = []
        for r in d["files"]:
            for prefix in (f"{d['info']}/licenses/", f"{d['info']}/"):
                if r.startswith(prefix):
                    r_in = r[len(prefix) :]
                    break
            else:
                r_in = r
            to = dest / d["name"] / r_in
            to.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(site / r, to)
            copied.append(to.relative_to(base).as_posix())
        places = sorted(set(copied)) + [
            str(win / t) for t in sorted(attached.get(d["key"], []))
        ]
        lines += [
            "",
            f"{d['name']} {d['version']}",
            f"  License: {d['license']}",
            *(f"  {p}".replace("/", "\\") for p in places),
        ]
    if runtime:
        names = {d["key"]: f"{d['name']} {d['version']}" for d in dists}
        names[python] = f"Python {PYTHON_VERSION} (python.org)"
        terms = sorted({t for r in runtime for t in covered[r]})
        lines += [
            "",
            "Microsoft Visual C++ Runtime files in this package",
            *(f"  Terms: {win / t}".replace("/", "\\") for t in terms),
        ]
        for r in runtime:
            lines += [
                f"  {r}".replace("/", "\\"),
                f"    from: {names[origin(r)]}",
            ]
            if r in copies:
                lines.append(f"    copied from: {copies[r]}".replace("/", "\\"))
    index = "\r\n".join(lines) + "\r\n"
    (dest / LICENSE_INDEX).write_bytes(index.encode("utf-8-sig"))
    return index


def pdfium_line(site: Path) -> str:
    """version.txt に書く PDFium の行（入れた pypdfium2_raw/version.json と pypdfium2/version.json から読む）"""
    v = json.loads((site / "pypdfium2_raw" / "version.json").read_text("utf-8"))
    p = json.loads((site / "pypdfium2" / "version.json").read_text("utf-8"))
    return (
        f"PDFium: {v['major']}.{v['minor']}.{v['build']}.{v['patch']}"
        f" ({v['origin']}; built from the source of pypdfium2 {p['major']}.{p['minor']}.{p['patch']})"
    )


COMMIT_PREFIX = "Commit: "  # version.txt の、元のコミットの行の始まり
# 公開のリポジトリ。version.txt と配布の README に、この配布用パッケージに対応するソースの場所として書く（配布用パッケージを受け取った人が、
# 同じバージョンのソースを入手できるように）
PUBLIC_REPOSITORY = "https://github.com/hohm-sdx/sumikeshi-studio"
SOURCE_PREFIX = "Source: "  # version.txt の、公開のソースの場所の行の始まり


def source_commit(root: Path = ROOT) -> str:
    """配布用パッケージの元のコミット（version.txt の Commit の行の値）。40桁のハッシュで、追跡しているファイルにコミットして
    いない変更があれば「 (uncommitted changes)」を付ける。git がない・リポジトリでないときは「unknown」。
    配布用パッケージを作るのは止めない"""
    git = shutil.which("git")
    if not git:
        return "unknown"
    try:
        head = subprocess.run(
            [git, "-C", str(root), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if head.returncode != 0 or not re.fullmatch(
            r"[0-9a-f]{40}", head.stdout.strip()
        ):
            return "unknown"
        status = subprocess.run(
            [git, "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "unknown"
    # 変更があるか確かめられなかったときも、変更ありとして書く（変更がないと誤って書かないため）
    dirty = status.returncode != 0 or status.stdout.strip()
    return head.stdout.strip() + (" (uncommitted changes)" if dirty else "")


def public_commit_problem(commit: str) -> str | None:
    """公開用のパッケージを作れない理由（作れるなら None）。公開用のパッケージの version.txt には元のコミットを書かないので、
    コミットしていない変更から作ると、後から気づけないため"""
    if re.fullmatch(r"[0-9a-f]{40}", commit):
        return None
    if commit.endswith(" (uncommitted changes)"):
        return "コミットしていない変更があるので、公開用のパッケージを作りません（コミットしてから作る）"
    return "元のコミットが分からないので、公開用のパッケージを作りません（git のリポジトリの中で作る）"


def version_file_text(stamp: datetime, site: Path, commit: str | None) -> str:
    """配布用パッケージの version.txt の中身（中身は英語。作った日時の行は、version.made_at が MADE_PREFIX で探す）。
    commit が None（公開用のパッケージ）なら、Commit の行を書かない"""
    return (
        f"{PRODUCT} {__version__}\r\n{MADE_PREFIX}{stamp:%Y-%m-%d %H:%M}\r\n"
        + (f"{COMMIT_PREFIX}{commit}\r\n" if commit is not None else "")
        + f"{SOURCE_PREFIX}{PUBLIC_REPOSITORY} (tag v{__version__})\r\n"
        f"Python: {PYTHON_FILE} (python.org)\r\n{pdfium_line(site)}\r\n"
    )


# README の、ZIP の中の最長のパスと、展開先のフォルダの名前の上限の文。配布用パッケージを作るときに測った値を書き込む（手で
# 書き直すと、名前を変えたときに古い数のまま配布用パッケージができるおそれがあるため）。上限は、Windows のパスの長さの上限
# （260文字）から、最長のパスと3文字（区切りの \ など）を引いた値
README_FILES = ("README-ja.txt", "README-en.txt")
PATH_LENGTH_TEXTS = (
    re.compile(
        r"(この ZIP の中の最も長いパスは)\d+(文字です。展開先のフォルダの名前が)\d+(文字を超えない)"
    ),
    re.compile(
        r"(The longest path in this ZIP is )\d+( characters\. Keep the path of the destination folder to )\d+( characters)"
    ),
)


def folder_limit(longest: int) -> int:
    """展開先のフォルダの名前の上限の文字数"""
    return 260 - 3 - longest


def set_path_lengths(text: str, longest: int) -> str:
    """README の文に、最長のパスと展開先のフォルダの名前の上限を書き込む。文がちょうど1つないときは止める
    （README を書き直して文が消えたときに、数のないまま配布用パッケージを作らないため）"""
    found = 0
    for pattern in PATH_LENGTH_TEXTS:
        text, n = pattern.subn(
            rf"\g<1>{longest}\g<2>{folder_limit(longest)}\g<3>", text
        )
        found += n
    if found != 1:
        sys.exit(f"README に、最長のパスの文が1つではありません（{found}個）")
    return text


def check_pdfium_dll(site: Path) -> None:
    """入れた pdfium.dll が、決めたもの（PDFIUM_DLL_SHA256）かを確かめる"""
    dll = hashlib.sha256(
        (site / "pypdfium2_raw" / "pdfium.dll").read_bytes()
    ).hexdigest()
    if dll != PDFIUM_DLL_SHA256:
        sys.exit(f"pdfium.dll の SHA-256 が、決めたものと違います：{dll}")


def check_icons(site: Path, src: Path = ICONS) -> None:
    """配布用パッケージに、ツールのアイコンのファイル（.ico と PNG）が、リポジトリの icons/ と同じ中身で入っているかを
    確かめる（導入のスクリプトのショートカットとアプリの一覧が、パッケージの中のアイコンのファイルを指すため）"""
    dst = site / "sumikeshi_studio" / "icons"
    want = sorted(p.name for p in src.iterdir() if p.suffix in (".ico", ".png"))
    if "sumikeshi-studio.ico" not in want:
        sys.exit(f"リポジトリの {src} に、アイコンのファイルがありません")
    if any(
        not (dst / n).is_file() or (src / n).read_bytes() != (dst / n).read_bytes()
        for n in want
    ):
        sys.exit(
            f"配布用パッケージに、ツールのアイコンが入っていないか、中身がリポジトリのものと違います（{dst}）"
        )


def check_bundled_forms(site: Path, src: Path = BUNDLED_FORMS) -> None:
    """配布用パッケージに、導入で入れる3様式（.json と見本の画像）が、リポジトリの bundled_forms/ と同じ中身で入っているかを
    確かめる"""
    dst = site / "sumikeshi_studio" / "bundled_forms"
    want = sorted(p.name for p in src.iterdir() if p.is_file())
    have = sorted(p.name for p in dst.iterdir() if p.is_file()) if dst.is_dir() else []
    if sum(n.endswith(".json") for n in want) != 3:
        sys.exit(f"リポジトリの {src} に、様式の .json が3つありません")
    if want != have or any(
        (src / n).read_bytes() != (dst / n).read_bytes() for n in want
    ):
        sys.exit(
            f"配布用パッケージに、導入で入れる3様式が入っていないか、中身がリポジトリのものと違います（{dst}）"
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="配布用パッケージを作る")
    parser.add_argument(
        "--public",
        action="store_true",
        help="公開用のパッケージにする（version.txt に元のコミットを書かない）",
    )
    public = parser.parse_args(argv).public
    stamp = datetime.now()
    commit = source_commit()  # 作り始める前の作業ツリーの状態で決める
    if public and (problem := public_commit_problem(commit)):
        sys.exit(problem)
    name = f"{NAME}-{__version__}-{stamp:%Y%m%d}-win64"
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        base = work / "bundle" / FOLDER
        base.mkdir(parents=True)
        # 作り始める前に、署名を確かめるツールがあるかを見る
        tool, roots = signature_tool()
        python = shutil.move(fetch_python(work), base / "python")
        site = Path(python) / "Lib" / "site-packages"
        req = work / "requirements.txt"
        uv("export", "-q", "--frozen", "--no-dev", "--no-emit-project",
           "--format", "requirements-txt", "-o", str(req))  # fmt: skip
        req.write_text(
            keep_pure_hashes(
                req.read_text("utf-8"),
                pure_hashes((ROOT / "uv.lock").read_text("utf-8")),
            ),
            "utf-8",
        )
        target = ["--target", str(site), "--python-platform", "x86_64-pc-windows-msvc",
                  "--python-version", "3.14"]  # fmt: skip
        uv("pip", "install", "-q", *target, "--only-binary", ":all:",
           "--require-hashes", "-r", str(req))  # fmt: skip
        check_pdfium_dll(site)
        uv("build", "--wheel", "-q", "-o", str(work / "wheel"))
        wheel = next((work / "wheel").glob("sumikeshi_studio-*.whl"))
        uv("pip", "install", "-q", *target, "--no-deps", str(wheel))
        pruned = prune_pyside(site)
        if found := sorted(p.name for p in site.iterdir() if FORBIDDEN.match(p.name)):
            sys.exit(
                f"配布用パッケージに入れてはいけないライブラリが入りました：{'、'.join(found)}"
            )
        for rel in SITE_UNUSED_DIRS:
            shutil.rmtree(site / rel)
        for pattern in SITE_UNUSED_FILES:
            for p in site.glob(pattern):
                pruned.append((p.relative_to(site).as_posix(), p.stat().st_size))
                p.unlink()
        copied = provide_vc_runtime(Path(python), site)
        for p in sorted(site.rglob("*"), reverse=True):  # 深いものから
            if p.is_dir() and p.name in SITE_UNUSED_NAMES and p.parent != site:
                shutil.rmtree(p)
        for cache in base.rglob("__pycache__"):
            shutil.rmtree(cache)
        removed = remove_build_leftovers(site)
        check_bundled_forms(site)
        check_icons(site)
        counts, problems, crl_list, crl_rounds = check_bundle(
            base.parent,
            lambda p, crls: verify_output(p, tool, roots, crls),
            fetch_crl,
            work / "crls.pem",
        )
        if problems:
            sys.exit(
                "\n".join(
                    ["署名の確認で、決まりから外れたものがありました：", *problems]
                )
            )
        # windows/ の配置のまま入れる（一番上に入口の .bat、scripts/ に PowerShell のスクリプト）
        shutil.copytree(ROOT / "windows", base, dirs_exist_ok=True)
        make_licenses(
            base,
            site,
            copies={
                dst.relative_to(base).as_posix(): src.relative_to(base).as_posix()
                for dst, src in copied.items()
            },
        )
        license_count = sum(1 for p in (base / LICENSE_DIR).rglob("*") if p.is_file())
        runtimes = vc_runtime_versions(base)
        (base / VERSION_FILE).write_bytes(
            version_file_text(stamp, site, None if public else commit).encode(
                "utf-8-sig"
            )
        )
        # 最長のパスを測り、README に書き込んでから ZIP にする
        longest = max(
            len(p.relative_to(base.parent).as_posix()) for p in base.rglob("*")
        )
        for readme in README_FILES:
            path = base / readme
            path.write_bytes(
                set_path_lengths(path.read_bytes().decode("utf-8-sig"), longest).encode(
                    "utf-8-sig"
                )
            )
        OUT_DIR.mkdir(exist_ok=True)
        zip_path = shutil.make_archive(str(OUT_DIR / name), "zip", base.parent, FOLDER)
    size = Path(zip_path).stat().st_size / 1024 / 1024
    print(f"{zip_path}（{size:.0f}MB、ZIP の中の最長のパス {longest}文字）")
    print(
        "python フォルダにコピーした Visual C++ のランタイム："
        + (
            "、".join(f"{p.name}（{runtimes.get('python/' + p.name)}）" for p in copied)
            or "なし"
        )
    )
    versions = sorted(
        {v for v in runtimes.values() if v}, key=lambda v: tuple(map(int, v.split(".")))
    )
    print(
        f"配布用パッケージの中の Visual C++ のランタイム：{len(runtimes)}個、バージョン {versions[0]}〜{versions[-1]}"
        if versions
        else f"配布用パッケージの中の Visual C++ のランタイム：{len(runtimes)}個（バージョンを読めませんでした）"
    )
    print(f"除いた、作った環境のスクリプトと記録：{'、'.join(removed) or 'なし'}")
    print(
        f"配布用パッケージの licenses：ファイル {license_count}個（一覧は {LICENSE_DIR}/{LICENSE_INDEX}）"
    )
    print(
        f"除いた、ツールが使わない PySide6・shiboken6・OpenCV・PyYAML の部品：{len(pruned)}個、"
        f"{sum(size for _, size in pruned):,}バイト"
    )
    signed = "・".join(
        f"{who} {n}個" for who, n in counts.most_common() if who != "署名なし"
    )
    print(
        f"署名の確認：{signed}、署名なし {counts['署名なし']}個（決めた一覧の{len(UNSIGNED_FILES)}個と同じ）"
    )
    print(
        f"署名の確認で使った CRL：{len(crl_list)}個（配布先を集めるのに部品を{crl_rounds}回流した）"
    )
    for url in crl_list:
        print(f"  {url}")
    print(
        f"{COMMIT_PREFIX}{commit}"
        + (
            "（公開用のパッケージなので version.txt には書いていない元のコミット）"
            if public
            else "（version.txt に書いた元のコミット）"
        )
    )


if __name__ == "__main__":
    main()
